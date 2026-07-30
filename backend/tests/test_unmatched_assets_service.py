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

    def _raise_scan_error(_source_dirs, merge=False, exclude_artwork=False):
        raise RuntimeError("scan failed")

    monkeypatch.setattr("services.unmatched_assets.get_assets_files", _raise_scan_error)

    media_dict = {"movies": [], "series": [], "collections": []}
    result = service.detect_unmatched(media_dict, ["/tmp/posters"])

    assert result["summary"]["grand_total"]["total"] == 0
    assert result["unmatched"]["movies"] == []


def test_empty_destination_reports_everything_missing_not_complete(test_db, monkeypatch):
    """An empty destination walk means nothing is PLACED, not that nothing is MISSING.
    get_assets_files returns (None, None) when it finds nothing; that used to skip the match
    pass entirely and store an all-zero '100% complete' summary, hiding a down/empty mount."""
    service = UnmatchedAssetsService(test_db)

    monkeypatch.setattr(
        "services.unmatched_assets.get_assets_files",
        lambda _source_dirs, merge=False, exclude_artwork=False, artwork_out=None: (None, None),
    )

    media_dict = {
        "movies": [
            {
                "title": "Some Movie", "normalized_title": "somemovie", "alternate_titles": [],
                "normalized_alternate_titles": [], "year": 2024, "tmdb_id": 101, "tvdb_id": None,
                "imdb_id": None, "status": "released", "instance": "Plex A",
            },
        ],
        "series": [],
        "collections": [],
    }

    result = service.detect_unmatched(media_dict, ["/tmp/organized"])

    assert result["summary"]["movies"]["total"] == 1
    assert result["summary"]["movies"]["unmatched"] == 1
    assert result["summary"]["grand_total"]["percent_complete"] == 0.0
    assert [m["title"] for m in result["unmatched"]["movies"]] == ["Some Movie"]

    saved = json.loads(
        test_db.query(Setting).filter(Setting.key == "poster_unmatched_stats").first().value
    )
    assert saved["summary"]["grand_total"]["unmatched"] == 1


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

    def _fake_get_assets_files(_source_dirs, merge=False, exclude_artwork=False, artwork_out=None):
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

    def _fake_get_assets_files(_source_dirs, merge=False, exclude_artwork=False, artwork_out=None):
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

    def _fake_get_assets_files(_source_dirs, merge=False, exclude_artwork=False, artwork_out=None):
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

    def _fake_get_assets_files(_source_dirs, merge=False, exclude_artwork=False, artwork_out=None):
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

    def _fake_get_assets_files(_source_dirs, merge=False, exclude_artwork=False, artwork_out=None):
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

    def _fake_get_assets_files(_source_dirs, merge=False, exclude_artwork=False, artwork_out=None):
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

    def _fake_get_assets_files(_source_dirs, merge=False, exclude_artwork=False, artwork_out=None):
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


# ── Artwork unmatched detection (same service, artwork slots) ────────────────


def test_get_cached_artwork_results_empty_when_missing(test_db):
    payload = UnmatchedAssetsService(test_db).get_cached_artwork_results()
    assert payload["last_run"] is None
    for t in ("logo", "background", "squareart"):
        assert payload[t]["summary"]["grand_total"]["total"] == 0


def test_unified_pass_reports_only_items_missing_the_artwork_type(test_db, tmp_path):
    """One movie has a placed logo in the destination, the other doesn't. The unified pass
    (artwork_types set) must flag only the one missing it, per type, and persist a payload
    the UI can read back."""
    dest = tmp_path / "assets"
    has_logo = dest / "Matched Movie (2024) {tmdb-101}"
    has_logo.mkdir(parents=True)
    (has_logo / "logo.png").write_bytes(b"img")

    service = UnmatchedAssetsService(test_db)
    media_dict = {
        "movies": [
            {"title": "Matched Movie", "normalized_title": "matchedmovie",
             "alternate_titles": [], "normalized_alternate_titles": [], "year": 2024,
             "tmdb_id": 101, "status": "released", "has_file": True, "instance": "Radarr A"},
            {"title": "Missing Logo Movie", "normalized_title": "missinglogomovie",
             "alternate_titles": [], "normalized_alternate_titles": [], "year": 2023,
             "tmdb_id": 202, "status": "released", "has_file": True, "instance": "Radarr A"},
        ],
        "series": [], "collections": [],
    }

    payload = service.detect_unmatched(media_dict, [str(dest)], artwork_types=["logo", "background", "squareart"], asset_folders=True)["artwork"]

    logo = payload["logo"]
    unmatched_titles = {m["title"] for m in logo["unmatched"]["movies"]}
    assert unmatched_titles == {"Missing Logo Movie"}
    assert logo["summary"]["movies"]["total"] == 2
    assert logo["summary"]["movies"]["unmatched"] == 1
    # No seasons dimension for artwork.
    assert logo["summary"]["seasons"] == {"total": 0, "unmatched": 0, "percent_complete": 100.0}
    # Neither has a background -> both missing that type.
    assert payload["background"]["summary"]["movies"]["unmatched"] == 2

    # Persisted and readable via the cache accessor.
    assert service.get_cached_artwork_results()["logo"]["summary"]["movies"]["unmatched"] == 1


def test_detect_unmatched_slot_aware_checks_posters_and_artwork_in_one_pass(test_db, tmp_path):
    """artwork_types set: the SAME call reports missing posters AND missing artwork slots,
    saving both payloads — the renamer's slot model applied to detection."""
    dest = tmp_path / "assets"
    both = dest / "Has Both (2024) {tmdb-101}"
    both.mkdir(parents=True)
    (both / "poster.jpg").write_bytes(b"img")
    (both / "logo.png").write_bytes(b"img")
    poster_only = dest / "Poster Only (2023) {tmdb-202}"
    poster_only.mkdir(parents=True)
    (poster_only / "poster.jpg").write_bytes(b"img")

    service = UnmatchedAssetsService(test_db)
    media_dict = {
        "movies": [
            {"title": "Has Both", "normalized_title": "hasboth", "alternate_titles": [],
             "normalized_alternate_titles": [], "year": 2024, "tmdb_id": 101,
             "status": "released", "has_file": True, "instance": "Radarr A"},
            {"title": "Poster Only", "normalized_title": "posteronly", "alternate_titles": [],
             "normalized_alternate_titles": [], "year": 2023, "tmdb_id": 202,
             "status": "released", "has_file": True, "instance": "Radarr A"},
            {"title": "Nothing", "normalized_title": "nothing", "alternate_titles": [],
             "normalized_alternate_titles": [], "year": 2022, "tmdb_id": 303,
             "status": "released", "has_file": True, "instance": "Radarr A"},
        ],
        "series": [], "collections": [],
    }

    result = service.detect_unmatched(media_dict, [str(dest)], artwork_types=["logo", "background", "squareart"], asset_folders=True)

    # Posters: only "Nothing" lacks a poster.
    poster_missing = {m["title"] for m in result["unmatched"]["movies"]}
    assert poster_missing == {"Nothing"}

    # Artwork rode the same pass: only "Has Both" has a logo.
    logo_missing = {m["title"] for m in result["artwork"]["logo"]["unmatched"]["movies"]}
    assert logo_missing == {"Poster Only", "Nothing"}

    # Both payloads persisted and independently readable.
    assert service.get_cached_results()["summary"]["movies"]["unmatched"] == 1
    assert service.get_cached_artwork_results()["logo"]["summary"]["movies"]["unmatched"] == 2


def test_artwork_types_scopes_which_types_are_checked(test_db, tmp_path):
    """artwork_types limits the checked types; types the user hasn't enabled keep their cached
    stats instead of being wiped (so switching scope still shows their last result)."""
    dest = tmp_path / "assets"
    (dest / "Movie A (2024) {tmdb-101}").mkdir(parents=True)  # no artwork placed
    service = UnmatchedAssetsService(test_db)
    media = {
        "movies": [{"title": "Movie A", "normalized_title": "moviea", "alternate_titles": [],
                    "normalized_alternate_titles": [], "year": 2024, "tmdb_id": 101,
                    "status": "released", "has_file": True, "instance": "R"}],
        "series": [], "collections": [],
    }

    # Run 1: check background only.
    service.detect_unmatched(media, [str(dest)], check_posters=False, artwork_types=["background"])
    after_1 = service.get_cached_artwork_results()
    assert after_1["background"]["last_run"] is not None
    assert after_1["logo"]["last_run"] is None  # never checked
    bg_last = after_1["background"]["last_run"]

    # Run 2: check logo only — background must be preserved, not re-run.
    service.detect_unmatched(media, [str(dest)], check_posters=False, artwork_types=["logo"])
    after_2 = service.get_cached_artwork_results()
    assert after_2["logo"]["last_run"] is not None          # now checked
    assert after_2["background"]["last_run"] == bg_last      # preserved from run 1


def test_check_posters_false_leaves_poster_stats_untouched(test_db, tmp_path):
    """Artwork-only selection (posters not enabled) returns no poster summary and never writes
    poster_unmatched_stats."""
    dest = tmp_path / "assets"
    dest.mkdir()
    service = UnmatchedAssetsService(test_db)
    media = {"movies": [], "series": [], "collections": []}

    result = service.detect_unmatched(media, [str(dest)], check_posters=False, artwork_types=["logo"])

    assert "summary" not in result           # no poster result
    assert "artwork" in result
    assert test_db.query(Setting).filter(Setting.key == "poster_unmatched_stats").first() is None


def _movie(title, norm, year, tmdb_id):
    return {"title": title, "normalized_title": norm, "alternate_titles": [],
            "normalized_alternate_titles": [], "year": year, "tmdb_id": tmdb_id,
            "status": "released", "has_file": True, "instance": "Radarr A"}


def test_unified_both_run_logs_poster_line_and_compact_artwork_line(test_db, tmp_path):
    """Both sides: poster line unchanged + a compact indented artwork line under it, one library
    pass, combined stats table, and per-type artwork storage unchanged."""
    from loguru import logger
    dest = tmp_path / "assets"
    both = dest / "Has Both (2024) {tmdb-101}"
    both.mkdir(parents=True)
    (both / "poster.jpg").write_bytes(b"img")
    (both / "logo.png").write_bytes(b"img")

    media_dict = {"movies": [_movie("Has Both", "hasboth", 2024, 101),
                             _movie("Nothing", "nothing", 2022, 303)],
                  "series": [], "collections": []}

    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        result = UnmatchedAssetsService(test_db).detect_unmatched(
            media_dict, [str(dest)], artwork_types=["logo", "background", "squareart"], asset_folders=True)
    finally:
        logger.remove(sink)
    text = "\n".join(lines)

    # Poster line unchanged.
    assert "✓ Matched: Has Both (2024) via ID (101)" in text
    assert "✗ No match: Nothing (2022)" in text
    # Compact artwork line under each poster line (no title repeat).
    assert "↳ artwork — matched: logo · missing: background, squareart" in text
    assert "↳ artwork — matched: none · missing: logo, background, squareart" in text
    # The old combined-status-dots format must NOT appear.
    assert "poster ✓" not in text
    # One combined stats table with per-type columns.
    assert "Statistics" in text and "Poster" in text and "Logo" in text
    # Storage unchanged (per type).
    assert result["artwork"]["logo"]["summary"]["movies"]["unmatched"] == 1
    assert result["artwork"]["background"]["summary"]["movies"]["unmatched"] == 2


def test_unified_artwork_only_self_contained_line_with_reason(test_db, tmp_path):
    """Artwork-only run: no poster line, so the artwork line carries title/year + via-reason."""
    from loguru import logger
    dest = tmp_path / "assets"
    both = dest / "Has Both (2024) {tmdb-101}"
    both.mkdir(parents=True)
    (both / "logo.png").write_bytes(b"img")

    media_dict = {"movies": [_movie("Has Both", "hasboth", 2024, 101)], "series": [], "collections": []}

    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        result = UnmatchedAssetsService(test_db).detect_unmatched(
            media_dict, [str(dest)], check_posters=False, artwork_types=["logo", "background"], asset_folders=True)
    finally:
        logger.remove(sink)
    text = "\n".join(lines)

    # Reason now comes from the shared matcher ("by tmdb_id"), rendered without the "by ".
    assert "Has Both (2024) via tmdb_id — matched: logo · missing: background" in text
    # No poster line and no poster section banner.
    assert "✓ Matched:" not in text
    assert "Unmatched Assets Detection Starting" not in text
    assert "artwork" in result


def test_unified_single_walk_does_not_rescan_for_artwork(test_db, tmp_path, monkeypatch):
    """The both-case builds artwork from the poster walk — get_assets_files runs once and
    scan_destination_artwork is not called."""
    import services.unmatched_assets as ua
    dest = tmp_path / "assets"
    both = dest / "Has Both (2024) {tmdb-101}"
    both.mkdir(parents=True)
    (both / "poster.jpg").write_bytes(b"img")
    (both / "logo.png").write_bytes(b"img")

    calls = {"gaf": 0, "scan": 0}
    real_gaf, real_scan = ua.get_assets_files, ua.scan_destination_artwork
    monkeypatch.setattr(ua, "get_assets_files", lambda *a, **k: (calls.__setitem__("gaf", calls["gaf"] + 1), real_gaf(*a, **k))[1])
    monkeypatch.setattr(ua, "scan_destination_artwork", lambda *a, **k: (calls.__setitem__("scan", calls["scan"] + 1), real_scan(*a, **k))[1])

    media_dict = {"movies": [_movie("Has Both", "hasboth", 2024, 101)], "series": [], "collections": []}
    UnmatchedAssetsService(test_db).detect_unmatched(
        media_dict, [str(dest)], artwork_types=["logo", "background", "squareart"], asset_folders=True)

    assert calls["gaf"] == 1
    assert calls["scan"] == 0


def test_unified_skipped_item_logs_once_no_artwork_line(test_db, tmp_path):
    """An ineligible item logs a single Skipping line and no artwork line."""
    from loguru import logger
    dest = tmp_path / "assets"
    dest.mkdir(parents=True)

    media_dict = {"movies": [
        {"title": "Announced", "normalized_title": "announced", "alternate_titles": [],
         "normalized_alternate_titles": [], "year": 2030, "tmdb_id": 999,
         "status": "announced", "has_file": False, "instance": "Radarr A"},
    ], "series": [], "collections": []}

    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        UnmatchedAssetsService(test_db).detect_unmatched(
            media_dict, [str(dest)], artwork_types=["logo"], asset_folders=True)
    finally:
        logger.remove(sink)
    text = "\n".join(lines)
    assert text.count("Skipping Announced") == 1
    assert "↳ artwork" not in text
    assert "matched:" not in text  # no artwork line for the skipped item


def test_unified_combined_stats_table_has_column_per_type(test_db, tmp_path):
    """The end-of-run table is one box with a column per enabled asset type (poster + artwork)."""
    from loguru import logger
    dest = tmp_path / "assets"
    both = dest / "Has Both (2024) {tmdb-101}"
    both.mkdir(parents=True)
    (both / "poster.jpg").write_bytes(b"img")
    (both / "logo.png").write_bytes(b"img")

    media_dict = {"movies": [_movie("Has Both", "hasboth", 2024, 101)], "series": [], "collections": []}

    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        UnmatchedAssetsService(test_db).detect_unmatched(
            media_dict, [str(dest)], artwork_types=["logo", "background", "squareart"], asset_folders=True)
    finally:
        logger.remove(sink)
    header = next((ln for ln in lines if "| Type" in ln and "Poster" in ln), None)
    assert header is not None
    for col in ("Poster", "Logo", "Background", "Squareart"):
        assert col in header

    # A Percent row closes out the table, one completion figure per asset type.
    percent_row = next((ln for ln in lines if "| Percent" in ln), None)
    assert percent_row is not None
    # Only movie has a poster + logo + background, none have squareart.
    assert "100.00%" in percent_row and "0.00%" in percent_row
