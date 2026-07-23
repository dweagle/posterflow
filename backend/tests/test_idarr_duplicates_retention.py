"""Tests for the IDarr duplicates-folder retention cleanup."""
import os
import time

from core.config import settings as app_settings
from modules.idarr import _prune_idarr_duplicates


def _dup_dir():
    d = app_settings.config_dir / "idarr" / "duplicates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_prune_respects_retention_and_disabled():
    dup = _dup_dir()
    old = dup / "old.jpg"
    new = dup / "new.jpg"
    old.write_bytes(b"x")
    new.write_bytes(b"y")
    backdated = time.time() - 40 * 86400
    os.utime(old, (backdated, backdated))
    try:
        # disabled: 0 keeps everything
        _prune_idarr_duplicates({"duplicates_retention_days": 0})
        assert old.exists() and new.exists()
        # 30-day retention: the 40-day-old file goes, the fresh one stays
        _prune_idarr_duplicates({"duplicates_retention_days": 30})
        assert not old.exists()
        assert new.exists()
    finally:
        for f in dup.iterdir():
            f.unlink()


def test_config_round_trips_duplicates_retention(client, test_db):
    payload = {
        "sync_targets": [{"personal_drive_id": "d", "source_dir": "/tmp/x"}],
        "duplicates_retention_days": 14,
    }
    assert client.post("/api/idarr/", json=payload).status_code == 200
    assert client.get("/api/idarr/").json()["duplicates_retention_days"] == 14
