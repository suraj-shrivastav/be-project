"""yfinance → Supabase Postgres ingester.

Replaces scripts/synthetic.py. Pulls real fundamentals + daily OHLCV from
Yahoo Finance and writes to the `fundamentals` and `daily_prices` tables.

Usage:
    python -m scripts.ingest          # fundamentals + 2 years of daily prices
    python -m scripts.ingest --quick  # fundamentals only (~3 min)

Notes:
    - yfinance is a Yahoo scraper — rate-limited if called too aggressively.
      We batch-download prices and sleep between fundamentals chunks.
    - The dedicated financials/balance_sheet/cashflow methods in yfinance
      are broken in 2026 (return empty). We use Ticker.info exclusively.
    - Indian tickers carry a .NS suffix in yfinance; we strip it before
      writing to the canonical `ticker` column.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd
import yfinance as yf
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models import DailyPrice, Fundamental, QuarterlyFinancial
from db.session import AsyncSessionLocal
from scripts.tickers import (
    ALL_TICKERS,
    country_for,
    currency_for,
    display_symbol,
    exchange_for,
)

console = Console()

BATCH_SIZE = 25            # tickers per fundamentals batch
SLEEP_BETWEEN_BATCHES = 2  # seconds — soft-throttle Yahoo


def _safe_num(val: Any) -> Decimal | None:
    """Coerce a yfinance value to Decimal or None. Filters NaN/inf."""
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return Decimal(str(round(f, 6)))


def _safe_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return int(f)


def _info_to_row(yf_ticker: str, info: dict) -> dict | None:
    """Map a yfinance .info dict into a Fundamental row dict."""
    symbol = display_symbol(yf_ticker)
    name = info.get("longName") or info.get("shortName") or symbol
    sector = info.get("sector")
    industry = info.get("industry")
    if not name or not sector:
        # Yahoo sometimes returns near-empty info for delisted/renamed tickers
        return None

    # Prefer yfinance's reported exchange when available (more accurate for US listings).
    yf_exchange = (info.get("exchange") or "").upper()
    if yf_exchange in ("NMS", "NGM", "NCM"):     # Nasdaq tiers
        exchange = "NASDAQ"
    elif yf_exchange == "NYQ":                    # NYSE
        exchange = "NYSE"
    elif yf_exchange == "NSI":                    # NSE India
        exchange = "NSE"
    elif yf_exchange == "BSE":
        exchange = "BSE"
    else:
        exchange = exchange_for(yf_ticker)        # fall back to suffix mapping

    # yfinance changed the dividendYield scale in late 2024 to return percent
    # (e.g. 3.5 for 3.5%). Some tickers/versions still return decimal. Normalise:
    # if value > 1, treat as percent and convert to decimal so 3.5 → 0.035.
    raw_div_yield = info.get("dividendYield")
    if raw_div_yield is not None:
        try:
            dy = float(raw_div_yield)
            if dy > 1:
                raw_div_yield = dy / 100.0
        except (TypeError, ValueError):
            pass

    return {
        "ticker":           symbol,
        "company_name":     str(name)[:300],
        "country":          country_for(yf_ticker),
        "exchange":         exchange,
        "currency":         info.get("currency") or currency_for(yf_ticker),
        "sector":           sector,
        "industry":         industry,
        "description":      (info.get("longBusinessSummary") or "")[:2000] or None,
        "market_cap":       _safe_int(info.get("marketCap")),
        "pe_ratio":         _safe_num(info.get("trailingPE")),
        "pb_ratio":         _safe_num(info.get("priceToBook")),
        "dividend_yield":   _safe_num(raw_div_yield),
        "beta":             _safe_num(info.get("beta")),
        "eps":              _safe_num(info.get("trailingEps")),
        "revenue_growth":   _safe_num(info.get("revenueGrowth")),
        "profit_margin":    _safe_num(info.get("profitMargins")),
        "debt_to_equity":   _safe_num(info.get("debtToEquity")),
        "return_on_equity": _safe_num(info.get("returnOnEquity")),
        "week52_high":      _safe_num(info.get("fiftyTwoWeekHigh")),
        "week52_low":       _safe_num(info.get("fiftyTwoWeekLow")),
        "last_price":       _safe_num(info.get("currentPrice") or info.get("regularMarketPrice")),
        "month_change":     None,  # filled from daily prices later
        "year_change":      None,
        "updated_at":       datetime.now(timezone.utc),
    }


def fetch_fundamentals_sync(tickers: list[str]) -> list[dict]:
    """Pull fundamentals for all tickers, batched. Synchronous (yfinance is blocking)."""
    rows: list[dict] = []
    failed: list[str] = []
    total = len(tickers)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Fetching fundamentals", total=total)

        for i in range(0, total, BATCH_SIZE):
            batch = tickers[i : i + BATCH_SIZE]
            for yf_ticker in batch:
                try:
                    info = yf.Ticker(yf_ticker).info
                    row = _info_to_row(yf_ticker, info)
                    if row:
                        rows.append(row)
                    else:
                        failed.append(yf_ticker)
                except Exception as exc:
                    failed.append(yf_ticker)
                    console.log(f"[yellow]skip {yf_ticker}: {exc}[/]")
                progress.advance(task, 1)
            time.sleep(SLEEP_BETWEEN_BATCHES)

    if failed:
        console.log(f"[yellow]Failed tickers ({len(failed)}): {', '.join(failed)}[/]")
    console.log(f"[green]Got fundamentals for {len(rows)} / {total} tickers[/]")
    return rows


def fetch_daily_prices_sync(tickers: list[str], period: str = "2y") -> pd.DataFrame:
    """Bulk-download daily OHLCV for all tickers. Returns a long-form DataFrame.

    Columns: ticker, date, open, high, low, close, volume.
    """
    console.log(f"[blue]Downloading {period} of daily prices for {len(tickers)} tickers...[/]")
    raw = yf.download(
        tickers,
        period=period,
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        threads=True,
        progress=False,
    )

    long_rows: list[dict] = []
    for yf_ticker in tickers:
        symbol = display_symbol(yf_ticker)
        # When >1 ticker is requested, columns are MultiIndex (ticker, field)
        try:
            df = raw[yf_ticker] if yf_ticker in raw.columns.get_level_values(0) else raw
        except (KeyError, AttributeError):
            continue
        if df is None or df.empty:
            continue
        df = df.dropna(subset=["Close"])
        for date_idx, r in df.iterrows():
            long_rows.append({
                "ticker": symbol,
                "date":   date_idx.date(),
                "open":   _safe_num(r.get("Open")),
                "high":   _safe_num(r.get("High")),
                "low":    _safe_num(r.get("Low")),
                "close":  _safe_num(r.get("Close")),
                "volume": _safe_int(r.get("Volume")),
            })

    out = pd.DataFrame(long_rows)
    console.log(f"[green]Got {len(out)} daily price rows[/]")
    return out


def compute_change_fields(prices_df: pd.DataFrame) -> dict[str, dict]:
    """Per-ticker compute month_change / year_change / last_price from daily closes."""
    result: dict[str, dict] = {}
    for ticker, group in prices_df.groupby("ticker"):
        closes = group.sort_values("date")["close"].astype(float).reset_index(drop=True)
        if closes.empty:
            continue
        last = float(closes.iloc[-1])
        entry: dict[str, Any] = {"last_price": _safe_num(last)}
        if len(closes) > 21:
            prev = float(closes.iloc[-22])
            if prev:
                entry["month_change"] = _safe_num((last - prev) / prev)
        if len(closes) > 252:
            prev = float(closes.iloc[-253])
            if prev:
                entry["year_change"] = _safe_num((last - prev) / prev)
        result[ticker] = entry
    return result


# ── Async DB writers ────────────────────────────────────────────────────────


async def write_fundamentals(rows: list[dict]) -> None:
    """Atomic refresh — wipe and replace."""
    if not rows:
        console.log("[red]No fundamentals to write[/]")
        return
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Fundamental))
        # Bulk insert in chunks of 100 to keep statement size sane
        for i in range(0, len(rows), 100):
            chunk = rows[i : i + 100]
            await db.execute(pg_insert(Fundamental.__table__).values(chunk))
        await db.commit()
    console.log(f"[green]Wrote {len(rows)} fundamentals rows[/]")


async def write_daily_prices(prices_df: pd.DataFrame) -> None:
    """Idempotent insert — ON CONFLICT DO NOTHING so re-runs are safe."""
    if prices_df.empty:
        console.log("[red]No daily prices to write[/]")
        return
    rows = prices_df.to_dict(orient="records")
    async with AsyncSessionLocal() as db:
        for i in range(0, len(rows), 500):
            chunk = rows[i : i + 500]
            stmt = pg_insert(DailyPrice.__table__).values(chunk)
            stmt = stmt.on_conflict_do_nothing(index_elements=["ticker", "date"])
            await db.execute(stmt)
        await db.commit()
    console.log(f"[green]Upserted {len(rows)} daily price rows[/]")


async def patch_fundamentals_with_changes(changes: dict[str, dict]) -> None:
    """Update month_change / year_change / last_price after price ingestion."""
    if not changes:
        return
    async with AsyncSessionLocal() as db:
        for ticker, fields in changes.items():
            await db.execute(
                text("""
                    UPDATE fundamentals
                    SET last_price   = COALESCE(:last_price, last_price),
                        month_change = COALESCE(:month_change, month_change),
                        year_change  = COALESCE(:year_change, year_change),
                        updated_at   = now()
                    WHERE ticker = :ticker
                """),
                {
                    "ticker":       ticker,
                    "last_price":   fields.get("last_price"),
                    "month_change": fields.get("month_change"),
                    "year_change":  fields.get("year_change"),
                },
            )
        await db.commit()
    console.log(f"[green]Patched change fields for {len(changes)} tickers[/]")


# ── Quarterly financials ────────────────────────────────────────────────────


def _row_get(df, label):
    """Pull a row from a yfinance financials DataFrame by label, tolerating
    case + small naming differences. Returns the row Series or None."""
    if df is None or df.empty:
        return None
    target = label.lower().replace(" ", "")
    for idx in df.index:
        if str(idx).lower().replace(" ", "") == target:
            return df.loc[idx]
    return None


def fetch_quarterly_financials_sync(tickers: list[str]) -> list[dict]:
    """Pull quarterly income statement data per ticker. yfinance's
    quarterly_income_stmt returns a DataFrame: columns are quarter-end dates
    (most recent first), rows are line items.
    """
    rows: list[dict] = []
    skipped = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Fetching quarterly financials", total=len(tickers))

        for yf_ticker in tickers:
            try:
                t = yf.Ticker(yf_ticker)
                # New API: quarterly_income_stmt; old API: quarterly_financials.
                df = getattr(t, "quarterly_income_stmt", None)
                if df is None or (hasattr(df, "empty") and df.empty):
                    df = getattr(t, "quarterly_financials", None)
                if df is None or df.empty:
                    skipped += 1
                    progress.advance(task, 1)
                    continue

                symbol      = display_symbol(yf_ticker)
                rev_row     = _row_get(df, "Total Revenue")
                ni_row      = _row_get(df, "Net Income")
                op_row      = _row_get(df, "Operating Income")
                gp_row      = _row_get(df, "Gross Profit")

                # Iterate columns (each is a quarter-end timestamp)
                for col in df.columns:
                    qdate = getattr(col, "date", lambda: None)()
                    if qdate is None:
                        continue
                    rows.append({
                        "ticker":        symbol,
                        "quarter_end":   qdate,
                        "revenue":       _safe_num(rev_row[col]) if rev_row is not None else None,
                        "net_income":    _safe_num(ni_row[col])  if ni_row  is not None else None,
                        "operating_inc": _safe_num(op_row[col])  if op_row  is not None else None,
                        "gross_profit":  _safe_num(gp_row[col])  if gp_row  is not None else None,
                        "updated_at":    datetime.now(timezone.utc),
                    })
            except Exception as exc:
                skipped += 1
                console.log(f"[yellow]quarterly skip {yf_ticker}: {exc}[/]")
            progress.advance(task, 1)

    console.log(
        f"[green]Got {len(rows)} quarterly rows "
        f"({len(tickers) - skipped} / {len(tickers)} tickers)[/]"
    )
    return rows


async def write_quarterly_financials(rows: list[dict]) -> None:
    """Upsert quarterly rows. Latest run wins via ON CONFLICT DO UPDATE."""
    if not rows:
        console.log("[red]No quarterly financials to write[/]")
        return
    async with AsyncSessionLocal() as db:
        for i in range(0, len(rows), 200):
            chunk = rows[i : i + 200]
            stmt  = pg_insert(QuarterlyFinancial.__table__).values(chunk)
            stmt  = stmt.on_conflict_do_update(
                index_elements=["ticker", "quarter_end"],
                set_={
                    "revenue":       stmt.excluded.revenue,
                    "net_income":    stmt.excluded.net_income,
                    "operating_inc": stmt.excluded.operating_inc,
                    "gross_profit":  stmt.excluded.gross_profit,
                    "updated_at":    stmt.excluded.updated_at,
                },
            )
            await db.execute(stmt)
        await db.commit()
    console.log(f"[green]Upserted {len(rows)} quarterly rows[/]")


# ── Entry points ────────────────────────────────────────────────────────────


async def run(quick: bool = False, skip_quarterly: bool = False) -> None:
    tickers = ALL_TICKERS
    console.log(f"[bold]Universe: {len(tickers)} tickers[/]")

    fundamentals_rows = fetch_fundamentals_sync(tickers)
    await write_fundamentals(fundamentals_rows)

    if quick:
        console.log("[yellow]--quick: skipping daily prices and quarterly[/]")
        return

    # 5y of history powers the longer charts on the stock detail page. Older
    # rows are added via ON CONFLICT DO NOTHING; existing 2y rows are no-ops.
    prices_df = fetch_daily_prices_sync(tickers, period="5y")
    await write_daily_prices(prices_df)

    changes = compute_change_fields(prices_df)
    await patch_fundamentals_with_changes(changes)

    if skip_quarterly:
        console.log("[yellow]--no-quarterly: skipping quarterly financials[/]")
    else:
        quarterly_rows = fetch_quarterly_financials_sync(tickers)
        await write_quarterly_financials(quarterly_rows)

    console.log("[bold green]✓ Ingestion complete[/]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest stock data into Supabase")
    parser.add_argument("--quick", action="store_true", help="Skip daily prices + quarterly")
    parser.add_argument("--no-quarterly", action="store_true", help="Skip quarterly financials only")
    args = parser.parse_args()
    try:
        asyncio.run(run(quick=args.quick, skip_quarterly=args.no_quarterly))
    except KeyboardInterrupt:
        console.log("[red]Interrupted[/]")
        sys.exit(130)


if __name__ == "__main__":
    main()
