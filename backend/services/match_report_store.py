"""In-memory hand-off for finished match reports, keyed by job id.

The report is a one-off diagnostic, so it lives in the process for an hour and is read
back once by the modal; the Job row carries status and progress, not the payload.
"""
import threading
import time
from typing import Any, Dict, Optional

REPORT_TTL_SECONDS = 60 * 60

_lock = threading.Lock()
_reports: Dict[int, tuple[float, Dict[str, Any]]] = {}


def store_report(job_id: int, payload: Dict[str, Any], *, now: Optional[float] = None) -> None:
    moment = time.monotonic() if now is None else now
    with _lock:
        _prune(moment)
        _reports[int(job_id)] = (moment + REPORT_TTL_SECONDS, payload)


def get_report(job_id: int, *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    moment = time.monotonic() if now is None else now
    with _lock:
        _prune(moment)
        entry = _reports.get(int(job_id))
        return entry[1] if entry else None


def clear_reports() -> None:
    with _lock:
        _reports.clear()


def _prune(moment: float) -> None:
    expired = [job_id for job_id, (expires_at, _) in _reports.items() if expires_at <= moment]
    for job_id in expired:
        del _reports[job_id]
