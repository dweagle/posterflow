"""
Tests for backup restore endpoint (api/backup.py):
  - Requires confirm=true guard
  - File type validation
  - Zip Slip path traversal rejection
  - Restore through SQLite while the app holds the database open
  - Bad zip / bad database rejection
And the shared backup builder (services/backup.py):
  - Zip contents (DB snapshot + config files + metadata)
  - Snapshot of a live database, fail-closed on snapshot errors
  - Retention pruning
  - Scheduled run honoring backup_location / backup_retention settings
"""
import io
import json
import sqlite3
import zipfile
import pytest
import unittest.mock

import services.backup as backup_service
from models.setting import upsert_setting
from services.backup import (
    UnsupportedDatabaseBackup, build_backup_zip, prune_backups, run_backup_to_location, sqlite_database_path,
)


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


def _write_db(path, *values):
    """A real SQLite file with a marker table holding `values`."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS marker (value TEXT)")
    conn.executemany("INSERT INTO marker VALUES (?)", [(v,) for v in values])
    conn.commit()
    conn.close()
    return path


def _markers(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        return [row[0] for row in conn.execute("SELECT value FROM marker ORDER BY rowid")]
    finally:
        conn.close()


def _make_config_dir(tmp_path, monkeypatch):
    """Point the app config dir and database at tmp_path/config, with a real sqlite DB inside."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr(backup_service.app_settings, "config_dir", config_dir)
    monkeypatch.setattr(backup_service.app_settings, "database_url", f"sqlite:///{config_dir / 'posterflow.db'}")
    monkeypatch.setattr("api.backup.CONFIG_DIR", config_dir)
    monkeypatch.setattr("api.backup.RCLONE_CONF", config_dir / "rclone.conf")
    monkeypatch.setattr("api.backup.DRIVES_CACHE", config_dir / "drives_cache.json")
    _write_db(config_dir / "posterflow.db", "hello")
    return config_dir


def _live_connection(path):
    """The app's long-lived connection: WAL mode with changes left in the journal, as in production."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    return conn


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
    with unittest.mock.patch("api.backup.shutil.copy"), unittest.mock.patch("api.backup.restore_database"):
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


def test_restore_rejects_a_database_member_that_is_not_sqlite(client, tmp_path, monkeypatch):
    config_dir = _make_config_dir(tmp_path, monkeypatch)

    resp = _post_restore(client, _make_zip({"posterflow.db": b"fake-sqlite-db"}))

    assert resp.status_code == 400
    assert "sqlite" in resp.json()["detail"].lower()
    assert _markers(config_dir / "posterflow.db") == ["hello"]
    assert not (config_dir / "safety_backups").exists() or not list((config_dir / "safety_backups").iterdir())


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def test_restore_returns_success_with_valid_backup(client, tmp_path, monkeypatch):
    """A well-formed backup zip with posterflow.db should restore cleanly."""
    config_dir = _make_config_dir(tmp_path, monkeypatch)
    zip_bytes = _make_zip({
        "posterflow.db": _write_db(tmp_path / "from-backup.db", "restored").read_bytes(),
        "rclone.conf": b"[gdrive]\ntype=drive\n",
        "metadata.json": json.dumps({"version": "1.0", "created_at": "2026-01-01T00:00:00"}),
    })

    resp = _post_restore(client, zip_bytes)
    assert resp.status_code == 200
    data = resp.json()
    assert "restored" in data["message"].lower()
    assert data["restored_files"]["database"] is True
    assert data["restored_files"]["rclone_config"] is True

    assert _markers(config_dir / "posterflow.db") == ["restored"]
    assert (config_dir / "rclone.conf").read_bytes() == b"[gdrive]\ntype=drive\n"


def test_restore_creates_safety_backup_of_existing_db(client, tmp_path, monkeypatch):
    """If a DB already exists it should be preserved in safety_backups/."""
    config_dir = _make_config_dir(tmp_path, monkeypatch)
    zip_bytes = _make_zip({"posterflow.db": _write_db(tmp_path / "from-backup.db", "new").read_bytes()})

    resp = _post_restore(client, zip_bytes)
    assert resp.status_code == 200

    safety_files = list((config_dir / "safety_backups").glob("posterflow.db.*"))
    assert len(safety_files) == 1, "Safety backup of original DB should have been created"
    assert _markers(safety_files[0]) == ["hello"]
    assert _markers(config_dir / "posterflow.db") == ["new"]


def test_restore_replaces_a_database_the_app_holds_open(client, tmp_path, monkeypatch):
    """Restoring over a live WAL database must give the backup's content, not the old journal's."""
    config_dir = _make_config_dir(tmp_path, monkeypatch)
    live_db = config_dir / "posterflow.db"
    app = _live_connection(live_db)
    app.execute("INSERT INTO marker VALUES ('at backup')")
    app.commit()
    backup_zip = build_backup_zip(tmp_path / "backups")
    app.executemany("INSERT INTO marker VALUES (?)", [("after backup",)] * 50)
    app.commit()
    assert (live_db.parent / "posterflow.db-wal").stat().st_size > 0

    resp = _post_restore(client, backup_zip.read_bytes())
    assert resp.status_code == 200

    # visible to the app right away, and still there once it reopens the database
    assert [r[0] for r in app.execute("SELECT value FROM marker ORDER BY rowid")] == ["hello", "at backup"]
    app.close()
    assert _markers(live_db) == ["hello", "at backup"]
    safety_files = list((config_dir / "safety_backups").glob("posterflow.db.*"))
    assert _markers(safety_files[0]) == ["hello", "at backup"] + ["after backup"] * 50


# ---------------------------------------------------------------------------
# Shared builder (services/backup.py)
# ---------------------------------------------------------------------------

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
        assert _markers(extracted_db) == ["hello"]


def test_build_backup_zip_skips_missing_files(tmp_path, monkeypatch):
    _make_config_dir(tmp_path, monkeypatch)

    backup_path = build_backup_zip(tmp_path / "backups")

    with zipfile.ZipFile(backup_path) as zf:
        assert set(zf.namelist()) == {"posterflow.db", "metadata.json"}


def test_build_backup_zip_includes_changes_still_in_the_journal(tmp_path, monkeypatch):
    config_dir = _make_config_dir(tmp_path, monkeypatch)
    app = _live_connection(config_dir / "posterflow.db")
    app.execute("INSERT INTO marker VALUES ('journal only')")
    app.commit()

    backup_path = build_backup_zip(tmp_path / "backups")

    with zipfile.ZipFile(backup_path) as zf:
        extracted_db = tmp_path / "extracted.db"
        extracted_db.write_bytes(zf.read("posterflow.db"))
    assert _markers(extracted_db) == ["hello", "journal only"]
    app.close()


def test_build_backup_zip_uses_the_configured_database_path(tmp_path, monkeypatch):
    _make_config_dir(tmp_path, monkeypatch)
    (tmp_path / "elsewhere").mkdir()
    elsewhere = _write_db(tmp_path / "elsewhere" / "data.sqlite", "moved")
    monkeypatch.setattr(backup_service.app_settings, "database_url", f"sqlite:///{elsewhere}")

    assert sqlite_database_path() == elsewhere
    backup_path = build_backup_zip(tmp_path / "backups")

    with zipfile.ZipFile(backup_path) as zf:
        extracted_db = tmp_path / "extracted.db"
        extracted_db.write_bytes(zf.read("posterflow.db"))
    assert _markers(extracted_db) == ["moved"]


def test_backup_failure_leaves_no_zip_and_prunes_nothing(tmp_path, monkeypatch, test_db):
    config_dir = _make_config_dir(tmp_path, monkeypatch)
    (config_dir / "posterflow.db").unlink()
    dest_dir = tmp_path / "backups"
    dest_dir.mkdir()
    upsert_setting(test_db, "backup_location", str(dest_dir))
    upsert_setting(test_db, "backup_retention", "1")
    test_db.commit()
    (dest_dir / "posterflow_backup_20200101_000000.zip").write_bytes(b"old")

    with pytest.raises(sqlite3.OperationalError):
        run_backup_to_location(test_db)

    assert [p.name for p in dest_dir.iterdir()] == ["posterflow_backup_20200101_000000.zip"]


@pytest.mark.parametrize("url", [
    "postgresql://user:pw@db/posterflow",
    "sqlite://",
    "sqlite:///:memory:",
    "sqlite:///named.db?mode=memory",
    "sqlite:///file:named.db",
    "sqlite:///named.db?uri=true",
])
def test_backup_refuses_a_database_that_is_not_a_sqlite_file(client, test_db, tmp_path, monkeypatch, url):
    config_dir = _make_config_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(backup_service.app_settings, "database_url", url)

    with pytest.raises(UnsupportedDatabaseBackup):
        build_backup_zip(tmp_path / "backups")
    assert not (tmp_path / "backups").exists()
    assert client.get("/api/backup/").status_code == 501
    assert client.post("/api/backup/save").status_code == 501
    assert _post_restore(client, _make_zip({"posterflow.db": b"x"})).status_code == 501
    assert _markers(config_dir / "posterflow.db") == ["hello"]
    assert not (config_dir / "safety_backups").exists()


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
