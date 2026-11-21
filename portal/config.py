"""Load and validate the two YAML config files."""
import shutil
from datetime import datetime

import yaml

from . import CONFIG_DIR

SCORECARD = CONFIG_DIR / "scorecard.yaml"
SOURCES = CONFIG_DIR / "sources.yaml"


def load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_scorecard():
    return load(SCORECARD)


def load_sources():
    return load(SOURCES)


def validate_scorecard(sc: dict) -> list[str]:
    errs = []
    for key in ("source_reliability", "decay", "signals", "fact_types"):
        if key not in sc:
            errs.append(f"missing section: {key}")
    for name, ft in (sc.get("fact_types") or {}).items():
        if not isinstance(ft, dict) or "score" not in ft:
            errs.append(f"fact type {name}: needs a score")
            continue
        try:
            s = float(ft["score"])
            if not -5 <= s <= 5:
                errs.append(f"fact type {name}: score {s} is outside -5..5")
        except (TypeError, ValueError):
            errs.append(f"fact type {name}: score must be a number")
    sig = sc.get("signals") or {}
    if sig and float(sig.get("bullish_at", 0)) <= float(sig.get("bearish_at", 0)):
        errs.append("signals: bullish_at must be greater than bearish_at")
    return errs


def validate_sources(cfg: dict) -> list[str]:
    from .sources import REGISTRY
    errs = []
    for name, s in (cfg.get("sources") or {}).items():
        if not isinstance(s, dict) or s.get("adapter") not in REGISTRY:
            errs.append(f"source {name}: unknown adapter {s.get('adapter') if isinstance(s, dict) else s!r}. "
                        f"Known: {', '.join(sorted(REGISTRY))}")
    return errs


def save_yaml_text(path, text: str):
    """Back up the current file to config/backups/ and write the new text."""
    backups = path.parent / "backups"
    backups.mkdir(exist_ok=True)
    if path.exists():
        shutil.copy(path, backups / f"{path.stem}-{datetime.now():%Y%m%d-%H%M%S}.yaml")
    path.write_text(text, encoding="utf-8")


def dump(data) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
