"""Run: python -m pytest tests  (or python tests/test_scoring.py)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portal.db import connect  # noqa: E402
from portal.ingest import run_source  # noqa: E402
from portal.extract import extract_pending, rules_extract  # noqa: E402
from portal.config import load_scorecard, load_sources, validate_scorecard  # noqa: E402
from portal.scoring import load_facts, score_facts, signals  # noqa: E402


def demo_signals(tmp, sc=None):
    db = tmp / "t.db"
    with connect(db) as conn:
        cfg = load_sources()
        run_source(conn, "demo", {"adapter": "demo"}, cfg.get("settings", {}), lambda m: None)
        extract_pending(conn, lambda m: None)
        sc = sc or load_scorecard()
        return signals(score_facts(load_facts(conn), sc), sc).set_index("symbol")


def test_scorecard_valid():
    assert validate_scorecard(load_scorecard()) == []


def test_rules_extract():
    ft = load_scorecard()["fact_types"]
    got = [f["fact_type"] for f in rules_extract("Resignation of statutory auditor", ft)]
    assert "auditor_resignation" in got


def test_demo_signals(tmp_path):
    s = demo_signals(tmp_path)
    assert s.loc["DEMOAGRO", "signal"] == "Bullish"
    assert s.loc["DEMOTECH", "signal"] == "Avoid"     # auditor resignation hard stop
    assert s.loc["DEMOINFRA", "signal"] == "Avoid"    # pledge + default


def test_rule_edit_changes_signal(tmp_path):
    sc = load_scorecard()
    sc["fact_types"]["auditor_resignation"]["hard_stop"] = False
    sc["fact_types"]["auditor_resignation"]["score"] = 0
    sc["fact_types"]["results_weak"]["score"] = 0
    sc["fact_types"]["promoter_sell"]["score"] = 0
    s = demo_signals(tmp_path, sc)
    assert s.loc["DEMOTECH", "signal"] == "Neutral"


def test_investor_list_edit_reclassifies_deal(tmp_path):
    sc = load_scorecard()
    sc["investor_lists"]["credible"] = []
    s = demo_signals(tmp_path, sc)
    assert s.loc["DEMOAGRO", "score"] < demo_signals(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path).loc["DEMOAGRO", "score"]


if __name__ == "__main__":
    import tempfile
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            with tempfile.TemporaryDirectory() as d:
                fn(Path(d)) if fn.__code__.co_argcount else fn()
            print("ok", name)
