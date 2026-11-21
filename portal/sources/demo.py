"""Demo data for trying the portal without network access. All companies, people and
funds here are FICTIONAL. Disable this source in config/sources.yaml once live sources work."""
from datetime import date, timedelta

from .base import RawItem, SourceAdapter, register
from .nse import deal_facts, insider_facts

D = lambda n: (date.today() - timedelta(days=n)).isoformat()

ANNOUNCEMENTS = [
    ("DEMOAGRO", "Demo Agro Foods Ltd", 3, "Receipt of order worth Rs 48 crore",
     "The company has received a purchase order worth Rs 48 crore from a state government agency for supply over 12 months. The order is about 60% of FY26 revenue."),
    ("DEMOAGRO", "Demo Agro Foods Ltd", 40, "Half-yearly results H1 FY27",
     "Revenue grew 34% YoY to Rs 81 crore. EBITDA margin expanded to 14.2% from 11.0%. PAT up 52% YoY. Operating cash flow positive."),
    ("DEMOAGRO", "Demo Agro Foods Ltd", 20, "Board approves application for migration to NSE main board",
     "The board approved filing an application for migration of equity shares from NSE Emerge to the main board."),
    ("DEMOTECH", "Demo Techsol Ltd", 5, "Resignation of statutory auditor",
     "M/s ABC & Associates have resigned as statutory auditors citing non-receipt of information and explanations required for the audit."),
    ("DEMOTECH", "Demo Techsol Ltd", 30, "Half-yearly results H1 FY27",
     "Revenue declined 18% YoY. The company reported a net loss. Trade receivables increased sharply."),
    ("DEMOPLAST", "Demo Polyplast Ltd", 10, "Expansion of manufacturing capacity",
     "The board approved capex of Rs 22 crore to expand capacity by 40% at the existing plant, funded through internal accruals."),
    ("DEMOPLAST", "Demo Polyplast Ltd", 25, "Intimation of board meeting",
     "A meeting of the board will be held to consider and approve routine business."),
    ("DEMOINFRA", "Demo Infra Projects Ltd", 12, "Credit rating downgrade",
     "CARE has downgraded the long-term bank facilities rating to BB from BBB- on account of stretched liquidity and delays in debt servicing."),
    ("DEMOINFRA", "Demo Infra Projects Ltd", 50, "Lock-in expiry of anchor investor shares",
     "Lock-in on shares allotted to anchor investors ends on the 90th day after allotment."),
]

DEALS = [  # symbol, company, days ago, client, side, qty, price
    ("DEMOAGRO", "Demo Agro Foods Ltd", 8, "Example Small Cap Fund AIF", "BUY", "312000", "214.50"),
    ("DEMOPLAST", "Demo Polyplast Ltd", 15, "Random Trading LLP", "BUY", "150000", "88.00"),
    ("DEMOPLAST", "Demo Polyplast Ltd", 60, "Example Value PMS", "BUY", "200000", "71.20"),
    ("DEMOINFRA", "Demo Infra Projects Ltd", 6, "Example Small Cap Fund AIF", "SELL", "400000", "142.00"),
]

INSIDER = [  # symbol, company, days ago, person, category, txn, mode, qty
    ("DEMOAGRO", "Demo Agro Foods Ltd", 14, "R. Sample", "Promoter", "Buy", "Market Purchase", "50000"),
    ("DEMOINFRA", "Demo Infra Projects Ltd", 9, "K. Example", "Promoter Group", "Pledge", "Pledge Creation", "1200000"),
    ("DEMOTECH", "Demo Techsol Ltd", 18, "P. Fictional", "Promoter", "Sell", "Market Sale", "300000"),
]


@register("demo")
class Demo(SourceAdapter):
    source_type = "exchange_filing"

    def fetch(self):
        for i, (sym, co, d, title, body) in enumerate(ANNOUNCEMENTS):
            yield RawItem(source=self.name, source_type="exchange_filing", external_id=f"ann{i}",
                          symbol=sym, company=co, title=title, body=body,
                          source_url=f"https://example.com/demo/{sym}/announcement-{i}.pdf",
                          published_at=D(d), raw={"symbol": sym, "title": title, "body": body, "d": d})
        for i, (sym, co, d, client, side, qty, price) in enumerate(DEALS):
            title = f"Bulk deal: {client} {side} {qty} shares of {sym} @ ₹{price}"
            yield RawItem(source=self.name, source_type="exchange_deal", external_id=f"deal{i}",
                          symbol=sym, company=co, title=title, body=title,
                          source_url="https://example.com/demo/bulk.csv", published_at=D(d),
                          raw={"client": client, "side": side, "qty": qty, "price": price, "d": d},
                          facts=deal_facts(client, side, qty, price, "bulk", self.global_cfg))
        for i, (sym, co, d, who, cat, txn, mode, qty) in enumerate(INSIDER):
            title = f"Insider: {who} ({cat}) {txn} {qty} shares of {sym} ({mode})"
            yield RawItem(source=self.name, source_type="exchange_filing", external_id=f"pit{i}",
                          symbol=sym, company=co, title=title, body=title,
                          source_url="https://example.com/demo/pit.xml", published_at=D(d),
                          raw={"who": who, "cat": cat, "txn": txn, "mode": mode, "qty": qty, "d": d},
                          facts=insider_facts(cat, txn, mode, title))
