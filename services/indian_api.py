"""Client for the Indian Stock Market API (indianapi.in).

Requires: pip install httpx
Env var:  INDIAN_API_KEY
"""
import os
import httpx

BASE_URL = "https://stock.indianapi.in"
_TIMEOUT = 15.0


def _headers() -> dict:
    key = os.getenv("INDIAN_API_KEY", "")
    return {"x-api-key": key}


def get_stock(company_name: str) -> dict:
    """Fetch live stock data for an Indian company by name."""
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.get(f"{BASE_URL}/stock", params={"name": company_name}, headers=_headers())
        r.raise_for_status()
        return r.json()


def get_trending() -> dict:
    """Fetch trending stocks on BSE/NSE."""
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.get(f"{BASE_URL}/trending", headers=_headers())
        r.raise_for_status()
        return r.json()


def get_historical(stock_name: str, period: str = "1M") -> dict:
    """Fetch historical OHLCV data. period: 1W, 1M, 6M, 1Y, 5Y"""
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.get(
            f"{BASE_URL}/historical_data",
            params={"stock_name": stock_name, "period": period},
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()


def get_news(company_name: str) -> dict:
    """Fetch latest news for an Indian company."""
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.get(f"{BASE_URL}/news", params={"name": company_name}, headers=_headers())
        r.raise_for_status()
        return r.json()


def get_ipo() -> dict:
    """Fetch upcoming and recent IPO data."""
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.get(f"{BASE_URL}/ipo", headers=_headers())
        r.raise_for_status()
        return r.json()


def get_stock_forecast(company_name: str) -> dict:
    """Fetch analyst forecasts for an Indian stock."""
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.get(
            f"{BASE_URL}/stock_forecasts",
            params={"name": company_name},
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()
