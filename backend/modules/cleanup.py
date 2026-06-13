"""Orchestration helper for the opt-in asset cleanup post-action.

Not a background-job entrypoint of its own — it is invoked at the tail of a
Poster Renamer / Border Replacer run (and the workflow) when the cleanup toggle
is enabled, mirroring how ``auto_run_border`` is resolved and run inline.
"""

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from core.logging import LogTags, log_error, log_info
from models.job import Job, update_job_state
from models.setting import get_setting_value
from services.asset_cleanup import AssetCleanupService

SETTING_AUTO_RUN_CLEANUP = "auto_run_cleanup"
SETTING_CLEANUP_DELETE_UNKNOWN = "cleanup_delete_unknown"


def _resolve_bool(config_data: Dict[str, Any], key: str, db: Session, setting_key: str, default: str = "false") -> bool:
    """Per-run override (bool or 'true'/'false' string) else the persisted setting."""
    override = config_data.get(key)
    if isinstance(override, bool):
        return override
    if isinstance(override, str):
        return override.strip().lower() == "true"
    return str(get_setting_value(db, setting_key, default)).strip().lower() == "true"


def maybe_run_asset_cleanup(
    db: Session,
    *,
    config_data: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
    triggered_by: str = "manual",
    job: Optional[Job] = None,
) -> Optional[Dict[str, Any]]:
    """Run asset cleanup at a run's tail when the toggle is enabled.

    Args:
        config_data: Run config; may carry ``run_cleanup`` / ``cleanup_delete_unknown``
            per-run overrides plus the destination.
        dry_run: Inherit the parent run's dry-run flag (cleanup never deletes on dry runs).
        triggered_by: Run origin; used by callers to gate workflow vs standalone.
        job: Optional job for status-message updates.

    Returns:
        The cleanup result dict, or None when disabled/misconfigured.
    """
    config_data = config_data or {}

    enabled = _resolve_bool(config_data, "run_cleanup", db, SETTING_AUTO_RUN_CLEANUP, default="true")
    if not enabled:
        return None

    delete_unknown = _resolve_bool(config_data, "cleanup_delete_unknown", db, SETTING_CLEANUP_DELETE_UNKNOWN)

    destination_dir = (
        config_data.get("destination")
        or config_data.get("destination_dir")
        or get_setting_value(db, "poster_destination")
    )
    if not destination_dir:
        log_info(LogTags.CLEANUP, "Asset cleanup enabled but no destination configured — skipping")
        return None

    if job is not None:
        update_job_state(db, job, message="Cleaning up orphaned asset folders...")

    try:
        result = AssetCleanupService(db).cleanup(
            destination_dir,
            dry_run=dry_run,
            delete_unknown=delete_unknown,
        )
    except Exception as exc:  # never let cleanup failure fail the parent run
        log_error(LogTags.CLEANUP, f"Asset cleanup failed (parent run unaffected): {exc}")
        return None

    counts = result.get("counts", {})
    log_info(
        LogTags.CLEANUP,
        (
            f"Asset cleanup {'(dry run) ' if dry_run else ''}summary: "
            f"removed {counts.get('removed_orphans', 0)} orphan(s), "
            f"{counts.get('removed_stale', 0)} stale duplicate(s), "
            f"kept {counts.get('unknown_kept', 0)} unknown for review"
        ),
        triggered_by=triggered_by,
    )
    return result


def summarize_cleanup(result: Optional[Dict[str, Any]]) -> Optional[str]:
    """One-line summary for notifications, or None when cleanup didn't run."""
    if not result:
        return None
    if result.get("skipped_for_safety"):
        return "Asset cleanup skipped (safety guard — no media returned)"
    counts = result.get("counts", {})
    prefix = "Would remove" if result.get("dry_run") else "Removed"
    return (
        f"{prefix} {counts.get('removed_orphans', 0)} orphan + "
        f"{counts.get('removed_stale', 0)} stale folder(s); "
        f"{counts.get('unknown_kept', 0)} kept for review"
    )
