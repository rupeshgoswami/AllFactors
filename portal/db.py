"""SQLite storage. Every raw item keeps its source URL, fetch time and content hash
so any signal can be traced back and checked against the original."""
import json
import sqlite3
from contextlib import contextmanager

from . import DATA_DIR

DB_PATH = DATA_DIR / "portal.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_items (
    id            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,          -- adapter name, e.g. nse_announcements
    source_type   TEXT NOT NULL,          -- exchange_filing / exchange_deal / news / forum / manual
    external_id   TEXT NOT NULL,          -- id from the source, used to dedupe
    symbol        TEXT,
    company       TEXT,
    title         TEXT,
    body          TEXT,
    source_url    TEXT,
    published_at  TEXT,
    fetched_at    TEXT NOT NULL,
    content_hash  TEXT NOT NULL,          -- sha256 of the raw record as fetched
    doc_hash      TEXT,                   -- sha256 of the linked document (PDF etc.) if downloaded
    raw_json      TEXT,
    extracted     INTEGER DEFAULT 0,
    UNIQUE(source, external_id)
);
CREATE TABLE IF NOT EXISTS facts (
    id            INTEGER PRIMARY KEY,
    item_id       INTEGER NOT NULL REFERENCES raw_items(id),
    symbol        TEXT,
    fact_type     TEXT NOT NULL,
    summary       TEXT,
    evidence      TEXT,                   -- quote from the source that supports the fact
    confidence    REAL DEFAULT 1.0,
    extractor     TEXT,                   -- rules / claude:<model> / structured
    details_json  TEXT,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS source_runs (
    id INTEGER PRIMARY KEY, source TEXT, started_at TEXT, finished_at TEXT,
    new_items INTEGER, status TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS watchlist (
    symbol    TEXT PRIMARY KEY,      -- NSE symbol (e.g. ABCSME) or BSE scrip code
    exchange  TEXT DEFAULT 'NSE',    -- NSE or BSE
    name      TEXT,
    ticker    TEXT,                  -- Yahoo ticker; default SYMBOL.NS / CODE.BO
    aliases   TEXT,                  -- comma separated names used to match news
    peers     TEXT,                  -- comma separated peer symbols
    added_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, source TEXT,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS fundamentals (
    symbol TEXT PRIMARY KEY, data_json TEXT, source TEXT, fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS holders (       -- named holders from shareholding filings
    symbol TEXT, as_of TEXT, name TEXT, category TEXT, pct REAL, shares REAL,
    source_url TEXT, fetched_at TEXT,
    PRIMARY KEY (symbol, as_of, name, category)
);
CREATE TABLE IF NOT EXISTS macro (
    series TEXT, date TEXT, value REAL, source TEXT, PRIMARY KEY (series, date)
);
CREATE TABLE IF NOT EXISTS summaries (
    item_id INTEGER PRIMARY KEY, summary TEXT, model TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_items_symbol ON raw_items(symbol);
CREATE INDEX IF NOT EXISTS ix_facts_symbol ON facts(symbol);

CREATE TABLE IF NOT EXISTS dalalos_holdings (
    symbol TEXT NOT NULL, period_end TEXT NOT NULL,
    promoter_pct REAL, fii_pct REAL, dii_pct REAL, mf_pct REAL, retail_pct REAL,
    fii_chg_pp REAL, dii_chg_pp REAL, pledged_pct REAL, named_json TEXT, fetched_at TEXT,
    PRIMARY KEY (symbol, period_end)
);
CREATE TABLE IF NOT EXISTS dalalos_deals (
    symbol TEXT NOT NULL, deal_date TEXT NOT NULL, deal_type TEXT, client TEXT NOT NULL,
    side TEXT NOT NULL, qty REAL NOT NULL, price REAL NOT NULL, exchange TEXT, fetched_at TEXT,
    PRIMARY KEY (symbol, deal_date, client, side, qty, price)
);
CREATE TABLE IF NOT EXISTS dalalos_insider (
    symbol TEXT NOT NULL, person TEXT NOT NULL, category TEXT, side TEXT NOT NULL,
    shares REAL NOT NULL, value_inr REAL, trade_from TEXT NOT NULL, mode TEXT, source TEXT,
    fetched_at TEXT,
    PRIMARY KEY (symbol, person, trade_from, side, shares)
);
"""


@contextmanager
def connect(path=None):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def insert_item(conn, item, fetched_at, content_hash):
    """Insert a RawItem; returns the new row id, or None if it already exists."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO raw_items
           (source, source_type, external_id, symbol, company, title, body, source_url,
            published_at, fetched_at, content_hash, doc_hash, raw_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (item.source, item.source_type, item.external_id, item.symbol, item.company,
         item.title, item.body, item.source_url, item.published_at, fetched_at,
         content_hash, item.doc_hash, json.dumps(item.raw, default=str, ensure_ascii=False)),
    )
    return cur.lastrowid if cur.rowcount else None


def add_fact(conn, item_id, symbol, fact_type, summary, evidence="", confidence=1.0,
             extractor="rules", details=None):
    conn.execute(
        """INSERT INTO facts (item_id, symbol, fact_type, summary, evidence, confidence,
           extractor, details_json) VALUES (?,?,?,?,?,?,?,?)""",
        (item_id, symbol, fact_type, summary, evidence, confidence, extractor,
         json.dumps(details or {}, default=str)),
    )


def watchlist_rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM watchlist ORDER BY symbol")]


def yahoo_ticker(row) -> str:
    if row.get("ticker"):
        return row["ticker"]
    return f"{row['symbol']}.BO" if (row.get("exchange") or "NSE").upper() == "BSE" else f"{row['symbol']}.NS"
