"""Score facts and combine them into a per-stock signal. Runs from stored facts every
time, so edits to scorecard.yaml take effect immediately."""
import json
import math
from datetime import datetime

import pandas as pd

from .config import load_scorecard


def _age_days(ts, now):
    if not ts:
        return 0.0
    try:
        d = datetime.fromisoformat(str(ts)[:19])
    except ValueError:
        return 0.0
    return max(0.0, (now - d).total_seconds() / 86400)


def _in_list(name, names):
    n = (name or "").lower()
    return any(x and x.lower() in n for x in names or [])


def resolve_deal(details, sc):
    inv = dict(sc.get("investor_lists") or {})
    if sc.get("use_tracked_investors_as_credible", True):
        from .investors import credible_names
        inv["credible"] = list(inv.get("credible") or []) + credible_names()
    buy = details.get("side", "").upper().startswith("B")
    client = details.get("client", "")
    if _in_list(client, inv.get("avoid")):
        return "flagged_entity_buy" if buy else "flagged_entity_sell"
    if _in_list(client, inv.get("credible")):
        return "credible_investor_buy" if buy else "credible_investor_sell"
    return "unknown_bulk_buy" if buy else "unknown_bulk_sell"


def load_facts(conn) -> pd.DataFrame:
    q = """SELECT f.id AS fact_id, f.symbol, f.fact_type AS raw_type, f.summary, f.evidence,
                  f.confidence, f.extractor, f.details_json,
                  i.id AS item_id, i.source, i.source_type, i.company, i.title, i.source_url,
                  i.published_at, i.fetched_at, i.content_hash, i.doc_hash
           FROM facts f JOIN raw_items i ON i.id = f.item_id"""
    return pd.read_sql_query(q, conn)


def score_facts(df: pd.DataFrame, sc: dict | None = None, now=None) -> pd.DataFrame:
    sc = sc or load_scorecard()
    now = now or datetime.now()
    if df.empty:
        return df.assign(fact_type=[], label=[], base_score=[], weight=[], decay=[],
                         contribution=[], hard_stop=[], age_days=[])
    fts = sc.get("fact_types") or {}
    rel = sc.get("source_reliability") or {}
    half = float((sc.get("decay") or {}).get("half_life_days", 90))
    lookback = float((sc.get("decay") or {}).get("lookback_days", 365))
    window = float((sc.get("signals") or {}).get("hard_stop_window_days", 365))
    sh_thr = float(sc.get("shareholding_change_threshold_pct", 1.0))

    df = df.copy()
    df["details"] = df["details_json"].apply(lambda s: json.loads(s) if s else {})
    df["when"] = df["published_at"].fillna(df["fetched_at"])
    df["age_days"] = df["when"].apply(lambda t: _age_days(t, now))

    # Resolve deferred types
    prev_pct = {}
    prev_qty = {}
    mf_thr = float(sc.get("fund_holding_change_threshold_pct", 10))
    order = df.sort_values("when").index
    resolved = {}
    for idx in order:
        r = df.loc[idx]
        t = r["raw_type"]
        if t == "_deal":
            t = resolve_deal(r["details"], sc)
        elif t == "_shareholding_snapshot":
            try:
                pct = float(r["details"].get("promoter_pct"))
            except (TypeError, ValueError):
                pct = None
            last = prev_pct.get(r["symbol"])
            if pct is not None:
                prev_pct[r["symbol"]] = pct
            if pct is None or last is None or abs(pct - last) < sh_thr:
                t = "routine"
            else:
                t = "promoter_stake_increase" if pct > last else "promoter_stake_decrease"
                df.at[idx, "summary"] = f"Promoter holding {last:.2f}% -> {pct:.2f}%"
        elif t == "_mf_holding":
            d = r["details"]
            key = (d.get("fund"), r["symbol"])
            try:
                q = float(str(d.get("qty")).replace(",", ""))
            except ValueError:
                q = None
            last = prev_qty.get(key)
            if q is not None:
                prev_qty[key] = q
            if last is None:
                t = "mf_new_entry" if any(k[0] == d.get("fund") for k in prev_qty if k != key) else "mf_holding"
            elif q is None or last == 0 or abs(q - last) / last * 100 < mf_thr:
                t = "mf_holding"
            else:
                t = "mf_added" if q > last else "mf_cut"
        resolved[idx] = t
    df["fact_type"] = pd.Series(resolved)

    def rule(t):
        return fts.get(t) or {}

    df["label"] = df["fact_type"].apply(lambda t: rule(t).get("label", t))
    df["base_score"] = df["fact_type"].apply(lambda t: float(rule(t).get("score", 0)))
    df["weight"] = df["source_type"].apply(lambda s: float(rel.get(s, 0.5))) * df["confidence"].fillna(1.0)
    df["decay"] = df["age_days"].apply(lambda a: math.pow(0.5, a / half) if a <= lookback else 0.0)
    df["contribution"] = (df["base_score"] * df["weight"] * df["decay"]).round(3)
    df["hard_stop"] = df.apply(lambda r: bool(rule(r["fact_type"]).get("hard_stop"))
                               and r["age_days"] <= window, axis=1)
    return df.drop(columns=["details_json"])


def signals(scored: pd.DataFrame, sc: dict | None = None) -> pd.DataFrame:
    sc = sc or load_scorecard()
    sig = sc.get("signals") or {}
    bull, bear = float(sig.get("bullish_at", 5)), float(sig.get("bearish_at", -3))
    cols = ["symbol", "company", "signal", "score", "facts", "positive", "negative",
            "hard_stops", "last_update", "top_driver"]
    if scored.empty:
        return pd.DataFrame(columns=cols)
    out = []
    for sym, g in scored.groupby("symbol"):
        live = g[g["decay"] > 0]
        total = round(live["contribution"].sum(), 2)
        hs = live[live["hard_stop"]]
        if not hs.empty:
            label = "Avoid"
        elif total >= bull:
            label = "Bullish"
        elif total <= bear:
            label = "Bearish"
        else:
            label = "Neutral"
        drivers = live.reindex(live["contribution"].abs().sort_values(ascending=False).index)
        top = drivers.iloc[0]["label"] if not drivers.empty else ""
        out.append({"symbol": sym, "company": g["company"].dropna().iloc[0] if g["company"].notna().any() else "",
                    "signal": label, "score": total, "facts": len(live),
                    "positive": int((live["contribution"] > 0).sum()),
                    "negative": int((live["contribution"] < 0).sum()),
                    "hard_stops": ", ".join(sorted(set(hs["label"]))),
                    "last_update": str(g["when"].max())[:10], "top_driver": top})
    order = {"Bullish": 0, "Neutral": 1, "Bearish": 2, "Avoid": 3}
    res = pd.DataFrame(out, columns=cols)
    return res.sort_values(by=["signal", "score"], key=lambda s: s.map(order) if s.name == "signal" else -s)
