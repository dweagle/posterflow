"""Shared access to the poster/artwork destination setting.

Posters and artwork are renamed into the same destination, and nearly every feature
(rename, border, unmatched, cleanup, upload) needs to find it. The key and its default
live here so callers can't disagree about where assets are.

Keep DEFAULT_POSTER_DESTINATION in sync with the frontend constant of the same name
(frontend/src/api/posterManager.ts), which is what the UI documents and saves.
"""

import json

from sqlalchemy.orm import Session

from models.setting import get_setting_value

SETTING_POSTER_DESTINATION = "poster_destination"

DEFAULT_POSTER_DESTINATION = "/config/posters/assets"

SETTING_ASSET_INCLUDE = "asset_renamer_include"

ASSET_TYPES = ("posters", "logo", "background", "squareart")


def get_poster_destination(db: Session) -> str:
    """The configured destination, falling back to the default when unset or blank.

    Always returns a usable path, so callers only need to check that it exists —
    "not configured" isn't a reachable state.
    """
    value = get_setting_value(db, SETTING_POSTER_DESTINATION)
    return (str(value).strip() if value else "") or DEFAULT_POSTER_DESTINATION


def get_asset_include(db: Session) -> list[str]:
    """The saved Include selection (subset of ASSET_TYPES), shared by every entry point.

    Missing or unparseable → ["posters"] (the pre-merge default). An explicitly saved
    empty list is respected — the user unchecked everything, so callers no-op loudly
    rather than guess.
    """
    raw = get_setting_value(db, SETTING_ASSET_INCLUDE)
    if not raw:
        return ["posters"]
    try:
        vals = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ["posters"]
    if not isinstance(vals, list):
        return ["posters"]
    return [str(v) for v in vals if str(v) in ASSET_TYPES]
