"""The NSE Emerge (SME) universe: every listed SME symbol with its company name.
Source: NSE's official SME_EQUITY_L.csv, cached in data/nse_sme_symbols.csv for a day."""
import csv
import io
import re
import time

import requests

from . import DATA_DIR

SME_LIST_URL = "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"
CACHE = DATA_DIR / "nse_sme_symbols.csv"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
_SUFFIX = re.compile(r"\b(limited|ltd\.?|private|pvt\.?|india)\b", re.I)


def _text() -> str:
    fresh = CACHE.exists() and (time.time() - CACHE.stat().st_mtime) < 86400
    if not fresh:
        try:
            r = requests.get(SME_LIST_URL, headers=_UA, timeout=30)
            r.raise_for_status()
            DATA_DIR.mkdir(exist_ok=True)
            CACHE.write_text(r.text, encoding="utf-8")
        except Exception:
            pass
    return CACHE.read_text(encoding="utf-8") if CACHE.exists() else ""


def sme_companies() -> dict[str, str]:
    """{symbol: company name}. Rights entitlements / warrants (symbols with '-') are skipped:
    they have no price history of their own."""
    out = {}
    for r in csv.DictReader(io.StringIO(_text())):
        sym = (r.get("SYMBOL") or "").strip().upper()
        if sym and "-" not in sym:
            out[sym] = (r.get("NAME_OF_COMPANY") or "").strip()
    return out


def short_name(name: str) -> str:
    """'Vedant Poly Fabs Limited' -> 'Vedant Poly Fabs' (how news usually writes it)."""
    return re.sub(r"\s+", " ", _SUFFIX.sub("", name or "")).strip(" .,-")


def name_aliases() -> dict[str, list[str]]:
    """Aliases for matching news to SME stocks. Only multi-word names (or long single words)
    are used, so a company called 'Shine' does not match every sentence containing 'shine'."""
    out = {}
    for sym, name in sme_companies().items():
        s = short_name(name)
        if len(s.split()) >= 2 or len(s) >= 9:
            out[sym] = [s]
    return out
