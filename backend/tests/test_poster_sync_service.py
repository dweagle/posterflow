from models.drive import Drive
from models.job import Job
from models.poster import Poster
from services.poster_sync import PosterSyncService
from core.config import settings


def test_sync_drive_returns_error_for_missing_drive(test_db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "config_dir", tmp_path / "config")
    monkeypatch.setattr(settings, "gdrive_dir", tmp_path / "posters")

    service = PosterSyncService(test_db)

    result = service.sync_drive(drive_id=999999, job_id=1)

    assert result["success"] is False
    assert result["error"] == "Drive not found"


def test_sync_drive_returns_error_for_unsubscribed_drive(test_db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "config_dir", tmp_path / "config")
    monkeypatch.setattr(settings, "gdrive_dir", tmp_path / "posters")

    drive = Drive(
        name="Unsubscribed",
        drive_id="sync-unsub-1",
        style_type="CL2K",
        subscribed=False,
    )
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    service = PosterSyncService(test_db)
    result = service.sync_drive(drive_id=drive.id, job_id=1)

    assert result["success"] is False
    assert result["error"] == "Drive not subscribed"


def test_sync_drive_returns_error_for_missing_job(test_db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "config_dir", tmp_path / "config")
    monkeypatch.setattr(settings, "gdrive_dir", tmp_path / "posters")

    drive = Drive(
        name="Subscribed",
        drive_id="sync-sub-1",
        style_type="MM2K",
        subscribed=True,
    )
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)

    service = PosterSyncService(test_db)
    result = service.sync_drive(drive_id=drive.id, job_id=999999)

    assert result["success"] is False
    assert result["error"] == "Job not found"


def test_sync_drive_completes_with_mocked_rclone_success(test_db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "config_dir", tmp_path / "config")
    monkeypatch.setattr(settings, "gdrive_dir", tmp_path / "posters")

    drive = Drive(
        name="Subscribed",
        drive_id="sync-sub-2",
        style_type="MM2K",
        subscribed=True,
    )
    job = Job(
        job_type="Sync: Subscribed",
        status="pending",
        progress=0,
        message="queued",
    )
    test_db.add(drive)
    test_db.add(job)
    test_db.commit()
    test_db.refresh(drive)
    test_db.refresh(job)

    service = PosterSyncService(test_db)
    monkeypatch.setattr(
        service.rclone,
        "sync_folder",
        lambda drive_id, local_path, drive_name=None, progress_callback=None: {
            "success": True,
            "files_transferred": 0,
        },
    )

    result = service.sync_drive(drive_id=drive.id, job_id=job.id)

    assert result == {"success": True, "added": 0, "updated": 0, "deleted": 0}

    test_db.refresh(job)
    assert job.status == "completed"
    assert job.progress == 100
    assert job.message == "No posters synced"


# ---------------------------------------------------------------------------
# Poster DB tracking — new files create Poster records (added counter)
# ---------------------------------------------------------------------------

def _make_drive_and_job(test_db, drive_id="sync-track-1", name="TrackDrive", style="MM2K"):
    drive = Drive(name=name, drive_id=drive_id, style_type=style, subscribed=True)
    job = Job(job_type=f"Sync: {name}", status="pending", progress=0, message="queued")
    test_db.add(drive)
    test_db.add(job)
    test_db.commit()
    test_db.refresh(drive)
    test_db.refresh(job)
    return drive, job


def test_sync_creates_poster_records_for_new_files(test_db, monkeypatch, tmp_path):
    """After sync, each image file on disk must have a corresponding Poster DB record."""
    monkeypatch.setattr(settings, "config_dir", tmp_path / "config")
    local_dir = tmp_path / "posters" / "MM2K" / "TrackDrive"
    local_dir.mkdir(parents=True)
    monkeypatch.setattr(settings, "gdrive_dir", tmp_path / "posters")

    drive, job = _make_drive_and_job(test_db, drive_id="sync-track-2")

    # Pre-create two poster files on disk
    (local_dir / "poster_one.jpg").write_bytes(b"img1")
    (local_dir / "poster_two.jpg").write_bytes(b"img2")

    service = PosterSyncService(test_db)
    monkeypatch.setattr(
        service.rclone,
        "sync_folder",
        lambda drive_id, local_path, drive_name=None, progress_callback=None: {
            "success": True,
            "files_transferred": 2,
        },
    )

    result = service.sync_drive(drive_id=drive.id, job_id=job.id)

    assert result["success"] is True
    assert result["added"] == 2
    assert result["updated"] == 0
    assert result["deleted"] == 0

    posters = test_db.query(Poster).filter(Poster.drive_id == drive.drive_id).all()
    assert len(posters) == 2
    names = {p.file_name for p in posters}
    assert names == {"poster_one.jpg", "poster_two.jpg"}


def test_sync_updates_poster_record_when_file_changes(test_db, monkeypatch, tmp_path):
    """A file whose mtime has changed since last sync is counted as updated."""
    monkeypatch.setattr(settings, "config_dir", tmp_path / "config")
    local_dir = tmp_path / "posters" / "MM2K" / "TrackDrive2"
    local_dir.mkdir(parents=True)
    monkeypatch.setattr(settings, "gdrive_dir", tmp_path / "posters")

    drive, job = _make_drive_and_job(
        test_db, drive_id="sync-track-3", name="TrackDrive2"
    )

    poster_path = local_dir / "poster.jpg"
    poster_path.write_bytes(b"original")

    # Seed an existing Poster record with an old mtime so it looks stale
    old_poster = Poster(
        drive_id=drive.drive_id,
        file_name="poster.jpg",
        file_path=str(poster_path),
        file_size=8,
        file_mtime=0.0,  # very old mtime → will differ from disk value
    )
    test_db.add(old_poster)
    test_db.commit()

    service = PosterSyncService(test_db)
    monkeypatch.setattr(
        service.rclone,
        "sync_folder",
        lambda drive_id, local_path, drive_name=None, progress_callback=None: {
            "success": True,
            "files_transferred": 1,
        },
    )

    result = service.sync_drive(drive_id=drive.id, job_id=job.id)

    assert result["success"] is True
    assert result["updated"] == 1
    assert result["added"] == 0


def test_sync_removes_db_records_for_deleted_files(test_db, monkeypatch, tmp_path):
    """Post-sync phase 3 self-heal: orphaned DB records (file in DB but gone from disk
    after rclone) are counted as 'deleted' in the returned result."""
    monkeypatch.setattr(settings, "config_dir", tmp_path / "config")
    local_dir = tmp_path / "posters" / "MM2K" / "TrackDrive3"
    local_dir.mkdir(parents=True)
    monkeypatch.setattr(settings, "gdrive_dir", tmp_path / "posters")

    drive, job = _make_drive_and_job(
        test_db, drive_id="sync-track-4", name="TrackDrive3"
    )

    # File exists on disk AND in DB before sync — so pre-sync cleanup leaves it alone
    existing_path = local_dir / "existing.jpg"
    existing_path.write_bytes(b"exists")

    existing_stat = existing_path.stat()
    existing_poster = Poster(
        drive_id=drive.drive_id,
        file_name="existing.jpg",
        file_path=str(existing_path),
        file_size=existing_stat.st_size,
        file_mtime=existing_stat.st_mtime,
    )
    test_db.add(existing_poster)
    test_db.commit()

    def _fake_sync_deletes_file(drive_id, local_path, drive_name=None, progress_callback=None):
        # Simulate rclone removing the file (e.g., it was deleted from the remote)
        existing_path.unlink()
        return {"success": True, "files_transferred": 1}

    service = PosterSyncService(test_db)
    monkeypatch.setattr(service.rclone, "sync_folder", _fake_sync_deletes_file)

    result = service.sync_drive(drive_id=drive.id, job_id=job.id)

    assert result["success"] is True
    assert result["deleted"] == 1

    remaining = test_db.query(Poster).filter(
        Poster.drive_id == drive.drive_id,
        Poster.file_name == "existing.jpg",
    ).first()
    assert remaining is None, "Orphaned poster record should have been removed"


def test_sync_pre_cleanup_removes_stale_db_records_before_rclone(test_db, monkeypatch, tmp_path):
    """Pre-sync cleanup: stale DB records for files missing from disk are removed
    before rclone runs, so they come back as 'added' (not 'unchanged') if re-downloaded."""
    monkeypatch.setattr(settings, "config_dir", tmp_path / "config")
    local_dir = tmp_path / "posters" / "MM2K" / "TrackDrive4"
    local_dir.mkdir(parents=True)
    monkeypatch.setattr(settings, "gdrive_dir", tmp_path / "posters")

    drive, job = _make_drive_and_job(
        test_db, drive_id="sync-track-5", name="TrackDrive4"
    )

    # Seed a DB record for a file that is NOT on disk (simulates a previously-synced
    # file that has since been deleted locally)
    stale_path = local_dir / "stale.jpg"
    stale = Poster(
        drive_id=drive.drive_id,
        file_name="stale.jpg",
        file_path=str(stale_path),
        file_size=50,
        file_mtime=1.0,
    )
    test_db.add(stale)
    test_db.commit()

    # Rclone "re-downloads" the file — write it before service runs
    def _fake_sync(drive_id, local_path, drive_name=None, progress_callback=None):
        stale_path.write_bytes(b"re-downloaded")
        return {"success": True, "files_transferred": 1}

    service = PosterSyncService(test_db)
    monkeypatch.setattr(service.rclone, "sync_folder", _fake_sync)

    result = service.sync_drive(drive_id=drive.id, job_id=job.id)

    assert result["success"] is True
    # The file was re-created, so it should be counted as added (not updated),
    # because the stale record was deleted during pre-sync cleanup.
    assert result["added"] == 1


def test_sync_local_only_drive_finds_new_files_without_rclone(test_db, monkeypatch, tmp_path):
    """Regression: on a local-only drive (sync_enabled=False, rclone skipped so
    files_transferred is always 0), a manually-added file must still be indexed.
    The old 'no changes detected' shortcut only sampled existing DB records, so
    new files were missed by the single-drive sync while the workflow found them."""
    monkeypatch.setattr(settings, "config_dir", tmp_path / "config")
    # Custom drives are stored under Custom/<name> (see Drive.get_local_path)
    local_dir = tmp_path / "posters" / "Custom" / "LocalDrive"
    local_dir.mkdir(parents=True)
    monkeypatch.setattr(settings, "gdrive_dir", tmp_path / "posters")

    drive = Drive(
        name="LocalDrive",
        drive_id="sync-local-1",
        style_type="MM2K",
        subscribed=True,
        sync_enabled=False,
        is_custom=True,
    )
    job = Job(job_type="Sync: LocalDrive", status="pending", progress=0, message="queued")
    test_db.add(drive)
    test_db.add(job)
    test_db.commit()
    test_db.refresh(drive)
    test_db.refresh(job)

    # An already-indexed file with a matching mtime so the sample check sees "no change"
    existing_path = local_dir / "existing.jpg"
    existing_path.write_bytes(b"exists")
    existing_stat = existing_path.stat()
    test_db.add(Poster(
        drive_id=drive.drive_id,
        file_name="existing.jpg",
        file_path=str(existing_path),
        file_size=existing_stat.st_size,
        file_mtime=existing_stat.st_mtime,
    ))
    test_db.commit()

    # A brand-new file dropped into the folder manually (not in the DB, no rclone)
    (local_dir / "brand_new.jpg").write_bytes(b"new")

    service = PosterSyncService(test_db)
    result = service.sync_drive(drive_id=drive.id, job_id=job.id)

    assert result["success"] is True
    assert result["added"] == 1

    names = {
        p.file_name
        for p in test_db.query(Poster).filter(Poster.drive_id == drive.drive_id).all()
    }
    assert names == {"existing.jpg", "brand_new.jpg"}



# ---------------------------------------------------------------------------
# Characterization: self-healing fast path (single-drive) + batch method.
# Pinned before the sync-engine merge so the shared engine can't drift.
# ---------------------------------------------------------------------------

from datetime import datetime  # noqa: E402


def test_sync_fast_path_skips_db_update_when_nothing_changed(test_db, monkeypatch, tmp_path):
    """rclone transfers 0 files, the DB already has rows, and a sampled file still matches
    disk (mtime) → the fast path returns 'No changes detected' and never touches the row."""
    monkeypatch.setattr(settings, "config_dir", tmp_path / "config")
    local_dir = tmp_path / "posters" / "MM2K" / "FastPath"
    local_dir.mkdir(parents=True)
    monkeypatch.setattr(settings, "gdrive_dir", tmp_path / "posters")

    drive, job = _make_drive_and_job(test_db, drive_id="sync-fast-1", name="FastPath")
    drive.sync_enabled = True
    test_db.commit()

    poster_path = local_dir / "poster.jpg"
    poster_path.write_bytes(b"stable")
    st = poster_path.stat()
    stamp = datetime(2020, 1, 1)  # DB stores naive datetimes
    p = Poster(drive_id=drive.drive_id, file_name="poster.jpg", file_path=str(poster_path),
               file_size=st.st_size, file_mtime=st.st_mtime, last_processed=stamp)
    test_db.add(p)
    test_db.commit()

    service = PosterSyncService(test_db)
    monkeypatch.setattr(service.rclone, "sync_folder",
        lambda drive_id, local_path, drive_name=None, progress_callback=None: {"success": True, "files_transferred": 0})

    result = service.sync_drive(drive_id=drive.id, job_id=job.id)

    assert result == {"success": True, "added": 0, "updated": 0, "deleted": 0}
    test_db.refresh(job)
    assert job.message == "No changes detected"
    # The fast path must not reprocess the row.
    test_db.refresh(p)
    assert p.last_processed == stamp


def test_sync_multiple_drives_adds_new_files_across_drives(test_db, monkeypatch, tmp_path):
    """The batch engine scans each drive and inserts Poster rows for new files, aggregating
    the added/deleted counts and drives_synced."""
    monkeypatch.setattr(settings, "config_dir", tmp_path / "config")
    monkeypatch.setattr(settings, "gdrive_dir", tmp_path / "posters")

    d1, _ = _make_drive_and_job(test_db, drive_id="batch-1", name="BatchA")
    d2, _ = _make_drive_and_job(test_db, drive_id="batch-2", name="BatchB")
    job = Job(job_type="Sync All", status="pending", progress=0, message="queued")
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)

    dir1 = tmp_path / "posters" / "MM2K" / "BatchA"
    dir1.mkdir(parents=True)
    dir2 = tmp_path / "posters" / "MM2K" / "BatchB"
    dir2.mkdir(parents=True)
    (dir1 / "a1.jpg").write_bytes(b"a1")
    (dir2 / "b1.jpg").write_bytes(b"b1")
    (dir2 / "b2.jpg").write_bytes(b"b2")

    service = PosterSyncService(test_db)

    def _fake_batch(tasks, max_workers=1, progress_callback=None):
        return {t["result_key"]: {"success": True, "files_transferred": 1} for t in tasks}

    monkeypatch.setattr(service.rclone, "sync_multiple_folders", _fake_batch)

    result = service.sync_multiple_drives([d1.id, d2.id], job_id=job.id)

    assert result["success"] is True
    assert result["drives_synced"] == 2
    assert result["added"] == 3
    assert result["deleted"] == 0
    assert test_db.query(Poster).filter(Poster.drive_id == d1.drive_id).count() == 1
    assert test_db.query(Poster).filter(Poster.drive_id == d2.drive_id).count() == 2


def test_sync_multiple_drives_self_heals_orphaned_records(test_db, monkeypatch, tmp_path):
    """A file present at pre-sync but removed DURING the rclone sync is cleaned from the DB by
    the batch engine's post-scan self-heal (deleted count)."""
    monkeypatch.setattr(settings, "config_dir", tmp_path / "config")
    monkeypatch.setattr(settings, "gdrive_dir", tmp_path / "posters")

    drive, _ = _make_drive_and_job(test_db, drive_id="batch-heal-1", name="BatchHeal")
    drive.sync_enabled = True  # remote drive, so the rclone batch mock runs
    job = Job(job_type="Sync All", status="pending", progress=0, message="queued")
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)

    d = tmp_path / "posters" / "MM2K" / "BatchHeal"
    d.mkdir(parents=True)
    keep = d / "keep.jpg"
    keep.write_bytes(b"keep")
    kst = keep.stat()
    gone = d / "gone.jpg"
    gone.write_bytes(b"gone")
    gst = gone.stat()
    test_db.add(Poster(drive_id=drive.drive_id, file_name="keep.jpg", file_path=str(keep),
                       file_size=kst.st_size, file_mtime=kst.st_mtime))
    test_db.add(Poster(drive_id=drive.drive_id, file_name="gone.jpg", file_path=str(gone),
                       file_size=gst.st_size, file_mtime=gst.st_mtime))
    test_db.commit()

    service = PosterSyncService(test_db)

    def _fake_batch(tasks, max_workers=1, progress_callback=None):
        gone.unlink()  # rclone removed it during the sync
        return {t["result_key"]: {"success": True, "files_transferred": 0} for t in tasks}

    monkeypatch.setattr(service.rclone, "sync_multiple_folders", _fake_batch)

    result = service.sync_multiple_drives([drive.id], job_id=job.id)

    assert result["success"] is True
    assert result["deleted"] == 1
    remaining = {p.file_name for p in test_db.query(Poster).filter(Poster.drive_id == drive.drive_id).all()}
    assert remaining == {"keep.jpg"}


# ---------------------------------------------------------------------------
# Reconciled drift: single-drive sync now matches the batch path.
# ---------------------------------------------------------------------------


def test_sync_single_initial_metadata_fill_does_not_reprocess(test_db, monkeypatch, tmp_path):
    """A legacy row with NULL file_mtime gets its metadata filled but is NOT marked stale
    (last_processed preserved, not counted as 'updated') — matching the batch path."""
    monkeypatch.setattr(settings, "config_dir", tmp_path / "config")
    local_dir = tmp_path / "posters" / "MM2K" / "MetaFill"
    local_dir.mkdir(parents=True)
    monkeypatch.setattr(settings, "gdrive_dir", tmp_path / "posters")

    drive, job = _make_drive_and_job(test_db, drive_id="sync-meta-1", name="MetaFill")
    drive.sync_enabled = True
    test_db.commit()

    poster_path = local_dir / "p.jpg"
    poster_path.write_bytes(b"data")
    st = poster_path.stat()
    stamp = datetime(2020, 1, 1)
    p = Poster(drive_id=drive.drive_id, file_name="p.jpg", file_path=str(poster_path),
               file_size=st.st_size, file_mtime=None, last_processed=stamp)  # NULL mtime = legacy row
    test_db.add(p)
    test_db.commit()

    service = PosterSyncService(test_db)
    monkeypatch.setattr(service.rclone, "sync_folder",
        lambda drive_id, local_path, drive_name=None, progress_callback=None: {"success": True, "files_transferred": 0})

    result = service.sync_drive(drive.id, job.id)

    assert result["updated"] == 0  # metadata fill is not a real update
    test_db.refresh(p)
    assert p.file_mtime is not None       # mtime got populated
    assert p.last_processed == stamp       # NOT reset → renamer won't needlessly reprocess


def test_sync_single_manual_drive_skips_rclone(test_db, monkeypatch, tmp_path):
    """Single-syncing a manual/local custom drive (drive_id 'manual-…') must skip rclone and
    just scan local files — previously the skip only existed on the batch path."""
    monkeypatch.setattr(settings, "config_dir", tmp_path / "config")
    monkeypatch.setattr(settings, "gdrive_dir", tmp_path / "posters")

    folder = tmp_path / "manual"
    folder.mkdir()
    (folder / "local.jpg").write_bytes(b"x")

    drive = Drive(name="ManualDrive", drive_id="manual-xyz", style_type="Custom",
                  subscribed=True, sync_enabled=True, is_custom=True, custom_path=str(folder))
    job = Job(job_type="Sync: ManualDrive", status="pending", progress=0, message="q")
    test_db.add(drive)
    test_db.add(job)
    test_db.commit()
    test_db.refresh(drive)
    test_db.refresh(job)

    service = PosterSyncService(test_db)

    def _boom(*args, **kwargs):
        raise AssertionError("rclone.sync_folder must not be called for a manual/local drive")

    monkeypatch.setattr(service.rclone, "sync_folder", _boom)

    result = service.sync_drive(drive.id, job.id)

    assert result["success"] is True
    assert result["added"] == 1
    assert test_db.query(Poster).filter(Poster.drive_id == "manual-xyz").count() == 1
