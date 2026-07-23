from datetime import datetime, timezone
from pathlib import Path

from core.config import settings
from models.drive import Drive
from models.poster import Poster


def test_get_logs_parses_entries_and_filters_by_level(client, tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "posterflow.log"
    log_file.write_text(
        "26:02:15 10:00:00 | INFO     | [SYNC] Started sync\n"
        "26:02:15 10:01:00 | ERROR    | [SYNC] Failed sync\n"
        "this line should be ignored\n"
    )

    previous_log_file = settings.log_file
    settings.log_file = str(log_file)
    try:
        response = client.get("/api/logs/?lines=50")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["level"] == "INFO"
        assert data[1]["level"] == "ERROR"

        error_only = client.get("/api/logs/?level=ERROR")
        assert error_only.status_code == 200
        filtered = error_only.json()
        assert len(filtered) == 1
        assert filtered[0]["message"] == "[SYNC] Failed sync"
    finally:
        settings.log_file = previous_log_file


def test_log_history_pages_backwards_to_file_start(client, tmp_path):
    """The /history cursor walks older windows without gaps or duplicates, byte-accurately
    even with multi-byte glyphs, and reports 0 at the start of the file."""
    import api.logs as logs_api

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "posterflow.log"
    all_lines = [f"26/02/15 10:00:{i % 60:02d} | INFO    | [ TEST ] ✓ line {i}\n" for i in range(9)]
    log_file.write_text("".join(all_lines), encoding="utf-8")

    previous_log_file = settings.log_file
    previous_max = logs_api.LOG_WINDOW_MAX_LINES
    settings.log_file = str(log_file)
    logs_api.LOG_WINDOW_MAX_LINES = 4  # small pages so the test walks several windows
    try:
        entries, cursor = logs_api.read_log_window(max_lines=4)
        collected = [e["message"] for e in entries]
        assert collected == [f"[ TEST ] ✓ line {i}" for i in (5, 6, 7, 8)]

        seen_older = []
        while cursor > 0:
            response = client.get(f"/api/logs/history?end={cursor}")
            assert response.status_code == 200
            page = response.json()
            seen_older = [e["message"] for e in page["entries"]] + seen_older
            cursor = page["cursor"]
        assert seen_older == [f"[ TEST ] ✓ line {i}" for i in range(5)]
    finally:
        settings.log_file = previous_log_file
        logs_api.LOG_WINDOW_MAX_LINES = previous_max


def test_log_search_scans_whole_file_case_insensitive(client, tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "posterflow.log"
    log_file.write_text(
        "26/02/15 10:00:00 | INFO    | [ RENAMER ] ✓ Placed The Aviator (2004)\n"
        "26/02/15 10:00:01 | DEBUG   | [ SCANNER ] ◆ something else entirely\n"
        "26/02/15 10:00:02 | ERROR   | [ RENAMER ] ✗ aviator artwork failed\n",
        encoding="utf-8",
    )

    previous_log_file = settings.log_file
    settings.log_file = str(log_file)
    try:
        response = client.get("/api/logs/search?q=AVIATOR")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2 and data["truncated"] is False
        assert [e["level"] for e in data["entries"]] == ["INFO", "ERROR"]

        capped = client.get("/api/logs/search?q=aviator&limit=1").json()
        assert capped["total"] == 2 and capped["truncated"] is True
        assert len(capped["entries"]) == 1
        assert capped["entries"][0]["level"] == "ERROR", "newest match is the one kept"
    finally:
        settings.log_file = previous_log_file


def test_clear_logs_requires_confirm_and_truncates_file(client, tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "posterflow.log"
    log_file.write_text("26:02:15 10:00:00 | INFO     | hello\n")

    previous_log_file = settings.log_file
    settings.log_file = str(log_file)
    try:
        missing_confirm = client.post("/api/logs/clear")
        assert missing_confirm.status_code == 400
        assert "confirm=true" in missing_confirm.json()["detail"]

        cleared = client.post("/api/logs/clear?confirm=true")
        assert cleared.status_code == 200
        assert cleared.json()["message"] == "Logs cleared successfully"
        assert log_file.read_text() == ""
    finally:
        settings.log_file = previous_log_file


def test_job_logs_list_and_missing_file_behavior(client, tmp_path):
    logs_dir = tmp_path / "logs"
    workflow_dir = logs_dir / "workflow"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    log_file = workflow_dir / "workflow.log"
    log_file.write_text("workflow line\n")

    previous_log_file = settings.log_file
    settings.log_file = str(logs_dir / "posterflow.log")
    try:
        listing = client.get("/api/job-logs/")
        assert listing.status_code == 200
        data = listing.json()
        assert "workflow" in data
        assert len(data["workflow"]) == 1
        assert data["workflow"][0]["name"] == "workflow.log"

        missing = client.get("/api/job-logs/workflow/missing.log")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Log file not found"
    finally:
        settings.log_file = previous_log_file


def test_stats_returns_expected_counts(client, test_db):
    drive_one = Drive(
        name="CL2K Subscribed",
        drive_id="stats-cl2k-1",
        style_type="CL2K",
        subscribed=True,
        is_custom=False,
        last_synced=datetime.now(timezone.utc),
    )
    drive_two = Drive(
        name="MM2K Not Subscribed",
        drive_id="stats-mm2k-1",
        style_type="MM2K",
        subscribed=False,
        is_custom=False,
    )
    drive_three = Drive(
        name="Custom Subscribed",
        drive_id="stats-custom-1",
        style_type="Custom",
        subscribed=True,
        is_custom=True,
    )
    test_db.add_all([drive_one, drive_two, drive_three])

    poster_one = Poster(
        drive_id=drive_one.drive_id,
        file_name="a.jpg",
        file_path=str(Path("/tmp") / "a.jpg"),
    )
    poster_two = Poster(
        drive_id=drive_three.drive_id,
        file_name="b.jpg",
        file_path=str(Path("/tmp") / "b.jpg"),
    )
    poster_three = Poster(
        drive_id=drive_two.drive_id,
        file_name="c.jpg",
        file_path=str(Path("/tmp") / "c.jpg"),
    )
    test_db.add_all([poster_one, poster_two, poster_three])
    test_db.commit()

    response = client.get("/api/stats/")
    assert response.status_code == 200
    data = response.json()

    assert data["total_posters"] == 3
    assert data["subscribed_posters"] == 2
    assert data["posters_by_type"]["cl2k"] == 1
    assert data["posters_by_type"]["mm2k"] == 0
    assert data["posters_by_type"]["custom"] == 1

    assert data["drives"]["total"] == 3
    assert data["drives"]["subscribed"] == 2
    assert data["drives"]["synced"] == 1
    assert data["drives"]["by_type"]["cl2k"] == 1
    assert data["drives"]["by_type"]["mm2k"] == 1
    assert data["drives"]["by_type"]["custom"] == 1
    assert data["subscribed_drives_by_type"]["cl2k"] == 1
    assert data["subscribed_drives_by_type"]["mm2k"] == 0
    assert data["subscribed_drives_by_type"]["custom"] == 1


def test_recent_posters_only_from_subscribed_drives(client, test_db):
    subscribed_drive = Drive(
        name="Subscribed Drive",
        drive_id="recent-sub-1",
        style_type="CL2K",
        subscribed=True,
        is_custom=False,
    )
    unsubscribed_drive = Drive(
        name="Unsubscribed Drive",
        drive_id="recent-unsub-1",
        style_type="MM2K",
        subscribed=False,
        is_custom=False,
    )
    test_db.add_all([subscribed_drive, unsubscribed_drive])
    test_db.flush()

    subscribed_root = subscribed_drive.get_local_path(validate=False)
    unsubscribed_root = unsubscribed_drive.get_local_path(validate=False)
    subscribed_root.mkdir(parents=True, exist_ok=True)
    unsubscribed_root.mkdir(parents=True, exist_ok=True)

    (subscribed_root / "older_subscribed.jpg").write_bytes(b"sub-old")
    (subscribed_root / "newest_subscribed.jpg").write_bytes(b"sub-new")
    (unsubscribed_root / "newest_unsubscribed.jpg").write_bytes(b"unsub-new")

    older_subscribed = Poster(
        drive_id=subscribed_drive.drive_id,
        file_name="older_subscribed.jpg",
        file_path=str(subscribed_root / "older_subscribed.jpg"),
        downloaded_at=datetime(2026, 2, 16, 10, 0, 0, tzinfo=timezone.utc),
    )
    newest_unsubscribed = Poster(
        drive_id=unsubscribed_drive.drive_id,
        file_name="newest_unsubscribed.jpg",
        file_path=str(unsubscribed_root / "newest_unsubscribed.jpg"),
        downloaded_at=datetime(2026, 2, 16, 11, 0, 0, tzinfo=timezone.utc),
    )
    newest_subscribed = Poster(
        drive_id=subscribed_drive.drive_id,
        file_name="newest_subscribed.jpg",
        file_path=str(subscribed_root / "newest_subscribed.jpg"),
        downloaded_at=datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone.utc),
    )
    test_db.add_all([older_subscribed, newest_unsubscribed, newest_subscribed])
    test_db.commit()

    response = client.get("/api/stats/recent-posters?limit=10")
    assert response.status_code == 200
    data = response.json()

    assert data["count"] == 2
    assert [item["file_name"] for item in data["items"]] == [
        "newest_subscribed.jpg",
        "older_subscribed.jpg",
    ]
    assert all(item["drive_name"] == "Subscribed Drive" for item in data["items"])


def test_recent_posters_prefers_file_mtime_over_resync_download_time(client, test_db):
    drive = Drive(
        name="Subscribed Drive",
        drive_id="recent-mtime-1",
        style_type="CL2K",
        subscribed=True,
        is_custom=False,
    )
    test_db.add(drive)
    test_db.flush()

    drive_root = drive.get_local_path(validate=False)
    drive_root.mkdir(parents=True, exist_ok=True)

    (drive_root / "old_content_resynced.jpg").write_bytes(b"old")
    (drive_root / "genuinely_new_content.jpg").write_bytes(b"new")

    old_content_resynced = Poster(
        drive_id=drive.drive_id,
        file_name="old_content_resynced.jpg",
        file_path=str(drive_root / "old_content_resynced.jpg"),
        downloaded_at=datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc),
        file_mtime=float(datetime(2021, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp()),
    )
    genuinely_new_content = Poster(
        drive_id=drive.drive_id,
        file_name="genuinely_new_content.jpg",
        file_path=str(drive_root / "genuinely_new_content.jpg"),
        downloaded_at=datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc),
        file_mtime=float(datetime(2025, 12, 31, 0, 0, 0, tzinfo=timezone.utc).timestamp()),
    )
    test_db.add_all([old_content_resynced, genuinely_new_content])
    test_db.commit()

    response = client.get("/api/stats/recent-posters?limit=10")
    assert response.status_code == 200
    data = response.json()

    assert [item["file_name"] for item in data["items"]] == [
        "genuinely_new_content.jpg",
        "old_content_resynced.jpg",
    ]


def test_poster_image_rejects_file_outside_drive_root(client, test_db, tmp_path):
    source_drive = Drive(
        name="Source Drive",
        drive_id="source-drive-1",
        style_type="CL2K",
        subscribed=True,
        is_custom=False,
    )
    other_drive = Drive(
        name="Other Drive",
        drive_id="other-drive-1",
        style_type="MM2K",
        subscribed=True,
        is_custom=False,
    )
    test_db.add_all([source_drive, other_drive])
    test_db.flush()

    source_root = source_drive.get_local_path(validate=False)
    other_root = other_drive.get_local_path(validate=False)
    source_root.mkdir(parents=True, exist_ok=True)
    other_root.mkdir(parents=True, exist_ok=True)

    outside_file = other_root / "shared.jpg"
    outside_file.write_bytes(b"wrong-drive")

    poster = Poster(
        drive_id=source_drive.drive_id,
        file_name="shared.jpg",
        file_path=str(outside_file),
        downloaded_at=datetime.now(timezone.utc),
    )
    test_db.add(poster)
    test_db.commit()

    image_response = client.get(f"/api/stats/posters/{poster.id}/image")
    assert image_response.status_code == 404

    recent_response = client.get("/api/stats/recent-posters?limit=10")
    assert recent_response.status_code == 200
    recent_payload = recent_response.json()
    assert all(item["id"] != poster.id for item in recent_payload["items"])
