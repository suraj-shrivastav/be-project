"""Ticker universe organized by exchange.

Each market in models.markets pulls from one or more exchanges. Edit this file
to expand coverage; the ingester reads ALL_TICKERS.

yfinance suffix conventions:
    .NS → NSE (National Stock Exchange of India)
    .BO → BSE (Bombay Stock Exchange)
    no suffix → US listings (Nasdaq + NYSE — actual exchange is read from
    yfinance's `info.exchange` field at ingest time and stored per-row).
"""

from __future__ import annotations

# ── India: NSE — Nifty 200 (top 200 by market cap) ──────────────────────────
NSE_TICKERS: list[str] = [
    # Nifty 50 core
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "HINDUNILVR.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS",
    "LT.NS", "AXISBANK.NS", "ASIANPAINT.NS", "MARUTI.NS", "BAJFINANCE.NS",
    "HCLTECH.NS", "WIPRO.NS", "ULTRACEMCO.NS", "SUNPHARMA.NS", "TITAN.NS",
    "NESTLEIND.NS", "ONGC.NS", "NTPC.NS", "POWERGRID.NS", "M&M.NS",
    "TATASTEEL.NS", "TATAMOTORS.NS", "JSWSTEEL.NS", "ADANIENT.NS", "ADANIPORTS.NS",
    "COALINDIA.NS", "GRASIM.NS", "BAJAJFINSV.NS", "BAJAJ-AUTO.NS", "EICHERMOT.NS",
    "HEROMOTOCO.NS", "HINDALCO.NS", "TECHM.NS", "DRREDDY.NS", "CIPLA.NS",
    "BRITANNIA.NS", "DIVISLAB.NS", "INDUSINDBK.NS", "SBILIFE.NS", "HDFCLIFE.NS",
    "TATACONSUM.NS", "BPCL.NS", "UPL.NS", "APOLLOHOSP.NS", "LTIM.NS",

    # Nifty Next 50 + popular mid-caps
    "ADANIGREEN.NS", "ADANIPOWER.NS", "AMBUJACEM.NS", "BANKBARODA.NS",
    "BERGEPAINT.NS", "BIOCON.NS", "BOSCHLTD.NS", "CHOLAFIN.NS",
    "DABUR.NS", "DLF.NS", "GAIL.NS", "GODREJCP.NS", "HAVELLS.NS",
    "ICICIPRULI.NS", "INDIGO.NS", "IOC.NS", "MARICO.NS", "MOTHERSON.NS",
    "PIDILITIND.NS", "SHREECEM.NS", "SIEMENS.NS", "TATAPOWER.NS",
    "TORNTPHARM.NS", "VEDL.NS", "ZYDUSLIFE.NS",
    "ABB.NS", "ACC.NS", "ATGL.NS", "AUBANK.NS", "AWL.NS",
    "BANDHANBNK.NS", "BEL.NS", "BHEL.NS", "CANBK.NS", "CGPOWER.NS",
    "COLPAL.NS", "CONCOR.NS", "CUMMINSIND.NS", "DIXON.NS", "DMART.NS",
    "EXIDEIND.NS", "FEDERALBNK.NS", "GLAND.NS", "GMRINFRA.NS", "GODREJPROP.NS",
    "HAL.NS", "HINDPETRO.NS",

    # Nifty 200 expansion (101-200) — large/mid-caps across sectors
    "ICICIGI.NS", "IDEA.NS", "IDFCFIRSTB.NS", "INDHOTEL.NS", "IRCTC.NS",
    "JINDALSTEL.NS", "JUBLFOOD.NS", "LICI.NS", "LUPIN.NS", "MFSL.NS",
    "MPHASIS.NS", "MRF.NS", "MUTHOOTFIN.NS", "NAUKRI.NS", "NMDC.NS",
    "OFSS.NS", "PAGEIND.NS", "PEL.NS", "PETRONET.NS", "PIIND.NS",
    "PNB.NS", "POLYCAB.NS", "PFC.NS", "RECLTD.NS", "SAIL.NS",
    "SBICARD.NS", "SRF.NS", "TATACHEM.NS", "TATACOMM.NS", "TATAELXSI.NS",
    "TORNTPOWER.NS", "TRENT.NS", "TVSMOTOR.NS", "UBL.NS", "VOLTAS.NS",
    "ZOMATO.NS", "ZEEL.NS", "ALKEM.NS", "AUROPHARMA.NS", "ASHOKLEY.NS",
    "APOLLOTYRE.NS", "ASTRAL.NS", "BALKRISIND.NS", "BHARATFORG.NS", "BANKINDIA.NS",
    "BSOFT.NS", "CHAMBLFERT.NS", "COFORGE.NS", "COROMANDEL.NS", "CROMPTON.NS",
    "DEEPAKNTR.NS", "DELHIVERY.NS", "EMAMILTD.NS", "FORTIS.NS", "GICRE.NS",
    "GLENMARK.NS", "GODREJIND.NS", "GUJGASLTD.NS", "HDFCAMC.NS", "IDBI.NS",
    "IIFL.NS", "INDIANB.NS", "INDIAMART.NS", "IPCALAB.NS", "JUBLPHARMA.NS",
    "JKCEMENT.NS", "KEC.NS", "KAJARIACER.NS", "LALPATHLAB.NS", "LICHSGFIN.NS",
    "LODHA.NS", "MAHABANK.NS", "MANAPPURAM.NS", "MAXHEALTH.NS", "METROPOLIS.NS",
    "NATIONALUM.NS", "NHPC.NS", "NLCINDIA.NS", "NYKAA.NS", "OBEROIRLTY.NS",
    "PERSISTENT.NS", "PFIZER.NS", "PHOENIXLTD.NS", "POLICYBZR.NS", "POONAWALLA.NS",
    "RAMCOCEM.NS", "RBLBANK.NS", "RVNL.NS", "SUNDARMFIN.NS", "SUPREMEIND.NS",
    "SUZLON.NS", "SYNGENE.NS", "TIINDIA.NS", "UNIONBANK.NS", "VBL.NS",
    "VINATIORGA.NS", "WHIRLPOOL.NS", "YESBANK.NS",
]

# ── India: BSE-distinct listings (curated) ──────────────────────────────────
# Most large-caps are dual-listed; these are picks that benefit from BSE's
# broader coverage of mid/small-caps and unique listings.
BSE_TICKERS: list[str] = [
    "BAJAJHLDNG.BO",   # Bajaj Holdings (holding company)
    "ABBOTINDIA.BO",   # Abbott India
    "GILLETTE.BO",     # Gillette India
    "PGHH.BO",         # P&G Hygiene & Health Care (correct symbol)
    "3MINDIA.BO",      # 3M India
    "CASTROLIND.BO",   # Castrol India
    "OFSS.BO",         # Oracle Financial Services
    "MRF.BO",          # MRF Tyres
    "PAGEIND.BO",      # Page Industries (Jockey)
    "HONAUT.BO",       # Honeywell Automation India
    "BAJAJELEC.BO",    # Bajaj Electricals
    "FINPIPE.BO",      # Finolex Industries
]

# ── United States: Nasdaq + NYSE leaders ────────────────────────────────────
# Backs the "US" market scope. yfinance returns the actual exchange code per
# ticker (NMS/NGM → "NASDAQ", NYQ → "NYSE") which we map at ingest time, so
# this list can mix both freely.
US_TICKERS: list[str] = [
    # Mega-cap tech
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA",
    # Tech / semiconductors / software
    "AVGO", "ADBE", "CSCO", "AMD", "INTC", "QCOM", "TXN", "MU",
    "AMAT", "ADI", "INTU", "ISRG", "BKNG", "MRVL", "PANW", "CRWD",
    "ASML", "LRCX", "KLAC", "CDNS", "SNPS", "DDOG", "FTNT",
    "ORCL", "CRM", "IBM", "NOW", "WDAY", "SNOW", "ANET", "DELL",
    "HPQ", "ACN", "NET", "SHOP", "UBER", "SPOT", "PINS",
    "ZS", "TTD", "OKTA", "MDB", "DASH", "ABNB", "MELI",
    # Communication / streaming
    "NFLX", "CMCSA", "TMUS", "DIS", "VZ", "T", "CHTR",
    # Banking / financial services
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW",
    "V", "MA", "AXP", "COF", "USB", "PNC", "ICE", "CME",
    "SPGI", "MCO", "PYPL",
    # Healthcare / pharma / biotech
    "UNH", "JNJ", "LLY", "ABBV", "PFE", "MRK", "TMO", "DHR",
    "ABT", "BMY", "ELV", "CI", "CVS", "MDT", "SYK", "ZTS",
    "AMGN", "GILD", "VRTX", "REGN", "MRNA", "BIIB",
    # Consumer staples / retail / restaurants
    "PG", "KO", "PEP", "WMT", "MO", "PM", "CL", "KMB", "GIS",
    "MDLZ", "COST", "SBUX", "MNST", "MCD", "CMG",
    # Consumer discretionary
    "HD", "NKE", "LOW", "TGT", "TJX", "ROST", "ORLY", "AZO",
    "F", "GM", "MAR", "PCAR",
    # Industrials / aerospace / transport
    "CAT", "HON", "BA", "UPS", "RTX", "GE", "LMT", "DE",
    "UNP", "MMM", "NOC", "CSX", "FDX",
    # Energy
    "XOM", "CVX", "COP", "SLB", "EOG", "OXY",
    # Materials / utilities / real estate
    "LIN", "APD", "SHW", "NEE", "SO", "PLD", "AMT",
    # Misc large-caps
    "ADP", "PAYX",
]


# Backward-compat alias — older code may still reference NASDAQ_TICKERS.
NASDAQ_TICKERS = US_TICKERS


def exchange_for(yf_ticker: str) -> str:
    """Map a yfinance ticker to its canonical exchange code.

    For US tickers we return "NASDAQ" as a default; the ingester refines this
    with yfinance's actual exchange code (NMS/NGM → NASDAQ, NYQ → NYSE).
    """
    if yf_ticker.endswith(".NS"):
        return "NSE"
    if yf_ticker.endswith(".BO"):
        return "BSE"
    if yf_ticker in set(US_TICKERS):
        return "NASDAQ"
    return "OTHER"


def country_for(yf_ticker: str) -> str:
    """ISO-ish country for the ticker."""
    if yf_ticker.endswith(".NS") or yf_ticker.endswith(".BO"):
        return "IN"
    return "US"


def currency_for(yf_ticker: str) -> str:
    """Native trading currency."""
    if yf_ticker.endswith(".NS") or yf_ticker.endswith(".BO"):
        return "INR"
    return "USD"


def display_symbol(yf_ticker: str) -> str:
    """Strip the exchange suffix for the canonical `ticker` column.

    Same company on NSE vs BSE collapses to the same ticker — we keep one row
    per (ticker, exchange) implicit pair in the database via the exchange column.
    To avoid PK collisions we suffix BSE-only listings with '.BSE'.
    """
    if yf_ticker.endswith(".NS"):
        return yf_ticker[:-3]
    if yf_ticker.endswith(".BO"):
        # Suffix to avoid collision with NSE-listed dual listings
        return yf_ticker[:-3] + ".BSE"
    return yf_ticker


ALL_TICKERS: list[str] = NSE_TICKERS + BSE_TICKERS + US_TICKERS
