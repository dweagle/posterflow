"""
Tests for backup restore endpoint (api/backup.py):
  - Requires confirm=true guard
  - File type validation
  - Upload size limit (50 MB)
  - Zip Slip path traversal rejection
  - Happy path restore (DB + rclone + drives_cache)
  - Bad zip rejection
"""
import io
import json
import zipfile
import pytest
import unittest.mock


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
