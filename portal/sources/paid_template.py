"""TEMPLATE for a paid or login-only source (Trendlyne, Screener Premium, Tijori,
Researchbytes, Morningstar, a data vendor API ...).

Nothing here runs until you fill in the TODO(YOU) parts and enable a source that uses
`adapter: paid_template` in config/sources.yaml. Read the provider's terms first: use
their official API or export where one exists, and only data your plan allows.

Steps:
  1. Copy this file to portal/sources/<provider>.py, and change the register name below,
     e.g. @register("trendlyne").
  2. Put secrets in environment variables, never in the code or YAML:
         Windows:      setx TRENDLYNE_API_KEY "your-key"     (then open a new window)
         macOS/Linux:  export TRENDLYNE_API_KEY=your-key
     and name the variable in sources.yaml with `api_key_env:` (or `cookie_env:`).
  3. Fill in the TODO(YOU) blocks: request, where the rows are, and the field mapping.
  4. In sources.yaml set `adapter: <provider>` and `enabled: true` on your entry.
  5. Test it alone:  python ingest.py --source <entry name>
"""
import os

from .base import RawItem, SourceAdapter, register


@register("paid_template")          # TODO(YOU): rename, e.g. @register("trendlyne")
class PaidTemplate(SourceAdapter):
    source_type = "news"            # TODO(YOU): exchange_filing / exchange_deal / news / forum / manual

    def fetch(self):
        # ---- credentials (from environment variables named in sources.yaml) ----
        key_env = self.cfg.get("api_key_env")
        cookie_env = self.cfg.get("cookie_env")
        api_key = os.environ.get(key_env, "") if key_env else ""
        cookie = os.environ.get(cookie_env, "") if cookie_env else ""
        if not (api_key or cookie):
            raise RuntimeError(f"{self.name}: set the environment variable "
                               f"{key_env or cookie_env or '(api_key_env / cookie_env in sources.yaml)'}")

        # ---- TODO(YOU): build the request. Endpoint goes in sources.yaml as `url:` ----
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"   # TODO(YOU): provider's auth header
        if cookie:
            headers["Cookie"] = cookie                       # session cookie copied from your browser
        resp = self.get(self.cfg["url"], headers=headers)

        # ---- TODO(YOU): where are the rows in the response? ----
        rows = resp.json()          # e.g. resp.json()["data"]["results"]; for CSV use csv.DictReader

        # ---- TODO(YOU): field mapping. Left side = ours, right side = provider's key ----
        fields = self.cfg.get("fields") or {
            "id": "id", "symbol": "symbol", "company": "company_name",
            "title": "headline", "body": "text", "url": "link", "date": "date",
        }
        for r in rows:
            g = lambda k: str(r.get(fields.get(k, k), "") or "")
            yield RawItem(source=self.name, source_type=self.cfg.get("source_type", self.source_type),
                          external_id=g("id") or g("url") or g("title"),
                          symbol=g("symbol").upper() or None, company=g("company"),
                          title=g("title"), body=g("body"), source_url=g("url"),
                          published_at=g("date") or None, raw=r)
            # If a row is already a clear fact (e.g. "superstar X bought Y"), add
            #   facts=[{"fact_type": "credible_investor_buy", "summary": ..., "evidence": ...}]
            # to the RawItem above so it skips AI extraction.
