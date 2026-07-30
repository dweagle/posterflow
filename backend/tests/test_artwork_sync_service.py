"""Artwork drive sync on the shared engine: files in logos/backgrounds/squareart subfolders
index into the `artwork` table with the right type + parsed title/ids, self-heal like posters,
and emit the shared progress format ("Syncing files: X/Y transferred | <file>").
"""

from models.artwork_drive import ArtworkDrive
from models.artwork import Artwork
from models.job import Job
from services.artwork_sync import ArtworkSyncService


def _drive_and_job(test_db, folder, drive_id="maker-a", name="MakerA"):
    drive = ArtworkDrive(name=name, drive_id=drive_id, subscribed=True, sync_enabled=True, custom_path=str(folder))
    job = Job(job_type=f"Artwork Sync: {name}", status="pending", progress=0, message="queued")
    test_db.add(drive)
    test_db.add(job)
    test_db.commit()
    test_db.refresh(drive)
    test_db.refresh(job)
    return drive, job


def test_artwork_sync_indexes_files_by_subtype(test_db, monkeypatch, tmp_path):
    folder = tmp_path / "artwork" / "MakerA"
    (folder / "logos").mkdir(parents=True)
    (folder / "backgrounds").mkdir(parents=True)
    (folder / "logos" / "Inception (2010) {tmdb-27205} - logo.png").write_bytes(b"i")
    (folder / "backgrounds" / "Inception (2010) {tmdb-27205} - background.jpg").write_bytes(b"i")

    drive, job = _drive_and_job(test_db, folder)
    service = ArtworkSyncService(test_db)
    monkeypatch.setattr(service.rclone, "sync_folder",
        lambda drive_id, local_path, drive_name=None, progress_callback=None, exclude_dirs=None: {"success": True, "files_transferred": 2})

    result = service.sync_drive(drive.id, job.id)

    assert result["success"] is True
    assert result["added"] == 2
    rows = test_db.query(Artwork).filter(Artwork.artwork_drive_id == "maker-a").all()
    assert {r.artwork_type for r in rows} == {"logo", "background"}
    logo = next(r for r in rows if r.artwork_type == "logo")
    assert logo.tmdb_id == 27205
    assert logo.title == "Inception"
    assert logo.year == 2010


def test_artwork_sync_self_heals_orphaned_rows(test_db, monkeypatch, tmp_path):
    """A file present at pre-sync but removed during the rclone sync is cleaned from the artwork
    table by the shared self-heal — same behavior as posters."""
    folder = tmp_path / "artwork" / "MakerB"
    (folder / "logos").mkdir(parents=True)
    keep = folder / "logos" / "Keep - logo.png"
    keep.write_bytes(b"k")
    gone = folder / "logos" / "Gone - logo.png"
    gone.write_bytes(b"g")

    drive, job = _drive_and_job(test_db, folder, drive_id="maker-b", name="MakerB")
    kst = keep.stat()
    gst = gone.stat()
    test_db.add(Artwork(artwork_drive_id="maker-b", file_path=str(keep), file_name=keep.name,
                        artwork_type="logo", file_size=kst.st_size, file_mtime=kst.st_mtime))
    test_db.add(Artwork(artwork_drive_id="maker-b", file_path=str(gone), file_name=gone.name,
                        artwork_type="logo", file_size=gst.st_size, file_mtime=gst.st_mtime))
    test_db.commit()

    service = ArtworkSyncService(test_db)

    def _fake_sync(drive_id, local_path, drive_name=None, progress_callback=None, exclude_dirs=None):
        gone.unlink()  # rclone removed it during the sync
        return {"success": True, "files_transferred": 0}

    monkeypatch.setattr(service.rclone, "sync_folder", _fake_sync)

    result = service.sync_drive(drive.id, job.id)

    assert result["success"] is True
    assert result["deleted"] == 1
    remaining = {r.file_name for r in test_db.query(Artwork).filter(Artwork.artwork_drive_id == "maker-b").all()}
    assert remaining == {"Keep - logo.png"}


# --- Progress messages (drive the real shared sync_drive through a fake rclone) ---

def _run_with_progress(test_db, monkeypatch, tmp_path, events):
    folder = tmp_path / "artwork" / "Dweagle79"
    folder.mkdir(parents=True)
    drive, job = _drive_and_job(test_db, folder, drive_id="drive-123", name="Dweagle79")

    service = ArtworkSyncService(test_db)

    def _fake_sync(drive_id, local_path, drive_name=None, progress_callback=None, exclude_dirs=None):
        for filename, checked, transferred, phase, hint in events:
            progress_callback(filename, checked, transferred, phase, hint)
        return {"success": True, "files_transferred": 0}

    monkeypatch.setattr(service.rclone, "sync_folder", _fake_sync)

    captured = []
    service.sync_drive(drive.id, job.id, progress_callback=lambda phase, cur, total, msg: captured.append(msg))
    return captured


def test_transferring_message_includes_counts_and_filename(test_db, monkeypatch, tmp_path):
    # A checking event first (the transfer phase only reports once files have been listed).
    msgs = _run_with_progress(test_db, monkeypatch, tmp_path, [
        ("Checking files: 5,000/5,000", 5000, 0, "checking", 0),
        ("A-X-L (2018).jpg", 5000, 1234, "transferring", 5000),
    ])

    transfer = [m for m in msgs if "transferred" in m]
    assert transfer, f"no transfer message emitted: {msgs}"
    assert "1,234/5,000 transferred" in transfer[-1], f"counts missing from: {transfer[-1]!r}"
    assert "A-X-L (2018).jpg" in transfer[-1], f"filename missing from: {transfer[-1]!r}"


def test_checking_message_passes_the_rclone_count_through(test_db, monkeypatch, tmp_path):
    msgs = _run_with_progress(test_db, monkeypatch, tmp_path, [("Checking files: 4,321/9,000", 4321, 0, "checking", 0)])

    assert any("4,321" in m for m in msgs), f"checking count not surfaced: {msgs}"


# --- Per-drive artwork type selection ---

def test_drive_synced_types_default_to_all_and_exclude_nothing(test_db, tmp_path):
    drive, _ = _drive_and_job(test_db, tmp_path / "artwork" / "MakerA")

    assert drive.synced_type_list == ["logo", "background", "squareart"]
    assert drive.excluded_subfolders == []
    assert ArtworkSyncService(test_db)._remote_exclude_dirs(drive) == []


def test_logos_only_drive_excludes_the_other_subfolders_from_rclone(test_db, tmp_path):
    drive, _ = _drive_and_job(test_db, tmp_path / "artwork" / "MakerA")
    drive.synced_types = "logo"
    test_db.commit()

    assert drive.synced_type_list == ["logo"]
    assert ArtworkSyncService(test_db)._remote_exclude_dirs(drive) == ["backgrounds", "squareart"]


def test_sync_indexes_only_the_drives_selected_types(test_db, monkeypatch, tmp_path):
    """Files left over in a deselected type's folder must stay out of the index — rclone can't
    delete what its filters hide, so the scan is what keeps them from coming back."""
    folder = tmp_path / "artwork" / "MakerA"
    (folder / "logos").mkdir(parents=True)
    (folder / "backgrounds").mkdir(parents=True)
    (folder / "logos" / "Inception (2010) {tmdb-27205} - logo.png").write_bytes(b"i")
    (folder / "backgrounds" / "Inception (2010) {tmdb-27205} - background.jpg").write_bytes(b"i")

    drive, job = _drive_and_job(test_db, folder)
    drive.synced_types = "logo"
    test_db.commit()

    service = ArtworkSyncService(test_db)
    captured = {}

    def _fake_sync(drive_id, local_path, drive_name=None, progress_callback=None, exclude_dirs=None):
        captured["exclude_dirs"] = exclude_dirs
        return {"success": True, "files_transferred": 1}

    monkeypatch.setattr(service.rclone, "sync_folder", _fake_sync)
    result = service.sync_drive(drive.id, job.id)

    assert result["success"] is True
    assert captured["exclude_dirs"] == ["backgrounds", "squareart"]
    rows = test_db.query(Artwork).filter(Artwork.artwork_drive_id == "maker-a").all()
    assert [r.artwork_type for r in rows] == ["logo"]
