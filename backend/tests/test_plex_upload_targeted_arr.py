"""Targeted webhook Plex index and ARR availability: guid/title search, preflight
context, id-key lookups, and availability index construction."""

from services.plex_upload import PlexUploadService
from plex_upload_fakes import _FakePlexSection, _FakePlexServerForTargeted, _SimplePlex


# ---------------------------------------------------------------------------
# Tests for targeted webhook index path
# ---------------------------------------------------------------------------


def test_build_plex_index_targeted_uses_guid_search(test_db, monkeypatch):
    """_build_plex_index_targeted should search by GUID and return a non-empty micro-index."""
    movie_item = _SimplePlex("movie", "The Matrix", key="532")

    fake_section = _FakePlexSection(
        section_type="movie",
        title="Movies",
        guid_results=[movie_item],
        title_results=[],
    )

    import plexapi.server as plexapi_server
    monkeypatch.setattr(
        plexapi_server,
        "PlexServer",
        lambda url, token: _FakePlexServerForTargeted([fake_section]),
    )

    service = PlexUploadService(test_db)
    plex_instances = [{"name": "Main", "url": "http://plex:32400", "api_key": "abc"}]
    selected_libraries = {"Main": [{"key": "fake_key_Movies", "title": "Movies", "enabled": True}]}

    index, library_totals = service._build_plex_index_targeted(
        plex_instances,
        selected_libraries,
        tmdb_id=603,
        title="The Matrix",
        year=1999,
        media_type="movie",
    )

    assert index  # non-empty → targeted search found something
    assert library_totals
    assert any(lt["library"] == "Movies" for lt in library_totals)


def test_build_plex_index_targeted_falls_back_to_title_when_guid_empty(test_db, monkeypatch):
    """When GUID search returns nothing, targeted index should try title search."""
    show_item = _SimplePlex("show", "Breaking Bad", key="101")
    show_item.type = "show"
    show_item.year = 2008

    fake_section = _FakePlexSection(
        section_type="show",
        title="TV Shows",
        guid_results=[],          # GUID search misses (legacy agent)
        title_results=[show_item],
    )

    import plexapi.server as plexapi_server
    monkeypatch.setattr(
        plexapi_server,
        "PlexServer",
        lambda url, token: _FakePlexServerForTargeted([fake_section]),
    )

    service = PlexUploadService(test_db)
    plex_instances = [{"name": "Main", "url": "http://plex:32400", "api_key": "abc"}]
    selected_libraries = {"Main": [{"key": "fake_key_TV Shows", "title": "TV Shows", "enabled": True}]}

    index, library_totals = service._build_plex_index_targeted(
        plex_instances,
        selected_libraries,
        tvdb_id=81189,
        title="Breaking Bad",
        year=2008,
        media_type="series",
    )

    assert index
    assert library_totals


def test_build_plex_index_targeted_returns_empty_when_nothing_found(test_db, monkeypatch):
    """When no items match via GUID or title, the method returns ({}, []) as a fallback signal."""
    fake_section = _FakePlexSection(
        section_type="movie",
        title="Movies",
        guid_results=[],
        title_results=[],
    )

    import plexapi.server as plexapi_server
    monkeypatch.setattr(
        plexapi_server,
        "PlexServer",
        lambda url, token: _FakePlexServerForTargeted([fake_section]),
    )

    service = PlexUploadService(test_db)
    plex_instances = [{"name": "Main", "url": "http://plex:32400", "api_key": "abc"}]
    selected_libraries = {"Main": [{"key": "fake_key_Movies", "title": "Movies", "enabled": True}]}

    index, library_totals = service._build_plex_index_targeted(
        plex_instances,
        selected_libraries,
        tmdb_id=9999999,
        title="Completely Unknown Movie",
        year=2099,
        media_type="movie",
    )

    assert index == {}
    assert library_totals == []


def test_prepare_webhook_context_seeds_preflight_cache(test_db, monkeypatch):
    """prepare_webhook_context() should seed _preflight_context_cache via targeted index."""
    movie_item = _SimplePlex("movie", "The Matrix", key="532")

    fake_section = _FakePlexSection(
        section_type="movie",
        title="Movies",
        guid_results=[movie_item],
    )

    import plexapi.server as plexapi_server
    monkeypatch.setattr(
        plexapi_server,
        "PlexServer",
        lambda url, token: _FakePlexServerForTargeted([fake_section]),
    )

    # Provide the minimum settings needed for plex_instances and selected_libraries.
    from models.setting import upsert_setting as _upsert_setting
    import json as _json

    _upsert_setting(test_db, "plex_instances", _json.dumps([
        {"name": "Main", "url": "http://plex:32400", "api_key": "abc"}
    ]))
    _upsert_setting(test_db, "plex_library_config", _json.dumps([
        {
            "instance_name": "Main",
            "libraries": [{"key": "fake_key_Movies", "title": "Movies", "enabled": True}],
        }
    ]))
    test_db.commit()

    service = PlexUploadService(test_db)
    assert service._preflight_context_cache is None

    error = service.prepare_webhook_context(
        tmdb_id=603,
        title="The Matrix",
        year=1999,
        media_type="movie",
    )

    assert error is None
    assert service._preflight_context_cache is not None
    # The cache tuple is (preflight_error, destination_dir, index, library_totals)
    _pf_error, _dest, cached_index, cached_totals = service._preflight_context_cache
    assert cached_index is not None
    assert cached_totals is not None


def test_prepare_webhook_context_returns_error_when_no_plex_instances(test_db):
    """prepare_webhook_context() should return an error string if no Plex instances are configured."""
    from models.setting import upsert_setting as _upsert_setting
    import json as _json

    _upsert_setting(test_db, "plex_instances", _json.dumps([]))
    test_db.commit()

    service = PlexUploadService(test_db)
    error = service.prepare_webhook_context(tmdb_id=603, title="The Matrix", year=1999, media_type="movie")

    assert error == PlexUploadService.ERROR_NO_PLEX_INSTANCES


def test_prepare_webhook_context_falls_back_to_full_index_on_empty_targeted(test_db, monkeypatch):
    """When targeted search returns nothing, prepare_webhook_context should fall back to full index."""
    empty_section = _FakePlexSection(
        section_type="movie",
        title="Movies",
        guid_results=[],
        title_results=[],
    )
    full_movie = _SimplePlex("movie", "Some Other Movie", key="999")

    import plexapi.server as plexapi_server
    monkeypatch.setattr(
        plexapi_server,
        "PlexServer",
        lambda url, token: _FakePlexServerForTargeted([empty_section]),
    )

    # Patch _build_plex_index to verify fallback is called.
    fallback_called = []

    def _fake_full_index(plex_instances, selected_libraries):
        fallback_called.append(True)
        return (
            {"movies": {"somekey": [full_movie]}, "shows": {}, "collections": {}},
            [{"instance": "Main", "library": "Movies", "section_type": "movie", "items": 1, "collections": 0}],
        )

    from models.setting import upsert_setting as _upsert_setting
    import json as _json

    _upsert_setting(test_db, "plex_instances", _json.dumps([
        {"name": "Main", "url": "http://plex:32400", "api_key": "abc"}
    ]))
    _upsert_setting(test_db, "plex_library_config", _json.dumps([
        {
            "instance_name": "Main",
            "libraries": [{"key": "fake_key_Movies", "title": "Movies", "enabled": True}],
        }
    ]))
    test_db.commit()

    service = PlexUploadService(test_db)
    service._build_plex_index = _fake_full_index  # type: ignore[method-assign]

    error = service.prepare_webhook_context(
        tmdb_id=9999999,
        title="Unknown Movie",
        year=2099,
        media_type="movie",
    )

    assert error is None
    assert fallback_called, "Full index fallback should have been triggered"
    assert service._preflight_context_cache is not None


# ---------------------------------------------------------------------------
# _arr_id_keys_for_asset
# ---------------------------------------------------------------------------


def test_arr_id_keys_for_asset_returns_empty_when_no_availability(test_db):
    service = PlexUploadService(test_db)
    asset = {"media_key": "fridaynightlights", "path": "/posters/Friday Night Lights (2006)/poster.jpg"}
    assert service._arr_id_keys_for_asset(asset, None) == []


def test_arr_id_keys_for_asset_returns_empty_when_key_not_in_index(test_db):
    service = PlexUploadService(test_db)
    asset = {"media_key": "missingtitle", "path": "/posters/Missing Title/poster.jpg"}
    arr_availability = {"shows": {}, "movies": {}}
    assert service._arr_id_keys_for_asset(asset, arr_availability) == []


def test_arr_id_keys_for_asset_returns_show_tvdb_and_imdb(test_db):
    service = PlexUploadService(test_db)
    # media_key matches how _availability_keys_for_item indexes it (year stripped)
    asset = {"media_key": "fridaynightlights", "path": "/posters/Friday Night Lights (2006)/poster.jpg"}
    arr_availability = {
        "shows": {
            "fridaynightlights": {
                "tvdb_id": 79337,
                "imdb_id": "tt0364978",
            }
        },
        "movies": {},
    }
    keys = service._arr_id_keys_for_asset(asset, arr_availability, inferred_filter="series")
    assert "id:tvdb:79337" in keys
    assert "id:imdb:tt0364978" in keys


def test_arr_id_keys_for_asset_returns_movie_tmdb_and_imdb(test_db):
    service = PlexUploadService(test_db)
    asset = {"media_key": "thematrix", "path": "/posters/The Matrix (1999)/poster.jpg"}
    arr_availability = {
        "movies": {
            "thematrix": {
                "tmdb_id": 603,
                "imdb_id": "tt0133093",
            }
        },
        "shows": {},
    }
    keys = service._arr_id_keys_for_asset(asset, arr_availability, inferred_filter="movie")
    assert "id:tmdb:603" in keys
    assert "id:imdb:tt0133093" in keys


def test_arr_id_keys_for_asset_series_filter_skips_movies_index(test_db):
    """inferred_filter='series' must not return IDs from the movies index."""
    service = PlexUploadService(test_db)
    asset = {"media_key": "somekey", "path": "/posters/Some Show/poster.jpg"}
    arr_availability = {
        "shows": {},
        "movies": {"somekey": {"tmdb_id": 999, "imdb_id": "tt9999999"}},
    }
    assert service._arr_id_keys_for_asset(asset, arr_availability, inferred_filter="series") == []


def test_arr_id_keys_for_asset_movie_filter_skips_shows_index(test_db):
    """inferred_filter='movie' must not return IDs from the shows index."""
    service = PlexUploadService(test_db)
    asset = {"media_key": "somekey", "path": "/posters/Some Show/poster.jpg"}
    arr_availability = {
        "shows": {"somekey": {"tvdb_id": 12345, "imdb_id": None}},
        "movies": {},
    }
    assert service._arr_id_keys_for_asset(asset, arr_availability, inferred_filter="movie") == []


# ---------------------------------------------------------------------------
# _asset_has_arr_availability — uses caller-supplied (augmented) id keys
# ---------------------------------------------------------------------------


def test_asset_has_arr_availability_movie_uses_supplied_id_keys(test_db):
    """Caller-supplied id keys are consulted by the movie availability check."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "thematrix",
        "path": "/posters/The Matrix/poster.jpg",  # no {tmdb-}/{imdb-} tokens
        "asset_type": "main",
        "folder_year": 1999,
    }
    # Artificial index: record present only under the id key.
    arr_availability = {
        "movies": {"id:tmdb:603": {"has_file": False, "tmdb_id": 603}},
        "shows": {},
    }

    # Path-only extraction finds no id keys and no title match in this index.
    assert service._asset_has_arr_availability(asset, "movie", arr_availability) == (True, None)

    # Supplying the id key lets the no-file record be reached.
    available, reason = service._asset_has_arr_availability(
        asset, "movie", arr_availability, asset_id_keys=["id:tmdb:603"]
    )
    assert available is False
    assert reason == "no Radarr file available"


def test_asset_has_arr_availability_movie_supplied_id_keys_with_file(test_db):
    """Supplied id key resolving to a has_file record reports availability."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "thematrix",
        "path": "/posters/The Matrix/poster.jpg",
        "asset_type": "main",
        "folder_year": 1999,
    }
    arr_availability = {
        "movies": {"id:tmdb:603": {"has_file": True, "tmdb_id": 603}},
        "shows": {},
    }
    assert service._asset_has_arr_availability(
        asset, "movie", arr_availability, asset_id_keys=["id:tmdb:603"]
    ) == (True, None)


# ---------------------------------------------------------------------------
# _build_arr_availability_index — ID fields stored in entries
# ---------------------------------------------------------------------------


def test_build_arr_availability_index_stores_tvdb_and_imdb_in_show_entries(test_db, monkeypatch):
    service = PlexUploadService(test_db)
    monkeypatch.setattr(
        service,
        "_get_arr_instances",
        lambda _key: [{"url": "http://sonarr", "api_key": "abc"}],
    )

    class _FakeClient:
        connect_status = True

        def get_parsed_media(self, include_unmonitored=True):
            return [
                {
                    "title": "Friday Night Lights",
                    "year": 2006,
                    "folder": "/tv/Friday Night Lights (2006)",
                    "tvdb_id": 79337,
                    "imdb_id": "tt0364978",
                    "has_episodes": True,
                    "seasons": [{"season_number": 1, "season_has_episodes": True}],
                }
            ]

    monkeypatch.setattr("services.plex_upload.create_arr_client", lambda *args, **kwargs: _FakeClient())

    result = service._build_arr_availability_index(media_type_filter="series")

    # normalize_titles strips the year tag, so the index key has no year
    entry = result["shows"].get("fridaynightlights")
    assert entry is not None
    assert entry["tvdb_id"] == 79337
    assert entry["imdb_id"] == "tt0364978"


def test_build_arr_availability_index_stores_tmdb_and_imdb_in_movie_entries(test_db, monkeypatch):
    service = PlexUploadService(test_db)
    monkeypatch.setattr(
        service,
        "_get_arr_instances",
        lambda _key: [{"url": "http://radarr", "api_key": "abc"}],
    )

    class _FakeClient:
        connect_status = True

        def get_parsed_media(self, include_unmonitored=True):
            return [
                {
                    "title": "The Matrix",
                    "year": 1999,
                    "folder": "/movies/The Matrix (1999)",
                    "tmdb_id": 603,
                    "imdb_id": "tt0133093",
                    "has_file": True,
                }
            ]

    monkeypatch.setattr("services.plex_upload.create_arr_client", lambda *args, **kwargs: _FakeClient())

    result = service._build_arr_availability_index(media_type_filter="movie")

    # normalize_titles strips the year tag
    entry = result["movies"].get("thematrix")
    assert entry is not None
    assert entry["tmdb_id"] == 603
    assert entry["imdb_id"] == "tt0133093"


# ---------------------------------------------------------------------------
# _upload_asset — ARR supplementation integration
# ---------------------------------------------------------------------------


def test_upload_asset_supplements_arr_ids_when_path_has_no_tokens(test_db):
    """End-to-end: asset folder has no {tvdb-N} tokens, ARR index has the TVDB ID,
    and the Plex index is keyed by that GUID — the show must still be matched."""
    service = PlexUploadService(test_db)

    class _FakeSeason:
        def __init__(self, index: int):
            self.index = index

        def uploadPoster(self, filepath: str) -> None:
            pass

    class _FakeShow:
        class _FakeServer:
            machineIdentifier = "server-1"

        _server = _FakeServer()
        ratingKey = "42"
        title = "Friday Night Lights"
        year = 2006
        type = "show"
        librarySectionTitle = "TV Shows"
        librarySectionID = 1

        def seasons(self):
            return [_FakeSeason(1)]

        def uploadPoster(self, filepath: str) -> None:
            pass

    fake_show = _FakeShow()

    # Plex index has the show under the GUID-based key (set by includeGuids=1)
    index = {
        "movies": {},
        "shows": {"id:tvdb:79337": [fake_show]},
        "collections": {},
    }

    # ARR index has the TVDB ID stored (new behaviour from _build_arr_availability_index).
    # Key uses normalize_titles output — year is stripped.
    arr_availability = {
        "movies": {},
        "shows": {
            "fridaynightlights": {
                "has_episodes": True,
                "seasons": {1: True},
                "tvdb_id": 79337,
                "imdb_id": None,
            }
        },
    }

    # Asset path has NO {tvdb-N} token — simulates plain Sonarr folder naming.
    # media_key matches normalize_titles output (year stripped).
    asset = {
        "asset_type": "season",
        "path": "/posters/Friday Night Lights (2006)/Season01.jpg",
        "media_key": "fridaynightlights",
        "display_name": "Friday Night Lights (2006)",
        "season_number": 1,
    }

    outcome = service._upload_asset(
        asset=asset,
        index=index,
        dry_run=True,
        media_type_filter="series",
        arr_availability=arr_availability,
    )

    assert outcome.matched is True, "Show should be matched via ARR-supplemented TVDB ID key"
    assert outcome.plex_targets >= 1
    assert outcome.uploaded == 1  # dry_run counts the would-be upload
    assert outcome.seasons_missing == 0


def test_upload_asset_no_match_when_arr_empty_and_no_path_tokens(test_db):
    """When neither the path nor the ARR index provides IDs, and the Plex index only
    has a GUID key, the asset must NOT match (correct failure mode)."""
    service = PlexUploadService(test_db)

    class _FakeShow:
        class _FakeServer:
            machineIdentifier = "server-1"

        _server = _FakeServer()
        ratingKey = "99"
        title = "Friday Night Lights"
        year = 2006
        type = "show"
        librarySectionTitle = "TV Shows"
        librarySectionID = 1

        def seasons(self):
            return []

        def uploadPoster(self, filepath: str) -> None:
            pass

    index = {
        "movies": {},
        "shows": {"id:tvdb:79337": [_FakeShow()]},  # only GUID key, no title key
        "collections": {},
    }

    asset = {
        "asset_type": "season",
        "path": "/posters/Friday Night Lights (2006)/Season01.jpg",
        "media_key": "fridaynightlights",
        "display_name": "Friday Night Lights (2006)",
        "season_number": 1,
    }

    # No ARR availability at all
    outcome = service._upload_asset(
        asset=asset,
        index=index,
        dry_run=True,
        media_type_filter="series",
        arr_availability=None,
    )

    assert outcome.matched is False
    assert outcome.plex_targets == 0
    assert outcome.uploaded == 0
    assert outcome.skip_reason == "no_plex_match"
