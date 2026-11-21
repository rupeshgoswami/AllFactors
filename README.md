# SME Signal Portal

A personal research portal for NSE Emerge / BSE SME stocks. It pulls exchange filings,
bulk/block deals, insider and SAST disclosures, shareholding patterns, concalls, news,
daily prices, fundamentals and macro data (RBI, Fed, US 10Y, INR, crude), and gives every
stock a **Buy / Hold / Sell / Avoid** call from rules you can edit. Every call can be
traced back to the original filing, with its fetch time and content hash.

> **Personal use only. Not investment advice.** Check every source yourself. Publishing
> buy/sell calls to others can require SEBI registration.

**Pages**
* **SME universe** - every NSE SME stock: price moves, valuation, signal; filter, sort, add to watchlist.
* **Watchlist** - add stocks (and their peers); price, day/month/year change, final call,
  and alerts when a new fund or investor enters.
* **Stock** - final scorecard and why, price chart with 50/200-DMA, holders above 1% with
  NEW / EXITED flags, promoter holding over time, deals / insider / SAST / fund activity,
  concalls with one-click Claude summaries, news, peer comparison, and the source evidence.
* **Top investors** - well-known Indian investors, fund managers, PMS/AIFs and fund houses
  (editable list); each one's SME holdings above 1%, new entries / increases / cuts / exits,
  their deals and SAST disclosures, and news mentioning them.
* **All SME signals** - every SME stock seen in filings, ranked.
* **Macro** - RBI repo rate, US 10Y, Fed funds, USD/INR, Brent, India 10Y, RBI and Fed
  announcements, and how the macro rules move every stock's score.
* **Scorecard rules** - edit every score, weight and threshold, with a live preview.
* **Sources** - turn sources on/off, see errors, run them.

## API keys: one file

Everything that needs a key lives in **`config/api_keys.yaml`** (git-ignored). Paste a key
between the quotes and save; it is loaded when the app or `ingest.py` starts. Blank = skipped.

| Key | Needed? | What it gives you |
|---|---|---|
| `FRED_API_KEY` | Recommended, free (fredaccount.stlouisfed.org/apikeys) | Reliable US 10Y, Fed funds, USD/INR, Brent, India 10Y. Without it the app uses FRED's public CSV, then Yahoo Finance / NY Fed |
| `ANTHROPIC_API_KEY` | Optional | Claude fact extraction and concall summaries |
| `TRENDLYNE_API_KEY`, `TIJORI_API_KEY`, `RESEARCHBYTES_API_KEY`, `MORNINGSTAR_API_KEY`, `SCREENER_COOKIE` | Only if you subscribe | Paid sources, see "Adding a paid source" |

Everything else (NSE, Yahoo Finance, NY Fed, RBI, Google News RSS) needs no key.

## Coverage: the whole SME universe

`market_data.universe: all_sme` (default) fetches prices for every NSE SME stock and fundamentals
(P/E, market cap, ROE...) in rotation (`fundamentals_max_per_run`, default 250 per run, so the full
~580 fill in over 2-3 runs). The **SME universe** page lists all of them; tick rows to add to your
watchlist. Shareholding history for other stocks is swept 60 stocks per run
(`nse_sme_shareholding.sweep_per_run`), and news is matched against every SME company name. Peers
default to same-industry stocks when you have not set any.

Known gaps (source side, not fixable here): NSE's insider-trading (PIT) endpoint has returned no
rows after early May 2026; BSE-only SME stocks have no holder parsing; BSE bulk deals need the
CSV import (BSE's page is now JavaScript-rendered).

## Run it

Needs Python 3.10 or newer (python.org; on Windows tick **"Add python.exe to PATH"** when installing).

**Windows, easiest:** unzip, open the folder that contains `run.bat` and `app.py`
(Windows sometimes makes a folder inside a folder with the same name), and double-click
**`run.bat`**. The first run installs everything (a few minutes), fetches real data and
opens the dashboard in your browser. Later, `run.bat` fetches new data and opens the
dashboard; `dashboard.bat` opens it without fetching.

**By hand (Windows Command Prompt):**

```bat
cd C:\path\to\sme-signal-portal        :: the folder that contains app.py
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python ingest.py                         :: fetches real data (a few minutes)
python -m streamlit run app.py           :: opens http://localhost:8501
```

**macOS / Linux:** `./run.sh`, or the same steps with `source .venv/bin/activate`.

Always run commands from the folder that contains `app.py`. `python ingest.py`,
`python -m portal.ingest` and `python portal\ingest.py` all work.

### First run

1. The free sources are already switched on, so the first run fetches real data. This takes
   a few minutes because the exchange sites are queried slowly on purpose.
2. Open the **Watchlist** page and add your stocks (NSE symbol, or BSE code with exchange
   BSE), with their peers and the company name. Then press **Fetch latest data** in the
   sidebar. That loads prices, fundamentals, holders and peer data for them.
3. If a source fails, a red box at the top lists it, and the **Sources** page shows the error.
   Send the error text and it can be fixed. Other sources keep working.

To try the app with no internet, set `demo: enabled: true` in `config/sources.yaml`
(fictional companies).

Run ingest once a day: point Windows Task Scheduler at `.venv\\Scripts\\python.exe ingest.py`
(start in the project folder), or use cron on macOS/Linux.

### Optional: Claude for fact extraction

Without an API key, facts come from keyword rules in the scorecard (free, offline, rougher).
With a key, Claude reads each filing and returns typed facts with an exact evidence quote.

```bash
set ANTHROPIC_API_KEY=sk-ant-...         # macOS/Linux: export ANTHROPIC_API_KEY=sk-ant-...
python ingest.py --reextract     # redo facts for stored items with Claude
```

Model and effort are set under `extraction:` in `config/sources.yaml`
(default `claude-opus-5-5` at `effort: low`). Structured data (bulk/block deals, insider
trades, shareholding) never goes to Claude; it is parsed directly.

## Top investors

The list lives in `config/investors.yaml` (or the Top investors page > Edit list). Each entry
has a name, type, optional fund manager, `credible: true/false`, and `aliases` - every spelling
and holding entity used in filings (e.g. "Mukul Mahavir Agrawal", "Param Capital"). The
starter list is well-known public names; check the aliases yourself.

* Holdings come from the "holders above 1%" section of NSE shareholding XBRL filings. With
  `holders_scope: all_sme` (default) every SME stock's filings are read, at most 150 files
  per run, so the first full backfill takes a few daily runs.
* SME filings are half-yearly, so a new entry can show up months late. Bulk/block deals,
  SAST disclosures and monthly fund portfolios (`amfi_fund_portfolios`) catch moves sooner.
* On a stock page, tracked investors are listed first; if none hold it, the major
  shareholders (promoters and biggest holders) are shown.
* New entries / increases by credible investors add points to the final call; cuts and
  exits subtract (`final_scorecard.tracked_investors` in scorecard.yaml).
* BSE-only SME stocks: holder parsing is NSE-only for now. TODO(YOU): Trendlyne's superstar
  portfolios (paid) can fill this through the `trendlyne_superstars` placeholder.

## The final Buy / Hold / Sell call

```
total = filings, deals & news score   (decayed, weighted facts - see below)
      + price momentum                 (above/below 200-DMA, strong/weak 3-month return)
      + valuation vs peers             (P/E vs the median of the peers you list)
      + tracked investors              (new entry / increase / cut / exit in the latest filing)
      + macro                          (macro_rules: repo/Fed moves, US 10Y, INR, crude)
Buy if total >= 6, Sell if <= -3, else Hold. Any hard stop = Avoid.
```

Every number above is in `config/scorecard.yaml` under `final_scorecard` and `macro_rules`,
and editable on the Scorecard rules page (tab "Final call & macro").

## Where each kind of data comes from

| Data | Source | Notes |
|---|---|---|
| Daily prices | Yahoo Finance (yfinance), NSE price history as fallback | Some SME stocks are missing on Yahoo; set the `ticker` on the Watchlist page if needed |
| P/E, P/B, ROE, market cap | Yahoo Finance | Often thin for SMEs; add Screener/Tijori via the paid placeholders for better data |
| Holders above 1% | NSE shareholding XBRL files (watchlist stocks) | Half-yearly for SMEs, so a new fund shows up with a delay |
| Fund portfolios | Monthly fund house files (`amfi_fund_portfolios`) | Earlier than SME shareholding filings |
| Deals, insider, SAST | NSE / BSE | Daily |
| Concalls | Exchange announcements, company IR pages, AlphaStreet | "Summarise with Claude" needs ANTHROPIC_API_KEY |
| Macro | FRED (US 10Y, Fed funds, USD/INR, Brent, India 10Y), rbi.org.in (repo rate), RBI and Fed RSS | The repo rate is read daily, so its history builds up from your first run |

## How the filings score is built

```
source adapters ──> raw_items (url, fetched_at, sha256) ──> facts (type, evidence, confidence)
                                                                │
                      scorecard.yaml ──> score × source reliability × confidence × time decay
                                                                │
                                         sum per stock ──> Bullish / Neutral / Bearish
                                         any hard stop  ──> Avoid
```

* **Score**: each fact type has a score from -5 to +5.
* **Source reliability**: exchange filings count 1.0, news 0.6, forums 0.3 (editable).
* **Time decay**: a fact loses half its weight every `half_life_days` (default 90).
* **Hard stops** (pledge, auditor resignation, default, SEBI action) force **Avoid**.
* Scores are recomputed from stored facts every time, so rule edits apply instantly.

## Adjusting the scorecard

Either edit `config/scorecard.yaml`, or use the **Scorecard rules** page:

* **Fact scores**: change a score, tick/untick hard stop, edit keywords, or add a row
  for a new fact type. The page shows a before/after preview of every stock's signal.
* **Weights & thresholds**: source reliability, decay half-life, Bullish/Bearish cut-offs.
* **Investor lists**: funds you trust (`credible`) and entities you treat as operators
  (`avoid`). Bulk deals are re-classified against these lists instantly.
* Every save backs up the previous file to `config/backups/`.

After adding new fact types or keywords, press **Re-extract all** on the Sources page
(or `python ingest.py --reextract`) so old items get the new types.

## Resources included

All are in `config/sources.yaml`, switched off until you enable them. Search that file
for `TODO(YOU)` to see every spot that needs your input.

| Resource | Entry in sources.yaml | Status |
|---|---|---|
| NSE SME announcements (incl. IPO anchor / allotment notices) | `nse_sme_announcements` | Ready |
| NSE bulk deals / block deals | `nse_bulk_deals`, `nse_block_deals` | Ready |
| NSE insider trading (PIT) | `nse_insider_trading` | Ready |
| NSE SAST Reg 29 | `nse_sast_reg29` | Ready |
| NSE SME shareholding pattern | `nse_sme_shareholding` | Ready |
| NSE SME compliance / event calendar | `nse_sme_compliance_calendar` | Ready |
| BSE announcements | `bse_announcements` | Ready; add your BSE SME `scrip_codes` |
| BSE SME bulk deals | `bse_sme_bulk_deals` (page) or `bse_sme_bulk_deals_csv` (download) | Ready |
| Chittorgarh SME IPO pages | `chittorgarh_sme_ipo_pages` | Add the IPO page URLs you track |
| AMFI / fund house monthly portfolios | `amfi_fund_portfolios` | Add file, fund name, month, ISIN→symbol map |
| PMS / AIF quarterly letters | `pms_aif_letters` | Add each fund's letters URL |
| Company investor-relations pages | `company_ir_pages` | Add company URLs |
| ValuePickr | `valuepickr_latest` | Ready; fill `company_aliases` |
| Zerodha The Chatter, AlphaStreet India | `zerodha_the_chatter`, `alphastreet_india` | Ready; check the feed URL once |
| PMS Bazaar, SOIC (YouTube) | `pms_bazaar_youtube`, `soic_youtube` | Add the channel ID |
| Moneycontrol, ET Markets / ET Now, CNBC-TV18 | `moneycontrol_markets`, `et_markets`, `cnbctv18_market` | Ready; check the feed URL once |
| Trendlyne, Screener.in, Tijori, Researchbytes, Morningstar | `trendlyne_superstars`, `screener_in`, `tijori_finance`, `researchbytes`, `morningstar_india` | Paid / login: placeholders for you to complete |

I could not reach these sites from where I built this, so feed URLs marked `check` in
sources.yaml are best-known addresses. If one fails, open the site, find its RSS link,
and paste it in. The web adapters (`rss`, `web_page`) obey each site's robots.txt.

## Adding a paid source

Use only what your subscription and the site's terms allow. Pick one of two ways:

**A) Export a file (simplest).** Download a CSV/Excel from the site, save it in
`data/imports/`, and in `config/sources.yaml` change that entry to:

```yaml
  trendlyne_superstars:
    adapter: csv_import
    enabled: true
    path: data/imports/trendlyne_superstars.csv   # your file
    kind: deal                                    # rows are buys/sells by an investor
    source_type: manual
    columns: {symbol: "NSE Code", client: "Investor", side: "Action",
              qty: "Shares", price: "Price", date: "Date"}   # your file's headers
```

**B) Use their API.**
1. Copy `portal/sources/paid_template.py` to e.g. `portal/sources/trendlyne.py`.
2. In the copy, change `@register("paid_template")` to `@register("trendlyne")` and fill
   each `TODO(YOU)` block: auth header, where the rows are, field mapping.
3. Put your key in an environment variable, e.g. Windows `setx TRENDLYNE_API_KEY "..."`
   (then open a new window), macOS/Linux `export TRENDLYNE_API_KEY=...`.
4. In `sources.yaml` set `adapter: trendlyne`, `url:` to the API endpoint,
   `api_key_env: TRENDLYNE_API_KEY`, `enabled: true`.
5. Test it alone: `python ingest.py --source trendlyne_superstars`.

## Adding other resources

**Without code** — add an entry to `config/sources.yaml`:

```yaml
  my_news:
    adapter: rss                 # any RSS/Atom feed: news site, blog, YouTube channel
    enabled: true
    url: https://example.com/rss
    source_type: news            # sets its reliability weight
    require_symbol_match: true   # keep only items mentioning a watchlist stock / alias

  bse_sme_bulk:
    adapter: csv_import          # any CSV file or URL: BSE SME bulk deals, Trendlyne export...
    enabled: true
    path: data/imports/bse_sme_bulk.csv
    kind: deal
    source_type: exchange_deal
    columns: {symbol: "Scrip Code", client: "Client Name", side: "Deal Type",
              qty: "Quantity", price: "Price", date: "Deal Date"}
```

For news matching, fill `settings.watchlist` and `settings.company_aliases`.

**With code** — create `portal/sources/my_site.py`:

```python
from .base import RawItem, SourceAdapter, register

@register("my_site")
class MySite(SourceAdapter):
    source_type = "news"           # exchange_filing / exchange_deal / news / forum / manual

    def fetch(self):
        data = self.get(self.cfg["url"]).json()     # self.get waits between requests
        for row in data:
            yield RawItem(source=self.name, source_type=self.source_type,
                          external_id=row["id"], symbol=row["symbol"],
                          title=row["headline"], body=row["text"],
                          source_url=row["link"], published_at=row["date"], raw=row)
```

It is picked up automatically; add `adapter: my_site` to `sources.yaml`. If your source
already gives clean structured data, attach facts directly with
`facts=[{"fact_type": "order_win", "summary": "...", "evidence": "..."}]` to skip extraction.

## Checking a source

On the Signals page, click a stock, then open any fact under **Check the sources**:
original link, publish and fetch time, content hash of the record as fetched, the raw
record, and who extracted the fact (rules, Claude, or structured parsing).
**Re-fetch and verify** downloads the document again and compares its hash. Turn on
`settings.fetch_documents: true` to download filing PDFs at ingest time; that stores a
document hash and gives Claude the full PDF text instead of just the headline.

## Notes on the exchange sites

* NSE and BSE block heavy scraping. Adapters make one request at a time with a 3-second
  gap (`request_delay_seconds`). Don't lower it much, and run ingest once or twice a day.
* The endpoints are the public APIs behind nseindia.com and bseindia.com pages. They change
  without notice. If a source shows an error on the Sources page, open the matching page on
  the exchange site, find the new request in the browser's Network tab, and put the URL in
  that source's `url:` setting (placeholders `{from}` / `{to}` are filled in).
* BSE's announcement API has no SME filter, so list your BSE SME `scrip_codes`.
* SME companies report half-yearly, so shareholding and results data is sparse; the
  defaults use a 90-day half-life and a 365-day look-back for that reason.

## Project layout

```
app.py                     Streamlit dashboard (Signals, Scorecard rules, Sources, All items)
config/scorecard.yaml      all scoring rules
config/sources.yaml        sources, watchlist, extraction settings
config/investors.yaml      tracked investors / fund managers and their aliases
portal/investors.py        investor matching, portfolios, changes
portal/sources/            one file per adapter; auto-registered
portal/sources/paid_template.py  template for paid / login sources (TODO(YOU) markers)
portal/extract.py          Claude + keyword fact extraction
portal/scoring.py          scoring, decay, hard stops, signal
ingest.py                  CLI: python ingest.py [--source NAME] [--reextract] [--market-only] [--add SYM ...]
portal/market.py           prices, fundamentals, macro, holders (XBRL)
portal/insights.py         stock page data + final Buy/Hold/Sell scorecard
run.bat / dashboard.bat    Windows: set up + fetch + open / open only
portal/ingest.py           ingest logic
portal/db.py               SQLite schema (data/portal.db)
tests/                     python -m pytest tests
```
