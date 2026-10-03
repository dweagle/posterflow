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
from core.config import settings as app_settings
from services.backup import (
    UnsupportedDatabaseBackup, build_backup_zip, prune_backups, run_backup_to_location,
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
    monkeypatch.setattr(app_settings, "database_url", f"sqlite:///{tmp_path / 'posterflow.db'}")
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
    monkeypatch.setattr(app_settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr("api.backup.RCLONE_CONF", tmp_path / "rclone.conf")
    monkeypatch.setattr("api.backup.DRIVES_CACHE", tmp_path / "drives_cache.json")

    zip_bytes = _make_zip({"posterflow.db": b"new-db"})
    resp = _post_restore(client, zip_bytes)
    assert resp.status_code == 200

    safety_dir = tmp_path / "safety_backups"
    safety_files = list(safety_dir.glob("posterflow.db.*"))
    assert len(safety_files) == 1, "Safety backup of original DB should have been created"
    assert safety_files[0].read_bytes() == b"original-db"


def test_restore_backup_uses_custom_sqlite_url(client, tmp_path, monkeypatch):
    config_dir = _make_config_dir(tmp_path, monkeypatch)
    stale_db = config_dir / "posterflow.db"
    stale_bytes = stale_db.read_bytes()
    db_path = tmp_path / "custom #%.sqlite"
    db_path.write_bytes(b"original-custom-db")
    monkeypatch.setattr(app_settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr("api.backup.CONFIG_DIR", config_dir)

    resp = _post_restore(client, _make_zip({"posterflow.db": b"replacement-custom-db"}))

    assert resp.status_code == 200
    assert db_path.read_bytes() == b"replacement-custom-db"
    assert stale_db.read_bytes() == stale_bytes
    safety_files = list((config_dir / "safety_backups").glob("posterflow.db.*"))
    assert len(safety_files) == 1
    assert safety_files[0].read_bytes() == b"original-custom-db"


# ---------------------------------------------------------------------------
# Shared builder (services/backup.py)
# ---------------------------------------------------------------------------

def _make_config_dir(tmp_path, monkeypatch):
    """Point the app config dir at tmp_path/config with a real sqlite DB inside."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    import services.backup as backup_service
    monkeypatch.setattr(backup_service.app_settings, "config_dir", config_dir)
    monkeypatch.setattr(backup_service.app_settings, "database_url", f"sqlite:///{config_dir / 'posterflow.db'}")

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


@pytest.mark.parametrize("relative", [False, True])
def test_build_backup_zip_snapshots_custom_sqlite_url(tmp_path, monkeypatch, relative):
    config_dir = _make_config_dir(tmp_path, monkeypatch)
    (config_dir / "rclone.conf").write_text("[gdrive]\ntype=drive\n")
    # Reserved URI characters and a non-default suffix must identify the actual file.
    db_path = tmp_path / "custom #%.sqlite"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE marker (value TEXT)")
        conn.execute("INSERT INTO marker VALUES ('custom')")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.chdir(tmp_path)
    database = db_path.name if relative else str(db_path)
    monkeypatch.setattr(app_settings, "database_url", f"sqlite:///{database}")

    backup_path = build_backup_zip(tmp_path / "backups")

    with zipfile.ZipFile(backup_path) as zf:
        snapshot = tmp_path / "snapshot.db"
        snapshot.write_bytes(zf.read("posterflow.db"))
        assert zf.read("rclone.conf") == b"[gdrive]\ntype=drive\n"
    conn = sqlite3.connect(str(snapshot))
    try:
        assert conn.execute("SELECT value FROM marker").fetchall() == [("custom",)]
    finally:
        conn.close()


def test_build_backup_zip_snapshots_committed_live_wal(tmp_path, monkeypatch):
    config_dir = _make_config_dir(tmp_path, monkeypatch)
    db_path = config_dir / "posterflow.db"
    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        conn.execute("INSERT INTO marker VALUES ('live-wal')")
        conn.commit()
        assert db_path.with_name("posterflow.db-wal").stat().st_size > 0

        backup_path = build_backup_zip(tmp_path / "backups")

        with zipfile.ZipFile(backup_path) as zf:
            snapshot = tmp_path / "snapshot.db"
            snapshot.write_bytes(zf.read("posterflow.db"))
        observer = sqlite3.connect(str(snapshot))
        try:
            assert observer.execute("SELECT value FROM marker").fetchall() == [
                ("hello",), ("live-wal",),
            ]
        finally:
            observer.close()
    finally:
        conn.close()


def test_build_backup_zip_snapshot_failure_publishes_no_archive(tmp_path, monkeypatch):
    _make_config_dir(tmp_path, monkeypatch)
    dest_dir = tmp_path / "backups"
    with unittest.mock.patch(
        "services.backup.sqlite3.connect", side_effect=sqlite3.OperationalError("snapshot failed")
    ):
        with pytest.raises(sqlite3.OperationalError, match="snapshot failed"):
            build_backup_zip(dest_dir)
    assert not list(dest_dir.glob("*.zip"))


def test_run_backup_snapshot_failure_preserves_existing_backups(tmp_path, monkeypatch, test_db):
    _make_config_dir(tmp_path, monkeypatch)
    dest_dir = tmp_path / "backups"
    dest_dir.mkdir()
    originals = {
        f"posterflow_backup_{stamp}.zip": stamp.encode()
        for stamp in ("20200101_000000", "20200102_000000")
    }
    for name, content in originals.items():
        (dest_dir / name).write_bytes(content)
    upsert_setting(test_db, "backup_location", str(dest_dir))
    upsert_setting(test_db, "backup_retention", "1")
    test_db.commit()

    with unittest.mock.patch(
        "services.backup.sqlite3.connect", side_effect=sqlite3.OperationalError("snapshot failed")
    ):
        with pytest.raises(sqlite3.OperationalError, match="snapshot failed"):
            run_backup_to_location(test_db)

    assert {p.name: p.read_bytes() for p in dest_dir.iterdir()} == originals


def test_build_backup_zip_missing_active_database_fails_without_archive(tmp_path, monkeypatch):
    _make_config_dir(tmp_path, monkeypatch)
    missing_db = tmp_path / "missing.sqlite"
    monkeypatch.setattr(app_settings, "database_url", f"sqlite:///{missing_db}")
    dest_dir = tmp_path / "backups"

    with pytest.raises(sqlite3.OperationalError):
        build_backup_zip(dest_dir)

    assert not missing_db.exists()
    assert not list(dest_dir.glob("*.zip"))


@pytest.mark.parametrize("url", [
    "postgresql+posterflow_missing_driver://localhost/posterflow",
    "sqlite:///:memory:",
    "sqlite://",
    "sqlite:///named.db?mode=memory",
    "sqlite:///file:named.db",
    "sqlite:///named.db?uri=true",
    "sqlite:///named.db?uri=True",
    "sqlite:///named.db?uri=1",
])
def test_build_backup_zip_rejects_unsupported_database_before_creation(tmp_path, monkeypatch, url):
    _make_config_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(app_settings, "database_url", url)
    dest_dir = tmp_path / "must_not_exist"
    with pytest.raises(UnsupportedDatabaseBackup, match="file-backed SQLite"):
        build_backup_zip(dest_dir)
    assert not dest_dir.exists()


def test_unsupported_database_guard_does_not_import_driver(tmp_path, monkeypatch):
    _make_config_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(
        app_settings, "database_url", "postgresql+posterflow_missing_driver://localhost/posterflow"
    )
    import builtins
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert not name.startswith(("psycopg", "posterflow_missing_driver"))
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with unittest.mock.patch("services.backup.sqlite3.connect") as connect:
        with pytest.raises(UnsupportedDatabaseBackup) as exc:
            build_backup_zip(tmp_path / "must_not_exist")
    connect.assert_not_called()
    assert str(exc.value) == "Full ZIP backup/restore requires a file-backed SQLite database."


@pytest.mark.parametrize("url", [
    "postgresql+posterflow_missing_driver://localhost/posterflow",
    "sqlite:///:memory:",
    "sqlite:///file:named.db?uri=true",
])
@pytest.mark.parametrize("endpoint", ["download", "save", "restore"])
def test_backup_endpoints_refuse_unsupported_database_without_writes(
    client, test_db, tmp_path, monkeypatch, url, endpoint,
):
    config_dir = _make_config_dir(tmp_path, monkeypatch)
    monkeypatch.setattr("api.backup.CONFIG_DIR", config_dir)
    for name in ("rclone.conf", "drives_cache.json", "artwork_drives_cache.json"):
        (config_dir / name).write_bytes(f"original-{name}".encode())
    monkeypatch.setattr("api.backup.RCLONE_CONF", config_dir / "rclone.conf")
    monkeypatch.setattr("api.backup.DRIVES_CACHE", config_dir / "drives_cache.json")
    monkeypatch.setattr("api.backup.ARTWORK_DRIVES_CACHE", config_dir / "artwork_drives_cache.json")
    originals = {p.name: p.read_bytes() for p in config_dir.iterdir()}
    dest_dir = tmp_path / "must_not_exist"
    upsert_setting(test_db, "backup_location", str(dest_dir))
    test_db.commit()
    monkeypatch.setattr(app_settings, "database_url", url)

    with unittest.mock.patch("api.backup.tempfile.TemporaryDirectory") as temp_dir:
        if endpoint == "download":
            resp = client.get("/api/backup/")
        elif endpoint == "save":
            resp = client.post("/api/backup/save")
        else:
            resp = _post_restore(client, _make_zip({
                "posterflow.db": b"replacement-db",
                "rclone.conf": b"replacement-conf",
                "drives_cache.json": b"replacement-cache",
                "artwork_drives_cache.json": b"replacement-artwork-cache",
            }))

    assert resp.status_code == 501
    assert resp.json()["detail"] == "Full ZIP backup/restore requires a file-backed SQLite database."
    temp_dir.assert_not_called()
    assert {p.name: p.read_bytes() for p in config_dir.iterdir()} == originals
    assert not dest_dir.exists()
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
