"""Concurrent subscribes must not drop drives from the priority list (it's one JSON blob,
so every mutation is read-modify-write). Sessions here MUST stay autoflush=False like the
app's SessionLocal — with autoflush on the race disappears and these tests prove nothing."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from database import Base
from models.drive import Drive
from models.artwork_drive import ArtworkDrive
from models.setting import Setting, upsert_setting
import api.drives as drives_api
import api.artwork_drives as artwork_api


@pytest.fixture
def threaded_sessions(tmp_path):
    """Independent per-thread sessions configured like the app: autoflush=False, WAL, busy_timeout."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrency.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    Base.metadata.create_all(engine)
    yield sessionmaker(autocommit=False, autoflush=False, bind=engine)
    engine.dispose()


def _widen_race_window(monkeypatch, module):
    """Pause after each settings read so unlocked overlap loses updates every run, not just on lucky scheduling."""
    real_get_setting = module.get_setting

    def slow_get_setting(db, key):
        row = real_get_setting(db, key)
        time.sleep(0.002)
        return row

    monkeypatch.setattr(module, "get_setting", slow_get_setting)


def _run_together(fn, items):
    """Fire fn(item) on every item at once, so the read-modify-writes genuinely overlap."""
    barrier = threading.Barrier(len(items))

    def worker(item):
        barrier.wait()
        return fn(item)

    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        return [f.result() for f in [pool.submit(worker, i) for i in items]]


def _priority_ids(Session, key):
    db = Session()
    try:
        row = db.query(Setting).filter(Setting.key == key).first()
        return json.loads(row.value)["drive_ids"] if row and row.value else []
    finally:
        db.close()


def test_concurrent_subscribes_keep_every_drive_in_poster_priority(threaded_sessions, monkeypatch):
    _widen_race_window(monkeypatch, drives_api)
    Session = threaded_sessions
    seed = Session()
    drives = [
        Drive(name=f"Race {i}", drive_id=f"race-{i}", style_type="MM2K", subscribed=False, sync_enabled=True)
        for i in range(8)
    ]
    seed.add_all(drives)
    upsert_setting(seed, drives_api.SETTING_POSTER_DRIVE_PRIORITY, json.dumps({"drive_ids": []}))
    seed.commit()
    drive_ids = [d.id for d in drives]
    seed.close()

    def subscribe(drive_id):
        db = Session()
        try:
            return drives_api.subscribe_drive(drive_id, add_to_priority=True, db=db)
        finally:
            db.close()

    results = _run_together(subscribe, drive_ids)

    assert all(r["added_to_priority"] for r in results)
    assert sorted(_priority_ids(Session, drives_api.SETTING_POSTER_DRIVE_PRIORITY)) == sorted(drive_ids)


def test_concurrent_unsubscribes_prune_every_drive_from_poster_priority(threaded_sessions, monkeypatch):
    _widen_race_window(monkeypatch, drives_api)
    Session = threaded_sessions
    seed = Session()
    drives = [
        Drive(name=f"Drop {i}", drive_id=f"drop-{i}", style_type="MM2K", subscribed=True, sync_enabled=True)
        for i in range(8)
    ]
    seed.add_all(drives)
    seed.commit()
    drive_ids = [d.id for d in drives]
    upsert_setting(seed, drives_api.SETTING_POSTER_DRIVE_PRIORITY, json.dumps({"drive_ids": drive_ids}))
    seed.commit()
    seed.close()

    def unsubscribe(drive_id):
        db = Session()
        try:
            return drives_api.unsubscribe_drive(drive_id, db=db)
        finally:
            db.close()

    results = _run_together(unsubscribe, drive_ids)

    assert all(r["removed_from_priority"] for r in results)
    assert _priority_ids(Session, drives_api.SETTING_POSTER_DRIVE_PRIORITY) == []


def test_concurrent_subscribes_keep_every_artwork_drive_in_priority(threaded_sessions, monkeypatch):
    _widen_race_window(monkeypatch, artwork_api)
    Session = threaded_sessions
    seed = Session()
    drives = [
        ArtworkDrive(name=f"Art race {i}", drive_id=f"art-race-{i}", subscribed=False, sync_enabled=True)
        for i in range(8)
    ]
    seed.add_all(drives)
    upsert_setting(seed, artwork_api.SETTING_ARTWORK_DRIVE_PRIORITY, json.dumps({"drive_ids": []}))
    seed.commit()
    drive_ids = [d.id for d in drives]
    seed.close()

    def subscribe(drive_id):
        db = Session()
        try:
            return artwork_api.subscribe_artwork_drive(drive_id, add_to_priority=True, db=db)
        finally:
            db.close()

    results = _run_together(subscribe, drive_ids)

    assert all(r["added_to_priority"] for r in results)
    assert sorted(_priority_ids(Session, artwork_api.SETTING_ARTWORK_DRIVE_PRIORITY)) == sorted(drive_ids)
