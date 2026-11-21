"""Store DalalOS MCP results in portal.db so the (read-only) Streamlit Cloud app can show them.

The DalalOS MCP only runs inside a Claude session, so Claude saves each tool's output to
data/imports/dalalos/<SYMBOL>.json and this module loads it:

    {"symbol": "BLS",
     "shareholding": <data object of get_shareholding>,      # optional
     "deals":        <data.deals of get_bulk_block_deals>,   # optional
     "insider":      <data.results of get_insider_trades>}   # optional

    python -m portal.dalalos            # load every file in data/imports/dalalos/
    python -m portal.dalalos FILE ...   # load given files
Then `python sync_data.py publish` to ship the DB to Streamlit Cloud.
"""
import json
import sys
from datetime import datetime

from . import DATA_DIR
from .db import connect

IMPORT_DIR = DATA_DIR / "imports" / "dalalos"


def _inst(period, cat):
    for i in (period.get("breakdown") or {}).get("institutions") or []:
        if i.get("category") == cat:
            return i.get("pct")
    return None


def store(conn, doc):
    sym = doc["symbol"].upper()
    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    for p in (doc.get("shareholding") or {}).get("periods") or []:
        named = (p.get("breakdown") or {}).get("named_institutional_holders") or []
        conn.execute("INSERT OR REPLACE INTO dalalos_holdings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (sym, p["period_end"], p.get("promoter_pct"), p.get("fii_pct"), p.get("dii_pct"),
                      _inst(p, "Mutual Funds"), p.get("retail_and_other_pct"),
                      p.get("fii_pct_change_pp"), p.get("dii_pct_change_pp"),
                      (p.get("detail") or {}).get("pledged_pct"),
                      json.dumps(named, ensure_ascii=False), now))
        n += 1
    for d in doc.get("deals") or []:
        conn.execute("INSERT OR REPLACE INTO dalalos_deals VALUES (?,?,?,?,?,?,?,?,?)",
                     (sym, d["deal_date"], d.get("deal_type"), d["client_name"],
                      (d.get("buy_sell") or "").upper(), d["quantity"], d["price"],
                      d.get("exchange") or "NSE", now))
        n += 1
    for t in doc.get("insider") or []:
        conn.execute("INSERT OR REPLACE INTO dalalos_insider VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (sym, t["person_name"], t.get("person_category"), t["side"], t["shares"],
                      t.get("value_inr"), t["trade_from"], t.get("mode"), t.get("source"), now))
        n += 1
    return n


def main(paths=None):
    files = [__import__("pathlib").Path(p) for p in paths] if paths else sorted(IMPORT_DIR.glob("*.json"))
    if not files:
        sys.exit("No files. Expected JSON in %s" % IMPORT_DIR)
    with connect() as conn:
        for f in files:
            print(f.name, "->", store(conn, json.loads(f.read_text(encoding="utf-8"))), "rows")


if __name__ == "__main__":
    main(sys.argv[1:] or None)
