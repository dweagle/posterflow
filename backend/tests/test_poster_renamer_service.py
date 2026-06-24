from models.setting import Setting, get_setting
from services.poster_renamer import PosterRenameService


class _FakeTmdbResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_enrich_collections_with_tmdb_matches_and_skips(test_db, monkeypatch):
    test_db.add(Setting(key="tmdb_api_key", value="fake-key"))
    test_db.commit()

    service = PosterRenameService(test_db)

    calls = []

    def fake_get(url, params=None, timeout=None):
        query = (params or {}).get("query", "")
        calls.append(query)
        if "john wick" in query.lower():
            return _FakeTmdbResponse(
                {"results": [{"id": 404, "name": "John Wick Collection", "poster_path": "/jw.jpg"}]}
            )
        return _FakeTmdbResponse({"results": []})

    monkeypatch.setattr("services.poster_renamer.requests.get", fake_get)

    media_dict = {
        "movies": [],
        "series": [],
        "collections": [
            {"type": "collections", "title": "John Wick", "tmdb_id": None},
            {"type": "collections", "title": "My Favorites", "tmdb_id": None},
            {"type": "collections", "title": "Already Linked", "tmdb_id": 999},
        ],
    }

    service._enrich_collections_with_tmdb(media_dict)

    matched, custom, preset = media_dict["collections"]
    # The id lands on tmdb_id_ref (display-only), NOT tmdb_id, so the poster
    # matcher — which branches on media["tmdb_id"] — is left untouched.
    assert matched["tmdb_id_ref"] == 404
    assert not matched.get("tmdb_id")
    assert matched["poster_url"] == "https://image.tmdb.org/t/p/w185/jw.jpg"
    assert not custom.get("tmdb_id_ref")
    assert not custom.get("tmdb_id")
    assert not custom.get("poster_url")
    # A collection that already had an id is not re-queried.
    assert preset["tmdb_id"] == 999
    assert "Already Linked" not in calls

    # Both the positive and negative result are cached for the next run.
    cache_setting = get_setting(test_db, "poster_collection_tmdb_cache")
    assert cache_setting is not None and cache_setting.value


def test_enrich_collections_with_tmdb_noops_without_api_key(test_db, monkeypatch):
    service = PosterRenameService(test_db)

    def fail_get(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("TMDB should not be queried without an API key")

    monkeypatch.setattr("services.poster_renamer.requests.get", fail_get)

    media_dict = {
        "movies": [],
        "series": [],
        "collections": [{"type": "collections", "title": "John Wick", "tmdb_id": None}],
    }
    service._enrich_collections_with_tmdb(media_dict)
    assert not media_dict["collections"][0].get("tmdb_id")
    assert not media_dict["collections"][0].get("tmdb_id_ref")


def test_rename_posters_fails_when_no_assets_found(test_db, monkeypatch):
    service = PosterRenameService(test_db)

    monkeypatch.setattr("services.poster_renamer.get_assets_files", lambda source_dirs, logger: ([], {}))

    result = service.rename_posters(
        source_dirs=["/tmp/source"],
        destination_dir="/tmp/dest",
        dry_run=True,
    )

    assert result["success"] is False
    assert result["error"] == "No assets found in the source directories"


def test_rename_posters_fails_when_no_media_found(test_db, monkeypatch):
    service = PosterRenameService(test_db)

    assets = [
        {
            "title": "Movie One",
            "year": 2024,
            "files": ["/tmp/source/Movie One.jpg"],
            "folder": "Movie One (2024)",
        }
    ]

    monkeypatch.setattr("services.poster_renamer.get_assets_files", lambda source_dirs, logger: (assets, {"m": assets}))
    monkeypatch.setattr(service, "get_media_from_instances", lambda: {"movies": [], "series": [], "collections": []})

    result = service.rename_posters(
        source_dirs=["/tmp/source"],
        destination_dir="/tmp/dest",
        dry_run=True,
    )

    assert result["success"] is False
    assert "No media found" in result["error"]


def test_rename_posters_fails_when_no_assets_match_media(test_db, monkeypatch):
    service = PosterRenameService(test_db)

    assets = [
        {
            "title": "Movie One",
            "year": 2024,
            "files": ["/tmp/source/Movie One.jpg"],
            "folder": "Movie One (2024)",
        }
    ]
    media = {
        "movies": [{"title": "Different Movie", "year": 2024, "folder": "Different Movie (2024)", "tmdb_id": 123}],
        "series": [],
        "collections": [],
    }

    monkeypatch.setattr("services.poster_renamer.get_assets_files", lambda source_dirs, logger: (assets, {"m": assets}))
    monkeypatch.setattr(service, "get_media_from_instances", lambda: media)
    monkeypatch.setattr(
        "services.poster_renamer.match_assets_to_media",
        lambda media_dict, prefix_index, strict_folder_match=False: {"movies": [], "series": [], "collections": []},
    )

    result = service.rename_posters(
        source_dirs=["/tmp/source"],
        destination_dir="/tmp/dest",
        dry_run=True,
    )

    assert result["success"] is False
    assert "No assets matched to media" in result["error"]


def test_rename_posters_successful_flow_returns_stats(test_db, monkeypatch):
    service = PosterRenameService(test_db)

    assets = [
        {
            "title": "Movie One",
            "year": 2024,
            "files": ["/tmp/source/Movie One.jpg"],
            "folder": "Movie One (2024)",
        }
    ]
    media = {
        "movies": [{"title": "Movie One", "year": 2024, "folder": "Movie One (2024)", "tmdb_id": 123}],
        "series": [],
        "collections": [],
    }
    matched_assets = {
        "movies": [
            {
                "title": "Movie One",
                "year": 2024,
                "folder": "Movie One (2024)",
                "files": ["/tmp/source/Movie One.jpg"],
            }
        ],
        "series": [],
        "collections": [],
    }

    monkeypatch.setattr("services.poster_renamer.get_assets_files", lambda source_dirs, logger: (assets, {"m": assets}))
    monkeypatch.setattr(service, "get_media_from_instances", lambda: media)
    monkeypatch.setattr(
        "services.poster_renamer.match_assets_to_media",
        lambda media_dict, prefix_index, strict_folder_match=False: matched_assets,
    )
    monkeypatch.setattr(
        service,
        "rename_files",
        lambda matched_assets, destination_dir, action_type, asset_folders, dry_run, progress_callback=None: (
            {"movies": [{"title": "Movie One", "year": 2024, "folder": "Movie One (2024)", "messages": ["renamed"]}], "series": [], "collections": []},
            ["/tmp/dest/Movie One (2024)/poster.jpg"],
            ["/tmp/source/Movie One.jpg"],
            {"/tmp/dest/Movie One (2024)/poster.jpg": ("/tmp/source/Movie One.jpg", "Movie One", 2024, "movie", None, 27205, None, "tt0000001", "https://image.tmdb.org/t/p/original/x.jpg", True)},
        ),
    )

    result = service.rename_posters(
        source_dirs=["/tmp/source"],
        destination_dir="/tmp/dest",
        dry_run=True,
    )

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["stats"]["total_assets"] == 1
    assert result["stats"]["total_media"] == 1
    assert result["stats"]["total_matched"] == 1
    assert result["stats"]["movies"] == 1


def test_filter_assets_for_target_prefers_matching_ids_and_title(test_db):
    service = PosterRenameService(test_db)

    assets = [
        {
            "type": "movies",
            "title": "Movie One",
            "year": 2024,
            "normalized_title": "movieone2024",
            "tmdb_id": 101,
            "imdb_id": "tt0101",
            "files": ["/tmp/source/Movie One.jpg"],
        },
        {
            "type": "movies",
            "title": "Movie Two",
            "year": 2024,
            "normalized_title": "movietwo2024",
            "tmdb_id": 202,
            "imdb_id": "tt0202",
            "files": ["/tmp/source/Movie Two.jpg"],
        },
    ]

    filtered_assets, _filtered_index = service._filter_assets_for_target(
        assets,
        target_media_type="movie",
        target_title="Movie One",
        target_year=2024,
        target_tmdb_id=101,
        target_tvdb_id=None,
        target_imdb_id="tt0101",
        target_season_number=None,
    )

    assert len(filtered_assets) == 1
    assert filtered_assets[0]["title"] == "Movie One"


def test_filter_assets_for_target_respects_series_season_scope(test_db):
    service = PosterRenameService(test_db)

    assets = [
        {
            "type": "series",
            "title": "The Show",
            "year": 2023,
            "normalized_title": "theshow2023",
            "tvdb_id": 300,
            "season_numbers": [1, 2],
            "files": ["/tmp/source/The Show/Season01.jpg", "/tmp/source/The Show/Season02.jpg"],
        },
        {
            "type": "series",
            "title": "Other Show",
            "year": 2023,
            "normalized_title": "othershow2023",
            "tvdb_id": 301,
            "season_numbers": [1],
            "files": ["/tmp/source/Other Show/Season01.jpg"],
        },
    ]

    filtered_assets, _filtered_index = service._filter_assets_for_target(
        assets,
        target_media_type="series",
        target_title="The Show",
        target_year=2023,
        target_tmdb_id=None,
        target_tvdb_id=300,
        target_imdb_id=None,
        target_season_number=2,
    )

    assert len(filtered_assets) == 1
    assert filtered_assets[0]["title"] == "The Show"
