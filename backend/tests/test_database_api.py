from datetime import datetime, timezone

from models.poster import Poster
from models.drive import Drive
from models.artwork import Artwork
from models.artwork_drive import ArtworkDrive


# ---------------------------------------------------------------------------
# GET /api/database/stats
# ---------------------------------------------------------------------------


def test_database_stats_returns_zero_counts_when_empty(client):
    response = client.get("/api/database/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_records"] == 0
    assert data["health"]["orphaned_records"] == 0
    assert data["health"]["orphan_percentage"] == 0


def test_database_stats_counts_records(client, test_db, tmp_path):
    poster_file = tmp_path / "poster.jpg"
    poster_file.write_bytes(b"img")

    test_db.add(Poster(
        drive_id="drv-1",
        file_name="poster.jpg",
        file_path=str(poster_file),
    ))
    test_db.commit()

    response = client.get("/api/database/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_records"] == 1
    assert data["health"]["healthy_records"] == 1
    assert data["health"]["orphaned_records"] == 0


def test_database_stats_detects_orphaned_record(client, test_db):
    test_db.add(Poster(
        drive_id="drv-orphan",
        file_name="gone.jpg",
        file_path="/nonexistent/path/gone.jpg",
    ))
    test_db.commit()

    response = client.get("/api/database/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["health"]["orphaned_records"] == 1
    assert data["health"]["orphan_percentage"] == 100.0


# ---------------------------------------------------------------------------
# GET /api/database/cleanup/preview
# ---------------------------------------------------------------------------


def test_cleanup_preview_shows_no_orphans_when_files_exist(client, test_db, tmp_path):
    poster_file = tmp_path / "real.jpg"
    poster_file.write_bytes(b"img")
    test_db.add(Poster(
        drive_id="drv-real",
        file_name="real.jpg",
        file_path=str(poster_file),
    ))
    test_db.commit()

    response = client.get("/api/database/cleanup/preview")
    assert response.status_code == 200
    data = response.json()
    assert data["orphaned_count"] == 0
    assert data["would_delete"] == 0


def test_cleanup_preview_identifies_orphaned_records(client, test_db):
    test_db.add(Poster(
        drive_id="drv-x",
        file_name="missing.jpg",
        file_path="/no/such/file.jpg",
    ))
    test_db.commit()

    response = client.get("/api/database/cleanup/preview")
    assert response.status_code == 200
    data = response.json()
    assert data["orphaned_count"] == 1
    assert "sample_records" in data  # default is non-full mode
    # file_name is derived from os.path.basename(file_path)
    assert data["sample_records"][0]["file_name"] == "file.jpg"


def test_cleanup_preview_full_returns_all_records(client, test_db):
    for i in range(3):
        test_db.add(Poster(
            drive_id=f"drv-{i}",
            file_name=f"missing{i}.jpg",
            file_path=f"/no/such/file{i}.jpg",
        ))
    test_db.commit()

    response = client.get("/api/database/cleanup/preview?full=true")
    assert response.status_code == 200
    data = response.json()
    assert "orphaned_records" in data
    assert len(data["orphaned_records"]) == 3


# ---------------------------------------------------------------------------
# POST /api/database/cleanup/execute
# ---------------------------------------------------------------------------


def test_cleanup_execute_requires_confirm(client):
    response = client.post("/api/database/cleanup/execute")
    assert response.status_code == 400
    assert "confirm" in response.json()["detail"].lower()


def test_cleanup_execute_deletes_orphaned_records(client, test_db, tmp_path):
    # One record whose file exists (should be kept)
    real_file = tmp_path / "real.jpg"
    real_file.write_bytes(b"img")
    real_poster = Poster(
        drive_id="drv-real",
        file_name="real.jpg",
        file_path=str(real_file),
    )
    # One orphaned record (file does not exist)
    orphan_poster = Poster(
        drive_id="drv-orphan",
        file_name="gone.jpg",
        file_path="/nonexistent/gone.jpg",
    )
    test_db.add_all([real_poster, orphan_poster])
    test_db.commit()

    response = client.post("/api/database/cleanup/execute?confirm=true")
    assert response.status_code == 200
    data = response.json()
    assert data["deleted_count"] == 1
    assert data["success"] is True

    # Confirm orphan gone, real record remains
    remaining = test_db.query(Poster).all()
    assert len(remaining) == 1
    assert remaining[0].file_name == "real.jpg"


def test_cleanup_execute_with_no_orphans_returns_zero_deleted(client, test_db, tmp_path):
    real_file = tmp_path / "poster.jpg"
    real_file.write_bytes(b"img")
    test_db.add(Poster(
        drive_id="drv-ok",
        file_name="poster.jpg",
        file_path=str(real_file),
    ))
    test_db.commit()

    response = client.post("/api/database/cleanup/execute?confirm=true")
    assert response.status_code == 200
    assert response.json()["deleted_count"] == 0


# ---------------------------------------------------------------------------
# Artwork parity: cleanup covers orphaned Artwork rows too
# ---------------------------------------------------------------------------


def test_cleanup_preview_identifies_orphaned_artwork_from_unsubscribed_drive(client, test_db):
    test_db.add(ArtworkDrive(name="My Artwork Drive", drive_id="art-drv", subscribed=False))
    test_db.add(Artwork(
        artwork_drive_id="art-drv",
        artwork_type="logo",
        file_name="Show (2020) {tmdb-1} - logo.png",
        file_path="/nonexistent/artwork/Show (2020) {tmdb-1} - logo.png",
    ))
    test_db.commit()

    response = client.get("/api/database/cleanup/preview?full=true")
    assert response.status_code == 200
    data = response.json()
    assert data["orphaned_count"] == 1
    record = data["orphaned_records"][0]
    assert record["asset_type"] == "artwork"
    assert record["reason"] == "Drive unsubscribed"
    assert record["drive_name"] == "My Artwork Drive"


def test_cleanup_execute_deletes_orphaned_artwork_but_keeps_present_files(client, test_db, tmp_path):
    real_art = tmp_path / "keep - background.jpg"
    real_art.write_bytes(b"img")
    test_db.add(ArtworkDrive(name="Art", drive_id="art-drv", subscribed=True))
    test_db.add(Artwork(
        artwork_drive_id="art-drv", artwork_type="background",
        file_name="keep - background.jpg", file_path=str(real_art),
    ))
    test_db.add(Artwork(
        artwork_drive_id="art-drv", artwork_type="logo",
        file_name="gone - logo.png", file_path="/nonexistent/gone - logo.png",
    ))
    test_db.commit()

    response = client.post("/api/database/cleanup/execute?confirm=true")
    assert response.status_code == 200
    data = response.json()
    assert data["deleted_count"] == 1
    assert data["artwork_records_deleted"] == 1
    assert data["poster_records_deleted"] == 0

    remaining = test_db.query(Artwork).all()
    assert len(remaining) == 1
    assert remaining[0].file_name == "keep - background.jpg"


# ---------------------------------------------------------------------------
# Cleanup also refreshes the drive cards' own counters (on disk / last synced)
# ---------------------------------------------------------------------------


def test_cleanup_execute_resets_counters_of_a_drive_whose_folder_is_gone(client, test_db, tmp_path):
    # The report: unsubscribed drive, folder deleted by hand, cleanup run, card still shows the
    # old "on disk" count and sync date because only the index rows were dropped.
    folder = tmp_path / "MM2K" / "Gone_Drive"
    drive = Drive(name="Gone Drive", drive_id="gone-1", style_type="MM2K", subscribed=False,
                  custom_path=str(folder), sync_file_count=2, last_files_transferred=2,
                  last_synced=datetime.now(timezone.utc))
    test_db.add(drive)
    for i in range(2):
        test_db.add(Poster(drive_id="gone-1", file_name=f"Movie {i} (2020).jpg",
                           file_path=str(folder / f"Movie {i} (2020).jpg")))
    test_db.commit()

    response = client.post("/api/database/cleanup/execute?confirm=true")
    assert response.status_code == 200
    data = response.json()
    assert data["poster_records_deleted"] == 2
    assert data["drive_counters_refreshed"] == 1

    test_db.refresh(drive)
    assert drive.sync_file_count == 0
    assert drive.last_files_transferred == 0
    assert drive.last_synced is None

    card = {d["drive_id"]: d for d in client.get("/api/drives/").json()}["gone-1"]
    assert (card["poster_count"], card["sync_file_count"], card["last_synced"]) == (0, 0, None)


def test_cleanup_execute_recounts_a_drive_that_still_has_files(client, test_db, tmp_path):
    folder = tmp_path / "Half_Drive"
    folder.mkdir()
    (folder / "Kept (2020).jpg").write_bytes(b"img")
    synced_at = datetime.now(timezone.utc)
    drive = Drive(name="Half Drive", drive_id="half-1", style_type="CL2K", subscribed=True,
                  custom_path=str(folder), sync_file_count=2, last_synced=synced_at)
    test_db.add(drive)
    test_db.add(Poster(drive_id="half-1", file_name="Kept (2020).jpg", file_path=str(folder / "Kept (2020).jpg")))
    test_db.add(Poster(drive_id="half-1", file_name="Gone (2021).jpg", file_path=str(folder / "Gone (2021).jpg")))
    test_db.commit()

    response = client.post("/api/database/cleanup/execute?confirm=true")
    assert response.status_code == 200
    assert response.json()["poster_records_deleted"] == 1

    test_db.refresh(drive)
    assert drive.sync_file_count == 1       # recounted from disk
    assert drive.last_synced is not None    # the drive still has synced content


def test_cleanup_execute_resets_counters_of_an_artwork_drive_whose_folder_is_gone(client, test_db, tmp_path):
    folder = tmp_path / "artwork" / "Gone_Art"
    drive = ArtworkDrive(name="Gone Art", drive_id="art-gone", subscribed=False, custom_path=str(folder),
                         sync_file_count=1, last_files_transferred=1, last_synced=datetime.now(timezone.utc))
    test_db.add(drive)
    test_db.add(Artwork(artwork_drive_id="art-gone", artwork_type="logo",
                        file_name="Show (2020) - logo.png", file_path=str(folder / "logos" / "Show (2020) - logo.png")))
    test_db.commit()

    response = client.post("/api/database/cleanup/execute?confirm=true")
    assert response.status_code == 200
    assert response.json()["artwork_records_deleted"] == 1

    test_db.refresh(drive)
    assert (drive.sync_file_count, drive.last_files_transferred, drive.last_synced) == (0, 0, None)
