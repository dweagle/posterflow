"""Failed-session recovery against real SQLite transactions and independent readers."""

import json
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from core.logging import LogTags
from database import Base
from models.artwork import Artwork
from models.artwork_drive import ArtworkDrive
from models.drive import Drive
from models.job import Job, JOB_STATUSES_RECENT_TERMINAL, mark_job_failed
from models.plex_upload import PlexUploadRecord
from models.poster import Poster
from models.setting import Setting
import modules.sync as sync_module
from modules.upload import _mark_job_failed
from services.artwork_sync import ArtworkSyncService
from services.plex_upload import AssetOutcome, PlexUploadService
from services.poster_sync import PosterSyncService
from services.rclone import RcloneService


@pytest.fixture
def recovery_sessions(tmp_path):
    # test_db wraps an external transaction: it cannot prove commits are durable.
    engine = create_engine(
        f"sqlite:///{tmp_path / 'recovery.db'}", poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    Base.metadata.create_all(engine)  # Models registered by conftest and imports above.
    try:
        yield sessionmaker(bind=engine, autoflush=False)
    finally:
        engine.dispose()


@pytest.mark.parametrize("handler", ["upload", "shared"])
def test_failed_flush_marks_job_durably(recovery_sessions, handler):
    with recovery_sessions() as db:
        job = Job(job_type="Plex Upload", status="running")
        db.add_all([job, Setting(key="unique", value="original")])
        db.commit()
        job_id = job.id
        db.add(Setting(key="unique", value="duplicate"))
        with pytest.raises(IntegrityError, match="UNIQUE") as failure:
            db.flush()
        assert not db.is_active
        if handler == "upload":
            _mark_job_failed(
                db, job_id=job_id, completion_label="Asset upload failed",
                error_message=str(failure.value), failure_context="asset upload",
            )
        else:
            mark_job_failed(db, job_id, failure.value)
        assert db.is_active
        with recovery_sessions() as observer:
            persisted = observer.get(Job, job_id)
            assert persisted.status == "failed"
            assert persisted.error == str(failure.value)
            assert persisted.completed_at is not None
            assert persisted.progress == 100
            assert observer.query(Setting).filter_by(key="unique").one().value == "original"


@pytest.mark.parametrize("runner", ["all", "group", "group-auto"])
@pytest.mark.parametrize("failure_phase", ["startup", "cleanup", "terminal"])
def test_sync_orchestrator_commit_failure_reports_original_error(
    recovery_sessions, tmp_path, runner, failure_phase,
):
    (tmp_path / "poster.jpg").write_bytes(b"poster")
    with recovery_sessions() as db:
        drive = Drive(
            name="Boundary", drive_id="boundary", style_type="Custom", subscribed=True,
            sync_enabled=False, is_custom=True, custom_path=str(tmp_path),
        )
        db.add_all([
            drive, Setting(key="unique", value="original"),
            Poster(drive_id="boundary", file_name="orphan.jpg", file_path=str(tmp_path / "orphan.jpg")),
        ])
        job_id = None
        if runner != "group-auto":
            job = Job(job_type="Sync All", status="pending")
            db.add(job)
            db.flush()
            job_id = job.id
        db.commit()
        original_commit = db.commit
        original_notification = sync_module.send_discord_notification
        failures, reused = [], []

        def commit():
            current_job = db.query(Job).first()
            selected = current_job is not None and (
                (failure_phase == "startup" and (current_job.message or "").startswith("Preparing to sync"))
                or (failure_phase == "cleanup" and db.query(Poster).count() == 0)
                or (failure_phase == "terminal" and current_job.status == "completed")
            )
            if selected and not failures:
                db.add(Setting(key="unique", value="duplicate"))
            try:
                original_commit()
            except IntegrityError as error:
                assert not db.is_active
                failures.append(error)
                raise

        def notify(session, **kwargs):
            # Reporting must recover before querying notification settings, not just in the helper.
            assert session.execute(text("SELECT 1")).scalar_one() == 1
            return original_notification(session, **kwargs)

        def hook(_key, *, success, triggered_by, db):
            assert success is False
            assert db.execute(text("SELECT 1")).scalar_one() == 1
            db.add(Setting(key="reused", value="after failure"))
            db.commit()
            reused.append(True)

        with patch.object(sync_module, "SessionLocal", return_value=db), patch.object(
            db, "commit", commit
        ), patch.object(sync_module, "send_discord_notification", notify), patch.object(
            sync_module, "run_post_job_hook", hook
        ):
            with pytest.raises(IntegrityError, match="UNIQUE") as failure:
                if runner == "all":
                    sync_module.run_sync_all_job(job_id)
                else:
                    sync_module.run_sync_group_job("Custom", job_id)
        assert len(failures) == 1
        assert failure.value is failures[0]
        assert reused == [True]
        with recovery_sessions() as observer:
            persisted = observer.query(Job).one()
            assert persisted.status == "failed"
            assert persisted.progress == 100
            assert persisted.completed_at is not None
            assert persisted.error == str(failure.value)
            assert "PendingRollbackError" not in persisted.error
            expected_name = "poster.jpg" if failure_phase == "terminal" else "orphan.jpg"
            assert [row.file_name for row in observer.query(Poster).all()] == [expected_name]
            assert observer.query(Setting).filter_by(key="unique").one().value == "original"
            assert observer.query(Setting).filter_by(key="reused").one().value == "after failure"


@pytest.mark.parametrize("failed_asset", ["poster", "artwork"])
def test_mixed_sync_keeps_aggregate_failure_and_successful_work(recovery_sessions, tmp_path, failed_asset):
    with recovery_sessions() as db:
        first_paths = {}
        for asset, drive_model in (("poster", Drive), ("artwork", ArtworkDrive)):
            for index in range(2):
                folder = tmp_path / f"{asset}-{index}"
                image_folder = folder if asset == "poster" else folder / "logos"
                image_folder.mkdir(parents=True)
                image = image_folder / f"{index}.png"
                image.write_bytes(b"poster")
                if index == 0:
                    first_paths[asset] = str(image)
                db.add(drive_model(
                    name=f"{asset} {index}", drive_id=f"{asset}-{index}", subscribed=True,
                    sync_enabled=False, is_custom=True, custom_path=str(folder),
                    **({"style_type": "Custom"} if asset == "poster" else {}),
                ))
        job = Job(job_type="Sync All", status="pending")
        db.add(job)
        db.commit()
        job_id = job.id
        service_model = PosterSyncService if failed_asset == "poster" else ArtworkSyncService
        original_new_row = service_model._new_content_row

        def new_row(service, gdrive_id, file_info):
            row = original_new_row(service, gdrive_id, file_info)
            if gdrive_id == f"{failed_asset}-1":
                row.file_path = first_paths[failed_asset]
            return row

        with patch.object(sync_module, "SessionLocal", return_value=db), patch.object(
            service_model, "_new_content_row", new_row
        ):
            result = sync_module.run_sync_all_job(job_id, skip_discord=True, sync_posters=True, sync_artwork=True)
        assert result["success"] is False
        failed_result = result["posters" if failed_asset == "poster" else "artwork"]
        successful_result = result["artwork" if failed_asset == "poster" else "posters"]
        assert failed_result["success"] is False
        assert failed_result["errors"] == failed_result["added"] == 1
        assert successful_result["success"] is True
        assert successful_result["errors"] == 0
        assert successful_result["added"] == 2
        with recovery_sessions() as observer:
            persisted = observer.get(Job, job_id)
            assert persisted.status == "failed"
            assert persisted.error == persisted.message == failed_result["error"]
            assert persisted.progress == 100
            assert persisted.completed_at is not None
            for asset, content_model in (("poster", Poster), ("artwork", Artwork)):
                rows = observer.query(content_model).all()
                assert len(rows) == (1 if asset == failed_asset else 2)
                assert first_paths[asset] in {row.file_path for row in rows}


def test_sync_chunk_failure_retains_501_committed_rows(recovery_sessions, tmp_path):
    folder = tmp_path / "chunk"
    folder.mkdir()
    files = [
        {"name": f"{index}.jpg", "path": folder / f"{index}.jpg", "size": 5, "mtime": 1.0}
        for index in range(1002)
    ]
    with recovery_sessions() as db:
        drive = Drive(
            name="Chunk", drive_id="chunk", style_type="Custom", subscribed=True,
            sync_enabled=False, is_custom=True, custom_path=str(folder),
        )
        job = Job(job_type="Sync: Chunk", status="pending")
        db.add_all([drive, job])
        db.commit()
        drive_id, job_id = drive.id, job.id
        service = PosterSyncService(db)
        original_new_row = service._new_content_row
        constructed = []

        def new_row(gdrive_id, file_info):
            row = original_new_row(gdrive_id, file_info)
            constructed.append(file_info["name"])
            if file_info["name"] == "1000.jpg":
                row.file_path = str(files[0]["path"])
            return row

        with patch.object(service, "_scan_local_files", return_value=files), patch.object(
            service, "_new_content_row", new_row
        ):
            result = service.sync_drive(drive_id, job_id)
        assert result["success"] is False
        assert "UNIQUE" in result["error"]
        assert "This Session's transaction has been rolled back" not in result["error"]
        assert constructed == [f"{index}.jpg" for index in range(1001)]
        assert db.is_active
        with recovery_sessions() as observer:
            persisted = observer.get(Job, job_id)
            assert persisted.status == "failed"
            assert persisted.error == result["error"]
            assert persisted.completed_at is not None
            rows = observer.query(Poster).all()
            assert len(rows) == 501
            assert {row.file_name for row in rows} == {f"{index}.jpg" for index in range(501)}
            assert observer.get(Drive, drive_id).last_synced is None


def test_sync_progress_failed_commit_allows_next_update(recovery_sessions):
    with recovery_sessions() as db:
        job = Job(job_type="Sync progress", status="running")
        db.add_all([job, Setting(key="unique", value="original")])
        db.commit()
        job_id = job.id
        callback = sync_module._build_progress_callback(db, job_id, LogTags.SYNC)
        original_commit = db.commit
        commits, failures = [], []

        def commit():
            commits.append(True)
            if len(commits) == 1:
                db.add(Setting(key="unique", value="duplicate"))
            try:
                original_commit()
            except IntegrityError as error:
                failures.append(error)
                raise

        with patch.object(db, "commit", commit):
            callback("syncing", 20, 100, "First progress")
            callback("syncing", 60, 100, "Second progress")
        assert len(failures) == 1
        assert len(commits) == 2
        assert db.is_active
        with recovery_sessions() as observer:
            persisted = observer.get(Job, job_id)
            assert (persisted.progress, persisted.message, persisted.status) == (
                60, "Second progress", "running",
            )
            assert observer.query(Setting).filter_by(key="unique").one().value == "original"


@pytest.mark.parametrize("asset", ["poster", "artwork"])
@pytest.mark.parametrize("failure_phase", [
    None, "row", "transfer", "preparing", "syncing", "updating",
    "drive-updating", "completed", "failed",
])
def test_batch_production_callbacks_and_durable_results(
    recovery_sessions, tmp_path, asset, failure_phase,
):
    drive_model, content_model, service_model, run_batch = (
        (Drive, Poster, PosterSyncService, sync_module._sync_all_poster_drives)
        if asset == "poster" else
        (ArtworkDrive, Artwork, ArtworkSyncService, sync_module._sync_all_artwork_drives)
    )
    folders = [tmp_path / f"drive-{index}" for index in range(2)]
    paths = []
    for index, folder in enumerate(folders):
        image_folder = folder if asset == "poster" else folder / "logos"
        image_folder.mkdir(parents=True)
        paths.append(image_folder / f"{index}.png")
        paths[-1].write_bytes(b"poster")
    with recovery_sessions() as db:
        drives = [
            drive_model(
                name=f"Drive {index}", drive_id=f"drive-{index}", subscribed=True,
                sync_enabled=failure_phase == "transfer", is_custom=True,
                custom_path=str(folder), **({"style_type": "Custom"} if asset == "poster" else {}),
            )
            for index, folder in enumerate(folders)
        ]
        job = Job(job_type="Sync All", status="pending")
        db.add_all([*drives, job, Setting(key="unique", value="original")])
        db.commit()
        drive_ids, job_id = [drive.id for drive in drives], job.id
        original_builder = sync_module._build_progress_callback
        original_commit = db.commit
        original_new_row = service_model._new_content_row
        events, callbacks, failures = [], [], []

        def fail_commit():
            db.add(Setting(key="unique", value="duplicate"))
            try:
                original_commit()
            except IntegrityError as error:
                assert not db.is_active
                assert "UNIQUE" in str(error)
                failures.append(error)
                raise

        def build_progress(*args, **kwargs):
            production_callback = original_builder(*args, **kwargs)

            def progress(*args):
                events.append(args)
                phase, _current, _total, message = args
                selected = phase == failure_phase or (
                    failure_phase == "drive-updating" and phase == "updating" and "(2/2)" in message
                ) or (failure_phase == "transfer" and "first.png" in message)
                if selected and not failures:
                    with patch.object(db, "commit", fail_commit):
                        production_callback(*args)
                else:
                    production_callback(*args)

            callbacks.append(progress)
            return progress

        def new_row(service, gdrive_id, file_info):
            row = original_new_row(service, gdrive_id, file_info)
            if failure_phase in ("row", "failed") and gdrive_id == "drive-1":
                row.file_path = str(paths[0])
            return row

        def sync_folders(rclone, tasks, max_workers=1, progress_callback=None):
            for task in tasks:
                for filename, count in (("first.png", 1), ("second.png", 2)):
                    progress_callback(
                        task["task_index"], task["drive_name"], filename,
                        count, count, "transferring", count,
                    )
                    assert db.is_active
                with recovery_sessions() as observer:
                    assert "second.png" in observer.get(Job, job_id).message
            return {task["result_key"]: {"success": True, "files_transferred": 1} for task in tasks}

        with patch.object(sync_module, "_build_progress_callback", build_progress), patch.object(
            service_model, "_new_content_row", new_row
        ), patch.object(RcloneService, "sync_multiple_folders", sync_folders):
            result = run_batch(db, job_id, skip_discord=True)
        expected_errors = 2 if failure_phase == "failed" else int(failure_phase is not None)
        expected_added = 1 if failure_phase in ("row", "failed") else 2
        assert len(failures) == int(failure_phase not in (None, "row"))
        assert result["success"] is (failure_phase is None)
        assert result["errors"] == expected_errors
        assert result["added"] == expected_added
        assert result["drives_synced"] == 2
        assert result["updated"] == result["deleted"] == 0
        assert db.is_active
        if failure_phase is not None:
            assert f"{expected_errors} database write error(s)" in result["error"]
            assert "earlier drive commits retained" in result["error"]
            assert events[-1][0] == "failed"
            assert sum(args[0] == "failed" for args in events) == 1
            if failure_phase != "completed":
                assert all(args[0] != "completed" for args in events)
        else:
            assert events[-1][0] == "completed"
        with recovery_sessions() as observer:
            persisted = observer.get(Job, job_id)
            assert persisted.status == ("completed" if failure_phase is None else "failed")
            assert persisted.progress == 100
            assert persisted.completed_at is not None
            assert persisted.error == result.get("error")
            if failure_phase is not None:
                assert persisted.message == result["error"]
            terminal = (persisted.status, persisted.progress, persisted.message, persisted.error)
            assert observer.query(content_model).count() == expected_added
            assert observer.get(drive_model, drive_ids[0]).last_synced is not None
            assert (observer.get(drive_model, drive_ids[1]).last_synced is None) is (
                failure_phase in ("row", "failed")
            )
            assert observer.query(Setting).filter_by(key="unique").one().value == "original"
        callbacks[0]("syncing", 10, 100, "Delayed progress")
        with recovery_sessions() as observer:
            persisted = observer.get(Job, job_id)
            assert (persisted.status, persisted.progress, persisted.message, persisted.error) == terminal


def test_batch_internal_transfer_write_failure_is_not_completed(recovery_sessions, tmp_path):
    (tmp_path / "poster.jpg").write_bytes(b"poster")
    with recovery_sessions() as db:
        drive = Drive(
            name="Remote", drive_id="remote", style_type="Custom", subscribed=True,
            sync_enabled=True, is_custom=True, custom_path=str(tmp_path),
        )
        job = Job(job_type="Sync All", status="pending")
        db.add_all([drive, job, Setting(key="unique", value="original")])
        db.commit()
        drive_id, job_id = drive.id, job.id
        service = PosterSyncService(db)
        original_commit = db.commit
        failures, events = [], []

        def fail_commit():
            db.add(Setting(key="unique", value="duplicate"))
            try:
                original_commit()
            except IntegrityError as error:
                failures.append(error)
                raise

        def sync_folders(tasks, max_workers=1, progress_callback=None):
            task = tasks[0]
            with patch.object(db, "commit", fail_commit):
                progress_callback(0, task["drive_name"], "first.jpg", 1, 1, "transferring", 1)
            assert db.is_active
            progress_callback(0, task["drive_name"], "second.jpg", 2, 2, "transferring", 2)
            with recovery_sessions() as observer:
                assert "second.jpg" in observer.get(Job, job_id).message
            return {task["result_key"]: {"success": True, "files_transferred": 1}}

        with patch.object(service.rclone, "sync_multiple_folders", sync_folders):
            result = service.sync_multiple_drives([drive_id], job_id, progress_callback=lambda *e: events.append(e))
        assert len(failures) == 1
        assert result["success"] is False
        assert result["errors"] == result["added"] == 1
        assert events[-1][0] == "failed"
        assert all(args[0] != "completed" for args in events)
        with recovery_sessions() as observer:
            persisted = observer.get(Job, job_id)
            assert persisted.status == "failed"
            assert persisted.progress == 100
            assert persisted.error == result["error"]
            assert observer.query(Poster).count() == 1


@pytest.mark.parametrize("fail_write", [False, True], ids=["completed", "failed"])
def test_delayed_rclone_callback_keeps_terminal_state(recovery_sessions, tmp_path, fail_write):
    for name in ("first.jpg", "next.jpg"):
        (tmp_path / name).write_bytes(b"poster")
    with recovery_sessions() as db:
        drive = Drive(
            name="Remote", drive_id="remote", style_type="Custom", subscribed=True,
            sync_enabled=True, is_custom=True, custom_path=str(tmp_path),
        )
        job = Job(job_type="Sync All", status="pending")
        db.add_all([drive, job])
        db.commit()
        drive_id, job_id = drive.id, job.id
        service = PosterSyncService(db)
        original_new_row = service._new_content_row
        callbacks = []

        def new_row(gdrive_id, file_info):
            row = original_new_row(gdrive_id, file_info)
            if fail_write and file_info["name"] == "next.jpg":
                row.file_path = str(tmp_path / "first.jpg")
            return row

        def sync_folders(tasks, max_workers=1, progress_callback=None):
            callbacks.append(progress_callback)
            progress_callback(0, "Remote", "first.jpg", 1, 1, "transferring", 1)
            return {tasks[0]["result_key"]: {"success": True, "files_transferred": 1}}

        with patch.object(service.rclone, "sync_multiple_folders", sync_folders), patch.object(
            service, "_new_content_row", new_row
        ):
            result = service.sync_multiple_drives(
                [drive_id], job_id, progress_callback=sync_module._build_progress_callback(
                    db, job_id, LogTags.SYNC, propagate_database_errors=True,
                ),
            )
        assert result["success"] is (not fail_write)
        with recovery_sessions() as observer:
            persisted = observer.get(Job, job_id)
            assert persisted.status == ("failed" if fail_write else "completed")
            assert persisted.progress == 100
            terminal = (persisted.status, persisted.progress, persisted.message, persisted.error, persisted.completed_at)
        # Invoke the actual service callback supplied to rclone after finalization.
        callbacks[0](0, "Remote", "Delayed.jpg", 2, 2, "transferring", 2)
        with recovery_sessions() as observer:
            persisted = observer.get(Job, job_id)
            assert (persisted.status, persisted.progress, persisted.message, persisted.error, persisted.completed_at) == terminal


@pytest.mark.parametrize("asset", ["poster", "artwork"])
@pytest.mark.parametrize("fast_path", [False, True])
def test_single_sync_completed_stays_at_100(recovery_sessions, tmp_path, asset, fast_path):
    drive_model, service_model = (Drive, PosterSyncService) if asset == "poster" else (ArtworkDrive, ArtworkSyncService)
    folder = tmp_path if asset == "poster" else tmp_path / "logos"
    folder.mkdir(exist_ok=True)
    (folder / "poster.png").write_bytes(b"poster")
    with recovery_sessions() as db:
        drive = drive_model(
            name="Single", drive_id="single", subscribed=True, sync_enabled=fast_path,
            is_custom=True, custom_path=str(tmp_path),
            **({"style_type": "Custom"} if asset == "poster" else {}),
        )
        job = Job(job_type="Sync: Single", status="pending")
        db.add_all([drive, job])
        db.commit()
        drive_id, job_id = drive.id, job.id
        service = service_model(db)
        callback = sync_module._build_progress_callback(db, job_id, LogTags.SYNC)
        with patch.object(service.rclone, "sync_folder", return_value={"success": True, "files_transferred": 0}):
            if fast_path:
                assert service.sync_drive(drive_id, job_id)["success"]
                job.status = "pending"
                db.commit()
            assert service.sync_drive(drive_id, job_id, progress_callback=callback)["success"]
        with recovery_sessions() as observer:
            persisted = observer.get(Job, job_id)
            assert persisted.status == "completed"
            assert persisted.progress == 100
            terminal_message = persisted.message
            if fast_path:
                assert terminal_message == "No changes detected"
        callback("syncing", 10, 100, "Delayed progress")
        with recovery_sessions() as observer:
            persisted = observer.get(Job, job_id)
            assert (persisted.status, persisted.progress, persisted.message) == ("completed", 100, terminal_message)


@pytest.mark.parametrize("status", JOB_STATUSES_RECENT_TERMINAL)
def test_progress_guard_applies_to_every_terminal_job(recovery_sessions, status):
    with recovery_sessions() as db:
        job = Job(job_type="Sync", status=status, progress=100, message="Terminal", error="Preserved")
        db.add(job)
        db.commit()
        job_id = job.id
        callback = sync_module._build_progress_callback(db, job_id, LogTags.SYNC)
        callback("updating", 10, 100, "Delayed progress")
        with recovery_sessions() as observer:
            persisted = observer.get(Job, job_id)
            assert (persisted.status, persisted.progress, persisted.message, persisted.error) == (
                status, 100, "Terminal", "Preserved",
            )


@pytest.mark.parametrize("operation", ["read", "mark-query", "mark-commit", "backfill"])
def test_plex_record_failure_recovers_and_retains_good(recovery_sessions, tmp_path, operation):
    first, next_file = tmp_path / "first.jpg", tmp_path / "next.jpg"
    first.write_bytes(b"first")
    next_file.write_bytes(b"next")
    file_path = str(first)
    with recovery_sessions() as db:
        service = PlexUploadService(db, upload_delay_ms=0)
        service._mark_uploaded(file_path, library_name="Good")
        db.add(Setting(key="unique", value="original"))
        if operation == "backfill":
            db.query(PlexUploadRecord).filter_by(file_path=file_path).update({"file_mtime": None})
        db.commit()
        if operation in ("read", "backfill"):
            service.invalidate_record_cache()
        original_write = db.flush if operation in ("read", "mark-query") else db.commit
        failures = []

        def fail_write(*args, **kwargs):
            db.add(Setting(key="unique", value="duplicate"))
            try:
                original_write()
            except IntegrityError as error:
                assert not db.is_active
                assert "UNIQUE" in str(error)
                failures.append(error)
                raise

        method = "query" if operation in ("read", "mark-query") else "commit"
        with patch.object(db, method, fail_write):
            if operation == "backfill":
                record = service._get_uploaded_record(file_path)
                assert record["uploaded_to_libraries"] == ["Good"]
                assert record["file_hash"] == service._compute_file_hash(file_path)
                assert record.get("file_mtime") is None
                assert service._record_cache[file_path].get("file_mtime") is None
            else:
                with pytest.raises(IntegrityError) as failure:
                    if operation == "read":
                        service._get_uploaded_record(file_path)
                    else:
                        service._mark_uploaded(file_path, library_name="Failed")
                assert failure.value is failures[0]
                assert file_path not in service._record_cache
        assert len(failures) == 1
        assert db.is_active
        assert db.execute(text("SELECT 1")).scalar_one() == 1
        assert service._get_uploaded_record(file_path)["uploaded_to_libraries"] == ["Good"]
        service._mark_uploaded(str(next_file), library_name="Next")
        assert service._record_cache[str(next_file)]["uploaded_to_libraries"] == ["Next"]
        with recovery_sessions() as observer:
            rows = observer.query(PlexUploadRecord).all()
            assert {row.file_path: json.loads(row.uploaded_to_libraries) for row in rows} == {
                file_path: ["Good"], str(next_file): ["Next"],
            }
            if operation == "backfill":
                assert observer.query(PlexUploadRecord).filter_by(file_path=file_path).one().file_mtime is None
            assert observer.query(Setting).filter_by(key="unique").one().value == "original"


@pytest.mark.parametrize("asset_kind", ["poster", "artwork"])
@pytest.mark.parametrize("database_error", [True, False], ids=["database", "external"])
def test_per_asset_only_database_errors_rollback(recovery_sessions, tmp_path, asset_kind, database_error):
    assets = []
    for name in ("first.jpg", "next.jpg"):
        path = tmp_path / name
        path.write_bytes(b"poster")
        assets.append({"path": str(path), "asset_type": "main", "artwork_type": "logo"})
    with recovery_sessions() as db:
        db.add(Setting(key="unique", value="original"))
        db.commit()
        service = PlexUploadService(db, upload_delay_ms=0)
        stats = service._build_run_stats(assets, {})
        failures = []

        def upload(asset, *args, **kwargs):
            if asset is assets[0]:
                if database_error:
                    db.add(Setting(key="unique", value="duplicate"))
                    try:
                        db.flush()
                    except IntegrityError as error:
                        failures.append(error)
                        raise
                else:
                    db.add(Setting(key="external_pending", value="preserved"))
                    raise OSError("External upload failed")
            service._mark_uploaded(asset["path"], library_name="Next")
            return AssetOutcome(1, True, 1, service._empty_media_upload_counts())

        method = "_upload_asset" if asset_kind == "poster" else "_upload_artwork_asset"
        with patch.object(service, method, upload):
            if asset_kind == "poster":
                service._process_assets_for_upload(
                    local_assets=assets, index={}, stats=stats, dry_run=False,
                    arr_availability={}, remove_overlay_label=False,
                )
            else:
                service._process_artwork_for_upload(artwork_assets=assets, index={}, stats=stats, dry_run=False)
        outcomes = stats if asset_kind == "poster" else stats["artwork"]
        assert outcomes["errors"] == 1
        assert outcomes["uploaded"] == outcomes["uploaded_files"] == 1
        assert len(failures) == int(database_error)
        assert db.is_active
        assert db.execute(text("SELECT 1")).scalar_one() == 1
        with recovery_sessions() as observer:
            assert observer.query(PlexUploadRecord).filter_by(file_path=assets[1]["path"]).one().uploaded_to_libraries == '["Next"]'
            assert observer.query(Setting).filter_by(key="unique").one().value == "original"
            pending = observer.query(Setting).filter_by(key="external_pending").first()
            assert (pending is not None) is (not database_error)
            if pending is not None:
                assert pending.value == "preserved"
