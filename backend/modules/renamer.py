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
    log_phase,
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
from core.job_cancel import JobCancelled, check_cancelled
from models.job import (
    Job,
    JOB_STATUS_RUNNING,
    JOB_STATUS_COMPLETED,
    format_start_message,
    format_complete_message,
    mark_job_failed,
    update_job_state,
    finalize_job_cancelled,
)
from services.poster_renamer import PosterRenameService
from services.discord_notifications import send_discord_notification, send_major_error_notification
from services.community_reconcile import reconcile_community_lists
from core.hooks import run_post_job_hook, HOOK_KEY_RENAMER
from util.poster_settings import ASSET_TYPES, get_asset_include, get_poster_destination
from util.posters.scanner import is_artwork_file


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
        check_cancelled(job.id)
        if total > 0:
            progress = int((current / total) * 100)
            target_progress = min(max(progress, 0), 99)
            current_progress = int(job.progress or 0)
            next_progress = max(current_progress, target_progress)
            current_message = str(job.message or "")

            if next_progress != current_progress or message != current_message:
                update_job_state(db, job, progress=next_progress, message=message)

    return rename_progress


def _prune_tmp_staging(tmp_dir: str) -> tuple[int, int, int]:
    """Remove dangling symlinks, stray artwork, and folders left empty by their removal
    from tmp/.

    A staged symlink goes dead when its gdrive source poster is removed/renamed. Those
    stale links accumulate forever (rename only touches matched items, cleanup only
    removes orphans), so prune them here. Artwork (logo/background/square) is never staged
    — it's placed straight to the real destination — so any artwork file in tmp/ is residue
    the tmp→destination copy must not spread. Valid poster entries are untouched so their
    mtimes stay intact for the stat-based "needs new poster?" short-circuit.

    Returns:
        Tuple of (removed_links, removed_artwork, removed_dirs).
    """
    removed_links = 0
    removed_artwork = 0
    removed_dirs = 0
    if not os.path.isdir(tmp_dir):
        return (0, 0, 0)

    # Bottom-up so a folder emptied by link removal is seen as empty when we reach it.
    for root, _dirs, files in os.walk(tmp_dir, topdown=False):
        for name in files:
            path = os.path.join(root, name)
            if os.path.islink(path) and not os.path.exists(path):
                try:
                    os.remove(path)
                    removed_links += 1
                except OSError as exc:
                    log_warning(LogTags.RENAMER, f"Could not remove dead symlink {path}: {exc}", path=path, error=str(exc))
            elif is_artwork_file(name):
                try:
                    os.remove(path)
                    removed_artwork += 1
                except OSError as exc:
                    log_warning(LogTags.RENAMER, f"Could not remove stray artwork {path}: {exc}", path=path, error=str(exc))
        if root != tmp_dir:
            try:
                if not os.listdir(root):
                    os.rmdir(root)
                    removed_dirs += 1
            except OSError:
                pass
    return (removed_links, removed_artwork, removed_dirs)


def _run_poster_pass(db, job, config_data, media_dict, artwork_boxes=None, artwork_slot_filter=None):
    """The poster rename -> border -> tmp-copy pipeline (unchanged from the standalone
    job). Uses the injected media_dict so the Asset Renamer fetches media only once, and the
    injected artwork_boxes, which merge into the poster asset list so ONE match covers an
    item's poster and its logo/background/square. Returns the rename_posters result."""
    dest_dir = config_data.get("destination") or config_data.get("destination_dir")
    if not dest_dir:
        raise ValueError("Destination directory not specified")

    log_phase(LogTags.RENAMER, "Gathering posters from the poster drives")

    priority_setting = get_setting(db, "poster_drive_priority")

    if not priority_setting:
        log_warning(LogTags.RENAMER, "No drive priority configured (background task)")
        raise ValueError("No drive priority configured. Please configure drive priority in Asset Manager → Drive Priority tab.")

    try:
        priority_data = json.loads(priority_setting.value)
        drive_ids = priority_data.get("drive_ids", [])

        if not drive_ids:
            log_warning(LogTags.RENAMER, "Drive priority list is empty (background task)")
            raise ValueError("Drive priority list is empty. Please add drives to the priority list in Asset Manager → Drive Priority tab.")

        drives_to_use = []
        for drive_id in drive_ids:
            drive = db.query(Drive).filter(Drive.id == drive_id, Drive.subscribed == True).first()
            if drive:
                drives_to_use.append(drive)

        if not drives_to_use:
            log_warning(LogTags.RENAMER, "No drives in priority list are subscribed")
            raise ValueError("No drives in priority list are subscribed. Please subscribe to drives in the GDrives page and add them to Asset Manager → Drive Priority tab.")

        source_dirs = [str(drive.get_local_path()) for drive in drives_to_use]

        drive_names = [d.name for d in drives_to_use]
        drives_per_line = 3
        drive_lines = []
        for i in range(0, len(drive_names), drives_per_line):
            chunk = drive_names[i:i + drives_per_line]
            drive_lines.append("    • " + ", ".join(chunk))

        log_info(LogTags.RENAMER, f"Using {len(drives_to_use)} drives from priority list:")
        for line in drive_lines:
            log_info(LogTags.RENAMER, line)

    except json.JSONDecodeError:
        log_error(LogTags.RENAMER, "Failed to parse drive priority data")
        raise ValueError("Drive priority configuration is invalid. Please reconfigure in Asset Manager → Drive Priority tab.")

    use_temp_folder = True
    log_info(LogTags.RENAMER, "Using tmp/ staging for consistent workflow")

    service = PosterRenameService(db)
    result = service.rename_posters(
        source_dirs=source_dirs,
        destination_dir=dest_dir,
        action_type=config_data.get("action_type") or "copy",
        asset_folders=config_data.get("asset_folders", True),
        dry_run=config_data.get("dry_run", False),
        use_temp_folder=use_temp_folder,
        library_setting_key=config_data.get("library_setting_key") or "poster_renamer_libraries",
        progress_callback=_build_progress_callback(db, job),
        media_dict=media_dict,
        artwork_boxes=artwork_boxes,
        artwork_slot_filter=artwork_slot_filter,
    )

    if not result["success"]:
        raise Exception(result.get("error", "Unknown error"))

    # A missing drive folder (unmounted / never synced) scans as empty, so its items would
    # read as unsourced and cleanup would prune their placed posters. Hand cleanup an empty
    # map instead — "prune nothing", same as the standalone derivation's OSError path.
    missing_dirs = [d for d in source_dirs if not os.path.isdir(d)]
    if missing_dirs:
        log_warning(
            LogTags.RENAMER,
            f"{len(missing_dirs)} poster drive folder(s) missing — skipping poster reconciliation for cleanup",
            missing=missing_dirs,
        )
        result["poster_sourced"] = {}

    if result.get("stats") and not config_data.get("dry_run", False):
        stats = result["stats"]
        style_counts = stats.get("style_counts", {})
        stats_json = json.dumps(stats)
        upsert_setting(db, "poster_renamer_stats", stats_json)
        db.commit()
        log_info(LogTags.RENAMER, f"Stored stats: matched={stats.get('total_matched', 0)}, movies={stats.get('movies', 0)}, series={stats.get('series', 0)}, collections={stats.get('collections', 0)}, styles={style_counts}")

        # Headless cross-sync: the style-fallback set is now fresh, so drop any
        # of this user's published list items whose preferred-style poster now
        # exists locally (resolved outside the request/list path). Best-effort.
        reconcile_community_lists(db, {"style_fallback"})

    auto_run_border_override = config_data.get("auto_run_border")
    if isinstance(auto_run_border_override, bool):
        auto_run_border_enabled = auto_run_border_override
    elif isinstance(auto_run_border_override, str):
        auto_run_border_enabled = auto_run_border_override.strip().lower() == "true"
    else:
        auto_run_border_enabled = get_setting_value(db, "auto_run_border", "false").lower() == "true"

    skip_border_post_processing = bool(config_data.get("skip_border_post_processing", False))

    border_ran_successfully = False

    # Prune stale staging symlinks (source removed/renamed on the drive) and stray artwork
    # before border/copy runs over tmp/, so one dead link can't poison those steps and
    # cruft doesn't accumulate. Valid entries' mtimes are left intact.
    if not skip_border_post_processing and not config_data.get("dry_run", False):
        pruned_links, pruned_artwork, pruned_dirs = _prune_tmp_staging(os.path.join(dest_dir, "tmp"))
        if pruned_links or pruned_artwork or pruned_dirs:
            log_info(
                LogTags.RENAMER,
                f"Pruned {pruned_links} dead staging symlink(s), {pruned_artwork} stray artwork file(s) "
                f"and {pruned_dirs} empty folder(s) from tmp/",
                removed_links=pruned_links,
                removed_artwork=pruned_artwork,
                removed_dirs=pruned_dirs,
            )

    if not skip_border_post_processing and auto_run_border_enabled:
        try:
            log_section_start(LogTags.BORDER_REPLACER, "Auto-Running Border Replacer")
            update_job_state(db, job, message="Running border replacer...")

            border_mode = get_setting_value(db, "border_replacer_mode", "incremental")

            # Load ALL border settings identically to the standalone/workflow job so
            # the post-rename auto-run follows the same style + season config.
            from services.border_replacer import build_border_run_settings
            border_run_settings = build_border_run_settings(db, run_type="autorun")

            remove_borders = border_run_settings["remove_borders"]
            border_width = border_run_settings["border_width"]
            exclusions = border_run_settings["exclusion_list"]
            border_colors = border_run_settings["border_colors"] or []

            action = "Removing borders" if remove_borders else "Adding borders"
            log_info(
                LogTags.BORDER_REPLACER,
                f"{action}, mode: {border_mode}, width: {border_width}px, exclusions: {len(exclusions)}",
                colors=len(border_colors),
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
                    dry_run=config_data.get("dry_run", False),
                    mode=border_mode,
                    **border_run_settings,
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
                        log_info(LogTags.RENAMER, "Border replacer failed — falling back to plain copy from tmp/ to final destination")
                    else:
                        log_info(LogTags.RENAMER, "Border replacer disabled - copying changed files to final destination")
                    update_job_state(db, job, message="Copying files to final destination...")

                    copied_count = 0
                    skipped_count = 0
                    failed_count = 0
                    skipped_artwork = 0

                    # Copy a single staged file to its destination. Isolated so one
                    # bad file (e.g. a dangling symlink whose gdrive source is gone)
                    # can't abort the whole sync and leave assets/ partially populated.
                    def _copy_one(src_file: str, dest_file: str) -> None:
                        nonlocal copied_count, skipped_count, failed_count
                        try:
                            # Dangling symlink (source missing) — skip, don't abort the loop
                            if os.path.islink(src_file) and not os.path.exists(src_file):
                                log_warning(
                                    LogTags.RENAMER,
                                    f"Skipping broken symlink in tmp/ (source missing): {src_file}",
                                    path=src_file,
                                )
                                failed_count += 1
                                return
                            if os.path.exists(dest_file) and filecmp.cmp(src_file, dest_file, shallow=False):
                                skipped_count += 1
                            else:
                                shutil.copy2(src_file, dest_file)
                                copied_count += 1
                        except Exception as copy_err:
                            log_warning(
                                LogTags.RENAMER,
                                f"Failed to copy {src_file} → {dest_file}: {copy_err}",
                                src=src_file,
                                dest=dest_file,
                                error=str(copy_err),
                            )
                            failed_count += 1

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
                                    # Artwork is never staged (it's placed straight to the real
                                    # destination), so any artwork in tmp/ is residue — don't spread it.
                                    if is_artwork_file(file_name):
                                        skipped_artwork += 1
                                        continue
                                    _copy_one(
                                        os.path.join(root, file_name),
                                        os.path.join(dest_root, file_name),
                                    )

                        elif os.path.isfile(src_path):
                            if is_artwork_file(item):
                                skipped_artwork += 1
                                continue
                            _copy_one(src_path, dest_path)

                    if skipped_artwork:
                        log_warning(
                            LogTags.RENAMER,
                            f"Skipped {skipped_artwork} artwork file(s) found in tmp/ staging — artwork is never staged, so these are residue and were not copied to the destination",
                            skipped_artwork=skipped_artwork,
                        )

                    if failed_count:
                        log_warning(
                            LogTags.RENAMER,
                            f"Copy complete: {copied_count} changed, {skipped_count} unchanged, {failed_count} failed/skipped",
                            copied=copied_count,
                            skipped=skipped_count,
                            failed=failed_count,
                        )
                    else:
                        log_success(
                            LogTags.RENAMER,
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
                                LogTags.RENAMER,
                                f"Reset border replacer tracking for {reset_count} file(s)",
                                count=reset_count
                            )
                    except Exception as db_err:
                        log_warning(
                            LogTags.RENAMER,
                            f"Failed to reset border replacer tracking: {db_err}",
                            error=str(db_err)
                        )
                        db.rollback()

            except Exception as e:
                log_warning(
                    LogTags.RENAMER,
                    f"Failed to copy from tmp/ to final: {e}",
                    error=str(e)
                )

    return result


def _resolve_asset_include(db, config_data):
    """Config `include` override, else the shared saved Include selection."""
    include = config_data.get("include")
    if include is None:
        return get_asset_include(db)
    return [t for t in include if t in ASSET_TYPES]


def _run_artwork_only_pass(db, job, config_data, media_dict, artwork_boxes, artwork_slot_filter=None):
    """Artwork-only run (posters unchecked): match the artwork boxes with the SAME matcher the
    poster pass uses, then place through the SAME rename_files engine — the asset list just
    happens to hold artwork instead of posters. Returns {total} for the completion message.
    artwork_boxes is None when no artwork drives are configured (soft-skip → nothing placed)."""
    dest = config_data.get("destination") or config_data.get("destination_dir") or get_poster_destination(db)
    action_type = config_data.get("action_type") or get_setting_value(db, "poster_action_type", "copy")
    asset_folders = config_data.get("asset_folders")
    if asset_folders is None:
        asset_folders = str(get_setting_value(db, "poster_asset_folders", "true")).lower() == "true"
    dry_run = bool(config_data.get("dry_run", False))
    if not dry_run:
        os.makedirs(dest, exist_ok=True)  # poster flow makes this; artwork-only must too

    progress = _build_progress_callback(db, job)

    matched: dict[str, Any] = {"collections": [], "movies": [], "series": []}
    if artwork_boxes:
        from util.posters.index import build_search_index, create_new_empty_index
        from util.posters.match import match_assets_to_media
        progress("matching", 40, 100, "Matching artwork to media...")
        index = create_new_empty_index()
        for box in artwork_boxes:
            build_search_index(index, box.get("title", ""), box)
        matched = match_assets_to_media(media_dict, index, label="artwork drives", report_near_misses=False)

    progress("renaming", 50, 100, "Renaming and organizing artwork...")

    def _rename_progress(current: int, total: int, message: str) -> None:
        progress("renaming", 50 + int((current / total) * 50), 100, message)

    *_, artwork_renamed = PosterRenameService(db).rename_files(
        matched, dest, action_type=action_type, asset_folders=bool(asset_folders),
        dry_run=dry_run, progress_callback=_rename_progress,
        artwork_slot_filter=artwork_slot_filter,
    )

    # Refresh just the artwork drive-usage keys in the stats blob (the poster pass owns the
    # rest) so an artwork-only run still updates the Drive Usage report.
    if not dry_run:
        from services.poster_renamer import build_artwork_drive_usage
        stats_setting = get_setting(db, "poster_renamer_stats")
        try:
            stored = json.loads(stats_setting.value) if stats_setting and stats_setting.value else {}
        except json.JSONDecodeError:
            stored = {}
        stored.update(build_artwork_drive_usage(db, artwork_renamed))
        upsert_setting(db, "poster_renamer_stats", json.dumps(stored))
        db.commit()

    # Files actually written this run (not resolved slots), so "placed" means placed.
    matched_slots = sum(len(it.get("_artwork_slots") or {}) for at in matched for it in matched[at])
    from services.artwork_scan import sourced_types_from_matched
    return {
        "success": True,
        "total": artwork_renamed["total"],
        "by_media": artwork_renamed["by_media"],
        "by_type": artwork_renamed["by_type"],
        "matched": matched_slots,
        "artwork_sourced": sourced_types_from_matched(matched),
    }


def _scan_artwork_boxes(db, artwork_types):
    """The UNFILTERED artwork drive scan, or None when no artwork types are selected or no
    artwork drives are configured (soft-skip — poster-only is unaffected).

    Unfiltered on purpose: Asset Cleanup reuses this scan to learn what the drives actually
    source, and it only prunes a type some item still provides. Handing it the Include-filtered
    view would make unchecking a type silently stop cleaning that type.
    """
    if not artwork_types:
        return None
    from services.artwork_scan import scan_artwork_drive_boxes, drives_in_priority_order
    if not drives_in_priority_order(db, require=False):
        log_info(LogTags.RENAMER, "No artwork drives configured - skipping artwork")
        return None
    return scan_artwork_drive_boxes(db)


def _artwork_slot_filter(artwork_types):
    """The Include selection as placement-time slot names (squareart -> square)."""
    from util.posters.scanner import ARTWORK_TYPE_TO_NAME
    return {ARTWORK_TYPE_TO_NAME[t] for t in artwork_types if t in ARTWORK_TYPE_TO_NAME}


def run_rename_background_job(
    job_id: int,
    config_data: dict[str, Any],
    skip_discord: bool = False,
    triggered_by: str = "manual",
    scan_out: dict[str, Any] | None = None,
) -> None:
    """
    Execute the Asset Renamer (posters + artwork) in a background thread.

    One engine: artwork (logo/background/square) rides the poster match+placement via an
    artwork index, so an item's poster and artwork are placed together; artwork-only runs use
    the same rename_files engine with no poster sources. Which asset types run comes from the
    Include selection (posters / logo / background / squareart). One media fetch, shared.

    skip_discord: When True, suppresses individual Discord notifications (e.g. from workflow).
    """
    db = SessionLocal()

    handler_id = add_job_log_handler("poster_renamer", job_id, "Asset Renamer")
    success = False

    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log_error(LogTags.RENAMER, f"Job {job_id} not found in database")
            return

        update_job_state(db, job, status=JOB_STATUS_RUNNING, message=format_start_message("asset renamer", dry_run=bool(config_data.get("dry_run", False))))

        log_section_start(LogTags.RENAMER, f"Background Asset Renamer Job {job_id}")

        include = _resolve_asset_include(db, config_data)
        do_posters = "posters" in include
        artwork_types = [t for t in ("logo", "background", "squareart") if t in include]
        if not do_posters and not artwork_types:
            log_warning(LogTags.RENAMER, "Include selection is empty — nothing to rename")
            update_job_state(db, job, status=JOB_STATUS_COMPLETED, progress=100, message="No asset types selected — nothing to rename")
            success = True
            return

        # One media fetch, shared by both passes.
        library_setting_key = config_data.get("library_setting_key") or "poster_renamer_libraries"
        service = PosterRenameService(db)
        log_phase(LogTags.RENAMER, "Fetching media from configured instances")
        update_job_state(db, job, message="Fetching media from configured instances...")
        media_dict = service.get_media_from_instances(setting_key=library_setting_key)
        if not any(media_dict.values()):
            raise Exception("No media found. Check Settings -> Media tab to configure Plex/Radarr/Sonarr.")
        update_job_state(db, job, progress=10)

        # Artwork boxes (logo/background/square) — matched and placed by the SAME engine
        # whether posters run or not. None when no artwork types / no artwork drives.
        # Scanned ONCE here and reused by the cleanup at this run's tail, which otherwise
        # re-scanned every artwork drive (duplicate I/O and duplicate per-item logging).
        if artwork_types:
            update_job_state(db, job, message="Scanning artwork drives...")
        scanned_artwork = _scan_artwork_boxes(db, artwork_types)
        if scanned_artwork:
            log_info(LogTags.RENAMER, f"Found {len(scanned_artwork)} artwork item(s) across the artwork drives")
        update_job_state(db, job, progress=15)
        # Matching sees the FULL slot set (so cleanup learns everything the drives source);
        # the Include selection filters at placement time instead.
        artwork_slot_filter = _artwork_slot_filter(artwork_types) if scanned_artwork else None

        poster_result = None
        if do_posters:
            # Artwork rides the poster pass (placed alongside each item's poster).
            poster_result = _run_poster_pass(db, job, config_data, media_dict, scanned_artwork, artwork_slot_filter)
        else:
            log_info(LogTags.RENAMER, "Posters not selected - skipping poster pass")

        # Artwork-only run (posters unchecked): same engine, no poster sources.
        artwork_result = None
        if artwork_types and not do_posters:
            artwork_result = _run_artwork_only_pass(db, job, config_data, media_dict, scanned_artwork, artwork_slot_filter)

        # The run's match verdicts: which artwork types the drives source per item. Cleanup
        # consumes this instead of re-matching the library, so place and prune can't disagree.
        artwork_sourced = None
        if scanned_artwork:
            artwork_sourced = (
                (poster_result or {}).get("artwork_sourced")
                if do_posters else (artwork_result or {}).get("artwork_sourced")
            )
        # Poster slots the drives source per item; None on artwork-only runs (no poster
        # scan happened) so cleanup derives it itself.
        poster_sourced = (poster_result or {}).get("poster_sourced") if do_posters else None

        # Hand the media fetch and the match verdicts to the caller. A workflow runs cleanup
        # itself (this job skips its own); without this it re-fetched media and re-matched.
        if scan_out is not None:
            scan_out["media_dict"] = media_dict
            scan_out["artwork_boxes"] = scanned_artwork
            scan_out["artwork_sourced"] = artwork_sourced
            scan_out["poster_sourced"] = poster_sourced

        # -- Asset cleanup (opt-in toggle, standalone runs only) --
        # Skipped for workflow children - the workflow runs cleanup at its own tail.
        cleanup_summary = None
        if triggered_by != "workflow":
            from modules.cleanup import maybe_run_asset_cleanup, summarize_cleanup
            cleanup_result = maybe_run_asset_cleanup(
                db,
                config_data=config_data,
                dry_run=config_data.get("dry_run", False),
                triggered_by=triggered_by,
                job=job,
                media_dict=media_dict,  # same media (+ library selection) the rename just used
                artwork_boxes=scanned_artwork,  # unfiltered scan from above; don't re-walk the drives
                artwork_sourced=artwork_sourced,  # this run's match verdicts; don't re-match
                poster_sourced=poster_sourced,
            )
            cleanup_summary = summarize_cleanup(cleanup_result)

        # Combined completion message covering whichever passes ran.
        p_stats = (poster_result or {}).get("stats", {}) or {}
        poster_matched = p_stats.get("total_matched", 0)
        # Artwork rides the poster pass when posters ran; else it came from the artwork-only pass.
        artwork_placed = p_stats.get("artwork", 0) if do_posters else (artwork_result or {}).get("total", 0)
        if do_posters:
            artwork_by_media = p_stats.get("artwork_by_media") or {}
            artwork_by_type = p_stats.get("artwork_by_type") or {}
        else:
            artwork_by_media = (artwork_result or {}).get("by_media") or {}
            artwork_by_type = (artwork_result or {}).get("by_type") or {}
        parts = []
        if do_posters:
            parts.append(f"{poster_matched} posters")
        if artwork_types:
            parts.append(f"{artwork_placed} artwork")
        details = (", ".join(parts) + " organized") if parts else "nothing selected to organize"
        update_job_state(
            db,
            job,
            status=JOB_STATUS_COMPLETED,
            progress=100,
            message=format_complete_message("Asset Renamer", details),
            completed_at=datetime.now(timezone.utc),
        )

        success = True

        # Persist poster output only when the poster pass ran.
        output: dict = {}
        if do_posters and poster_result:
            output = poster_result.get("output", {}) if isinstance(poster_result.get("output"), dict) else {}
            try:
                upsert_setting(db, "poster_renamer_last_output", json.dumps(output))
                db.commit()
            except Exception:  # nosec B110
                pass

        # Report whichever passes ran — an artwork-only rename notifies too.
        if not skip_discord and (do_posters or artwork_types):
            stats = (poster_result or {}).get("stats", {}) or {}
            desc_bits = []
            fields = []
            if do_posters:
                desc_bits.append(
                    f"Matched {stats.get('total_matched', 0)} poster(s): "
                    f"movies={stats.get('movies', 0)}, series={stats.get('series', 0)}, collections={stats.get('collections', 0)}"
                )
                fields.extend([
                    {"name": f"Movies ({len(output.get('movies', []))})", "value": _build_renamer_section(output.get("movies", [])), "inline": False},
                    {"name": f"Series ({len(output.get('series', []))})", "value": _build_renamer_section(output.get("series", [])), "inline": False},
                    {"name": f"Collections ({len(output.get('collections', []))})", "value": _build_renamer_section(output.get("collections", [])), "inline": False},
                ])
            if artwork_types:
                desc_bits.append(f"Placed {artwork_placed:,} artwork file(s)")
                # Break it down the way the poster fields do (per media type), plus per artwork type.
                media_bits = ", ".join(
                    f"{mt}={int(artwork_by_media.get(mt, 0)):,}" for mt in ("movies", "series", "collections")
                )
                type_bits = ", ".join(
                    f"{at}={int(artwork_by_type.get(at, 0)):,}" for at in ("logo", "background", "squareart")
                    if at in artwork_types
                )
                fields.append({
                    "name": f"Artwork ({artwork_placed:,})",
                    "value": (f"{media_bits}\n{type_bits}" if artwork_placed else "-"),
                    "inline": False,
                })
            if cleanup_summary:
                fields.append({"name": "Asset Cleanup", "value": cleanup_summary, "inline": False})
            send_discord_notification(
                db,
                feature_key="poster_renamer",
                event_type="success",
                title="Asset Renamer Notification",
                description=" · ".join(desc_bits),
                fields=fields,
                color=0x4CAF50,
            )
        log_section_end(LogTags.RENAMER, "Background Asset Renamer Complete")

    except JobCancelled:
        db.rollback()
        finalize_job_cancelled(db, job_id)
        log_section_end(LogTags.RENAMER, "Background Asset Renamer Stopped")
        raise
    except Exception as e:
        log_error(LogTags.RENAMER, f"Background Asset Renamer failed: {e}\n{traceback.format_exc()}")
        db.rollback()  # reset a possibly-poisoned session before reusing it
        if not skip_discord:
            send_discord_notification(
                db,
                feature_key="poster_renamer",
                event_type="error",
                title="Asset Renamer Failed",
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
            log_error(LogTags.RENAMER, f"Failed to update job status: {commit_error}\n{traceback.format_exc()}")
            db.rollback()
    finally:
        run_post_job_hook(HOOK_KEY_RENAMER, success=success, triggered_by=triggered_by, db=db)
        remove_job_log_handler(handler_id, "poster_renamer", success=success)
        db.close()
