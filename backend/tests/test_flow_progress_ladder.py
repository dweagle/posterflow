"""The workflow progress bar is monotonic — clamped with max() on the server
(flow.py) and again on both clients (Sidebar, Dashboard), keyed only on job id.
So a backward progress write inside a run never rewinds the bar; it freezes it
until the run climbs back past the old peak. Each step must therefore hand off to
the next at the same number it finished on, and each child must be promoted across
a range starting where the previous step ended.
"""

import json

import pytest

from models.job import Job, JOB_STATUS_COMPLETED, update_job_state
from models.setting import Setting
import modules.flow as flow_module

PARENT_JOB_TYPE = "Poster Workflow"

ALL_STEPS_ON = {
    "sync_drives": {"enabled": True, "stop_on_error": True, "posters": True, "artwork": True},
    "rename_assets": {"enabled": True, "stop_on_error": True},
    "border_replacer": {"enabled": True, "stop_on_error": True},
    "plex_upload": {"enabled": True, "stop_on_error": False},
    "detect_unmatched": {"enabled": True, "stop_on_error": True},
    "cleanup_assets": {"enabled": False, "delete_unknown": False},
}


@pytest.fixture
def trace_progress(test_db, monkeypatch):
    """Run the real flow, recording every progress point applied to the parent job."""
    trace = []
    real_update = flow_module.update_job_state

    def _recording_update(db, job, **kwargs):
        progress = kwargs.get("progress")
        if progress is not None and job.job_type == PARENT_JOB_TYPE:
            trace.append((kwargs.get("message", ""), progress))
        return real_update(db, job, **kwargs)

    def _promote(db, job, child_id, lo, hi, msg, runner, *args):
        # The child ramps the parent across [lo, hi); both ends are progress points.
        trace.append((f"{msg} (child start)", lo))
        trace.append((f"{msg} (child end)", hi))
        child = db.query(Job).filter(Job.id == child_id).first()
        update_job_state(db, child, status=JOB_STATUS_COMPLETED, progress=100, message="stubbed")
        return {}

    monkeypatch.setattr(flow_module, "update_job_state", _recording_update)
    monkeypatch.setattr(flow_module, "_promote_child_progress_to_parent", _promote)
    monkeypatch.setattr(flow_module, "SessionLocal", lambda: test_db)
    # The flow closes its own session in a finally; keep the test's alive.
    monkeypatch.setattr(test_db, "close", lambda: None, raising=False)

    def _go(config):
        test_db.add(Setting(key="poster_flow_config", value=json.dumps(config)))
        test_db.commit()
        job = Job(job_type=PARENT_JOB_TYPE, status="pending", progress=0, message="Queued")
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)
        flow_module.run_flow_background_job(job.id, dry_run=False)
        return trace

    return _go


def test_progress_never_goes_backward_with_all_steps_enabled(trace_progress):
    trace = trace_progress(ALL_STEPS_ON)

    assert trace, "expected the flow to report progress"
    values = [p for _, p in trace]
    regressions = [
        (trace[i - 1], trace[i]) for i in range(1, len(trace)) if values[i] < values[i - 1]
    ]
    assert not regressions, "progress went backward, which freezes the bar:\n" + "\n".join(
        f"  {prev[1]} ({prev[0]!r})  ->  {cur[1]} ({cur[0]!r})" for prev, cur in regressions
    )


def test_border_step_hands_off_between_rename_and_plex(trace_progress):
    """The border child once ramped over 60->75 while rename had already written 66,
    pinning the bar at 66 for the first 40% of the border run."""
    trace = trace_progress(ALL_STEPS_ON)

    border = [(m, p) for m, p in trace if "border" in m.lower()]
    assert border, "expected the border step to report progress"

    starts = [p for m, p in border if "child start" in m]
    ends = [p for m, p in border if "child end" in m]
    assert starts == [66], f"border child must start where rename ended (66), got {starts}"
    assert ends == [78], f"border child must end where plex begins (78), got {ends}"
