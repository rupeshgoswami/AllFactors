"""BSE adapters. BSE's announcement API does not filter to the SME segment, so set a
watchlist or `scrip_codes` in config/sources.yaml to keep the volume down."""
from datetime import date, timedelta

from .base import RawItem, SourceAdapter, register
from .nse import _parse_date

BSE_API = ("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno={page}"
           "&strCat=-1&strPrevDate={from}&strScrip={scrip}&strSearch=P&strToDate={to}"
           "&strType=C&subcategory=-1")
ATTACH = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/{name}"


@register("bse_announcements")
class BSEAnnouncements(SourceAdapter):
    source_type = "exchange_filing"

    def fetch(self):
        days = int(self.cfg.get("lookback_days", 7))
        end = date.today()
        start = end - timedelta(days=days)
        headers = {"Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com",
                   "Accept": "application/json"}
        scrips = [str(s) for s in self.cfg.get("scrip_codes") or []] or [""]
        max_pages = int(self.cfg.get("max_pages", 5))
        wl = self.watchlist()
        for scrip in scrips:
            for page in range(1, max_pages + 1):
                url = (self.cfg.get("url", BSE_API)
                       .replace("{page}", str(page)).replace("{scrip}", scrip)
                       .replace("{from}", start.strftime("%Y%m%d"))
                       .replace("{to}", end.strftime("%Y%m%d")))
                rows = self.get(url, headers=headers).json().get("Table", [])
                if not rows:
                    break
                for r in rows:
                    sym = (r.get("SCRIP_CD") and str(r["SCRIP_CD"])) or ""
                    name = r.get("SLONGNAME") or ""
                    if wl and sym not in wl and name.upper() not in wl:
                        continue
                    att = r.get("ATTACHMENTNAME")
                    yield RawItem(
                        source=self.name, source_type=self.source_type,
                        external_id=str(r.get("NEWSID")),
                        symbol=sym, company=name,
                        title=r.get("NEWSSUB") or r.get("HEADLINE") or "Announcement",
                        body=r.get("HEADLINE") or "",
                        source_url=ATTACH.format(name=att) if att else r.get("NSURL"),
                        published_at=_parse_date((r.get("NEWS_DT") or "").replace("T", " ")[:19]),
                        raw=r)
