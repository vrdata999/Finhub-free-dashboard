"""
Finnhub Research Dashboard.

Run with:  streamlit run app.py

Single-page Streamlit UI over the 27 free Finnhub endpoints.
Sidebar-controlled symbol picker, 6 tabs of research views.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Allow `python -m streamlit run app.py` from anywhere
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from finnhub_client import (  # noqa: E402
    basic_financials, company_profile, country_list, covid_us, crypto_exchanges,
    crypto_symbols, dashboard_version, earnings_surprises, fda_calendar,
    financials_reported, forex_exchanges, forex_symbols,
    insider_sentiment_from_transactions, insider_transactions,
    ipo_calendar, latest_xbrl_summary, lobbying,
    market_holiday, market_status, metrics_for_display, news, peers, quote,
    recommendation_trend, search, sec_filings, symbol_search, usa_spending,
    uspto_patents, visa_applications,
)
from cache import clear_for_symbol, fetched_at, stats  # noqa: E402
import ai_client as ai  # noqa: E402

st.set_page_config(
    page_title="Finnhub Research Dashboard",
    page_icon=":chart_with_upwards_trend:",
    layout="wide",
)


# ---- helpers ------------------------------------------------------------ #

def _fmt_ts(epoch: float | None) -> str:
    if not epoch:
        return "—"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def _stale_badge(path: str, params: dict) -> str:
    ts = fetched_at(path, params)
    if not ts:
        return ""
    age = (datetime.now(timezone.utc).timestamp() - ts) / 60
    if age < 5:
        return f":small_blue_diamond: cached {age:.0f}m ago"
    return f":alarm_clock: cached {age:.0f}m ago"


def _empty(label: str):
    st.info(f"No data for {label}.")


def _price_color(delta: float) -> str:
    return "normal" if abs(delta) < 1e-9 else ("off" if delta < 0 else "inverse")


# ---- sidebar ------------------------------------------------------------ #

with st.sidebar:
    st.header(":mag: Symbol")
    symbol = st.text_input("Ticker", value="AAPL", max_chars=10).strip().upper()
    asset_class = st.radio("Asset class", ("Stock", "Crypto", "Forex"), horizontal=True,
                           label_visibility="collapsed")
    refresh = st.button(":arrows_counterclockwise: Refresh this symbol",
                        help="Clear the per-symbol cache so the next render re-fetches.")
    st.divider()
    st.caption(dashboard_version())
    s = stats()
    st.caption(f"Cache: {s['rows']} rows, oldest { _fmt_ts(s['oldest']) }, newest { _fmt_ts(s['newest']) }")
    st.caption("Source: Finnhub free tier (60 req/min)")

if not symbol:
    st.warning("Type a ticker in the sidebar.")
    st.stop()

if refresh and asset_class == "Stock":
    sym_paths = [
        "/quote", "/stock/profile2", "/stock/peers", "/stock/metric",
        "/stock/recommendation", "/stock/earnings", "/stock/financials-reported",
        "/stock/insider-transactions", "/stock/insider-sentiment",
        "/stock/filings", "/stock/lobbying", "/stock/uspto-patent",
        "/stock/visa-application", "/stock/usa-spending",
    ]
    n = clear_for_symbol(symbol, *sym_paths)
    n += ai.clear_for_symbol(symbol)
    st.toast(f"Cleared {n} cached rows for {symbol}", icon=":recycle:")
    st.rerun()


# ---- title -------------------------------------------------------------- #

st.title(f":chart_with_upwards_trend:  {symbol}")
st.caption(f"Research dashboard · asset class: **{asset_class}**")


# ---- tab scaffolding ---------------------------------------------------- #

tabs = st.tabs([
    ":sparkles: Overview",
    ":bar_chart: Financials",
    ":male_detective: Analyst & Insider",
    ":classical_building: Government / Alt-Data",
    ":newspaper: Filings & News",
    ":earth_americas: Reference",
    ":brain: AI Analysis",
])


# ============================================================ #
# TAB 1 - OVERVIEW                                            #
# ============================================================ #
with tabs[0]:
    if asset_class != "Stock":
        st.warning(f"Overview tab is wired for stocks only. {asset_class} reference data lives in the Reference tab.")
    else:
        q = quote(symbol)
        prof = company_profile(symbol)

        if q:
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("Last", f"${q['c']:.2f}",
                          delta=f"{q['d']:+.2f} ({q['dp']:+.2f}%)",
                          delta_color=_price_color(q["d"]))
            with c2:
                st.metric("Day range", f"${q['l']:.2f} – ${q['h']:.2f}")
            with c3:
                st.metric("Open", f"${q['o']:.2f}")
            with c4:
                st.metric("Prev close", f"${q['pc']:.2f}")
        else:
            _empty("quote")

        if prof:
            mc = prof.get("marketCapitalization")
            mc_str = f"${mc / 1000:.2f} B" if mc else "—"
            st.subheader(f"{prof.get('name', symbol)}")
            cols = st.columns([1, 3])
            with cols[0]:
                logo = prof.get("logo")
                if logo:
                    st.image(logo, width=80)
            with cols[1]:
                st.markdown(
                    f"**{prof.get('finnhubIndustry', '—')}** · "
                    f"{prof.get('country', '—')} · "
                    f"{prof.get('exchange', '—')} ({prof.get('currency', '—')})  \n"
                    f"Market cap: **{mc_str}** · "
                    f"IPO: **{prof.get('ipo', '—')}** · "
                    f"Web: [{prof.get('weburl', '—')}]({prof.get('weburl', '#')})"
                )
        else:
            _empty("profile")

        # Daily-bar chart from /quote (free tier has no candles)
        if q:
            st.subheader(":candle: Today's bar (no historical OHLCV on free tier)")
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=["Today"],
                y=[q["h"] - q["l"]],
                base=[q["l"]],
                marker_color="rgba(0, 204, 150, 0.4)",
                width=0.4,
                showlegend=False,
                hovertemplate=f"Range: ${q['l']} – ${q['h']}<extra></extra>",
            ))
            for label, val, color in [
                ("Open", q["o"], "#888"),
                ("Current", q["c"], "#00CC96"),
                ("Prev close", q["pc"], "#FFA15A"),
            ]:
                fig.add_trace(go.Scatter(
                    x=["Today"], y=[val], mode="markers+text",
                    marker=dict(color=color, size=14, symbol="diamond"),
                    text=[f"{label}<br>${val}"], textposition="top center",
                    name=label, showlegend=True,
                ))
            fig.update_layout(
                yaxis_title="Price ($)",
                height=320,
                margin=dict(l=20, r=20, t=20, b=20),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig, width="stretch")
            st.caption("Daily bar only — historical OHLCV (`/stock/candle`) requires a paid tier.")

        # Market status + peers
        mcol, pcol = st.columns([1, 2])
        with mcol:
            st.subheader(":door: Market")
            ms = market_status("US")
            if ms:
                badge = ":green_circle:" if ms.get("isOpen") else ":red_circle:"
                st.markdown(f"{badge} **{ms.get('exchange', 'US')}**: "
                            f"{'Open' if ms.get('isOpen') else 'Closed'}")
                st.caption(_stale_badge("/stock/market-status", {"exchange": "US"}))
            else:
                _empty("market status")

        with pcol:
            st.subheader(":busts_in_silhouette: Peers")
            pr = peers(symbol)
            if pr:
                badges = "  ".join(f"`{p}`" for p in pr[:12])
                st.markdown(badges)
                st.caption(_stale_badge("/stock/peers", {"symbol": symbol}))
            else:
                _empty("peers")


# ============================================================ #
# TAB 2 - FINANCIALS                                          #
# ============================================================ #
with tabs[1]:
    if asset_class != "Stock":
        st.info("Financials apply to stocks only.")
    else:
        c1, c2 = st.columns([1, 1])
        with c1:
            st.subheader(":abacus: Basic financials")
            metrics = basic_financials(symbol)
            table = metrics_for_display(metrics)
            if table is not None and not table.empty:
                st.dataframe(table, hide_index=True, width="stretch")
                st.caption(_stale_badge("/stock/metric",
                                         {"symbol": symbol, "metric": "all"}))
            else:
                _empty("basic financials")

        with c2:
            st.subheader(":moneybag: Earnings surprises")
            es = earnings_surprises(symbol, limit=12)
            if es is not None and not es.empty:
                show_cols = [c for c in
                             ["period", "estimate", "actual", "surprise", "surprisePercent"]
                             if c in es.columns]
                st.dataframe(es[show_cols], hide_index=True, width="stretch")
                st.caption(_stale_badge("/stock/earnings",
                                         {"symbol": symbol, "limit": 12}))
            else:
                _empty("earnings")

        st.divider()
        st.subheader(":page_facing_up: Latest 10-K (XBRL as reported)")
        fr = financials_reported(symbol, freq="annual")
        summary = latest_xbrl_summary(fr)
        if summary is not None and not summary.empty:
            def _fmt(v, unit):
                if unit in ("USD", "usd"):
                    return f"${v/1e9:.2f} B"
                if unit in ("shares",):
                    return f"{v/1e9:.2f} B"
                if unit in ("USD/shares", "usd/share"):
                    return f"${v:.2f}"
                return f"{v:,.2f} {unit}"
            summary["Display"] = [_fmt(r["Value"], r["Unit"])
                                  for _, r in summary.iterrows()]
            st.dataframe(summary[["Line item", "Display"]],
                         hide_index=True, width="stretch")
            st.caption(_stale_badge("/stock/financials-reported",
                                     {"symbol": symbol, "freq": "annual"}))
        else:
            _empty("financials reported")


# ============================================================ #
# TAB 3 - ANALYST & INSIDER                                    #
# ============================================================ #
with tabs[2]:
    if asset_class != "Stock":
        st.info("Analyst coverage applies to stocks only.")
    else:
        st.subheader(":bar_chart: Analyst recommendations")
        rec = recommendation_trend(symbol)
        if rec is not None and not rec.empty:
            cats = ["strongBuy", "buy", "hold", "sell", "strongSell"]
            colors = ["#00CC96", "#3D9970", "#FFA15A", "#FF4136", "#85144b"]
            fig = go.Figure()
            for c, col in zip(cats, colors):
                if c in rec.columns:
                    fig.add_trace(go.Bar(
                        x=rec["period"], y=rec[c], name=c, marker_color=col,
                        hovertemplate="%{x}<br>" + c + ": %{y}<extra></extra>",
                    ))
            fig.update_layout(
                barmode="stack", height=320,
                yaxis_title="# analysts", xaxis_title="Month",
                margin=dict(l=20, r=20, t=20, b=20),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig, width="stretch")
            st.caption(_stale_badge("/stock/recommendation", {"symbol": symbol}))
        else:
            _empty("analyst recommendations")

        c1, c2 = st.columns(2)
        with c1:
            st.subheader(":briefcase: Insider transactions (top 25)")
            it = insider_transactions(symbol)
            if it is not None and not it.empty:
                cols = [c for c in ["filingDate", "name", "share", "change",
                                    "transactionPrice", "transactionCode"]
                        if c in it.columns]
                st.dataframe(it.head(25)[cols], hide_index=True, width="stretch")
                st.caption(_stale_badge("/stock/insider-transactions", {"symbol": symbol}))
            else:
                _empty("insider transactions")

        with c2:
            st.subheader(":bar_chart: Insider sentiment (derived)")
            st.caption("Built from open-market Form 4 transactions (P/S only) "
                       "in the feed above — grants, exercises, gifts and tax "
                       "withholding are excluded. Score: −100 all-selling … "
                       "+100 all-buying.")
            sent = insider_sentiment_from_transactions(it)
            if sent is not None and not sent.empty:
                # overall summary across the whole window
                tot_buy = sent["buy_shares"].sum()
                tot_sell = sent["sell_shares"].sum()
                tot_denom = tot_buy + tot_sell
                overall_score = ((tot_buy - tot_sell) / tot_denom * 100
                                 if tot_denom > 0 else 0.0)
                last3 = sent.head(3)["net_shares"].sum()
                om = it[it["transactionCode"].isin(["P", "S"])] if "transactionCode" in it.columns else it
                buyers = om.loc[om["change"] > 0, "name"].nunique() if "name" in om.columns else 0
                sellers = om.loc[om["change"] < 0, "name"].nunique() if "name" in om.columns else 0

                m1, m2, m3 = st.columns(3)
                m1.metric("Open-market score", f"{overall_score:+.0f}")
                m2.metric("3-mo net shares", f"{last3:+,.0f}")
                m3.metric("Buyers / sellers", f"{buyers} / {sellers}")

                # stacked buy vs sell shares by month (chronological)
                chart_df = sent.sort_values("month")
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=chart_df["month"], y=chart_df["buy_shares"],
                    name="Bought", marker_color="#00CC96",
                    hovertemplate="%{x}<br>Bought: %{y:,.0f}<extra></extra>",
                ))
                fig.add_trace(go.Bar(
                    x=chart_df["month"], y=chart_df["sell_shares"],
                    name="Sold", marker_color="#FF4136",
                    hovertemplate="%{x}<br>Sold: %{y:,.0f}<extra></extra>",
                ))
                fig.update_layout(
                    barmode="stack", height=300,
                    yaxis_title="Shares", xaxis_title="Month",
                    margin=dict(l=20, r=20, t=20, b=20),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                )
                st.plotly_chart(fig, width="stretch")

                show = sent.copy()
                for c_ in ("net_shares", "buy_shares", "sell_shares"):
                    if c_ in show.columns:
                        show[c_] = show[c_].map(lambda v: f"{v:,.0f}")
                if "net_value" in show.columns:
                    show["net_value"] = show["net_value"].map(
                        lambda v: f"${v:,.0f}")
                if "score" in show.columns:
                    show["score"] = show["score"].map(lambda v: f"{v:+.0f}")
                st.dataframe(show, hide_index=True, width="stretch")
                st.caption(_stale_badge("/stock/insider-transactions",
                                         {"symbol": symbol}))
            else:
                _empty("insider sentiment (no open-market P/S transactions "
                       "in the recent feed for this symbol)")


# ============================================================ #
# TAB 4 - GOVERNMENT / ALT-DATA                                #
# ============================================================ #
with tabs[3]:
    if asset_class != "Stock":
        st.info("Government / alt-data is per-issuer; applies to stocks only.")
    else:
        # ---- Lobbying ----
        st.subheader(":classical_building: Senate lobbying")
        lob = lobbying(symbol)
        if lob is not None and not lob.empty:
            if "year" in lob.columns:
                y_newest = int(lob["year"].max())
                p_newest = lob.loc[lob["year"] == y_newest, "period"].max() if "period" in lob.columns else ""
                newest_lbl = f"{y_newest} {p_newest}".strip()
                yspan = f"{int(lob['year'].min())}–{y_newest}"
            else:
                newest_lbl = yspan = "—"
            st.caption(f":calendar: {len(lob)} filings · {yspan} · newest {newest_lbl}")
            if "expenses" in lob.columns and "period" in lob.columns:
                agg = (lob.dropna(subset=["expenses"])
                          .groupby(["year", "period"], as_index=False)["expenses"].sum())
                if not agg.empty:
                    agg["label"] = agg["year"].astype(str) + " " + agg["period"]
                    agg = agg.sort_values(["year", "period"], ascending=False)
                    fig = go.Figure(go.Bar(
                        x=agg["label"], y=agg["expenses"],
                        hovertemplate="%{x}<br>$%{y:,.0f}<extra></extra>",
                    ))
                    fig.update_layout(height=240, yaxis_title="Expenses ($)",
                                      margin=dict(l=20, r=20, t=20, b=20))
                    st.plotly_chart(fig, width="stretch")
            cols = [c for c in ["year", "period", "expenses",
                                "description", "clientId", "documentUrl"]
                    if c in lob.columns]
            col_config = {}
            if "documentUrl" in cols:
                col_config["documentUrl"] = st.column_config.LinkColumn(
                    "Filing", help="LDA filing on Senate.gov",
                    display_text="open ↗")
            st.dataframe(lob.head(25)[cols],
                         column_config=col_config,
                         hide_index=True, width="stretch")
        else:
            _empty(f"lobbying (no data for {symbol} in the last 5 years)")

        # ---- Patents ----
        st.subheader(":scroll: USPTO patents")
        pat = uspto_patents(symbol)
        if pat is not None and not pat.empty:
            newest = pat["filingDate"].max() if "filingDate" in pat.columns else "—"
            st.caption(f":calendar: {len(pat)} filings · newest {newest} "
                       f"(Finnhub patent feed lags ~18-24 months)")
            c1, c2 = st.columns([1, 2])
            with c1:
                st.metric("Filings", len(pat))
                if "patentType" in pat.columns:
                    st.bar_chart(pat["patentType"].value_counts())
            with c2:
                cols = [c for c in ["filingDate", "patentType", "description",
                                    "patentNumber", "filingStatus"]
                        if c in pat.columns]
                st.dataframe(pat.head(15)[cols], hide_index=True, width="stretch")
        else:
            _empty(f"USPTO patents (no data for {symbol} in the last 5 years)")

        # ---- H-1B ----
        st.subheader(":passport_control: H-1B visa applications")
        visa = visa_applications(symbol)
        if visa is not None and not visa.empty:
            newest = visa["receivedDate"].max() if "receivedDate" in visa.columns else "—"
            st.caption(f":calendar: {len(visa)} applications · newest {newest}")
            cols = [c for c in ["receivedDate", "jobTitle", "employerName",
                                "worksiteCity", "worksiteState",
                                "wageRangeFrom", "wageRangeTo", "wageUnitOfPay",
                                "caseStatus"]
                    if c in visa.columns]
            # format wage range as one friendly column when both halves exist
            if "wageRangeFrom" in cols and "wageRangeTo" in cols and "wageUnitOfPay" in cols:
                visa["Wage"] = visa.apply(
                    lambda r: f"${r['wageRangeFrom']:,.0f}-${r['wageRangeTo']:,.0f}/{r['wageUnitOfPay']}"
                    if pd.notna(r.get("wageRangeFrom")) and pd.notna(r.get("wageRangeTo"))
                    else "—",
                    axis=1)
                cols = [c for c in cols if c not in ("wageRangeFrom", "wageRangeTo", "wageUnitOfPay")] + ["Wage"]
            st.dataframe(visa.head(25)[cols], hide_index=True, width="stretch")
        else:
            _empty(f"H-1B applications (no data for {symbol} in the last 5 years)")

        # ---- USA spending ----
        st.subheader(":moneybag: Federal contract spending (USA Spending)")
        sp = usa_spending(symbol)
        if sp is not None and not sp.empty:
            newest = sp["actionDate"].max() if "actionDate" in sp.columns else "—"
            st.caption(f":calendar: {len(sp)} contracts · newest {newest}")
            cols = [c for c in ["actionDate", "awardDescription",
                                "awardingAgencyName", "totalValue", "permalink"]
                    if c in sp.columns]
            col_config = {}
            if "totalValue" in cols:
                col_config["totalValue"] = st.column_config.NumberColumn(
                    "Amount", format="USD $ %d")
            if "permalink" in cols:
                col_config["permalink"] = st.column_config.LinkColumn(
                    "Award", help="USA Spending award page",
                    display_text="open ↗")
            st.dataframe(sp.head(25)[cols],
                         column_config=col_config,
                         hide_index=True, width="stretch")
        else:
            _empty(f"USA spending (no federal contracts for {symbol} in the last 5 years)")


# ============================================================ #
# TAB 5 - FILINGS & NEWS                                      #
# ============================================================ #
with tabs[4]:
    c1, c2 = st.columns([1, 1])
    with c1:
        st.subheader(":page_facing_up: SEC filings")
        sf = sec_filings(symbol)
        if sf is not None and not sf.empty:
            display = sf.head(25).copy()
            cols = [c for c in ["filedDate", "form", "accessNumber",
                                "filingUrl", "reportUrl"]
                    if c in display.columns]
            col_config = {}
            if "filingUrl" in cols:
                col_config["filingUrl"] = st.column_config.LinkColumn(
                    "Filing index", help="SEC filing index page",
                    display_text="index ↗")
            if "reportUrl" in cols:
                col_config["reportUrl"] = st.column_config.LinkColumn(
                    "Report", help="Primary document",
                    display_text="doc ↗")
            st.dataframe(display[cols],
                         column_config=col_config,
                         hide_index=True, width="stretch")
            st.caption(_stale_badge("/stock/filings", {"symbol": symbol}))
        else:
            _empty("SEC filings")

    with c2:
        st.subheader(":newspaper: Market news (general)")
        n = news("general")
        if n is not None and not n.empty:
            for _, row in n.head(20).iterrows():
                headline = row.get("headline", "")
                url = row.get("url", "#")
                src = row.get("source", "")
                when = row.get("datetime", "")
                st.markdown(
                    f"- [{headline}]({url})  \n"
                    f"  :small_blue_diamond: *{src}* · {when}"
                )
            st.caption(_stale_badge("/news", {"category": "general"}))
        else:
            _empty("news")


# ============================================================ #
# TAB 6 - REFERENCE                                            #
# ============================================================ #
with tabs[5]:
    if asset_class == "Crypto":
        st.subheader(":coin: Crypto reference")
        ce = crypto_exchanges()
        st.write("**Exchanges**:", ", ".join(ce or []))
        cs = crypto_symbols("BINANCE")
        if cs is not None and not cs.empty:
            st.dataframe(cs.head(50), hide_index=True, width="stretch")
        else:
            _empty("crypto symbols")
    elif asset_class == "Forex":
        st.subheader(":dollar: Forex reference")
        fe = forex_exchanges()
        st.write("**Exchanges**:", ", ".join(fe or []))
        fs = forex_symbols("oanda")
        if fs is not None and not fs.empty:
            st.dataframe(fs.head(50), hide_index=True, width="stretch")
        else:
            _empty("forex symbols")
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.subheader(":date: IPO calendar")
            ipo = ipo_calendar()
            if ipo is not None and not ipo.empty:
                cols = [c for c in ["date", "name", "symbol", "exchange",
                                    "price", "numberOfShares", "totalSharesValue",
                                    "status"]
                        if c in ipo.columns]
                col_config = {}
                if "numberOfShares" in cols:
                    col_config["numberOfShares"] = st.column_config.NumberColumn(
                        "Shares", format="%d")
                if "totalSharesValue" in cols:
                    col_config["totalSharesValue"] = st.column_config.NumberColumn(
                        "Total value", format="USD $ %d")
                st.dataframe(ipo.head(30)[cols],
                             column_config=col_config,
                             hide_index=True, width="stretch")
                span = f"{ipo['date'].min()} – {ipo['date'].max()}" if "date" in ipo.columns else ""
                st.caption(f"{len(ipo)} IPO records · {span} (newest first)")
            else:
                _empty("IPO calendar")

            st.subheader(":microscope: FDA advisory committee")
            fda = fda_calendar()
            if fda is not None and not fda.empty:
                cols = [c for c in ["fromDate", "toDate", "eventDescription", "url"]
                        if c in fda.columns]
                col_config = {}
                if "url" in cols:
                    col_config["url"] = st.column_config.LinkColumn(
                        "Meeting", help="FDA advisory committee page",
                        display_text="open ↗")
                st.dataframe(fda.head(15)[cols],
                             column_config=col_config,
                             hide_index=True, width="stretch")
                st.caption(f"{len(fda)} meetings")
            else:
                _empty("FDA calendar")

            st.subheader(":globe_with_meridians: COVID-19 US")
            cv = covid_us()
            if cv is not None and not cv.empty:
                st.dataframe(cv.head(20), hide_index=True, width="stretch")
            else:
                _empty("COVID-19 US")

        with c2:
            st.subheader(":world_map: Country metadata")
            ct = country_list()
            if ct is not None and not ct.empty:
                st.dataframe(ct.head(50), hide_index=True, width="stretch")
                st.caption(f"{len(ct)} countries")
            else:
                _empty("country list")

            st.subheader(":date: US market holidays")
            mh = market_holiday("US")
            if mh is not None and not mh.empty:
                cols = [c for c in ["atDate", "eventName"] if c in mh.columns]
                st.dataframe(mh[cols].head(15), hide_index=True, width="stretch")
            else:
                _empty("market holidays")

            st.subheader(":abacus: Symbol universe")
            sym = symbol_search("US")
            if sym is not None and not sym.empty:
                st.caption(f"{len(sym)} US symbols - use the search above to look one up.")
                st.dataframe(sym.head(20), hide_index=True, width="stretch")
            else:
                _empty("symbol universe")


# ============================================================ #
# TAB 7 - AI ANALYSIS                                          #
# ============================================================ #
with tabs[6]:
    st.header(":brain: AI analysis")
    if not ai.is_configured():
        st.warning(
            "AI analysis needs an OpenRouter key. Add "
            "`OPENROUTER_API_KEY=...` to `.env` in the project root "
            "(next to `FINNHUB_API_KEY`), then restart `streamlit run app.py`."
        )
        st.caption(f"Default model: `{ai.model()}` (override via OPENROUTER_MODEL in .env)")
    elif asset_class != "Stock":
        st.info("AI analysis is wired for stocks only.")
    else:
        st.caption(
            f"Model: `{ai.model()}` · button-triggered, cached 6h · "
            "interprets the free-tier data already pulled by the other tabs. "
            "Generates nothing until you click Analyze."
        )

        # drop cached AI results when the symbol changes
        if st.session_state.get("ai_symbol") != symbol:
            for _k in [k for k in st.session_state if k.startswith("ai_")]:
                del st.session_state[_k]
            st.session_state["ai_symbol"] = symbol

        def _ai_card(key: str, title: str, fn, hint: str):
            st.subheader(title)
            bc, cc = st.columns([1, 3])
            with bc:
                clicked = st.button(":brain: Analyze", key=f"ai_btn_{key}",
                                    type="primary")
            with cc:
                ts = ai.fetched_at(key, symbol)
                if ts:
                    st.caption(_fmt_ts(ts) + " · cached" + (f" · {hint}" if hint else ""))
                elif hint:
                    st.caption(hint)
            if clicked:
                with st.spinner("Analyzing…"):
                    res = fn(symbol)
                st.session_state[f"ai_{key}"] = res
            val = st.session_state.get(f"ai_{key}")
            if val:
                st.markdown(val)
            elif not clicked:
                st.caption("_Click Analyze to generate._")
            else:
                st.error(f"Analysis failed: {ai.last_error() or 'unknown error'}")

        _ai_card("overall", ":memo: Overall research brief", ai.analyze_overall,
                 "quote, profile, metrics, earnings, analyst recs, insider, news")
        st.divider()
        _ai_card("financials", ":abacus: Financials & earnings", ai.analyze_financials,
                 "basic metrics, earnings surprises, latest 10-K")
        st.divider()
        _ai_card("gov", ":classical_building: Government / alt-data", ai.analyze_gov,
                 "lobbying, patents, H-1B, federal contracts")
        st.divider()
        _ai_card("insider", ":male_detective: Insider sentiment", ai.analyze_insider,
                 "derived from open-market Form 4 (P/S) transactions")