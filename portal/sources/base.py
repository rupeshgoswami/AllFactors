"""Source adapter interface.

To add a new resource:
  1. Create a file in portal/sources/ with a class that subclasses SourceAdapter
     and is decorated with @register("my_source").
  2. Implement fetch(), yielding RawItem objects.
  3. Add an entry under `sources:` in config/sources.yaml with `adapter: my_source`.
Facts that come with clear structure (e.g. bulk deals) can be attached directly to the
item via RawItem.facts, skipping the AI extraction step.
"""
import hashlib
import json
import time
import urllib.robotparser
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from typing import Iterator

import requests

REGISTRY: dict[str, type["SourceAdapter"]] = {}


def register(name):
    def deco(cls):
        cls.adapter_name = name
        REGISTRY[name] = cls
        return cls
    return deco


@dataclass
class RawItem:
    source: str
    source_type: str
    external_id: str
    title: str
    body: str = ""
    symbol: str | None = None
    company: str | None = None
    source_url: str | None = None
    published_at: str | None = None    # ISO date or datetime
    raw: dict = field(default_factory=dict)
    doc_hash: str | None = None
    # Pre-structured facts: list of dicts with fact_type, summary, evidence, confidence, details
    facts: list = field(default_factory=list)

    def content_hash(self) -> str:
        payload = json.dumps(self.raw or {"title": self.title, "body": self.body,
                                          "url": self.source_url},
                             sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SourceAdapter:
    adapter_name = "base"
    source_type = "exchange_filing"
    USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

    def __init__(self, name: str, cfg: dict, global_cfg: dict):
        self.name = name
        self.cfg = cfg
        self.global_cfg = global_cfg
        self.source_type = cfg.get("source_type", self.source_type)
        self.delay = float(cfg.get("request_delay_seconds",
                                   global_cfg.get("request_delay_seconds", 3)))
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT,
                                     "Accept-Language": "en-US,en;q=0.9"})
        self._last = 0.0

    # Polite fetching: one request at a time with a delay between them.
    def get(self, url, **kw):
        wait = self.delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        kw.setdefault("timeout", 30)
        resp = self.session.get(url, **kw)
        self._last = time.time()
        resp.raise_for_status()
        return resp

    _robots: dict = {}

    def allowed(self, url) -> bool:
        """robots.txt check, used by the generic web adapters. Set respect_robots: false
        on a source only if you have checked the site's terms yourself."""
        if not self.cfg.get("respect_robots", True):
            return True
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        rp = self._robots.get(base)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                txt = self.session.get(base + "/robots.txt", timeout=15)
                rp.parse(txt.text.splitlines() if txt.status_code == 200 else [])
            except Exception:
                rp.parse([])
            self._robots[base] = rp
        return rp.can_fetch(self.USER_AGENT, url) and rp.can_fetch("*", url)

    def watchlist(self) -> set[str]:
        """Optional filter from settings.watchlist in sources.yaml (empty = no filter)."""
        return {s.upper() for s in self.global_cfg.get("watchlist") or []}

    def match_universe(self):
        """Symbols and company names used to tag news / forum posts with a stock:
        settings.watchlist + company_aliases from sources.yaml, plus the Watchlist page."""
        symbols = {s.upper() for s in self.global_cfg.get("watchlist") or []}
        aliases = {}
        if self.global_cfg.get("match_all_sme", True):
            from ..universe import name_aliases        # every NSE SME company name
            aliases = {k: list(v) for k, v in name_aliases().items()}
        for k, v in (self.global_cfg.get("company_aliases") or {}).items():
            aliases[k.upper()] = list(aliases.get(k.upper(), [])) + list(v or [])
        for row in self.global_cfg.get("_watchlist_rows") or []:
            symbols.add(row["symbol"].upper())
            names = [n.strip() for n in (row.get("aliases") or "").split(",") if n.strip()]
            if row.get("name"):
                names.append(row["name"])
            aliases.setdefault(row["symbol"].upper(), [])
            aliases[row["symbol"].upper()] = list(aliases[row["symbol"].upper()]) + names
        return symbols, aliases

    def fetch(self) -> Iterator[RawItem]:
        raise NotImplementedError


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
