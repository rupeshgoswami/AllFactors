# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Personal research portal for NSE Emerge / BSE SME stocks: Streamlit dashboard + CLI ingester over a SQLite DB. Produces an editable-rules Buy/Hold/Sell/Avoid call per stock, traceable to the source filing. See `README.md` for page/feature details and per-source status.

## Commands

Run everything from the repo root (the folder containing `app.py`). Python 3.10+; a `.venv` is used on Windows.

```bash
pip install -r requirements.txt
python ingest.py                      # fetch all enabled sources (slow on purpose: 3s gap per exchange request)
python ingest.py --source NAME        # one source, even if disabled in sources.yaml
python ingest.py --reextract          # re-run fact extraction on stored items (after scorecard keyword/fact-type edits)
python ingest.py --market-only        # prices/fundamentals/macro only
python ingest.py --add SYM ...        # add to watchlist
python -m streamlit run app.py        # dashboard on :8501
python -m pytest tests                # all tests
python -m pytest tests/test_scoring.py::test_rules_extract   # single test
python sync_data.py publish|pull      # share data/portal.db via the force-pushed `data-snapshot` git branch
```

`run.bat`/`run.sh` = setup + ingest + open dashboard; `dashboard.bat` = open only; `update.bat` = ingest then `sync_data.py publish` (scheduled daily, logs to `data/update.log`). No linter is configured.

Tests use the fictional `demo` source (`portal/sources/demo.py`, `tests/seed.py`) against a temp DB — no network needed. To run the app offline set `demo: enabled: true` in `config/sources.yaml`.

## Architecture

Pipeline: **source adapters → `raw_items` → `facts` → scoring → signal**, all in `data/portal.db` (git-ignored; schema in `portal/db.py`).

- **Adapters** (`portal/sources/`): subclass `SourceAdapter`, decorate with `@register("name")`, yield `RawItem`s from `fetch()`. They are auto-registered into `REGISTRY` and wired up by `adapter: name` entries in `config/sources.yaml`. `self.get()` handles rate limiting and robots.txt. Items with pre-structured data (deals, insider, shareholding) attach `facts=[...]` directly and skip extraction; `paid_template.py` is a skeleton for paid sources.
- **Ingest** (`portal/ingest.py`): `run_source` dedupes on `(source, external_id)` and stores a sha256 content hash + fetch time (the evidence trail). One source failing does not stop others; errors surface on the Sources page.
- **Extraction** (`portal/extract.py`): unstructured items become typed facts via keyword rules from `scorecard.yaml`, or via Claude if `ANTHROPIC_API_KEY` is set (model/effort under `extraction:` in sources.yaml). Structured data never goes to Claude.
- **Scoring** (`portal/scoring.py`): score per fact type × source reliability × confidence × time decay (half-life), summed per stock; hard-stop facts force Avoid. Scores are **recomputed from stored facts on every read**, so rule edits apply instantly.
- **Final call** (`portal/insights.py`): filings score + price momentum + valuation vs peers + tracked-investor changes (`portal/investors.py`) + macro rules → Buy/Hold/Sell thresholds, all under `final_scorecard` / `macro_rules` in `config/scorecard.yaml`.
- **Market data** (`portal/market.py`, `portal/universe.py`): Yahoo/NSE bhavcopy prices, fundamentals, macro (FRED → public CSV → Yahoo/NY Fed fallbacks), XBRL holders >1%. Universe is all NSE SME stocks; fundamentals and shareholding sweeps are rotated per run (`fundamentals_max_per_run`, `sweep_per_run`), so full coverage takes several runs.
- **`app.py`**: single Streamlit file (all pages). Scorecard rules page writes `config/scorecard.yaml` and backs up to `config/backups/`.

### Config and secrets
- `config/*.yaml` is the behavior surface (scorecard, sources + watchlist + aliases, investors); `portal/config.py` loads/validates it (`validate_scorecard`).
- API keys live in `config/api_keys.yaml` (git-ignored; copy from `api_keys.example.yaml`); `portal/__init__.py` exports non-empty values as env vars at import time.

### Hosted mode
With env `PORTAL_HOSTED` set (Streamlit Cloud), `app.py` is a read-only viewer: it pulls the DB snapshot at startup (`sync_data.py pull`, uses `GITHUB_TOKEN` for private repos) and hides the Fetch button.

## Gotchas
- NSE/BSE endpoints are unofficial and change without notice; fix by updating the source's `url:` in sources.yaml rather than the adapter where possible. Don't lower `request_delay_seconds`.
- Known source gaps: NSE PIT insider endpoint returns nothing after early May 2026; BSE-only SME stocks have no holder parsing; BSE bulk deals need CSV import.
- Search `TODO(YOU)` in `config/sources.yaml` / `paid_template.py` for spots needing user input.
