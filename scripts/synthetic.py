import multiprocessing
import os
import random
import string
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from rich.console import Console, Group
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TimeElapsedColumn,
    TimeRemainingColumn,
    track,
)

console = Console()


def random_ticker_name():
    length = np.random.randint(3, 6)
    return "".join(random.choices(string.ascii_uppercase, k=length))


def random_sector():
    sectors = [
        "Technology",
        "Healthcare",
        "Financials",
        "Consumer Discretionary",
        "Industrials",
        "Energy",
        "Utilities",
        "Materials",
        "Real Estate",
        "Communication Services",
    ]
    return random.choice(sectors)


def random_industry(sector):
    mapping = {
        "Technology": ["Semiconductors", "Software", "Hardware", "IT Services"],
        "Healthcare": ["Biotech", "Pharmaceutical", "Medical Devices"],
        "Financials": ["Banks", "Insurance", "Asset Management"],
        "Consumer Discretionary": ["Retail", "Autos", "Entertainment"],
        "Industrials": ["Aerospace", "Machinery", "Transportation"],
        "Energy": ["Oil & Gas", "Renewables", "Energy Equipment"],
        "Utilities": ["Electric", "Water", "Gas"],
        "Materials": ["Chemicals", "Metals", "Paper"],
        "Real Estate": ["REITs", "Property Management"],
        "Communication Services": ["Telecom", "Media", "Internet"],
    }
    return random.choice(mapping[sector])


def generate_fundamentals(num_tickers: int) -> pd.DataFrame:
    with console.status("generating fundamentals.."):
        tickers = [random_ticker_name() for _ in range(num_tickers)]
        data = []
        for t in track(tickers, description="creating fundamentals for tickers"):
            sector = random_sector()
            industry = random_industry(sector)
            market_cap = np.random.uniform(100e6, 500e9)
            pe = np.random.uniform(5, 60)
            pb = np.random.uniform(0.5, 10)
            div_yield = np.random.uniform(0, 0.05)
            beta = np.clip(np.random.normal(1.0, 0.35), 0.5, 2.0)
            float_shares = np.random.uniform(10e6, 5e9)
            month_chg = np.random.normal(0.02, 0.08)
            year_chg = np.random.normal(0.10, 0.25)
            eps = np.random.uniform(0.5, 20)
            data.append(
                {
                    "Ticker": t,
                    "Sector": sector,
                    "Industry": industry,
                    "MarketCap": market_cap,
                    "PeRatio": pe,
                    "PbRatio": pb,
                    "DividendYield": div_yield,
                    "Beta": beta,
                    "FloatShares": float_shares,
                    "MonthPercentageChange": month_chg,
                    "YearPercentageChange": year_chg,
                    "EarningsPerShare": eps,
                }
            )
        df = pd.DataFrame(data)
        os.makedirs("data", exist_ok=True)
        pq.write_table(
            pa.Table.from_pandas(df), "data/fundamentals.parquet", compression="snappy"
        )
        console.log(f"[green]Saved fundamentals for {num_tickers} tickers[/]")
        return df


def sector_volatility(sector):
    base_vol = {
        "Technology": 0.025,
        "Healthcare": 0.02,
        "Financials": 0.015,
        "Consumer Discretionary": 0.018,
        "Industrials": 0.017,
        "Energy": 0.022,
        "Utilities": 0.012,
        "Materials": 0.019,
        "Real Estate": 0.014,
        "Communication Services": 0.021,
    }
    return base_vol.get(sector, 0.018)


def generate_minute_data(price, minutes, volatility, drift, avg_vol):
    """Generate minute-level OHLCV data with proper column names."""
    dt = 1 / minutes
    shocks = np.random.normal(0, 1, minutes)
    prices = np.zeros(minutes)
    prices[0] = price

    for t in range(1, minutes):
        prices[t] = prices[t - 1] * np.exp(
            (drift - 0.5 * volatility**2) * dt + volatility * np.sqrt(dt) * shocks[t]
        )

    df = pd.DataFrame(
        {
            "Open": prices,
            "High": prices * (1 + np.random.uniform(0, 0.005, minutes)),
            "Low": prices * (1 - np.random.uniform(0, 0.005, minutes)),
            "Close": prices * (1 + np.random.uniform(-0.002, 0.002, minutes)),
        }
    )

    # Use CamelCase column names to match grammar expectations
    df["PreviousClose"] = df["Close"].shift(1).fillna(df["Open"])
    df["ShareVolume"] = np.random.lognormal(np.log(avg_vol), 0.5, minutes)
    df["Value"] = df["ShareVolume"] * df["Close"]
    df["MonthPercentageChange"] = np.random.normal(drift * 30 * 24 * 60, 0.05, minutes)
    df["YearPercentageChange"] = np.random.normal(drift * 365 * 24 * 60, 0.25, minutes)
    df["EarningsPerShare"] = df["Close"].mean() / np.random.uniform(40, 80)
    return df


def generate_ticker_monthly_data(
    row: pd.Series,
    year: int,
    month: int,
    output_dir: str,
    ticker_progress: Progress,
    overall_progress: Progress,
    overall_task_id: TaskID,
):
    """Generate monthly consolidated data for a ticker."""
    ticker = row["Ticker"]

    # Create hive-style partitioned directory structure
    ticker_dir = os.path.join(
        output_dir, "consolidated", f"symbol={ticker}", f"year={year}"
    )
    os.makedirs(ticker_dir, exist_ok=True)

    base_vol = sector_volatility(row["Sector"]) * row["Beta"]
    drift = 0.0003 + row["DividendYield"] * 0.5 - (base_vol * 0.1)
    avg_volume = np.clip(row["MarketCap"] / 1e8, 10000, 5e6)
    # Use PeRatio and PbRatio (the column names in fundamentals)
    price = np.clip(row["PeRatio"] * row["PbRatio"], 5, 2000)

    # Calculate trading days in this month
    start_date = datetime(year, month, 1)
    if month == 12:
        end_date = datetime(year + 1, 1, 1)
    else:
        end_date = datetime(year, month + 1, 1)

    # Get all trading days (Mon-Fri) in the month
    trading_days = []
    current = start_date
    while current < end_date:
        if current.weekday() < 5:  # Monday = 0, Friday = 4
            trading_days.append(current)
        current += timedelta(days=1)

    task_id = ticker_progress.add_task(
        f"{ticker} {year}-{month:02d}", total=len(trading_days)
    )

    try:
        monthly_data = []

        for date in trading_days:
            df = generate_minute_data(price, 390, base_vol, drift, avg_volume)
            df["Datetime"] = pd.date_range(
                start=date.replace(hour=9, minute=30), periods=len(df), freq="min"
            )
            df["Ticker"] = ticker
            df["Date"] = date.date()
            monthly_data.append(df)

            price = df["Close"].iloc[-1]
            ticker_progress.advance(task_id, 1)

        # Concatenate all days and write monthly file
        if monthly_data:
            combined_df = pd.concat(monthly_data, ignore_index=True)
            path = os.path.join(ticker_dir, f"month={month:02d}.parquet")
            pq.write_table(
                pa.Table.from_pandas(combined_df),
                path,
                compression="zstd",  # Better compression than snappy
                row_group_size=100000,  # Optimal for Parquet
                use_dictionary=True,  # Essential for ticker column
                write_statistics=True,  # Enable predicate pushdown
            )
    finally:
        ticker_progress.remove_task(task_id)
        overall_progress.advance(overall_task_id, 1)


def generate_synthetic_market(num_tickers=100, num_days=730, output_dir="data"):
    """
    Generate synthetic market data with monthly consolidation.

    Args:
        num_tickers: Number of tickers to generate
        num_days: Number of days of history (default 2 years)
        output_dir: Output directory for data
    """
    os.makedirs(output_dir, exist_ok=True)
    fundamentals = generate_fundamentals(num_tickers)

    # Calculate date range
    end_date = datetime.today()
    start_date = end_date - timedelta(days=num_days)

    # Generate list of (year, month) tuples to process
    months_to_generate = []
    current = datetime(start_date.year, start_date.month, 1)
    while current <= end_date:
        months_to_generate.append((current.year, current.month))
        if current.month == 12:
            current = datetime(current.year + 1, 1, 1)
        else:
            current = datetime(current.year, current.month + 1, 1)

    total_tasks = num_tickers * len(months_to_generate)

    overall_progress = Progress(
        SpinnerColumn(),
        "[progress.description]{task.description}",
        BarColumn(pulse_style="bar.pulse"),
        TimeElapsedColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )
    overall_task_id = overall_progress.add_task("Overall", total=total_tasks)

    ticker_progress = Progress(
        " + [dim white]{task.description}",
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )

    grouped = Group(overall_progress, ticker_progress)

    with Live(grouped, console=console, refresh_per_second=10):
        with ThreadPoolExecutor(max_workers=multiprocessing.cpu_count()) as executor:
            futures = []
            for _, row in fundamentals.iterrows():
                for year, month in months_to_generate:
                    futures.append(
                        executor.submit(
                            generate_ticker_monthly_data,
                            row,
                            year,
                            month,
                            output_dir,
                            ticker_progress,
                            overall_progress,
                            overall_task_id,
                        )
                    )

            for future in as_completed(futures):
                future.result()

    console.log(
        f"[green]Dataset creation completed: {num_tickers} tickers, {len(months_to_generate)} months per ticker[/]"
    )
    console.log(
        "[green]Storage format: Monthly consolidated Parquet with hive partitioning[/]"
    )


if __name__ == "__main__":
    import shutil

    # Clean up old data structure if it exists
    if os.path.exists("data"):
        console.log("[yellow]Cleaning up old data structure...[/]")
        # Keep fundamentals.parquet if it exists
        fundamentals_backup = None
        if os.path.exists("data/fundamentals.parquet"):
            import tempfile

            fundamentals_backup = os.path.join(
                tempfile.gettempdir(), "fundamentals_backup.parquet"
            )
            shutil.copy("data/fundamentals.parquet", fundamentals_backup)

        # Remove old ticker directories (they have uppercase names like "AAPL")
        for item in os.listdir("data"):
            item_path = os.path.join("data", item)
            if os.path.isdir(item_path) and item.isupper():
                shutil.rmtree(item_path)
                console.log(f"[dim]Removed old directory: {item}[/]")

        console.log("[green]Cleanup complete[/]")

    # Generate new consolidated data
    generate_synthetic_market(num_tickers=100, num_days=365, output_dir="data")
