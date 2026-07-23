"""IDarr sync behavior: the progress-message cadence during upload, and the change detection
that decides whether a workflow scope syncs at all.

Progress cadence: the upload spans ~65 progress points across many thousands of files, so the
number only ticks every ~199 files (roughly 40s). Gating the message on the number means the
display freezes on one filename while files stream by, and the upload looks stalled.

Workflow sync: the "sync after IDarr" toggle pushes a scope to its personal drive when there's
something new to push -- files renamed, forced, no prior sync, or an image (recursing into
asset-drive logos/backgrounds/squareart subfolders) newer than the last sync -- and skips when
nothing changed. A top-level-only scan missed every change on an asset drive.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from models.job import Job
from models.setting import Setting
import modules.idarr as idarr_module

PAST = datetime.now(timezone.utc) - timedelta(days=1)
FUTURE = datetime.now(timezone.utc) + timedelta(days=1)


# --- Progress-message cadence -------------------------------------------------------------


@pytest.fixture
def run_sync(test_db, monkeypatch, tmp_path):
    """Run the real sync job against a fake rclone that replays progress events."""
    writes = []
    real_update = idarr_module.update_job_state

    def _recording_update(db, job, **kwargs):
        if "progress" in kwargs or "message" in kwargs:
            writes.append((kwargs.get("progress"), kwargs.get("message") or ""))
        return real_update(db, job, **kwargs)

    monkeypatch.setattr(idarr_module, "update_job_state", _recording_update)
    monkeypatch.setattr(idarr_module, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None, raising=False)

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.jpg").write_text("x")

    def _go(events):
        class FakeRclone:
            def upload_folder(self, local_path, drive_id, drive_name=None, mode="copy", progress_callback=None):
                for current, total, phase, message in events:
                    progress_callback(current, total, phase, message)
                return {"success": True}

        monkeypatch.setattr(idarr_module, "RcloneService", lambda: FakeRclone())
        job = Job(job_type="idarr", status="pending", progress=0, message="Queued")
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)
        idarr_module.run_idarr_sync_background_job(
            job.id, {"personal_drive_id": "drive123", "source_dir": str(src)}
        )
        return writes

    return _go


def test_message_keeps_moving_while_percentage_holds(run_sync):
    """100/120/150 of 10,901 files all round to the same percent -- the message must
    still advance, because that percent lasts ~40 seconds of real uploading."""
    events = [
        (100, 10901, "uploading_file", "squareart/A (1999) - squareart.jpg: Copied (new)"),
        (100, 10901, "uploading_stats", "Transferred 100/10901 files"),
        (120, 10901, "uploading_stats", "Transferred 120/10901 files"),
        (150, 10901, "uploading_stats", "Transferred 150/10901 files"),
    ]
    writes = run_sync(events)

    stats_writes = [(p, m) for p, m in writes if "files transferred" in m]
    assert stats_writes, "no upload stats reached the job at all"

    # Precondition: these events really do share one percent.
    assert len({p for p, _ in stats_writes}) == 1, (
        f"test is not exercising the freeze; percents: {[p for p, _ in stats_writes]}"
    )
    # The actual invariant: distinct messages despite the frozen percent.
    messages = [m for _, m in stats_writes]
    assert len(set(messages)) == 3, f"message froze while the percentage held: {messages}"


def test_upload_stats_message_carries_count_and_filename(run_sync):
    """The once-per-second stats line is what the user actually reads, so it has to say
    both how far along the upload is and what it is working on."""
    events = [
        (100, 10901, "uploading_file", "squareart/Inside Out (2015) - squareart.jpg: Copied (new)"),
        (100, 10901, "uploading_stats", "Transferred 100/10901 files"),
    ]
    writes = run_sync(events)

    stats = [m for p, m in writes if "files transferred" in m]
    assert stats, "expected an uploading_stats message"
    assert "100/10901 files transferred" in stats[-1]
    assert "Inside Out" in stats[-1], f"newest filename missing: {stats[-1]}"


# --- Workflow sync change-detection -------------------------------------------------------


@pytest.fixture
def run_workflow_idarr(test_db, monkeypatch, tmp_path):
    """Run the real workflow IDarr step with a fake runner that renames nothing, so the sync
    decision comes purely from the change check."""
    sync_calls = []

    class FakeRunner:
        def __init__(self, db):
            pass

        def run(self, scope_config, progress_callback=None):
            return SimpleNamespace(success=True, stats={"files_renamed": 0}, message="ok")

    def _record_sync(db, job, personal_drive_id, source_path, start, end, label):
        sync_calls.append((personal_drive_id, str(source_path), label))

    monkeypatch.setattr(idarr_module, "IdarrRunner", FakeRunner)
    monkeypatch.setattr(idarr_module, "_run_idarr_personal_sync_inline", _record_sync)
    monkeypatch.setattr(idarr_module, "_update_last_sync_time", lambda db, sd: None)
    monkeypatch.setattr(idarr_module, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None, raising=False)

    def _stamp_last_sync(source_dir, when):
        key = idarr_module._sync_state_key(str(source_dir))
        row = test_db.query(Setting).filter(Setting.key == key).first()
        if row:
            row.value = when.isoformat()
        else:
            test_db.add(Setting(key=key, value=when.isoformat()))
        test_db.commit()

    def _go(sync_after_run=True, *, nested=False, last_sync=None, dry_run=False, force=False):
        src = tmp_path / "scope"
        dest = src / "logos" if nested else src
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "poster.png").write_text("x")
        if last_sync is not None:
            _stamp_last_sync(src, last_sync)

        idarr_config = {
            "force_sync_after_run": force,
            "sync_targets": [
                {"label": "CL2K", "source_dir": str(src), "personal_drive_id": "drive-1"},
            ],
        }
        run_config = {
            "idarr_config": idarr_config,
            "scope_indices": [0],
            "dry_run": dry_run,
            "sync_after_run": sync_after_run,
        }
        job = Job(job_type="idarr", status="pending", progress=0, message="Queued")
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)
        idarr_module.run_idarr_workflow_step(job.id, run_config)
        return sync_calls

    return _go


def test_syncs_when_a_top_level_file_is_newer(run_workflow_idarr):
    calls = run_workflow_idarr(nested=False, last_sync=PAST)

    assert len(calls) == 1, "a source file newer than last sync must trigger the sync"
    assert calls[0][0] == "drive-1"


def test_syncs_when_a_nested_subfolder_file_is_newer(run_workflow_idarr):
    """The recursion regression: an asset-drive change lives in logos/ -- the old top-level
    scan saw only the subfolder and skipped, so the drive never synced."""
    calls = run_workflow_idarr(nested=True, last_sync=PAST)

    assert len(calls) == 1, "a newer file inside a subfolder must trigger the sync"


def test_syncs_when_no_previous_sync_recorded(run_workflow_idarr):
    calls = run_workflow_idarr(last_sync=None)

    assert len(calls) == 1, "first-ever run (no last-sync stamp) must sync"


def test_skips_when_nothing_changed_since_last_sync(run_workflow_idarr):
    calls = run_workflow_idarr(nested=False, last_sync=FUTURE)

    assert calls == [], "nothing newer than the last sync -> no push"


def test_force_sync_pushes_even_when_unchanged(run_workflow_idarr):
    calls = run_workflow_idarr(nested=False, last_sync=FUTURE, force=True)

    assert len(calls) == 1, "force_sync_after_run must push regardless of change detection"


def test_no_sync_when_opt_in_is_off(run_workflow_idarr):
    calls = run_workflow_idarr(sync_after_run=False, last_sync=PAST)

    assert calls == [], "no sync should happen when the toggle is unchecked"


def test_no_sync_on_dry_run(run_workflow_idarr):
    calls = run_workflow_idarr(sync_after_run=True, last_sync=PAST, dry_run=True)

    assert calls == [], "a dry run must not push anything to the personal drive"


def test_change_check_recurses_into_asset_drive_subfolders(tmp_path):
    src = tmp_path / "asset"
    (src / "logos").mkdir(parents=True)
    (src / "logos" / "a.png").write_text("x")

    assert idarr_module._has_files_newer_than(src, PAST) is True
    assert idarr_module._has_files_newer_than(src, FUTURE) is False


def test_change_check_ignores_non_image_files(tmp_path):
    src = tmp_path / "flat"
    src.mkdir()
    (src / "notes.txt").write_text("x")

    assert idarr_module._has_files_newer_than(src, PAST) is False, "non-image writes are not a change"
