"""Overlay settings: the `overlays_config` setting (master switch, libraries, per-type config).

Each type holds named `overlays`: every one applies to all selected libraries or to the
libraries ("instance:key") it names, with its own placement and options. An item takes the
overlay naming its library, else one set to all libraries; none = no badge of that type."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from core.logging import LogTags, log_warning
from models.setting import get_setting_value, upsert_setting

from .base import normalize_placement
from .registry import all_types

SETTING_OVERLAYS_CONFIG = "overlays_config"
SETTING_TMDB_API_KEY = "tmdb_api_key"


def default_config() -> Dict[str, Any]:
    return {
        "enabled": False,
        # all_libraries = every enabled movie/show library (including ones enabled later);
        # otherwise only the "<instance name>:<library key>" entries in `libraries`.
        "all_libraries": True,
        "libraries": [],
        # Also badge items Posterflow has no poster file for, on the media server's own poster.
        "badge_server_posters": False,
        # After a badge goes up, remove the superseded uploads from the item's Plex bundle
        # (needs the Plex Metadata folder from the Tools page).
        "prune_plex_uploads": True,
        "types": {
            key: {"enabled": False, "overlays": []}
            for key, t in all_types().items()
        },
    }


def normalize_config(raw: Any) -> Dict[str, Any]:
    base = default_config()
    if not isinstance(raw, dict):
        return base
    enabled = raw.get("enabled", base["enabled"])
    base["enabled"] = enabled if isinstance(enabled, bool) else str(enabled).lower() == "true"
    all_libraries = raw.get("all_libraries", base["all_libraries"])
    base["all_libraries"] = all_libraries if isinstance(all_libraries, bool) else str(all_libraries).lower() != "false"
    libraries = raw.get("libraries")
    if isinstance(libraries, list):
        base["libraries"] = sorted({str(k).strip() for k in libraries if str(k).strip()})
    server_posters = raw.get("badge_server_posters", base["badge_server_posters"])
    base["badge_server_posters"] = server_posters if isinstance(server_posters, bool) else str(server_posters).lower() == "true"
    prune = raw.get("prune_plex_uploads", base["prune_plex_uploads"])
    base["prune_plex_uploads"] = prune if isinstance(prune, bool) else str(prune).lower() != "false"
    raw_types = raw.get("types") if isinstance(raw.get("types"), dict) else {}
    for key, otype in all_types().items():
        rt = raw_types.get(key) if isinstance(raw_types.get(key), dict) else {}
        t_enabled = rt.get("enabled", False)
        if "overlays" in rt:
            raw_overlays = rt.get("overlays") if isinstance(rt.get("overlays"), list) else []
        elif rt:
            # A type entry without an overlays list (settings saved before overlays were named): its
            # placement/options become one overlay for all libraries.
            raw_overlays = [{"name": otype.label, "all_libraries": True, "placement": rt.get("placement"), "options": rt.get("options")}]
        else:
            raw_overlays = []
        overlays: List[Dict[str, Any]] = []
        claimed: set = set()
        for n, raw_overlay in enumerate(raw_overlays, start=1):
            if not isinstance(raw_overlay, dict):
                continue
            libs = sorted({str(k).strip() for k in (raw_overlay.get("libraries") or []) if str(k).strip()} - claimed)
            claimed |= set(libs)  # a library is named by one overlay of a type
            all_libraries = raw_overlay.get("all_libraries", not libs)
            o_enabled = raw_overlay.get("enabled", True)
            overlays.append({
                "name": str(raw_overlay.get("name") or "").strip()[:60] or f"{otype.label} {n}",
                "enabled": o_enabled if isinstance(o_enabled, bool) else str(o_enabled).lower() != "false",
                "all_libraries": all_libraries if isinstance(all_libraries, bool) else str(all_libraries).lower() == "true",
                "libraries": libs,
                "placement": normalize_placement(raw_overlay.get("placement"), otype.default_placement()),
                "options": otype.normalize_options(raw_overlay.get("options")),
            })
        base["types"][key] = {
            "enabled": t_enabled if isinstance(t_enabled, bool) else str(t_enabled).lower() == "true",
            "overlays": overlays,
        }
    return base


def load_config(db: Session) -> Dict[str, Any]:
    raw = get_setting_value(db, SETTING_OVERLAYS_CONFIG)
    if not raw:
        return default_config()
    try:
        return normalize_config(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        log_warning(LogTags.OVERLAYS, "overlays_config is not valid JSON — using defaults")
        return default_config()


def save_config(db: Session, raw: Any) -> Dict[str, Any]:
    config = normalize_config(raw)
    upsert_setting(db, SETTING_OVERLAYS_CONFIG, json.dumps(config))
    db.commit()
    return config


def overlay_signature(overlay: Dict[str, Any]) -> str:
    """Changes whenever one overlay's rendering inputs (placement, options) change. Kept in each
    badge record, so a settings change is seen per item and nothing else has to remember it."""
    inputs = {"placement": overlay.get("placement") or {}, "options": overlay.get("options") or {}}
    return hashlib.sha1(json.dumps(inputs, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def overlays_active(config: Dict[str, Any]) -> bool:
    return bool(config.get("enabled")) and any(t.get("enabled") for t in config.get("types", {}).values())
