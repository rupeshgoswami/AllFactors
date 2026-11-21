"""Every module in this folder is imported automatically, so a new adapter file
registers itself just by being here."""
import importlib
import pkgutil

from .base import REGISTRY, RawItem, SourceAdapter, register  # noqa: F401

for _m in pkgutil.iter_modules(__path__):
    if _m.name != "base":
        importlib.import_module(f"{__name__}.{_m.name}")
