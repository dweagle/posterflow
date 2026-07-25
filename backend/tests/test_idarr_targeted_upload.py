"""Targeted (quick add) IDarr runs upload their own files instead of mirroring the scope.

A single dropped poster used to trigger a full `rclone sync` of the whole scope folder: list the
entire remote, check every local file, push one image. Targeted runs now copy just the files they
touched, hold back anything that went pending or conflicted, and leave the last-sync timestamp
alone so a later full sync still reconciles deletes and stale renames.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.job import Job
from models.idarr import IdarrRun
from services.idarr_runner import IdarrRunner
import modules.idarr as idarr_module


def _outcomes(assets, operation_rows=None, unmatched=None, source_dir=Path("/scope"), selected=None):
    return IdarrRunner._build_targeted_outcomes(
        assets=assets,
        operation_rows=operation_rows or [],
        unmatched_assets=unmatched or [],
        source_dir=source_dir,
        selected_filenames=selected or set(),
    )


# --- Outcome classification ---------------------------------------------------------------


def test_renamed_match_is_ready_and_keeps_its_original_name():
    """The maker dropped "inside out.jpg"; the popup has to name the file they recognize."""
    rows = _outcomes(
        assets=[{"file_path": Path("/scope/Inside Out (2015) {tmdb-150540}.jpg"), "has_id": True, "title": "Inside Out", "year": 2015}],
        operation_rows=[{
            "from_path": "/scope/inside out.jpg",
            "to_path": "/scope/Inside Out (2015) {tmdb-150540}.jpg",
            "status": "success",
        }],
    )

    assert len(rows) == 1
    assert rows[0]["status"] == "ready"
    assert rows[0]["source_filename"] == "inside out.jpg"
    assert rows[0]["final_filename"] == "Inside Out (2015) {tmdb-150540}.jpg"
    assert rows[0]["relative_path"] == "Inside Out (2015) {tmdb-150540}.jpg"


def test_unmatched_file_is_pending_with_its_reason():
    rows = _outcomes(
        assets=[{"file_path": Path("/scope/who knows.jpg"), "has_id": False, "match_reason": "no_match", "title": "Who Knows"}],
        unmatched=[{"source_path": "/scope/who knows.jpg"}],
    )

    assert rows[0]["status"] == "pending"
    assert rows[0]["reason"] == "no_match"


def test_skipped_rename_is_a_conflict():
    rows = _outcomes(
        assets=[{"file_path": Path("/scope/dupe.jpg"), "has_id": True, "title": "Dupe", "year": 2020}],
        operation_rows=[{
            "from_path": "/scope/dupe.jpg",
            "to_path": "/scope/Dupe (2020) {tmdb-1}.jpg",
            "status": "skipped",
            "reason": "intra_batch_filename_conflict",
        }],
    )

    assert rows[0]["status"] == "conflict"
    assert rows[0]["reason"] == "intra_batch_filename_conflict"


def test_remade_poster_replacing_the_older_copy_is_ready_to_upload():
    """Re-dropping a poster: the run archives the older copy and the newer one takes the same
    corrected name, so it uploads over the drive file it replaces -- no notice, no orphan."""
    rows = _outcomes(
        assets=[{"file_path": Path("/scope/Inside Out (2015) {tmdb-150540}.jpg"), "has_id": True, "title": "Inside Out", "year": 2015}],
        operation_rows=[{
            "from_path": "/scope/inside out.jpg",
            "to_path": "/scope/Inside Out (2015) {tmdb-150540}.jpg",
            "status": "success",
            "reason": "archived_older_existing_renamed_newer_incoming",
        }],
    )

    assert rows[0]["status"] == "ready"
    assert rows[0]["relative_path"] == "Inside Out (2015) {tmdb-150540}.jpg"


def test_redrop_that_loses_the_mtime_check_is_held_back_as_a_conflict():
    """If the incoming copy isn't newer, the run leaves both files for the picker -- the drive
    keeps what it has and the maker has to be told."""
    rows = _outcomes(
        assets=[{"file_path": Path("/scope/inside out.jpg"), "has_id": True, "title": "Inside Out", "year": 2015}],
        operation_rows=[{
            "from_path": "/scope/inside out.jpg",
            "to_path": "/scope/Inside Out (2015) {tmdb-150540}.jpg",
            "status": "skipped",
            "reason": "rename_target_exists",
        }],
    )

    assert rows[0]["status"] == "conflict"
    assert rows[0]["reason"] == "rename_target_exists"


def test_asset_drive_subfolder_keeps_its_relative_path():
    """Artwork scopes nest under logos/backgrounds/squareart -- uploading the bare filename
    would drop the file in the scope root."""
    rows = _outcomes(
        assets=[{"file_path": Path("/scope/logos/Show (2020) {tmdb-9} - logo.png"), "has_id": True, "title": "Show", "year": 2020}],
    )

    assert rows[0]["relative_path"] == "logos/Show (2020) {tmdb-9} - logo.png"


def test_selected_file_that_never_scanned_is_reported_missing():
    rows = _outcomes(assets=[], selected={"ghost.jpg"})

    assert [(row["source_filename"], row["status"]) for row in rows] == [("ghost.jpg", "missing")]


# --- Upload behavior ----------------------------------------------------------------------


class FakeRclone:
    def __init__(self, fail_on=None):
        self.uploads = []
        self.folder_syncs = []
        self.fail_on = fail_on or set()

    def upload_file(self, local_path, drive_id, remote_path, drive_name=None):
        self.uploads.append((str(local_path), drive_id, remote_path))
        if Path(remote_path).name in self.fail_on:
            return {"success": False, "error": "quota exceeded"}
        return {"success": True, "remote_path": remote_path}

    def upload_folder(self, *args, **kwargs):  # pragma: no cover - must never be called
        self.folder_syncs.append((args, kwargs))
        return {"success": True}


@pytest.fixture
def run_targeted_upload(test_db, monkeypatch, tmp_path):
    """Run the real single-item upload job against a fake rclone and a real scope folder."""

    def _go(outcomes, *, fail_on=None):
        source_dir = tmp_path / "scope"
        (source_dir / "logos").mkdir(parents=True, exist_ok=True)
        for row in outcomes:
            (source_dir / row["relative_path"]).write_text("x")

        fake = FakeRclone(fail_on=fail_on)
        monkeypatch.setattr(idarr_module, "RcloneService", lambda: fake)
        monkeypatch.setattr(idarr_module, "SessionLocal", lambda: test_db)
        monkeypatch.setattr(test_db, "close", lambda: None, raising=False)

        run = IdarrRun(job_id=None, success=True, details_json=json.dumps({"targeted_outcomes": outcomes}), unmatched_count=0)
        test_db.add(run)
        job = Job(job_type="idarr", status="pending", progress=0, message="Queued")
        test_db.add(job)
        test_db.commit()
        test_db.refresh(run)
        test_db.refresh(job)

        idarr_module.run_idarr_targeted_upload_job(
            job.id,
            {
                "personal_drive_id": "drive-1",
                "source_dir": str(source_dir),
                "label": "CL2K",
                "idarr_run_id": run.id,
                "triggered_by_job_id": 1,
            },
        )
        test_db.refresh(run)
        stored = json.loads(run.details_json)["targeted_outcomes"]
        return job, stored, fake

    return _go


def _outcome(name, status="ready", relative_path=None):
    return {
        "source_filename": name,
        "final_filename": name,
        "relative_path": relative_path or name,
        "title": "",
        "year": None,
        "status": status,
        "reason": "",
        "uploaded": False,
    }


def test_only_ready_files_are_uploaded(run_targeted_upload):
    job, outcomes, fake = run_targeted_upload([
        _outcome("Matched (2020) {tmdb-1}.jpg"),
        _outcome("pending.jpg", status="pending"),
        _outcome("conflicted.jpg", status="conflict"),
    ])

    assert [Path(path).name for _, _, path in fake.uploads] == ["Matched (2020) {tmdb-1}.jpg"]
    assert outcomes[0]["status"] == "uploaded" and outcomes[0]["uploaded"] is True
    assert outcomes[1]["status"] == "pending" and outcomes[1]["uploaded"] is False
    assert "uploaded=1, failed=0, held_back=2" in str(job.message)


def test_upload_uses_the_relative_path_for_asset_drives(run_targeted_upload):
    _, _, fake = run_targeted_upload([_outcome("Show - logo.png", relative_path="logos/Show - logo.png")])

    assert fake.uploads[0][2] == "logos/Show - logo.png"


def test_failed_upload_is_recorded_on_the_outcome(run_targeted_upload):
    job, outcomes, _ = run_targeted_upload(
        [_outcome("Matched (2020) {tmdb-1}.jpg")],
        fail_on={"Matched (2020) {tmdb-1}.jpg"},
    )

    assert outcomes[0]["status"] == "upload_failed"
    assert "quota exceeded" in outcomes[0]["reason"]
    assert "failed=1" in str(job.message)


def test_upload_job_completes_with_its_own_status(run_targeted_upload):
    """It's a job of its own, so it has to finish like one -- not leave a stuck row behind."""
    job, _, _ = run_targeted_upload([_outcome("Matched (2020) {tmdb-1}.jpg")])

    assert job.status == "completed"
    assert job.progress == 100


# --- Job wiring ---------------------------------------------------------------------------


@pytest.fixture
def run_targeted_job(test_db, monkeypatch, tmp_path):
    """Run the real IDarr job with a fake runner, capturing what it hands to the job queue."""
    from core import job_queue as job_queue_module

    queued_full_syncs = []
    submissions = []

    def _go(*, outcomes, sync_after_run=True, source_filenames=("dropped.jpg",)):
        source_dir = tmp_path / "scope"
        source_dir.mkdir(parents=True, exist_ok=True)
        for row in outcomes:
            (source_dir / row["relative_path"]).write_text("x")

        class FakeRunner:
            def __init__(self, db):
                pass

            def run(self, config_data, progress_callback=None):
                return SimpleNamespace(
                    success=True,
                    message="ok",
                    stats={"files_renamed": len(outcomes)},
                    warnings=[],
                    unmatched_assets=[],
                    details={"targeted_outcomes": outcomes},
                )

        fake = FakeRclone()
        monkeypatch.setattr(idarr_module, "IdarrRunner", FakeRunner)
        monkeypatch.setattr(idarr_module, "RcloneService", lambda: fake)
        monkeypatch.setattr(idarr_module, "SessionLocal", lambda: test_db)
        monkeypatch.setattr(test_db, "close", lambda: None, raising=False)
        monkeypatch.setattr(
            idarr_module,
            "_queue_idarr_sync_after_run",
            lambda db, config_data, triggered_by_job_id: queued_full_syncs.append(triggered_by_job_id),
        )
        monkeypatch.setattr(
            job_queue_module.job_queue,
            "submit",
            lambda job_func, job_id, *args, **kwargs: submissions.append((job_func, args)),
        )

        job = Job(job_type="idarr", status="pending", progress=0, message="Queued")
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        config_data = {
            "source_dir": str(source_dir),
            "sync_target_index": 0,
            "sync_targets": [{"label": "CL2K", "source_dir": str(source_dir), "personal_drive_id": "drive-1"}],
            "source_filenames": list(source_filenames),
            "sync_after_run": sync_after_run,
            "scope_token": "scope-1",
        }
        idarr_module._run_idarr_background_job_locked(job.id, config_data)
        return SimpleNamespace(
            job_id=job.id,
            rclone=fake,
            full_syncs=queued_full_syncs,
            submissions=submissions,
            run_queued=lambda: [job_func(*args) for job_func, args in submissions],
        )

    return _go


def _run_row(test_db, job_id):
    return test_db.query(IdarrRun).filter(IdarrRun.job_id == job_id).first()


def test_targeted_run_queues_its_own_upload_job_not_the_mirror_sync(run_targeted_job, test_db):
    """The upload gets its own job so it has its own log, progress and history entry."""
    outcome = run_targeted_job(outcomes=[_outcome("Matched (2020) {tmdb-1}.jpg")])

    assert outcome.full_syncs == [], "a targeted run must not fall back to the full mirror sync"
    assert outcome.rclone.folder_syncs == []
    assert outcome.rclone.uploads == [], "the rename job must not upload inline"

    assert len(outcome.submissions) == 1
    job_func, _ = outcome.submissions[0]
    assert job_func is idarr_module.run_idarr_targeted_upload_job

    details = json.loads(_run_row(test_db, outcome.job_id).details_json)
    upload_job = test_db.query(Job).filter(Job.id == details["targeted_upload_job_id"]).first()
    assert upload_job is not None
    assert f"after IDarr job {outcome.job_id}" in str(upload_job.message)


def test_queued_upload_job_uploads_and_updates_the_run(run_targeted_job, test_db):
    outcome = run_targeted_job(outcomes=[_outcome("Matched (2020) {tmdb-1}.jpg")])
    outcome.run_queued()

    assert len(outcome.rclone.uploads) == 1
    stored = json.loads(_run_row(test_db, outcome.job_id).details_json)["targeted_outcomes"]
    assert stored[0]["status"] == "uploaded"


def test_nothing_to_upload_skips_the_upload_job_entirely(run_targeted_job, test_db):
    """Every file held back means no upload -- don't leave an empty job in the history."""
    outcome = run_targeted_job(outcomes=[_outcome("pending.jpg", status="pending")])

    assert outcome.submissions == []
    assert "targeted_upload_job_id" not in json.loads(_run_row(test_db, outcome.job_id).details_json)


def test_targeted_run_leaves_the_last_sync_timestamp_alone(run_targeted_job, test_db):
    """Stamping it would mask older local changes from the next full sync's change check."""
    outcome = run_targeted_job(outcomes=[_outcome("Matched (2020) {tmdb-1}.jpg")])
    outcome.run_queued()

    assert idarr_module._get_last_sync_time(test_db, _run_row(test_db, outcome.job_id).source_dir) is None


def test_pending_file_is_still_recorded_when_auto_upload_is_off(run_targeted_job, test_db):
    """Auto-upload off means nothing uploads, but the maker still has to hear about pending."""
    outcome = run_targeted_job(
        outcomes=[_outcome("pending.jpg", status="pending")],
        sync_after_run=False,
    )

    assert outcome.rclone.uploads == []
    assert outcome.submissions == []
    stored = json.loads(_run_row(test_db, outcome.job_id).details_json)["targeted_outcomes"]
    assert stored[0]["status"] == "pending"


def test_run_result_waits_for_the_upload_job_before_reporting_finished(client, test_db, run_targeted_job):
    """The popup has to wait for the upload's verdict, or an upload failure never gets shown."""
    outcome = run_targeted_job(outcomes=[
        _outcome("Matched (2020) {tmdb-1}.jpg"),
        _outcome("pending.jpg", status="pending"),
    ])

    payload = client.get(f"/api/idarr/run-result/{outcome.job_id}").json()
    assert payload["status"] == "completed", "the rename itself is done"
    assert payload["finished"] is False, "but its upload job is still queued"
    assert payload["upload_job_id"] is not None

    outcome.run_queued()

    payload = client.get(f"/api/idarr/run-result/{outcome.job_id}").json()
    assert payload["finished"] is True
    assert [row["status"] for row in payload["outcomes"]] == ["uploaded", "pending"]


def test_run_result_is_finished_immediately_when_no_upload_was_queued(client, test_db, run_targeted_job):
    outcome = run_targeted_job(outcomes=[_outcome("pending.jpg", status="pending")])

    payload = client.get(f"/api/idarr/run-result/{outcome.job_id}").json()
    assert payload["finished"] is True
    assert payload["upload_job_id"] is None
