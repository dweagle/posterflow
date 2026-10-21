"""The queue closes out a job whose runner raised without finalizing its own record.

Scheduled syncs re-raise JobCancelled expecting a task wrapper to mark the row cancelled, but
the scheduler submits them bare, so a stopped scheduled Sync All stayed 'running' with
'Stopping…' forever.
"""
from concurrent.futures import wait

import core.job_queue as job_queue_module
from core.job_cancel import JobCancelled
from core.job_queue import JobQueueManager
from models.job import Job


def _running_job(test_db):
    job = Job(job_type="Sync All (2 drives)", status="running", progress=9, message="Stopping…")
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


def _run_to_completion(test_db, monkeypatch, runner, job_id):
    monkeypatch.setattr(job_queue_module, "SessionLocal", lambda: test_db)
    # the finalizer closes its session; keep the test's alive
    monkeypatch.setattr(test_db, "close", lambda: None, raising=False)
    manager = JobQueueManager(max_concurrent=1)
    future = manager.submit(runner, job_id)
    wait([future])
    manager.shutdown(wait=True)
    test_db.expire_all()
    return future


def test_cancelled_runner_without_a_wrapper_ends_as_cancelled(test_db, monkeypatch):
    job = _running_job(test_db)

    def _runner():
        raise JobCancelled(f"Job {job.id} cancelled by user")

    future = _run_to_completion(test_db, monkeypatch, _runner, job.id)

    assert isinstance(future.exception(), JobCancelled)
    assert job.status == "cancelled"
    assert job.message == "Stopped by user"
    assert job.completed_at is not None


def test_crashing_runner_without_a_wrapper_ends_as_failed(test_db, monkeypatch):
    job = _running_job(test_db)

    def _runner():
        raise RuntimeError("rclone exploded")

    future = _run_to_completion(test_db, monkeypatch, _runner, job.id)

    assert isinstance(future.exception(), RuntimeError)
    assert job.status == "failed"
    assert job.error == "rclone exploded"
    assert job.completed_at is not None


def test_runner_that_finalized_itself_is_left_alone(test_db, monkeypatch):
    """The manual paths already finalize through _run_job_task; the net must not rewrite them."""
    job = _running_job(test_db)

    def _runner():
        job.status = "completed"
        job.message = "Done"
        test_db.commit()
        raise JobCancelled("late cancel after completion")

    _run_to_completion(test_db, monkeypatch, _runner, job.id)

    assert job.status == "completed"
    assert job.message == "Done"
