"""BSE bulk deals (includes BSE SME scrips). BSE publishes the day's bulk deals as an
HTML table; this reads the first table that has a 'Client Name' column."""
import io
from datetime import date

import pandas as pd

from .base import RawItem, SourceAdapter, register
from .nse import _parse_date, deal_facts

BSE_BULK = "https://www.bseindia.com/markets/equity/EQReports/bulk_deals.aspx"


@register("bse_bulk_deals")
class BSEBulkDeals(SourceAdapter):
    """BSE bulk deals page (HTML table). Filter to SME with watchlist or scrip_codes."""
    source_type = "exchange_deal"

    def fetch(self):
        url = self.cfg.get("url", BSE_BULK)
        html = self.get(url, headers={"Referer": "https://www.bseindia.com/"}).text
        tables = pd.read_html(io.StringIO(html))
        table = next((t for t in tables if any("client" in str(c).lower() for c in t.columns)), None)
        if table is None:
            raise ValueError("No bulk-deal table found; the page layout may have changed. "
                             "Download the CSV from BSE and use a csv_import source instead.")
        table.columns = [str(c).strip() for c in table.columns]
        col = lambda *names: next((c for c in table.columns if any(n in c.lower() for n in names)), None)
        c_code, c_name = col("security code", "scrip code"), col("security name", "scrip name")
        c_client, c_side = col("client"), col("deal type", "buy/sell", "type")
        c_qty, c_price, c_date = col("quantity"), col("price"), col("deal date", "date")
        codes = {str(c) for c in self.cfg.get("scrip_codes") or []}
        wl = self.watchlist()
        for _, r in table.iterrows():
            code = str(r.get(c_code, "")).split(".")[0]
            name = str(r.get(c_name, ""))
            if codes and code not in codes:
                continue
            if wl and code not in wl and name.upper() not in wl:
                continue
            client, side = str(r.get(c_client, "")), str(r.get(c_side, "")).upper()
            side = "BUY" if side.startswith(("B", "P")) else "SELL"
            qty, price = str(r.get(c_qty, "")), str(r.get(c_price, ""))
            d = _parse_date(str(r.get(c_date, ""))) or date.today().isoformat()
            title = f"BSE bulk deal: {client} {side} {qty} shares of {name} @ ₹{price}"
            yield RawItem(source=self.name, source_type=self.source_type,
                          external_id=f"{d}|{code}|{client}|{side}|{qty}|{price}",
                          symbol=code, company=name, title=title, body=title, source_url=url,
                          published_at=d, raw={k: str(v) for k, v in r.items()},
                          facts=deal_facts(client, side, qty, price, "bulk"))
