import json
import os
import shutil
import traceback
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from database import SessionLocal
from core.logging import (
    LogTags,
    log_info,
    log_warning,
    log_error,
    log_success,
    log_section_start,
    log_section_end,
    add_job_log_handler,
    remove_job_log_handler,
)
from models.setting import get_setting, get_setting_value, upsert_setting
from models.drive import Drive
from models.poster import Poster
from models.job import (
    Job,
    JOB_STATUS_RUNNING,
    JOB_STATUS_COMPLETED,
    format_start_message,
    format_complete_message,
    mark_job_failed,
    update_job_state,
)
from services.poster_renamer import PosterRenameService
from services.discord_notifications import send_discord_notification, send_major_error_notification
from core.hooks import run_post_job_hook, HOOK_KEY_RENAMER


def _extract_target_name(rename_message: str) -> str:
    marker = " -renamed-> "
    if marker in rename_message:
        return rename_message.split(marker, 1)[1].strip()
    return str(rename_message or "").strip()


def _build_renamer_section(items: list[dict[str, Any]], *, cap: int = 8) -> str:
    if not items:
        return "-"

    # Discord field value limit is 1024 chars
    BUDGET = 1000
    blocks: list[str] = []
    used = 0
    shown = 0

    for item in items[:cap]:
        title = str(item.get("title") or "Unknown")
        year = item.get("year")
        heading = f"{title} ({year})" if year else title
        messages = item.get("messages", []) or []
        all_file_lines = []
        for message in messages:
            target_name = _extract_target_name(str(message))
            if target_name:
                all_file_lines.append(f"  {target_name}")
        if not all_file_lines:
            all_file_lines = ["  poster.jpg"]

        # Try to fit all files; if the full block won't fit in the remaining budget,
        # trim file lines one at a time until it fits (or left with just the heading).
        file_lines = list(all_file_lines)
        trimmed = 0
        while file_lines:
            suffix = [f"  ...+{trimmed} more files"] if trimmed else []
            block = "\n".join([heading, *file_lines, *suffix])
            block_cost = len(block) + 2  # +2 for \n\n separator
            if used + block_cost <= BUDGET:
                break
            file_lines.pop()
            trimmed += 1
        else:
            # Even heading alone doesn't fit — stop here
            remaining = len(items) - shown
            blocks.append(f"...and {remaining} more")
            break

        suffix = [f"  ...+{trimmed} more files"] if trimmed else []
        block = "\n".join([heading, *file_lines, *suffix])
        block_cost = len(block) + 2

        if used + block_cost > BUDGET:
            remaining = len(items) - shown
            blocks.append(f"...and {remaining} more")
            break

        blocks.append(block)
        used += block_cost
        shown += 1

    if shown == cap and len(items) > cap:
        blocks.append(f"...and {len(items) - cap} more")

    return "```\n" + "\n\n".join(blocks) + "\n```"


def _build_progress_callback(
    db: Session,
    job: Job,
) -> Callable[[str, int, int, str], None]:
    """Create a standard progress callback for rename jobs."""
    def rename_progress(phase: str, current: int, total: int, message: str) -> None:
        if total > 0:
            progress = int((current / total) * 100)
            target_progress = min(max(progress, 0), 99)
            current_progress = int(job.progress or 0)
            next_progress = max(current_progress, target_progress)
            current_message = str(job.message or "")

            if next_progress != current_progress or message != current_message:
                update_job_state(db, job, progress=next_progress, message=message)

    return rename_progress


def run_rename_background_job(job_id: int, config_data: dict[str, Any], skip_discord: bool = False, triggered_by: str = "manual") -> None:
    """
    Execute Poster Renamer in a background thread.
    Shared orchestration used by poster manager entrypoints.
    Args:
        skip_discord: When True, suppresses individual Discord notifications (e.g. when called from workflow).
    """
    db = SessionLocal()

    handler_id = add_job_log_handler("poster_renamer", job_id, "Poster Renamer")
    success = False

    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log_error(LogTags.POSTER_RENAMER, f"Job {job_id} not found in database")
            return

        update_job_state(db, job, status=JOB_STATUS_RUNNING, message=format_start_message("poster renamer"))

        log_section_start(LogTags.POSTER_RENAMER, f"Background Poster Renamer Job {job_id}")

        dest_dir = config_data.get("destination") or config_data.get("destination_dir")
        if not dest_dir:
            raise ValueError("Destination directory not specified")

        priority_setting = get_setting(db, "poster_drive_priority")

        if not priority_setting:
            log_warning(LogTags.POSTER_RENAMER, "No drive priority configured (background task)")
            raise ValueError("No drive priority configured. Please configure drive priority in Poster Manager → Drive Priority tab.")

        try:
            priority_data = json.loads(priority_setting.value)
            drive_ids = priority_data.get("drive_ids", [])

            if not drive_ids:
                log_warning(LogTags.POSTER_RENAMER, "Drive priority list is empty (background task)")
                raise ValueError("Drive priority list is empty. Please add drives to the priority list in Poster Manager → Drive Priority tab.")

            drives_to_use = []
            for drive_id in drive_ids:
                drive = db.query(Drive).filter(Drive.id == drive_id, Drive.subscribed == True).first()
                if drive:
                    drives_to_use.append(drive)

            if not drives_to_use:
                log_warning(LogTags.POSTER_RENAMER, "No drives in priority list are subscribed")
                raise ValueError("No drives in priority list are subscribed. Please subscribe to drives in the GDrives page and add them to Poster Manager → Drive Priority tab.")

            source_dirs = [str(drive.get_local_path()) for drive in drives_to_use]

            drive_names = [d.name for d in drives_to_use]
            drives_per_line = 3
            drive_lines = []
            for i in range(0, len(drive_names), drives_per_line):
                chunk = drive_names[i:i + drives_per_line]
                drive_lines.append("    • " + ", ".join(chunk))

            log_info(LogTags.POSTER_RENAMER, f"Using {len(drives_to_use)} drives from priority list:")
            for line in drive_lines:
                log_info(LogTags.POSTER_RENAMER, line)

        except json.JSONDecodeError:
            log_error(LogTags.POSTER_RENAMER, "Failed to parse drive priority data")
            raise ValueError("Drive priority configuration is invalid. Please reconfigure in Poster Manager → Drive Priority tab.")

        use_temp_folder = True
        log_info(LogTags.POSTER_RENAMER, "Using tmp/ staging for consistent workflow")

        service = PosterRenameService(db)
        result = service.rename_posters(
            source_dirs=source_dirs,
            destination_dir=dest_dir,
            action_type=config_data.get("action_type") or "copy",
            asset_folders=config_data.get("asset_folders", True),
            dry_run=config_data.get("dry_run", False),
            use_temp_folder=use_temp_folder,
            progress_callback=_build_progress_callback(db, job),
        )

        if not result["success"]:
            raise Exception(result.get("error", "Unknown error"))

        if result.get("stats") and not config_data.get("dry_run", False):
            stats = result["stats"]
            style_counts = stats.get("style_counts", {})
            stats_json = json.dumps(stats)
            upsert_setting(db, "poster_renamer_stats", stats_json)
            db.commit()
            log_info(LogTags.POSTER_RENAMER, f"Stored stats: matched={stats.get('total_matched', 0)}, movies={stats.get('movies', 0)}, series={stats.get('series', 0)}, collections={stats.get('collections', 0)}, styles={style_counts}")

        auto_run_border_override = config_data.get("auto_run_border")
        if isinstance(auto_run_border_override, bool):
            auto_run_border_enabled = auto_run_border_override
        elif isinstance(auto_run_border_override, str):
            auto_run_border_enabled = auto_run_border_override.strip().lower() == "true"
        else:
            auto_run_border_enabled = get_setting_value(db, "auto_run_border", "false").lower() == "true"

        skip_border_post_processing = bool(config_data.get("skip_border_post_processing", False))

        border_ran_successfully = False

        if not skip_border_post_processing and auto_run_border_enabled:
            try:
                log_section_start(LogTags.BORDER_REPLACER, "Auto-Running Border Replacer")
                update_job_state(db, job, message="Running border replacer...")

                border_colors_value = get_setting_value(db, "border_replacer_colors")
                border_width_value = get_setting_value(db, "border_replacer_width")
                remove_borders_value = get_setting_value(db, "border_replacer_remove_borders", "false")

                border_colors = []
                if border_colors_value:
                    try:
                        border_colors = json.loads(border_colors_value)
                    except json.JSONDecodeError:
                        log_warning(LogTags.BORDER_REPLACER, "Failed to parse border colors, using empty list")
                        border_colors = []

                border_width = 26
                if border_width_value:
                    try:
                        border_width = int(border_width_value)
                    except ValueError:
                        log_warning(LogTags.BORDER_REPLACER, f"Invalid border width '{border_width_value}', using default 26")

                remove_borders = str(remove_borders_value).lower() == "true"

                border_mode = get_setting_value(db, "border_replacer_mode", "incremental")

                exclusions_value = get_setting_value(db, "border_replacer_exclusions")
                exclusions = []
                if exclusions_value:
                    try:
                        exclusions = json.loads(exclusions_value)
                    except json.JSONDecodeError:
                        log_warning(LogTags.BORDER_REPLACER, "Failed to parse exclusions, using empty list")

                action = "Removing borders" if remove_borders else "Adding borders"
                log_info(
                    LogTags.BORDER_REPLACER,
                    f"{action}, mode: {border_mode}, width: {border_width}px, exclusions: {len(exclusions)}",
                    colors=len(border_colors) if border_colors else 0,
                    width=border_width,
                    mode=border_mode,
                    exclusions=len(exclusions)
                )

                from services.border_replacer import BorderReplacerService
                border_service = BorderReplacerService(db)

                source_dir = os.path.join(dest_dir, "tmp")
                destination_dir = dest_dir

                if os.path.exists(source_dir):
                    border_result = border_service.process_posters(
                        source_dir=source_dir,
                        destination_dir=destination_dir,
                        border_colors=border_colors if border_colors else None,
                        remove_borders=remove_borders,
                        border_width=border_width,
                        exclusion_list=exclusions,
                        dry_run=config_data.get("dry_run", False),
                        mode=border_mode
                    )

                    if border_result.get("success"):
                        changed = border_result.get("changed", 0)
                        skipped = border_result.get("skipped", 0)
                        log_success(
                            LogTags.BORDER_REPLACER,
                            f"Auto-run complete: {changed} changed, {skipped} skipped",
                            changed=changed,
                            skipped=skipped
                        )
                        border_ran_successfully = True
                    else:
                        log_warning(
                            LogTags.BORDER_REPLACER,
                            f"Auto-run completed with warnings: {border_result.get('error', 'Unknown error')} — falling back to plain copy"
                        )
                else:
                    log_warning(LogTags.BORDER_REPLACER, f"Tmp directory not found: {source_dir}")

            except Exception as e:
                log_error(
                    LogTags.BORDER_REPLACER,
                    f"Auto-run border replacer failed: {e}\n{traceback.format_exc()} — falling back to plain copy",
                    error=str(e)
                )

        # Copy from tmp/ to final destination when:
        #   - Border replacer is disabled (normal no-border path)
        #   - Border replacer was enabled but failed (fallback to ensure files always land in destination)
        if not skip_border_post_processing and not border_ran_successfully:
            if not config_data.get("dry_run", False):
                try:
                    import filecmp
                    tmp_dir = os.path.join(dest_dir, "tmp")

                    if os.path.exists(tmp_dir):
                        if auto_run_border_enabled:
                            log_info(LogTags.POSTER_RENAMER, "Border replacer failed — falling back to plain copy from tmp/ to final destination")
                        else:
                            log_info(LogTags.POSTER_RENAMER, "Border replacer disabled - copying changed files to final destination")
                        update_job_state(db, job, message="Copying files to final destination...")

                        copied_count = 0
                        skipped_count = 0

                        for item in os.listdir(tmp_dir):
                            src_path = os.path.join(tmp_dir, item)
                            dest_path = os.path.join(dest_dir, item)

                            if os.path.isdir(src_path):
                                os.makedirs(dest_path, exist_ok=True)

                                for root, dirs, files in os.walk(src_path):
                                    rel_dir = os.path.relpath(root, src_path)
                                    dest_root = os.path.join(dest_path, rel_dir) if rel_dir != "." else dest_path

                                    for dir_name in dirs:
                                        os.makedirs(os.path.join(dest_root, dir_name), exist_ok=True)

                                    for file_name in files:
                                        src_file = os.path.join(root, file_name)
                                        dest_file = os.path.join(dest_root, file_name)

                                        if os.path.exists(dest_file) and filecmp.cmp(src_file, dest_file, shallow=False):
                                            skipped_count += 1
                                        else:
                                            shutil.copy2(src_file, dest_file)
                                            copied_count += 1

                            elif os.path.isfile(src_path):
                                if os.path.exists(dest_path) and filecmp.cmp(src_path, dest_path, shallow=False):
                                    skipped_count += 1
                                else:
                                    shutil.copy2(src_path, dest_path)
                                    copied_count += 1

                        log_success(
                            LogTags.POSTER_RENAMER,
                            f"Copy complete: {copied_count} changed, {skipped_count} unchanged",
                            copied=copied_count,
                            skipped=skipped_count
                        )

                        try:
                            dest_pattern = dest_dir.rstrip('/') + '/%'
                            reset_count = db.query(Poster).filter(
                                Poster.file_path.like(dest_pattern)
                            ).update({"file_mtime": 0}, synchronize_session=False)
                            db.commit()

                            if reset_count > 0:
                                log_info(
                                    LogTags.POSTER_RENAMER,
                                    f"Reset border replacer tracking for {reset_count} file(s)",
                                    count=reset_count
                                )
                        except Exception as db_err:
                            log_warning(
                                LogTags.POSTER_RENAMER,
                                f"Failed to reset border replacer tracking: {db_err}",
                                error=str(db_err)
                            )
                            db.rollback()

                except Exception as e:
                    log_warning(
                        LogTags.POSTER_RENAMER,
                        f"Failed to copy from tmp/ to final: {e}",
                        error=str(e)
                    )

        stats = result.get("stats", {})
        update_job_state(
            db,
            job,
            status=JOB_STATUS_COMPLETED,
            progress=100,
            message=format_complete_message("Poster Renamer", f"{stats.get('total_matched', 0)} posters organized"),
            completed_at=datetime.now(timezone.utc),
        )

        success = True
        output = result.get("output", {}) if isinstance(result.get("output"), dict) else {}
        # Persist output so the workflow notification can include the renamed poster list
        try:
            upsert_setting(db, "poster_renamer_last_output", json.dumps(output))
            db.commit()
        except Exception:
            pass
        if not skip_discord:
            send_discord_notification(
                db,
                feature_key="poster_renamer",
                event_type="success",
                title="Poster Renamer Notification",
                description=(
                    f"Matched {stats.get('total_matched', 0)} poster(s): "
                    f"movies={stats.get('movies', 0)}, series={stats.get('series', 0)}, collections={stats.get('collections', 0)}"
                ),
                fields=[
                    {
                        "name": f"Movies ({len(output.get('movies', []))})",
                        "value": _build_renamer_section(output.get("movies", [])),
                        "inline": False,
                    },
                    {
                        "name": f"Series ({len(output.get('series', []))})",
                        "value": _build_renamer_section(output.get("series", [])),
                        "inline": False,
                    },
                    {
                        "name": f"Collections ({len(output.get('collections', []))})",
                        "value": _build_renamer_section(output.get("collections", [])),
                        "inline": False,
                    },
                ],
                color=0x4CAF50,
            )
        log_section_end(LogTags.POSTER_RENAMER, "Background Poster Renamer Complete")

    except Exception as e:
        log_error(LogTags.POSTER_RENAMER, f"Background Poster Renamer failed: {e}\n{traceback.format_exc()}")
        if not skip_discord:
            send_discord_notification(
                db,
                feature_key="poster_renamer",
                event_type="error",
                title="Poster Renamer Failed",
                description=str(e),
                fields=[{"name": "Job ID", "value": str(job_id), "inline": True}],
                color=0xF44336,
            )
            send_major_error_notification(
                db,
                source="poster_renamer",
                message=str(e),
                job_id=job_id,
            )
        try:
            mark_job_failed(db, job_id, e)
        except Exception as commit_error:
            log_error(LogTags.POSTER_RENAMER, f"Failed to update job status: {commit_error}\n{traceback.format_exc()}")
            db.rollback()
    finally:
        run_post_job_hook(HOOK_KEY_RENAMER, success=success, triggered_by=triggered_by, db=db)
        remove_job_log_handler(handler_id, "poster_renamer", success=success)
        db.close()
