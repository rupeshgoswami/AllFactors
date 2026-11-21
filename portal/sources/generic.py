"""Generic adapters: add a resource with config only, no code.

rss        - any RSS/Atom feed (news sites, blogs, YouTube channel feeds, forums).
csv_import - any CSV file or CSV URL, with a column mapping. Use it for BSE SME bulk
             deals downloaded by hand, a Trendlyne / Screener export, your own notes, etc.
"""
import csv
import hashlib
import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .. import ROOT
from .base import RawItem, SourceAdapter, register
from .nse import _parse_date, deal_facts


def _strip_html(s):
    return re.sub(r"<[^>]+>", " ", s or "").strip()


@register("rss")
class RSSFeed(SourceAdapter):
    source_type = "news"

    def fetch(self):
        url = self.cfg["url"]
        if not self.allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        root = ET.fromstring(self.get(url).content)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entries = root.findall(".//item") or root.findall(".//a:entry", ns)
        symbols, aliases = self.match_universe()
        for e in entries:
            def t(tag):
                el = e.find(tag) if e.find(tag) is not None else e.find("a:" + tag, ns)
                return (el.text or "") if el is not None else ""
            link = t("link")
            if not link:
                el = e.find("a:link", ns)
                link = el.get("href") if el is not None else ""
            title, body = t("title"), _strip_html(t("description") or t("summary"))
            sym = match_symbol(title + " " + body, symbols, aliases)
            if self.cfg.get("require_symbol_match", True) and not sym:
                continue
            yield RawItem(source=self.name, source_type=self.source_type,
                          external_id=t("guid") or t("id") or link or hashlib.md5(title.encode()).hexdigest(),
                          symbol=sym, title=title, body=body, source_url=link,
                          published_at=_parse_date(t("pubDate") or t("updated") or t("published")),
                          raw={"title": title, "link": link, "body": body})


_RX: dict = {}


def _rx(word):
    r = _RX.get(word)
    if r is None:
        r = _RX[word] = re.compile(r"\b" + re.escape(word.upper()) + r"\b")
    return r


def match_symbol(text, symbols, aliases):
    """Tag text with a stock. Company names (aliases) match anywhere; a bare ticker only
    matches for symbols in `symbols` (the watchlist) so that tickers that are ordinary
    words do not tag unrelated news."""
    up = text.upper()
    for sym, names in aliases.items():
        for n in names or []:
            if _rx(n).search(up):
                return sym.upper()
    for sym in symbols:
        if _rx(sym).search(up):
            return sym
    return None


@register("csv_import")
class CSVImport(SourceAdapter):
    """columns: maps our fields to your CSV's headers, e.g.
         columns: {symbol: "Scrip Code", date: "Deal Date", title: "Remarks"}
       kind: "deal" turns each row into a bulk-deal fact (needs client/side/qty/price)."""
    source_type = "manual"

    def fetch(self):
        loc = self.cfg["path"]
        if loc.startswith("http"):
            text = self.get(loc).text
            url = loc
        else:
            p = Path(loc) if Path(loc).is_absolute() else ROOT / loc
            text = p.read_text(encoding="utf-8-sig")
            url = p.as_uri()
        cols = self.cfg.get("columns", {})
        col = lambda r, k: (r.get(cols.get(k, k)) or "").strip()
        for i, r in enumerate(csv.DictReader(io.StringIO(text))):
            sym = col(r, "symbol").upper()
            title = col(r, "title")
            facts = []
            if self.cfg.get("kind") == "deal":
                client, side, qty, price = (col(r, k) for k in ("client", "side", "qty", "price"))
                title = title or f"Deal: {client} {side} {qty} @ ₹{price}"
                facts = deal_facts(client, side.upper(), qty, price, "bulk", self.global_cfg)
            yield RawItem(source=self.name, source_type=self.cfg.get("source_type", self.source_type),
                          external_id=col(r, "id") or hashlib.md5(repr(sorted(r.items())).encode()).hexdigest(),
                          symbol=sym, company=col(r, "company"), title=title,
                          body=col(r, "body") or title, source_url=col(r, "url") or url,
                          published_at=_parse_date(col(r, "date")), raw=r, facts=facts)
