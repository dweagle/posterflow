import json

from models.setting import Setting
from services.unmatched_assets import UnmatchedAssetsService


def test_get_cached_results_returns_empty_when_missing(test_db):
    service = UnmatchedAssetsService(test_db)

    result = service.get_cached_results()

    assert result["summary"]["grand_total"]["total"] == 0
    assert result["unmatched"]["movies"] == []
    assert result["last_run"] is None


def test_get_cached_results_returns_empty_for_invalid_json(test_db):
    test_db.add(Setting(key="poster_unmatched_stats", value="{bad-json"))
    test_db.commit()

    service = UnmatchedAssetsService(test_db)
    result = service.get_cached_results()

    assert result["summary"]["grand_total"]["total"] == 0
    assert result["last_run"] is None


def test_detect_unmatched_returns_empty_when_asset_scan_fails(test_db, monkeypatch):
    service = UnmatchedAssetsService(test_db)

    def _raise_scan_error(_source_dirs, merge=False):
        raise RuntimeError("scan failed")

    monkeypatch.setattr("services.unmatched_assets.get_assets_files", _raise_scan_error)

    media_dict = {"movies": [], "series": [], "collections": []}
    result = service.detect_unmatched(media_dict, ["/tmp/posters"])

    assert result["summary"]["grand_total"]["total"] == 0
    assert result["unmatched"]["movies"] == []


def test_detect_unmatched_successful_flow_saves_results(test_db, monkeypatch):
    service = UnmatchedAssetsService(test_db)

    asset = {
        "title": "Matched Movie",
        "normalized_title": "matchedmovie",
        "alternate_titles": [],
        "normalized_alternate_titles": [],
        "year": 2024,
        "tmdb_id": 101,
        "tvdb_id": None,
        "imdb_id": None,
        "files": ["Matched Movie (2024).jpg"],
        "season_numbers": [],
    }

    def _fake_get_assets_files(_source_dirs, merge=False):
        return [asset], {"m": [asset]}

    def _fake_search_matches(_prefix_index, title, tmdb_id=None, tvdb_id=None):
        if tmdb_id == 101:
            return [asset]
        return []

    monkeypatch.setattr("services.unmatched_assets.get_assets_files", _fake_get_assets_files)
    monkeypatch.setattr("services.unmatched_assets.search_matches", _fake_search_matches)

    media_dict = {
        "movies": [
            {
                "title": "Matched Movie",
                "normalized_title": "matchedmovie",
                "alternate_titles": [],
                "normalized_alternate_titles": [],
                "year": 2024,
                "tmdb_id": 101,
                "tvdb_id": None,
                "imdb_id": None,
                "status": "released",
                "instance": "Plex A",
            },
            {
                "title": "Unmatched Movie",
                "normalized_title": "unmatchedmovie",
                "alternate_titles": [],
                "normalized_alternate_titles": [],
                "year": 2023,
                "tmdb_id": 202,
                "tvdb_id": None,
                "imdb_id": None,
                "status": "released",
                "instance": "Plex A",
            },
        ],
        "series": [],
        "collections": [],
    }

    progress_updates = []
    result = service.detect_unmatched(
        media_dict,
        ["/tmp/organized"],
        progress_callback=lambda phase, current, total, message: progress_updates.append(
            (phase, current, total, message)
        ),
    )

    assert result["summary"]["movies"]["total"] == 2
    assert result["summary"]["movies"]["unmatched"] == 1
    assert result["summary"]["grand_total"]["total"] == 2
    assert result["summary"]["grand_total"]["unmatched"] == 1
    assert len(result["unmatched"]["movies"]) == 1
    assert result["unmatched"]["movies"][0]["title"] == "Unmatched Movie"
    assert len(progress_updates) > 0

    saved = test_db.query(Setting).filter(Setting.key == "poster_unmatched_stats").first()
    assert saved is not None
    saved_payload = json.loads(saved.value)
    assert saved_payload["summary"]["movies"]["unmatched"] == 1


def test_detect_unmatched_ignores_unmonitored_when_enabled(test_db, monkeypatch):
    service = UnmatchedAssetsService(test_db)

    test_db.add(Setting(key="unmatched_ignore_unmonitored", value="true"))
    test_db.commit()

    asset = {
        "title": "Monitored Movie",
        "normalized_title": "monitoredmovie",
        "alternate_titles": [],
        "normalized_alternate_titles": [],
        "year": 2024,
        "tmdb_id": 101,
        "tvdb_id": None,
        "imdb_id": None,
        "files": ["Monitored Movie (2024).jpg"],
        "season_numbers": [],
    }

    def _fake_get_assets_files(_source_dirs, merge=False):
        return [asset], {"m": [asset]}

    def _fake_search_matches(_prefix_index, title, tmdb_id=None, tvdb_id=None):
        if tmdb_id == 101:
            return [asset]
        return []

    monkeypatch.setattr("services.unmatched_assets.get_assets_files", _fake_get_assets_files)
    monkeypatch.setattr("services.unmatched_assets.search_matches", _fake_search_matches)

    media_dict = {
        "movies": [
            {
                "title": "Monitored Movie",
                "normalized_title": "monitoredmovie",
                "alternate_titles": [],
                "normalized_alternate_titles": [],
                "year": 2024,
                "tmdb_id": 101,
                "tvdb_id": None,
                "imdb_id": None,
                "status": "released",
                "monitored": True,
                "root_folder": "/data/media/movies",
                "instance": "Radarr A",
            },
            {
                "title": "Unmonitored Movie",
                "normalized_title": "unmonitoredmovie",
                "alternate_titles": [],
                "normalized_alternate_titles": [],
                "year": 2023,
                "tmdb_id": 202,
                "tvdb_id": None,
                "imdb_id": None,
                "status": "released",
                "monitored": False,
                "root_folder": "/data/media/movies",
                "instance": "Radarr A",
            },
        ],
        "series": [],
        "collections": [],
    }

    result = service.detect_unmatched(media_dict, ["/tmp/organized"])

    assert result["summary"]["movies"]["total"] == 1
    assert result["summary"]["movies"]["unmatched"] == 0


def test_detect_unmatched_ignores_root_folders(test_db, monkeypatch):
    service = UnmatchedAssetsService(test_db)

    test_db.add(Setting(key="unmatched_ignore_root_folders", value='["skip_movies"]'))
    test_db.commit()

    asset = {
        "title": "Dummy",
        "normalized_title": "dummy",
        "alternate_titles": [],
        "normalized_alternate_titles": [],
        "year": 2024,
        "tmdb_id": 999,
        "tvdb_id": None,
        "imdb_id": None,
        "files": ["Dummy.jpg"],
        "season_numbers": [],
    }

    def _fake_get_assets_files(_source_dirs, merge=False):
        return [asset], {"d": [asset]}

    def _fake_search_matches(_prefix_index, title, tmdb_id=None, tvdb_id=None):
        return []

    monkeypatch.setattr("services.unmatched_assets.get_assets_files", _fake_get_assets_files)
    monkeypatch.setattr("services.unmatched_assets.search_matches", _fake_search_matches)

    media_dict = {
        "movies": [
            {
                "title": "Should Be Ignored",
                "normalized_title": "shouldbeignored",
                "alternate_titles": [],
                "normalized_alternate_titles": [],
                "year": 2024,
                "tmdb_id": 1,
                "tvdb_id": None,
                "imdb_id": None,
                "status": "released",
                "monitored": True,
                "root_folder": "/data/skip_movies",
                "instance": "Radarr A",
            },
            {
                "title": "Should Be Counted",
                "normalized_title": "shouldbecounted",
                "alternate_titles": [],
                "normalized_alternate_titles": [],
                "year": 2024,
                "tmdb_id": 2,
                "tvdb_id": None,
                "imdb_id": None,
                "status": "released",
                "monitored": True,
                "root_folder": "/data/keep_movies",
                "instance": "Radarr A",
            },
        ],
        "series": [],
        "collections": [],
    }

    result = service.detect_unmatched(media_dict, ["/tmp/organized"])

    assert result["summary"]["movies"]["total"] == 1
    assert result["summary"]["movies"]["unmatched"] == 1
    assert result["unmatched"]["movies"][0]["title"] == "Should Be Counted"


def test_detect_unmatched_ignores_collections_by_title(test_db, monkeypatch):
    service = UnmatchedAssetsService(test_db)

    test_db.add(Setting(key="unmatched_ignore_collections", value='["ignore me"]'))
    test_db.commit()

    asset = {
        "title": "Dummy",
        "normalized_title": "dummy",
        "alternate_titles": [],
        "normalized_alternate_titles": [],
        "year": None,
        "tmdb_id": None,
        "tvdb_id": None,
        "imdb_id": None,
        "files": ["Dummy.jpg"],
        "season_numbers": [],
    }

    def _fake_get_assets_files(_source_dirs, merge=False):
        return [asset], {"d": [asset]}

    def _fake_search_matches(_prefix_index, title, tmdb_id=None, tvdb_id=None):
        return []

    monkeypatch.setattr("services.unmatched_assets.get_assets_files", _fake_get_assets_files)
    monkeypatch.setattr("services.unmatched_assets.search_matches", _fake_search_matches)

    media_dict = {
        "movies": [],
        "series": [],
        "collections": [
            {
                "title": "Ignore Me",
                "normalized_title": "ignoreme",
                "alternate_titles": [],
                "normalized_alternate_titles": [],
                "year": None,
                "instance": "Plex A",
            },
            {
                "title": "Keep Me",
                "normalized_title": "keepme",
                "alternate_titles": [],
                "normalized_alternate_titles": [],
                "year": None,
                "instance": "Plex A",
            },
        ],
    }

    result = service.detect_unmatched(media_dict, ["/tmp/organized"])

    assert result["summary"]["collections"]["total"] == 1
    assert result["summary"]["collections"]["unmatched"] == 1
    assert result["unmatched"]["collections"][0]["title"] == "Keep Me"


def test_detect_unmatched_ignore_unmonitored_keeps_items_without_monitored_flag(test_db, monkeypatch):
    service = UnmatchedAssetsService(test_db)

    test_db.add(Setting(key="unmatched_ignore_unmonitored", value="true"))
    test_db.commit()

    def _fake_get_assets_files(_source_dirs, merge=False):
        return [], {}

    monkeypatch.setattr("services.unmatched_assets.get_assets_files", _fake_get_assets_files)

    media_dict = {
        "movies": [
            {
                "title": "Movie Without Monitored Flag",
                "normalized_title": "moviewithoutmonitoredflag",
                "alternate_titles": [],
                "normalized_alternate_titles": [],
                "year": 2024,
                "tmdb_id": 111,
                "status": "released",
                "instance": "Radarr A",
            }
        ],
        "series": [
            {
                "title": "Series Without Monitored Flag",
                "normalized_title": "serieswithoutmonitoredflag",
                "alternate_titles": [],
                "normalized_alternate_titles": [],
                "year": 2024,
                "tvdb_id": 222,
                "status": "continuing",
                "seasons": [{"season_number": 1, "season_has_episodes": True}],
                "instance": "Sonarr A",
            }
        ],
        "collections": [],
    }

    filtered = service._apply_unmatched_filters(
        media_dict,
        ignore_root_folders=[],
        ignore_collections=[],
        ignore_unmonitored=True,
    )

    assert len(filtered["movies"]) == 1
    assert len(filtered["series"]) == 1


def test_detect_unmatched_incinemas_flagged_only_when_in_library(test_db, monkeypatch):
    """In-cinemas movies are skipped (no poster expected yet) unless the file is downloaded."""
    service = UnmatchedAssetsService(test_db)

    unrelated_asset = {
        "title": "Unrelated",
        "normalized_title": "unrelated",
        "alternate_titles": [],
        "normalized_alternate_titles": [],
        "year": 2000,
        "tmdb_id": 999,
        "tvdb_id": None,
        "imdb_id": None,
        "files": ["Unrelated (2000).jpg"],
        "season_numbers": [],
    }

    def _fake_get_assets_files(_source_dirs, merge=False):
        return [unrelated_asset], {"u": [unrelated_asset]}

    def _fake_search_matches(_prefix_index, title, tmdb_id=None, tvdb_id=None):
        return []

    monkeypatch.setattr("services.unmatched_assets.get_assets_files", _fake_get_assets_files)
    monkeypatch.setattr("services.unmatched_assets.search_matches", _fake_search_matches)

    media_dict = {
        "movies": [
            {
                "title": "In Cinemas No File",
                "normalized_title": "incinemasnofile",
                "alternate_titles": [],
                "normalized_alternate_titles": [],
                "year": 2026,
                "tmdb_id": 301,
                "status": "incinemas",
                "has_file": False,
                "instance": "Radarr A",
            },
            {
                "title": "Deep Water",
                "normalized_title": "deepwater",
                "alternate_titles": [],
                "normalized_alternate_titles": [],
                "year": 2026,
                "tmdb_id": 1127384,
                "status": "incinemas",
                "has_file": True,
                "instance": "Radarr A",
            },
        ],
        "series": [],
        "collections": [],
    }

    result = service.detect_unmatched(media_dict, ["/tmp/organized"])

    unmatched_titles = [m["title"] for m in result["unmatched"]["movies"]]
    assert unmatched_titles == ["Deep Water"]

    # The not-downloaded in-cinemas movie is excluded from the total, so it can't
    # silently pad the completion percentage.
    assert result["summary"]["movies"]["total"] == 1
    assert result["summary"]["movies"]["unmatched"] == 1
    assert result["summary"]["movies"]["percent_complete"] == 0.0


def test_detect_unmatched_stats_exclude_undownloaded_unreleased(test_db, monkeypatch):
    """The completion percentage only counts movies a poster is expected for."""
    service = UnmatchedAssetsService(test_db)

    asset = {
        "title": "Released Matched",
        "normalized_title": "releasedmatched",
        "alternate_titles": [],
        "normalized_alternate_titles": [],
        "year": 2024,
        "tmdb_id": 101,
        "tvdb_id": None,
        "imdb_id": None,
        "files": ["Released Matched (2024).jpg"],
        "season_numbers": [],
    }

    def _fake_get_assets_files(_source_dirs, merge=False):
        return [asset], {"m": [asset]}

    def _fake_search_matches(_prefix_index, title, tmdb_id=None, tvdb_id=None):
        return [asset] if tmdb_id == 101 else []

    monkeypatch.setattr("services.unmatched_assets.get_assets_files", _fake_get_assets_files)
    monkeypatch.setattr("services.unmatched_assets.search_matches", _fake_search_matches)

    media_dict = {
        "movies": [
            # Released with poster -> counts, matched
            {"title": "Released Matched", "normalized_title": "releasedmatched",
             "alternate_titles": [], "normalized_alternate_titles": [], "year": 2024,
             "tmdb_id": 101, "status": "released", "has_file": True, "instance": "Radarr A"},
            # In cinemas, downloaded, no poster -> counts, unmatched
            {"title": "Deep Water", "normalized_title": "deepwater",
             "alternate_titles": [], "normalized_alternate_titles": [], "year": 2026,
             "tmdb_id": 1127384, "status": "incinemas", "has_file": True, "instance": "Radarr A"},
            # In cinemas, not downloaded -> excluded from total
            {"title": "Theater Only", "normalized_title": "theateronly",
             "alternate_titles": [], "normalized_alternate_titles": [], "year": 2026,
             "tmdb_id": 302, "status": "incinemas", "has_file": False, "instance": "Radarr A"},
            # Announced, not downloaded -> excluded from total
            {"title": "Future Film", "normalized_title": "futurefilm",
             "alternate_titles": [], "normalized_alternate_titles": [], "year": 2027,
             "tmdb_id": 303, "status": "announced", "has_file": False, "instance": "Radarr A"},
        ],
        "series": [],
        "collections": [],
    }

    result = service.detect_unmatched(media_dict, ["/tmp/organized"])

    movies = result["summary"]["movies"]
    assert movies["total"] == 2  # released + downloaded in-cinemas only
    assert movies["unmatched"] == 1  # Deep Water
    assert movies["percent_complete"] == 50.0
    assert result["summary"]["grand_total"]["total"] == 2
