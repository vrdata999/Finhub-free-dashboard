"""
Typed Python wrapper over the 27 Finnhub endpoints proven to work on free tier.

Design rules:
  - One function per endpoint, named after the data, not the HTTP path
  - All return Optional[...] - never raise. The dashboard renders 'no data' on None.
  - Reads ../.env (same key the other scripts in this project use)
  - Every call goes through the disk cache with the project's TTL policy
  - Symbol-specific endpoints accept symbol positionally; the rest take a scope arg
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv

from cache import get as cache_get, set as cache_set

# Load ../.env (one directory up - the dashboard folder is a subfolder of the project root)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(dotenv_path=os.path.join(_PROJECT_ROOT, ".env"))

API_KEY = os.getenv("FINNHUB_API_KEY")
if not API_KEY:
    raise RuntimeError(
        "FINNHUB_API_KEY missing. Add it to .env in the project root "
        "(the parent of this dashboard folder)."
    )

BASE = "https://finnhub.io/api/v1"

# ---- TTL policy (seconds) ----
TTL_QUOTE = 60
TTL_NEWS = 300
TTL_MARKET = 300
TTL_SYMBOL = 3600           # most per-symbol data
TTL_REFERENCE = 24 * 3600   # static-ish reference data


# ---- low-level HTTP ----------------------------------------------------- #

def _http(path: str, params: dict | None = None, timeout: float = 10.0) -> Any | None:
    """Raw GET. Returns parsed JSON, or None on any error or empty/error payload."""
    params = dict(params or {})
    params["token"] = API_KEY
    try:
        r = requests.get(f"{BASE}{path}", params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
    except (requests.RequestException, ValueError):
        return None
    if isinstance(data, dict) and data.get("error"):
        return None
    if isinstance(data, dict) and data.get("s") == "no_data":
        return None
    return data


def _call(path: str, params: dict | None, ttl: int) -> Any | None:
    """Cache-aware wrapper around _http. Cached payload takes precedence."""
    cached = cache_get(path, params, ttl=ttl)
    if cached is not None:
        return cached
    data = _http(path, params)
    if data is not None:
        cache_set(path, params, data, ttl=ttl)
    return data


def _is_empty_list(payload: Any) -> bool:
    return isinstance(payload, list) and len(payload) == 0


# ---- per-endpoint functions --------------------------------------------- #

def quote(symbol: str) -> dict | None:
    """Real-time price: {c, d, dp, h, l, o, pc, t}."""
    return _call("/quote", {"symbol": symbol.upper()}, TTL_QUOTE)


def search(q: str) -> pd.DataFrame | None:
    """Symbol search. Returns DataFrame of matches."""
    data = _call("/search", {"q": q}, TTL_SYMBOL)
    if not data:
        return None
    rows = data.get("result", [])
    if not rows:
        return pd.DataFrame(columns=["symbol", "displaySymbol", "description", "type"])
    return pd.DataFrame(rows)


def company_profile(symbol: str) -> dict | None:
    """Profile v2: {name, ticker, exchange, currency, country, finnhubIndustry,
    marketCapitalization, logo, weburl, ...}."""
    return _call("/stock/profile2", {"symbol": symbol.upper()}, TTL_SYMBOL)


def peers(symbol: str) -> list[str] | None:
    data = _call("/stock/peers", {"symbol": symbol.upper()}, TTL_SYMBOL)
    if not isinstance(data, list):
        return None
    return data


def basic_financials(symbol: str) -> dict | None:
    """133-key metric dictionary."""
    data = _call("/stock/metric", {"symbol": symbol.upper(), "metric": "all"}, TTL_SYMBOL)
    if not isinstance(data, dict):
        return None
    return data


def recommendation_trend(symbol: str) -> pd.DataFrame | None:
    data = _call("/stock/recommendation", {"symbol": symbol.upper()}, TTL_SYMBOL)
    if not isinstance(data, list):
        return None
    if not data:
        return pd.DataFrame(columns=["period", "strongBuy", "buy", "hold", "sell", "strongSell"])
    df = pd.DataFrame(data)
    if "period" in df.columns:
        df["period"] = pd.to_datetime(df["period"]).dt.strftime("%Y-%m")
    return df


def earnings_surprises(symbol: str, limit: int = 8) -> pd.DataFrame | None:
    data = _call("/stock/earnings", {"symbol": symbol.upper(), "limit": limit}, TTL_SYMBOL)
    if not isinstance(data, list):
        return None
    if not data:
        return pd.DataFrame(columns=["period", "estimate", "actual", "surprise", "surprisePercent"])
    df = pd.DataFrame(data)
    if "period" in df.columns:
        df["period"] = pd.to_datetime(df["period"]).dt.strftime("%Y-%m-%d")
    return df


def financials_reported(symbol: str, freq: str = "annual") -> pd.DataFrame | None:
    """XBRL as-reported. Returns a long-format DataFrame: each row is one
    concept (e.g. 'us-gaap_Revenues') for one period. The 'value' column is
    coerced to numeric where possible; non-numeric values (e.g. date strings)
    are dropped so downstream Arrow serialization succeeds."""
    raw = _call("/stock/financials-reported",
                {"symbol": symbol.upper(), "freq": freq}, TTL_SYMBOL)
    if not isinstance(raw, dict):
        return None
    rows = []
    for filing in raw.get("data", []):
        period = filing.get("period") or f"{filing.get('year')}-Q{filing.get('quarter') or 0}"
        for statement in ("bs", "ic", "cf"):
            for concept in filing.get("report", {}).get(statement, []):
                v = concept.get("value")
                # Keep only numeric values (drop endDate / dateType concepts that
                # arrive as strings; callers don't need them in the long-format view).
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    continue
                rows.append({
                    "accessNumber": filing.get("accessNumber"),
                    "form": filing.get("form"),
                    "filedDate": filing.get("filedDate"),
                    "period": period,
                    "year": filing.get("year"),
                    "quarter": filing.get("quarter"),
                    "statement": {"bs": "balance", "ic": "income", "cf": "cash"}[statement],
                    "concept": concept.get("concept"),
                    "label": concept.get("label"),
                    "value": float(v),
                    "unit": concept.get("unit"),
                })
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["period", "statement", "concept", "label", "value", "unit"])


def insider_transactions(symbol: str) -> pd.DataFrame | None:
    data = _call("/stock/insider-transactions", {"symbol": symbol.upper()}, TTL_SYMBOL)
    if not isinstance(data, dict):
        return None
    rows = data.get("data", [])
    if not rows:
        return pd.DataFrame(columns=["filingDate", "name", "share", "change", "transactionPrice"])
    return pd.DataFrame(rows)


def insider_sentiment(symbol: str, days_back: int = 90) -> pd.DataFrame | None:
    end = int(time.time())
    start = end - days_back * 86400
    data = _call("/stock/insider-sentiment",
                 {"symbol": symbol.upper(), "from": start, "to": end}, TTL_SYMBOL)
    if not isinstance(data, dict):
        return None
    rows = data.get("data", [])
    if not rows:
        return pd.DataFrame(columns=["year", "month", "change", "mspr"])
    return pd.DataFrame(rows)


# SEC Form 4 transaction codes we treat as open-market / discretionary.
_OPEN_MARKET_CODES = {"P", "S"}  # P = purchase, S = sale


def insider_sentiment_from_transactions(
    tx: pd.DataFrame | None,
) -> pd.DataFrame | None:
    """Derive a monthly insider-sentiment signal from /stock/insider-transactions.

    Finnhub's /stock/insider-sentiment is soft-gated (returns no data on the free
    tier), so we build our own from the transaction feed we already pull.

    Only OPEN-MARKET transactions are used (SEC Form 4 codes P=purchase,
    S=sale) - the discretionary trades that reflect sentiment. Grants (A),
    option exercises (M), tax withholding (F) and gifts (G) are excluded as
    non-discretionary.

    Returns one row per month (YYYY-MM, newest first):
      month, net_shares, buy_shares, sell_shares, buy_txns, sell_txns,
      net_value, score
    where score = (buys - sells) / (buys + sells) * 100  ->  -100 bearish ..
    +100 bullish. Returns None if tx is None; an empty DataFrame (with the
    right columns) if there are no open-market transactions in the window.
    """
    empty = pd.DataFrame(columns=["month", "net_shares", "buy_shares",
                                   "sell_shares", "buy_txns", "sell_txns",
                                   "net_value", "score"])
    if tx is None or tx.empty:
        return None
    if "transactionCode" not in tx.columns or "change" not in tx.columns:
        return empty
    df = tx.copy()
    om = df[df["transactionCode"].isin(_OPEN_MARKET_CODES)].copy()
    if om.empty:
        return empty
    date_col = "transactionDate" if "transactionDate" in om.columns else "filingDate"
    om["month"] = pd.to_datetime(om[date_col], errors="coerce").dt.strftime("%Y-%m")
    om = om.dropna(subset=["month"])
    if om.empty:
        return empty
    om["change"] = pd.to_numeric(om["change"], errors="coerce").fillna(0)
    if "transactionPrice" in om.columns:
        price = pd.to_numeric(om["transactionPrice"], errors="coerce").fillna(0.0)
    else:
        price = 0.0
    om["value"] = om["change"] * price
    om["buy_shares"] = om["change"].where(om["change"] > 0, 0)
    om["sell_shares"] = (-om["change"]).where(om["change"] < 0, 0)
    om["is_buy"] = om["change"] > 0
    om["is_sell"] = om["change"] < 0
    monthly = om.groupby("month", as_index=False).agg(
        net_shares=("change", "sum"),
        buy_shares=("buy_shares", "sum"),
        sell_shares=("sell_shares", "sum"),
        buy_txns=("is_buy", "sum"),
        sell_txns=("is_sell", "sum"),
        net_value=("value", "sum"),
    )
    denom = monthly["buy_shares"] + monthly["sell_shares"]
    monthly["score"] = 0.0
    active = denom > 0
    monthly.loc[active, "score"] = (
        (monthly.loc[active, "buy_shares"] - monthly.loc[active, "sell_shares"])
        / denom[active] * 100
    )
    return monthly.sort_values("month", ascending=False).reset_index(drop=True)


def sec_filings(symbol: str) -> pd.DataFrame | None:
    data = _call("/stock/filings", {"symbol": symbol.upper()}, TTL_SYMBOL)
    if not isinstance(data, list):
        return None
    if not data:
        return pd.DataFrame(columns=["filedDate", "form", "accessNumber", "filingUrl"])
    df = pd.DataFrame(data)
    if "filedDate" in df.columns:
        df["filedDate"] = pd.to_datetime(df["filedDate"]).dt.strftime("%Y-%m-%d")
    return df


def _fetch_year_window(symbol: str, path: str, years_back: int = 5) -> list[dict] | None:
    """Fetch every year in the window (current year back through `years_back`),
    merge the `data` rows from each. Each year is cached separately (TTL_SYMBOL),
    so repeat renders are free.

    Returns the concatenated rows (newest year first by construction), or None
    if the endpoint never returned a usable dict payload for any year in the
    window (e.g. 403 / network error on every year).
    """
    current_year = datetime.now(timezone.utc).year
    out: list[dict] = []
    saw_payload = False
    for y in range(current_year, current_year - years_back - 1, -1):
        data = _call(path,
                     {"symbol": symbol.upper(),
                      "from": f"{y}-01-01", "to": f"{y}-12-31"},
                     TTL_SYMBOL)
        if isinstance(data, dict):
            saw_payload = True
            out.extend(data.get("data") or [])
    return out if saw_payload else None


def lobbying(symbol: str, years_back: int = 5) -> pd.DataFrame | None:
    """Senate lobbying filings across the last `years_back` years, newest first.
    Merged across years and sorted by year then period descending so the most
    recent disclosures surface at the top — no single-year pinning."""
    rows = _fetch_year_window(symbol, "/stock/lobbying", years_back)
    if rows is None:
        return None
    if not rows:
        return pd.DataFrame(columns=["year", "period", "expenses", "documentUrl"])
    df = pd.DataFrame(rows)
    sort_cols = [c for c in ("year", "period") if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols, ascending=False)
    return df.reset_index(drop=True)


def uspto_patents(symbol: str, years_back: int = 5) -> pd.DataFrame | None:
    """USPTO patent filings across the last `years_back` years, newest first.
    Note: Finnhub's patent feed lags ~18-24 months, so the newest rows may be
    from a prior year — that is the source's latest, not stale cache."""
    rows = _fetch_year_window(symbol, "/stock/uspto-patent", years_back)
    if rows is None:
        return None
    if not rows:
        return pd.DataFrame(columns=["filingDate", "description", "patentNumber", "patentType"])
    df = pd.DataFrame(rows)
    if "filingDate" in df.columns:
        df = df.sort_values("filingDate", ascending=False)
    return df.reset_index(drop=True)


def visa_applications(symbol: str, years_back: int = 5) -> pd.DataFrame | None:
    """H-1B visa applications across the last `years_back` years, newest first."""
    rows = _fetch_year_window(symbol, "/stock/visa-application", years_back)
    if rows is None:
        return None
    if not rows:
        return pd.DataFrame(columns=["receivedDate", "jobTitle", "wageRangeFrom", "wageRangeTo", "wageUnitOfPay", "employerName"])
    df = pd.DataFrame(rows)
    if "receivedDate" in df.columns:
        df = df.sort_values("receivedDate", ascending=False)
    return df.reset_index(drop=True)


def usa_spending(symbol: str, years_back: int = 5) -> pd.DataFrame | None:
    """Federal contract awards (USA Spending) across the last `years_back`
    years, newest first."""
    rows = _fetch_year_window(symbol, "/stock/usa-spending", years_back)
    if rows is None:
        return None
    if not rows:
        return pd.DataFrame(columns=["actionDate", "awardDescription", "awardingAgencyName", "totalValue"])
    df = pd.DataFrame(rows)
    if "actionDate" in df.columns:
        df = df.sort_values("actionDate", ascending=False)
    return df.reset_index(drop=True)


def market_status(exchange: str = "US") -> dict | None:
    return _call("/stock/market-status", {"exchange": exchange}, TTL_MARKET)


def market_holiday(exchange: str = "US") -> pd.DataFrame | None:
    data = _call("/stock/market-holiday", {"exchange": exchange}, TTL_REFERENCE)
    if not isinstance(data, dict):
        return None
    rows = data.get("data", [])
    if not rows:
        return pd.DataFrame(columns=["atDate", "eventName"])
    return pd.DataFrame(rows)


def news(category: str = "general") -> pd.DataFrame | None:
    data = _call("/news", {"category": category}, TTL_NEWS)
    if not isinstance(data, list):
        return None
    if not data:
        return pd.DataFrame(columns=["datetime", "headline", "source", "url"])
    df = pd.DataFrame(data)
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"], unit="s", utc=True).dt.strftime("%Y-%m-%d %H:%MZ")
    return df


def symbol_search(exchange: str = "US") -> pd.DataFrame | None:
    """Full ticker list for an exchange. ~30k rows for US - cached 24h."""
    data = _call("/stock/symbol", {"exchange": exchange}, TTL_REFERENCE)
    if not isinstance(data, list):
        return None
    return pd.DataFrame(data) if data else pd.DataFrame()


def ipo_calendar(years_back: int = 1, years_ahead: int = 1) -> pd.DataFrame | None:
    """IPO calendar spanning [current_year - years_back, current_year + years_ahead],
    merged and sorted newest date first. No hardcoded year — the window rolls
    forward automatically as time advances."""
    current_year = datetime.now(timezone.utc).year
    out: list[dict] = []
    saw_payload = False
    for y in range(current_year - years_back, current_year + years_ahead + 1):
        data = _call("/calendar/ipo",
                     {"from": f"{y}-01-01", "to": f"{y}-12-31"}, TTL_REFERENCE)
        if isinstance(data, dict):
            saw_payload = True
            out.extend(data.get("ipoCalendar") or [])
    if not saw_payload:
        return None
    if not out:
        return pd.DataFrame(columns=["date", "name", "symbol", "exchange", "price", "status"])
    df = pd.DataFrame(out)
    if "date" in df.columns:
        df = df.sort_values("date", ascending=False)
    return df.reset_index(drop=True)


def fda_calendar() -> pd.DataFrame | None:
    data = _call("/fda-advisory-committee-calendar", {}, TTL_REFERENCE)
    if not isinstance(data, list):
        return None
    return pd.DataFrame(data) if data else pd.DataFrame()


def country_list() -> pd.DataFrame | None:
    data = _call("/country", {}, TTL_REFERENCE)
    if not isinstance(data, list):
        return None
    return pd.DataFrame(data) if data else pd.DataFrame()


def covid_us() -> pd.DataFrame | None:
    data = _call("/covid19/us", {}, TTL_REFERENCE)
    if not isinstance(data, list):
        return None
    return pd.DataFrame(data) if data else pd.DataFrame()


def crypto_exchanges() -> list[str] | None:
    data = _call("/crypto/exchange", {}, TTL_REFERENCE)
    return data if isinstance(data, list) else None


def crypto_symbols(exchange: str = "BINANCE") -> pd.DataFrame | None:
    data = _call("/crypto/symbol", {"exchange": exchange}, TTL_REFERENCE)
    if not isinstance(data, list):
        return None
    return pd.DataFrame(data) if data else pd.DataFrame()


def forex_exchanges() -> list[str] | None:
    data = _call("/forex/exchange", {}, TTL_REFERENCE)
    return data if isinstance(data, list) else None


def forex_symbols(exchange: str = "oanda") -> pd.DataFrame | None:
    data = _call("/forex/symbol", {"exchange": exchange}, TTL_REFERENCE)
    if not isinstance(data, list):
        return None
    return pd.DataFrame(data) if data else pd.DataFrame()


# ---- helpers used by the UI --------------------------------------------- #

def metrics_for_display(metrics: dict | None) -> pd.DataFrame | None:
    """Pick the high-signal keys from the 133-key metric dict into a flat DataFrame."""
    if not metrics:
        return None
    m = metrics.get("metric", {})
    if not isinstance(m, dict):
        return None
    interesting = {
        "peBasicExtraTTM": "P/E (TTM)",
        "peNormalizedExtraTTM": "P/E normalized (TTM)",
        "epsBasicExtraTTM": "EPS basic (TTM)",
        "epsNormalizedBasicAnnual": "EPS normalized (annual)",
        "beta": "Beta",
        "52WeekHigh": "52-week high",
        "52WeekLow": "52-week low",
        "52WeekHighDate": "52w high date",
        "52WeekLowDate": "52w low date",
        "dividendYieldIndicatedAnnual": "Dividend yield (annual)",
        "dividendPerShareAnnual": "Dividend per share (annual)",
        "currentEv/freeCashFlowTTM": "EV / FCF (TTM)",
        "currentRatioQuarterly": "Current ratio",
        "debt/equityQuarterly": "Debt / equity",
        "returnOnEquityTTM": "ROE (TTM)",
        "returnOnAssetsTTM": "ROA (TTM)",
        "grossMarginTTM": "Gross margin (TTM)",
        "operatingMarginTTM": "Operating margin (TTM)",
        "netProfitMarginTTM": "Net profit margin (TTM)",
        "revenuePerShareTTM": "Revenue / share (TTM)",
        "bookValuePerShareQuarterly": "Book value / share",
    }
    rows = []
    for k, label in interesting.items():
        v = m.get(k)
        if v is None:
            continue
        # Always render Value as a string so Arrow can serialize a uniform
        # column type (the metric dict mixes numbers and date strings).
        rows.append({"Metric": label, "Value": str(v)})
    return pd.DataFrame(rows) if rows else None


def latest_xbrl_summary(financials_df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Pick the headline figures out of the latest annual 10-K. Returns a tidy DF."""
    if financials_df is None or financials_df.empty:
        return None
    # find latest annual filing (year desc, quarter==0)
    annual = financials_df[financials_df["quarter"] == 0].copy()
    if annual.empty:
        annual = financials_df.copy()
    if annual.empty:
        return None
    latest_year = annual["year"].max()
    latest = annual[annual["year"] == latest_year]
    if latest.empty:
        return None
    headline_concepts = {
        "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax": "Net sales",
        "us-gaap_Revenues": "Total revenues",
        "us-gaap_GrossProfit": "Gross profit",
        "us-gaap_OperatingIncomeLoss": "Operating income",
        "us-gaap_NetIncomeLoss": "Net income",
        "us-gaap_Assets": "Total assets",
        "us-gaap_Liabilities": "Total liabilities",
        "us-gaap_StockholdersEquity": "Stockholders' equity",
        "us-gaap_EarningsPerShareDiluted": "EPS diluted ($)",
        "us-gaap_CashAndCashEquivalentsAtCarryingValue": "Cash & equivalents",
        "us-gaap_LongTermDebtNoncurrent": "Long-term debt",
        "us-gaap_NetCashProvidedByUsedInOperatingActivities": "Operating cash flow",
    }
    rows = []
    for concept, label in headline_concepts.items():
        hit = latest[latest["concept"] == concept]
        if hit.empty:
            continue
        val = hit.iloc[0]["value"]
        # Some concepts (e.g. endDate) carry date strings - skip non-numeric values.
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        rows.append({
            "Line item": label,
            "Value": float(val),
            "Unit": hit.iloc[0]["unit"],
        })
    return pd.DataFrame(rows) if rows else None


def dashboard_version() -> str:
    return f"client={os.path.getmtime(__file__):.0f}"


# CLI sanity-check (no Streamlit required):
if __name__ == "__main__":
    print(dashboard_version())
    q = quote("AAPL")
    print(f"quote: ${q['c']} ({q['dp']}%)")
    p = company_profile("AAPL")
    print(f"profile: {p['name']} ({p['exchange']})")
    print(f"peers: {peers('AAPL')[:5]}")