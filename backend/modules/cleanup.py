"""Orchestration helper for the opt-in asset cleanup post-action.

Not a background-job entrypoint of its own — it is invoked at the tail of a
Poster Renamer / Border Replacer run (and the workflow) when the cleanup toggle
is enabled, mirroring how ``auto_run_border`` is resolved and run inline.
"""

import traceback

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from core.logging import LogTags, log_error, log_info
from models.job import Job, update_job_state
from models.setting import get_setting_value
from util.poster_settings import get_poster_destination
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
    media_dict: Optional[Dict[str, Any]] = None,
    artwork_boxes: Optional[List[Dict[str, Any]]] = None,
    artwork_sourced: Optional[Dict[int, set]] = None,
    poster_sourced: Optional[Dict[int, set]] = None,
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

    # A missing/non-existent destination is handled by the service, which reports
    # "nothing to clean" rather than deleting anything.
    destination_dir = (
        config_data.get("destination")
        or config_data.get("destination_dir")
        or get_poster_destination(db)
    )

    if job is not None:
        update_job_state(db, job, message="Cleaning up orphaned asset folders...")

    try:
        result = AssetCleanupService(db).cleanup(
            destination_dir,
            dry_run=dry_run,
            delete_unknown=delete_unknown,
            media_dict=media_dict,  # reconcile against the SAME media the rename used (same library selection)
            library_setting_key=config_data.get("library_setting_key") or "asset_renamer_libraries",
            artwork_boxes=artwork_boxes,  # reuse the rename's drive scan instead of re-walking
            artwork_sourced=artwork_sourced,  # reuse the rename's match verdicts instead of re-matching
            poster_sourced=poster_sourced,
        )
    except Exception as exc:
        # Deliberate boundary: cleanup is a post-action, so its failure must not fail the
        # rename/border run that called it. Broad by design — but log the traceback, or a
        # defect in here is indistinguishable from "there was nothing to clean up".
        log_error(
            LogTags.CLEANUP,
            f"Asset cleanup failed (parent run unaffected): {exc}\n{traceback.format_exc()}",
        )
        return None

    counts = result.get("counts", {})
    log_info(
        LogTags.CLEANUP,
        (
            f"Asset cleanup {'(dry run) ' if dry_run else ''}summary: "
            f"removed {counts.get('removed_orphans', 0)} orphan(s), "
            f"{counts.get('removed_stale', 0)} stale duplicate(s), "
            f"{counts.get('removed_artwork', 0)} orphaned artwork, "
            f"{counts.get('removed_posters', 0)} unsourced poster(s); "
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
        f"{counts.get('removed_stale', 0)} stale folder(s) + "
        f"{counts.get('removed_artwork', 0)} orphaned artwork + "
        f"{counts.get('removed_posters', 0)} unsourced poster(s); "
        f"{counts.get('unknown_kept', 0)} kept for review"
    )
