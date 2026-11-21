import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from seed import seed  # noqa: E402
from portal import insights as ins  # noqa: E402
from portal.config import load_scorecard  # noqa: E402
from portal.db import connect  # noqa: E402
from portal.market import parse_shareholding_xbrl  # noqa: E402
from portal.scoring import load_facts, score_facts  # noqa: E402


def test_final_scorecard(tmp_path):
    db = seed(tmp_path / "t.db")
    sc = load_scorecard()
    with connect(db) as conn:
        scored = score_facts(load_facts(conn), sc)
        fin = ins.final_scorecard(conn, scored, sc).set_index("symbol")
        assert fin.loc["DEMOAGRO", "call"] == "Buy"
        assert fin.loc["DEMOTECH", "call"] == "Avoid"
        assert fin.loc["DEMOAGRO", "Valuation vs peers"] == 1        # P/E 18 vs peer median 28
        assert fin.loc["DEMOAGRO", "Macro"] == 1.0   # repo cut; US10Y fell 80 days ago, outside its 60-day rule
        h, gone = ins.holders(conn, "DEMOAGRO")
        assert set(h[h.change == "NEW"]["name"]) == {"New Capital AIF", "ASHISH KACHOLIA"}
        assert list(gone["name"]) == ["Mr X"]
        ne = ins.new_entrants(conn, scored, "DEMOAGRO")
        assert "New Capital AIF" in set(ne["investor"])
        pt, peers = ins.peers_table(conn, "DEMOAGRO", scored, sc)
        assert peers == ["DEMOPLAST", "DEMOINFRA"] and len(pt) == 3


def test_xbrl_parser():
    xml = b"""<xbrl xmlns:s="x"><s:NameOfTheShareholder contextRef="PublicShareholdersMoreThanOnePercent_1">Fund A</s:NameOfTheShareholder>
    <s:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="PublicShareholdersMoreThanOnePercent_1">0.0215</s:ShareholdingAsAPercentageOfTotalNumberOfShares>
    <s:NumberOfShares contextRef="PublicShareholdersMoreThanOnePercent_1">120000</s:NumberOfShares></xbrl>"""
    rows = parse_shareholding_xbrl(xml)
    assert rows[0]["name"] == "Fund A" and abs(rows[0]["pct"] - 2.15) < 1e-9 and rows[0]["shares"] == 120000


def test_tracked_investors(tmp_path):
    from portal import investors as invs
    db = seed(tmp_path / "t.db")
    sc = load_scorecard()
    with connect(db) as conn:
        assert invs.match("ASHISH KACHOLIA") == "Ashish Kacholia"
        assert invs.match("Mukul Mahavir Agrawal") == "Mukul Agrawal"
        assert invs.match("Fund A") is None
        ch = invs.holding_changes(conn).set_index(["symbol", "investor"])["event"]
        assert ch[("DEMOAGRO", "Ashish Kacholia")] == "NEW ENTRY"
        assert ch[("DEMOAGRO", "SBI Mutual Fund")] == "INCREASED"
        assert ch[("DEMOPLAST", "Mukul Agrawal")].startswith("EXITED")
        pf = invs.portfolio(conn, "Ashish Kacholia")
        assert list(pf["symbol"]) == ["DEMOAGRO"]
        pts, note = invs.tracked_score(conn, "DEMOAGRO", sc)
        assert pts == 3 and "Ashish Kacholia" in note          # new entry 2 + increase 1
        assert invs.tracked_score(conn, "DEMOPLAST", sc)[0] == -2
        scored = score_facts(load_facts(conn), sc)
        tracked, major, ev = invs.stock_view(conn, "DEMOPLAST", scored)
        assert major.iloc[0]["holder"] == "Promoter Person"
        assert "EXITED" in " ".join(ev["event"])
        assert len(invs.overview(conn)) == len(invs.load_investors())
