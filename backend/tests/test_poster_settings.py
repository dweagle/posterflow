"""Shared poster/artwork settings resolution.

Every feature (rename, border, unmatched, cleanup, upload) reads the destination
through get_poster_destination, so "not configured" is not a reachable state — callers
only need to check the path exists. Every entry point (renamer, unmatched, workflow,
API) reads the Include selection through get_asset_include, so an empty or invalid
selection means the same thing everywhere. These pin both contracts.
"""

import json

from models.setting import Setting
from util.poster_settings import (
    DEFAULT_POSTER_DESTINATION,
    SETTING_ASSET_INCLUDE,
    get_asset_include,
    get_poster_destination,
)


def test_returns_configured_destination(test_db):
    test_db.add(Setting(key="poster_destination", value="/kometa/config/assets"))
    test_db.commit()

    assert get_poster_destination(test_db) == "/kometa/config/assets"


def test_defaults_when_setting_row_is_absent(test_db):
    assert get_poster_destination(test_db) == DEFAULT_POSTER_DESTINATION


def test_defaults_when_value_is_blank(test_db):
    """The UI documents "leave blank to use the default", so blank must resolve."""
    test_db.add(Setting(key="poster_destination", value=""))
    test_db.commit()

    assert get_poster_destination(test_db) == DEFAULT_POSTER_DESTINATION


def test_defaults_when_value_is_whitespace(test_db):
    test_db.add(Setting(key="poster_destination", value="   "))
    test_db.commit()

    assert get_poster_destination(test_db) == DEFAULT_POSTER_DESTINATION


def test_strips_surrounding_whitespace(test_db):
    test_db.add(Setting(key="poster_destination", value="  /kometa/config/assets  "))
    test_db.commit()

    assert get_poster_destination(test_db) == "/kometa/config/assets"


def test_default_matches_the_frontend_constant():
    """Paired with DEFAULT_POSTER_DESTINATION in frontend/src/api/posterManager.ts,
    which is what the settings page documents and saves."""
    assert DEFAULT_POSTER_DESTINATION == "/config/posters/assets"


# --- Include selection (one policy for every entry point) ---

def _save_include(test_db, value):
    test_db.add(Setting(key=SETTING_ASSET_INCLUDE, value=value))
    test_db.commit()


def test_include_defaults_to_posters_when_unset(test_db):
    assert get_asset_include(test_db) == ["posters"]


def test_include_defaults_to_posters_on_invalid_json(test_db):
    _save_include(test_db, "not json")
    assert get_asset_include(test_db) == ["posters"]


def test_include_respects_explicit_empty_selection(test_db):
    """The user unchecked everything — callers no-op loudly rather than guess posters."""
    _save_include(test_db, json.dumps([]))
    assert get_asset_include(test_db) == []


def test_include_filters_unknown_types(test_db):
    _save_include(test_db, json.dumps(["posters", "logo", "banner", 3]))
    assert get_asset_include(test_db) == ["posters", "logo"]
