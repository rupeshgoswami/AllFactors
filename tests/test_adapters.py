"""Offline tests of the live adapters' parsing, using canned responses."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portal.sources import REGISTRY  # noqa: E402

BULK = """Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price,Remarks
21-Nov-2026,ABCSME,ABC SME Ltd,SOME FUND,BUY,"1,20,000",101.50,-
21-Nov-2026,BIGCO,Big Co Ltd,X,SELL,500000,900,-
"""
SME = "SYMBOL,NAME OF COMPANY\nABCSME,ABC SME Ltd\n"


def fake(adapter, text_by_url):
    def get(url, **kw):
        for k, v in text_by_url.items():
            if k in url:
                return SimpleNamespace(text=v, content=str(v).encode(), json=lambda v=v: v)
        raise AssertionError(url)
    adapter.get = get
    return adapter


def test_nse_bulk_filters_to_sme(tmp_path, monkeypatch):
    import portal.sources.nse as nse
    monkeypatch.setattr(nse, "DATA_DIR", tmp_path)
    a = fake(REGISTRY["nse_bulk_deals"]("nse_bulk", {}, {}), {"bulk.csv": BULK, "SME_EQUITY_L": SME})
    items = list(a.fetch())
    assert [i.symbol for i in items] == ["ABCSME"]
    assert items[0].facts[0]["fact_type"] == "_deal"
    assert items[0].facts[0]["details"]["side"] == "BUY"


def test_nse_insider_pledge(tmp_path, monkeypatch):
    import portal.sources.nse as nse
    monkeypatch.setattr(nse, "DATA_DIR", tmp_path)
    rows = {"data": [{"symbol": "ABCSME", "acqName": "P", "personCategory": "Promoters",
                      "tdpTransactionType": "Pledge", "acqMode": "Pledge Creation",
                      "secAcq": "100", "secVal": "1", "date": "20-Nov-2026 10:00:00"}]}
    a = fake(REGISTRY["nse_insider_trading"]("pit", {}, {}),
             {"corporates-pit": rows, "SME_EQUITY_L": SME, "nseindia.com": ""})
    a._primed = True
    items = list(a.fetch())
    assert items[0].facts[0]["fact_type"] == "promoter_pledge"


def test_rss_matches_alias():
    rss = """<rss><channel><item><title>ABC Foods wins big order</title><link>https://x/1</link>
             <description>text</description><pubDate>Fri, 20 Nov 2026 10:00:00 +0530</pubDate></item>
             <item><title>Unrelated</title><link>https://x/2</link></item></channel></rss>"""
    a = fake(REGISTRY["rss"]("news", {"url": "https://x/feed"},
                             {"company_aliases": {"ABCSME": ["ABC Foods"]}}), {"x/feed": rss})
    items = list(a.fetch())
    assert len(items) == 1 and items[0].symbol == "ABCSME"


def test_bse_bulk_html():
    html = """<table><tr><th>Deal Date</th><th>Security Code</th><th>Security Name</th><th>Client Name</th>
      <th>Deal Type *</th><th>Quantity</th><th>Price **</th></tr>
      <tr><td>21/11/2026</td><td>543210</td><td>ABC SME LTD</td><td>SOME FUND</td><td>B</td><td>120000</td><td>55.1</td></tr></table>"""
    a = fake(REGISTRY["bse_bulk_deals"]("bse", {"scrip_codes": [543210]}, {}), {"bulk_deals": html})
    items = list(a.fetch())
    assert len(items) == 1 and items[0].facts[0]["details"]["side"] == "BUY"


def test_web_page_versions():
    a = fake(REGISTRY["web_page"]("w", {"pages": [{"url": "https://f/letter", "symbol": "ABC"}]}, {}),
             {"f/letter": "<html><title>Q2 letter</title><p>We bought ABC SME</p></html>"})
    a.allowed = lambda url: True
    a.get.__defaults__  # noqa
    orig = a.get
    a.get = lambda url, **kw: SimpleNamespace(headers={"content-type": "text/html"}, **vars(orig(url)))
    items = list(a.fetch())
    assert items[0].symbol == "ABC" and "bought ABC" in items[0].body and items[0].doc_hash


def test_mf_portfolio_and_scoring(tmp_path):
    from portal.config import load_scorecard
    from portal.db import connect
    from portal.extract import store_structured
    from portal.db import insert_item
    from portal.scoring import load_facts, score_facts
    rows = []
    for month, qty in [("2026-08", None), ("2026-09", 1000), ("2026-10", 2000)]:
        f = tmp_path / f"{month}.csv"
        lines = ["Name of the Instrument,ISIN,Quantity,% to Net Assets", "Other Co,INE999,10,1"]
        if qty:
            lines.append(f"ABC SME Ltd,INE123,{qty},0.5")
        f.write_text("\n".join(lines))
        a = REGISTRY["mf_portfolio"]("mf", {"fund_name": "F", "month": month, "files": [str(f)],
                                            "symbol_map": {"INE123": "ABCSME", "INE999": "OTHER"}}, {})
        rows += list(a.fetch())
    with connect(tmp_path / "t.db") as conn:
        for it in rows:
            rid = insert_item(conn, it, "2026-11-01T00:00:00", it.content_hash())
            store_structured(conn, rid, it.symbol, it.facts)
        s = score_facts(load_facts(conn), load_scorecard())
    abc = s[s.symbol == "ABCSME"].sort_values("when")["fact_type"].tolist()
    assert abc == ["mf_new_entry", "mf_added"]
