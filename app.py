"""SME signal portal dashboard.  Run:  streamlit run app.py"""
import copy
import json

import pandas as pd
import requests
import streamlit as st
import yaml

from portal.config import (SCORECARD, SOURCES, dump, load_scorecard, load_sources,
                           save_yaml_text, validate_scorecard, validate_sources)
from portal import insights as ins
from portal.db import connect, watchlist_rows
from portal import investors as invs
from portal.insights import final_scorecard
from portal.ingest import run as run_ingest, run_source, settings_with_watchlist
from portal.extract import extract_pending, reextract_all
from portal.scoring import load_facts, score_facts, signals
from portal.sources import REGISTRY
from portal.sources.base import sha256_bytes

st.set_page_config(page_title="SME Signal Portal", page_icon="📈", layout="wide")

SIGNAL_ICON = {"Bullish": "🟢 Bullish", "Neutral": "⚪ Neutral", "Bearish": "🔴 Bearish", "Avoid": "⛔ Avoid"}


def get_scored(sc):
    with connect() as conn:
        return score_facts(load_facts(conn), sc)


# ------------------------------------------------------------------ Signals page
def page_signals():
    sc = load_scorecard()
    scored = get_scored(sc)
    sig = signals(scored, sc)
    st.title("All SME signals")
    health_banner()
    st.caption("Personal research tool. Not investment advice. Click a row to see what drives the signal.")
    if sig.empty:
        st.info("No signals yet.")
        return
    c = st.columns(4)
    for col, name in zip(c, ["Bullish", "Neutral", "Bearish", "Avoid"]):
        col.metric(SIGNAL_ICON[name], int((sig["signal"] == name).sum()))
    pick = st.multiselect("Show", list(SIGNAL_ICON), default=list(SIGNAL_ICON))
    q = st.text_input("Search symbol or company")
    view = sig[sig["signal"].isin(pick)]
    if q:
        view = view[view["symbol"].str.contains(q, case=False) | view["company"].str.contains(q, case=False)]
    view = view.assign(signal=view["signal"].map(SIGNAL_ICON)).reset_index(drop=True)
    ev = st.dataframe(view, hide_index=True, width="stretch",
                      on_select="rerun", selection_mode="single-row")
    rows = ev.selection.rows if ev and ev.selection else []
    if rows:
        stock_detail(view.iloc[rows[0]]["symbol"], scored, sig)


def stock_detail(symbol, scored, sig, header=True):
    if symbol not in set(sig["symbol"]):
        st.info("No filings, deals or news facts for this stock yet.")
        return
    s = sig[sig["symbol"] == symbol].iloc[0]
    f = scored[(scored["symbol"] == symbol)].sort_values("when", ascending=False)
    if header:
        st.divider()
        st.header(f"{symbol} · {s['company']}")
        if st.button(f"Open full page for {symbol}"):
            st.session_state["stock"] = symbol
            st.session_state["page"] = "Stock"
            st.rerun()
    a, b, c = st.columns(3)
    a.metric("Signal", SIGNAL_ICON[s["signal"]])
    b.metric("Score", s["score"])
    c.metric("Facts in window", s["facts"])
    if s["hard_stops"]:
        st.error(f"Hard stop: {s['hard_stops']}. This forces Avoid whatever the score.")
    st.subheader("What builds this signal")
    st.caption("contribution = rule score × source reliability × confidence × time decay")
    chart = f[f["decay"] > 0][["label", "contribution"]].groupby("label").sum().sort_values("contribution")
    st.bar_chart(chart, horizontal=True)
    table = f[["when", "label", "summary", "base_score", "weight", "decay", "contribution",
               "hard_stop", "source", "extractor", "source_url"]].copy()
    table["when"] = table["when"].astype(str).str[:10]
    table["decay"] = table["decay"].round(2)
    st.dataframe(table, hide_index=True, width="stretch",
                 column_config={"source_url": st.column_config.LinkColumn("source"),
                                "weight": st.column_config.NumberColumn(format="%.2f")})
    st.subheader("Check the sources")
    for _, r in f.iterrows():
        with st.expander(f"{str(r['when'])[:10]} · {r['label']} · {r['contribution']:+.2f} · {r['source']}"):
            st.markdown(f"**Fact:** {r['summary']}")
            if r["evidence"]:
                st.markdown(f"**Evidence quote:** _\"{r['evidence']}\"_")
            st.markdown(f"**Original:** [{r['source_url']}]({r['source_url']})")
            st.markdown(f"**Item title:** {r['title']}")
            st.code(f"source       : {r['source']} ({r['source_type']})\n"
                    f"published    : {r['published_at']}\n"
                    f"fetched at   : {r['fetched_at']}\n"
                    f"content hash : {r['content_hash']}\n"
                    f"document hash: {r['doc_hash'] or '— (not downloaded)'}\n"
                    f"extracted by : {r['extractor']}  (confidence {r['confidence']:.2f})", language="text")
            if st.button("Re-fetch and verify", key=f"v{r['fact_id']}"):
                verify(r)
            with connect() as conn:
                raw = conn.execute("SELECT raw_json, body FROM raw_items WHERE id=?",
                                   (int(r["item_id"]),)).fetchone()
            st.caption("Raw record as fetched")
            st.json(json.loads(raw["raw_json"] or "{}"), expanded=False)


def verify(r):
    url = r["source_url"] or ""
    if not url.startswith("http") or "example.com" in url:
        st.warning("This is demo data; there is no real source to fetch.")
        return
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0",
                                                     "Referer": url.split("/", 3)[0] + "//" + url.split("/")[2]})
        h = sha256_bytes(resp.content)
    except Exception as e:
        st.error(f"Could not fetch: {e}")
        return
    if r["doc_hash"]:
        if h == r["doc_hash"]:
            st.success("Document matches the stored hash: it has not changed since it was fetched.")
        else:
            st.error(f"Document differs from the stored copy.\nnow: {h}\nthen: {r['doc_hash']}")
    else:
        st.info(f"Fetched OK (HTTP {resp.status_code}). No document hash was stored for this item, "
                f"so compare by reading the original. Current hash: {h}. "
                "Turn on settings.fetch_documents in sources.yaml to store document hashes.")


# ------------------------------------------------------------------ Rules page
def page_rules():
    st.title("Scorecard rules")
    st.caption("Changes are previewed live below and apply to all stored facts when you save. "
               "A backup of the old file goes to config/backups/.")
    sc = load_scorecard()
    new = copy.deepcopy(sc)
    tab1, tab2, tab3, tab5, tab4 = st.tabs(["Fact scores", "Weights & thresholds", "Investor lists",
                                            "Final call & macro", "Raw YAML"])

    with tab1:
        rows = [{"key": k, "label": v.get("label", k), "score": float(v.get("score", 0)),
                 "hard_stop": bool(v.get("hard_stop", False)),
                 "keywords": ", ".join(v.get("keywords") or []), "description": v.get("description", "")}
                for k, v in sc.get("fact_types", {}).items()]
        ed = st.data_editor(pd.DataFrame(rows), num_rows="dynamic", hide_index=True,
                            width="stretch", key="ft",
                            column_config={"score": st.column_config.NumberColumn(min_value=-5, max_value=5, step=0.5),
                                           "keywords": st.column_config.TextColumn(width="large"),
                                           "description": st.column_config.TextColumn(width="large")})
        st.caption("Add a row to create a new fact type. Keywords drive the offline extractor; the "
                   "description tells Claude when to use the type. After adding types or keywords, "
                   "use **Re-extract all** on the Sources page.")
        ft = {}
        for _, r in ed.iterrows():
            if not r["key"] or pd.isna(r["key"]):
                continue
            d = {"label": r["label"] or r["key"], "score": float(r["score"] or 0)}
            if r["hard_stop"]:
                d["hard_stop"] = True
            d["description"] = r["description"] or ""
            d["keywords"] = [k.strip() for k in str(r["keywords"] or "").split(",") if k.strip()]
            ft[str(r["key"]).strip()] = d
        new["fact_types"] = ft

    with tab2:
        st.subheader("Source reliability")
        rel = {}
        cols = st.columns(3)
        for i, (k, v) in enumerate(sc.get("source_reliability", {}).items()):
            rel[k] = cols[i % 3].slider(k, 0.0, 1.0, float(v), 0.05)
        new["source_reliability"] = rel
        st.subheader("Time decay")
        c1, c2 = st.columns(2)
        new["decay"] = {"half_life_days": c1.number_input("Half-life (days)", 1, 3650, int(sc["decay"]["half_life_days"])),
                        "lookback_days": c2.number_input("Ignore facts older than (days)", 1, 3650, int(sc["decay"]["lookback_days"]))}
        st.subheader("Signal thresholds")
        c1, c2, c3 = st.columns(3)
        new["signals"] = {"bullish_at": c1.number_input("Bullish at score ≥", value=float(sc["signals"]["bullish_at"]), step=0.5),
                          "bearish_at": c2.number_input("Bearish at score ≤", value=float(sc["signals"]["bearish_at"]), step=0.5),
                          "hard_stop_window_days": c3.number_input("Hard stops count for (days)", 1, 3650,
                                                                   int(sc["signals"].get("hard_stop_window_days", 365)))}
        new["shareholding_change_threshold_pct"] = st.number_input(
            "Promoter holding change that counts (percentage points)", 0.0, 50.0,
            float(sc.get("shareholding_change_threshold_pct", 1.0)), 0.25)

    with tab3:
        inv = sc.get("investor_lists", {})
        c1, c2 = st.columns(2)
        cred = c1.text_area("Credible funds / investors (one per line)", "\n".join(inv.get("credible") or []), height=250)
        avoid = c2.text_area("Avoid / operator entities (one per line)", "\n".join(inv.get("avoid") or []), height=250)
        new["investor_lists"] = {"credible": [x.strip() for x in cred.splitlines() if x.strip()],
                                 "avoid": [x.strip() for x in avoid.splitlines() if x.strip()]}
        st.caption("Matched case-insensitively as part of the buyer/seller name in deals. "
                   "Edits re-classify past deals immediately.")

    with tab5:
        fc = copy.deepcopy(sc.get("final_scorecard") or {})
        st.subheader("Buy / Hold / Sell")
        c1, c2 = st.columns(2)
        th = fc.setdefault("thresholds", {"buy_at": 6, "sell_at": -3})
        th["buy_at"] = c1.number_input("Buy when total ≥", value=float(th.get("buy_at", 6)), step=0.5)
        th["sell_at"] = c2.number_input("Sell when total ≤", value=float(th.get("sell_at", -3)), step=0.5)
        st.subheader("Weight of each part")
        w = fc.setdefault("weights", {})
        cols = st.columns(5)
        for i, (k, lbl) in enumerate([("facts", "Filings, deals & news"), ("momentum", "Price momentum"),
                                      ("valuation", "Valuation vs peers"), ("tracked_investors", "Tracked investors"),
                                      ("macro", "Macro")]):
            w[k] = cols[i].number_input(lbl, 0.0, 5.0, float(w.get(k, 1.0)), 0.25)
        st.subheader("Momentum and valuation points")
        mo, va = fc.setdefault("momentum", {}), fc.setdefault("valuation", {})
        c = st.columns(4)
        mo["above_200dma"] = c[0].number_input("Above 200-DMA", -5.0, 5.0, float(mo.get("above_200dma", 1)), 0.5)
        mo["below_200dma"] = c[1].number_input("Below 200-DMA", -5.0, 5.0, float(mo.get("below_200dma", -1)), 0.5)
        va["cheap_score"] = c[2].number_input("P/E cheap vs peers", -5.0, 5.0, float(va.get("cheap_score", 1)), 0.5)
        va["expensive_score"] = c[3].number_input("P/E expensive vs peers", -5.0, 5.0, float(va.get("expensive_score", -1)), 0.5)
        new["final_scorecard"] = fc
        st.subheader("Macro rules")
        mr = pd.DataFrame(sc.get("macro_rules") or [])
        mr_ed = st.data_editor(mr, num_rows="dynamic", hide_index=True, width="stretch", key="mr")
        st.caption("series: US10Y, FED_FUNDS_UPPER, USDINR, BRENT, INDIA10Y, RBI_REPO. Fill one condition column "
                   "per row (change_above / change_below in points, pct_change_above / pct_change_below in %).")
        new["macro_rules"] = [{k: (v.item() if hasattr(v, "item") else v) for k, v in r.items() if pd.notna(v) and v != ""}
                              for r in mr_ed.to_dict("records") if r.get("series")]

    with tab4:
        raw = st.text_area("scorecard.yaml", SCORECARD.read_text(encoding="utf-8"), height=500)
        if st.button("Save raw YAML"):
            try:
                parsed = yaml.safe_load(raw)
                errs = validate_scorecard(parsed)
            except yaml.YAMLError as e:
                errs = [str(e)]
            if errs:
                st.error("\n".join(errs))
            else:
                save_yaml_text(SCORECARD, raw)
                st.success("Saved.")
                st.rerun()

    st.divider()
    st.subheader("Preview with these rules")
    errs = validate_scorecard(new)
    if errs:
        st.error("\n".join(errs))
    else:
        before = signals(get_scored(sc), sc)[["symbol", "signal", "score"]]
        after = signals(get_scored(new), new)[["symbol", "signal", "score"]]
        cmp = before.merge(after, on="symbol", suffixes=(" now", " new"), how="outer")
        cmp["changed"] = cmp["signal now"] != cmp["signal new"]
        st.dataframe(cmp, hide_index=True, width="stretch")
        if st.button("Save rules", type="primary"):
            save_yaml_text(SCORECARD, dump(new))
            st.success("Saved. Signals use the new rules now.")
            st.rerun()


# ------------------------------------------------------------------ Sources page
def set_enabled(text, name, value):
    import re
    pat = re.compile(rf"(^  {re.escape(name)}:\s*\n(?:(?:    .*|\s*)\n)*?    enabled:\s*)(true|false)",
                     re.MULTILINE)
    return pat.sub(lambda m: m.group(1) + ("true" if value else "false"), text, count=1)


def page_sources():
    st.title("Sources")
    cfg = load_sources()
    with connect() as conn:
        runs = pd.read_sql_query("""SELECT source, MAX(finished_at) AS last_run, status, message, new_items
                                    FROM source_runs GROUP BY source""", conn)
        counts = pd.read_sql_query("SELECT source, COUNT(*) AS items FROM raw_items GROUP BY source", conn)
    rows = []
    for name, s in (cfg.get("sources") or {}).items():
        rows.append({"run": False, "name": name, "adapter": s.get("adapter"), "enabled": bool(s.get("enabled")),
                     "notes": s.get("notes", "")})
    df = pd.DataFrame(rows).merge(runs, left_on="name", right_on="source", how="left") \
                           .merge(counts, left_on="name", right_on="source", how="left")
    df = df[["run", "name", "enabled", "items", "last_run", "status", "message", "adapter", "notes"]]
    ed = st.data_editor(df, hide_index=True, width="stretch",
                        disabled=["name", "adapter", "items", "last_run", "status", "message", "notes"])
    c1, c2, c3, c4 = st.columns(4)
    if c1.button("Save enabled flags"):
        # Edit only the `enabled:` lines so the comments and TODO(YOU) notes in the file stay.
        text = SOURCES.read_text(encoding="utf-8")
        for _, r in ed.iterrows():
            text = set_enabled(text, r["name"], bool(r["enabled"]))
        save_yaml_text(SOURCES, text)
        st.success("Saved.")
    targets = []
    if c2.button("Run ticked sources", type="primary"):
        targets = list(ed[ed["run"]]["name"])
    if c3.button("Run all enabled"):
        targets = list(ed[ed["enabled"]]["name"])
    if targets:
        log = st.empty()
        lines = []
        def say(m):
            lines.append(m)
            log.code("\n".join(lines))
        with connect() as conn:
            settings = settings_with_watchlist(conn, cfg)
            for n in targets:
                say(f"Running {n} ...")
                run_source(conn, n, cfg["sources"][n], settings, say)
            extract_pending(conn, say)
    if c4.button("Re-extract all"):
        with connect() as conn:
            n = reextract_all(conn, st.write)
        st.success(f"Re-extracted {n} items.")

    with st.expander("Edit sources.yaml"):
        raw = st.text_area("sources.yaml", SOURCES.read_text(encoding="utf-8"), height=450)
        if st.button("Save sources.yaml"):
            try:
                errs = validate_sources(yaml.safe_load(raw))
            except yaml.YAMLError as e:
                errs = [str(e)]
            if errs:
                st.error("\n".join(errs))
            else:
                save_yaml_text(SOURCES, raw)
                st.success("Saved.")
                st.rerun()

    with st.expander("Available adapters and how to add a new resource"):
        for name, cls in sorted(REGISTRY.items()):
            st.markdown(f"**{name}** ({cls.source_type}) — {(cls.__doc__ or '').strip().splitlines()[0] if cls.__doc__ else ''}")
        st.markdown("""
**Paid or login-only source:** fill in its placeholder at the bottom of sources.yaml
(search for `TODO(YOU)`) and, for an API, copy `portal/sources/paid_template.py`.

**No code:** add an entry using `rss` (any news/blog/YouTube feed) or `csv_import`
(any CSV file or URL, with a column mapping) in sources.yaml.

**New site:** add a Python file in `portal/sources/` with a class that subclasses
`SourceAdapter`, decorate it with `@register("my_adapter")`, and yield `RawItem`s from
`fetch()`. It is picked up automatically. Then add `adapter: my_adapter` in sources.yaml.
""")


# ------------------------------------------------------------------ Items page
def page_items():
    st.title("All stored items")
    with connect() as conn:
        df = pd.read_sql_query("""SELECT i.id, i.published_at, i.symbol, i.source, i.title, i.source_url,
                                         i.fetched_at, i.content_hash,
                                         (SELECT GROUP_CONCAT(fact_type) FROM facts WHERE item_id=i.id) AS facts
                                  FROM raw_items i ORDER BY i.published_at DESC""", conn)
    q = st.text_input("Filter")
    if q:
        df = df[df.apply(lambda r: q.lower() in " ".join(map(str, r.values)).lower(), axis=1)]
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={"source_url": st.column_config.LinkColumn()})



# ------------------------------------------------------------------ first-run / health banner
def health_banner():
    with connect() as conn:
        n_real = conn.execute("SELECT COUNT(*) FROM raw_items WHERE source != 'demo'").fetchone()[0]
        n_wl = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
        errs = conn.execute("""SELECT source, message FROM source_runs r WHERE status='error' AND id =
                               (SELECT MAX(id) FROM source_runs WHERE source=r.source)""").fetchall()
        ran = conn.execute("SELECT COUNT(*) FROM source_runs").fetchone()[0]
    if not ran:
        st.warning("No data fetched yet. Press **Fetch latest data** in the sidebar (takes a few minutes the first time).")
    elif n_real == 0:
        st.error("Sources ran but returned no real data. See the errors below and on the **Sources** page.")
    if n_wl == 0:
        st.info("Your watchlist is empty. Browse every SME stock on the **SME universe** page, or add stocks here, "
                "to follow them with alerts, peers and a Buy/Hold/Sell call.")
    if errs:
        with st.expander(f"⚠️ {len(errs)} source(s) failed on the last run"):
            for e in errs:
                st.markdown(f"- **{e['source']}**: `{(e['message'] or '')[:300]}`")


# ------------------------------------------------------------------ Watchlist page
CALL_ICON = {"Buy": "🟢 Buy", "Hold": "⚪ Hold", "Sell": "🔴 Sell", "Avoid": "⛔ Avoid"}


def page_watchlist():
    st.title("Watchlist")
    health_banner()
    with st.form("add", clear_on_submit=True):
        c1, c2, c3, c4, c5 = st.columns([1.2, 0.8, 1.5, 1.5, 2])
        sym = c1.text_input("Symbol (NSE) or BSE code")
        exch = c2.selectbox("Exchange", ["NSE", "BSE"])
        name = c3.text_input("Company name (for news)")
        peers = c4.text_input("Peers (comma separated)")
        aliases = c5.text_input("Other names in news (optional)")
        if st.form_submit_button("Add to watchlist", type="primary") and sym.strip():
            with connect() as conn:
                conn.execute("INSERT OR REPLACE INTO watchlist (symbol, exchange, name, peers, aliases) VALUES (?,?,?,?,?)",
                             (sym.strip().upper(), exch, name.strip(), peers.upper().replace(" ", ""), aliases))
            st.success(f"Added {sym.upper()}. Press **Fetch latest data** in the sidebar to load its data.")
    sc = load_scorecard()
    with connect() as conn:
        wl = pd.DataFrame(watchlist_rows(conn))
        if wl.empty:
            return
        scored = score_facts(load_facts(conn), sc)
        fin = final_scorecard(conn, scored, sc, list(wl["symbol"])).set_index("symbol")
        rows, alerts = [], []
        for _, w in wl.iterrows():
            stt = ins.price_stats(ins.prices(conn, w["symbol"]))
            f = fin.loc[w["symbol"]]
            rows.append({"symbol": w["symbol"], "name": w["name"], "call": CALL_ICON[f["call"]], "total": f["total"],
                         "price": stt.get("last"), "1D %": stt.get("chg_1d"), "1M %": stt.get("ret_1m"),
                         "1Y %": stt.get("ret_1y"), "price date": stt.get("date"), "why": f["why"]})
            ne = ins.new_entrants(conn, scored, w["symbol"])
            for _, a in ne.iterrows():
                alerts.append(f"**{w['symbol']}**: {a['investor']} — {a['how']} ({a['date']})")
        for _, e in invs.recent_events(conn, scored, days=120, symbols=list(wl["symbol"])).iterrows():
            alerts.append(f"**{e['symbol']}**: {e['investor']} — {e['event']} ({e['date']})")
        alerts = list(dict.fromkeys(alerts))
    if alerts:
        st.subheader("🔔 New investors")
        for a in alerts:
            st.markdown("- " + a)
    st.subheader("Your stocks")
    view = pd.DataFrame(rows)
    ev = st.dataframe(view, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row")
    if ev and ev.selection and ev.selection.rows:
        st.session_state["stock"] = view.iloc[ev.selection.rows[0]]["symbol"]
        st.session_state["page"] = "Stock"
        st.rerun()
    st.caption("Click a row to open the stock page.")
    with st.expander("Edit or remove watchlist entries"):
        ed = st.data_editor(wl[["symbol", "exchange", "name", "ticker", "peers", "aliases"]], hide_index=True,
                            width="stretch", num_rows="dynamic", key="wl_ed")
        st.caption("ticker: Yahoo Finance ticker if the default (SYMBOL.NS / CODE.BO) is wrong.")
        if st.button("Save watchlist"):
            with connect() as conn:
                conn.execute("DELETE FROM watchlist")
                for _, r in ed.iterrows():
                    if r["symbol"]:
                        conn.execute("INSERT INTO watchlist (symbol, exchange, name, ticker, peers, aliases) VALUES (?,?,?,?,?,?)",
                                     (str(r["symbol"]).upper(), r["exchange"] or "NSE", r["name"], r["ticker"] or None,
                                      r["peers"], r["aliases"]))
            st.success("Saved.")
            st.rerun()


# ------------------------------------------------------------------ SME universe page
def page_universe():
    st.title("SME universe")
    health_banner()
    st.caption("Every NSE Emerge (SME) stock with real price moves from Yahoo Finance and fundamentals when available. "
               "Click a column header to sort. Tick rows to add them to your watchlist or open one.")
    sc = load_scorecard()
    sig = signals(get_scored(sc), sc)
    with connect() as conn:
        df = ins.universe_table(conn, sig)
    c = st.columns(4)
    c[0].metric("SME stocks listed", len(df))
    c[1].metric("With price data", int(df["price"].notna().sum()))
    c[2].metric("With P/E", int(df["P/E"].notna().sum()))
    c[3].metric("With a filings signal", int(df["signal"].notna().sum()))
    f1, f2, f3, f4 = st.columns([2, 2, 1.5, 1.5])
    q = f1.text_input("Search symbol or company")
    sectors = sorted(df["sector"].dropna().unique())
    pick = f2.multiselect("Sector", sectors)
    min_cap = f3.number_input("Min market cap (Cr)", min_value=0, value=0, step=50)
    only = f4.selectbox("Show", ["All", "With price data", "With fundamentals", "Gainers 1M", "Losers 1M"])
    v = df
    if q:
        v = v[v["symbol"].str.contains(q, case=False) | v["company"].str.contains(q, case=False)]
    if pick:
        v = v[v["sector"].isin(pick)]
    if min_cap:
        v = v[v["mcap (Cr)"].fillna(0) >= min_cap]
    if only == "With price data":
        v = v[v["price"].notna()]
    elif only == "With fundamentals":
        v = v[v["P/E"].notna() | v["mcap (Cr)"].notna()]
    elif only == "Gainers 1M":
        v = v[v["1M %"] > 0].sort_values("1M %", ascending=False)
    elif only == "Losers 1M":
        v = v[v["1M %"] < 0].sort_values("1M %")
    v = v.assign(signal=v["signal"].map(SIGNAL_ICON)).reset_index(drop=True)
    ev = st.dataframe(v, hide_index=True, width="stretch", height=600, on_select="rerun", selection_mode="multi-row")
    st.caption(f"{len(v)} stocks shown. Fundamentals fill in over the first few runs "
               "(Yahoo is queried one stock at a time); price date shows how fresh each price is.")
    rows = ev.selection.rows if ev and ev.selection else []
    if rows:
        picked = list(v.iloc[rows]["symbol"])
        b1, b2, _ = st.columns([1.5, 1.5, 5])
        if b1.button(f"Add {len(picked)} to watchlist", type="primary"):
            with connect() as conn:
                for sym_, nm in zip(picked, v.iloc[rows]["company"]):
                    conn.execute("INSERT OR IGNORE INTO watchlist (symbol, name) VALUES (?,?)", (sym_, nm))
            st.success("Added.")
        if b2.button("Open first selected"):
            st.session_state["stock"] = picked[0]
            st.session_state["page"] = "Stock"
            st.rerun()


# ------------------------------------------------------------------ Stock page
def page_stock():
    from portal.universe import sme_companies
    with connect() as conn:
        wl = [r["symbol"] for r in watchlist_rows(conn)]
        all_syms = sorted(set(wl) | set(sme_companies())
                          | {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM raw_items WHERE symbol IS NOT NULL")})
    if not all_syms:
        st.title("Stock")
        health_banner()
        return
    cur = st.session_state.get("stock")
    sym = st.selectbox("Stock", all_syms, index=all_syms.index(cur) if cur in all_syms else 0)
    st.session_state["stock"] = sym
    sc = load_scorecard()
    with connect() as conn:
        row = conn.execute("SELECT * FROM watchlist WHERE symbol=?", (sym,)).fetchone()
        scored = score_facts(load_facts(conn), sc)
        sig = signals(scored, sc)
        fin = final_scorecard(conn, scored, sc, [sym]).iloc[0]
        px = ins.prices(conn, sym)
        stt = ins.price_stats(px)
        fu = ins.fundamentals(conn, sym)
        company = (row and row["name"]) or fu.get("longName") or (sig.set_index("symbol").loc[sym, "company"] if sym in set(sig["symbol"]) else "") or sme_companies().get(sym, "")
        st.title(f"{sym} · {company}")
        if not row:
            if st.button("Add to watchlist (for prices, holders, peers)"):
                conn.execute("INSERT OR IGNORE INTO watchlist (symbol, name) VALUES (?,?)", (sym, company))
                st.rerun()
        c = st.columns(5)
        c[0].metric("Final call", CALL_ICON[fin["call"]], f"{fin['total']:+.2f}")
        c[1].metric("Price", stt.get("last", "—"), f"{stt['chg_1d']}%" if stt.get("chg_1d") is not None else None)
        c[2].metric("1Y return", f"{stt['ret_1y']}%" if stt.get("ret_1y") is not None else "—")
        c[3].metric("Market cap", f"₹{fu['marketCap'] / 1e7:,.0f} Cr" if fu.get("marketCap") else "—")
        c[4].metric("P/E", f"{fu['trailingPE']:.1f}" if fu.get("trailingPE") else "—")
        if fin["hard_stops"]:
            st.error(f"Hard stop: {fin['hard_stops']}")

        tabs = st.tabs(["Scorecard", "Price", "Holders & investors", "Concalls", "News", "Peers", "Signal evidence"])
        with tabs[0]:
            parts = pd.DataFrame([{"component": k, "score": fin[k]} for k in
                                  ["Filings, deals & news", "Price momentum", "Valuation vs peers", "Tracked investors", "Macro"]])
            st.dataframe(parts, hide_index=True, width="stretch")
            for part in fin["why"].split(" | "):
                st.markdown("- " + part)
            th = (sc.get("final_scorecard") or {}).get("thresholds", {})
            st.caption(f"Buy at total ≥ {th.get('buy_at')}, Sell at ≤ {th.get('sell_at')}, Hold in between; "
                       "any hard stop = Avoid. Change these on the Scorecard rules page.")
        with tabs[1]:
            if px.empty:
                st.info("No prices yet. Add the stock to the watchlist and press Fetch latest data. "
                        "If it still has none, set its Yahoo ticker on the Watchlist page.")
            else:
                rng = st.radio("Range", ["3M", "1Y", "3Y", "All"], index=1, horizontal=True)
                days = {"3M": 91, "1Y": 365, "3Y": 1095, "All": 100000}[rng]
                v = px[px.index >= px.index[-1] - pd.Timedelta(days=days)]
                st.line_chart(v[["close", "dma50", "dma200"]])
                st.bar_chart(v["volume"], height=150)
                st.caption(f"52-week range ₹{stt['low_52w']} – ₹{stt['high_52w']} · last close {stt['date']}")
        with tabs[2]:
            tracked, major, ev = invs.stock_view(conn, sym, scored)
            ne = ins.new_entrants(conn, scored, sym)
            if not ne.empty:
                st.success("🔔 New investors: " + "; ".join(f"{r['investor']} ({r['how']}, {r['date']})" for _, r in ne.iterrows()))
            st.subheader("Top investors and funds in this stock")
            if not tracked.empty:
                st.dataframe(tracked.drop(columns=["symbol"]), hide_index=True, width="stretch",
                             column_config={"source_url": st.column_config.LinkColumn("filing")})
            else:
                st.caption("None of the investors on your Top investors list hold more than 1% in the latest "
                           "filing. Major shareholders are shown below instead.")
            st.subheader("Major shareholders (latest filing)")
            if major.empty:
                st.caption("No shareholding filing parsed yet for this stock. Holders are read from NSE "
                           "shareholding XBRL files; the first backfill is spread over a few runs.")
            else:
                st.dataframe(major, hide_index=True, width="stretch",
                             column_config={"source_url": st.column_config.LinkColumn("filing"),
                                            "investor": st.column_config.TextColumn("tracked as")})
                _, gone = ins.holders(conn, sym)
                if not gone.empty:
                    st.caption("Exited since the previous filing")
                    st.dataframe(gone, hide_index=True, width="stretch")
            if not ev.empty:
                st.subheader("Moves by tracked investors")
                st.dataframe(ev, hide_index=True, width="stretch",
                             column_config={"source_url": st.column_config.LinkColumn("source")})
            ph = ins.promoter_history(scored, sym)
            st.subheader("Promoter holding over time")
            if ph.empty:
                st.caption("No shareholding filings stored yet.")
            else:
                st.line_chart(ph)
            st.subheader("Deals, insider trades, SAST and fund portfolio activity")
            act = ins.investor_activity(scored, sym)
            if act.empty:
                st.caption("Nothing yet.")
            else:
                st.dataframe(act, hide_index=True, width="stretch",
                             column_config={"source_url": st.column_config.LinkColumn("source")})
        with tabs[3]:
            cc = ins.concalls(conn, sym)
            if cc.empty:
                st.caption("No concall, transcript or presentation filings found yet. Add the company's "
                           "investor-relations page under company_ir_pages in sources.yaml.")
            for _, r in cc.iterrows():
                with st.expander(f"{str(r['published_at'])[:10]} · {r['title']}"):
                    st.markdown(f"[Open original]({r['source_url']}) · source: {r['source']}")
                    if r["summary"]:
                        st.markdown(r["summary"])
                    elif st.button("Summarise with Claude", key=f"sum{r['id']}"):
                        try:
                            with st.spinner("Reading the document..."):
                                st.markdown(ins.summarize_item(conn, int(r["id"])))
                        except Exception as e:
                            st.error(f"Could not summarise: {e}. Is ANTHROPIC_API_KEY set?")
        with tabs[4]:
            nw = ins.news(conn, sym)
            if nw.empty:
                st.caption("No news yet. Fill the company name and other names on the Watchlist page so "
                           "news and ValuePickr posts can be matched to this stock.")
            else:
                st.dataframe(nw, hide_index=True, width="stretch",
                             column_config={"source_url": st.column_config.LinkColumn("link")})
        with tabs[5]:
            pt, peers = ins.peers_table(conn, sym, scored, sc)
            if not peers:
                st.caption("No peers set. Add them on the Watchlist page (Edit entries > peers).")
            st.dataframe(pt, hide_index=True, width="stretch")
            if fu:
                st.caption(f"Sector: {fu.get('sector', '—')} · Industry: {fu.get('industry', '—')}")
            pxs = {p: ins.prices(conn, p)["close"] for p in [sym] + peers}
            pxs = {k: v[v.index >= v.index[-1] - pd.Timedelta(days=365)] for k, v in pxs.items() if len(v)}
            if len(pxs) > 1:
                rel = pd.DataFrame({k: v / v.iloc[0] * 100 for k, v in pxs.items()})
                st.caption("1-year price, rebased to 100")
                st.line_chart(rel)
    with tabs[6]:
        stock_detail(sym, scored, sig, header=False)


# ------------------------------------------------------------------ Top investors page
def page_investors():
    st.title("Top investors")
    st.caption("Investors, fund managers, PMS/AIFs and fund houses from config/investors.yaml. Holdings are "
               "stakes above 1% in SME shareholding filings (half-yearly), plus bulk/block deals, SAST "
               "disclosures and fund portfolio files.")
    sc = load_scorecard()
    with connect() as conn:
        scored = score_facts(load_facts(conn), sc)
        ov = invs.overview(conn)
        feed = invs.recent_events(conn, scored, days=180)
        tab1, tab2, tab3 = st.tabs(["Recent changes", "Investor portfolios", "Edit list"])
        with tab1:
            if feed.empty:
                st.info("No moves by tracked investors yet. They appear as shareholding filings, deals and SAST "
                        "disclosures are fetched.")
            else:
                types = st.multiselect("Show", sorted(feed["event"].unique()), default=sorted(feed["event"].unique()))
                st.dataframe(feed[feed["event"].isin(types)], hide_index=True, width="stretch",
                             column_config={"source_url": st.column_config.LinkColumn("source")})
        with tab2:
            kinds = st.multiselect("Type", sorted(ov["type"].dropna().unique()), default=sorted(ov["type"].dropna().unique()))
            v = ov[ov["type"].isin(kinds)].reset_index(drop=True)
            ev = st.dataframe(v, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row")
            st.caption("Click an investor to see their portfolio.")
            if ev and ev.selection and ev.selection.rows:
                name = v.iloc[ev.selection.rows[0]]["investor"]
                info = invs.info(name)
                st.subheader(name)
                st.caption(f"{invs.TYPE_LABEL.get(info.get('type'), '')}"
                           + (f" · manager: {info['manager']}" if info.get("manager") else "")
                           + f" · matched as: {', '.join(info.get('aliases') or [])}")
                pf = invs.portfolio(conn, name)
                if pf.empty:
                    st.caption("No SME holdings above 1% found in the filings fetched so far.")
                else:
                    st.markdown("**Holdings (latest filing per stock)**")
                    st.dataframe(pf.drop(columns=["investor"]), hide_index=True, width="stretch",
                                 column_config={"source_url": st.column_config.LinkColumn("filing")})
                    if st.button("Add all to watchlist"):
                        for sym_ in pf[~pf["event"].str.startswith("EXITED")]["symbol"]:
                            conn.execute("INSERT OR IGNORE INTO watchlist (symbol) VALUES (?)", (sym_,))
                        st.success("Added. Press Fetch latest data for prices.")
                act = invs.activity(scored)
                act = act[act["investor"] == name]
                if not act.empty:
                    st.markdown("**Deals, SAST and fund activity**")
                    st.dataframe(act, hide_index=True, width="stretch",
                                 column_config={"source_url": st.column_config.LinkColumn("source")})
                nm = invs.news_mentions(conn, name)
                st.markdown("**News and forum mentions**")
                if nm.empty:
                    st.caption("None yet.")
                else:
                    st.dataframe(nm, hide_index=True, width="stretch",
                                 column_config={"source_url": st.column_config.LinkColumn("link")})
        with tab3:
            raw = st.text_area("investors.yaml", invs.INVESTORS_FILE.read_text(encoding="utf-8"), height=500)
            st.caption("Add a name with every spelling used in filings under aliases. credible: true makes their "
                       "buys count as positive in the scorecard.")
            if st.button("Save investor list"):
                try:
                    data = yaml.safe_load(raw)
                    assert isinstance(data.get("investors"), list), "needs an 'investors:' list"
                    for i in data["investors"]:
                        assert i.get("name"), f"entry without a name: {i}"
                    save_yaml_text(invs.INVESTORS_FILE, raw)
                    st.success("Saved.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Not saved: {e}")


# ------------------------------------------------------------------ Macro page
def page_macro():
    st.title("Macro")
    sc = load_scorecard()
    with connect() as conn:
        m = ins.macro_series(conn)
        score, hits = ins.macro_overlay(conn, sc)
        mnews = pd.read_sql_query("""SELECT published_at, title, source, source_url FROM raw_items
                                     WHERE source_type='macro' ORDER BY published_at DESC LIMIT 40""", conn)
    if m.empty:
        st.info("No macro data yet. Press **Fetch latest data** in the sidebar.")
    else:
        from portal.market import DEFAULT_MACRO
        labels = {k: v["label"] for k, v in DEFAULT_MACRO.items()} | {"RBI_REPO": "RBI policy repo rate (%)"}
        series = list(dict.fromkeys(m["series"]))
        cols = st.columns(min(len(series), 3))
        for i, s in enumerate(series):
            d = m[m["series"] == s]
            last, prev = d["value"].iloc[-1], d["value"].iloc[-min(len(d), 22)]
            with cols[i % len(cols)]:
                st.metric(labels.get(s, s), f"{last:.2f}", f"{last - prev:+.2f} vs ~1M ago")
                if len(d) > 2:
                    st.line_chart(d.assign(date=pd.to_datetime(d["date"])).set_index("date")["value"].tail(750), height=150)
                st.caption(f"as of {d['date'].iloc[-1]} · {d['source'].iloc[-1]}")
    st.subheader(f"Macro effect on every stock's score: {score:+.2f}")
    if hits:
        st.dataframe(pd.DataFrame(hits), hide_index=True, width="stretch")
    else:
        st.caption("No macro rule is triggered. Rules are under macro_rules in scorecard.yaml.")
    st.subheader("RBI and Fed announcements")
    if mnews.empty:
        st.caption("None fetched yet.")
    else:
        st.dataframe(mnews, hide_index=True, width="stretch",
                     column_config={"source_url": st.column_config.LinkColumn("link")})


def sidebar_fetch():
    if st.sidebar.button("Fetch latest data", type="primary"):
        box = st.sidebar.empty()
        lines = []
        def say(msg):
            lines.append(str(msg))
            box.code("\n".join(lines[-12:]))
        with st.spinner("Fetching (a few minutes; sites are queried slowly on purpose)..."):
            run_ingest(log=say)
        st.sidebar.success("Done.")
        st.rerun()


PAGES = {"Watchlist": page_watchlist, "SME universe": page_universe, "Stock": page_stock,
         "Top investors": page_investors, "All SME signals": page_signals,
         "Macro": page_macro, "Scorecard rules": page_rules, "Sources": page_sources, "All items": page_items}
if "page" in st.session_state and st.session_state["page"] in PAGES:
    st.session_state["nav"] = st.session_state.pop("page")
choice = st.sidebar.radio("Go to", list(PAGES), key="nav")
sidebar_fetch()
st.sidebar.caption("For personal research only. Not investment advice. Check every source yourself.")
PAGES[choice]()
