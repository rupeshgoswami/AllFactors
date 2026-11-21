# SME Signal Portal

A personal research portal for **NSE Emerge / BSE SME stocks**. It collects exchange filings,
bulk/block deals, insider and SAST disclosures, shareholding patterns, concalls, news, daily
prices, fundamentals and macro data (RBI, Fed, US 10Y, INR, crude), and gives every stock a
**Buy / Hold / Sell / Avoid** call from scoring rules you can edit. Every call can be traced
back to the original filing, with its fetch time and content hash.

> **Personal use only. Not investment advice.** Check every source yourself. Publishing
> buy/sell calls to others can require SEBI registration.

---

## 1. What it does

1. **Collects** data from free sources (NSE, BSE, Yahoo Finance, FRED, RBI, Google News RSS, ...)
   and optional paid ones, through small plug-in *source adapters*.
2. **Stores** every record in a local SQLite database (`data/portal.db`) with its source URL,
   fetch time and a sha256 content hash, so any number can be audited later.
3. **Extracts facts** from unstructured text (filings, news) with keyword rules, or with Claude
   when an API key is set. Structured data (deals, insider trades, shareholding) is parsed directly.
4. **Scores** each stock: fact score x source reliability x confidence x time decay, plus price
   momentum, valuation vs peers, tracked-investor moves and macro rules. Hard stops force **Avoid**.
5. **Shows** it in a Streamlit dashboard, locally or on Streamlit Cloud.
6. **Adds DalalOS data** (institutional holdings, bulk/block deals, insider trades) through an MCP
   integration, joined with the portal's own prices.

### Dashboard pages

| Page | What it shows |
|---|---|
| Watchlist | Your stocks and peers: price, day/month/year change, final call, new fund/investor alerts |
| SME universe | Every NSE SME stock: price moves, valuation, signal; filter, sort, add to watchlist |
| Stock | Scorecard and why, price chart with 50/200-DMA, holders above 1% (NEW/EXITED flags), promoter holding, deals / insider / SAST, concalls with one-click Claude summaries, news, peers, source evidence |
| Top investors | Well-known investors / fund managers / PMS / AIFs (editable list) and their SME holdings and moves |
| Institutional (DalalOS) | Quarterly FII / DII / MF holdings, QoQ change, pledge, bulk/block deals and insider trades, joined with portal prices |
| All SME signals | Every SME stock seen in filings, ranked |
| Macro | RBI repo, US 10Y, Fed funds, USD/INR, Brent, India 10Y, central-bank announcements and their effect on scores |
| Scorecard rules | Edit every score, weight and threshold with a live before/after preview |
| Sources | Turn sources on/off, see errors, run them |
| All items | Raw stored items |
| About / README | This document |

---

## 2. Architecture

```
                ┌────────────────────────── local machine ──────────────────────────┐
 NSE / BSE /    │  portal/sources/*  (adapters, auto-registered)                    │
 Yahoo / FRED / │        │ RawItem (url, fetched_at, sha256)                        │
 RBI / News ───►│        ▼                                                          │
                │  portal/ingest.py ──► raw_items ──► portal/extract.py ──► facts   │
                │  portal/market.py ──► prices, fundamentals, macro, holders        │
                │                         │                                         │
 DalalOS MCP ──►│  (Claude session) ─► data/imports/dalalos/*.json                  │
 (Claude Code)  │        └─ portal/dalalos.py ─► dalalos_holdings/deals/insider     │
                │                         ▼                                         │
                │                   data/portal.db  (SQLite)                        │
                │                         │                                         │
                │   portal/scoring.py + portal/insights.py + portal/investors.py    │
                │         (recomputed from stored facts on every read)              │
                │                         ▼                                         │
                │                    app.py  (Streamlit)                            │
                └──────────────┬────────────────────────────────────────────────────┘
                               │ python sync_data.py publish
                               ▼
              GitHub branch `data-snapshot`  (portal.db.gz, ONE force-pushed commit)
                               │ sync_data.py pull  (at app start, re-checked hourly)
                               ▼
              Streamlit Cloud  (PORTAL_HOSTED=1: read-only viewer of the snapshot)
```

### Data flow

`source adapters -> raw_items -> facts -> scoring -> signal`, all in `data/portal.db`.

* **Adapters** (`portal/sources/`) subclass `SourceAdapter`, register with `@register("name")` and
  yield `RawItem`s. They are wired to `config/sources.yaml` entries by `adapter: name`. Requests are
  rate limited (3 s gap) and obey robots.txt. Items with pre-structured data attach `facts=[...]`
  and skip extraction.
* **Ingest** (`portal/ingest.py`) dedupes on `(source, external_id)` and records the hash and fetch
  time. One failing source never stops the others; errors show on the Sources page.
* **Extraction** (`portal/extract.py`) turns text into typed facts with keyword rules from
  `scorecard.yaml`, or Claude (`extraction:` in `sources.yaml`; default `claude-opus-5-5`, effort low).
* **Scoring** (`portal/scoring.py`) applies score x reliability x confidence x half-life decay,
  summed per stock; hard-stop facts (pledge, auditor resignation, default, SEBI action) force Avoid.
  Scores are recomputed from stored facts every time, so rule edits apply instantly.
* **Final call** (`portal/insights.py`):
  ```
  total = filings, deals & news score   (decayed, weighted facts)
        + price momentum                (above/below 200-DMA, strong/weak 3-month return)
        + valuation vs peers            (P/E vs median of listed peers)
        + tracked investors             (new entry / increase / cut / exit)
        + macro                         (macro_rules: repo/Fed moves, US 10Y, INR, crude)
  Buy if total >= 6, Sell if <= -3, else Hold. Any hard stop = Avoid.
  ```
  All numbers live in `config/scorecard.yaml` (`final_scorecard`, `macro_rules`).
* **Market data** (`portal/market.py`, `portal/universe.py`): Yahoo prices with NSE bhavcopy as
  fallback, fundamentals (rotated per run), macro (FRED -> public CSV -> Yahoo / NY Fed), and
  XBRL holders above 1%.

### Repository layout

```
app.py                      Streamlit dashboard (all pages)
ingest.py                   CLI entry: fetch data
sync_data.py                publish / pull the database snapshot via GitHub
run.bat / run.sh            set up + ingest + open dashboard
dashboard.bat               open dashboard only
update.bat                  daily job: ingest, then publish snapshot (log: data/update.log)
config/scorecard.yaml       all scoring rules
config/sources.yaml         sources, watchlist, aliases, extraction settings
config/investors.yaml       tracked investors and aliases
config/api_keys.yaml        your keys (git-ignored; copy from api_keys.example.yaml)
portal/dalalos.py           load DalalOS MCP results into the database
portal/                     db, config, ingest, extract, scoring, insights, investors, market, universe, dalalos
portal/sources/             one file per adapter (nse, bse, bse_deals, google_news, web, generic, demo, paid_template)
tests/                      scoring, adapters, insights (use the offline demo source)
.claude/skills/institutional-holdings/SKILL.md   Claude Code skill for DalalOS data
CLAUDE.md                   guidance for Claude Code instances
data/                       portal.db and imports (git-ignored)
```

### Database tables

`raw_items`, `facts`, `source_runs`, `watchlist`, `prices`, `fundamentals`, `holders`, `macro`,
`summaries`, plus `dalalos_holdings`, `dalalos_deals`, `dalalos_insider`.

---

## 3. What it is built with

| Layer | Technology |
|---|---|
| UI | Streamlit |
| Data handling | pandas, SQLite (stdlib `sqlite3`) |
| Config | YAML (PyYAML), validated in `portal/config.py` |
| HTTP / parsing | requests, lxml, pypdf, openpyxl |
| Prices | yfinance (Yahoo), NSE bhavcopy (`nsearchives.nseindia.com`) |
| AI extraction (optional) | Anthropic API (`anthropic`), pydantic |
| Institutional data | DalalOS MCP (https://dalalos.in) via Claude Code |
| Hosting | Streamlit Cloud + GitHub branch as the data store |
| Language | Python 3.10+ |

---

## 4. How to run it

Needs Python 3.10 or newer. Always run commands from the folder that contains `app.py`.

**Windows, easiest:** double-click `run.bat`. The first run installs everything, fetches data and
opens the dashboard. Later runs fetch new data and open it. `dashboard.bat` opens without fetching.

**By hand:**

```bat
python -m venv .venv
.venv\Scripts\activate          :: macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python ingest.py                :: fetch data (a few minutes; exchange sites are queried slowly on purpose)
python -m streamlit run app.py  :: http://localhost:8501
```

### Useful commands

```bash
python ingest.py --source NAME      # one source, even if disabled
python ingest.py --reextract        # redo facts for stored items (after scorecard keyword edits)
python ingest.py --market-only      # prices / fundamentals / macro only
python ingest.py --add SYM ...      # add to watchlist
python -m pytest tests              # tests (pip install pytest first; they use the offline demo source)
python sync_data.py publish         # upload data/portal.db to the data-snapshot branch
python sync_data.py pull            # download it (keeps data/portal.before-pull.db)
python -m portal.dalalos            # load DalalOS JSON files from data/imports/dalalos/
```

Run ingest once a day: Windows Task Scheduler on `update.bat` (it ingests, then publishes), or cron.

### First run
1. Free sources are already on. The first run takes a few minutes.
2. Add your stocks on the **Watchlist** page (NSE symbol, or BSE code with exchange BSE), with
   peers, then press **Fetch latest data** in the sidebar.
3. A red box at the top lists failing sources; the **Sources** page shows the error.
4. To try it offline set `demo: enabled: true` in `config/sources.yaml` (fictional companies).

### API keys: one file
Everything needing a key lives in `config/api_keys.yaml` (git-ignored). Blank = skipped.

| Key | Needed? | Gives you |
|---|---|---|
| `FRED_API_KEY` | Recommended, free | Reliable US 10Y, Fed funds, USD/INR, Brent, India 10Y |
| `ANTHROPIC_API_KEY` | Optional | Claude fact extraction and concall summaries |
| `TRENDLYNE_API_KEY`, `TIJORI_API_KEY`, `RESEARCHBYTES_API_KEY`, `MORNINGSTAR_API_KEY`, `SCREENER_COOKIE` | Only if you subscribe | Paid sources |

---

## 5. Deploying on Streamlit Cloud

The cloud app is a **read-only viewer** of a database snapshot; it never fetches data itself.

1. Push the code to GitHub; create the Streamlit Cloud app with main file `app.py`.
2. Set env `PORTAL_HOSTED=1` (hides the Fetch button and enables snapshot download). If the repo
   is private, also set `GITHUB_TOKEN`.
3. On your machine run `python ingest.py`, then `python sync_data.py publish`. This force-pushes
   `portal.db.gz` as a single commit to branch `data-snapshot`, so the repo never grows by more than
   one copy.
4. The app downloads the snapshot at start and re-checks hourly (`ensure_data()` in `app.py`,
   cached for 3600 s). To see fresh data immediately use **Manage app -> Reboot app**.

Publishing uploads your **whole** local database.

---

## 6. DalalOS integration (institutional holdings)

The DalalOS MCP server is connected to Claude Code. MCP tools run **only inside a Claude session**,
not inside Streamlit, so the flow is:

```
Claude Code + institutional-holdings skill ─► MCP: get_shareholding, get_bulk_block_deals,
        get_insider_trades, get_pledge_trend ─► data/imports/dalalos/<SYMBOL>.json
        ─► python -m portal.dalalos ─► dalalos_* tables ─► sync_data.py publish ─► Streamlit Cloud
```

JSON file shape: `{"symbol": "BLS", "shareholding": <data of get_shareholding>, "deals": <data.deals>, "insider": <data.results>}`.

**Coverage (checked 2026-11-21):**

| Tool | Mainboard (BLS, BSL) | NSE SME (CUDML, GSMFOILS, ENCOMPAS) |
|---|---|---|
| Shareholding (FII/DII/MF %, QoQ, named holders) | Yes | `no_data` for the ones tried |
| Pledge trend | Yes | Expect no data |
| Bulk / block deals | Yes | Yes, current to the previous trading day |
| Insider trades | Yes | Only BSE-listed companies; NSE-only are not covered |

So for SME stocks, DalalOS mainly adds **deals and insider trades**; SME holdings still come from the
portal's own NSE shareholding files. DalalOS returns evidence, not recommendations. Blank FII/DII/MF
values mean "not disclosed", not zero.

---

## 7. The Buy / Hold / Sell scoring in detail

* **Fact score**: each fact type has a score from -5 to +5.
* **Source reliability**: exchange filings 1.0, news 0.6, forums 0.3 (editable).
* **Time decay**: a fact loses half its weight every `half_life_days` (default 90).
* **Hard stops** force **Avoid**.

Edit `config/scorecard.yaml` or use the **Scorecard rules** page: fact scores and keywords,
weights and thresholds, investor lists (`credible` / `avoid`). Every save backs up the old file to
`config/backups/`. After adding fact types or keywords press **Re-extract all** (or
`python ingest.py --reextract`).

### Top investors
The list is in `config/investors.yaml` (or Top investors > Edit list): name, type, optional fund
manager, `credible`, and `aliases` (every spelling used in filings; check them yourself).
Holdings come from the "holders above 1%" section of NSE shareholding XBRL filings
(`holders_scope: all_sme`, at most 150 files per run). SME filings are half-yearly, so new entries
can appear months late; deals, SAST and monthly fund portfolios catch moves sooner.

---

## 8. Sources

All are in `config/sources.yaml`; search for `TODO(YOU)` to find spots needing your input.

| Data | Source |
|---|---|
| Daily prices | Yahoo Finance (yfinance); NSE bhavcopy fallback |
| P/E, P/B, ROE, market cap | Yahoo Finance (thin for SMEs) |
| Holders above 1% | NSE shareholding XBRL files |
| Fund portfolios | Monthly fund house files (`amfi_fund_portfolios`) |
| Deals, insider, SAST | NSE / BSE |
| Announcements, compliance calendar | NSE SME, BSE (add your BSE `scrip_codes`) |
| Concalls | Exchange announcements, company IR pages, AlphaStreet |
| News | Google News RSS per company and per tracked investor, Moneycontrol, ET, CNBC-TV18, ValuePickr, Zerodha Chatter |
| Macro | FRED, rbi.org.in, RBI and Fed RSS |
| Paid / login (placeholders) | Trendlyne, Screener.in, Tijori, Researchbytes, Morningstar |

### Adding a source
**No code:** add an entry to `sources.yaml` with `adapter: rss` (any feed) or `adapter: csv_import`
(any CSV file or URL; map columns with `columns:`). **With code:** create `portal/sources/my_site.py`:

```python
from .base import RawItem, SourceAdapter, register

@register("my_site")
class MySite(SourceAdapter):
    source_type = "news"           # exchange_filing / exchange_deal / news / forum / manual
    def fetch(self):
        for row in self.get(self.cfg["url"]).json():      # self.get rate-limits
            yield RawItem(source=self.name, source_type=self.source_type,
                          external_id=row["id"], symbol=row["symbol"], title=row["headline"],
                          body=row["text"], source_url=row["link"],
                          published_at=row["date"], raw=row)
```
It is picked up automatically; set `adapter: my_site` in `sources.yaml`. Paid sources: copy
`portal/sources/paid_template.py` and fill the `TODO(YOU)` blocks, keep the key in an env var or
`api_keys.yaml`, and test with `python ingest.py --source NAME`. Use only what your subscription allows.

### Checking a source
On the Stock page open any fact under **Check the sources**: original link, publish and fetch
time, content hash, raw record, and who extracted it. **Re-fetch and verify** downloads the document
again and compares hashes. `settings.fetch_documents: true` stores filing PDFs' hashes and gives
Claude the full PDF text.

### Notes on the exchange sites
NSE and BSE block heavy scraping: one request at a time with a 3 s gap (`request_delay_seconds`);
do not lower it much. The endpoints are unofficial and change without notice; if a source errors,
find the new request URL in the browser's Network tab and put it in that source's `url:`.
SME companies report half-yearly, so data is sparse; defaults use a 90-day half-life and 365-day look-back.

---

## 9. Known gaps

* **`holders.pct` is empty.** In the local database every row in `holders` has `pct` NULL: the XBRL
  parser (`parse_shareholding_xbrl` in `portal/market.py`) guesses field names that do not match, so
  holdings above 1% have no sizes and tracked-investor scoring has less to work with.
* NSE's insider-trading (PIT) endpoint has returned no rows after early May 2026 (DalalOS insider
  data can cover BSE-listed companies).
* BSE-only SME stocks have no holder parsing. BSE bulk deals need CSV import (BSE's page is JavaScript-rendered).
* DalalOS has no shareholding data for the SME stocks tested.
* Yahoo is often missing or thin for SME stocks.

---

## 10. How it can be improved

**Data quality**
1. Fix the XBRL holder parsing so `holders.pct` is filled (inspect a real NSE shareholding XBRL and map the actual element names); add a test with a saved sample file.
2. Use DalalOS `get_stock_events`, `get_financials`, `get_peer_comparison` and `get_delivery_trends` for fundamentals and delivery data where Yahoo is thin (mainboard names first).
3. Use DalalOS deals and insider trades as a fallback source to cover the dead NSE PIT feed and BSE deals.
4. Backfill DalalOS data for the whole watchlist, not only the symbols fetched so far.

**Automation**
5. Check whether DalalOS offers a plain API key and endpoint; if so, call it from an adapter in `portal/sources/` so ingest refreshes it automatically with no Claude session.
6. Schedule `update.bat` (ingest + publish) daily, or move ingest to GitHub Actions so the snapshot refreshes without your PC.
7. Add a hosted-mode "Refresh data" button that clears the snapshot cache and re-pulls on demand.

**Product**
8. Show DalalOS institutional data on the Stock page, and feed FII/DII/MF changes into the final score.
9. Alerts (email / Telegram) for new fund entries, big bulk deals, pledge increases.
10. Backtest the scorecard against past price moves to tune weights and thresholds.

**Engineering**
11. Add `pytest` to a dev requirements file and run tests in CI (GitHub Actions) on every push.
12. Replace deprecated `use_container_width` with `width="stretch"` in Streamlit calls.
13. Split the ~800-line `app.py` into one module per page.
14. Publish only the tables the cloud app needs, so the snapshot is smaller, and consider a private repo plus `GITHUB_TOKEN` since the snapshot holds all your data.
15. Add schema migrations for `portal/db.py` instead of relying on `CREATE TABLE IF NOT EXISTS` alone.
