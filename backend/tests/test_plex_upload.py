import json
from pathlib import Path
from datetime import datetime, timezone

from models.setting import Setting
from models.drive import Drive
from models.job import Job
from models.poster import Poster
from models.plex_upload import PlexUploadRecord
from core.config import settings
from services.plex_upload import PlexUploadService


class _FakePlexItem:
    class _FakeServer:
        def __init__(self, machine_identifier: str):
            self.machineIdentifier = machine_identifier

    def __init__(
        self,
        item_type: str,
        title: str,
        year: int | None = None,
        library: str = "Plex",
        section_id: int | None = None,
        server_id: str | None = None,
        rating_key: str | None = None,
    ):
        self.type = item_type
        self.title = title
        self.year = year
        self.librarySectionTitle = library
        if section_id is not None:
            self.librarySectionID = section_id
        if server_id:
            self._server = self._FakeServer(server_id)
        if rating_key:
            self.ratingKey = rating_key

    def uploadPoster(self, _filepath: str) -> None:
        return None


def test_run_plex_upload_queues_background_job(client, test_db, monkeypatch):
    submitted_calls = []

    def _capture_submit(*args, **kwargs):
        submitted_calls.append((args, kwargs))
        return None

    monkeypatch.setattr("api.plex_upload.job_queue.submit", _capture_submit)

    response = client.post(
        "/api/posterflow/plex-upload/run",
        json={
            "dry_run": True,
            "reapply": True,
            "remove_overlay_label": True,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["status"] == "pending"
    assert isinstance(data["job_id"], int)
    assert submitted_calls

    submit_args, _submit_kwargs = submitted_calls[0]
    assert submit_args[0].__name__ == "run_plex_upload_background_job"
    assert submit_args[3] is True
    assert submit_args[4] is True
    assert submit_args[5] is True


def test_source_search_returns_prioritized_source_candidates(client, test_db, monkeypatch):
    poster_path = (
        settings.gdrive_dir
        / "MM2K"
        / "MM2K_Movies"
        / "The Matrix (1999) {tmdb-603} {imdb-tt0133093}"
        / "poster.jpg"
    )

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
            file_name="poster.jpg",
            file_path=str(poster_path),
        )
    )
    test_db.commit()

    response = client.get("/api/posterflow/plex-upload/source-search", params={"q": "matrix"})
    assert response.status_code == 200

    payload = response.json()
    assert payload["count"] == 1
    item = payload["items"][0]
    assert item["media_type"] == "movie"
    assert item["title"] == "The Matrix"
    assert item["tmdb_id"] == 603


def test_run_single_upload_queues_background_job(client, test_db, monkeypatch):
    submitted_calls = []

    def _capture_submit(*args, **kwargs):
        submitted_calls.append((args, kwargs))
        return None

    monkeypatch.setattr("api.plex_upload.job_queue.submit", _capture_submit)

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
    test_db.commit()

    response = client.post(
        "/api/posterflow/plex-upload/run-single",
        json={
            "media_type": "movie",
            "title": "The Matrix",
            "year": 1999,
            "tmdb_id": 603,
            "imdb_id": "TT0133093",
            "dry_run": True,
            "reapply": True,
            "remove_overlay_label": True,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["status"] == "pending"
    assert isinstance(data["job_id"], int)
    assert submitted_calls

    submit_args, _submit_kwargs = submitted_calls[0]
    assert submit_args[0].__name__ == "run_plex_single_manual_background_job"
    run_payload = submit_args[3]
    assert run_payload["media_type"] == "movie"
    assert run_payload["title"] == "The Matrix"
    assert run_payload["dry_run"] is True
    assert run_payload["reapply"] is True
    assert run_payload["remove_overlay_label"] is True
    assert run_payload["imdb_id"] == "tt0133093"


def test_run_full_upload_dry_run_reapply_does_not_clear_cache(test_db, monkeypatch):
    service = PlexUploadService(test_db)
    calls = {"clear": 0}

    monkeypatch.setattr(service, "_prepare_upload_context", lambda: (None, Path("/tmp"), {}, {}))
    monkeypatch.setattr(service, "_discover_local_assets", lambda _destination: [{"asset_type": "main", "path": "a.jpg"}])
    monkeypatch.setattr(service, "_build_arr_availability_index", lambda *args, **kwargs: {})
    monkeypatch.setattr(service, "_process_assets_for_upload", lambda **_kwargs: None)
    monkeypatch.setattr(service, "_persist_upload_cache", lambda: None)

    def _clear_cache() -> None:
        calls["clear"] += 1

    monkeypatch.setattr(service, "_clear_upload_cache", _clear_cache)

    result = service.run_full_upload(dry_run=True, reapply=True, remove_overlay_label=False)

    assert result["success"] is True
    assert result["dry_run"] is True
    assert calls["clear"] == 0


def test_run_full_upload_live_reapply_clears_cache(test_db, monkeypatch):
    service = PlexUploadService(test_db)
    calls = {"clear": 0}

    monkeypatch.setattr(service, "_prepare_upload_context", lambda: (None, Path("/tmp"), {}, {}))
    monkeypatch.setattr(service, "_discover_local_assets", lambda _destination: [{"asset_type": "main", "path": "a.jpg"}])
    monkeypatch.setattr(service, "_build_arr_availability_index", lambda *args, **kwargs: {})
    monkeypatch.setattr(service, "_process_assets_for_upload", lambda **_kwargs: None)
    monkeypatch.setattr(service, "_persist_upload_cache", lambda: None)

    def _clear_cache() -> None:
        calls["clear"] += 1

    monkeypatch.setattr(service, "_clear_upload_cache", _clear_cache)

    result = service.run_full_upload(dry_run=False, reapply=True, remove_overlay_label=False)

    assert result["success"] is True
    assert result["dry_run"] is False
    assert calls["clear"] == 1


def test_manual_single_background_job_passes_dry_run_to_preupload(test_db, monkeypatch):
    """Manual single background jobs should propagate dry_run to preupload prep."""
    import modules.upload as upload_module

    captured = {"dry_run": None}

    job = Job(job_type="Plex Upload Single", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()
    job_id = job.id

    monkeypatch.setattr("modules.upload.SessionLocal", lambda: test_db)
    monkeypatch.setattr("modules.upload.add_job_log_handler", lambda *args, **kwargs: 1)
    monkeypatch.setattr("modules.upload.remove_job_log_handler", lambda *args, **kwargs: None)

    def _fake_preupload(_db, _job, _payload, dry_run=False):
        captured["dry_run"] = dry_run
        return {
            "rename": {"matched": 2},
            "border": {"enabled": True, "changed": 1},
        }

    class _FakePlexUploadService:
        def __init__(self, _db, **_kwargs):
            pass

        def run_single_upload(self, **_kwargs):
            return {
                "success": True,
                "stats": {
                    "scanned": 1,
                    "matched": 1,
                    "uploaded": 0,
                    "would_upload": 1,
                    "candidate_matches_raw": 1,
                    "candidate_matches_unique": 1,
                    "skipped": 0,
                    "errors": 0,
                },
            }

    monkeypatch.setattr("modules.upload._run_webhook_preupload_rename_pass", _fake_preupload)
    monkeypatch.setattr("modules.upload.PlexUploadService", _FakePlexUploadService)

    upload_module.run_plex_single_manual_background_job(
        job_id,
        {
            "media_type": "movie",
            "title": "The Matrix",
            "year": 1999,
            "dry_run": True,
            "remove_overlay_label": False,
        },
    )

    refreshed = test_db.query(Job).filter(Job.id == job_id).first()
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert captured["dry_run"] is True
    assert refreshed.message is not None
    assert "prep: rename matched 2, border would_change 1" in refreshed.message


def test_webhook_settings_default_disabled(client):
    """Webhook settings should default to enabled with all options on when not configured."""
    response = client.get("/api/posterflow/plex-upload/webhook-settings")
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["remove_overlay_label"] is True
    assert data["rename_then_upload"] is True
    assert data["adopt_existing_processed"] is True
    assert data["retry_attempts"] == 10
    assert data["retry_delay_seconds"] == 30


def test_webhook_settings_toggle(client):
    """Webhook settings endpoint should persist enabled/disabled flag."""
    disable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={
            "enabled": False,
            "remove_overlay_label": True,
            "rename_then_upload": True,
            "adopt_existing_processed": True,
            "retry_attempts": 6,
            "retry_delay_seconds": 12,
        },
    )
    assert disable_resp.status_code == 200
    assert disable_resp.json()["enabled"] is False
    assert disable_resp.json()["remove_overlay_label"] is True
    assert disable_resp.json()["rename_then_upload"] is True
    assert disable_resp.json()["adopt_existing_processed"] is True
    assert disable_resp.json()["retry_attempts"] == 6
    assert disable_resp.json()["retry_delay_seconds"] == 12

    get_resp = client.get("/api/posterflow/plex-upload/webhook-settings")
    assert get_resp.status_code == 200
    assert get_resp.json()["enabled"] is False
    assert get_resp.json()["remove_overlay_label"] is True
    assert get_resp.json()["rename_then_upload"] is True
    assert get_resp.json()["adopt_existing_processed"] is True
    assert get_resp.json()["retry_attempts"] == 6
    assert get_resp.json()["retry_delay_seconds"] == 12

    enable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={
            "enabled": True,
            "remove_overlay_label": False,
            "rename_then_upload": False,
            "adopt_existing_processed": False,
            "retry_attempts": 4,
            "retry_delay_seconds": 5,
        },
    )
    assert enable_resp.status_code == 200
    assert enable_resp.json()["enabled"] is True
    assert enable_resp.json()["remove_overlay_label"] is False
    assert enable_resp.json()["rename_then_upload"] is False
    assert enable_resp.json()["adopt_existing_processed"] is False
    assert enable_resp.json()["retry_attempts"] == 4
    assert enable_resp.json()["retry_delay_seconds"] == 5


def test_webhook_settings_retry_bounds_are_coerced(client, test_db):
    """Retry settings should be clamped to allowed min/max bounds."""
    response = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={
            "enabled": True,
            "remove_overlay_label": False,
            "rename_then_upload": False,
            "adopt_existing_processed": False,
            "retry_attempts": 999,
            "retry_delay_seconds": 0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["retry_attempts"] == 20
    assert payload["retry_delay_seconds"] == 1

    attempts_setting = test_db.query(Setting).filter(Setting.key == "plex_webhook_retry_attempts").first()
    delay_setting = test_db.query(Setting).filter(Setting.key == "plex_webhook_retry_delay_seconds").first()
    assert attempts_setting is not None
    assert attempts_setting.value == "20"
    assert delay_setting is not None
    assert delay_setting.value == "1"


def test_webhook_settings_invalid_persisted_retry_values_use_defaults(client, test_db):
    """Invalid persisted retry values should fall back to default webhook retry settings."""
    test_db.add(Setting(key="plex_webhook_retry_attempts", value="not-an-int"))
    test_db.add(Setting(key="plex_webhook_retry_delay_seconds", value="also-not-an-int"))
    test_db.commit()

    response = client.get("/api/posterflow/plex-upload/webhook-settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["retry_attempts"] == 10
    assert payload["retry_delay_seconds"] == 30


def test_webhook_queue_passes_rename_then_upload_setting(client, monkeypatch):
    """Webhook queue should pass rename_then_upload setting into background worker args."""
    submitted_calls = []

    def _capture_submit(*args, **kwargs):
        submitted_calls.append((args, kwargs))
        return None

    monkeypatch.setattr("api.plex_upload.job_queue.submit", _capture_submit)

    save_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={
            "enabled": True,
            "remove_overlay_label": False,
            "rename_then_upload": True,
            "retry_attempts": 7,
            "retry_delay_seconds": 11,
        },
    )
    assert save_resp.status_code == 200

    response = client.post(
        "/api/posterflow/plex-upload/webhook",
        json={
            "eventType": "Download",
            "movie": {"title": "The Matrix", "year": 1999, "tmdbId": 603},
        },
    )

    assert response.status_code == 200
    assert response.json()["queued"] is True
    assert submitted_calls

    submit_args, _submit_kwargs = submitted_calls[0]
    assert len(submit_args) >= 8
    assert submit_args[0].__name__ == "run_plex_webhook_background_job"
    assert submit_args[5] is True
    assert submit_args[6] == 7
    assert submit_args[7] == 11


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


def test_webhook_background_job_completes_with_warning_when_no_local_assets(test_db, monkeypatch):
    """Webhook background job should complete with warning when no local assets exist for the target."""
    import modules.upload as upload_module

    job = Job(job_type="Plex Upload Webhook", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()
    job_id = job.id

    monkeypatch.setattr("modules.upload.SessionLocal", lambda: test_db)
    monkeypatch.setattr("modules.upload.add_job_log_handler", lambda *args, **kwargs: 1)
    monkeypatch.setattr("modules.upload.remove_job_log_handler", lambda *args, **kwargs: None)
    monkeypatch.setattr("modules.upload.time.sleep", lambda _seconds: None)

    class _FakePlexUploadService:
        run_calls = 0

        def __init__(self, _db, **_kwargs):
            pass

        def is_single_target_fully_cached(self, **_kwargs):
            return False

        def run_single_upload(self, **_kwargs):
            self.__class__.run_calls += 1
            return {
                "success": True,
                "stats": {
                    "scanned": 0,
                    "matched": 0,
                    "uploaded": 0,
                    "candidate_matches_raw": 0,
                    "candidate_matches_unique": 0,
                    "skipped": 0,
                    "errors": 0,
                },
            }

        def is_series_show_poster_cached(self, **_kwargs):
            return False

        def invalidate_preflight_cache(self) -> None:
            pass

        def invalidate_arr_availability_cache(self) -> None:
            pass

        def invalidate_local_assets_cache(self) -> None:
            pass

        def _get_destination_dir(self):
            from pathlib import Path
            return Path("/tmp")

        def _get_local_assets(self, _destination):
            return [{"media_key": "placeholder", "asset_type": "main"}]

        def _select_local_assets_for_target(self, assets, **_kwargs):
            return assets

    monkeypatch.setattr("modules.upload.PlexUploadService", _FakePlexUploadService)

    parsed_payload = {
        "media_type": "movie",
        "title": "No Match Movie",
        "year": 2026,
        "season_number": None,
    }

    upload_module.run_plex_webhook_background_job(
        job_id,
        parsed_payload,
        False,
        False,
        3,
        1,
    )

    refreshed = test_db.query(Job).filter(Job.id == job_id).first()
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert "Webhook skipped (no local assets)" in (refreshed.message or "")
    assert refreshed.error in (None, "")
    assert _FakePlexUploadService.run_calls == 1


def test_webhook_background_job_completes_without_retry_on_matched_zero_upload(test_db, monkeypatch):
    """Webhook should treat matched zero-upload as already up-to-date without cache clearing or retry."""
    import modules.upload as upload_module

    job = Job(job_type="Plex Upload Webhook", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()
    job_id = job.id

    monkeypatch.setattr("modules.upload.SessionLocal", lambda: test_db)
    monkeypatch.setattr("modules.upload.add_job_log_handler", lambda *args, **kwargs: 1)
    monkeypatch.setattr("modules.upload.remove_job_log_handler", lambda *args, **kwargs: None)
    monkeypatch.setattr("modules.upload.time.sleep", lambda _seconds: None)

    class _FakePlexUploadService:
        run_calls = 0
        clear_calls = 0

        def __init__(self, _db, **_kwargs):
            pass

        def is_single_target_fully_cached(self, **_kwargs):
            return False

        def run_single_upload(self, **_kwargs):
            self.__class__.run_calls += 1
            return {
                "success": True,
                "stats": {
                    "scanned": 1,
                    "matched": 1,
                    "uploaded": 0,
                    "candidate_matches_raw": 1,
                    "candidate_matches_unique": 1,
                    "skipped": 1,
                    "errors": 0,
                },
            }

        def clear_upload_cache_for_target(self, **_kwargs):
            self.__class__.clear_calls += 1
            return 1

        def is_series_show_poster_cached(self, **_kwargs):
            return False

        def invalidate_preflight_cache(self) -> None:
            pass

        def invalidate_arr_availability_cache(self) -> None:
            pass

        def invalidate_local_assets_cache(self) -> None:
            pass

        def _get_destination_dir(self):
            from pathlib import Path
            return Path("/tmp")

        def _get_local_assets(self, _destination):
            return [{"media_key": "placeholder", "asset_type": "main"}]

        def _select_local_assets_for_target(self, assets, **_kwargs):
            return assets

    monkeypatch.setattr("modules.upload.PlexUploadService", _FakePlexUploadService)

    parsed_payload = {
        "media_type": "movie",
        "title": "Retry Movie",
        "year": 2026,
        "season_number": None,
    }

    upload_module.run_plex_webhook_background_job(
        job_id,
        parsed_payload,
        False,
        False,
        3,
        1,
    )

    refreshed = test_db.query(Job).filter(Job.id == job_id).first()
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert _FakePlexUploadService.clear_calls == 0
    assert _FakePlexUploadService.run_calls == 1


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


def test_webhook_background_job_short_circuits_when_target_is_fully_cached(test_db, monkeypatch):
    """Webhook background job should complete early without running upload when cache gate says target is already uploaded."""
    import modules.upload as upload_module

    job = Job(job_type="Plex Upload Webhook", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()
    job_id = job.id

    monkeypatch.setattr("modules.upload.SessionLocal", lambda: test_db)
    monkeypatch.setattr("modules.upload.add_job_log_handler", lambda *args, **kwargs: 1)
    monkeypatch.setattr("modules.upload.remove_job_log_handler", lambda *args, **kwargs: None)

    class _FakePlexUploadService:
        run_calls = 0
        cache_checks = 0

        def __init__(self, _db, **_kwargs):
            pass

        def is_single_target_fully_cached(self, **_kwargs):
            self.__class__.cache_checks += 1
            return True

        def run_single_upload(self, **_kwargs):
            self.__class__.run_calls += 1
            return {
                "success": True,
                "stats": {
                    "scanned": 1,
                    "matched": 1,
                    "uploaded": 1,
                    "candidate_matches_raw": 1,
                    "candidate_matches_unique": 1,
                    "skipped": 0,
                    "errors": 0,
                },
            }

        def is_series_show_poster_cached(self, **_kwargs):
            return False

        def invalidate_preflight_cache(self) -> None:
            pass

        def invalidate_arr_availability_cache(self) -> None:
            pass

        def invalidate_local_assets_cache(self) -> None:
            pass

        def _get_destination_dir(self):
            from pathlib import Path
            return Path("/tmp")

        def _get_local_assets(self, _destination):
            return [{"media_key": "placeholder", "asset_type": "main"}]

        def _select_local_assets_for_target(self, assets, **_kwargs):
            return assets

    monkeypatch.setattr("modules.upload.PlexUploadService", _FakePlexUploadService)

    parsed_payload = {
        "media_type": "series",
        "title": "Family Guy",
        "year": 1999,
        "season_number": 24,
    }

    upload_module.run_plex_webhook_background_job(
        job_id,
        parsed_payload,
        False,
        True,
        3,
        1,
    )

    refreshed = test_db.query(Job).filter(Job.id == job_id).first()
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert "Webhook skipped (already uploaded)" in (refreshed.message or "")
    assert _FakePlexUploadService.cache_checks == 2
    assert _FakePlexUploadService.run_calls == 0

    stats_setting = test_db.query(Setting).filter(Setting.key == "plex_webhook_stats").first()
    assert stats_setting is not None
    stats_payload = json.loads(stats_setting.value)
    assert int(stats_payload.get("skipped_cached", 0)) == 1


def test_webhook_background_job_series_season_runs_season_and_show_posters(test_db, monkeypatch):
    """Series season webhook should run upload for season asset and series main poster."""
    import modules.upload as upload_module

    job = Job(job_type="Plex Upload Webhook", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()
    job_id = job.id

    monkeypatch.setattr("modules.upload.SessionLocal", lambda: test_db)
    monkeypatch.setattr("modules.upload.add_job_log_handler", lambda *args, **kwargs: 1)
    monkeypatch.setattr("modules.upload.remove_job_log_handler", lambda *args, **kwargs: None)
    monkeypatch.setattr("modules.upload.time.sleep", lambda _seconds: None)

    class _FakePlexUploadService:
        run_calls = []

        def __init__(self, _db, **_kwargs):
            pass

        def is_single_target_fully_cached(self, **_kwargs):
            return False

        def run_single_upload(self, **kwargs):
            self.__class__.run_calls.append(kwargs.get("season_number"))
            return {
                "success": True,
                "stats": {
                    "scanned": 1,
                    "matched": 1,
                    "uploaded": 1,
                    "candidate_matches_raw": 1,
                    "candidate_matches_unique": 1,
                    "skipped": 0,
                    "errors": 0,
                },
            }

        def is_series_show_poster_cached(self, **_kwargs):
            return False

        def invalidate_preflight_cache(self) -> None:
            pass

        def invalidate_arr_availability_cache(self) -> None:
            pass

        def invalidate_local_assets_cache(self) -> None:
            pass

        def _get_destination_dir(self):
            from pathlib import Path
            return Path("/tmp")

        def _get_local_assets(self, _destination):
            return [{"media_key": "placeholder", "asset_type": "main"}]

        def _select_local_assets_for_target(self, assets, **_kwargs):
            return assets

    monkeypatch.setattr("modules.upload.PlexUploadService", _FakePlexUploadService)

    parsed_payload = {
        "media_type": "series",
        "title": "A New Show",
        "year": 2026,
        "season_number": 1,
        "tmdb_id": None,
        "tvdb_id": 12345,
        "imdb_id": None,
    }

    upload_module.run_plex_webhook_background_job(
        job_id,
        parsed_payload,
        False,
        False,
        2,
        1,
    )

    refreshed = test_db.query(Job).filter(Job.id == job_id).first()
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert _FakePlexUploadService.run_calls == [1, None]


def test_webhook_background_job_series_season_cache_gate_requires_both_targets(test_db, monkeypatch):
    """Series season webhook cache gate should only short-circuit when season and show targets are both cached."""
    import modules.upload as upload_module

    job = Job(job_type="Plex Upload Webhook", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()
    job_id = job.id

    monkeypatch.setattr("modules.upload.SessionLocal", lambda: test_db)
    monkeypatch.setattr("modules.upload.add_job_log_handler", lambda *args, **kwargs: 1)
    monkeypatch.setattr("modules.upload.remove_job_log_handler", lambda *args, **kwargs: None)
    monkeypatch.setattr("modules.upload.time.sleep", lambda _seconds: None)

    class _FakePlexUploadService:
        cache_calls = []
        run_calls = 0

        def __init__(self, _db, **_kwargs):
            pass

        def is_single_target_fully_cached(self, **kwargs):
            season_value = kwargs.get("season_number")
            self.__class__.cache_calls.append(season_value)
            return isinstance(season_value, int)

        def run_single_upload(self, **_kwargs):
            self.__class__.run_calls += 1
            return {
                "success": True,
                "stats": {
                    "scanned": 1,
                    "matched": 1,
                    "uploaded": 1,
                    "candidate_matches_raw": 1,
                    "candidate_matches_unique": 1,
                    "skipped": 0,
                    "errors": 0,
                },
            }

        def is_series_show_poster_cached(self, **_kwargs):
            return False

        def invalidate_preflight_cache(self) -> None:
            pass

        def invalidate_arr_availability_cache(self) -> None:
            pass

        def invalidate_local_assets_cache(self) -> None:
            pass

        def _get_destination_dir(self):
            from pathlib import Path
            return Path("/tmp")

        def _get_local_assets(self, _destination):
            return [{"media_key": "placeholder", "asset_type": "main"}]

        def _select_local_assets_for_target(self, assets, **_kwargs):
            return assets

    monkeypatch.setattr("modules.upload.PlexUploadService", _FakePlexUploadService)

    parsed_payload = {
        "media_type": "series",
        "title": "A New Show",
        "year": 2026,
        "season_number": 1,
        "tmdb_id": None,
        "tvdb_id": 12345,
        "imdb_id": None,
    }

    upload_module.run_plex_webhook_background_job(
        job_id,
        parsed_payload,
        False,
        False,
        2,
        1,
    )

    refreshed = test_db.query(Job).filter(Job.id == job_id).first()
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert _FakePlexUploadService.cache_calls == [1, None]
    assert _FakePlexUploadService.run_calls > 0


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


def test_webhook_test_event_updates_stats(client):
    """ARR test event should be ignored but counted in webhook stats."""
    enable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 200

    response = client.post(
        "/api/posterflow/plex-upload/webhook",
        json={"eventType": "test"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["queued"] is False

    stats_resp = client.get("/api/posterflow/plex-upload/webhook-stats")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["received"] == 1
    assert stats["skipped_test"] == 1
    assert stats["skipped_cached"] == 0
    assert stats["queued"] == 0


def test_webhook_duplicate_suppression_updates_stats(client, monkeypatch):
    """Duplicate webhook payload should be suppressed and tracked in stats."""
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)

    enable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 200

    movie_payload = {
        "eventType": "Download",
        "movie": {
            "title": "The Matrix",
            "year": 1999,
            "tmdbId": 603,
            "imdbId": "tt0133093",
        },
    }

    first = client.post("/api/posterflow/plex-upload/webhook", json=movie_payload)
    assert first.status_code == 200
    assert first.json()["queued"] is True

    second = client.post("/api/posterflow/plex-upload/webhook", json=movie_payload)
    assert second.status_code == 200
    second_data = second.json()
    assert second_data["queued"] is False
    assert second_data["duplicate"] is True

    stats_resp = client.get("/api/posterflow/plex-upload/webhook-stats")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["received"] == 2
    assert stats["queued"] == 1
    assert stats["duplicates"] == 1


def test_webhook_dedupe_clear_single_item_allows_requeue(client, monkeypatch):
    """Clearing webhook dedupe for one item should allow that item to queue again."""
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)

    enable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 200

    movie_payload = {
        "eventType": "Download",
        "movie": {
            "title": "The Matrix",
            "year": 1999,
            "tmdbId": 603,
            "imdbId": "tt0133093",
        },
    }

    first = client.post("/api/posterflow/plex-upload/webhook", json=movie_payload)
    assert first.status_code == 200
    assert first.json()["queued"] is True

    duplicate = client.post("/api/posterflow/plex-upload/webhook", json=movie_payload)
    assert duplicate.status_code == 200
    assert duplicate.json()["queued"] is False
    assert duplicate.json()["duplicate"] is True

    clear_resp = client.post(
        "/api/posterflow/plex-upload/webhook-dedupe/clear",
        json={
            "media_type": "movie",
            "title": "The Matrix",
            "year": 1999,
        },
    )
    assert clear_resp.status_code == 200
    clear_data = clear_resp.json()
    assert clear_data["success"] is True
    assert clear_data["removed"] >= 1

    after_clear = client.post("/api/posterflow/plex-upload/webhook", json=movie_payload)
    assert after_clear.status_code == 200
    assert after_clear.json()["queued"] is True


def test_webhook_dedupe_clear_all_allows_requeue(client, monkeypatch):
    """Clearing all webhook dedupe entries should allow all targets to queue again."""
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)

    enable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 200

    movie_payload = {
        "eventType": "Download",
        "movie": {
            "title": "The Matrix",
            "year": 1999,
            "tmdbId": 603,
            "imdbId": "tt0133093",
        },
    }
    series_payload = {
        "eventType": "Download",
        "series": {
            "title": "The Office",
            "year": 2005,
            "tvdbId": 73244,
            "imdbId": "tt0386676",
        },
    }

    assert client.post("/api/posterflow/plex-upload/webhook", json=movie_payload).status_code == 200
    assert client.post("/api/posterflow/plex-upload/webhook", json=series_payload).status_code == 200

    movie_dup = client.post("/api/posterflow/plex-upload/webhook", json=movie_payload)
    series_dup = client.post("/api/posterflow/plex-upload/webhook", json=series_payload)
    assert movie_dup.status_code == 200 and movie_dup.json()["queued"] is False
    assert series_dup.status_code == 200 and series_dup.json()["queued"] is False

    clear_resp = client.post(
        "/api/posterflow/plex-upload/webhook-dedupe/clear",
        json={"clear_all": True},
    )
    assert clear_resp.status_code == 200
    clear_data = clear_resp.json()
    assert clear_data["success"] is True
    assert clear_data["removed"] >= 2

    movie_after = client.post("/api/posterflow/plex-upload/webhook", json=movie_payload)
    series_after = client.post("/api/posterflow/plex-upload/webhook", json=series_payload)
    assert movie_after.status_code == 200 and movie_after.json()["queued"] is True
    assert series_after.status_code == 200 and series_after.json()["queued"] is True


def test_webhook_dedupe_entries_search_returns_active_locks(client, monkeypatch):
    """Webhook dedupe entries endpoint should return active lock entries from DB cache."""
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)

    enable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 200

    movie_payload = {
        "eventType": "Download",
        "movie": {
            "title": "The Matrix",
            "year": 1999,
            "tmdbId": 603,
            "imdbId": "tt0133093",
        },
    }
    series_payload = {
        "eventType": "Download",
        "series": {
            "title": "The Office",
            "year": 2005,
            "tvdbId": 73244,
            "imdbId": "tt0386676",
        },
    }

    assert client.post("/api/posterflow/plex-upload/webhook", json=movie_payload).status_code == 200
    assert client.post("/api/posterflow/plex-upload/webhook", json=series_payload).status_code == 200

    response = client.get(
        "/api/posterflow/plex-upload/webhook-dedupe/entries",
        params={"q": "matrix", "media_type": "movie", "limit": 10},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "matrix"
    assert payload["media_type"] == "movie"
    assert payload["count"] >= 1
    assert payload["total"] >= payload["count"]

    first = payload["items"][0]
    assert first["media_type"] == "movie"
    assert "matrix" in first["title"]
    assert first["year"] == 1999
    assert "dedupe_key" in first
    assert "last_seen_at" in first


def test_webhook_dedupe_entries_search_rejects_invalid_media_type(client):
    """Webhook dedupe entries endpoint should validate media_type query parameter."""
    response = client.get(
        "/api/posterflow/plex-upload/webhook-dedupe/entries",
        params={"q": "matrix", "media_type": "invalid"},
    )
    assert response.status_code == 400


def test_webhook_different_seasons_not_deduplicated(client, monkeypatch):
    """Webhooks for different seasons of the same show must each queue a separate job.

    Season 2 arriving within the dedupe window after Season 1 must NOT be treated as
    a duplicate — each season needs its own upload job.  Per-episode spam within the
    same season is still collapsed (second webhook for the same season IS a duplicate).
    """
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *args, **kwargs: None)

    enable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 200

    def _make_series_payload(season_number: int) -> dict:
        return {
            "eventType": "Download",
            "series": {
                "title": "Breaking Bad",
                "year": 2008,
                "tvdbId": 81189,
            },
            "episodes": [{"seasonNumber": season_number, "episodeNumber": 1}],
        }

    # Season 1 — should queue
    s1_first = client.post("/api/posterflow/plex-upload/webhook", json=_make_series_payload(1))
    assert s1_first.status_code == 200
    assert s1_first.json()["queued"] is True, "Season 1 first webhook should be queued"

    # Season 1 again — should be deduplicated (same season)
    s1_dup = client.post("/api/posterflow/plex-upload/webhook", json=_make_series_payload(1))
    assert s1_dup.status_code == 200
    assert s1_dup.json()["queued"] is False, "Season 1 repeat within window should be deduplicated"
    assert s1_dup.json()["duplicate"] is True

    # Season 2 — must NOT be deduplicated even though it's within the window
    s2_first = client.post("/api/posterflow/plex-upload/webhook", json=_make_series_payload(2))
    assert s2_first.status_code == 200
    assert s2_first.json()["queued"] is True, "Season 2 should not be deduplicated by Season 1's cache entry"

    # Season 2 again — should be deduplicated
    s2_dup = client.post("/api/posterflow/plex-upload/webhook", json=_make_series_payload(2))
    assert s2_dup.status_code == 200
    assert s2_dup.json()["queued"] is False, "Season 2 repeat within window should be deduplicated"


def test_webhook_disabled_rejects_and_tracks_stats(client):
    """Disabled webhook setting should block ingestion and increment disabled counter."""
    disable_resp = client.post(
        "/api/posterflow/plex-upload/webhook-settings",
        json={"enabled": False, "remove_overlay_label": False},
    )
    assert disable_resp.status_code == 200

    response = client.post(
        "/api/posterflow/plex-upload/webhook",
        json={
            "eventType": "Download",
            "movie": {"title": "The Matrix", "year": 1999},
        },
    )
    assert response.status_code == 403

    stats_resp = client.get("/api/posterflow/plex-upload/webhook-stats")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["received"] == 1
    assert stats["rejected_disabled"] == 1


def test_webhook_stats_reset_endpoint(client):
    """Webhook stats reset endpoint should restore all counters/timestamps to defaults."""
    client.post(
        "/api/posterflow/plex-upload/webhook",
        json={"eventType": "test"},
    )

    pre_reset = client.get("/api/posterflow/plex-upload/webhook-stats")
    assert pre_reset.status_code == 200
    pre_stats = pre_reset.json()
    assert pre_stats["received"] > 0

    reset_response = client.post("/api/posterflow/plex-upload/webhook-stats/reset")
    assert reset_response.status_code == 200
    reset_data = reset_response.json()
    assert reset_data["success"] is True
    assert reset_data["received"] == 0
    assert reset_data["queued"] == 0
    assert reset_data["duplicates"] == 0
    assert reset_data["skipped_test"] == 0
    assert reset_data["skipped_cached"] == 0
    assert reset_data["rejected_disabled"] == 0
    assert reset_data["parse_errors"] == 0
    assert reset_data["internal_errors"] == 0
    assert reset_data["last_event_at"] is None
    assert reset_data["last_queued_at"] is None
    assert reset_data["last_error"] is None


def test_service_selected_libraries_invalid_json_returns_configuration_error(test_db):
    service = PlexUploadService(test_db)

    test_db.add(
        Setting(
            key="plex_library_config",
            value="{invalid-json",
        )
    )
    test_db.commit()

    selected, error = service._get_selected_libraries([
        {"name": "Plex", "url": "http://localhost:32400", "api_key": "token"}
    ])

    assert selected == {}
    assert error == service.ERROR_INVALID_LIBRARY_CONFIG


def test_service_library_override_invalid_json_returns_error_tuple(test_db):
    service = PlexUploadService(test_db)

    test_db.add(
        Setting(
            key="plex_upload_library_override",
            value="{invalid-json",
        )
    )
    test_db.commit()

    enabled, configs, error = service._load_plex_upload_library_override()

    assert enabled is False
    assert configs == []
    assert error is not None
    assert "Invalid Plex Upload library override configuration" in error


def test_service_get_arr_instances_invalid_json_returns_empty_list(test_db):
    service = PlexUploadService(test_db)

    test_db.add(
        Setting(
            key=service.SETTING_RADARR_INSTANCES,
            value="{invalid-json",
        )
    )
    test_db.commit()

    instances = service._get_arr_instances(service.SETTING_RADARR_INSTANCES)
    assert instances == []


def test_plex_upload_ambiguous_no_id_asset_is_skipped(test_db, monkeypatch):
    """No-ID assets matching both movie and show sections should be skipped as ambiguous when no collection candidate exists."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "thefall",
        "path": "/tmp/posters/The_Fall/poster.jpg",
        "display_name": "The Fall",
        "asset_type": "main",
    }
    index = {
        "movies": {"thefall": [_FakePlexItem("movie", "The Fall", 2006, "Movies")]},
        "shows": {"thefall": [_FakePlexItem("show", "The Fall", 2013, "TV Shows")]},
        "collections": {"thefall": []},
    }

    info_messages: list[str] = []

    def _capture_info(_tag, message, **_context):
        info_messages.append(message)

    monkeypatch.setattr("services.plex_upload.log_info", _capture_info)

    uploaded, matched, raw_candidates, unique_candidates, media_counts = service._upload_asset(
        asset,
        index,
        dry_run=True,
    )

    assert uploaded == 0
    assert matched is False
    assert raw_candidates == 2
    assert unique_candidates == 0
    assert media_counts == service._empty_media_upload_counts()
    assert any("Skipping ambiguous no-ID asset" in message for message in info_messages)


def test_plex_upload_no_id_asset_prefers_collection_when_type_unknown(test_db):
    """No-ID assets should prefer collections when no explicit type signal is available."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "alien",
        "path": "/tmp/posters/Alien/poster.jpg",
        "display_name": "Alien",
        "asset_type": "main",
    }
    index = {
        "movies": {"alien": [_FakePlexItem("movie", "Alien", 1979, "Movies")]},
        "shows": {"alien": []},
        "collections": {"alien": [_FakePlexItem("collection", "Alien Collection", None, "Movies")]},
    }

    uploaded, matched, _raw_candidates, unique_candidates, media_counts = service._upload_asset(
        asset,
        index,
        dry_run=True,
    )

    assert uploaded == 1
    assert matched is True
    assert unique_candidates == 1
    assert media_counts["collections"] == 1
    assert media_counts["movies"] == 0


def test_plex_upload_no_id_asset_prefers_collection_over_arr_movie_hint(test_db):
    """No-ID assets with Plex collection candidates should resolve to collections, even if ARR has a movie match."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "alien",
        "path": "/tmp/posters/Alien/poster.jpg",
        "display_name": "Alien",
        "asset_type": "main",
    }
    index = {
        "movies": {"alien": [_FakePlexItem("movie", "Alien", 1979, "Movies")]},
        "shows": {"alien": []},
        "collections": {"alien": [_FakePlexItem("collection", "Alien Collection", None, "Movies")]},
    }
    arr_availability = {
        "movies": {"alien": {"has_file": True}},
        "shows": {},
    }

    uploaded, matched, _raw_candidates, unique_candidates, media_counts = service._upload_asset(
        asset,
        index,
        dry_run=True,
        arr_availability=arr_availability,
    )

    assert uploaded == 1
    assert matched is True
    assert unique_candidates == 1
    assert media_counts["collections"] == 1
    assert media_counts["movies"] == 0


def test_plex_upload_discovery_excludes_tmp_assets_nested_anywhere(test_db, tmp_path):
    """Uploader discovery should ignore staged tmp files even when tmp is nested below destination root."""
    destination = tmp_path / "root"
    processed = destination / "assets" / "Zootopia (2016)" / "poster.jpg"
    staged = destination / "assets" / "tmp" / "Zootopia (2016)" / "poster.jpg"

    processed.parent.mkdir(parents=True, exist_ok=True)
    staged.parent.mkdir(parents=True, exist_ok=True)
    processed.write_bytes(b"processed")
    staged.write_bytes(b"staged")

    service = PlexUploadService(test_db)
    assets = service._discover_local_assets(destination)
    asset_paths = {str(asset.get("path")) for asset in assets}

    assert str(processed) in asset_paths
    assert str(staged) not in asset_paths


def test_plex_upload_no_id_asset_falls_back_to_collection_when_untyped_only(test_db):
    """No-ID assets should fallback to collections only when section-typed matches are absent."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "middleearth",
        "path": "/tmp/posters/Middle_Earth/poster.jpg",
        "display_name": "Middle Earth",
        "asset_type": "main",
    }
    index = {
        "movies": {"middleearth": []},
        "shows": {"middleearth": []},
        "collections": {"middleearth": [_FakePlexItem("collection", "Middle Earth", None, "Movies")]},
    }

    uploaded, matched, _raw_candidates, unique_candidates, media_counts = service._upload_asset(
        asset,
        index,
        dry_run=True,
    )

    assert uploaded == 1
    assert matched is True
    assert unique_candidates == 1
    assert media_counts["collections"] == 1
    assert media_counts["movies"] == 0


def test_plex_upload_matches_show_by_tvdb_id_when_title_key_misses(test_db):
    """Uploader should match by tvdb ID key even when normalized title key differs."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "pluribus2025",
        "path": "/tmp/Pluribus (2025) {tvdb-436457}/poster.jpg",
        "display_name": "Pluribus (2025) {tvdb-436457}",
        "asset_type": "main",
    }
    index = {
        "movies": {},
        "shows": {
            "plur1bus2025": [_FakePlexItem("show", "Plur1bus", 2025, "4k TV Shows")],
            "id:tvdb:436457": [_FakePlexItem("show", "Plur1bus", 2025, "4k TV Shows")],
        },
        "collections": {},
    }

    uploaded, matched, _raw_candidates, unique_candidates, media_counts = service._upload_asset(
        asset,
        index,
        dry_run=True,
    )

    assert uploaded == 1
    assert matched is True
    assert unique_candidates == 1
    assert media_counts["shows"] == 1


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

    uploaded, matched, _raw_candidates, unique_candidates, media_counts = service._upload_asset(
        asset,
        index,
        dry_run=True,
    )

    assert uploaded == 2
    assert matched is True
    assert unique_candidates == 2
    assert media_counts["movies"] == 2


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

    uploaded, matched, _raw_candidates, unique_candidates, media_counts = service._upload_asset(
        asset,
        index,
        dry_run=True,
        media_type_filter="movie",
    )

    assert uploaded == 1
    assert matched is True
    assert unique_candidates == 2
    assert media_counts["movies"] == 1


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


def test_run_single_upload_series_season_only_processes_season_asset(test_db, monkeypatch):
    """Series single upload with season_number should only process season poster assets."""
    service = PlexUploadService(test_db)

    monkeypatch.setattr(service, "_get_destination_dir", lambda: "/tmp/organized")
    monkeypatch.setattr(service, "_get_plex_instances", lambda: [{"name": "Plex", "url": "http://plex", "api_key": "token"}])
    monkeypatch.setattr(service, "_get_selected_libraries", lambda instances: ({"Plex": [{"key": "1", "type": "show", "title": "TV"}]}, None))
    monkeypatch.setattr(
        service,
        "_build_plex_index",
        lambda instances, selected: (
            {"movies": {}, "shows": {}, "collections": {}},
            [{"instance": "Plex", "library": "TV", "section_type": "show", "items": 0, "collections": 0}],
        ),
    )
    monkeypatch.setattr(service, "_build_arr_availability_index", lambda *args, **kwargs: {})

    assets = [
        {"media_key": "theshow", "asset_type": "main", "path": "/tmp/organized/The Show/poster.jpg"},
        {"media_key": "theshow", "asset_type": "season", "season_number": 1, "path": "/tmp/organized/The Show/Season01.jpg"},
        {"media_key": "theshow", "asset_type": "season", "season_number": 2, "path": "/tmp/organized/The Show/Season02.jpg"},
    ]
    monkeypatch.setattr(service, "_discover_local_assets", lambda destination: assets)

    processed_paths = []

    def _fake_upload_asset(asset, index, dry_run, **kwargs):
        processed_paths.append(asset["path"])
        return 1, True, 1, 1, service._empty_media_upload_counts()

    monkeypatch.setattr(service, "_upload_asset", _fake_upload_asset)

    result = service.run_single_upload(
        media_type="series",
        title="The Show",
        year=None,
        season_number=2,
        dry_run=True,
    )

    assert result["success"] is True
    assert result["stats"]["scanned"] == 1
    assert processed_paths == ["/tmp/organized/The Show/Season02.jpg"]


def test_run_single_upload_prefers_id_matched_asset_over_title_overlap(test_db, monkeypatch):
    """Single upload should select ID-matched movie asset instead of title-overlap collection-style folder."""
    service = PlexUploadService(test_db)

    monkeypatch.setattr(service, "_get_destination_dir", lambda: "/tmp/organized")
    monkeypatch.setattr(service, "_get_plex_instances", lambda: [{"name": "Plex", "url": "http://plex", "api_key": "token"}])
    monkeypatch.setattr(service, "_get_selected_libraries", lambda instances: ({"Plex": [{"key": "1", "type": "movie", "title": "Movies"}]}, None))
    monkeypatch.setattr(
        service,
        "_build_plex_index",
        lambda instances, selected: (
            {"movies": {}, "shows": {}, "collections": {}},
            [{"instance": "Plex", "library": "Movies", "section_type": "movie", "items": 0, "collections": 0}],
        ),
    )
    monkeypatch.setattr(service, "_build_arr_availability_index", lambda *args, **kwargs: {})

    assets = [
        {
            "media_key": "zootopia",
            "asset_type": "main",
            "path": "/tmp/organized/Zootopia/poster.jpg",
        },
        {
            "media_key": "zootopia2016",
            "asset_type": "main",
            "path": "/tmp/organized/Zootopia (2016) {tmdb-269149} {imdb-tt2948356}/poster.jpg",
        },
    ]
    monkeypatch.setattr(service, "_discover_local_assets", lambda destination: assets)

    processed_paths = []

    def _fake_upload_asset(asset, index, dry_run, **kwargs):
        processed_paths.append(asset["path"])
        return 1, True, 1, 1, service._empty_media_upload_counts()

    monkeypatch.setattr(service, "_upload_asset", _fake_upload_asset)

    result = service.run_single_upload(
        media_type="movie",
        title="Zootopia",
        year=2016,
        tmdb_id=269149,
        imdb_id="tt2948356",
        dry_run=True,
    )

    assert result["success"] is True
    assert result["stats"]["scanned"] == 1
    assert processed_paths == ["/tmp/organized/Zootopia (2016) {tmdb-269149} {imdb-tt2948356}/poster.jpg"]


def test_plex_upload_library_override_defaults(client):
    """Plex Upload library override should default to disabled with empty configs."""
    response = client.get("/api/posterflow/plex-upload/library-override")
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False
    assert data["configs"] == []
    assert data["global_configs"] == []


def test_plex_upload_library_override_round_trip(client, test_db):
    """Plex Upload library override settings should persist and return global configs."""
    global_config = [
        {
            "instance_name": "Main Plex",
            "libraries": [
                {"title": "Movies", "key": "1", "type": "movie", "enabled": True},
                {"title": "TV Shows", "key": "2", "type": "show", "enabled": True},
            ],
        }
    ]
    test_db.add(Setting(key="plex_library_config", value=json.dumps(global_config)))
    test_db.commit()

    payload = {
        "enabled": True,
        "configs": [
            {
                "instance_name": "Main Plex",
                "libraries": [
                    {"title": "Movies", "key": "1", "type": "movie", "enabled": True},
                    {"title": "TV Shows", "key": "2", "type": "show", "enabled": False},
                ],
            }
        ],
    }

    save_response = client.post("/api/posterflow/plex-upload/library-override", json=payload)
    assert save_response.status_code == 200
    save_data = save_response.json()
    assert save_data["success"] is True
    assert save_data["enabled"] is True
    assert len(save_data["configs"]) == 1
    assert len(save_data["global_configs"]) == 1

    get_response = client.get("/api/posterflow/plex-upload/library-override")
    assert get_response.status_code == 200
    get_data = get_response.json()
    assert get_data["enabled"] is True
    assert get_data["configs"][0]["instance_name"] == "Main Plex"
    assert get_data["configs"][0]["libraries"][0]["enabled"] is True
    assert get_data["configs"][0]["libraries"][1]["enabled"] is False
    assert get_data["global_configs"][0]["instance_name"] == "Main Plex"


def test_plex_upload_cache_defaults(client):
    """Plex upload cache endpoint should return empty summary by default."""
    response = client.get("/api/posterflow/plex-upload/upload-cache")
    assert response.status_code == 200
    data = response.json()
    assert data["entries_count"] == 0
    assert data["total_library_refs"] == 0
    assert data["total_edition_refs"] == 0
    assert data["entries"] == []


def test_plex_upload_cache_clear_round_trip(client, test_db):
    """Plex upload cache clear endpoint should clear one file and all files."""
    test_db.add(PlexUploadRecord(
        file_path="/tmp/a.jpg",
        file_hash=None,
        uploaded_to_libraries=json.dumps(["Movies"]),
        uploaded_to_library_keys=json.dumps([]),
        uploaded_editions=json.dumps(["default"]),
        uploaded_media_types=json.dumps([]),
    ))
    test_db.add(PlexUploadRecord(
        file_path="/tmp/b.jpg",
        file_hash=None,
        uploaded_to_libraries=json.dumps(["TV Shows"]),
        uploaded_to_library_keys=json.dumps([]),
        uploaded_editions=json.dumps([]),
        uploaded_media_types=json.dumps([]),
    ))
    test_db.commit()

    get_response = client.get("/api/posterflow/plex-upload/upload-cache")
    assert get_response.status_code == 200
    get_data = get_response.json()
    assert get_data["entries_count"] == 2

    clear_one_response = client.post(
        "/api/posterflow/plex-upload/upload-cache/clear",
        json={"file_path": "/tmp/a.jpg"},
    )
    assert clear_one_response.status_code == 200
    clear_one_data = clear_one_response.json()
    assert clear_one_data["success"] is True
    assert clear_one_data["removed"] == 1
    assert clear_one_data["entries_count"] == 1

    clear_all_response = client.post("/api/posterflow/plex-upload/upload-cache/clear", json={})
    assert clear_all_response.status_code == 200
    clear_all_data = clear_all_response.json()
    assert clear_all_data["success"] is True
    assert clear_all_data["removed"] == 1
    assert clear_all_data["entries_count"] == 0


def test_plex_upload_cache_export(client, test_db):
    """Plex upload cache export endpoint should return downloadable JSON payload."""
    for fp, libs, editions, media_types in [
        ("/tmp/a.jpg", ["Movies"], ["default_edition"], ["movies"]),
        ("/tmp/The Show/poster.jpg", ["Movies"], [], ["shows"]),
        ("/tmp/The Show/Season01.jpg", ["Movies"], [], ["seasons"]),
        ("/tmp/Zeta Collection/poster.jpg", ["Movies"], [], ["collections"]),
        ("/tmp/Alpha Collection/poster.jpg", ["Movies"], [], ["collections"]),
    ]:
        test_db.add(PlexUploadRecord(
            file_path=fp,
            file_hash=None,
            uploaded_to_libraries=json.dumps(libs),
            uploaded_to_library_keys=json.dumps([]),
            uploaded_editions=json.dumps(editions),
            uploaded_media_types=json.dumps(media_types),
        ))
    test_db.commit()

    response = client.get("/api/posterflow/plex-upload/upload-cache/export")
    assert response.status_code == 200
    payload = response.json()
    assert payload["entries_count"] == 5
    assert "exported_at" in payload
    assert "cache" not in payload
    assert payload["library_totals"]["totals"] == {
        "libraries": 1,
        "collections": 2,
        "movies": 1,
        "shows": 1,
        "seasons": 1,
    }
    assert payload["library_totals"]["per_library"]["Movies"] == {
        "collections": 2,
        "movies": 1,
        "shows": 1,
        "seasons": 1,
    }
    assert "grouped_by_library" in payload
    assert "Movies" in payload["grouped_by_library"]
    grouped_movies = payload["grouped_by_library"]["Movies"]
    assert list(grouped_movies.keys()) == ["totals", "collections", "movies", "shows"]
    assert grouped_movies["totals"] == {
        "collections": 2,
        "movies": 1,
        "shows": 1,
        "seasons": 1,
    }
    assert grouped_movies["movies"][0]["file_path"] == "/tmp/a.jpg"
    assert grouped_movies["movies"][0]["uploaded_editions"] == ["default_edition"]
    assert [item["title"] for item in grouped_movies["collections"]] == ["Alpha Collection", "Zeta Collection"]
    assert len(grouped_movies["shows"]) == 1
    assert grouped_movies["shows"][0]["title"] == "The Show"
    assert grouped_movies["shows"][0]["counts"] == {"show_posters": 1, "seasons": 1}
    assert grouped_movies["shows"][0]["show_posters"][0]["file_path"] == "/tmp/The Show/poster.jpg"
    assert grouped_movies["shows"][0]["seasons"][0]["file_path"] == "/tmp/The Show/Season01.jpg"
    assert grouped_movies["shows"][0]["seasons"][0]["season_number"] == 1
    assert grouped_movies["shows"][0]["seasons"][0]["uploaded_media_types"] == ["seasons"]


def test_plex_upload_cache_entries_pagination_and_search(client, test_db):
    """Paginated cache entries endpoint should support offset/limit and text filtering."""
    for fp, libs, media_types, editions in [
        ("/tmp/alpha_movie.jpg", ["Movies"], ["movies"], ["default"]),
        ("/tmp/beta_show_poster.jpg", ["TV Shows"], ["shows"], []),
        ("/tmp/gamma_collection.jpg", ["Collections"], ["collections"], []),
    ]:
        test_db.add(PlexUploadRecord(
            file_path=fp,
            file_hash=None,
            uploaded_to_libraries=json.dumps(libs),
            uploaded_to_library_keys=json.dumps([]),
            uploaded_editions=json.dumps(editions),
            uploaded_media_types=json.dumps(media_types),
        ))
    test_db.commit()

    first_page = client.get(
        "/api/posterflow/plex-upload/upload-cache/entries",
        params={"limit": 2, "offset": 0},
    )
    assert first_page.status_code == 200
    first_data = first_page.json()
    assert first_data["total"] == 3
    assert first_data["limit"] == 2
    assert first_data["offset"] == 0
    assert first_data["has_more"] is True
    assert len(first_data["entries"]) == 2

    second_page = client.get(
        "/api/posterflow/plex-upload/upload-cache/entries",
        params={"limit": 2, "offset": 2},
    )
    assert second_page.status_code == 200
    second_data = second_page.json()
    assert second_data["total"] == 3
    assert second_data["offset"] == 2
    assert second_data["has_more"] is False
    assert len(second_data["entries"]) == 1

    filtered = client.get(
        "/api/posterflow/plex-upload/upload-cache/entries",
        params={"q": "tv shows", "limit": 25, "offset": 0},
    )
    assert filtered.status_code == 200
    filtered_data = filtered.json()
    assert filtered_data["total"] == 1
    assert filtered_data["entries"][0]["file_path"] == "/tmp/beta_show_poster.jpg"


def test_select_local_assets_for_target_collection_suffix_stripped(test_db):
    """_select_local_assets_for_target should find a local asset named 'X' when
    the search title is 'X Collection' (source drives often append the suffix)."""
    service = PlexUploadService(test_db)

    # Destination has folder "Men in Black" (no suffix),
    # but user searched for "Men in Black Collection" (from source drive).
    asset = {
        "media_key": "meninblack",
        "path": "/assets/Men in Black/poster.jpg",
        "display_name": "Men in Black",
        "asset_type": "main",
    }

    result = service._select_local_assets_for_target(
        [asset],
        media_type="collection",
        title="Men in Black Collection",
        year=None,
    )

    assert len(result) == 1
    assert result[0]["media_key"] == "meninblack"


def test_select_local_assets_for_target_collection_no_suffix_also_added(test_db):
    """_select_local_assets_for_target should find a local asset named 'X Collection'
    when the search title is 'X' (reverse case: destination has suffix)."""
    service = PlexUploadService(test_db)

    asset = {
        "media_key": "meninblackcollection",
        "path": "/assets/Men in Black Collection/poster.jpg",
        "display_name": "Men in Black Collection",
        "asset_type": "main",
    }

    result = service._select_local_assets_for_target(
        [asset],
        media_type="collection",
        title="Men in Black",
        year=None,
    )

    assert len(result) == 1
    assert result[0]["media_key"] == "meninblackcollection"


def test_plex_upload_cache_entries_limit_and_offset_safety(client, test_db):
    """Paginated cache endpoint should clamp invalid limit/offset values."""
    test_db.add(PlexUploadRecord(
        file_path="/tmp/one.jpg",
        file_hash=None,
        uploaded_to_libraries=json.dumps(["Movies"]),
        uploaded_to_library_keys=json.dumps([]),
        uploaded_editions=json.dumps([]),
        uploaded_media_types=json.dumps(["movies"]),
    ))
    test_db.commit()

    response = client.get(
        "/api/posterflow/plex-upload/upload-cache/entries",
        params={"limit": 0, "offset": -5},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["limit"] == 1
    assert data["offset"] == 0
    assert data["total"] == 1
    assert len(data["entries"]) == 1


# ---------------------------------------------------------------------------
# _resolve_target_media_type  — folder_year disambiguation
# ---------------------------------------------------------------------------

def _fake_movie(rating_key="m1"):
    return _FakePlexItem("movie", "300", year=2007, rating_key=rating_key)


def _fake_collection(rating_key="c1"):
    return _FakePlexItem("collection", "300", rating_key=rating_key)


def test_resolve_target_media_type_year_bearing_asset_not_redirected_to_collection(test_db):
    """An asset folder WITH a year (movie convention) must not be sent to a
    same-named collection even when both a movie and a collection exist in Plex."""
    service = PlexUploadService(test_db)

    asset = {
        "media_key": "300",
        "path": "/assets/300 (2007) {imdb-tt0416449} {tmdb-1271}/poster.jpg",
        "display_name": "300 (2007)",
        "asset_type": "main",
        "folder_year": 2007,
    }

    result, reason = service._resolve_target_media_type(
        asset,
        media_type_filter=None,
        arr_availability=None,
        movies_raw=[_fake_movie()],
        shows_raw=[],
        collections_raw=[_fake_collection()],
    )

    assert result == "movie", f"expected 'movie' but got {result!r} (reason={reason})"
    assert reason is None


def test_resolve_target_media_type_no_year_asset_goes_to_collection(test_db):
    """An asset folder WITHOUT a year (collection convention) should still be
    directed to the Plex collection when one exists for the same title."""
    service = PlexUploadService(test_db)

    asset = {
        "media_key": "300",
        "path": "/assets/300/poster.jpg",
        "display_name": "300",
        "asset_type": "main",
        "folder_year": None,
    }

    result, reason = service._resolve_target_media_type(
        asset,
        media_type_filter=None,
        arr_availability=None,
        movies_raw=[_fake_movie()],
        shows_raw=[],
        collections_raw=[_fake_collection()],
    )

    assert result == "collection", f"expected 'collection' but got {result!r} (reason={reason})"
    assert reason is None


def test_resolve_target_media_type_explicit_movie_filter_not_redirected(test_db):
    """When media_type_filter='movie' is set explicitly, the result must be
    'movie' even if a collection with that title also exists in Plex."""
    service = PlexUploadService(test_db)

    asset = {
        "media_key": "300",
        "path": "/assets/300 (2007) {tmdb-1271}/poster.jpg",
        "display_name": "300",
        "asset_type": "main",
        "folder_year": 2007,
    }

    result, reason = service._resolve_target_media_type(
        asset,
        media_type_filter="movie",
        arr_availability=None,
        movies_raw=[_fake_movie()],
        shows_raw=[],
        collections_raw=[_fake_collection()],
    )

    assert result == "movie", f"expected 'movie' but got {result!r} (reason={reason})"
    assert reason is None
