import os
from pathlib import Path

from models.drive import Drive
from models.poster import Poster


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
