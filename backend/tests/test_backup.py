"""
Tests for backup restore endpoint (api/backup.py):
  - Requires confirm=true guard
  - File type validation
  - Upload size limit (50 MB)
  - Zip Slip path traversal rejection
  - Happy path restore (DB + rclone + drives_cache)
  - Bad zip rejection
And the shared backup builder (services/backup.py):
  - Zip contents (DB snapshot + config files + metadata)
  - Retention pruning
  - Scheduled run honoring backup_location / backup_retention settings
"""
import io
import json
import sqlite3
import zipfile
import pytest
import unittest.mock

from models.setting import upsert_setting
from services.backup import build_backup_zip, prune_backups, run_backup_to_location


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_zip(files: dict) -> bytes:
    """Build an in-memory zip with the given {member_name: bytes_content} mapping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _post_restore(client, zip_bytes: bytes, filename: str = "backup.zip", confirm: bool = True):
    return client.post(
        "/api/backup/?confirm=true" if confirm else "/api/backup/",
        files={"file": (filename, io.BytesIO(zip_bytes), "application/zip")},
    )


# ---------------------------------------------------------------------------
# Guard tests (no file IO needed)
# ---------------------------------------------------------------------------

def test_restore_requires_confirm_true(client):
    zip_bytes = _make_zip({"metadata.json": json.dumps({"version": "1.0"})})
    resp = client.post(
        "/api/backup/",
        files={"file": ("backup.zip", io.BytesIO(zip_bytes), "application/zip")},
    )
    assert resp.status_code == 400
    assert "confirm" in resp.json()["detail"].lower()


def test_restore_rejects_non_zip_extension(client):
    resp = client.post(
        "/api/backup/?confirm=true",
        files={"file": ("backup.tar", io.BytesIO(b"data"), "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert ".zip" in resp.json()["detail"].lower()


def test_restore_rejects_zip_with_traversal_entries(client, monkeypatch):
    """A zip containing ../escape path members must be rejected (Zip Slip)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", "pwned")
        zf.writestr("posterflow.db", b"fake-db")
    buf.seek(0)

    # Patch CONFIG_DIR writes so nothing touches the real filesystem
    with unittest.mock.patch("api.backup.shutil.copy"):
        resp = client.post(
            "/api/backup/?confirm=true",
            files={"file": ("backup.zip", buf, "application/zip")},
        )
    assert resp.status_code == 400
    assert "traversal" in resp.json()["detail"].lower()


def test_restore_rejects_bad_zip_file(client):
    resp = client.post(
        "/api/backup/?confirm=true",
        files={"file": ("backup.zip", io.BytesIO(b"not a zip file!"), "application/zip")},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_restore_returns_success_with_valid_backup(client, tmp_path, monkeypatch):
    """A well-formed backup zip with posterflow.db should restore cleanly."""
    # Patch CONFIG_DIR so the backup writes to tmp_path rather than the real config dir
    monkeypatch.setattr("api.backup.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("api.backup.DB_FILE", tmp_path / "posterflow.db")
    monkeypatch.setattr("api.backup.RCLONE_CONF", tmp_path / "rclone.conf")
    monkeypatch.setattr("api.backup.DRIVES_CACHE", tmp_path / "drives_cache.json")

    zip_bytes = _make_zip({
        "posterflow.db": b"fake-sqlite-db",
        "rclone.conf": b"[gdrive]\ntype=drive\n",
        "metadata.json": json.dumps({"version": "1.0", "created_at": "2026-01-01T00:00:00"}),
    })

    resp = _post_restore(client, zip_bytes)
    assert resp.status_code == 200
    data = resp.json()
    assert "restored" in data["message"].lower()
    assert data["restored_files"]["database"] is True
    assert data["restored_files"]["rclone_config"] is True

    # Verify files were actually written
    assert (tmp_path / "posterflow.db").read_bytes() == b"fake-sqlite-db"
    assert (tmp_path / "rclone.conf").read_bytes() == b"[gdrive]\ntype=drive\n"


def test_restore_creates_safety_backup_of_existing_db(client, tmp_path, monkeypatch):
    """If a DB already exists it should be preserved in safety_backups/."""
    monkeypatch.setattr("api.backup.CONFIG_DIR", tmp_path)
    db_path = tmp_path / "posterflow.db"
    db_path.write_bytes(b"original-db")
    monkeypatch.setattr("api.backup.DB_FILE", db_path)
    monkeypatch.setattr("api.backup.RCLONE_CONF", tmp_path / "rclone.conf")
    monkeypatch.setattr("api.backup.DRIVES_CACHE", tmp_path / "drives_cache.json")

    zip_bytes = _make_zip({"posterflow.db": b"new-db"})
    resp = _post_restore(client, zip_bytes)
    assert resp.status_code == 200

    safety_dir = tmp_path / "safety_backups"
    safety_files = list(safety_dir.glob("posterflow.db.*"))
    assert len(safety_files) == 1, "Safety backup of original DB should have been created"
    assert safety_files[0].read_bytes() == b"original-db"


# ---------------------------------------------------------------------------
# Shared builder (services/backup.py)
# ---------------------------------------------------------------------------

def _make_config_dir(tmp_path, monkeypatch):
    """Point the app config dir at tmp_path/config with a real sqlite DB inside."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    import services.backup as backup_service
    monkeypatch.setattr(backup_service.app_settings, "config_dir", config_dir)

    conn = sqlite3.connect(str(config_dir / "posterflow.db"))
    conn.execute("CREATE TABLE marker (value TEXT)")
    conn.execute("INSERT INTO marker VALUES ('hello')")
    conn.commit()
    conn.close()
    return config_dir


def test_build_backup_zip_includes_db_snapshot_and_config_files(tmp_path, monkeypatch):
    config_dir = _make_config_dir(tmp_path, monkeypatch)
    (config_dir / "rclone.conf").write_text("[gdrive]\ntype=drive\n")
    (config_dir / "drives_cache.json").write_text("{}")
    (config_dir / "artwork_drives_cache.json").write_text("{}")

    dest_dir = tmp_path / "backups"
    backup_path = build_backup_zip(dest_dir)

    assert backup_path.parent == dest_dir
    with zipfile.ZipFile(backup_path) as zf:
        names = set(zf.namelist())
        assert names == {
            "posterflow.db",
            "rclone.conf",
            "drives_cache.json",
            "artwork_drives_cache.json",
            "metadata.json",
        }
        # DB member must be a valid sqlite snapshot with the original data
        extracted_db = tmp_path / "extracted.db"
        extracted_db.write_bytes(zf.read("posterflow.db"))
        conn = sqlite3.connect(str(extracted_db))
        assert conn.execute("SELECT value FROM marker").fetchone() == ("hello",)
        conn.close()


def test_build_backup_zip_skips_missing_files(tmp_path, monkeypatch):
    _make_config_dir(tmp_path, monkeypatch)

    backup_path = build_backup_zip(tmp_path / "backups")

    with zipfile.ZipFile(backup_path) as zf:
        assert set(zf.namelist()) == {"posterflow.db", "metadata.json"}


def test_prune_backups_removes_oldest_beyond_keep(tmp_path):
    for stamp in ("20260101_000000", "20260102_000000", "20260103_000000", "20260104_000000"):
        (tmp_path / f"posterflow_backup_{stamp}.zip").write_bytes(b"zip")
    (tmp_path / "unrelated.zip").write_bytes(b"zip")

    removed = prune_backups(tmp_path, keep=2)

    assert removed == 2
    remaining = sorted(p.name for p in tmp_path.glob("posterflow_backup_*.zip"))
    assert remaining == [
        "posterflow_backup_20260103_000000.zip",
        "posterflow_backup_20260104_000000.zip",
    ]
    assert (tmp_path / "unrelated.zip").exists()


def test_prune_backups_zero_keeps_everything(tmp_path):
    for stamp in ("20260101_000000", "20260102_000000"):
        (tmp_path / f"posterflow_backup_{stamp}.zip").write_bytes(b"zip")

    assert prune_backups(tmp_path, keep=0) == 0
    assert len(list(tmp_path.glob("posterflow_backup_*.zip"))) == 2


def test_run_backup_to_location_uses_location_and_retention_settings(tmp_path, monkeypatch, test_db):
    _make_config_dir(tmp_path, monkeypatch)
    dest_dir = tmp_path / "nas-backups"
    upsert_setting(test_db, "backup_location", str(dest_dir))
    upsert_setting(test_db, "backup_retention", "1")
    test_db.commit()

    # Pre-existing older backup should be pruned once the new one lands
    dest_dir.mkdir()
    (dest_dir / "posterflow_backup_20200101_000000.zip").write_bytes(b"old")

    backup_path = run_backup_to_location(test_db)

    backups = list(dest_dir.glob("posterflow_backup_*.zip"))
    assert backups == [backup_path]
    assert backup_path.name != "posterflow_backup_20200101_000000.zip"


def test_run_backup_to_location_defaults_to_config_backups_dir(tmp_path, monkeypatch, test_db):
    config_dir = _make_config_dir(tmp_path, monkeypatch)

    run_backup_to_location(test_db)

    assert len(list((config_dir / "backups").glob("posterflow_backup_*.zip"))) == 1


def test_save_endpoint_writes_backup_to_configured_location(client, test_db, tmp_path, monkeypatch):
    """POST /api/backup/save creates a zip in the configured backup location."""
    _make_config_dir(tmp_path, monkeypatch)
    dest_dir = tmp_path / "manual-backups"
    upsert_setting(test_db, "backup_location", str(dest_dir))
    test_db.commit()

    resp = client.post("/api/backup/save")
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"].startswith(str(dest_dir))
    assert "saved" in data["message"].lower()
    assert len(list(dest_dir.glob("posterflow_backup_*.zip"))) == 1
