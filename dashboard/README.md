# Finnhub Research Dashboard

Interactive Streamlit dashboard over the **free Finnhub API**. Type any ticker
and see a research view assembled from the 27 endpoints the free tier can
reach — quote, profile, peers, financials (XBRL as-reported), earnings,
analyst recommendations, insider trades, SEC filings, lobbying, USPTO patents,
H-1B applications, USA spending, news, plus reference data (IPO, FDA, market
status, country, COVID).

## Run

```
cd "C:\Users\thesh\Desktop\finhub data\dashboard"
pip install -r requirements.txt
streamlit run app.py
```

Browser opens at <http://localhost:8501>.

The dashboard reads `../.env` for `FINNHUB_API_KEY` — no need to copy it.

## What it does NOT have

- Historical OHLCV (`/stock/candle`) — paid tier only. The Overview tab
  shows a single-day bar from `/quote` (open, current, prev close, day
  range) plus an annotation noting the gap.
- Multi-symbol comparison — single-symbol only in v1.

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI, 6 tabs |
| `finnhub_client.py` | Typed per-endpoint functions, returns Optional, never raises |
| `cache.py` | SQLite TTL cache at `.cache/finnhub.db` |
| `.streamlit/config.toml` | Theme + server settings |
| `requirements.txt` | `streamlit>=1.30` only |

## Using the client outside Streamlit

```
>>> from finnhub_client import quote
>>> quote("AAPL")
{'c': 296.25, 'd': 1.95, 'dp': 0.6626, ...}
```

## Cache

| Endpoint family | TTL |
|---|---|
| `/quote` | 60 s |
| `/news`, market status | 5 min |
| All other per-symbol | 1 hour |
| Reference data (country, COVID, FDA, IPO, exchanges) | 24 hours |

To wipe: delete `dashboard/.cache/finnhub.db`. The dashboard rebuilds it.