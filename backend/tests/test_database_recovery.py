# Uses its own SQLite file: test_db runs inside one outer transaction, so a second
# connection could not see what survives a failed write.
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import modules.artwork_sync as artwork_sync_module
import modules.sync as sync_module
from core.logging import LogTags
from database import Base
from models.artwork import Artwork
from models.artwork_drive import ArtworkDrive
from models.drive import Drive
from models.job import Job, JOB_STATUSES_RECENT_TERMINAL, mark_job_failed
from models.plex_upload import PlexUploadRecord
from models.poster import Poster
from models.setting import Setting
from modules.upload import _mark_job_failed
from services.artwork_sync import ArtworkSyncService
from services.plex_upload import AssetOutcome, PlexUploadService
from services.poster_sync import PosterSyncService


@pytest.fixture
def sessions(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'recovery.db'}", poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(autocommit=False, autoflush=False, bind=engine)
    engine.dispose()


def _failing_commit(db, failures, when=lambda: True):
    """A commit that fails once with a real IntegrityError, leaving the session needing a rollback."""
    original = db.commit

    def commit():
        if not failures and when():
            db.add(Setting(key="unique", value="duplicate"))
        try:
            original()
        except IntegrityError as error:
            failures.append(error)
            raise

    return commit


def _job_message_pending(db, fragment):
    return lambda: any(isinstance(o, Job) and fragment in (o.message or "") for o in db.dirty)


def _image(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"image")
    return path


def _add_drives(db, tmp_path, asset, count=2, remote=False):
    """Custom drives named 'Drive N' with one image each; returns (drives, image paths)."""
    drives, paths = [], []
    for index in range(count):
        folder = tmp_path / f"{asset}-{index}"
        paths.append(_image(folder if asset == "poster" else folder / "logos", f"{index}.png"))
        common = dict(name=f"Drive {index}", drive_id=f"{asset}-{index}", subscribed=True,
                      sync_enabled=remote, is_custom=True, custom_path=str(folder))
        drives.append(Drive(style_type="Custom", **common) if asset == "poster" else ArtworkDrive(**common))
    db.add_all(drives)
    return drives, paths


def _add_job(db, status="pending"):
    job = Job(job_type="Sync All", status=status)
    db.add_all([job, Setting(key="unique", value="original")])
    db.commit()
    return job.id


def _duplicate_second_drive_path(service_model, asset, paths):
    """Make 'Drive 1' insert the path 'Drive 0' already saved, so its write hits the UNIQUE constraint."""
    original = service_model._new_content_row

    def new_row(service, gdrive_id, file_info):
        row = original(service, gdrive_id, file_info)
        if gdrive_id == f"{asset}-1":
            row.file_path = str(paths[0])
        return row

    return patch.object(service_model, "_new_content_row", new_row)


ASSETS = {
    "poster": (Drive, Poster, PosterSyncService, sync_module._sync_all_poster_drives),
    "artwork": (ArtworkDrive, Artwork, ArtworkSyncService, sync_module._sync_all_artwork_drives),
}


# ---- marking a job failed ---------------------------------------------------------------

@pytest.mark.parametrize("helper", ["shared", "upload"])
def test_job_is_marked_failed_after_a_failed_write(sessions, helper):
    with sessions() as db:
        job_id = _add_job(db, status="running")
        db.add(Setting(key="unique", value="duplicate"))
        with pytest.raises(IntegrityError) as failure:
            db.flush()
        assert not db.is_active

        if helper == "shared":
            mark_job_failed(db, job_id, failure.value)
        else:
            _mark_job_failed(db, job_id=job_id, completion_label="Asset upload failed",
                             error_message=str(failure.value), failure_context="asset upload")

    with sessions() as observer:
        saved = observer.get(Job, job_id)
        assert (saved.status, saved.progress) == ("failed", 100)
        assert "UNIQUE" in saved.error
        assert saved.completed_at is not None


# ---- progress updates ------------------------------------------------------------------

@pytest.mark.parametrize("status", JOB_STATUSES_RECENT_TERMINAL)
def test_late_progress_does_not_change_a_finished_job(sessions, status):
    with sessions() as db:
        job = Job(job_type="Sync All", status=status, progress=100, message="Final")
        db.add(job)
        db.commit()
        job_id = job.id
        sync_module._build_progress_callback(db, job_id, LogTags.SYNC)("syncing", 10, 100, "Late")

    with sessions() as observer:
        saved = observer.get(Job, job_id)
        assert (saved.status, saved.progress, saved.message) == (status, 100, "Final")


def test_a_failed_progress_write_does_not_block_the_next_one(sessions):
    with sessions() as db:
        job_id = _add_job(db, status="running")
        failures = []
        progress = sync_module._build_progress_callback(db, job_id, LogTags.SYNC)
        with patch.object(db, "commit", _failing_commit(db, failures)):
            progress("syncing", 20, 100, "First")
            progress("syncing", 60, 100, "Second")
        assert len(failures) == 1

    with sessions() as observer:
        saved = observer.get(Job, job_id)
        assert (saved.status, saved.progress, saved.message) == ("running", 60, "Second")


# ---- single-drive sync ------------------------------------------------------------------

@pytest.mark.parametrize("asset", ["poster", "artwork"])
def test_single_sync_finishes_at_100_with_its_final_message(sessions, tmp_path, asset):
    _drive_model, _content_model, service_model, _run = ASSETS[asset]
    with sessions() as db:
        drives, _paths = _add_drives(db, tmp_path, asset, count=1)
        job_id = _add_job(db)
        progress = sync_module._build_progress_callback(db, job_id, LogTags.SYNC)

        result = service_model(db).sync_drive(drives[0].id, job_id, progress_callback=progress)

        assert result["success"] is True
    with sessions() as observer:
        saved = observer.get(Job, job_id)
        assert (saved.status, saved.progress) == ("completed", 100)
        assert saved.message == "Sync complete: 1 added"


def test_single_sync_fast_path_finishes_at_100(sessions, tmp_path):
    with sessions() as db:
        drives, _paths = _add_drives(db, tmp_path, "poster", count=1, remote=True)
        job_id = _add_job(db)
        service = PosterSyncService(db)
        progress = sync_module._build_progress_callback(db, job_id, LogTags.SYNC)
        with patch.object(service.rclone, "sync_folder", return_value={"success": True, "files_transferred": 0}):
            assert service.sync_drive(drives[0].id, job_id)["success"] is True
            assert service.sync_drive(drives[0].id, job_id, progress_callback=progress)["success"] is True

    with sessions() as observer:
        saved = observer.get(Job, job_id)
        assert (saved.status, saved.progress, saved.message) == ("completed", 100, "No changes detected")


def test_single_sync_write_failure_reports_the_real_error_and_keeps_the_saved_chunk(sessions, tmp_path):
    folder = tmp_path / "chunk"
    folder.mkdir()
    files = [{"name": f"{i}.jpg", "path": folder / f"{i}.jpg", "size": 5, "mtime": 1.0} for i in range(1002)]
    with sessions() as db:
        drive = Drive(name="Chunk", drive_id="chunk", style_type="Custom", subscribed=True,
                      sync_enabled=False, is_custom=True, custom_path=str(folder))
        db.add(drive)
        job_id = _add_job(db)
        drive_id = drive.id
        service = PosterSyncService(db)
        original = service._new_content_row

        def new_row(gdrive_id, file_info):
            row = original(gdrive_id, file_info)
            if file_info["name"] == "1000.jpg":
                row.file_path = str(files[0]["path"])
            return row

        with patch.object(service, "_scan_local_files", return_value=files), \
             patch.object(service, "_new_content_row", new_row):
            result = service.sync_drive(drive_id, job_id)

        assert result["success"] is False
        assert "UNIQUE" in result["error"]
        assert "rolled back" not in result["error"]
        assert db.is_active
    with sessions() as observer:
        saved = observer.get(Job, job_id)
        assert saved.status == "failed"
        assert saved.error == result["error"]
        assert observer.query(Poster).count() == 501
        assert observer.get(Drive, drive_id).last_synced is None


# ---- multi-drive sync -------------------------------------------------------------------

@pytest.mark.parametrize("asset", ["poster", "artwork"])
def test_batch_sync_finishes_at_100_with_its_final_message(sessions, tmp_path, asset):
    _drive_model, content_model, _service_model, run_batch = ASSETS[asset]
    with sessions() as db:
        _add_drives(db, tmp_path, asset)
        job_id = _add_job(db)

        result = run_batch(db, job_id, skip_discord=True)

        assert result["success"] is True
    with sessions() as observer:
        saved = observer.get(Job, job_id)
        assert (saved.status, saved.progress) == ("completed", 100)
        assert saved.message == "Synced 2 drives: 2 added, 0 updated, 0 deleted"
        assert observer.query(content_model).count() == 2


@pytest.mark.parametrize("asset", ["poster", "artwork"])
@pytest.mark.parametrize("writer", ["callback", "engine"])
def test_batch_progress_write_failure_does_not_fail_the_sync(sessions, tmp_path, asset, writer):
    drive_model, content_model, service_model, _run = ASSETS[asset]
    with sessions() as db:
        drives, _paths = _add_drives(db, tmp_path, asset)
        job_id = _add_job(db)
        drive_ids = [drive.id for drive in drives]
        progress = sync_module._build_progress_callback(db, job_id, LogTags.SYNC) if writer == "callback" else None
        failures = []

        with patch.object(db, "commit", _failing_commit(db, failures, _job_message_pending(db, "(2/2)"))):
            result = service_model(db).sync_multiple_drives(drive_ids, job_id, progress_callback=progress)

        assert len(failures) == 1
        assert (result["success"], result["added"], result["errors"]) == (True, 2, 0)
    with sessions() as observer:
        saved = observer.get(Job, job_id)
        assert (saved.status, saved.progress, saved.error) == ("completed", 100, None)
        assert observer.query(content_model).count() == 2
        assert all(observer.get(drive_model, drive_id).last_synced is not None for drive_id in drive_ids)


def test_batch_rclone_progress_write_failure_does_not_fail_the_sync(sessions, tmp_path):
    with sessions() as db:
        drives, _paths = _add_drives(db, tmp_path, "poster", count=1, remote=True)
        job_id = _add_job(db)
        service = PosterSyncService(db)
        failures = []

        def sync_folders(tasks, max_workers=1, progress_callback=None):
            task = tasks[0]
            with patch.object(db, "commit", _failing_commit(db, failures)):
                progress_callback(0, task["drive_name"], "first.png", 1, 1, "transferring", 1)
            assert db.is_active
            progress_callback(0, task["drive_name"], "second.png", 2, 2, "transferring", 2)
            return {task["result_key"]: {"success": True, "files_transferred": 1}}

        with patch.object(service.rclone, "sync_multiple_folders", sync_folders):
            result = service.sync_multiple_drives([drives[0].id], job_id)

        assert len(failures) == 1
        assert (result["success"], result["added"]) == (True, 1)
    with sessions() as observer:
        saved = observer.get(Job, job_id)
        assert (saved.status, saved.progress) == ("completed", 100)


@pytest.mark.parametrize("asset", ["poster", "artwork"])
def test_batch_database_failure_fails_the_job_and_keeps_saved_drives(sessions, tmp_path, asset):
    drive_model, content_model, service_model, run_batch = ASSETS[asset]
    with sessions() as db:
        drives, paths = _add_drives(db, tmp_path, asset)
        job_id = _add_job(db)
        drive_ids = [drive.id for drive in drives]

        with _duplicate_second_drive_path(service_model, asset, paths):
            result = run_batch(db, job_id, skip_discord=True)

        assert result["success"] is False
        assert result["error"] == "Database update failed for Drive 1"
        assert (result["added"], result["errors"]) == (1, 1)
        sync_module._build_progress_callback(db, job_id, LogTags.SYNC)("syncing", 10, 100, "Late")
    with sessions() as observer:
        saved = observer.get(Job, job_id)
        assert (saved.status, saved.progress) == ("failed", 100)
        assert saved.message == saved.error == "Database update failed for Drive 1"
        assert [row.file_path for row in observer.query(content_model).all()] == [str(paths[0])]
        assert observer.get(drive_model, drive_ids[0]).last_synced is not None
        assert observer.get(drive_model, drive_ids[1]).last_synced is None


def test_late_rclone_progress_does_not_change_a_finished_batch(sessions, tmp_path):
    with sessions() as db:
        drives, _paths = _add_drives(db, tmp_path, "poster", count=1, remote=True)
        job_id = _add_job(db)
        service = PosterSyncService(db)
        captured = []

        def sync_folders(tasks, max_workers=1, progress_callback=None):
            captured.append(progress_callback)
            return {tasks[0]["result_key"]: {"success": True, "files_transferred": 1}}

        with patch.object(service.rclone, "sync_multiple_folders", sync_folders):
            assert service.sync_multiple_drives([drives[0].id], job_id)["success"] is True
        captured[0](0, "Drive 0", "late.png", 2, 2, "transferring", 2)

    with sessions() as observer:
        saved = observer.get(Job, job_id)
        assert (saved.status, saved.progress) == ("completed", 100)
        assert saved.message == "Synced 1 drives: 1 added, 0 updated, 0 deleted"


# ---- job runners ------------------------------------------------------------------------

@pytest.mark.parametrize("failed", ["poster", "artwork"])
def test_combined_sync_stays_failed_when_one_side_fails(sessions, tmp_path, failed):
    with sessions() as db:
        paths = {asset: _add_drives(db, tmp_path, asset)[1] for asset in ("poster", "artwork")}
        job_id = _add_job(db)

        with patch.object(sync_module, "SessionLocal", return_value=db), \
             _duplicate_second_drive_path(ASSETS[failed][2], failed, paths[failed]):
            result = sync_module.run_sync_all_job(job_id, skip_discord=True, sync_posters=True, sync_artwork=True)

        assert result["success"] is False
    label = "Poster sync" if failed == "poster" else "Artwork sync"
    with sessions() as observer:
        saved = observer.get(Job, job_id)
        assert (saved.status, saved.progress) == ("failed", 100)
        assert saved.message == saved.error == f"{label}: Database update failed for Drive 1"
        for asset, (_drive_model, content_model, _service, _run) in ASSETS.items():
            assert observer.query(content_model).count() == (1 if asset == failed else 2)


@pytest.mark.parametrize("runner", ["all", "group", "group-auto"])
def test_runner_reports_the_original_error_when_a_commit_fails(sessions, tmp_path, runner):
    with sessions() as db:
        _add_drives(db, tmp_path, "poster", count=1)
        if runner == "group-auto":
            db.add(Setting(key="unique", value="original"))
            db.commit()
            job_id = None
        else:
            job_id = _add_job(db)
        failures, notified = [], []

        def notify(session, **kwargs):
            notified.append(session.execute(text("SELECT 1")).scalar_one())

        with patch.object(sync_module, "SessionLocal", return_value=db), \
             patch.object(db, "commit", _failing_commit(db, failures, _job_message_pending(db, "Preparing to sync"))), \
             patch.object(sync_module, "send_discord_notification", notify), \
             patch.object(sync_module, "send_major_error_notification", lambda *a, **k: None), \
             pytest.raises(IntegrityError):
            if runner == "all":
                sync_module.run_sync_all_job(job_id)
            else:
                sync_module.run_sync_group_job("Custom", job_id)

        assert len(failures) == 1
        assert notified == ([1] if runner == "all" else [])
    with sessions() as observer:
        saved = observer.query(Job).one()
        assert (saved.status, saved.progress) == ("failed", 100)
        assert "UNIQUE" in saved.error
        assert "rolled back" not in saved.error


@pytest.mark.parametrize("asset", ["poster", "artwork"])
def test_single_drive_runner_recovers_the_session_before_notifying(sessions, asset):
    module, service_model, run = (
        (sync_module, PosterSyncService, sync_module.run_sync_one_job) if asset == "poster"
        else (artwork_sync_module, ArtworkSyncService, artwork_sync_module.run_artwork_sync_job)
    )
    with sessions() as db:
        job_id = _add_job(db, status="running")
        notified = []

        def sync_drive(service, drive_id, job_id, progress_callback=None):
            db.add(Setting(key="unique", value="duplicate"))
            db.flush()

        def notify(session, **kwargs):
            notified.append(session.execute(text("SELECT 1")).scalar_one())

        with patch.object(module, "SessionLocal", return_value=db), \
             patch.object(service_model, "sync_drive", sync_drive), \
             patch.object(module, "send_discord_notification", notify), \
             patch.object(module, "send_major_error_notification", lambda *a, **k: None), \
             pytest.raises(IntegrityError):
            run(1, job_id)

        assert notified == [1]
    with sessions() as observer:
        saved = observer.get(Job, job_id)
        assert (saved.status, saved.progress) == ("failed", 100)
        assert "UNIQUE" in saved.error


# ---- Plex upload records ----------------------------------------------------------------

def _uploaded_libraries(observer):
    return {row.file_path: row.uploaded_to_libraries for row in observer.query(PlexUploadRecord).all()}


def test_failed_record_save_leaves_the_session_usable(sessions, tmp_path):
    first, second, third = (str(_image(tmp_path, name)) for name in ("first.jpg", "second.jpg", "third.jpg"))
    with sessions() as db:
        db.add(Setting(key="unique", value="original"))
        db.commit()
        service = PlexUploadService(db, upload_delay_ms=0)
        service._mark_uploaded(first, library_name="Movies")
        failures = []

        with patch.object(db, "commit", _failing_commit(db, failures)), pytest.raises(IntegrityError):
            service._mark_uploaded(second, library_name="Movies")

        assert db.is_active
        service._mark_uploaded(third, library_name="Movies")
    with sessions() as observer:
        assert _uploaded_libraries(observer) == {first: '["Movies"]', third: '["Movies"]'}


def test_failed_record_read_leaves_the_session_usable(sessions, tmp_path):
    path = str(_image(tmp_path, "first.jpg"))
    with sessions() as db:
        db.add(Setting(key="unique", value="original"))
        db.commit()
        service = PlexUploadService(db, upload_delay_ms=0)
        service._mark_uploaded(path, library_name="Movies")
        service.invalidate_record_cache()

        def failing_query(*args, **kwargs):
            db.add(Setting(key="unique", value="duplicate"))
            db.flush()

        with patch.object(db, "query", failing_query), pytest.raises(IntegrityError):
            service._get_uploaded_record(path)

        assert db.is_active
        assert service._get_uploaded_record(path)["uploaded_to_libraries"] == ["Movies"]


def test_failed_mtime_backfill_still_returns_the_record(sessions, tmp_path):
    path = str(_image(tmp_path, "first.jpg"))
    with sessions() as db:
        db.add(Setting(key="unique", value="original"))
        db.commit()
        service = PlexUploadService(db, upload_delay_ms=0)
        service._mark_uploaded(path, library_name="Movies")
        db.query(PlexUploadRecord).filter_by(file_path=path).update({"file_mtime": None})
        db.commit()
        service.invalidate_record_cache()
        failures = []

        with patch.object(db, "commit", _failing_commit(db, failures)):
            record = service._get_uploaded_record(path)

        assert len(failures) == 1
        assert record["uploaded_to_libraries"] == ["Movies"]
        assert db.is_active
    with sessions() as observer:
        assert observer.query(PlexUploadRecord).one().file_mtime is None


@pytest.mark.parametrize("kind", ["poster", "artwork"])
@pytest.mark.parametrize("error", ["database", "external"])
def test_one_asset_failing_does_not_stop_the_next(sessions, tmp_path, kind, error):
    assets = [{"path": str(_image(tmp_path, name)), "asset_type": "main", "artwork_type": "logo"}
              for name in ("first.jpg", "next.jpg")]
    with sessions() as db:
        db.add(Setting(key="unique", value="original"))
        db.commit()
        service = PlexUploadService(db, upload_delay_ms=0)
        stats = service._build_run_stats(assets, {})

        def upload(asset, *args, **kwargs):
            if asset is assets[0]:
                if error == "database":
                    db.add(Setting(key="unique", value="duplicate"))
                    db.flush()
                db.add(Setting(key="pending", value="kept"))
                raise OSError("Plex unreachable")
            service._mark_uploaded(asset["path"], library_name="Movies")
            return AssetOutcome(1, True, 1, service._empty_media_upload_counts())

        if kind == "poster":
            with patch.object(service, "_upload_asset", upload):
                service._process_assets_for_upload(local_assets=assets, index={}, stats=stats, dry_run=False,
                                                   arr_availability={}, remove_overlay_label=False)
        else:
            with patch.object(service, "_upload_artwork_asset", upload):
                service._process_artwork_for_upload(artwork_assets=assets, index={}, stats=stats, dry_run=False)

        outcome = stats if kind == "poster" else stats["artwork"]
        assert (outcome["errors"], outcome["uploaded"]) == (1, 1)
        assert db.is_active
    with sessions() as observer:
        assert _uploaded_libraries(observer) == {assets[1]["path"]: '["Movies"]'}
        # only a database error discards what the failed asset left pending
        kept = observer.query(Setting).filter_by(key="pending").first()
        assert (kept is not None) is (error == "external")
