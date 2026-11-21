"""Market data that is not "news": daily prices, fundamentals, macro series, and named
holders from shareholding filings. Controlled by `market_data:` in config/sources.yaml.

Free sources used:
  * Yahoo Finance via the yfinance package (prices, basic fundamentals). Unofficial but
    free; some SME stocks are missing there, so NSE's own price history is the fallback.
  * Macro: FRED (API key from config/api_keys.yaml, else FRED's public CSV), then Yahoo
    Finance (US 10Y, USD/INR, Brent, Nifty, India VIX) and the NY Fed (effective Fed funds
    rate) when FRED is unreachable. RBI home page for the policy repo rate.
  * NSE shareholding XBRL files for named holders above 1%.

`market_data.universe: all_sme` (default) covers every NSE SME stock, not only the watchlist.
"""
import io
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

import pandas as pd

from .db import watchlist_rows, yahoo_ticker
from .sources.base import SourceAdapter
from .universe import sme_companies

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}"
FRED_API = "https://api.stlouisfed.org/fred/series/observations"
NYFED_EFFR = "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json?startDate={start}&endDate={end}"
# fred = FRED series id; yahoo = Yahoo ticker used when FRED fails; nyfed = NY Fed rate fallback
DEFAULT_MACRO = {
    "US10Y": {"fred": "DGS10", "yahoo": "^TNX", "label": "US 10-year Treasury yield (%)"},
    "FED_FUNDS_UPPER": {"fred": "DFEDTARU", "nyfed": "effr", "label": "Fed funds rate (%)"},
    "USDINR": {"fred": "DEXINUS", "yahoo": "INR=X", "label": "USD/INR"},
    "BRENT": {"fred": "DCOILBRENTEU", "yahoo": "BZ=F", "label": "Brent crude (USD/bbl)"},
    "INDIA10Y": {"fred": "INDIRLTLT01STM", "label": "India 10-year bond yield (%), monthly"},
    "NIFTY50": {"yahoo": "^NSEI", "label": "Nifty 50"},
    "INDIAVIX": {"yahoo": "^INDIAVIX", "label": "India VIX"},
}


class _Http(SourceAdapter):
    """Reuse the polite HTTP session (delay, user agent) outside a source."""
    def fetch(self):
        return iter(())


def _http(settings, name="market"):
    return _Http(name, {}, settings)


# ------------------------------------------------------------------ universe
def universe(conn, mcfg):
    """(rows, priority): every symbol to fetch market data for. Watchlist stocks and their
    peers come first; with universe: all_sme every NSE SME stock follows."""
    rows, prio = {}, []
    for r in watchlist_rows(conn):
        rows[r["symbol"]] = r
        prio.append(r["symbol"])
    for r in list(rows.values()):
        for p in (r.get("peers") or "").split(","):
            p = p.strip().upper()
            if p and p not in rows:
                rows[p] = {"symbol": p, "exchange": r.get("exchange") or "NSE"}
                prio.append(p)
    if mcfg.get("universe", "all_sme") == "all_sme":
        for sym, name in sme_companies().items():
            rows.setdefault(sym, {"symbol": sym, "exchange": "NSE", "name": name})
    return rows, set(prio)


# ------------------------------------------------------------------ prices
def _yahoo_batch(tickers, start, log):
    """Download several tickers in one call. Returns {ticker: DataFrame(open..volume)}."""
    try:
        import yfinance as yf
        df = yf.download(list(tickers), start=start.isoformat(), progress=False, auto_adjust=False,
                         group_by="ticker", threads=True)
    except Exception as e:
        log(f"  yahoo batch: {type(e).__name__}: {e}")
        return {}
    if df is None or df.empty:
        return {}
    out = {}
    for t in tickers:
        try:
            sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
        except KeyError:
            continue
        sub = sub.copy()
        sub.columns = [str(c).lower() for c in sub.columns]
        if "close" not in sub.columns:
            continue
        sub = sub.dropna(subset=["close"])
        if not sub.empty:
            out[t] = sub[[c for c in ("open", "high", "low", "close", "volume") if c in sub.columns]]
    return out


def _store_prices(conn, sym, df, src):
    conn.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?,?)",
                     [(sym, str(d)[:10], x.get("open"), x.get("high"), x.get("low"),
                       x.get("close"), x.get("volume"), src) for d, x in df.iterrows()])


def update_prices(conn, settings, mcfg, log=print):
    rows, prio = universe(conn, mcfg)
    if not rows:
        log("prices: nothing to fetch - add stocks on the Watchlist page or set market_data.universe: all_sme")
        return
    years = int(mcfg.get("price_history_years", 3))
    today = date.today()
    last = {r[0]: r[1] for r in conn.execute("SELECT symbol, MAX(date) FROM prices GROUP BY symbol")}
    stale_before = (today - timedelta(days=1)).isoformat()
    todo = [s for s in rows if s in prio or not last.get(s) or last[s] < stale_before]
    new = [s for s in todo if not last.get(s)]
    old = sorted((s for s in todo if last.get(s)), key=lambda s: last[s])
    ok, missing = 0, []
    chunk = int(mcfg.get("price_batch_size", 40))

    def run(symbols, start_for):
        nonlocal ok
        for i in range(0, len(symbols), chunk):
            part = symbols[i:i + chunk]
            tick = {yahoo_ticker(rows[s]): s for s in part}
            got = _yahoo_batch(list(tick), start_for(part), log)
            for t, s in tick.items():
                if t in got:
                    _store_prices(conn, s, got[t], "yahoo")
                    ok += 1
                else:
                    missing.append(s)
            conn.commit()
            log(f"prices: {min(i + chunk, len(symbols))}/{len(symbols)} done")

    if new:
        run(new, lambda part: today - timedelta(days=365 * years))
    if old:
        run(old, lambda part: datetime.fromisoformat(min(last[s] for s in part)).date() - timedelta(days=5))

    # NSE's own history as a fallback for stocks Yahoo does not have (capped: it is slow)
    cap, used = int(mcfg.get("nse_fallback_max", 25)), 0
    for sym in sorted(missing, key=lambda s: s not in prio):
        if used >= cap or (rows[sym].get("exchange") or "NSE").upper() != "NSE":
            continue
        used += 1
        df = _nse_prices(sym, today - timedelta(days=365 * years) if not last.get(sym)
                         else datetime.fromisoformat(last[sym]).date() - timedelta(days=5), settings, mcfg, log)
        if df is not None and not df.empty:
            _store_prices(conn, sym, df, "nse")
            ok += 1
            missing.remove(sym)
    conn.commit()
    log(f"prices: updated {ok} of {len(todo)} stocks"
        + (f"; no price data for {len(missing)} (e.g. {', '.join(missing[:8])}). For a watchlist stock "
           "set its ticker on the Watchlist page" if missing else ""))


def _nse_prices(sym, start, settings, mcfg, log):
    from .sources.nse import NSE_HOME, NSEBase
    url = mcfg.get("nse_price_url", NSE_HOME + '/api/historical/cm/equity?symbol={symbol}'
                   '&series=["EQ","SM","ST","BE"]&from={from}&to={to}')
    nse = NSEBase("nse_prices", {}, settings)
    frames = []
    s = start
    try:
        while s <= date.today():   # NSE returns at most ~1 year per call
            e = min(s + timedelta(days=360), date.today())
            u = (url.replace("{symbol}", sym).replace("{from}", s.strftime("%d-%m-%Y"))
                 .replace("{to}", e.strftime("%d-%m-%Y")))
            data = nse.api(u).get("data", [])
            if data:
                frames.append(pd.DataFrame(data))
            s = e + timedelta(days=1)
    except Exception as e:
        log(f"  nse prices {sym}: {e}")
    if not frames:
        return None
    df = pd.concat(frames)
    m = {"CH_TIMESTAMP": "date", "CH_OPENING_PRICE": "open", "CH_TRADE_HIGH_PRICE": "high",
         "CH_TRADE_LOW_PRICE": "low", "CH_CLOSING_PRICE": "close", "CH_TOT_TRADED_QTY": "volume"}
    df = df.rename(columns=m)[list(m.values())]
    return df.set_index("date").sort_index()


# ------------------------------------------------------------------ fundamentals
FUND_KEYS = ["longName", "sector", "industry", "marketCap", "trailingPE", "priceToBook",
             "returnOnEquity", "debtToEquity", "revenueGrowth", "earningsGrowth",
             "profitMargins", "dividendYield", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
             "trailingEps", "bookValue", "totalRevenue", "ebitda", "freeCashflow",
             "sharesOutstanding", "heldPercentInsiders", "heldPercentInstitutions"]


def update_fundamentals(conn, log=print, mcfg=None):
    """Yahoo .info is one request per stock, so each run does at most
    fundamentals_max_per_run stocks: never-fetched first, then the stalest. Watchlist and peers
    always go first. A stock Yahoo has nothing for is stored as empty and retried after
    fundamentals_refresh_days."""
    mcfg = mcfg or {}
    rows, prio = universe(conn, mcfg)
    try:
        import yfinance as yf
    except ImportError:
        log("fundamentals: pip install yfinance")
        return
    cap = int(mcfg.get("fundamentals_max_per_run", 250))
    refresh = int(mcfg.get("fundamentals_refresh_days", 7))
    got = {r[0]: r[1] for r in conn.execute("SELECT symbol, fetched_at FROM fundamentals")}
    cutoff = (datetime.now() - timedelta(days=refresh)).isoformat(timespec="seconds")
    todo = [s for s in rows if s not in got or got[s] < cutoff]
    todo.sort(key=lambda s: (s not in prio, s in got, got.get(s, "")))
    todo = todo[:cap]
    n, empty, errors = 0, 0, 0
    for i, sym in enumerate(todo, 1):
        try:
            info = yf.Ticker(yahoo_ticker(rows[sym])).info or {}
        except Exception as e:
            errors += 1
            if "429" in str(e) or "Too Many" in str(e) or errors >= 15:
                log(f"  fundamentals: Yahoo is rate-limiting ({e}); the rest continues next run")
                break
            continue
        data = {k: info.get(k) for k in FUND_KEYS if info.get(k) is not None}
        # Yahoo has nothing for many tiny / new SME listings: look again in ~30 days, not every run
        when = datetime.now() + (timedelta(0) if data else timedelta(days=30 - refresh))
        conn.execute("INSERT OR REPLACE INTO fundamentals VALUES (?,?,?,?)",
                     (sym, json.dumps(data), "yahoo", when.isoformat(timespec="seconds")))
        n, empty = n + 1, empty + (0 if data else 1)
        if i % 25 == 0:
            conn.commit()
            log(f"fundamentals: {i}/{len(todo)}")
    conn.commit()
    pending = len([s for s in rows if s not in got or got[s] < cutoff]) - n
    log(f"fundamentals: fetched {n} stocks ({empty} had no data on Yahoo)"
        + (f"; {pending} more will be fetched on the next runs" if pending > 0 else ""))


# ------------------------------------------------------------------ macro
def _fred_series(http, fred_id, days):
    key = os.environ.get("FRED_API_KEY")
    if key:
        start = (date.today() - timedelta(days=days)).isoformat()
        js = http.get(FRED_API, params={"series_id": fred_id, "api_key": key, "file_type": "json",
                                        "observation_start": start}).json()
        df = pd.DataFrame(js.get("observations", []))[["date", "value"]]
    else:
        df = pd.read_csv(io.StringIO(http.get(FRED_CSV.format(id=fred_id), timeout=20).text))
        df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna().tail(days)


def _nyfed_series(http, days):
    url = NYFED_EFFR.format(start=(date.today() - timedelta(days=days)).isoformat(), end=date.today().isoformat())
    js = http.get(url, timeout=30).json()
    df = pd.DataFrame([{"date": r["effectiveDate"], "value": r["percentRate"]} for r in js["refRates"]])
    return df.sort_values("date")


def update_macro(conn, settings, mcfg, log=print):
    http = _http(settings, "macro")
    series = mcfg.get("macro_series") or DEFAULT_MACRO
    days = int(mcfg.get("macro_history_days", 1500))
    fred_down = False
    for key, s in series.items():
        df, src, errs = None, None, []
        if s.get("fred") and not fred_down:
            try:
                df, src = _fred_series(http, s["fred"], days), "FRED:" + s["fred"]
            except Exception as e:
                errs.append(f"FRED {type(e).__name__}")
                if not os.environ.get("FRED_API_KEY") and "Timeout" in type(e).__name__ + str(e):
                    fred_down = True      # do not wait for the same timeout on every series
        if (df is None or df.empty) and s.get("nyfed"):
            try:
                df, src = _nyfed_series(http, days), "NYFed:EFFR"
            except Exception as e:
                errs.append(f"NY Fed {type(e).__name__}")
        if (df is None or df.empty) and s.get("yahoo"):
            y = _yahoo_prices(s["yahoo"], date.today() - timedelta(days=days), log)
            if y is not None and not y.empty:
                df = pd.DataFrame({"date": [str(d)[:10] for d in y.index], "value": y["close"].values}).dropna()
                src = "Yahoo:" + s["yahoo"]
        if df is None or df.empty:
            log(f"macro {key}: no data ({'; '.join(errs) or 'no source configured'}). "
                "Add FRED_API_KEY in config/api_keys.yaml for a reliable feed")
            continue
        conn.executemany("INSERT OR REPLACE INTO macro VALUES (?,?,?,?)",
                         [(key, str(d)[:10], float(v), src) for d, v in df[["date", "value"]].values])
        log(f"macro {key}: latest {df['value'].iloc[-1]:.2f} on {str(df['date'].iloc[-1])[:10]} ({src})")
    try:
        html = http.get(mcfg.get("rbi_url", "https://www.rbi.org.in/")).text
        m = re.search(r"Policy\s+Repo\s+Rate\s*(?:<[^>]+>|\s|:|&nbsp;)*([\d.]+)\s*%", html, re.I)
        if m:
            conn.execute("INSERT OR REPLACE INTO macro VALUES (?,?,?,?)",
                         ("RBI_REPO", date.today().isoformat(), float(m.group(1)), "rbi.org.in"))
            log(f"macro RBI_REPO: {m.group(1)}%")
        else:
            log("macro RBI_REPO: rate not found on the RBI page (layout changed?)")
    except Exception as e:
        log(f"macro RBI_REPO: failed ({type(e).__name__}: {e})")
    conn.commit()


def _yahoo_prices(ticker, start, log):
    try:
        import yfinance as yf
        df = yf.download(ticker, start=start.isoformat(), progress=False, auto_adjust=False,
                         multi_level_index=False)
    except Exception as e:
        log(f"  yahoo {ticker}: {e}")
        return None
    if df is None or df.empty:
        return None
    df.columns = [str(c).lower() for c in df.columns]
    return df[[c for c in ("open", "high", "low", "close", "volume") if c in df.columns]]


# ------------------------------------------------------------------ holders from XBRL
def parse_shareholding_xbrl(xml_bytes):
    """Best-effort: pull (name, category, pct, shares) for named shareholders out of an
    NSE/BSE shareholding-pattern XBRL file. Facts are grouped by their context."""
    root = ET.fromstring(xml_bytes)
    by_ctx = {}
    for el in root.iter():
        ctx = el.get("contextRef")
        if not ctx or el.text is None:
            continue
        local = el.tag.split("}")[-1]
        by_ctx.setdefault(ctx, {})[local] = el.text.strip()
    out = []
    for ctx, f in by_ctx.items():
        name = f.get("NameOfTheShareholder") or f.get("NameOfShareholder")
        if not name:
            continue
        pct = (f.get("ShareholdingAsAPercentageOfTotalNumberOfShares")
               or f.get("ShareholdingAsAPercentageOfTotalNoOfShares"))
        shares = f.get("NumberOfShares") or f.get("NumberOfFullyPaidUpEquitySharesHeld")
        try:
            pct = float(pct) * (100 if pct and float(pct) <= 1 else 1)
        except (TypeError, ValueError):
            pct = None
        try:
            shares = float(shares)
        except (TypeError, ValueError):
            shares = None
        cat = re.sub(r"(?<!^)(?=[A-Z])", " ", ctx.split("_")[0]) if "_" in ctx else ctx
        if "promoter" in ctx.lower():
            cat = "Promoter: " + cat
        out.append({"name": name, "category": cat[:80], "pct": pct, "shares": shares})
    return out


def update_holders(conn, settings, mcfg, log=print):
    """Download and parse shareholding XBRL files not parsed yet. Watchlist stocks first;
    with holders_scope: all_sme, then every SME stock, at most holders_max_files_per_run
    per run so the first big backfill is spread over a few days."""
    wl = {r["symbol"] for r in watchlist_rows(conn)}
    scope = mcfg.get("holders_scope", "all_sme")
    limit = int(mcfg.get("holders_max_files_per_run", 150))
    q = """SELECT symbol, published_at, source_url, raw_json FROM raw_items
           WHERE source_url LIKE '%.xml' AND title LIKE 'Shareholding pattern%'
           ORDER BY published_at DESC"""
    rows = [r for r in conn.execute(q).fetchall() if scope == "all_sme" or r["symbol"] in wl]
    rows.sort(key=lambda r: r["symbol"] not in wl)          # watchlist first
    http = _http(settings, "holders")
    n = 0
    for r in rows:
        if n >= limit:
            log(f"holders: reached {limit} files this run; the rest continue next run")
            break
        as_of = json.loads(r["raw_json"] or "{}").get("date") or r["published_at"]
        if conn.execute("SELECT 1 FROM holders WHERE symbol=? AND as_of=?", (r["symbol"], as_of)).fetchone():
            continue
        try:
            parsed = parse_shareholding_xbrl(http.get(r["source_url"]).content)
        except Exception as e:
            log(f"  holders {r['symbol']} {as_of}: {e}")
            continue
        for h in parsed or [{"name": "(no named holders in filing)", "category": "", "pct": None, "shares": None}]:
            conn.execute("INSERT OR REPLACE INTO holders VALUES (?,?,?,?,?,?,?,?)",
                         (r["symbol"], as_of, h["name"], h["category"], h["pct"], h["shares"],
                          r["source_url"], datetime.now().isoformat(timespec="seconds")))
        n += 1
        conn.commit()
    log(f"holders: parsed {n} new shareholding filings")


def update_all(conn, settings, mcfg, log=print):
    if mcfg.get("prices", True):
        update_prices(conn, settings, mcfg, log)
    if mcfg.get("fundamentals", True):
        update_fundamentals(conn, log, mcfg)
    if mcfg.get("macro", True):
        update_macro(conn, settings, mcfg, log)
    if mcfg.get("holders", True):
        update_holders(conn, settings, mcfg, log)
