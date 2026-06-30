"""
Probe every Finnhub endpoint with the free API key and tag each as
  ok          - 200 with non-empty JSON
  empty       - 200 but {} or {"error": ...} (still reachable)
  forbidden   - 403, 401, or 404 -> paid tier
  other       - any other HTTP code
"""
import json
import sys
import time
import requests

SPEC = json.load(open(r"C:\Users\thesh\AppData\Local\Temp\finnhub_swagger.json", encoding="utf-8"))
API_KEY = open(r"C:\Users\thesh\Desktop\finhub data\.env").read().strip().split("=", 1)[1]
BASE = "https://finnhub.io/api/v1"

# AAPL test parameters
AAPL_PARAMS = {
    "symbol": "AAPL",
    "from": int(time.time()) - 30 * 86400,
    "to": int(time.time()),
    "resolution": "D",
    "date": "2025-06-15",
    "isin": "US0378331005",
    "code": "GDP",
    "category": "general",
    "q": "apple",
    "exchange": "US",
    "metric": "all",
    "statement": "bs",
    "freq": "annual",
    "cusip": "037833100",
    "cik": "0000320193",
    "theme": "Innovation",
    "region": "NA",
    "indicator": "rsi",
    "field": "accessNo",
    "documentId": "https://finnhub.io/docs/api/",
    "accessNumber": "0000320193-20-000096",
    "limit": 10,
    "skip": 0,
    "airline": "AAL",
    "id": "AAPL_2020_q1",
    "freq": "annual",
}

results = {"ok": [], "empty": [], "forbidden": [], "other": []}

for path, ops in SPEC["paths"].items():
    for method, op in ops.items():
        if method.lower() not in ("get", "post"):
            continue
        params = {}
        for p in op.get("parameters", []):
            if p.get("in") == "query":
                name = p["name"]
                if name in AAPL_PARAMS:
                    params[name] = AAPL_PARAMS[name]
                elif p.get("required"):
                    params[name] = p.get("default", "AAPL") if name not in ("token",) else API_KEY
        params["token"] = API_KEY
        try:
            r = requests.request(method.upper(), f"{BASE}{path}", params=params, timeout=10)
            code = r.status_code
            body = r.text
            try:
                data = r.json()
            except Exception:
                data = None
            if code == 200:
                empty = (
                    data in (None, {}, [])
                    or (isinstance(data, dict) and ("error" in data or data.get("s") == "no_data"))
                )
                bucket = "empty" if empty else "ok"
            elif code in (401, 403, 404):
                bucket = "forbidden"
            else:
                bucket = "other"
        except Exception as e:
            bucket = "other"
            code = str(e)
        results[bucket].append((method.upper(), path, code))

for bucket, label in [
    ("ok", "OK (200 with real data)"),
    ("empty", "OK but empty / 200 no_data / 200 with error"),
    ("forbidden", "Forbidden (401/403/404) - paid tier"),
    ("other", "Other"),
]:
    print(f"\n=== {label} : {len(results[bucket])} ===")
    for m, p, c in sorted(results[bucket]):
        print(f"  {m:5s} {str(c):>4s}  {p}")

print(f"\nTotals: ok={len(results['ok'])} empty={len(results['empty'])} "
      f"forbidden={len(results['forbidden'])} other={len(results['other'])}")
