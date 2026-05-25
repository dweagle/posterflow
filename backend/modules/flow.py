import json
import threading
import time
import traceback
from functools import partial
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from database import SessionLocal
from core.logging import (
    LogTags,
    log_info,
    log_error,
    log_success,
    log_debug,
    log_step,
    log_section_start,
    log_section_end,
    add_job_log_handler,
    remove_job_log_handler,
)
from models.setting import get_setting, get_setting_value
from models.job import (
    Job,
    JOB_TYPE_SYNC_ALL_PREFIX,
    JOB_TYPE_POSTER_RENAMER,
    JOB_TYPE_BORDER_REPLACER,
    JOB_TYPE_UNMATCHED_DETECTION,
    JOB_TYPE_PLEX_UPLOAD,
    JOB_TYPE_IDARR,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    format_start_message,
    format_workflow_step,
    format_workflow_step_complete,
    mark_job_failed,
    update_job_state,
)
from modules.sync import run_sync_all_job
from modules.renamer import run_rename_background_job, _build_renamer_section
from modules.border import run_border_replacer_background_job
from modules.unmatched import run_unmatched_detection_background_job
from modules.upload import run_plex_upload_background_job
from modules.idarr import run_idarr_workflow_step
from services.discord_notifications import send_discord_notification, send_discord_workflow_summary, send_major_error_notification
from core.hooks import run_post_job_hook, HOOK_KEY_WORKFLOW


def _create_child_job(db: Any, job_type: str, message: str) -> Job:
    """Create a child job record used by workflow-orchestrated module runs."""
    child = Job(
        job_type=job_type,
        status=JOB_STATUS_PENDING,
        progress=0,
        message=message,
    )
    db.add(child)
    db.commit()
    db.refresh(child)
    return child


def _get_child_result(db: Any, child_job_id: int) -> tuple[bool, str | None]:
    """Read child job state after a module runner finishes."""
    child = db.query(Job).filter(Job.id == child_job_id).first()
    if not child:
        return False, "Child job record not found"
    if child.status == JOB_STATUS_FAILED:
        return False, child.error or child.message or "Module failed"
    return True, child.message


def _promote_child_progress_to_parent(
    db: Any,
    parent_job: Job,
    child_job_id: int,
    parent_start: int,
    parent_end: int,
    fallback_message: str,
    runner: Callable[..., Any],
    *runner_args: Any,
) -> Any:
    """Run a child job and continuously map child progress into the parent workflow range."""
    result_container: dict[str, Any] = {"value": None}
    error_container: dict[str, Exception | None] = {"error": None}

    def _run_child() -> None:
        try:
            result_container["value"] = runner(child_job_id, *runner_args)
        except Exception as exc:
            error_container["error"] = exc

    span = max(1, parent_end - parent_start)
    max_mapped = parent_end - 1 if parent_end > parent_start else parent_end

    last_parent_progress = int(parent_job.progress or 0)
    last_parent_message = str(parent_job.message or "")

    thread = threading.Thread(target=_run_child, daemon=True, name=f"workflow_child_{child_job_id}")
    thread.start()

    while thread.is_alive():
        db.expire_all()
        child = db.query(Job).filter(Job.id == child_job_id).first()
        if child:
            child_progress = max(0, min(int(child.progress or 0), 99))
            mapped_progress = parent_start + int((child_progress / 100) * span)
            mapped_progress = max(parent_start, min(max_mapped, mapped_progress))
            mapped_progress = max(last_parent_progress, mapped_progress)
            mapped_message = str(child.message or fallback_message)

            if mapped_progress != last_parent_progress or mapped_message != last_parent_message:
                update_job_state(db, parent_job, progress=mapped_progress, message=mapped_message)
                last_parent_progress = mapped_progress
                last_parent_message = mapped_message

        time.sleep(0.75)

    thread.join()

    db.expire_all()
    child = db.query(Job).filter(Job.id == child_job_id).first()
    if child:
        child_progress = max(0, min(int(child.progress or 0), 99))
        mapped_progress = parent_start + int((child_progress / 100) * span)
        mapped_progress = max(parent_start, min(max_mapped, mapped_progress))
        mapped_progress = max(last_parent_progress, mapped_progress)
        mapped_message = str(child.message or fallback_message)

        if mapped_progress != last_parent_progress or mapped_message != last_parent_message:
            update_job_state(db, parent_job, progress=mapped_progress, message=mapped_message)

    if error_container["error"] is not None:
        raise error_container["error"]

    return result_container["value"]


def run_flow_background_job(job_id: int, dry_run: bool = False, on_finish: Optional[Callable[[], None]] = None, triggered_by: str = "manual", config_override: Optional[dict] = None) -> None:
    """
    Execute the Flow workflow in a background thread.
    Shared orchestration used by poster manager entrypoints.

    Args:
        job_id: Workflow job id
        dry_run: Whether to run in dry run mode
        on_finish: Optional callback executed in finally block (used to release locks)
        triggered_by: "manual" or "scheduled"
    """
    db = SessionLocal()

    workflow_handler_id = add_job_log_handler("workflow", job_id, "Poster Workflow")
    success = False

    try:
        log_debug(LogTags.WORKFLOW, f"Background task started for job {job_id}", job_id=job_id)

        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log_error(LogTags.WORKFLOW, f"Job {job_id} not found in database", job_id=job_id)
            return

        update_job_state(
            db,
            job,
            status=JOB_STATUS_RUNNING,
            message=format_start_message("workflow", dry_run=dry_run),
            progress=0,
        )

        log_section_start(LogTags.WORKFLOW, f"Starting poster workflow execution in background (Job ID: {job_id})")

        flow_setting = get_setting(db, "poster_flow_config")

        if config_override is not None:
            flow_config = config_override
        elif flow_setting:
            flow_config = json.loads(flow_setting.value)
        else:
            flow_config = {
                "sync_drives": {"enabled": True, "stop_on_error": True},
                "rename_posters": {"enabled": True, "stop_on_error": True},
                "detect_unmatched": {"enabled": True, "stop_on_error": True},
                "border_replacer": {"enabled": False, "stop_on_error": True},
                "plex_upload": {"enabled": False, "stop_on_error": False},
            }

        if dry_run:
            log_info(LogTags.WORKFLOW, "Dry run enabled - no changes will be made")

        results = {
            "success": True,
            "jobs_run": [],
            "jobs_skipped": [],
            "jobs_failed": [],
        }

        # ── Step 1/6: IDarr ────────────────────────────────────────────────
        idarr_flow_cfg = flow_config.get("idarr", {})
        if idarr_flow_cfg.get("enabled", False):
            log_step(LogTags.WORKFLOW, 1, 6, "Running IDarr rename")
            update_job_state(db, job, progress=0, message=format_workflow_step(1, 6, "Running IDarr rename..."))

            idarr_setting_raw = get_setting_value(db, "maker_tools_idarr_config", "")
            idarr_cfg_valid = False
            idarr_run_config: dict[str, Any] = {}
            if str(idarr_setting_raw or "").strip():
                try:
                    idarr_cfg_parsed = json.loads(idarr_setting_raw)
                    if isinstance(idarr_cfg_parsed, dict) and isinstance(idarr_cfg_parsed.get("sync_targets"), list):
                        idarr_run_config = {
                            "idarr_config": idarr_cfg_parsed,
                            "scope_indices": [int(i) for i in (idarr_flow_cfg.get("scope_indices") or []) if isinstance(i, (int, float))],
                            "dry_run": dry_run,
                            "sync_after_run": bool(idarr_flow_cfg.get("sync_after_run", False)),
                        }
                        idarr_cfg_valid = True
                except Exception:
                    pass

            if idarr_cfg_valid:
                idarr_child = _create_child_job(db, JOB_TYPE_IDARR, "Workflow requested IDarr step")
                try:
                    _promote_child_progress_to_parent(
                        db, job, idarr_child.id, 0, 15,
                        format_workflow_step(1, 6, "Running IDarr rename..."),
                        run_idarr_workflow_step,
                        idarr_run_config,
                    )
                    child_ok, child_message = _get_child_result(db, idarr_child.id)

                    results["jobs_run"].append({
                        "job": "idarr",
                        "success": child_ok,
                        "child_job_id": idarr_child.id,
                        "embed_spec": {
                            "event_type": "success",
                            "title": "IDarr Rename",
                            "description": child_message or "IDarr rename completed",
                            "fields": [],
                            "color": 0x4CAF50,
                        },
                    })

                    if child_ok:
                        log_success(LogTags.WORKFLOW, "Step 1/6 complete: IDarr rename finished", child_job_id=idarr_child.id)
                        update_job_state(db, job, progress=15, message=format_workflow_step_complete(1, 6, "IDarr rename finished"))
                    else:
                        raise Exception(child_message or "IDarr step failed")

                except Exception as e:
                    error_msg = str(e)
                    log_error(LogTags.WORKFLOW, f"IDarr step failed: {error_msg}\n{traceback.format_exc()}")
                    results["jobs_failed"].append({"job": "idarr", "error": error_msg, "child_job_id": idarr_child.id})

                    if idarr_flow_cfg.get("stop_on_error", False):
                        results["success"] = False
                        update_job_state(db, job, status=JOB_STATUS_FAILED, error=error_msg)
                        send_discord_notification(
                            db, feature_key="workflow", event_type="error",
                            title="Workflow Failed",
                            description=f"IDarr step failed: {error_msg}",
                            fields=[
                                {"name": "Job ID", "value": str(job_id), "inline": True},
                                {"name": "Step", "value": "IDarr Rename", "inline": True},
                            ],
                            color=0xF44336,
                        )
                        send_major_error_notification(db, source="workflow.idarr", message=error_msg, job_id=job_id)
                        return results
            else:
                log_info(LogTags.WORKFLOW, "Step 1/6: IDarr enabled but not configured — skipping")
                results["jobs_skipped"].append({"job": "idarr", "reason": "IDarr not configured (no valid maker_tools_idarr_config)"})
                update_job_state(db, job, progress=15, message=format_workflow_step(1, 6, "IDarr skipped (not configured)"))
        else:
            log_info(LogTags.WORKFLOW, "Step 1/6: IDarr disabled - skipping")
            results["jobs_skipped"].append({"job": "idarr", "reason": "Disabled in configuration"})
            update_job_state(db, job, progress=15, message=format_workflow_step(1, 6, "IDarr skipped"))

        # ── Step 2/6: Sync Google Drives ───────────────────────────────────
        if flow_config.get("sync_drives", {}).get("enabled", False):
            log_step(LogTags.WORKFLOW, 2, 6, "Syncing Google Drives")
            update_job_state(db, job, progress=15, message=format_workflow_step(2, 6, "Syncing Google Drives..."))

            sync_child = _create_child_job(db, JOB_TYPE_SYNC_ALL_PREFIX, "Workflow requested sync all")
            try:
                sync_result = _promote_child_progress_to_parent(
                    db,
                    job,
                    sync_child.id,
                    15,
                    40,
                    format_workflow_step(2, 6, "Syncing Google Drives..."),
                    partial(run_sync_all_job, triggered_by="workflow"),
                    True,  # skip_discord: sub-module notifications suppressed in workflow
                )
                child_ok, child_message = _get_child_result(db, sync_child.id)

                _sr = sync_result or {}
                results["jobs_run"].append({
                    "job": "sync_drives",
                    "success": child_ok,
                    "child_job_id": sync_child.id,
                    "result": sync_result,
                    "embed_spec": {
                        "event_type": "success",
                        "title": "Google Drive Sync Summary",
                        "description": f"{int(_sr.get('drives_synced', 1))} drive(s) processed",
                        "fields": [
                            {"name": "New", "value": str(int(_sr.get("added", 0))), "inline": True},
                            {"name": "Replaced", "value": str(int(_sr.get("updated", 0))), "inline": True},
                            {"name": "Deleted", "value": str(int(_sr.get("deleted", 0))), "inline": True},
                        ],
                        "color": 0x4CAF50,
                    },
                })

                if child_ok:
                    log_success(LogTags.WORKFLOW, "Step 2/6 complete: Sync finished", child_job_id=sync_child.id)
                    update_job_state(db, job, progress=40, message=format_workflow_step_complete(2, 6, "Sync finished"))
                else:
                    raise Exception(child_message or "Sync step failed")

            except Exception as e:
                error_msg = str(e)
                log_error(LogTags.WORKFLOW, f"Sync drives failed: {error_msg}\n{traceback.format_exc()}")
                results["jobs_failed"].append({"job": "sync_drives", "error": error_msg, "child_job_id": sync_child.id})

                if flow_config.get("sync_drives", {}).get("stop_on_error", False):
                    results["success"] = False
                    update_job_state(db, job, status=JOB_STATUS_FAILED, error=error_msg)
                    send_discord_notification(
                        db,
                        feature_key="workflow",
                        event_type="error",
                        title="Workflow Failed",
                        description=f"Sync step failed: {error_msg}",
                        fields=[
                            {"name": "Job ID", "value": str(job_id), "inline": True},
                            {"name": "Step", "value": "Sync Drives", "inline": True},
                        ],
                        color=0xF44336,
                    )
                    send_major_error_notification(
                        db,
                        source="workflow.sync_drives",
                        message=error_msg,
                        job_id=job_id,
                    )
                    return results
        else:
            log_info(LogTags.WORKFLOW, "Step 2/6: Sync drives disabled - skipping")
            results["jobs_skipped"].append({"job": "sync_drives", "reason": "Disabled in configuration"})
            update_job_state(db, job, progress=40, message=format_workflow_step(2, 6, "Sync drives skipped"))

        # ── Step 3/6: Rename & Organize Posters ───────────────────────────────
        if flow_config.get("rename_posters", {}).get("enabled", False):
            log_step(LogTags.WORKFLOW, 3, 6, "Renaming and organizing posters")
            update_job_state(db, job, progress=40, message=format_workflow_step(3, 6, "Renaming and organizing posters..."))

            rename_child = _create_child_job(db, JOB_TYPE_POSTER_RENAMER, "Workflow requested Poster Renamer")
            try:
                config_data: dict[str, Any] = {
                    "destination": get_setting_value(db, "poster_destination", "/config/posters/assets"),
                    "action_type": get_setting_value(db, "poster_action_type", "copy"),
                    "dry_run": dry_run,
                    "match_threshold": 0.8,
                    "auto_run_border": False,
                    "skip_border_post_processing": flow_config.get("border_replacer", {}).get("enabled", False),
                }
                asset_folders_value = get_setting_value(db, "poster_asset_folders")
                config_data["asset_folders"] = asset_folders_value.lower() == "true" if asset_folders_value else True

                _promote_child_progress_to_parent(
                    db,
                    job,
                    rename_child.id,
                    40,
                    65,
                    format_workflow_step(3, 6, "Renaming and organizing posters..."),
                    partial(run_rename_background_job, triggered_by="workflow"),
                    config_data,
                    True,  # skip_discord: sub-module notifications suppressed in workflow
                )
                child_ok, child_message = _get_child_result(db, rename_child.id)

                _rs: dict = {}
                _ro: dict = {}
                try:
                    import json as _json
                    _rs_raw = get_setting_value(db, "poster_renamer_stats")
                    if _rs_raw:
                        _rs = _json.loads(_rs_raw)
                    _ro_raw = get_setting_value(db, "poster_renamer_last_output")
                    if _ro_raw:
                        _ro = _json.loads(_ro_raw)
                except Exception as e:
                    log_debug(LogTags.WORKFLOW, f"Could not load poster_renamer stats/output from settings: {e}")
                results["jobs_run"].append({
                    "job": "rename_posters",
                    "success": child_ok,
                    "child_job_id": rename_child.id,
                    "embed_spec": {
                        "event_type": "success",
                        "title": "Poster Renamer Notification",
                        "description": (
                            f"Matched {int(_rs.get('total_matched', 0))} poster(s): "
                            f"movies={int(_rs.get('movies', 0))}, "
                            f"series={int(_rs.get('series', 0))}, "
                            f"collections={int(_rs.get('collections', 0))}"
                        ),
                        "fields": [
                            {
                                "name": f"Movies ({len(_ro.get('movies', []))})",
                                "value": _build_renamer_section(_ro.get("movies", [])),
                                "inline": False,
                            },
                            {
                                "name": f"Series ({len(_ro.get('series', []))})",
                                "value": _build_renamer_section(_ro.get("series", [])),
                                "inline": False,
                            },
                            {
                                "name": f"Collections ({len(_ro.get('collections', []))})",
                                "value": _build_renamer_section(_ro.get("collections", [])),
                                "inline": False,
                            },
                        ],
                        "color": 0x4CAF50,
                    },
                })

                if child_ok:
                    log_success(LogTags.WORKFLOW, "Step 3/6 complete: Rename finished", child_job_id=rename_child.id)
                    update_job_state(db, job, progress=65, message=format_workflow_step_complete(3, 6, "Rename finished"))
                else:
                    raise Exception(child_message or "Rename step failed")

            except Exception as e:
                error_msg = str(e)
                log_error(LogTags.WORKFLOW, f"Rename posters failed: {error_msg}\n{traceback.format_exc()}")
                results["jobs_failed"].append({"job": "rename_posters", "error": error_msg, "child_job_id": rename_child.id})

                if flow_config.get("rename_posters", {}).get("stop_on_error", True):
                    results["success"] = False
                    update_job_state(db, job, status=JOB_STATUS_FAILED, error=error_msg)
                    send_discord_notification(
                        db,
                        feature_key="workflow",
                        event_type="error",
                        title="Workflow Failed",
                        description=f"Renamer step failed: {error_msg}",
                        fields=[
                            {"name": "Job ID", "value": str(job_id), "inline": True},
                            {"name": "Step", "value": "Rename Posters", "inline": True},
                        ],
                        color=0xF44336,
                    )
                    send_major_error_notification(
                        db,
                        source="workflow.rename_posters",
                        message=error_msg,
                        job_id=job_id,
                    )
                    return results
        else:
            log_info(LogTags.WORKFLOW, "Step 3/6: Rename posters disabled - skipping")
            results["jobs_skipped"].append({"job": "rename_posters", "reason": "Disabled in configuration"})
            update_job_state(db, job, progress=65, message=format_workflow_step(3, 6, "Rename posters skipped"))

        # ── Step 4/6: Border Replacer ───────────────────────────────────────
        if flow_config.get("border_replacer", {}).get("enabled", False):
            log_step(LogTags.WORKFLOW, 4, 6, "Applying borders to posters")
            update_job_state(db, job, progress=65, message=format_workflow_step(4, 6, "Applying borders to posters..."))

            border_child = _create_child_job(db, JOB_TYPE_BORDER_REPLACER, "Workflow requested border replacer")
            try:
                border_mode = get_setting_value(db, "border_replacer_mode", "incremental")
                if border_mode not in ["full", "incremental"]:
                    border_mode = "incremental"

                _promote_child_progress_to_parent(
                    db,
                    job,
                    border_child.id,
                    60,
                    75,
                    format_workflow_step(3, 4, "Applying borders to posters..."),
                    partial(run_border_replacer_background_job, triggered_by="workflow"),
                    dry_run,
                    border_mode,
                )
                child_ok, child_message = _get_child_result(db, border_child.id)

                results["jobs_run"].append({
                    "job": "border_replacer",
                    "success": child_ok,
                    "child_job_id": border_child.id,
                    "mode": border_mode,
                    "embed_spec": {
                        "event_type": "success",
                        "title": "Border Replacer",
                        "description": child_message or "Border replacer completed",
                        "fields": [],
                        "color": 0x4CAF50,
                    },
                })

                if child_ok:
                    log_success(LogTags.WORKFLOW, "Step 4/6 complete: Border replacer finished", child_job_id=border_child.id)
                    update_job_state(db, job, progress=78, message=format_workflow_step_complete(4, 6, "Border replacer finished"))
                else:
                    raise Exception(child_message or "Border replacer step failed")

            except Exception as e:
                error_msg = str(e)
                log_error(LogTags.WORKFLOW, f"Border replacer failed: {error_msg}\n{traceback.format_exc()}")
                results["jobs_failed"].append({"job": "border_replacer", "error": error_msg, "child_job_id": border_child.id})

                if flow_config.get("border_replacer", {}).get("stop_on_error", False):
                    results["success"] = False
                    update_job_state(db, job, status=JOB_STATUS_FAILED, error=error_msg)
                    send_discord_notification(
                        db,
                        feature_key="workflow",
                        event_type="error",
                        title="Workflow Failed",
                        description=f"Border replacer step failed: {error_msg}",
                        fields=[
                            {"name": "Job ID", "value": str(job_id), "inline": True},
                            {"name": "Step", "value": "Border Replacer", "inline": True},
                        ],
                        color=0xF44336,
                    )
                    send_major_error_notification(
                        db,
                        source="workflow.border_replacer",
                        message=error_msg,
                        job_id=job_id,
                    )
                    return results
        else:
            log_info(LogTags.WORKFLOW, "Step 4/6: Border replacer disabled - skipping")
            results["jobs_skipped"].append({"job": "border_replacer", "reason": "Disabled in configuration"})
            update_job_state(db, job, progress=78, message=format_workflow_step(4, 6, "Border replacer skipped"))

        # ── Step 5/6: Plex Upload ─────────────────────────────────────────────
        if flow_config.get("plex_upload", {}).get("enabled", False):
            log_step(LogTags.WORKFLOW, 5, 6, "Uploading posters to Plex")
            update_job_state(db, job, progress=78, message=format_workflow_step(5, 6, "Uploading posters to Plex..."))

            plex_child = _create_child_job(db, JOB_TYPE_PLEX_UPLOAD, "Workflow requested Plex upload")
            try:
                remove_overlay_label_setting = get_setting(db, "plex_upload_manual_remove_overlay_label")
                remove_overlay_label = (
                    str(remove_overlay_label_setting.value).lower() == "true"
                    if remove_overlay_label_setting and remove_overlay_label_setting.value
                    else False
                )

                _promote_child_progress_to_parent(
                    db,
                    job,
                    plex_child.id,
                    78,
                    90,
                    format_workflow_step(5, 6, "Uploading posters to Plex..."),
                    run_plex_upload_background_job,
                    False,   # dry_run
                    False,   # reapply
                    remove_overlay_label,
                    True,    # skip_discord: sub-module notifications suppressed in workflow
                )
                child_ok, child_message = _get_child_result(db, plex_child.id)

                results["jobs_run"].append({
                    "job": "plex_upload",
                    "success": child_ok,
                    "child_job_id": plex_child.id,
                    "embed_spec": {
                        "event_type": "success",
                        "title": "Plex Upload",
                        "description": child_message or "Plex upload completed",
                        "fields": [],
                        "color": 0x4CAF50,
                    },
                })

                if child_ok:
                    log_success(LogTags.WORKFLOW, "Step 5/6 complete: Plex upload finished", child_job_id=plex_child.id)
                    update_job_state(db, job, progress=90, message=format_workflow_step_complete(5, 6, "Plex upload finished"))
                else:
                    raise Exception(child_message or "Plex upload step failed")

            except Exception as e:
                error_msg = str(e)
                log_error(LogTags.WORKFLOW, f"Plex upload failed: {error_msg}\n{traceback.format_exc()}")
                results["jobs_failed"].append({"job": "plex_upload", "error": error_msg, "child_job_id": plex_child.id})

                if flow_config.get("plex_upload", {}).get("stop_on_error", False):
                    results["success"] = False
                    update_job_state(db, job, status=JOB_STATUS_FAILED, error=error_msg)
                    send_discord_notification(
                        db,
                        feature_key="workflow",
                        event_type="error",
                        title="Workflow Failed",
                        description=f"Plex upload step failed: {error_msg}",
                        fields=[
                            {"name": "Job ID", "value": str(job_id), "inline": True},
                            {"name": "Step", "value": "Plex Upload", "inline": True},
                        ],
                        color=0xF44336,
                    )
                    send_major_error_notification(
                        db,
                        source="workflow.plex_upload",
                        message=error_msg,
                        job_id=job_id,
                    )
                    return results
        else:
            log_info(LogTags.WORKFLOW, "Step 5/6: Plex upload disabled - skipping")
            results["jobs_skipped"].append({"job": "plex_upload", "reason": "Disabled in configuration"})
            update_job_state(db, job, progress=90, message=format_workflow_step(5, 6, "Plex upload skipped"))

        # ── Step 6/6: Detect Unmatched ──────────────────────────────────────
        if flow_config.get("detect_unmatched", {}).get("enabled", False):
            log_step(LogTags.WORKFLOW, 6, 6, "Detecting unmatched assets")
            update_job_state(db, job, progress=90, message=format_workflow_step(6, 6, "Detecting unmatched assets..."))

            unmatched_child = _create_child_job(db, JOB_TYPE_UNMATCHED_DETECTION, "Workflow requested unmatched detection")
            try:
                _promote_child_progress_to_parent(
                    db,
                    job,
                    unmatched_child.id,
                    90,
                    99,
                    format_workflow_step(6, 6, "Detecting unmatched assets..."),
                    partial(run_unmatched_detection_background_job, triggered_by="workflow"),
                    True,  # skip_discord: sub-module notifications suppressed in workflow
                )
                child_ok, child_message = _get_child_result(db, unmatched_child.id)

                _us: dict = {}
                try:
                    import json as _json2
                    _us_raw = get_setting_value(db, "unmatched_last_stats")
                    if _us_raw:
                        _us = _json2.loads(_us_raw)
                except Exception as e:
                    log_debug(LogTags.WORKFLOW, f"Could not load unmatched_last_stats from settings: {e}")
                _uc = int(_us.get("unmatched_count", 0))
                results["jobs_run"].append({
                    "job": "detect_unmatched",
                    "success": child_ok,
                    "child_job_id": unmatched_child.id,
                    "embed_spec": {
                        "event_type": "success",
                        "title": "Unmatched Assets Summary",
                        "description": f"{_uc:,} total assets missing posters",
                        "fields": [
                            {"name": "Total", "value": str(_uc), "inline": True},
                            {"name": "Movies", "value": str(int(_us.get("movies_missing", 0))), "inline": True},
                            {"name": "Shows", "value": str(int(_us.get("shows_missing", 0))), "inline": True},
                            {"name": "Seasons", "value": str(int(_us.get("seasons_missing", 0))), "inline": True},
                            {"name": "Collections", "value": str(int(_us.get("collections_missing", 0))), "inline": True},
                        ],
                        "color": 0x4CAF50,
                    },
                })

                if child_ok:
                    log_success(LogTags.WORKFLOW, "Step 6/6 complete: Unmatched detection finished", child_job_id=unmatched_child.id)
                    update_job_state(db, job, progress=99, message=format_workflow_step_complete(6, 6, "Unmatched detection finished"))
                else:
                    raise Exception(child_message or "Unmatched step failed")

            except Exception as e:
                error_msg = str(e)
                log_error(LogTags.WORKFLOW, f"Detect unmatched failed: {error_msg}\n{traceback.format_exc()}")
                results["jobs_failed"].append({"job": "detect_unmatched", "error": error_msg, "child_job_id": unmatched_child.id})

                if flow_config.get("detect_unmatched", {}).get("stop_on_error", False):
                    results["success"] = False
                    update_job_state(db, job, status=JOB_STATUS_FAILED, error=error_msg)
                    send_discord_notification(
                        db,
                        feature_key="workflow",
                        event_type="error",
                        title="Workflow Failed",
                        description=f"Unmatched detection step failed: {error_msg}",
                        fields=[
                            {"name": "Job ID", "value": str(job_id), "inline": True},
                            {"name": "Step", "value": "Detect Unmatched", "inline": True},
                        ],
                        color=0xF44336,
                    )
                    send_major_error_notification(
                        db,
                        source="workflow.detect_unmatched",
                        message=error_msg,
                        job_id=job_id,
                    )
                    return results
        else:
            log_info(LogTags.WORKFLOW, "Step 6/6: Detect unmatched disabled - skipping")
            results["jobs_skipped"].append({"job": "detect_unmatched", "reason": "Disabled in configuration"})

        log_section_end(
            LogTags.WORKFLOW,
            f"Workflow completed - {len(results['jobs_run'])} jobs run, {len(results['jobs_skipped'])} skipped, {len(results['jobs_failed'])} failed",
        )
        update_job_state(
            db,
            job,
            status=JOB_STATUS_COMPLETED,
            progress=100,
            message=f"Workflow completed: {len(results['jobs_run'])} jobs run, {len(results['jobs_skipped'])} skipped, {len(results['jobs_failed'])} failed",
            completed_at=datetime.now(timezone.utc),
        )

        success = True
        results["job_id"] = job_id

        _step_labels = {
            "idarr": "IDarr Rename",
            "sync_drives": "Sync Drives",
            "rename_posters": "Rename Posters",
            "border_replacer": "Border Replacer",
            "detect_unmatched": "Detect Unmatched",
            "plex_upload": "Plex Upload",
        }

        def _fmt_simple_list(items: list, include_error: bool = False) -> str:
            if not items:
                return "None"
            lines = []
            for item in items:
                label = _step_labels.get(item.get("job", ""), item.get("job", ""))
                if include_error and item.get("error"):
                    snippet = str(item["error"])[:80]
                    lines.append(f"• {label}: {snippet}")
                else:
                    lines.append(f"• {label}")
            return "\n".join(lines)

        # Build workflow header embed
        workflow_header_fields = [
            {"name": "Job ID", "value": str(job_id), "inline": True},
            {"name": "Mode", "value": "Dry Run" if dry_run else "Live", "inline": True},
        ]
        if results["jobs_skipped"]:
            workflow_header_fields.append({
                "name": "Skipped",
                "value": _fmt_simple_list(results["jobs_skipped"]),
                "inline": True,
            })
        if results["jobs_failed"]:
            workflow_header_fields.append({
                "name": "Failed",
                "value": _fmt_simple_list(results["jobs_failed"], include_error=True),
                "inline": False,
            })

        all_embeds = [
            {
                "event_type": "success",
                "title": "Workflow Completed",
                "description": (
                    f"Workflow finished: {len(results['jobs_run'])} run, "
                    f"{len(results['jobs_skipped'])} skipped, {len(results['jobs_failed'])} failed"
                ),
                "fields": workflow_header_fields,
                "color": 0x4CAF50,
            }
        ]

        # Append one embed per completed step, formatted exactly like standalone notifications
        for _run_item in results["jobs_run"]:
            spec = _run_item.get("embed_spec")
            if spec:
                all_embeds.append(spec)

        send_discord_workflow_summary(db, embeds=all_embeds)

        return results

    except Exception as e:
        log_error(LogTags.WORKFLOW, f"Workflow execution failed: {e}\n{traceback.format_exc()}")
        send_discord_notification(
            db,
            feature_key="workflow",
            event_type="error",
            title="Workflow Failed",
            description=str(e),
            fields=[{"name": "Job ID", "value": str(job_id), "inline": True}],
            color=0xF44336,
        )
        send_major_error_notification(
            db,
            source="workflow",
            message=str(e),
            job_id=job_id,
        )
        try:
            mark_job_failed(db, job_id, e)
        except Exception as commit_error:
            log_error(LogTags.WORKFLOW, f"Failed to update job status: {commit_error}\n{traceback.format_exc()}")
            db.rollback()
    finally:
        run_post_job_hook(HOOK_KEY_WORKFLOW, success=success, triggered_by=triggered_by, db=db)
        remove_job_log_handler(workflow_handler_id, "workflow", success=success)
        db.close()
        if on_finish:
            on_finish()
