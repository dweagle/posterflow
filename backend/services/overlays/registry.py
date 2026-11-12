"""Discovers overlay types: every module in services/overlays/types/ that defines an
OverlayType subclass with a `key` is registered. Adding a new overlay = adding a file."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Dict, List, Optional

from core.logging import LogTags, log_warning

from .base import OverlayType

_REGISTRY: Optional[Dict[str, OverlayType]] = None


def _discover() -> Dict[str, OverlayType]:
    from . import types as types_pkg

    found: Dict[str, OverlayType] = {}
    for info in pkgutil.iter_modules(types_pkg.__path__):
        if info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{types_pkg.__name__}.{info.name}")
        except Exception as exc:
            log_warning(LogTags.OVERLAYS, f"Overlay type module '{info.name}' failed to import: {exc}")
            continue
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls is OverlayType or not issubclass(cls, OverlayType) or cls.__module__ != module.__name__:
                continue
            if not cls.key:
                continue
            if cls.key in found:
                log_warning(LogTags.OVERLAYS, f"Duplicate overlay type key '{cls.key}' in {info.name}; keeping first")
                continue
            found[cls.key] = cls()
    return dict(sorted(found.items(), key=lambda kv: (kv[1].order, kv[0])))


def all_types() -> Dict[str, OverlayType]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _discover()
    return _REGISTRY


def get_type(key: str) -> Optional[OverlayType]:
    return all_types().get(key)


def describe_types() -> List[dict]:
    return [t.describe() for t in all_types().values()]
