"""Upload cache semantics: record trust/invalidation, editions (radarr upgrades,
reverts, multi-library), library keys, and rating-key re-add detection."""

import json
from pathlib import Path

from models.setting import Setting
from models.plex_upload import PlexUploadRecord
from services.plex_upload import PlexUploadService
from plex_upload_fakes import _FakePlexItem, _SimplePlex


def test_extract_edition_from_radarr_path_returns_edition_when_token_present():
    from modules.upload import _extract_edition_from_radarr_path

    assert _extract_edition_from_radarr_path("/movies/Aliens (1986) {edition-Extended Cut}/Aliens.mkv") == "Extended Cut"
    assert _extract_edition_from_radarr_path("/movies/Aliens (1986) {edition-Extended}/Aliens.mkv") == "Extended"
    assert _extract_edition_from_radarr_path("Aliens (1986) {EDITION-Director's Cut}/file.mkv") == "Director's Cut"


def test_extract_edition_from_radarr_path_returns_none_when_no_token():
    from modules.upload import _extract_edition_from_radarr_path

    assert _extract_edition_from_radarr_path("/movies/Aliens (1986)/Aliens.mkv") is None
    assert _extract_edition_from_radarr_path("") is None
    assert _extract_edition_from_radarr_path("   ") is None


def test_parse_arr_webhook_payload_captures_is_upgrade_and_movie_file_path():
    from modules.upload import _parse_arr_webhook_payload

    payload = {
        "eventType": "Download",
        "isUpgrade": True,
        "movie": {"title": "Aliens", "year": 1986, "tmdbId": 679, "imdbId": "tt0090605"},
        "movieFile": {"path": "/movies/Aliens (1986) {edition-Extended Cut}/Aliens.1986.mkv"},
    }
    result = _parse_arr_webhook_payload(payload)

    assert result["is_upgrade"] is True
    assert result["movie_file_path"] == "/movies/Aliens (1986) {edition-Extended Cut}/Aliens.1986.mkv"


def test_parse_arr_webhook_payload_is_upgrade_false_when_not_set():
    from modules.upload import _parse_arr_webhook_payload

    payload = {
        "eventType": "Download",
        "movie": {"title": "Aliens", "year": 1986, "tmdbId": 679},
    }
    result = _parse_arr_webhook_payload(payload)

    assert result["is_upgrade"] is False
    assert result["movie_file_path"] == ""


def test_parse_arr_webhook_payload_uses_relative_path_fallback():
    from modules.upload import _parse_arr_webhook_payload

    payload = {
        "eventType": "Download",
        "isUpgrade": True,
        "movie": {"title": "Aliens", "year": 1986, "tmdbId": 679},
        "movieFile": {"relativePath": "Aliens (1986) {edition-Extended}/Aliens.mkv"},
    }
    result = _parse_arr_webhook_payload(payload)

    assert result["is_upgrade"] is True
    assert result["movie_file_path"] == "Aliens (1986) {edition-Extended}/Aliens.mkv"


def test_handle_radarr_upgrade_clears_cache_when_new_edition_detected(test_db, tmp_path):
    """Cache should be cleared when Radarr upgrade contains an edition not yet in the upload records."""
    (tmp_path / "Aliens (1986)").mkdir()
    poster = tmp_path / "Aliens (1986)" / "poster.jpg"
    poster.write_bytes(b"fake")

    from models.setting import upsert_setting as _upsert_setting
    _upsert_setting(test_db, "poster_destination", str(tmp_path))
    test_db.commit()

    record = PlexUploadRecord(
        file_path=str(poster),
        uploaded_to_libraries=json.dumps(["Movies"]),
        uploaded_to_library_keys=json.dumps(["abc123"]),
        uploaded_editions=json.dumps(["Extended"]),
        uploaded_media_types=json.dumps(["movies"]),
        file_hash=None,  # no hash: accepted as-is without comparison
    )
    test_db.add(record)
    test_db.commit()

    service = PlexUploadService(test_db)
    from modules.upload import _handle_radarr_upgrade_edition_check

    parsed_payload = {
        "source": "radarr",
        "is_upgrade": True,
        "movie_file_path": "/movies/Aliens (1986) {edition-Extended Cut}/Aliens.mkv",
        "year": 1986,
        "tmdb_id": 679,
        "tvdb_id": None,
        "imdb_id": "tt0090605",
    }

    _handle_radarr_upgrade_edition_check(service, parsed_payload, "movie", "Aliens")

    # DB record for this file should have been removed (cache cleared)
    remaining = test_db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == str(poster)).first()
    assert remaining is None
    # And matching is now constrained to the new edition so we wait for Plex to rescan.
    assert service._expected_edition == "Extended Cut"


def test_handle_radarr_upgrade_skips_clear_when_edition_already_cached(test_db, tmp_path):
    """Cache should NOT be cleared when the Radarr upgrade edition is already recorded."""
    (tmp_path / "Aliens (1986)").mkdir()
    poster = tmp_path / "Aliens (1986)" / "poster.jpg"
    poster.write_bytes(b"fake")

    from models.setting import upsert_setting as _upsert_setting
    _upsert_setting(test_db, "poster_destination", str(tmp_path))
    test_db.commit()

    record = PlexUploadRecord(
        file_path=str(poster),
        uploaded_to_libraries=json.dumps(["Movies"]),
        uploaded_to_library_keys=json.dumps(["abc123"]),
        uploaded_editions=json.dumps(["Extended Cut"]),
        uploaded_media_types=json.dumps(["movies"]),
        file_hash=None,  # no hash: accepted as-is without comparison
    )
    test_db.add(record)
    test_db.commit()

    service = PlexUploadService(test_db)
    from modules.upload import _handle_radarr_upgrade_edition_check

    parsed_payload = {
        "source": "radarr",
        "is_upgrade": True,
        "movie_file_path": "/movies/Aliens (1986) {edition-Extended Cut}/Aliens.mkv",
        "year": 1986,
        "tmdb_id": None,
        "tvdb_id": None,
        "imdb_id": None,
    }

    _handle_radarr_upgrade_edition_check(service, parsed_payload, "movie", "Aliens")

    # Record should still be present — same edition, no cache clear needed
    remaining = test_db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == str(poster)).first()
    assert remaining is not None
    assert "Extended Cut" in json.loads(remaining.uploaded_editions)


def test_handle_radarr_upgrade_skips_action_when_no_edition_in_path(test_db, tmp_path):
    """When the upgrade path has no edition token it is a quality-only upgrade — cache is left alone."""
    (tmp_path / "Aliens (1986)").mkdir()
    poster = tmp_path / "Aliens (1986)" / "poster.jpg"
    poster.write_bytes(b"fake")

    from models.setting import upsert_setting as _upsert_setting
    _upsert_setting(test_db, "poster_destination", str(tmp_path))
    test_db.commit()

    record = PlexUploadRecord(
        file_path=str(poster),
        uploaded_to_libraries=json.dumps(["Movies"]),
        uploaded_to_library_keys=json.dumps(["abc123"]),
        uploaded_editions=json.dumps(["default_edition"]),
        uploaded_media_types=json.dumps(["movies"]),
        file_hash=None,
    )
    test_db.add(record)
    test_db.commit()

    service = PlexUploadService(test_db)
    from modules.upload import _handle_radarr_upgrade_edition_check

    parsed_payload = {
        "source": "radarr",
        "is_upgrade": True,
        "movie_file_path": "/movies/Aliens (1986)/Aliens.mkv",  # no {edition-*} token
        "year": 1986,
        "tmdb_id": None,
        "tvdb_id": None,
        "imdb_id": None,
    }

    _handle_radarr_upgrade_edition_check(service, parsed_payload, "movie", "Aliens")

    # Cache should be untouched — quality-only upgrade, same default_edition
    remaining = test_db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == str(poster)).first()
    assert remaining is not None
    assert "default_edition" in json.loads(remaining.uploaded_editions)


def test_handle_radarr_upgrade_clears_cache_when_edition_removed(test_db, tmp_path):
    """When a movie loses its edition (edition → no-edition), the cache should be cleared so that
    Plex's new no-edition library entry receives a poster upload."""
    (tmp_path / "Aliens (1986)").mkdir()
    poster = tmp_path / "Aliens (1986)" / "poster.jpg"
    poster.write_bytes(b"fake")

    from models.setting import upsert_setting as _upsert_setting
    _upsert_setting(test_db, "poster_destination", str(tmp_path))
    test_db.commit()

    # Cache records a prior upload for the edition version.
    record = PlexUploadRecord(
        file_path=str(poster),
        uploaded_to_libraries=json.dumps(["Movies"]),
        uploaded_to_library_keys=json.dumps(["abc123"]),
        uploaded_editions=json.dumps(["Extended Cut"]),  # real edition previously cached
        uploaded_media_types=json.dumps(["movies"]),
        file_hash=None,
    )
    test_db.add(record)
    test_db.commit()

    service = PlexUploadService(test_db)
    from modules.upload import _handle_radarr_upgrade_edition_check

    # New file path has no {edition-*} token — edition was removed.
    parsed_payload = {
        "source": "radarr",
        "is_upgrade": True,
        "movie_file_path": "/movies/Aliens (1986)/Aliens.1986.mkv",
        "year": 1986,
        "tmdb_id": 679,
        "tvdb_id": None,
        "imdb_id": "tt0090605",
    }

    _handle_radarr_upgrade_edition_check(service, parsed_payload, "movie", "Aliens")

    # DB record should have been cleared so the no-edition Plex entry gets a poster.
    remaining = test_db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == str(poster)).first()
    assert remaining is None
    # Matching now targets the no-edition item (waits for Plex to create it).
    assert service._expected_edition == PlexUploadService.DEFAULT_EDITION_MOVIE


def test_upload_asset_defers_until_expected_edition_present(test_db, monkeypatch):
    """An edition-change upgrade must not land the poster on the old-edition item:
    while only the old edition exists in Plex, matching defers (matched=False) so the
    webhook retries; once the new-edition item appears, it matches and uploads."""
    service = PlexUploadService(test_db)

    old = _SimplePlex("movie", "Aliens", key="100")
    old.editionTitle = "Theatrical"
    new = _SimplePlex("movie", "Aliens", key="200")
    new.editionTitle = "Extended Cut"

    movies_marker = object()
    index = {"movies": movies_marker, "shows": {}, "collections": {}}
    current_items: list = []

    monkeypatch.setattr(service, "_resolve_target_media_type", lambda *a, **k: ("movie", "id-match"))
    monkeypatch.setattr(service, "_asset_has_arr_availability", lambda *a, **k: (True, None))
    monkeypatch.setattr(
        service, "_resolve_index_candidates",
        lambda sub, *a, **k: list(current_items) if sub is movies_marker else [],
    )

    asset = {"media_key": "aliens", "path": "/x/aliens.jpg", "asset_type": "main",
             "display_name": "Aliens", "folder_year": 1986}

    service.set_expected_edition("Extended Cut")

    # Only the old edition present -> deferred (no match), so the webhook will retry.
    current_items = [old]
    uploaded, matched, *_ = service._upload_asset(asset, index, dry_run=True)
    assert matched is False
    assert uploaded == 0

    # New edition now scanned in -> matches and uploads (to the new item only).
    current_items = [old, new]
    uploaded, matched, *_ = service._upload_asset(asset, index, dry_run=True)
    assert matched is True
    assert uploaded == 1


def test_plex_upload_cache_trusts_record_without_hash(test_db, tmp_path):
    """Records with file_hash=None (e.g. inserted without a hash) should be trusted without comparison.

    The hash check is skipped when file_hash is NULL, so existing upload data is preserved
    and no spurious re-upload occurs.
    """
    file_path = tmp_path / "The Matrix (1999)" / "poster.jpg"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"existing-poster-bytes")

    test_db.add(PlexUploadRecord(
        file_path=str(file_path),
        file_hash=None,  # no hash stored
        uploaded_to_libraries=json.dumps(["Movies"]),
        uploaded_to_library_keys=json.dumps(["serverid:/library/sections/1"]),
        uploaded_editions=json.dumps(["default_edition"]),
        uploaded_media_types=json.dumps(["movies"]),
    ))
    test_db.commit()

    service = PlexUploadService(test_db)

    record = service._get_uploaded_record(str(file_path))
    # Entry with no stored hash should be trusted; libraries/editions must be preserved.
    assert record["uploaded_to_libraries"] == ["Movies"]
    assert record["uploaded_to_library_keys"] == ["serverid:/library/sections/1"]
    assert record["uploaded_editions"] == ["default_edition"]
    assert record["uploaded_media_types"] == ["movies"]


def test_plex_upload_cache_invalidates_when_stored_signature_changes(test_db, tmp_path):
    """Cache entries with a stored file_hash must be invalidated when the file changes."""
    file_path = tmp_path / "Inception (2010)" / "poster.jpg"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"original-bytes")

    # Compute hash for the original file and save it as a DB record.
    service_tmp = PlexUploadService(test_db)
    old_hash = service_tmp._compute_file_hash(str(file_path))
    assert old_hash is not None

    test_db.add(PlexUploadRecord(
        file_path=str(file_path),
        file_hash=old_hash,
        uploaded_to_libraries=json.dumps(["Movies"]),
        uploaded_to_library_keys=json.dumps(["serverid:/library/sections/1"]),
        uploaded_editions=json.dumps(["default_edition"]),
        uploaded_media_types=json.dumps(["movies"]),
    ))
    test_db.add(Setting(key="plex_upload_records_migrated", value="1"))
    test_db.commit()

    # Overwrite the file so the stored hash no longer matches.
    file_path.write_bytes(b"updated-poster-bytes-that-are-longer")

    service = PlexUploadService(test_db)

    record = service._get_uploaded_record(str(file_path))
    # Stored hash exists but mismatches → cache should be cleared.
    assert record["uploaded_to_libraries"] == []
    assert record["uploaded_to_library_keys"] == []
    assert record["uploaded_editions"] == []
    assert record["uploaded_media_types"] == []

    # Verify the new hash is different from the original.
    new_hash = service._compute_file_hash(str(file_path))
    assert new_hash is not None
    assert new_hash != old_hash


def test_plex_upload_cache_persist_prunes_missing_files_only(test_db, tmp_path):
    """Persist should keep DB records for existing files and prune stale missing-file records."""
    existing_file = tmp_path / "Inception (2010)" / "poster.jpg"
    existing_file.parent.mkdir(parents=True, exist_ok=True)
    existing_file.write_bytes(b"poster-bytes")

    missing_file = tmp_path / "Old Movie (2001)" / "poster.jpg"

    # Mark migration done so _ensure_migrated does not re-import.
    test_db.add(Setting(key="plex_upload_records_migrated", value="1"))

    def _add_records():
        for fp in [str(existing_file), str(missing_file)]:
            test_db.add(PlexUploadRecord(
                file_path=fp,
                file_hash=None,
                uploaded_to_libraries=json.dumps(["Plex"]),
                uploaded_to_library_keys=json.dumps([]),
                uploaded_editions=json.dumps(["default_edition"]),
                uploaded_media_types=json.dumps(["movies"]),
            ))
        test_db.commit()

    _add_records()

    # _clear_upload_cache should delete all records regardless of file existence.
    service = PlexUploadService(test_db)
    service._clear_upload_cache()

    remaining = test_db.query(PlexUploadRecord).all()
    assert len(remaining) == 0

    # Re-seed and verify _persist_upload_cache keeps existing and prunes missing.
    _add_records()

    service2 = PlexUploadService(test_db)
    service2._mark_uploaded(str(existing_file), library_name="Plex", edition_title="default_edition", media_type="movies")
    service2._persist_upload_cache()

    paths_in_db = {r.file_path for r in test_db.query(PlexUploadRecord).all()}
    assert str(existing_file) in paths_in_db
    assert str(missing_file) not in paths_in_db


def test_movie_cache_uses_library_keys_not_legacy_library_name(test_db):
    """Legacy cache names should not suppress uploads when stable library keys are available."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "startrek",
        "path": "/tmp/posters/Star Trek/poster.jpg",
        "display_name": "Star Trek",
        "asset_type": "main",
    }
    index = {
        "movies": {
            "startrek": [
                _FakePlexItem(
                    "movie",
                    "Star Trek",
                    2009,
                    "Movies",
                    section_id=1,
                    server_id="plex-a",
                    rating_key="rk-1",
                ),
                _FakePlexItem(
                    "movie",
                    "Star Trek",
                    2009,
                    "Movies",
                    section_id=2,
                    server_id="plex-a",
                    rating_key="rk-2",
                ),
            ]
        },
        "shows": {},
        "collections": {},
    }

    test_db.add(Setting(key="plex_upload_records_migrated", value="1"))
    test_db.add(PlexUploadRecord(
        file_path=asset["path"],
        file_hash=None,
        uploaded_to_libraries=json.dumps(["Movies"]),
        uploaded_to_library_keys=json.dumps([]),
        uploaded_editions=json.dumps([]),
        uploaded_media_types=json.dumps(["movies"]),
    ))
    test_db.commit()

    outcome = service._upload_asset(
        asset,
        index,
        dry_run=True,
    )

    assert outcome.uploaded == 2
    assert outcome.matched is True
    assert outcome.plex_targets == 2
    assert outcome.media_counts["movies"] == 2


def test_movie_default_edition_cache_is_scoped_per_library_key(test_db):
    """Default-edition cache should not suppress uploads to a different Plex library key."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "duallibmovie",
        "path": "/tmp/posters/Dual Lib Movie/poster.jpg",
        "display_name": "Dual Lib Movie",
        "asset_type": "main",
    }
    index = {
        "movies": {
            "duallibmovie": [
                _FakePlexItem(
                    "movie",
                    "Dual Lib Movie",
                    2026,
                    "Movies",
                    section_id=1,
                    server_id="plex-a",
                    rating_key="rk-1",
                ),
                _FakePlexItem(
                    "movie",
                    "Dual Lib Movie",
                    2026,
                    "4k Movies",
                    section_id=20,
                    server_id="plex-a",
                    rating_key="rk-20",
                ),
            ]
        },
        "shows": {},
        "collections": {},
    }

    test_db.add(Setting(key="plex_upload_records_migrated", value="1"))
    test_db.add(PlexUploadRecord(
        file_path=asset["path"],
        file_hash=None,
        uploaded_to_libraries=json.dumps(["Movies"]),
        uploaded_to_library_keys=json.dumps(["plex-a:1"]),
        uploaded_editions=json.dumps(["default_edition"]),
        uploaded_media_types=json.dumps(["movies"]),
    ))
    test_db.commit()

    outcome = service._upload_asset(
        asset,
        index,
        dry_run=True,
        media_type_filter="movie",
    )

    assert outcome.uploaded == 1
    assert outcome.matched is True
    assert outcome.plex_targets == 2
    assert outcome.media_counts["movies"] == 1


def test_is_single_target_fully_cached_movie_requires_all_library_keys(test_db):
    """Webhook cache gate should require movie cache coverage for every matched library key."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "duallibmovie",
        "path": "/tmp/posters/Dual Lib Movie/poster.jpg",
        "display_name": "Dual Lib Movie",
        "asset_type": "main",
        "season_number": None,
    }
    index = {
        "movies": {
            "duallibmovie": [
                _FakePlexItem(
                    "movie",
                    "Dual Lib Movie",
                    2026,
                    "Movies",
                    section_id=1,
                    server_id="plex-a",
                    rating_key="rk-1",
                ),
                _FakePlexItem(
                    "movie",
                    "Dual Lib Movie",
                    2026,
                    "4k Movies",
                    section_id=20,
                    server_id="plex-a",
                    rating_key="rk-20",
                ),
            ]
        },
        "shows": {},
        "collections": {},
    }

    test_db.add(Setting(key="plex_upload_records_migrated", value="1"))
    test_db.add(PlexUploadRecord(
        file_path=asset["path"],
        file_hash=None,
        uploaded_to_libraries=json.dumps(["Movies"]),
        uploaded_to_library_keys=json.dumps(["plex-a:1"]),
        uploaded_editions=json.dumps(["default_edition"]),
        uploaded_media_types=json.dumps(["movies"]),
    ))
    test_db.commit()

    fully_cached = service._is_asset_fully_cached_for_targets(
        asset,
        index=index,
        media_type_filter="movie",
        arr_availability={"movies": {}, "shows": {}},
    )

    assert fully_cached is False


def test_mark_uploaded_persists_library_key(test_db, tmp_path):
    """Uploader cache should persist stable library identity keys when available."""
    file_path = tmp_path / "The Matrix (1999)" / "poster.jpg"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"poster")

    service = PlexUploadService(test_db)
    service._mark_uploaded(
        str(file_path),
        library_name="Movies",
        library_key="plex-main:1",
        edition_title="default_edition",
        media_type="movies",
    )
    service._persist_upload_cache()

    record = test_db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == str(file_path)).first()
    assert record is not None
    assert json.loads(record.uploaded_to_libraries) == ["Movies"]
    assert json.loads(record.uploaded_to_library_keys) == ["plex-main:1"]


def test_movie_reverted_edition_reuploads_and_prunes_stale(test_db, tmp_path):
    """A movie reverted from a special edition back to default re-uploads and drops the stale edition."""
    file_path = tmp_path / "Movie X (2020)" / "poster.jpg"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"x")

    service = PlexUploadService(test_db)
    asset = {
        "media_key": "moviex",
        "path": str(file_path),
        "display_name": "Movie X",
        "asset_type": "main",
        "folder_year": 2020,
    }
    # Live Plex item is default edition (no editionTitle).
    index = {
        "movies": {"moviex": [_FakePlexItem("movie", "Movie X", 2020, "Movies", section_id=1, server_id="plex-a", rating_key="rk-1")]},
        "shows": {},
        "collections": {},
    }
    test_db.add(PlexUploadRecord(
        file_path=str(file_path),
        file_hash=None,
        uploaded_to_libraries=json.dumps(["Movies"]),
        uploaded_to_library_keys=json.dumps(["plex-a:1"]),
        uploaded_to_rating_keys=json.dumps(["rk-1"]),
        uploaded_editions=json.dumps(["default_edition", "Extended Cut"]),
        uploaded_media_types=json.dumps(["movies"]),
    ))
    test_db.commit()

    uploaded, *_rest = service._upload_asset(asset, index, dry_run=False, media_type_filter="movie")

    assert uploaded == 1
    record = test_db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == str(file_path)).first()
    assert json.loads(record.uploaded_editions) == ["default_edition"]


def test_movie_multi_library_editions_not_treated_as_stale(test_db, tmp_path):
    """Editions still present across libraries must not trigger a spurious re-upload."""
    file_path = tmp_path / "Movie X (2020)" / "poster.jpg"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"x")

    service = PlexUploadService(test_db)
    asset = {
        "media_key": "moviex",
        "path": str(file_path),
        "display_name": "Movie X",
        "asset_type": "main",
        "folder_year": 2020,
    }
    default_item = _FakePlexItem("movie", "Movie X", 2020, "Movies", section_id=1, server_id="plex-a", rating_key="rk-1")
    extended_item = _FakePlexItem("movie", "Movie X", 2020, "4k Movies", section_id=2, server_id="plex-a", rating_key="rk-2")
    extended_item.editionTitle = "Extended Cut"
    index = {"movies": {"moviex": [default_item, extended_item]}, "shows": {}, "collections": {}}

    test_db.add(PlexUploadRecord(
        file_path=str(file_path),
        file_hash=None,
        uploaded_to_libraries=json.dumps(["Movies", "4k Movies"]),
        uploaded_to_library_keys=json.dumps(["plex-a:1", "plex-a:2"]),
        uploaded_to_rating_keys=json.dumps(["rk-1", "rk-2"]),
        uploaded_editions=json.dumps(["default_edition", "Extended Cut"]),
        uploaded_media_types=json.dumps(["movies"]),
    ))
    test_db.commit()

    uploaded, *_rest = service._upload_asset(asset, index, dry_run=False, media_type_filter="movie")

    assert uploaded == 0  # both editions still live and cached — nothing to do
    record = test_db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == str(file_path)).first()
    assert set(json.loads(record.uploaded_editions)) == {"default_edition", "Extended Cut"}


def test_two_libraries_one_edition_changes_reuploads_both(test_db, tmp_path):
    """When one library's edition changes, the flat-set cache re-syncs the whole movie:
    both libraries get the (identical) poster re-applied and the cache reflects live editions."""
    file_path = tmp_path / "Movie X (2020)" / "poster.jpg"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"x")

    service = PlexUploadService(test_db)
    asset = {
        "media_key": "moviex",
        "path": str(file_path),
        "display_name": "Movie X",
        "asset_type": "main",
        "folder_year": 2020,
    }
    # Library A used to be default; it is now "Director's Cut". Library B is unchanged.
    changed_item = _FakePlexItem("movie", "Movie X", 2020, "Movies", section_id=1, server_id="plex-a", rating_key="rk-1")
    changed_item.editionTitle = "Director's Cut"
    unchanged_item = _FakePlexItem("movie", "Movie X", 2020, "4k Movies", section_id=2, server_id="plex-a", rating_key="rk-2")
    unchanged_item.editionTitle = "Extended Cut"
    index = {"movies": {"moviex": [changed_item, unchanged_item]}, "shows": {}, "collections": {}}

    test_db.add(PlexUploadRecord(
        file_path=str(file_path),
        file_hash=None,
        uploaded_to_libraries=json.dumps(["Movies", "4k Movies"]),
        uploaded_to_library_keys=json.dumps(["plex-a:1", "plex-a:2"]),
        uploaded_to_rating_keys=json.dumps(["rk-1", "rk-2"]),
        uploaded_editions=json.dumps(["default_edition", "Extended Cut"]),
        uploaded_media_types=json.dumps(["movies"]),
    ))
    test_db.commit()

    uploaded, *_rest = service._upload_asset(asset, index, dry_run=False, media_type_filter="movie")

    # The vanished "default_edition" triggers a whole-movie re-sync, so both items are
    # re-applied (the unchanged library redundantly but harmlessly — same poster).
    assert uploaded == 2
    record = test_db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == str(file_path)).first()
    assert set(json.loads(record.uploaded_editions)) == {"Director's Cut", "Extended Cut"}


def test_movie_reverted_edition_dry_run_preserves_cache(test_db, tmp_path):
    """Dry run reports the re-upload but must not delete the cache record."""
    file_path = tmp_path / "Movie X (2020)" / "poster.jpg"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"x")

    service = PlexUploadService(test_db)
    asset = {
        "media_key": "moviex",
        "path": str(file_path),
        "display_name": "Movie X",
        "asset_type": "main",
        "folder_year": 2020,
    }
    index = {
        "movies": {"moviex": [_FakePlexItem("movie", "Movie X", 2020, "Movies", section_id=1, server_id="plex-a", rating_key="rk-1")]},
        "shows": {},
        "collections": {},
    }
    test_db.add(PlexUploadRecord(
        file_path=str(file_path),
        file_hash=None,
        uploaded_to_libraries=json.dumps(["Movies"]),
        uploaded_to_library_keys=json.dumps(["plex-a:1"]),
        uploaded_to_rating_keys=json.dumps(["rk-1"]),
        uploaded_editions=json.dumps(["default_edition", "Extended Cut"]),
        uploaded_media_types=json.dumps(["movies"]),
    ))
    test_db.commit()

    uploaded, *_rest = service._upload_asset(asset, index, dry_run=True, media_type_filter="movie")

    assert uploaded == 1  # would re-upload
    record = test_db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == str(file_path)).first()
    assert record is not None  # dry run did not delete the record


def test_mark_uploaded_persists_rating_key(test_db, tmp_path):
    """Uploader cache should persist the Plex ratingKey the file was applied to."""
    file_path = tmp_path / "The Matrix (1999)" / "poster.jpg"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"poster")

    service = PlexUploadService(test_db)
    service._mark_uploaded(
        str(file_path),
        library_name="Movies",
        library_key="plex-main:1",
        media_type="movies",
        rating_key="rk-42",
    )

    record = test_db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == str(file_path)).first()
    assert record is not None
    assert json.loads(record.uploaded_to_rating_keys) == ["rk-42"]


def _readded_movie_index(rating_key: str) -> dict:
    return {
        "movies": {
            "duallibmovie": [
                _FakePlexItem(
                    "movie",
                    "Dual Lib Movie",
                    2026,
                    "Movies",
                    section_id=1,
                    server_id="plex-a",
                    rating_key=rating_key,
                ),
            ]
        },
        "shows": {},
        "collections": {},
    }


def _seed_movie_record(test_db, file_path, *, rating_keys):
    test_db.add(PlexUploadRecord(
        file_path=str(file_path),
        file_hash=None,
        uploaded_to_libraries=json.dumps(["Movies"]),
        uploaded_to_library_keys=json.dumps(["plex-a:1"]),
        uploaded_to_rating_keys=json.dumps(rating_keys),
        uploaded_editions=json.dumps(["default_edition"]),
        uploaded_media_types=json.dumps(["movies"]),
    ))
    test_db.commit()


def test_readded_movie_with_new_rating_key_is_reuploaded(test_db, tmp_path):
    """A library-cached movie whose Plex ratingKey changed (deleted + re-added) re-uploads."""
    file_path = tmp_path / "Dual Lib Movie (2026)" / "poster.jpg"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"poster")

    service = PlexUploadService(test_db)
    asset = {
        "media_key": "duallibmovie",
        "path": str(file_path),
        "display_name": "Dual Lib Movie",
        "asset_type": "main",
        "folder_year": 2026,
    }
    _seed_movie_record(test_db, file_path, rating_keys=["rk-OLD"])

    uploaded, matched, *_rest = service._upload_asset(
        asset,
        _readded_movie_index("rk-NEW"),
        dry_run=False,
        media_type_filter="movie",
    )

    assert uploaded == 1
    assert matched is True
    record = test_db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == str(file_path)).first()
    assert "rk-NEW" in json.loads(record.uploaded_to_rating_keys)


def test_unchanged_rating_key_movie_is_skipped(test_db, tmp_path):
    """A library-cached movie whose ratingKey is unchanged is still skipped."""
    file_path = tmp_path / "Dual Lib Movie (2026)" / "poster.jpg"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"poster")

    service = PlexUploadService(test_db)
    asset = {
        "media_key": "duallibmovie",
        "path": str(file_path),
        "display_name": "Dual Lib Movie",
        "asset_type": "main",
        "folder_year": 2026,
    }
    _seed_movie_record(test_db, file_path, rating_keys=["rk-1"])

    uploaded, *_rest = service._upload_asset(
        asset,
        _readded_movie_index("rk-1"),
        dry_run=False,
        media_type_filter="movie",
    )

    assert uploaded == 0


def test_legacy_record_backfills_rating_key_without_reupload(test_db, tmp_path):
    """A legacy record with no rating keys establishes a baseline (backfill) without re-uploading."""
    file_path = tmp_path / "Dual Lib Movie (2026)" / "poster.jpg"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"poster")

    service = PlexUploadService(test_db)
    asset = {
        "media_key": "duallibmovie",
        "path": str(file_path),
        "display_name": "Dual Lib Movie",
        "asset_type": "main",
        "folder_year": 2026,
    }
    _seed_movie_record(test_db, file_path, rating_keys=[])

    uploaded, *_rest = service._upload_asset(
        asset,
        _readded_movie_index("rk-1"),
        dry_run=False,
        media_type_filter="movie",
    )

    assert uploaded == 0  # baseline run does not re-upload existing items
    record = test_db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == str(file_path)).first()
    assert json.loads(record.uploaded_to_rating_keys) == ["rk-1"]


def test_is_asset_fully_cached_returns_false_when_rating_key_changed(test_db, tmp_path):
    """Webhook cache gate should treat a re-added item (new ratingKey) as not cached."""
    file_path = tmp_path / "Dual Lib Movie (2026)" / "poster.jpg"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"poster")

    service = PlexUploadService(test_db)
    asset = {
        "media_key": "duallibmovie",
        "path": str(file_path),
        "display_name": "Dual Lib Movie",
        "asset_type": "main",
        "season_number": None,
        "folder_year": 2026,
    }
    _seed_movie_record(test_db, file_path, rating_keys=["rk-OLD"])

    assert service._is_asset_fully_cached_for_targets(
        asset,
        index=_readded_movie_index("rk-NEW"),
        media_type_filter="movie",
        arr_availability={"movies": {}, "shows": {}},
    ) is False

    service.invalidate_record_cache()
    assert service._is_asset_fully_cached_for_targets(
        asset,
        index=_readded_movie_index("rk-OLD"),
        media_type_filter="movie",
        arr_availability={"movies": {}, "shows": {}},
    ) is True


def _series_show_poster_status(service, asset, index):
    """Drive is_series_show_poster_cached with stubbed context and return the recorded reason."""
    service._prepare_upload_context = lambda *a, **k: (None, Path("/tmp"), index, [])
    service._get_local_assets = lambda *a, **k: [asset]
    service._select_local_assets_for_target = lambda assets, **k: assets
    service._get_arr_availability_index = lambda *a, **k: {}
    service.is_series_show_poster_cached(title="Chicago Fire", year=2012, tvdb_id=258541)
    return service._series_show_poster_status


def test_series_show_poster_status_detects_re_added(test_db, tmp_path):
    """When the show was removed and re-added in Plex (new ratingKey), the status is 're_added'."""
    file_path = tmp_path / "Chicago Fire (2012)" / "poster.jpg"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"x")
    asset = {
        "media_key": "chicagofire",
        "path": str(file_path),
        "display_name": "Chicago Fire",
        "asset_type": "main",
        "season_number": None,
        "folder_year": 2012,
    }
    index = {
        "movies": {},
        "shows": {"chicagofire": [_FakePlexItem("show", "Chicago Fire", 2012, "TV Shows", section_id=1, server_id="plex-a", rating_key="rk-NEW")]},
        "collections": {},
    }
    test_db.add(PlexUploadRecord(
        file_path=str(file_path),
        file_hash=None,
        uploaded_to_libraries=json.dumps(["TV Shows"]),
        uploaded_to_library_keys=json.dumps(["plex-a:1"]),
        uploaded_to_rating_keys=json.dumps(["rk-OLD"]),
        uploaded_editions=json.dumps([]),
        uploaded_media_types=json.dumps(["shows"]),
    ))
    test_db.commit()

    service = PlexUploadService(test_db)
    assert _series_show_poster_status(service, asset, index) == "re_added"


def test_series_show_poster_status_not_uploaded_without_record(test_db, tmp_path):
    """With no prior upload record, the status is 'not_uploaded' (not the re-add reason)."""
    file_path = tmp_path / "Chicago Fire (2012)" / "poster.jpg"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"x")
    asset = {
        "media_key": "chicagofire",
        "path": str(file_path),
        "display_name": "Chicago Fire",
        "asset_type": "main",
        "season_number": None,
        "folder_year": 2012,
    }
    index = {
        "movies": {},
        "shows": {"chicagofire": [_FakePlexItem("show", "Chicago Fire", 2012, "TV Shows", section_id=1, server_id="plex-a", rating_key="rk-NEW")]},
        "collections": {},
    }

    service = PlexUploadService(test_db)
    assert _series_show_poster_status(service, asset, index) == "not_uploaded"
