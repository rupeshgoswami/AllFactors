"""Seed a database with demo + synthetic market data (offline testing only)."""
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portal.config import load_sources  # noqa: E402
from portal.db import connect  # noqa: E402
from portal.extract import extract_pending  # noqa: E402
from portal.ingest import run_source  # noqa: E402


def seed(db=None):
    with connect(db) as conn:
        run_source(conn, "demo", {"adapter": "demo"}, load_sources().get("settings", {}), lambda m: None)
        extract_pending(conn, lambda m: None)
        for sym, name, peers in [("DEMOAGRO", "Demo Agro Foods Ltd", "DEMOPLAST,DEMOINFRA"),
                                 ("DEMOTECH", "Demo Techsol Ltd", "")]:
            conn.execute("INSERT OR REPLACE INTO watchlist (symbol, name, peers) VALUES (?,?,?)", (sym, name, peers))
        for k, (sym, base, drift) in enumerate([("DEMOAGRO", 150, 0.0012), ("DEMOPLAST", 80, 0.0003),
                                                ("DEMOINFRA", 160, -0.0008), ("DEMOTECH", 60, -0.001)]):
            for i in range(600):
                d = date.today() - timedelta(days=600 - i)
                c = base * math.exp(drift * i) * (1 + 0.03 * math.sin(i / 9 + k))
                conn.execute("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?,?)",
                             (sym, d.isoformat(), c, c * 1.01, c * 0.99, c, 10000 + 50 * (i % 30), "test"))
        for sym, pe in [("DEMOAGRO", 18), ("DEMOPLAST", 30), ("DEMOINFRA", 26), ("DEMOTECH", 40)]:
            conn.execute("INSERT OR REPLACE INTO fundamentals VALUES (?,?,?,?)",
                         (sym, json.dumps({"trailingPE": pe, "marketCap": 4e9, "priceToBook": 3,
                                           "returnOnEquity": 0.18, "sector": "Consumer"}), "test", "now"))
        for s, vals in [("US10Y", (4.4, 4.0)), ("RBI_REPO", (6.0, 5.5)), ("USDINR", (84, 85))]:
            for i in range(200):
                d = date.today() - timedelta(days=200 - i)
                conn.execute("INSERT OR REPLACE INTO macro VALUES (?,?,?,?)",
                             (s, d.isoformat(), vals[0] if i < 120 else vals[1], "test"))
        # Holder names below are FICTIONAL test data, except that two match the tracked-investor
        # list format so the matching logic can be exercised.
        H = {"DEMOAGRO": [("2026-03-31", [("Fund A", 2.1), ("Mr X", 1.5), ("SBI SMALL CAP FUND", 1.2)]),
                          ("2026-09-30", [("Fund A", 3.0), ("New Capital AIF", 1.8), ("SBI SMALL CAP FUND", 2.0),
                                          ("ASHISH KACHOLIA", 1.4)])],
             "DEMOPLAST": [("2026-03-31", [("Mukul Mahavir Agrawal", 2.5), ("Promoter Person", 45.0)]),
                           ("2026-09-30", [("Promoter Person", 45.0)])]}
        for sym, filings in H.items():
            for as_of, rows in filings:
                for n, pct in rows:
                    conn.execute("INSERT OR REPLACE INTO holders VALUES (?,?,?,?,?,?,?,?)",
                                 (sym, as_of, n, "Public", pct, None, "https://example.com/x.xml", "now"))
    return db


if __name__ == "__main__":
    seed()
    print("seeded data/portal.db")
