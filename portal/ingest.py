"""Run sources, store new items, extract facts.

    python -m portal.ingest                 # all enabled sources
    python -m portal.ingest --source demo   # one source (even if disabled)
    python -m portal.ingest --reextract     # re-run extraction on stored items
"""

# Allow running this file directly (python portal\ingest.py or double-click).
if __name__ == "__main__" and not __package__:
    import pathlib as _p
    import sys as _s
    _s.path.insert(0, str(_p.Path(__file__).resolve().parent.parent))
    from portal.ingest import main as _main
    _main()
    raise SystemExit

import argparse
import io
import traceback
from datetime import datetime

from .config import load_sources
from .db import connect, insert_item, watchlist_rows
from .extract import extract_pending, reextract_all, store_structured
from .sources import REGISTRY
from .sources.base import sha256_bytes


def _attach_document(adapter, item, log):
    url = item.source_url or ""
    if not url.lower().endswith(".pdf") or "example.com" in url:
        return
    try:
        data = adapter.get(url).content
        item.doc_hash = sha256_bytes(data)
        try:
            from pypdf import PdfReader
            text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages[:30])
            item.body = (item.body + "\n\n" + text).strip()
        except Exception:
            pass
    except Exception as e:
        log(f"  could not fetch document {url}: {e}")


def run_source(conn, name, scfg, settings, log=print):
    cls = REGISTRY[scfg["adapter"]]
    adapter = cls(name, scfg, settings)
    started = datetime.now().isoformat(timespec="seconds")
    new, status, msg = 0, "ok", ""
    try:
        for item in adapter.fetch():
            if settings.get("fetch_documents") and not item.facts:
                exists = conn.execute("SELECT 1 FROM raw_items WHERE source=? AND external_id=?",
                                      (item.source, item.external_id)).fetchone()
                if not exists:
                    _attach_document(adapter, item, log)
            fetched = datetime.now().isoformat(timespec="seconds")
            rid = insert_item(conn, item, fetched, item.content_hash())
            if rid:
                new += 1
                if item.facts:
                    store_structured(conn, rid, item.symbol, item.facts)
    except Exception as e:
        status, msg = "error", f"{type(e).__name__}: {e}"
        log(f"  {name}: {msg}")
        traceback.print_exc()
    conn.execute("INSERT INTO source_runs (source, started_at, finished_at, new_items, status, message)"
                 " VALUES (?,?,?,?,?,?)",
                 (name, started, datetime.now().isoformat(timespec="seconds"), new, status, msg))
    conn.commit()
    log(f"{name}: {new} new items ({status})")
    return new, status, msg


def run_market(conn, settings, mcfg, log=print):
    from . import market
    steps = [("prices", lambda: market.update_prices(conn, settings, mcfg, log)),
             ("fundamentals", lambda: market.update_fundamentals(conn, log, mcfg)),
             ("macro", lambda: market.update_macro(conn, settings, mcfg, log)),
             ("holders", lambda: market.update_holders(conn, settings, mcfg, log))]
    for name, fn in steps:
        if not mcfg.get(name, True):
            continue
        started = datetime.now().isoformat(timespec="seconds")
        status, msg = "ok", ""
        try:
            fn()
        except Exception as e:
            status, msg = "error", f"{type(e).__name__}: {e}"
            log(f"market {name}: {msg}")
        conn.execute("INSERT INTO source_runs (source, started_at, finished_at, new_items, status, message)"
                     " VALUES (?,?,?,?,?,?)", (f"market:{name}", started,
                                              datetime.now().isoformat(timespec="seconds"), 0, status, msg))
        conn.commit()


def settings_with_watchlist(conn, cfg):
    settings = dict(cfg.get("settings", {}))
    settings["_watchlist_rows"] = watchlist_rows(conn)
    return settings


def run(only=None, log=print, market=True):
    cfg = load_sources()
    with connect() as conn:
        settings = settings_with_watchlist(conn, cfg)
        for name, scfg in (cfg.get("sources") or {}).items():
            if only and name not in only:
                continue
            if not only and not scfg.get("enabled"):
                continue
            run_source(conn, name, scfg, settings, log)
        extract_pending(conn, log)
        if market and not only:
            run_market(conn, settings, cfg.get("market_data", {}), log)
        failed = conn.execute("""SELECT source, message FROM source_runs r WHERE status='error' AND id =
                                 (SELECT MAX(id) FROM source_runs WHERE source=r.source)""").fetchall()
        if failed:
            log("\nSOURCES THAT FAILED (details also on the Sources page):")
            for f in failed:
                log(f"  - {f['source']}: {f['message'][:200]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", action="append")
    ap.add_argument("--reextract", action="store_true")
    ap.add_argument("--market-only", action="store_true", help="only prices, fundamentals, macro, holders")
    ap.add_argument("--add", nargs="+", metavar="SYMBOL", help="add stocks to the watchlist")
    a = ap.parse_args()
    if a.add:
        with connect() as conn:
            for sym in a.add:
                conn.execute("INSERT OR IGNORE INTO watchlist (symbol) VALUES (?)", (sym.upper(),))
        print("Added to watchlist:", ", ".join(s.upper() for s in a.add))
    if a.market_only:
        cfg = load_sources()
        with connect() as conn:
            run_market(conn, settings_with_watchlist(conn, cfg), cfg.get("market_data", {}))
    elif a.reextract:
        with connect() as conn:
            reextract_all(conn)
    else:
        run(a.source)


if __name__ == "__main__":
    main()
