"""Tracked investors (config/investors.yaml): who holds what, and what changed.

Data used (all free):
  * holders table   - named holders above 1% from shareholding XBRL filings (half-yearly for SME)
  * facts           - bulk/block deals, SAST, insider trades, monthly fund portfolios
  * raw_items       - news / forum posts that mention an investor by name
"""
import re
from datetime import datetime, timedelta
from functools import lru_cache

import pandas as pd
import yaml

from . import CONFIG_DIR

INVESTORS_FILE = CONFIG_DIR / "investors.yaml"
TYPE_LABEL = {"individual": "Individual investor", "pms_aif": "PMS / AIF", "mutual_fund": "Mutual fund",
              "fpi": "Foreign investor", "sme_anchor": "Frequent SME anchor"}


def to_dt(series):
    """Dates come as 2026-09-30 or 30-Sep-2026 / 30-09-2026 depending on the source."""
    iso = pd.to_datetime(series, errors="coerce", format="ISO8601")
    rest = pd.to_datetime(series[iso.isna()], errors="coerce", dayfirst=True, format="mixed")
    return iso.fillna(rest)


def load_investors() -> list[dict]:
    if not INVESTORS_FILE.exists():
        return []
    with open(INVESTORS_FILE, encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("investors") or []


@lru_cache(maxsize=4)
def _patterns(key):
    invs = load_investors()
    pats = []
    for inv in invs:
        for a in sorted(set(inv.get("aliases") or []) | {inv["name"]}, key=len, reverse=True):
            a = a.strip()
            if len(a) >= 4:
                pats.append((re.compile(r"(?<![A-Za-z])" + re.escape(a) + r"(?![A-Za-z])", re.I), inv["name"]))
    return pats


def _key():
    return INVESTORS_FILE.stat().st_mtime if INVESTORS_FILE.exists() else 0


def match(name: str | None) -> str | None:
    """Return the tracked investor a holder / client name belongs to, or None."""
    if not name:
        return None
    for pat, inv in _patterns(_key()):
        if pat.search(name):
            return inv
    return None


def credible_names() -> list[str]:
    out = []
    for inv in load_investors():
        if inv.get("credible"):
            out += [inv["name"]] + list(inv.get("aliases") or [])
    return out


def info(name) -> dict:
    return next((i for i in load_investors() if i["name"] == name), {})


# ------------------------------------------------------------------ holdings from filings
def holder_rows(conn) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT symbol, as_of, name AS holder, category, pct, shares, source_url FROM holders", conn)
    if df.empty:
        return df.assign(investor=[])
    df["investor"] = df["holder"].map(match)
    df["as_of_dt"] = to_dt(df["as_of"])
    return df


def _filing_dates(df):
    return {s: sorted(g["as_of_dt"].dropna().unique()) for s, g in df.groupby("symbol")}


def holding_changes(conn, df=None) -> pd.DataFrame:
    """For every stock, compare each tracked investor between the last two filings."""
    df = holder_rows(conn) if df is None else df
    cols = ["symbol", "investor", "event", "pct", "prev_pct", "as_of", "source_url"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    out = []
    t = df[df["investor"].notna()]
    for sym, dates in _filing_dates(df).items():
        if not dates:
            continue
        last = dates[-1]
        prev = dates[-2] if len(dates) > 1 else None
        cur = t[(t.symbol == sym) & (t.as_of_dt == last)].groupby("investor").agg(pct=("pct", "sum"), as_of=("as_of", "first"), source_url=("source_url", "first"))
        if prev is None:
            for inv, r in cur.iterrows():
                out.append([sym, inv, "HOLDS (first filing on record)", r.pct, None, r.as_of, r.source_url])
            continue
        old = t[(t.symbol == sym) & (t.as_of_dt == prev)].groupby("investor")["pct"].sum()
        for inv, r in cur.iterrows():
            if inv not in old.index:
                ev = "NEW ENTRY"
            elif r.pct - old[inv] >= 0.05:
                ev = "INCREASED"
            elif old[inv] - r.pct >= 0.05:
                ev = "DECREASED"
            else:
                ev = "UNCHANGED"
            out.append([sym, inv, ev, r.pct, old.get(inv), r.as_of, r.source_url])
        last_as_of = df[(df.symbol == sym) & (df.as_of_dt == last)]["as_of"].iloc[0]
        for inv in old.index.difference(cur.index):
            out.append([sym, inv, "EXITED (sold or below 1%)", None, old[inv], last_as_of, None])
    return pd.DataFrame(out, columns=cols)


def portfolio(conn, investor) -> pd.DataFrame:
    ch = holding_changes(conn)
    p = ch[ch["investor"] == investor].copy()
    if p.empty:
        return p
    names = dict(conn.execute("SELECT symbol, MAX(company) FROM raw_items WHERE company IS NOT NULL GROUP BY symbol").fetchall())
    p.insert(1, "company", p["symbol"].map(names))
    return p.sort_values(["event", "pct"], ascending=[True, False], key=lambda s: s.str.startswith("EXITED") if s.name == "event" else s)


def overview(conn) -> pd.DataFrame:
    ch = holding_changes(conn)
    rows = []
    for inv in load_investors():
        c = ch[ch["investor"] == inv["name"]]
        held = c[~c["event"].str.startswith("EXITED")]
        rows.append({"investor": inv["name"], "type": TYPE_LABEL.get(inv.get("type"), inv.get("type")),
                     "manager": inv.get("manager", ""), "SME stocks held (>1%)": len(held),
                     "new": int((c["event"] == "NEW ENTRY").sum()), "increased": int((c["event"] == "INCREASED").sum()),
                     "decreased": int((c["event"] == "DECREASED").sum()),
                     "exited": int(c["event"].str.startswith("EXITED").sum()), "credible": bool(inv.get("credible"))})
    return pd.DataFrame(rows).sort_values(["SME stocks held (>1%)", "investor"], ascending=[False, True])


# ------------------------------------------------------------------ deals / SAST / fund activity
def activity(scored: pd.DataFrame) -> pd.DataFrame:
    """Deals, SAST, insider and fund-portfolio facts whose counterparty is a tracked investor."""
    cols = ["date", "investor", "symbol", "event", "detail", "source", "source_url"]
    if scored is None or scored.empty:
        return pd.DataFrame(columns=cols)
    out = []
    for _, r in scored.iterrows():
        d = r["details"] or {}
        who = d.get("client") or d.get("fund") or (re.match(r"Insider: (.*?) \(", r["title"] or "") or [None, ""])[1]
        inv = match(who) or match(r["title"])
        if inv and r["raw_type"] not in ("_shareholding_snapshot",):
            out.append([str(r["when"])[:10], inv, r["symbol"], r["label"], r["summary"], r["source"], r["source_url"]])
    return pd.DataFrame(out, columns=cols).sort_values("date", ascending=False)


def news_mentions(conn, investor, limit=50) -> pd.DataFrame:
    inv = info(investor)
    names = [a for a in set(inv.get("aliases") or []) | {investor} if len(a) >= 6]
    if not names:
        return pd.DataFrame()
    where = " OR ".join(["title LIKE ? OR body LIKE ?"] * len(names))
    params = [p for n in names for p in (f"%{n}%", f"%{n}%")]
    return pd.read_sql_query(f"""SELECT published_at, symbol, title, source, source_url FROM raw_items
                                WHERE ({where}) ORDER BY published_at DESC LIMIT {int(limit)}""", conn, params=params)


def recent_events(conn, scored, days=120, symbols=None) -> pd.DataFrame:
    """One feed of tracked-investor changes: filings diffs + deals/SAST/fund activity."""
    cut = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    ch = holding_changes(conn)
    ch = ch[~ch["event"].isin(["UNCHANGED"]) & ~ch["event"].str.startswith("HOLDS")]
    ch = ch.assign(as_of_dt=to_dt(ch["as_of"]))
    f1 = pd.DataFrame({"date": ch["as_of_dt"].dt.strftime("%Y-%m-%d"), "investor": ch["investor"], "symbol": ch["symbol"],
                       "event": ch["event"],
                       "detail": ch.apply(lambda r: f"{r['prev_pct'] if pd.notna(r['prev_pct']) else '-'}% -> "
                                                    f"{r['pct'] if pd.notna(r['pct']) else '-'}% (shareholding filing)", axis=1) if not ch.empty else [],
                       "source": "shareholding", "source_url": ch["source_url"]})
    f2 = activity(scored)
    out = pd.concat([f1, f2], ignore_index=True)
    out = out[out["date"].fillna("") >= cut]
    if symbols is not None:
        out = out[out["symbol"].isin(symbols)]
    return out.sort_values("date", ascending=False)


# ------------------------------------------------------------------ per stock
def stock_view(conn, symbol, scored):
    """(tracked investors in this stock, major holders, events)."""
    df = holder_rows(conn)
    s = df[df.symbol == symbol]
    major = pd.DataFrame()
    if not s.empty:
        last = s["as_of_dt"].max()
        major = s[s.as_of_dt == last].sort_values("pct", ascending=False)[["holder", "category", "pct", "shares", "investor", "as_of", "source_url"]]
    ch = holding_changes(conn, df)
    tracked = ch[ch.symbol == symbol]
    ev = recent_events(conn, scored, days=3650, symbols=[symbol])
    return tracked, major, ev


def tracked_score(conn, symbol, sc) -> tuple[float, str]:
    """Points for the final scorecard from tracked-investor moves in the latest filing."""
    pts = (sc.get("final_scorecard") or {}).get("tracked_investors") or {}
    ch = holding_changes(conn)
    c = ch[ch.symbol == symbol]
    total, notes = 0.0, []
    for _, r in c.iterrows():
        cred = bool(info(r["investor"]).get("credible"))
        key = {"NEW ENTRY": "new_entry", "INCREASED": "increased", "DECREASED": "decreased"}.get(r["event"])
        if r["event"].startswith("EXITED"):
            key = "exited"
        if key and (cred or key in ("decreased", "exited")):
            v = float(pts.get(key, 0))
            if v:
                total += v
                notes.append(f"{r['investor']} {r['event'].split(' (')[0].lower()}")
    return total, ", ".join(notes)
