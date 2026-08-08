"""Webhook preupload rename pass: targeted scope, border tmp, fast path, adopt-existing."""

import json
from pathlib import Path
from datetime import datetime, timezone

from models.setting import Setting
from models.drive import Drive
from models.job import Job
from models.poster import Poster


def test_webhook_preupload_rename_pass_uses_targeted_scope(test_db, monkeypatch):
    """Webhook rename-then-upload prepass should pass single-item target filters to rename service."""
    import modules.upload as plex_upload_module

    captured_kwargs = {}

    class _FakePosterRenameService:
        def __init__(self, _db, **_kwargs):
            pass

        def rename_posters(self, **kwargs):
            captured_kwargs.update(kwargs)
            return {
                "success": True,
                "stats": {"total_matched": 1, "movies": 1, "series": 0, "collections": 0},
            }

    monkeypatch.setattr("modules.upload.PosterRenameService", _FakePosterRenameService)

    test_db.add(Setting(key="poster_destination", value="/tmp/organized"))
    test_db.add(Setting(key="poster_drive_priority", value=json.dumps({"drive_ids": [1]})))
    test_db.add(
        Drive(
            id=1,
            name="MM2K Movies",
            drive_id="drive-1",
            style_type="MM2K",
            subscribed=True,
        )
    )
    job = Job(job_type="Plex Upload Webhook", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()

    parsed_payload = {
        "media_type": "movie",
        "title": "The Matrix",
        "year": 1999,
        "tmdb_id": 603,
        "imdb_id": "tt0133093",
        "tvdb_id": None,
        "season_number": None,
    }

    plex_upload_module._run_webhook_preupload_rename_pass(test_db, job, parsed_payload)

    assert captured_kwargs["target_media_type"] == "movie"
    assert captured_kwargs["target_title"] == "The Matrix"
    assert captured_kwargs["target_year"] == 1999
    assert captured_kwargs["target_tmdb_id"] == 603
    assert captured_kwargs["target_imdb_id"] == "tt0133093"
    assert captured_kwargs["destination_dir"] == "/tmp/organized"
    assert captured_kwargs["use_temp_folder"] is True


def test_webhook_preupload_rename_pass_runs_border_with_tmp_when_enabled(test_db, monkeypatch):
    """Webhook preupload rename pass should run standard border pass from destination/tmp when auto_run_border is enabled."""
    import modules.upload as plex_upload_module

    captured_border_kwargs = {}

    class _FakePlexUploadService:
        def set_arr_instance_scope(self, _arr_instance=None):
            pass

        def __init__(self, _db, **_kwargs):
            pass

        def _discover_local_assets(self, _destination):
            return []

    class _FakePosterRenameService:
        def __init__(self, _db, **_kwargs):
            pass

        def rename_posters(self, **_kwargs):
            return {
                "success": True,
                "stats": {"total_matched": 1, "movies": 1, "series": 0, "collections": 0},
            }

    class _FakeBorderReplacerService:
        def __init__(self, _db, **_kwargs):
            pass

        def process_posters(self, **kwargs):
            captured_border_kwargs.update(kwargs)
            return {"success": True, "changed": 1, "skipped": 0}

    monkeypatch.setattr("modules.upload.PlexUploadService", _FakePlexUploadService)
    monkeypatch.setattr("modules.upload.PosterRenameService", _FakePosterRenameService)
    monkeypatch.setattr("modules.upload.BorderReplacerService", _FakeBorderReplacerService)
    monkeypatch.setattr(
        "modules.upload.os.path.exists",
        lambda path: str(path).endswith("/tmp"),
    )

    test_db.add(Setting(key="poster_destination", value="/tmp/organized"))
    test_db.add(Setting(key="poster_drive_priority", value=json.dumps({"drive_ids": [1]})))
    test_db.add(Setting(key="auto_run_border", value="true"))
    test_db.add(
        Drive(
            id=1,
            name="MM2K Movies",
            drive_id="drive-1",
            style_type="MM2K",
            subscribed=True,
        )
    )

    job = Job(job_type="Plex Upload Webhook", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()

    parsed_payload = {
        "media_type": "movie",
        "title": "The Matrix",
        "year": 1999,
        "tmdb_id": 603,
        "imdb_id": "tt0133093",
        "tvdb_id": None,
        "season_number": None,
    }

    plex_upload_module._run_webhook_preupload_rename_pass(test_db, job, parsed_payload)

    assert captured_border_kwargs["source_dir"] == "/tmp/organized/tmp"
    assert captured_border_kwargs["destination_dir"] == "/tmp/organized"
    assert captured_border_kwargs["dry_run"] is False


def test_webhook_preupload_rename_pass_always_runs_even_when_target_exists_in_destination(test_db, monkeypatch):
    """Webhook preupload rename should still run targeted rename even if destination already has matching assets."""
    import modules.upload as plex_upload_module

    rename_called = {"value": False}

    class _FakePosterRenameService:
        def __init__(self, _db, **_kwargs):
            pass

        def rename_posters(self, **kwargs):
            rename_called["value"] = True
            return {
                "success": True,
                "stats": {"total_matched": 1, "movies": 1, "series": 0, "collections": 0},
            }

    monkeypatch.setattr("modules.upload.PosterRenameService", _FakePosterRenameService)

    test_db.add(Setting(key="poster_destination", value="/tmp/organized"))
    test_db.add(Setting(key="poster_drive_priority", value=json.dumps({"drive_ids": [1]})))
    test_db.add(
        Drive(
            id=1,
            name="MM2K Movies",
            drive_id="drive-1",
            style_type="MM2K",
            subscribed=True,
        )
    )
    job = Job(job_type="Plex Upload Webhook", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()

    parsed_payload = {
        "media_type": "movie",
        "title": "The Matrix",
        "year": 1999,
        "tmdb_id": 603,
        "imdb_id": "tt0133093",
        "tvdb_id": None,
        "season_number": None,
    }

    plex_upload_module._run_webhook_preupload_rename_pass(test_db, job, parsed_payload)

    assert rename_called["value"] is True


def test_webhook_preupload_rename_pass_runs_for_title_only_collection_name_overlap(test_db, monkeypatch):
    """Webhook preupload should still execute targeted rename for title overlaps like movie vs collection naming."""
    import modules.upload as plex_upload_module

    rename_called = {"value": False}

    class _FakePosterRenameService:
        def __init__(self, _db, **_kwargs):
            pass

        def rename_posters(self, **_kwargs):
            rename_called["value"] = True
            return {
                "success": True,
                "stats": {"total_matched": 1, "movies": 1, "series": 0, "collections": 0},
            }

    monkeypatch.setattr("modules.upload.PosterRenameService", _FakePosterRenameService)

    test_db.add(Setting(key="poster_destination", value="/tmp/organized"))
    test_db.add(Setting(key="poster_drive_priority", value=json.dumps({"drive_ids": [1]})))
    test_db.add(
        Drive(
            id=1,
            name="MM2K Movies",
            drive_id="drive-1",
            style_type="MM2K",
            subscribed=True,
        )
    )
    job = Job(job_type="Plex Upload Webhook", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()

    parsed_payload = {
        "media_type": "movie",
        "title": "Zootopia",
        "year": 2016,
        "tmdb_id": 269149,
        "imdb_id": "tt2948356",
        "tvdb_id": None,
        "season_number": None,
    }

    plex_upload_module._run_webhook_preupload_rename_pass(test_db, job, parsed_payload)

    assert rename_called["value"] is True


def test_webhook_preupload_fast_path_skips_when_exact_target_is_already_current(test_db, monkeypatch):
    """Webhook preupload should skip rename/border only when exact-ID destination asset exists and source is unchanged."""
    import modules.upload as plex_upload_module

    rename_called = {"value": False}
    source_file = "/tmp/source/Zootopia (2016) {tmdb-269149} {imdb-tt2948356}.jpg"

    class _FakePlexUploadService:
        def set_arr_instance_scope(self, _arr_instance=None):
            pass

        def __init__(self, _db, **_kwargs):
            pass

        def _discover_local_assets(self, _destination):
            return [
                {
                    "asset_type": "main",
                    "season_number": None,
                    "path": "/tmp/organized/Zootopia (2016) {tmdb-269149} {imdb-tt2948356}/poster.jpg",
                }
            ]

    def _fake_rename_posters(self, **_kwargs):
        rename_called["value"] = True
        return {"success": True, "stats": {"total_matched": 1, "movies": 1, "series": 0, "collections": 0}}

    monkeypatch.setattr("modules.upload.PlexUploadService", _FakePlexUploadService)
    monkeypatch.setattr("modules.upload.os.path.getmtime", lambda _path: 100.0)
    monkeypatch.setattr("modules.upload.PosterRenameService.rename_posters", _fake_rename_posters)

    test_db.add(Setting(key="poster_destination", value="/tmp/organized"))
    test_db.add(Setting(key="poster_drive_priority", value=json.dumps({"drive_ids": [1]})))
    test_db.add(
        Drive(
            id=1,
            name="MM2K Movies",
            drive_id="drive-1",
            style_type="MM2K",
            subscribed=True,
        )
    )
    test_db.add(
        Poster(
            drive_id="drive-1",
            file_name="Zootopia.jpg",
            file_path=str(Path(source_file).resolve()),
            file_mtime=100.0,
            last_processed=datetime.fromtimestamp(150, timezone.utc),
        )
    )
    job = Job(job_type="Plex Upload Webhook", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()

    parsed_payload = {
        "media_type": "movie",
        "title": "Zootopia",
        "year": 2016,
        "tmdb_id": 269149,
        "imdb_id": "tt2948356",
        "tvdb_id": None,
        "season_number": None,
    }

    plex_upload_module._run_webhook_preupload_rename_pass(test_db, job, parsed_payload)

    assert rename_called["value"] is False


def test_webhook_preupload_fast_path_runs_when_source_changed_after_last_processed(test_db, monkeypatch):
    """Webhook preupload should run targeted rename when source mtime is newer than last_processed."""
    import modules.upload as plex_upload_module

    rename_called = {"value": False}
    source_file = "/tmp/source/Zootopia (2016) {tmdb-269149} {imdb-tt2948356}.jpg"

    class _FakePlexUploadService:
        def set_arr_instance_scope(self, _arr_instance=None):
            pass

        def __init__(self, _db, **_kwargs):
            pass

        def _discover_local_assets(self, _destination):
            return [
                {
                    "asset_type": "main",
                    "season_number": None,
                    "path": "/tmp/organized/Zootopia (2016) {tmdb-269149} {imdb-tt2948356}/poster.jpg",
                }
            ]

    def _fake_rename_posters(self, **_kwargs):
        rename_called["value"] = True
        return {"success": True, "stats": {"total_matched": 1, "movies": 1, "series": 0, "collections": 0}}

    monkeypatch.setattr("modules.upload.PlexUploadService", _FakePlexUploadService)
    monkeypatch.setattr("modules.upload.os.path.getmtime", lambda _path: 100.0)
    monkeypatch.setattr("modules.upload.PosterRenameService.rename_posters", _fake_rename_posters)

    test_db.add(Setting(key="poster_destination", value="/tmp/organized"))
    test_db.add(Setting(key="poster_drive_priority", value=json.dumps({"drive_ids": [1]})))
    test_db.add(
        Drive(
            id=1,
            name="MM2K Movies",
            drive_id="drive-1",
            style_type="MM2K",
            subscribed=True,
        )
    )
    test_db.add(
        Poster(
            drive_id="drive-1",
            file_name="Zootopia.jpg",
            file_path=str(Path(source_file).resolve()),
            file_mtime=100.0,
            last_processed=datetime.fromtimestamp(90, timezone.utc),
        )
    )
    job = Job(job_type="Plex Upload Webhook", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()

    parsed_payload = {
        "media_type": "movie",
        "title": "Zootopia",
        "year": 2016,
        "tmdb_id": 269149,
        "imdb_id": "tt2948356",
        "tvdb_id": None,
        "season_number": None,
    }

    plex_upload_module._run_webhook_preupload_rename_pass(test_db, job, parsed_payload)

    assert rename_called["value"] is True


def test_webhook_preupload_adopt_existing_skips_when_destination_has_target_asset(test_db, monkeypatch):
    """When adopt-existing mode is enabled, destination title match should skip preupload rename/border prep."""
    import modules.upload as plex_upload_module

    rename_called = {"value": False}

    class _FakePlexUploadService:
        def set_arr_instance_scope(self, _arr_instance=None):
            pass

        def __init__(self, _db, **_kwargs):
            pass

        def _discover_local_assets(self, _destination):
            return [
                {
                    "asset_type": "main",
                    "season_number": None,
                    "media_key": "zootopia",
                    "path": "/tmp/organized/Zootopia (2016)/poster.jpg",
                }
            ]

    class _FakePosterRenameService:
        def __init__(self, _db, **_kwargs):
            pass

        def rename_posters(self, **_kwargs):
            rename_called["value"] = True
            return {"success": True, "stats": {"total_matched": 1, "movies": 1, "series": 0, "collections": 0}}

    monkeypatch.setattr("modules.upload.PlexUploadService", _FakePlexUploadService)
    monkeypatch.setattr("modules.upload.PosterRenameService", _FakePosterRenameService)

    test_db.add(Setting(key="poster_destination", value="/tmp/organized"))
    test_db.add(Setting(key="poster_drive_priority", value=json.dumps({"drive_ids": [1]})))
    test_db.add(Setting(key="plex_webhook_adopt_existing_processed", value="true"))
    test_db.add(
        Drive(
            id=1,
            name="MM2K Movies",
            drive_id="drive-1",
            style_type="MM2K",
            subscribed=True,
        )
    )
    job = Job(job_type="Plex Upload Webhook", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()

    parsed_payload = {
        "media_type": "movie",
        "title": "Zootopia",
        "year": 2016,
        "tmdb_id": None,
        "imdb_id": None,
        "tvdb_id": None,
        "season_number": None,
    }

    summary = plex_upload_module._run_webhook_preupload_rename_pass(test_db, job, parsed_payload)

    assert rename_called["value"] is False
    assert summary.get("fast_path_skipped") is True
