"""Web-page and fund-portfolio adapters.

web_page     - watch pages that have no feed (fund letters, company IR pages, Chittorgarh
               IPO pages, compliance calendars). Each time a page's text changes, the new
               version is stored and Claude extracts facts from it. Honours robots.txt.
mf_portfolio - monthly mutual fund / PMS portfolio disclosures (Excel or CSV, from a file or
               URL). Compared month to month at scoring time: new entry, added, cut, exit.
"""
import hashlib
import io
import re
from datetime import date
from html import unescape
from pathlib import Path

import pandas as pd

from .. import ROOT
from .base import RawItem, SourceAdapter, register, sha256_bytes


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|nav|footer|header)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</tr>|</h\d>", "\n", html)
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


@register("web_page")
class WebPage(SourceAdapter):
    """Watch web pages for changes; each new version becomes an item."""
    source_type = "news"

    def fetch(self):
        from .generic import match_symbol
        symbols, aliases = self.match_universe()
        pages = self.cfg.get("pages") or ([{"url": self.cfg["url"]}] if self.cfg.get("url") else [])
        for page in pages:
            page = page if isinstance(page, dict) else {"url": page}
            url = page["url"]
            if not self.allowed(url):
                print(f"  skipped (robots.txt disallows): {url}")
                continue
            resp = self.get(url)
            ctype = resp.headers.get("content-type", "")
            if "pdf" in ctype or url.lower().endswith(".pdf"):
                try:
                    from pypdf import PdfReader
                    text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(resp.content)).pages[:40])
                except Exception:
                    text = ""
            else:
                text = html_to_text(resp.text)
            if page.get("contains") and page["contains"].lower() not in text.lower():
                continue
            h = sha256_bytes(text.encode("utf-8"))
            sym = (page.get("symbol") or match_symbol(text[:5000], symbols, aliases) or "").upper() or None
            title = page.get("title") or (re.search(r"(?is)<title>(.*?)</title>", resp.text or "") or [None, url])[1]
            yield RawItem(source=self.name, source_type=self.source_type,
                          external_id=f"{url}#{h[:16]}", symbol=sym, title=str(title).strip()[:300],
                          body=text[: int(self.cfg.get("max_chars", 40000))], source_url=url,
                          published_at=date.today().isoformat(), doc_hash=h,
                          raw={"url": url, "text_sha256": h, "length": len(text)})


@register("mf_portfolio")
class MFPortfolio(SourceAdapter):
    """Monthly fund portfolio disclosures (AMFI / fund house / PMS factsheets)."""
    source_type = "exchange_filing"

    def fetch(self):
        cols = self.cfg.get("columns", {})
        fund = self.cfg.get("fund_name", self.name)
        month = str(self.cfg.get("month") or date.today().strftime("%Y-%m"))
        for loc in self.cfg.get("files") or [self.cfg["path"]]:
            if str(loc).startswith("http"):
                data, url = self.get(loc).content, loc
            else:
                p = Path(loc) if Path(loc).is_absolute() else ROOT / loc
                data, url = p.read_bytes(), p.as_uri()
            if str(loc).lower().endswith((".xls", ".xlsx")):
                df = pd.read_excel(io.BytesIO(data), sheet_name=self.cfg.get("sheet", 0),
                                   header=int(self.cfg.get("header_row", 0)))
            else:
                df = pd.read_csv(io.BytesIO(data), header=int(self.cfg.get("header_row", 0)))
            df.columns = [str(c).strip() for c in df.columns]
            name_col, qty_col = cols.get("company", "Name of the Instrument"), cols.get("qty", "Quantity")
            isin_col, pct_col = cols.get("isin", "ISIN"), cols.get("pct", "% to Net Assets")
            map_ = {k.upper(): v for k, v in (self.cfg.get("symbol_map") or {}).items()}
            wl = {s.upper() for s in self.global_cfg.get("watchlist") or []}
            for _, r in df.iterrows():
                name = str(r.get(name_col, "")).strip()
                isin = str(r.get(isin_col, "")).strip().upper()
                if not name or name.lower() == "nan":
                    continue
                sym = map_.get(isin) or map_.get(name.upper())
                if not sym:
                    if self.cfg.get("only_mapped", True):
                        continue
                    sym = name.upper()
                if wl and sym not in wl:
                    continue
                qty, pct = str(r.get(qty_col, "")), str(r.get(pct_col, ""))
                title = f"{fund} holds {qty} shares of {name} ({pct}% of fund) in {month}"
                yield RawItem(source=self.name, source_type=self.source_type,
                              external_id=hashlib.md5(f"{fund}|{month}|{isin or name}".encode()).hexdigest(),
                              symbol=sym, company=name, title=title, body=title, source_url=url,
                              published_at=f"{month}-28" if len(month) == 7 else month,
                              raw={k: str(v) for k, v in r.items()},
                              facts=[{"fact_type": "_mf_holding", "summary": title, "evidence": title,
                                      "details": {"fund": fund, "qty": qty, "pct": pct, "month": month}}])
