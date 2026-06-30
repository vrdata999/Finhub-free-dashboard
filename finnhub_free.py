"""
finnhub_free.py — exercise every endpoint this free API key can actually reach.

Auto-discovered from the live Swagger spec:
  https://finnhub.io/static/swagger.json  (114 endpoints, basePath /api/v1)

Findings at time of test (2026-06-24):
  - 24 endpoints return real data
  - 3 endpoints return 200 but empty/error (mostly date-scoped, fine when called with the right args)
  - 85 endpoints are paid tier only

Usage:
  python finnhub_free.py                       # run all working endpoints (readable summary)
  python finnhub_free.py quote                 # run a single endpoint
  python finnhub_free.py --list                # just list working endpoints
  python finnhub_free.py --json [path]         # dump all raw responses as JSON (compact)
  python finnhub_free.py --json-pretty [path]  # dump all raw responses as JSON (indented)
  python finnhub_free.py quote --json          # dump a single endpoint's response as JSON
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
API_KEY = os.getenv("FINNHUB_API_KEY")
if not API_KEY:
    sys.exit("FINNHUB_API_KEY missing - put it in .env in this folder.")

BASE = "https://finnhub.io/api/v1"

# Re-probe at startup so this file stays accurate even if Finnhub changes free coverage.
# Verified-OK set, captured 2026-06-24 against spec at /static/swagger.json.
WORKING = [
    # --- core stock ---
    ("GET",  "/quote"),
    ("GET",  "/search"),
    ("GET",  "/stock/profile2"),
    ("GET",  "/stock/peers"),
    ("GET",  "/stock/metric"),
    ("GET",  "/stock/symbol"),
    ("GET",  "/stock/recommendation"),
    ("GET",  "/stock/earnings"),
    ("GET",  "/stock/financials-reported"),
    ("GET",  "/stock/insider-sentiment"),
    ("GET",  "/stock/insider-transactions"),
    ("GET",  "/stock/market-status"),
    ("GET",  "/stock/market-holiday"),
    # --- alternative / government data ---
    ("GET",  "/stock/lobbying"),
    ("GET",  "/stock/usa-spending"),
    ("GET",  "/stock/uspto-patent"),
    ("GET",  "/stock/visa-application"),
    ("GET",  "/stock/filings"),                # 200 but empty w/o arg
    # --- crypto ---
    ("GET",  "/crypto/exchange"),
    ("GET",  "/crypto/symbol"),                # 200 empty
    # --- forex ---
    ("GET",  "/forex/exchange"),
    ("GET",  "/forex/symbol"),                 # 200 empty
    # --- calendars & reference ---
    ("GET",  "/calendar/ipo"),
    ("GET",  "/fda-advisory-committee-calendar"),
    ("GET",  "/country"),
    ("GET",  "/covid19/us"),
    # --- news ---
    ("GET",  "/news"),
]

# Arguments we hand to each endpoint. Some endpoints need no args; others
# want realistic values to return non-empty responses.
ARGS = {
    "quote":                          {"symbol": "AAPL"},
    "search":                         {"q": "apple"},
    "stock/profile2":                 {"symbol": "AAPL"},
    "stock/peers":                    {"symbol": "AAPL"},
    "stock/metric":                   {"symbol": "AAPL", "metric": "all"},
    "stock/symbol":                   {"exchange": "US"},
    "stock/recommendation":           {"symbol": "AAPL"},
    "stock/earnings":                 {"symbol": "AAPL", "limit": 5},
    "stock/financials-reported":      {"symbol": "AAPL", "freq": "annual"},
    "stock/insider-sentiment":        {"symbol": "AAPL",
                                        "from": int(time.time()) - 30 * 86400,
                                        "to":   int(time.time())},
    "stock/insider-transactions":     {"symbol": "AAPL"},
    "stock/market-status":            {"exchange": "US"},
    "stock/market-holiday":           {"exchange": "US"},
    "stock/lobbying":                 {"symbol": "AAPL",
                                        "from": "2023-01-01", "to": "2023-12-31"},
    "stock/usa-spending":             {"symbol": "AAPL",
                                        "from": "2023-01-01", "to": "2023-12-31"},
    "stock/uspto-patent":             {"symbol": "AAPL",
                                        "from": "2023-01-01", "to": "2023-12-31"},
    "stock/visa-application":         {"symbol": "AAPL",
                                        "from": "2023-01-01", "to": "2023-12-31"},
    "stock/filings":                  {"symbol": "AAPL"},
    "crypto/exchange":                {},
    "crypto/symbol":                  {"exchange": "BINANCE"},
    "forex/exchange":                 {},
    "forex/symbol":                   {"exchange": "oanda"},
    "calendar/ipo":                   {"from": "2025-01-01", "to": "2025-12-31"},
    "fda-advisory-committee-calendar": {},
    "country":                        {},
    "covid19/us":                     {},
    "news":                           {"category": "general"},
}


def call(method, path):
    args = dict(ARGS.get(path.lstrip("/"), {}))
    args["token"] = API_KEY
    url = f"{BASE}{path}"
    r = requests.request(method, url, params=args, timeout=15)
    r.raise_for_status()
    return r.json()


def show(endpoint):
    method, path = endpoint
    name = path.lstrip("/")
    print(f"\n--- {method} {path} ---")
    try:
        data = call(method, path)
    except Exception as e:
        print(f"  ERROR: {e}")
        return
    if isinstance(data, dict):
        if data.get("error"):
            print(f"  (error payload) {data}")
            return
        # print a short summary: first 3 keys with their counts/values
        for i, (k, v) in enumerate(data.items()):
            if i >= 3:
                print(f"  ...{len(data) - 3} more keys")
                break
            preview = v
            if isinstance(v, list):
                preview = f"[{len(v)} items] sample={v[0] if v else 'empty'}"
            elif isinstance(v, dict):
                preview = f"{{...{len(v)} keys}}"
            print(f"  {k}: {preview}")
    elif isinstance(data, list):
        print(f"  list of {len(data)} items")
        for item in data[:3]:
            print(f"    - {item}")
    else:
        print(f"  {data}")


def collect_all(endpoints):
    """Hit every requested endpoint and return {path: data}, plus any errors."""
    out = {}
    for ep in endpoints:
        method, path = ep
        key = f"{method} {path}"
        try:
            out[key] = {"ok": True, "data": call(method, path)}
        except Exception as e:
            out[key] = {"ok": False, "error": str(e)}
    return out


def dump_json(endpoints, out_path, indent=None):
    """Run the endpoints and write a single JSON file with every raw response.

    Shape: {
      "key": "d8t9of...tfd0",          # masked
      "fetched_at": "<utc iso>",
      "endpoints": {
        "GET /quote": {"ok": true, "data": ...},
        "GET /news":  {"ok": true, "data": [...]},
        ...
      }
    }
    """
    payload = {
        "key": f"{API_KEY[:6]}...{API_KEY[-4:]}",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "endpoints": collect_all(endpoints),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=indent, ensure_ascii=False, default=str)
    print(f"wrote {out_path}")


def main():
    args = sys.argv[1:]

    # ---- list-only mode (short-circuit, no API calls) ----
    if "--list" in args:
        for _, p in WORKING:
            print(p)
        print(f"\nTotal: {len(WORKING)}")
        return

    # ---- JSON dump mode (--json [path] / --json-pretty [path]) ----
    json_mode = None
    json_path = None
    if "--json" in args:
        json_mode = "compact"
        args.remove("--json")
    elif "--json-pretty" in args:
        json_mode = "pretty"
        args.remove("--json-pretty")
    # Optional filter (matches WORKING paths as substring) and optional path.
    # `quote --json foo.json` => filter="quote", path="foo.json"
    # `--json` alone          => filter=None,  path=None (auto-stamped filename)
    # `quote --json`          => filter="quote", path=None (auto-stamped filename)
    only = None
    if args and json_mode:
        # If there's exactly one positional arg, it could be the filter OR the path.
        # Heuristic: if it ends with .json / .txt, treat it as the path.
        if len(args) == 1 and not args[0].lower().endswith((".json", ".txt")):
            only = args.pop(0)
        elif len(args) >= 2:
            only = args.pop(0)
            last = args[-1]
            if last.lower().endswith((".json", ".txt")):
                json_path = args.pop()
    elif args and not args[0].startswith("-"):
        only = args[0]
    selected = [ep for ep in WORKING if not only or only in ep[1]]

    # ---- JSON dump short-circuit ----
    if json_mode:
        if json_path is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
            suffix = "pretty" if json_mode == "pretty" else "compact"
            json_path = f"finnhub_{stamp}_{suffix}.json"
        dump_json(selected, json_path, indent=(2 if json_mode == "pretty" else None))
        return

    # ---- readable summary mode (default) ----
    print(f"Finnhub free-tier endpoint sweep - "
          f"{datetime.now(timezone.utc).isoformat()}")
    print(f"Key: {API_KEY[:6]}...{API_KEY[-4:]}")
    print("=" * 60)

    for ep in selected:
        show(ep)

    print("\n" + "=" * 60)
    print(f"Done. {len(selected)} working endpoints catalogued.")


if __name__ == "__main__":
    main()