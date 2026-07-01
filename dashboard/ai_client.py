"""
OpenRouter-backed AI analysis over the Finnhub data the dashboard already pulls.

Design:
  - One function per analysis section (insider, gov, financials, overall). Each
    fetches its inputs from finnhub_client (all cache-backed, so free on repeat)
    and builds a compact, token-budgeted prompt.
  - `analyze()` is the only thing that hits OpenRouter; it is cache-wrapped so a
    repeated click or re-render is free. Cache key is deterministic on
    (symbol, section, model) so a symbol's analysis is stable until its TTL lapses.
  - Never raises. Returns a markdown string, or None if not configured / errored.
  - Reads ../.env (same key file the rest of the project uses).
"""
from __future__ import annotations

import os
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv

from cache import get as cache_get, set as cache_set, make_key, fetched_at as _fetched_at
import finnhub_client as fb

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(dotenv_path=os.path.join(_PROJECT_ROOT, ".env"))

API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
# Default to a :free model so the dashboard works on a $0 OpenRouter account.
# Paid models (e.g. anthropic/claude-sonnet-4.6) 402 on a free tier.
DEFAULT_MODEL = "google/gemma-4-31b-it:free"
MODEL = os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

BASE = "https://openrouter.ai/api/v1/chat/completions"
TTL_AI = 6 * 3600  # analyses sit on slowly-changing finnhub data; 6h is plenty

_SYSTEM = (
    "You are a concise, objective equity research analyst working from Finnhub "
    "free-tier data. Interpret ONLY the data provided — do not hallucinate "
    "numbers, dates, or events. Be specific and quantify claims. Explicitly flag "
    "gaps or stale feeds (e.g. USPTO patent lag ~18-24 months, empty insider "
    "feeds). Use short markdown bullets. End with one line starting "
    "'Bottom line:'. Stay under ~200 words unless asked for more."
)


# ---- config / cache helpers --------------------------------------------- #

def is_configured() -> bool:
    return bool(API_KEY)


def model() -> str:
    return MODEL


def _cache_params(section: str, symbol: str) -> dict:
    return {"symbol": symbol.upper(), "section": section, "model": MODEL}


def fetched_at(section: str, symbol: str) -> float | None:
    """Epoch seconds this analysis was last generated, or None."""
    return _fetched_at("/ai/analyze", _cache_params(section, symbol))


def clear_for_symbol(symbol: str) -> int:
    """Delete cached AI analyses for a symbol across all sections."""
    import sqlite3
    from cache import DB_PATH
    n = 0
    with sqlite3.connect(DB_PATH) as c:
        for section in ("overall", "financials", "gov", "insider"):
            k = make_key("/ai/analyze", _cache_params(section, symbol))
            cur = c.execute("DELETE FROM responses WHERE key = ?", (k,))
            n += cur.rowcount
        c.commit()
    return n


# ---- low-level chat ----------------------------------------------------- #

LAST_ERROR: str | None = None


def last_error() -> str | None:
    return LAST_ERROR


# Free models 429 unpredictably (upstream rate-limiting at peak). Try the
# configured model first, then fall back through alternates so a single
# analysis succeeds even when the primary is overloaded.
_FALLBACK_MODELS = [
    "openai/gpt-oss-120b:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]


def _chat(prompt: str, max_tokens: int = 800) -> str | None:
    global LAST_ERROR
    if not API_KEY:
        LAST_ERROR = "OPENROUTER_API_KEY not set in .env"
        return None
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "X-Title": "Finnhub Dashboard",
    }
    body = {
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }
    candidates = [MODEL, *_FALLBACK_MODELS]
    last_err = None
    for mdl in candidates:
        body["model"] = mdl
        try:
            r = requests.post(BASE, headers=headers, json=body, timeout=60)
        except requests.RequestException as e:
            last_err = f"{mdl}: {type(e).__name__}: {e}"
            continue
        if r.status_code == 200:
            try:
                LAST_ERROR = None
                return r.json()["choices"][0]["message"]["content"]
            except (KeyError, ValueError) as e:
                last_err = f"{mdl}: bad response: {e}"
                continue
        snippet = r.text[:160].replace("\n", " ")
        last_err = f"{mdl}: HTTP {r.status_code}: {snippet}"
        # 429 / 5xx / etc. -> try the next candidate
    LAST_ERROR = last_err or "all models failed"
    return None


def analyze(prompt: str, section: str, symbol: str, max_tokens: int = 800) -> str | None:
    """Cache-wrapped chat. Key is deterministic on (symbol, section, model)."""
    params = _cache_params(section, symbol)
    cached = cache_get("/ai/analyze", params, ttl=TTL_AI)
    if cached is not None:
        return cached
    text = _chat(prompt, max_tokens=max_tokens)
    if text:
        cache_set("/ai/analyze", params, text, ttl=TTL_AI)
    return text


# ---- compact serialization -------------------------------------------- #

def _df_text(df: pd.DataFrame | None, cols: list[str], n: int = 8) -> str:
    """Compact text rendering of up to `n` rows of selected columns."""
    if df is None or df.empty:
        return "(none)"
    have = [c for c in cols if c in df.columns]
    if not have:
        return "(no matching columns)"
    rows = df[have].head(n).to_dict("records")
    lines = []
    for r in rows:
        parts = [f"{c}={r[c]}" for c in have if pd.notna(r.get(c))]
        lines.append("  " + ", ".join(parts))
    return "\n".join(lines) if lines else "(none)"


def _fmt_money(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f) >= 1e9:
        return f"${f/1e9:.2f}B"
    if abs(f) >= 1e6:
        return f"${f/1e6:.2f}M"
    if abs(f) >= 1e3:
        return f"${f/1e3:.1f}K"
    return f"${f:.0f}"


# ---- per-section analysis --------------------------------------------- #

def analyze_insider(symbol: str) -> str | None:
    q = fb.quote(symbol) or {}
    it = fb.insider_transactions(symbol)
    sent = fb.insider_sentiment_from_transactions(it) if it is not None and not it.empty else None
    price = q.get("c")
    om = pd.DataFrame()
    if it is not None and not it.empty and "transactionCode" in it.columns:
        om = it[it["transactionCode"].isin(["P", "S"])].copy()
    score = buyers = sellers = last3 = None
    if sent is not None and not sent.empty:
        tb = float(sent["buy_shares"].sum())
        ts = float(sent["sell_shares"].sum())
        score = (tb - ts) / (tb + ts) * 100 if (tb + ts) > 0 else 0.0
        buyers = int(om.loc[om["change"] > 0, "name"].nunique()) if not om.empty and "name" in om.columns else 0
        sellers = int(om.loc[om["change"] < 0, "name"].nunique()) if not om.empty and "name" in om.columns else 0
        last3 = float(sent.head(3)["net_shares"].sum())
    prompt = (
        f"Analyze insider sentiment for {symbol} "
        f"(current price ${price}). Derived ONLY from open-market Form 4 "
        f"transactions (codes P/S); grants, exercises, gifts, tax excluded.\n\n"
        f"Open-market transactions (last ~12 months):\n"
        f"{_df_text(om, ['transactionDate','name','transactionCode','change','transactionPrice'], n=15)}\n\n"
        f"Monthly derived sentiment (newest first):\n"
        f"{_df_text(sent, ['month','net_shares','buy_shares','sell_shares','score'], n=12)}\n\n"
        f"Overall score={score}, buyers/sellers={buyers}/{sellers}, "
        f"3-month net shares={last3}.\n"
        f"Interpret: distinguish profit-taking after gains from bearish selling; "
        f"comment on timing of buys vs the price; flag thin sample size."
    )
    return analyze(prompt, "insider", symbol, max_tokens=600)


def analyze_gov(symbol: str) -> str | None:
    lob = fb.lobbying(symbol)
    pat = fb.uspto_patents(symbol)
    vis = fb.visa_applications(symbol)
    sp = fb.usa_spending(symbol)

    def newest(df, col):
        if df is None or df.empty or col not in df.columns:
            return "—"
        try:
            return str(df[col].max())
        except Exception:
            return "—"

    top_issues = ""
    if lob is not None and not lob.empty and "description" in lob.columns:
        vc = lob["description"].fillna("(none)").value_counts().head(3)
        top_issues = "; ".join(f"{k} ({v})" for k, v in vc.items())

    top_agencies = ""
    if sp is not None and not sp.empty and "awardingAgencyName" in sp.columns:
        vc = sp["awardingAgencyName"].fillna("(none)").value_counts().head(3)
        top_agencies = "; ".join(f"{k} ({v})" for k, v in vc.items())

    total_contracts = float(sp["totalValue"].sum()) if (sp is not None and not sp.empty and "totalValue" in sp.columns) else 0.0

    prompt = (
        f"Analyze government / alternative data for {symbol}.\n\n"
        f"Senate lobbying: {0 if lob is None or lob.empty else len(lob)} filings, "
        f"newest {newest(lob,'year')} (by period year), top issues: {top_issues or '—'}\n"
        f"USPTO patents: {0 if pat is None or pat.empty else len(pat)} filings, "
        f"newest filingDate {newest(pat,'filingDate')} (NOTE: feed lags ~18-24 months)\n"
        f"H-1B visa applications: {0 if vis is None or vis.empty else len(vis)}, "
        f"newest receivedDate {newest(vis,'receivedDate')}\n"
        f"Federal contracts (USA Spending): {0 if sp is None or sp.empty else len(sp)} awards, "
        f"total value {_fmt_money(total_contracts)}, newest actionDate {newest(sp,'actionDate')}, "
        f"top agencies: {top_agencies or '—'}\n\n"
        f"Interpret: what the company lobbies on, R&D/patent pipeline signal, "
        f"talent demand (H-1B), and federal-customer reliance. Note data gaps."
    )
    return analyze(prompt, "gov", symbol, max_tokens=700)


def analyze_financials(symbol: str) -> str | None:
    q = fb.quote(symbol) or {}
    prof = fb.company_profile(symbol) or {}
    metrics = fb.metrics_for_display(fb.basic_financials(symbol))
    es = fb.earnings_surprises(symbol, limit=6)
    fr = fb.financials_reported(symbol, freq="annual")
    xbrl = fb.latest_xbrl_summary(fr)
    prompt = (
        f"Analyze financials for {symbol} — {prof.get('name', symbol)} "
        f"({prof.get('finnhubIndustry','—')}, {prof.get('country','—')}). "
        f"Price ${q.get('c')} (day {q.get('l')}–{q.get('h')}). "
        f"Market cap {_fmt_money((prof.get('marketCapitalization') or 0)*1e6)}.\n\n"
        f"Key metrics (label=value):\n{_df_text(metrics, ['Metric','Value'], n=15)}\n\n"
        f"Earnings surprises (recent quarters):\n"
        f"{_df_text(es, ['period','estimate','actual','surprise','surprisePercent'], n=6)}\n\n"
        f"Latest 10-K headline figures:\n"
        f"{_df_text(xbrl, ['Line item','Value','Unit'], n=12)}\n\n"
        f"Interpret profitability, growth, balance-sheet strength, and earnings "
        f"quality. Flag any partial or missing data."
    )
    return analyze(prompt, "financials", symbol, max_tokens=800)


def analyze_overall(symbol: str) -> str | None:
    q = fb.quote(symbol) or {}
    prof = fb.company_profile(symbol) or {}
    metrics = fb.metrics_for_display(fb.basic_financials(symbol))
    es = fb.earnings_surprises(symbol, limit=4)
    rec = fb.recommendation_trend(symbol)
    it = fb.insider_transactions(symbol)
    sent = fb.insider_sentiment_from_transactions(it) if it is not None and not it.empty else None
    news = fb.news("general")

    score = None
    if sent is not None and not sent.empty:
        tb = float(sent["buy_shares"].sum())
        ts = float(sent["sell_shares"].sum())
        score = (tb - ts) / (tb + ts) * 100 if (tb + ts) > 0 else 0.0
    rec_latest = rec.iloc[0].to_dict() if (rec is not None and not rec.empty) else {}
    headlines = []
    if news is not None and not news.empty:
        headlines = news.head(5)["headline"].tolist()

    prompt = (
        f"Write a ~250-word equity research brief for {symbol} — "
        f"{prof.get('name', symbol)} ({prof.get('finnhubIndustry','—')}, "
        f"{prof.get('country','—')}, {prof.get('exchange','—')}). "
        f"Price ${q.get('c')} ({q.get('dp')}% today). "
        f"Market cap {_fmt_money((prof.get('marketCapitalization') or 0)*1e6)}. "
        f"52-week high/low are in the metrics below.\n\n"
        f"Key metrics:\n{_df_text(metrics, ['Metric','Value'], n=10)}\n\n"
        f"Latest earnings:\n{_df_text(es, ['period','actual','estimate','surprisePercent'], n=4)}\n"
        f"Latest analyst recommendations: {rec_latest}\n"
        f"Derived insider score: {score} (from open-market P/S transactions)\n"
        f"Recent market news headlines:\n" + "\n".join(f"- {h}" for h in headlines) +
        f"\n\nCover: business snapshot, valuation & financial health, analyst "
        f"stance, insider signal, a recent-news angle, and 2-3 key risks. "
        f"Note: this is free-tier data — no price targets, transcripts, or "
        f"estimates available."
    )
    return analyze(prompt, "overall", symbol, max_tokens=1000)


if __name__ == "__main__":
    if not is_configured():
        print("OPENROUTER_API_KEY not set in ../.env")
    else:
        print(f"configured: model={MODEL}")