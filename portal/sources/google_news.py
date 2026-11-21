"""Per-stock and per-investor news from Google News RSS (free, no key).

One search per company (watchlist stocks first, then other SME stocks, `max_stocks` per run)
and, with `include_investors: true`, one per tracked investor in config/investors.yaml.
Each item is tagged with the stock it was searched for. Titles and links are the publishers'
own; open the link to read the story.
"""
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

from .. import DATA_DIR
from .base import RawItem, SourceAdapter, register

BASE = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"


@register("google_news")
class GoogleNews(SourceAdapter):
    source_type = "news"

    def _targets(self):
        """[(symbol or None, search name)] in the order they should be searched."""
        from ..universe import sme_companies, short_name
        comps = sme_companies()
        out, seen = [], set()
        for r in self.global_cfg.get("_watchlist_rows") or []:
            s = r["symbol"].upper()
            name = r.get("name") or short_name(comps.get(s, "")) or s
            out.append((s, name))
            seen.add(s)
        # rest of the SME universe, least recently searched first (state file keeps the order)
        import json
        state_file = DATA_DIR / "google_news_state.json"
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            state = {}
        rest = sorted((s for s in comps if s not in seen), key=lambda s: state.get(s, ""))
        n_rest = max(0, int(self.cfg.get("max_stocks", 40)) - len(out))
        now = datetime.now().isoformat(timespec="seconds")
        for s in rest[:n_rest]:
            out.append((s, short_name(comps[s])))
            state[s] = now
        if rest[:n_rest]:
            DATA_DIR.mkdir(exist_ok=True)
            state_file.write_text(json.dumps(state), encoding="utf-8")
        if self.cfg.get("include_investors", True):
            from ..investors import load_investors
            out += [(None, i["name"].split("(")[0].strip()) for i in load_investors() if i.get("name")]
        return out

    def fetch(self):
        days = int(self.cfg.get("lookback_days", 14))
        per = int(self.cfg.get("max_per_query", 8))
        for sym, name in self._targets():
            if not name:
                continue
            extra = " (NSE OR BSE OR shares OR stock)" if sym else " (stake OR shares OR portfolio OR invests)"
            q = quote_plus(f'"{name}"{extra} when:{days}d')
            try:
                root = ET.fromstring(self.get(BASE.format(q=q)).content)
            except Exception as e:
                print(f"  google_news {name}: {type(e).__name__}: {e}")
                continue
            for it in root.findall(".//item")[:per]:
                title = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                pub = it.findtext("pubDate")
                try:
                    pub = parsedate_to_datetime(pub).replace(tzinfo=None).isoformat() if pub else None
                except Exception:
                    pass
                publisher = (it.findtext("source") or "").strip()
                yield RawItem(source=self.name, source_type=self.source_type,
                              external_id=it.findtext("guid") or hashlib.md5(link.encode()).hexdigest(),
                              symbol=sym, company=name if sym else None,
                              title=f"{title}" + (f" [{publisher}]" if publisher and publisher not in title else ""),
                              body=f"{title}. Searched for: {name}", source_url=link, published_at=pub,
                              raw={"title": title, "link": link, "publisher": publisher, "query": name})
