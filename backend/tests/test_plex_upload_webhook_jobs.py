"""Webhook/manual background jobs: run_full_upload orchestration, job outcomes,
cache gates, series+season passes, and retry behavior (targeted index + ARR reconnect)."""

import json
from pathlib import Path

from models.setting import Setting
from models.job import Job
from services.plex_upload import PlexUploadService


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
        def set_arr_instance_scope(self, _arr_instance=None):
            pass

        def __init__(self, _db, **_kwargs):
            pass

        def prepare_webhook_context(self, **kwargs):
            captured["targeted_context"] = kwargs
            return None

        def run_single_upload(self, **_kwargs):
            return {
                "success": True,
                "stats": {
                    "scanned": 1,
                    "matched": 1,
                    "uploaded": 0,
                    "would_upload": 1,
                    "plex_targets": 1,
                    "multi_library_assets": 0,
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
            "tmdb_id": 603,
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

    # Manual single upload should build a targeted (single-item) index, not a full scan.
    assert captured.get("targeted_context") is not None
    assert captured["targeted_context"]["tmdb_id"] == 603
    assert captured["targeted_context"]["title"] == "The Matrix"
    assert captured["targeted_context"]["year"] == 1999
    assert captured["targeted_context"]["media_type"] == "movie"
    assert captured["targeted_context"]["allow_full_fallback"] is True


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

        def prepare_webhook_context(self, **_kwargs):
            return None

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
                    "plex_targets": 0,
                    "multi_library_assets": 0,
                    "skipped": 0,
                    "errors": 0,
                },
            }

        def is_series_show_poster_cached(self, **_kwargs):
            return False

        def set_arr_instance_scope(self, _arr_instance=None):
            pass

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

    # The otherwise-silent no-asset outcome is now surfaced as a webhook stat.
    stats = upload_module._load_webhook_stats(test_db)
    assert stats["skipped_no_asset"] == 1


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

        def prepare_webhook_context(self, **_kwargs):
            return None

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
                    "plex_targets": 1,
                    "multi_library_assets": 0,
                    "skipped": 1,
                    "errors": 0,
                },
            }

        def clear_upload_cache_for_target(self, **_kwargs):
            self.__class__.clear_calls += 1
            return 1

        def is_series_show_poster_cached(self, **_kwargs):
            return False

        def set_arr_instance_scope(self, _arr_instance=None):
            pass

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

        def prepare_webhook_context(self, **_kwargs):
            return None

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
                    "plex_targets": 1,
                    "multi_library_assets": 0,
                    "skipped": 0,
                    "errors": 0,
                },
            }

        def is_series_show_poster_cached(self, **_kwargs):
            return False

        def set_arr_instance_scope(self, _arr_instance=None):
            pass

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

        def prepare_webhook_context(self, **_kwargs):
            return None

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
                    "plex_targets": 1,
                    "multi_library_assets": 0,
                    "skipped": 0,
                    "errors": 0,
                },
            }

        def is_series_show_poster_cached(self, **_kwargs):
            return False

        def set_arr_instance_scope(self, _arr_instance=None):
            pass

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

        def prepare_webhook_context(self, **_kwargs):
            return None

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
                    "plex_targets": 1,
                    "multi_library_assets": 0,
                    "skipped": 0,
                    "errors": 0,
                },
            }

        def is_series_show_poster_cached(self, **_kwargs):
            return False

        def set_arr_instance_scope(self, _arr_instance=None):
            pass

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


def test_webhook_background_job_retry_rebuilds_targeted_index(test_db, monkeypatch):
    """On retry attempts, prepare_webhook_context() should be called again (not a full scan)
    so Plex can be re-queried in case it has now finished scanning the item."""
    import modules.upload as upload_module

    job = Job(job_type="Plex Upload Webhook", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()
    job_id = job.id

    monkeypatch.setattr("modules.upload.SessionLocal", lambda: test_db)
    monkeypatch.setattr("modules.upload.add_job_log_handler", lambda *args, **kwargs: 1)
    monkeypatch.setattr("modules.upload.remove_job_log_handler", lambda *args, **kwargs: None)
    monkeypatch.setattr("modules.upload.time.sleep", lambda _seconds: None)

    context_calls = []
    run_calls = []

    class _FakePlexUploadService:
        ERROR_INDEX_BUILD_FAILED = "Unable to build Plex index from configured instances/libraries."

        def __init__(self, _db, **_kwargs):
            pass

        def prepare_webhook_context(self, **_kwargs):
            context_calls.append(1)
            return None

        def is_single_target_fully_cached(self, **_kwargs):
            return False

        def is_series_show_poster_cached(self, **_kwargs):
            return False

        def run_single_upload(self, **_kwargs):
            run_calls.append(1)
            # Fail on attempt 1 with a retryable preflight error, succeed on attempt 2.
            if len(run_calls) == 1:
                return {"success": False, "error": "Unable to build Plex index from configured instances/libraries."}
            return {
                "success": True,
                "stats": {
                    "scanned": 1,
                    "matched": 1,
                    "uploaded": 1,
                    "plex_targets": 1,
                    "multi_library_assets": 0,
                    "skipped": 0,
                    "errors": 0,
                },
            }

        def invalidate_arr_availability_cache(self) -> None:
            pass

        def arr_availability_was_incomplete(self) -> bool:
            return False

        def set_arr_instance_scope(self, _arr_instance=None):
            pass

        def invalidate_preflight_cache(self) -> None:
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
        "title": "The Matrix",
        "year": 1999,
        "season_number": None,
        "tmdb_id": 603,
        "tvdb_id": None,
        "imdb_id": "tt0133093",
    }

    upload_module.run_plex_webhook_background_job(
        job_id,
        parsed_payload,
        False,
        False,
        2,   # 2 attempts
        1,
    )

    refreshed = test_db.query(Job).filter(Job.id == job_id).first()
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert len(run_calls) == 2

    # prepare_webhook_context should have been called once for the initial build
    # and once again for the retry — not a full scan fallback.
    assert len(context_calls) == 2, (
        f"Expected prepare_webhook_context called twice (initial + retry), got {len(context_calls)}"
    )


# ---------------------------------------------------------------------------
# Webhook retries: don't reconnect to all ARR instances on Plex-scan-lag retries
# ---------------------------------------------------------------------------


def test_arr_availability_incomplete_flag_tracks_connection_failures(test_db, monkeypatch):
    import services.plex_upload as svc_mod

    service = PlexUploadService(test_db)
    monkeypatch.setattr(
        service,
        "_get_arr_instances",
        lambda key: (
            [{"name": "A", "url": "u1", "api_key": "k"}, {"name": "B", "url": "u2", "api_key": "k"}]
            if key == PlexUploadService.SETTING_RADARR_INSTANCES
            else []
        ),
    )

    class _FakeClient:
        def __init__(self, ok):
            self.connect_status = ok

        def get_parsed_media(self, include_unmonitored=True):
            return []

    # Second instance fails to connect -> build is incomplete.
    seq = []

    def _make(url, api_key, itype, logger=None):
        seq.append(1)
        return _FakeClient(len(seq) == 1)

    monkeypatch.setattr(svc_mod, "create_arr_client", _make)
    service._build_arr_availability_index(media_type_filter="movie")
    assert service.arr_availability_was_incomplete() is True

    # Both instances connect -> build is complete.
    monkeypatch.setattr(svc_mod, "create_arr_client", lambda *a, **k: _FakeClient(True))
    service._build_arr_availability_index(media_type_filter="movie")
    assert service.arr_availability_was_incomplete() is False


def test_arr_availability_scope_limits_to_firing_instance(test_db, monkeypatch):
    import services.plex_upload as svc_mod

    service = PlexUploadService(test_db)
    monkeypatch.setattr(
        service,
        "_get_arr_instances",
        lambda key: (
            [{"name": "Sonarr", "url": "u-sonarr", "api_key": "k"},
             {"name": "Sonarr 4k", "url": "u-sonarr4k", "api_key": "k"}]
            if key == PlexUploadService.SETTING_SONARR_INSTANCES
            else []
        ),
    )

    class _FakeClient:
        connect_status = True

        def get_parsed_media(self, include_unmonitored=True):
            return []

    calls: list[str] = []

    def _make(url, api_key, itype, logger=None):
        calls.append(url)
        return _FakeClient()

    monkeypatch.setattr(svc_mod, "create_arr_client", _make)

    # No scope -> every Sonarr instance is queried.
    service._build_arr_availability_index(media_type_filter="series")
    assert calls == ["u-sonarr", "u-sonarr4k"]

    # Scope to the firing instance -> only that instance is connected to.
    calls.clear()
    service.set_arr_instance_scope("Sonarr")
    service._build_arr_availability_index(media_type_filter="series")
    assert calls == ["u-sonarr"]

    # An unknown scope falls back to all instances (never connects to nothing).
    calls.clear()
    service.set_arr_instance_scope("Nonexistent")
    service._build_arr_availability_index(media_type_filter="series")
    assert calls == ["u-sonarr", "u-sonarr4k"]


def _run_retry_job_counting_invalidations(test_db, monkeypatch, incomplete):
    import modules.upload as upload_module

    job = Job(job_type="Plex Upload Webhook", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()
    job_id = job.id

    monkeypatch.setattr("modules.upload.SessionLocal", lambda: test_db)
    monkeypatch.setattr("modules.upload.add_job_log_handler", lambda *a, **k: 1)
    monkeypatch.setattr("modules.upload.remove_job_log_handler", lambda *a, **k: None)
    monkeypatch.setattr("modules.upload.time.sleep", lambda _s: None)

    runs = []
    invalidations = []

    class _Fake:
        ERROR_INDEX_BUILD_FAILED = "Unable to build Plex index from configured instances/libraries."

        def __init__(self, _db, **k):
            pass

        def set_arr_instance_scope(self, _arr_instance=None):
            pass

        def prepare_webhook_context(self, **k):
            return None

        def is_single_target_fully_cached(self, **k):
            return False

        def is_series_show_poster_cached(self, **k):
            return False

        def run_single_upload(self, **k):
            runs.append(1)
            if len(runs) == 1:
                return {"success": False, "error": _Fake.ERROR_INDEX_BUILD_FAILED}
            return {
                "success": True,
                "stats": {"scanned": 1, "matched": 1, "uploaded": 1,
                          "plex_targets": 1, "multi_library_assets": 0,
                          "skipped": 0, "errors": 0},
            }

        def invalidate_arr_availability_cache(self):
            invalidations.append(1)

        def arr_availability_was_incomplete(self):
            return incomplete

        def invalidate_preflight_cache(self):
            pass

        def invalidate_local_assets_cache(self):
            pass

        def _get_destination_dir(self):
            from pathlib import Path
            return Path("/tmp")

        def _get_local_assets(self, _d):
            return [{"media_key": "x", "asset_type": "main"}]

        def _select_local_assets_for_target(self, a, **k):
            return a

    monkeypatch.setattr("modules.upload.PlexUploadService", _Fake)
    upload_module.run_plex_webhook_background_job(
        job_id,
        {"media_type": "movie", "title": "X", "year": 2020, "season_number": None, "tmdb_id": 1},
        False, False, 2, 1,
    )
    return len(invalidations)


def test_webhook_retry_skips_arr_reconnect_when_availability_complete(test_db, monkeypatch):
    # End-of-job cleanup always invalidates once; with a complete availability build
    # the retry adds no extra invalidation (i.e. no reconnect to all ARR instances).
    assert _run_retry_job_counting_invalidations(test_db, monkeypatch, incomplete=False) == 1


def test_webhook_retry_rebuilds_arr_when_availability_was_incomplete(test_db, monkeypatch):
    # A prior incomplete build -> retry invalidates (1) + end-of-job cleanup (1) = 2.
    assert _run_retry_job_counting_invalidations(test_db, monkeypatch, incomplete=True) == 2
