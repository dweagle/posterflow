"""
Cooperative job cancellation registry.

Jobs run on a thread pool and cannot be force-killed. Instead, a cancel request
records the job id here; worker code checks `check_cancelled(job_id)` at its
progress callbacks and loop boundaries and raises `JobCancelled` to unwind.
"""
import threading


class JobCancelled(BaseException):
    """Raised inside a worker when the user has requested the job stop.

    Derives from BaseException (not Exception) — like asyncio.CancelledError — so
    the broad ``except Exception`` handlers throughout the sync/idarr/service call
    stack cannot swallow a cancellation. Only the explicit ``except JobCancelled``
    handlers at each worker's top level catch it and finalize the job as
    'cancelled'. ``try/finally`` cleanup still runs normally.
    """


_lock = threading.Lock()
_cancel_requested: set[int] = set()


def request_cancel(job_id: int) -> None:
    """Mark a job for cancellation."""
    with _lock:
        _cancel_requested.add(int(job_id))


def is_cancel_requested(job_id: int) -> bool:
    """Return True if a cancel has been requested for this job."""
    with _lock:
        return int(job_id) in _cancel_requested


def clear_cancel(job_id: int) -> None:
    """Forget any cancel request for a job (call when it reaches a terminal state)."""
    with _lock:
        _cancel_requested.discard(int(job_id))


def check_cancelled(job_id: int) -> None:
    """Raise JobCancelled if a stop has been requested for this job."""
    if is_cancel_requested(job_id):
        raise JobCancelled(f"Job {job_id} cancelled by user")
