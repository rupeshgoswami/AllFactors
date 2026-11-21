"""SME signal portal: ingest -> extract facts -> score -> signal -> dashboard."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"


def load_api_keys():
    """Export non-empty values from config/api_keys.yaml as environment variables.
    A variable already set in the real environment is never overwritten."""
    import os
    path = CONFIG_DIR / "api_keys.yaml"
    if not path.exists():
        return
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return
    for k, v in data.items():
        if v and str(v).strip() and not os.environ.get(str(k)):
            os.environ[str(k)] = str(v).strip()


load_api_keys()
