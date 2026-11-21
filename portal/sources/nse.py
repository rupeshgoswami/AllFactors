"""NSE adapters (NSE Emerge / SME).

NSE's JSON APIs need a cookie from the home page and reject fast or heavy traffic.
These adapters fetch slowly, one request at a time. Endpoints are NSE's public
website APIs and can change without notice; if one breaks, check the network tab of
the matching nseindia.com page and update the URL in config/sources.yaml.
"""
import csv
import io
from datetime import date, datetime, timedelta

from .. import DATA_DIR
from .base import RawItem, SourceAdapter, register

NSE_HOME = "https://www.nseindia.com"


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def _parse_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d", "%d-%b-%y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            pass
    return s


class NSEBase(SourceAdapter):
    _primed = False

    def prime(self):
        if not self._primed:
            self.get(NSE_HOME, headers={"Accept": "text/html"})
            self._primed = True

    def api(self, url):
        self.prime()
        return self.get(url, headers={"Accept": "application/json",
                                      "Referer": NSE_HOME + "/"}).json()

    def date_range(self):
        days = int(self.cfg.get("lookback_days", 7))
        end = date.today()
        return end - timedelta(days=days), end

    def sme_symbols(self) -> set[str]:
        """NSE Emerge symbol list, cached for a day in data/."""
        cache = DATA_DIR / "nse_sme_symbols.csv"
        url = self.cfg.get("sme_list_url",
                           "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv")
        text = None
        if cache.exists() and (datetime.now().timestamp() - cache.stat().st_mtime) < 86400:
            text = cache.read_text(encoding="utf-8")
        else:
            try:
                text = self.get(url).text
                DATA_DIR.mkdir(exist_ok=True)
                cache.write_text(text, encoding="utf-8")
            except Exception:
                if cache.exists():
                    text = cache.read_text(encoding="utf-8")
        if not text:
            return set()
        rows = csv.DictReader(io.StringIO(text))
        return {(r.get("SYMBOL") or "").strip().upper() for r in rows if r.get("SYMBOL")}

    def keep(self, symbol: str, sme: set[str]) -> bool:
        symbol = (symbol or "").upper()
        wl = self.watchlist()
        if wl and symbol not in wl:
            return False
        if self.cfg.get("sme_only", True) and sme and symbol not in sme:
            return False
        return True


@register("nse_announcements")
class NSEAnnouncements(NSEBase):
    """Corporate announcements for the SME segment (index=sme)."""
    source_type = "exchange_filing"

    def fetch(self):
        start, end = self.date_range()
        url = self.cfg.get("url", NSE_HOME + "/api/corporate-announcements?index=sme"
                           "&from_date={from}&to_date={to}")
        url = url.replace("{from}", _ddmmyyyy(start)).replace("{to}", _ddmmyyyy(end))
        data = self.api(url)
        rows = data if isinstance(data, list) else data.get("data", [])
        wl = self.watchlist()
        for r in rows:
            sym = (r.get("symbol") or "").upper()
            if wl and sym not in wl:
                continue
            title = r.get("desc") or r.get("subject") or "Announcement"
            body = r.get("attchmntText") or ""
            yield RawItem(
                source=self.name, source_type=self.source_type,
                external_id=str(r.get("seq_id") or f"{sym}-{r.get('an_dt')}-{title}"),
                symbol=sym, company=r.get("sm_name"), title=title, body=body,
                source_url=r.get("attchmntFile") or f"{NSE_HOME}/get-quotes/equity?symbol={sym}",
                published_at=_parse_date(r.get("an_dt") or r.get("sort_date")), raw=r)


class NSEDealsBase(NSEBase):
    """NSE bulk.csv / block.csv list every trade over the threshold for the day."""
    source_type = "exchange_deal"
    default_url = ""
    deal_kind = "bulk"

    def fetch(self):
        url = self.cfg.get("url", self.default_url)
        text = self.get(url).text
        sme = self.sme_symbols()
        reader = csv.DictReader(io.StringIO(text))
        for r in reader:
            r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
            sym = r.get("Symbol", "").upper()
            if not sym or not self.keep(sym, sme):
                continue
            client = r.get("Client Name", "")
            side = r.get("Buy/Sell", "").upper()
            qty = r.get("Quantity Traded", "").replace(",", "")
            price = r.get("Trade Price / Wght. Avg. Price", "") or r.get("Trade Price", "")
            d = _parse_date(r.get("Date"))
            title = f"{self.deal_kind.title()} deal: {client} {side} {qty} shares of {sym} @ ₹{price}"
            yield RawItem(
                source=self.name, source_type=self.source_type,
                external_id=f"{r.get('Date')}|{sym}|{client}|{side}|{qty}|{price}",
                symbol=sym, company=r.get("Security Name"), title=title,
                body=title, source_url=url, published_at=d, raw=r,
                facts=deal_facts(client, side, qty, price, self.deal_kind, self.global_cfg))


@register("nse_bulk_deals")
class NSEBulkDeals(NSEDealsBase):
    default_url = "https://nsearchives.nseindia.com/content/equities/bulk.csv"
    deal_kind = "bulk"


@register("nse_block_deals")
class NSEBlockDeals(NSEDealsBase):
    default_url = "https://nsearchives.nseindia.com/content/equities/block.csv"
    deal_kind = "block"


@register("nse_insider_trading")
class NSEInsider(NSEBase):
    """SEBI PIT (insider trading) disclosures: promoter buys, sells and pledges."""
    source_type = "exchange_filing"

    def fetch(self):
        start, end = self.date_range()
        url = self.cfg.get("url", NSE_HOME + "/api/corporates-pit?index=equities"
                           "&from_date={from}&to_date={to}")
        url = url.replace("{from}", _ddmmyyyy(start)).replace("{to}", _ddmmyyyy(end))
        data = self.api(url)
        rows = data.get("data", []) if isinstance(data, dict) else data
        sme = self.sme_symbols()
        for r in rows:
            sym = (r.get("symbol") or "").upper()
            if not self.keep(sym, sme):
                continue
            who, cat = r.get("acqName", ""), r.get("personCategory", "")
            txn = (r.get("tdpTransactionType") or r.get("acqMode") or "").strip()
            qty, val = r.get("secAcq", ""), r.get("secVal", "")
            mode = r.get("acqMode", "")
            title = f"Insider: {who} ({cat}) {txn} {qty} shares of {sym} (₹{val}, {mode})"
            yield RawItem(
                source=self.name, source_type=self.source_type,
                external_id=str(r.get("did") or f"{sym}|{who}|{r.get('date')}|{qty}|{txn}"),
                symbol=sym, company=r.get("company"), title=title, body=title,
                source_url=r.get("xbrl") or f"{NSE_HOME}/companies-listing/corporate-filings-insider-trading",
                published_at=_parse_date(r.get("date") or r.get("acqfromDt")), raw=r,
                facts=insider_facts(cat, txn, mode, title))


@register("nse_shareholding")
class NSEShareholding(NSEBase):
    """Shareholding-pattern filings for SME. Compares promoter % with the previous filing
    stored in the database to detect stake changes."""
    source_type = "exchange_filing"

    def _sweep_symbols(self, skip: set) -> list:
        """Walk through every SME stock a few at a time (`sweep_per_run`, default 60; 0 = off),
        least recently visited first, so shareholding history for the whole universe builds up
        over successive runs without hammering NSE. Visit times live in data/shareholding_sweep.json."""
        import json
        n = int(self.cfg.get("sweep_per_run", 60))
        if n <= 0 or self.watchlist():
            return []
        state_file = DATA_DIR / "shareholding_sweep.json"
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            state = {}
        order = sorted((s for s in self.sme_symbols() if s not in skip and "-" not in s),
                       key=lambda s: state.get(s, ""))[:n]
        now = datetime.now().isoformat(timespec="seconds")
        for s in order:
            state[s] = now
        DATA_DIR.mkdir(exist_ok=True)
        state_file.write_text(json.dumps(state), encoding="utf-8")
        return order

    def fetch(self):
        url = self.cfg.get("url", NSE_HOME + "/api/corporate-share-holdings-master?index=sme")
        sym_url = self.cfg.get("symbol_url", NSE_HOME + "/api/corporate-share-holdings-master"
                               "?index=equities&symbol={symbol}")
        wl_syms = [r["symbol"] for r in self.global_cfg.get("_watchlist_rows") or []   # full history for
                   if (r.get("exchange") or "NSE").upper() == "NSE"]                  # watchlist stocks
        sweep = self._sweep_symbols(set(wl_syms))
        # SME-segment stocks are only found with index=sme; mainboard ones (some watchlist
        # stocks have graduated) with index=equities. Watchlist stocks try both.
        sme_url = sym_url.replace("index=equities", "index=sme")
        urls = [url]
        for s in wl_syms:
            urls += [sme_url.replace("{symbol}", s), sym_url.replace("{symbol}", s)]
        urls += [sme_url.replace("{symbol}", s) for s in sweep]
        wl = self.watchlist()
        seen = set()
        for u in urls:
            try:
                data = self.api(u)
            except Exception as e:
                print(f"  {u}: {e}")
                continue
            rows = data if isinstance(data, list) else data.get("data", [])
            for r in rows:
                sym = (r.get("symbol") or "").upper()
                if wl and sym not in wl:
                    continue
                prom = r.get("pr_and_prgrp")
                asof = r.get("date")
                key = (sym, asof)
                if key in seen:
                    continue
                seen.add(key)
                title = f"Shareholding pattern as of {asof}: promoter & group {prom}%"
                yield RawItem(
                    source=self.name, source_type=self.source_type,
                    external_id=f"{sym}|{asof}|{r.get('submissionDate', '')}",
                    symbol=sym, company=r.get("name"), title=title, body=title,
                    source_url=r.get("xbrl") or f"{NSE_HOME}/companies-listing/corporate-filings-shareholding-pattern",
                    published_at=_parse_date(r.get("submissionDate") or asof),
                    raw=r, facts=[{"fact_type": "_shareholding_snapshot",
                                   "summary": title,
                                   "details": {"promoter_pct": prom, "public_pct": r.get("public_val"),
                                               "as_of": asof}}])


@register("nse_sast")
class NSESAST(NSEBase):
    """SEBI SAST Reg 29 disclosures: someone crosses 5% or moves 2%+ in a company."""
    source_type = "exchange_filing"

    def fetch(self):
        start, end = self.date_range()
        url = self.cfg.get("url", NSE_HOME + "/api/corporate-sast-reg29?index=equities"
                           "&from_date={from}&to_date={to}")
        url = url.replace("{from}", _ddmmyyyy(start)).replace("{to}", _ddmmyyyy(end))
        data = self.api(url)
        rows = data.get("data", []) if isinstance(data, dict) else data
        sme = self.sme_symbols()
        for r in rows:
            sym = (r.get("symbol") or "").upper()
            if not self.keep(sym, sme):
                continue
            who = r.get("acquirerName") or r.get("acqName") or ""
            sale = (r.get("acqSaleType") or r.get("acquisitionType") or "").lower()
            after = r.get("totAcqShare") or r.get("afterAcqSharesPer") or ""
            side = "SELL" if ("sale" in sale or "dispos" in sale) else "BUY"
            title = f"SAST Reg 29: {who} {'sold' if side == 'SELL' else 'acquired'}; holding now {after}% of {sym}"
            facts = deal_facts(who, side, r.get("noOfShareAcq") or r.get("secAcq") or "", "", "sast")
            if r.get("promoterType", "").lower().startswith("promoter"):
                facts = [{"fact_type": "promoter_stake_increase" if side == "BUY" else "promoter_sell",
                          "summary": title, "evidence": title, "details": r}]
            yield RawItem(source=self.name, source_type=self.source_type,
                          external_id=str(r.get("id") or f"{sym}|{who}|{r.get('timestamp') or r.get('date')}|{after}"),
                          symbol=sym, company=r.get("company"), title=title, body=title,
                          source_url=r.get("attachement") or r.get("xbrl") or
                          f"{NSE_HOME}/companies-listing/corporate-filings-regulation-29",
                          published_at=_parse_date(r.get("timestamp") or r.get("date")), raw=r, facts=facts)


@register("nse_event_calendar")
class NSEEventCalendar(NSEBase):
    """Upcoming board meetings (results dates, fund raising) for SME companies."""
    source_type = "exchange_filing"

    def fetch(self):
        url = self.cfg.get("url", NSE_HOME + "/api/event-calendar?index=sme")
        data = self.api(url)
        rows = data if isinstance(data, list) else data.get("data", [])
        wl = self.watchlist()
        for r in rows:
            sym = (r.get("symbol") or "").upper()
            if wl and sym not in wl:
                continue
            purpose = r.get("purpose") or r.get("bm_purpose") or "Board meeting"
            when = r.get("date") or r.get("bm_date")
            title = f"Upcoming {when}: {purpose}"
            yield RawItem(source=self.name, source_type=self.source_type,
                          external_id=f"{sym}|{when}|{purpose}"[:250], symbol=sym,
                          company=r.get("company") or r.get("sm_name"), title=title,
                          body=(r.get("bm_desc") or title),
                          source_url=f"{NSE_HOME}/companies-listing/corporate-filings-event-calendar",
                          published_at=_parse_date(when), raw=r,
                          facts=[{"fact_type": "upcoming_event", "summary": title, "evidence": title}])


# ---------- rule helpers shared with other deal / insider adapters ----------

def deal_facts(client, side, qty, price, kind, _unused=None):
    """Deals are stored as a neutral `_deal` fact. Whether the buyer is credible,
    unknown or flagged is decided at scoring time from investor_lists in
    scorecard.yaml, so editing your lists re-scores old deals immediately."""
    buy = side.upper().startswith("B")
    verb = "bought" if buy else "sold"
    return [{"fact_type": "_deal",
             "summary": f"{client} {verb} {qty} shares @ ₹{price} ({kind} deal)",
             "evidence": f"{client} | {side} | {qty} | {price}",
             "details": {"client": client, "side": "BUY" if buy else "SELL",
                         "qty": qty, "price": price, "kind": kind}}]


def insider_facts(category, txn, mode, title):
    cat, t, m = category.lower(), txn.lower(), mode.lower()
    promoter = "promoter" in cat
    if "pledge" in t or "pledge" in m:
        ft = "pledge_released" if ("revoc" in t or "release" in t or "revoc" in m) else "promoter_pledge"
    elif not promoter:
        ft = "insider_buy" if "buy" in t or "acqui" in t else "insider_sell"
    elif "buy" in t or "acqui" in t:
        ft = "promoter_open_market_buy" if "market" in m else "promoter_stake_increase"
    elif "sell" in t or "dispos" in t:
        ft = "promoter_sell"
    else:
        return []
    return [{"fact_type": ft, "summary": title, "evidence": title,
             "details": {"category": category, "txn": txn, "mode": mode}}]
