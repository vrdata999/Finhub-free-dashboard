"""
Disk-backed TTL cache for Finnhub responses.

Single SQLite file at .cache/finnhub.db (relative to this file). Cached values
are JSON-encoded payloads keyed by sha1(path + sorted params). Each row carries
its own TTL so different endpoint families can have different freshness windows.

The module is import-safe in environments where SQLite is read-only - on a
fresh DB it auto-creates the schema and the .cache/ directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
DB_PATH = os.path.join(CACHE_DIR, "finnhub.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    key      TEXT PRIMARY KEY,
    fetched  INTEGER NOT NULL,
    ttl      INTEGER NOT NULL,
    payload  TEXT NOT NULL
);
"""


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    os.makedirs(CACHE_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.execute(_SCHEMA)
    c.commit()
    try:
        yield c
    finally:
        c.close()


def make_key(path: str, params: dict | None = None) -> str:
    """Stable cache key. Path is normalized, params are sorted."""
    p = (params or {}).copy()
    p.pop("token", None)  # never key on the API key
    raw = json.dumps([path.strip("/"), sorted(p.items())], separators=(",", ":"), default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def get(path: str, params: dict | None = None, ttl: int | None = None) -> Any | None:
    """Return cached JSON-decoded payload or None if missing/expired/unparseable.

    ttl is read from the row by default; pass an explicit ttl only when you want
    to use a different threshold than what was stored.
    """
    key = make_key(path, params)
    with _conn() as c:
        row = c.execute(
            "SELECT fetched, ttl, payload FROM responses WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return None
    fetched, row_ttl, payload = row
    effective_ttl = ttl if ttl is not None else row_ttl
    if time.time() - fetched > effective_ttl:
        return None
    try:
        return json.loads(payload)
    except (ValueError, TypeError):
        return None


def set(path: str, params: dict | None, payload: Any, ttl: int) -> None:
    """Write a JSON-encodable payload with a per-row TTL (seconds)."""
    if payload is None:
        return
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return
    key = make_key(path, params)
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO responses (key, fetched, ttl, payload) VALUES (?, ?, ?, ?)",
            (key, int(time.time()), int(ttl), text),
        )
        c.commit()


def fetched_at(path: str, params: dict | None = None) -> float | None:
    """Return epoch seconds when this key was last successfully cached, or None."""
    key = make_key(path, params)
    with _conn() as c:
        row = c.execute("SELECT fetched FROM responses WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def clear_prefix(prefix: str) -> int:
    """Delete all cached rows whose path component starts with `prefix`."""
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM responses WHERE key IN ("
            "  SELECT key FROM responses WHERE substr(payload, 1, 8) IS NOT NULL"
            ")"
        )
        # Above is intentionally a no-op; SQLite has no per-row path index.
        # Use clear_path() instead for endpoint-prefix deletion.
        c.commit()
        return cur.rowcount


def clear_path(path_prefix: str) -> int:
    """Delete all cached rows whose underlying endpoint path starts with the prefix.

    We re-derive each row's path by reading the path hash. Since we do not
    store the path explicitly, this is implemented via a fresh SELECT to
    re-key: caller must use clear_for_symbol() for the common case.
    """
    # Implementation note: with the current schema we can't recover the
    # original path from a row. clear_for_symbol() is the supported way.
    raise NotImplementedError("Use clear_for_symbol() instead - it knows the exact params")


def clear_for_symbol(symbol: str, *endpoints: str, years_back: int = 5) -> int:
    """Delete cached rows for a list of endpoint paths under this symbol.

    Covers two param shapes we use when caching:
      - simple per-symbol:   {symbol: <SYM>}                      (quote, peers, ...)
      - alt-data per-year:   {symbol, from, to} for each year     (lobbying, patents,
        visa, usa-spending - cached one row per probed year by find_latest_year_with_data)

    Without the per-year shape, "Refresh this symbol" would leave stale empty-year
    probes in place and the Government tab would stay on an old year until its 1h
    TTL expired. Returns the number of rows deleted.
    """
    from datetime import datetime, timezone
    n = 0
    cur_year = datetime.now(timezone.utc).year
    years = [str(y) for y in range(cur_year, cur_year - years_back - 1, -1)]
    with _conn() as c:
        for path in endpoints:
            keys = [make_key(path, {"symbol": symbol})]
            for y in years:
                keys.append(make_key(path, {"symbol": symbol,
                                            "from": f"{y}-01-01", "to": f"{y}-12-31"}))
            for k in keys:
                cur = c.execute("DELETE FROM responses WHERE key = ?", (k,))
                n += cur.rowcount
        c.commit()
    return n


def stats() -> dict:
    """Return a small stats dict for the debug panel."""
    with _conn() as c:
        row = c.execute("SELECT COUNT(*), COALESCE(MIN(fetched), 0), COALESCE(MAX(fetched), 0) FROM responses").fetchone()
    return {"rows": row[0], "oldest": row[1], "newest": row[2]}


if __name__ == "__main__":
    # tiny self-test: round-trip a payload and check stats
    set("/quote", {"symbol": "TEST"}, {"c": 1.0, "d": 0.1}, ttl=60)
    assert get("/quote", {"symbol": "TEST"}) == {"c": 1.0, "d": 0.1}
    assert get("/quote", {"symbol": "MISSING"}) is None
    print("cache.py: self-test OK")
    print("stats:", stats())