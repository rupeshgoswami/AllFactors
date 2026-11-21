---
name: institutional-holdings
description: Fetch quarterly FII/DII/MF/named-institution holdings, bulk/block deals, insider trades and promoter pledge for a stock via the DalalOS MCP, and join them with the portal's own prices/holders in data/portal.db. Use when asked about who holds or is buying/selling a watchlist or SME stock, or to fill gaps in the portal's holders/insider/BSE-deal data.
---

# Institutional holdings via DalalOS MCP

DalalOS MCP tools are available **to Claude in this session only**; the Python app (`app.py`, `ingest.py`) cannot call them. So the pattern is: Claude fetches -> writes a CSV under `data/imports/` -> the existing `csv_import` adapter (see `config/sources.yaml`) ingests it. Never edit `data/portal.db` directly and don't change `portal/` code unless asked.

Always pass `query` (not `symbol`) to the per-company tools, plus a short `context` string describing the purpose by role (no names/emails).

## Coverage (verified 2026-11-21)

| Tool | Mainboard (e.g. BLS, BSL) | NSE SME (e.g. CUDML, GSMFOILS, ENCOMPAS) |
|---|---|---|
| `get_shareholding` (FII/DII/MF %, QoQ change, named holders) | Full, 4+ quarters | **`no_data`** for the SME names tried |
| `get_pledge_trend` | Yes (same source as shareholding) | Expect no_data |
| `get_bulk_block_deals` | Yes | **Yes**, current to the previous trading day |
| `get_insider_trades` (omit `symbol` for market-wide) | Yes | Only if BSE-listed; NSE-only companies are not covered |

So for SME stocks use DalalOS for **deals and insider trades**, and keep the portal's own NSE shareholding XBRL for holders. Try `get_shareholding` first and fall back on `reason_code: no_data`.

## Steps

1. **Pick symbols.** Watchlist: `SELECT symbol FROM watchlist` in `data/portal.db`. Or the user's list.
2. **Holdings (mainboard):** `get_shareholding(query, limit=8)`. Use `fii_pct`, `dii_pct`, `*_pct_change_pp` (QoQ), `breakdown.institutions` (Mutual Funds, AIF, Insurance, FPI Cat I/II) and `named_institutional_holders` (the >1% names). `fii_pct` can be null when the filing doesn't split the public category; that is "not disclosed", not zero.
3. **Pledge:** `detail.pledged_pct` from step 2, or `get_pledge_trend` for a longer series. `pledge_disclosed=false` != 0.
4. **Deals:** `get_bulk_block_deals(query, days=180, exchange="all")`. One row per client per side; buyer and seller are not paired. BSE side codes are B/S/P (P = purchase).
5. **Insider:** `get_insider_trades(symbol=..., days=90, category="all")`. SDD "buy" rows include ESOP/allotments; Form C rows have `mode` (market/other). Latest ~2 weeks are still arriving.
6. **Prices from the portal:** `SELECT date, close, volume, source FROM prices WHERE symbol=? ORDER BY date DESC LIMIT 1` (prices come from Yahoo/NSE bhavcopy). Delivery % is not in the portal DB; `get_delivery_trends` has it if needed.
7. **Join on NSE symbol** (DalalOS returns `nse_symbol`) into one row per symbol: latest close/date, FII/DII/MF %, QoQ pp change, top named holder, promoter pledge %, #bulk/block deals and net qty in the window, net insider value. Print as a table; quote each figure's `as_of`/period.

## Feeding results back into the portal (only if asked)

Write deals to `data/imports/dalalos_deals.csv` with columns `symbol, client, side, qty, price, date` and enable a `csv_import` source with `kind: deal`, `source_type: manual`, and a `columns:` map to those headers (same shape as the "bse_sme_bulk" example in README.md). Then `python ingest.py --source <name>`. `data/imports/` is git-ignored.

## Rules
- DalalOS returns evidence, not recommendations; don't turn it into a buy/sell call. The portal's scorecard does that.
- Note data staleness: results carry `as_of`, `stale` and `freshness`. Shareholding is quarterly (SME: half-yearly), so "recent" changes can be months old.
- Respect any DalalOS usage-limit notice; batch symbols sensibly (2-10 at a time).
