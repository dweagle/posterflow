"""Tools page jobs: the registry of one-off maintenance tools and the background job that
runs one. A tool is a label, a readiness check and an execute function; the work itself
lives in services (PosterTools for the media server ones, ArrRenamer and ArrUpgrader for Sonarr/Radarr)."""

import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from database import SessionLocal
from core.job_cancel import JobCancelled, check_cancelled
from core.logging import (
    LogTags,
    add_job_log_handler,
    log_error,
    log_section_end,
    log_section_start,
    remove_job_log_handler,
)
from models.job import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_TYPE_TOOLS,
    Job,
    finalize_job_cancelled,
    format_complete_message,
    format_start_message,
    mark_job_failed,
    update_job_state,
)
from services.arr_renamer import ArrRenamer, arr_instance_names, load_arr_instances, normalize_one, tag_problem
from services.arr_upgrader import ArrUpgrader, normalize_one as normalize_upgrade, settings_problem as upgrade_problem
from services.plex_bundles import load_plex_metadata_paths
from services.poster_tools import IMAGE_KINDS, JELLYFIN_IMAGE_KINDS, KIND_LABELS, WRITE_ACCESS_HINT, PosterTools, ProgressCallback, instance_names
from services.tool_profiles import describe_profile, every_profile
from util.media_server.instances import server_type_label

Settings = Dict[str, Any]  # a profile's settings, in the shape the tool's normalize_settings gives
Execute = Callable[..., str]  # (db, progress, job_id, dry_run, settings, instance=None) → result detail; `instance` limits a per-instance tool to one


GROUPS = (("plex", "Plex"), ("jellyfin", "Jellyfin"), ("sonarr", "Sonarr"), ("radarr", "Radarr"))  # one card per app, in page order

# Tool job id → tool id, for every queued or running tool job: the page shows a tool as busy
# after a reload. A job row does not say which tool it runs, and stale jobs are failed at startup.
ACTIVE_TOOL_JOBS: Dict[int, str] = {}


def remember_tool_job(job_id: int, tool_id: str) -> None:
    ACTIVE_TOOL_JOBS[job_id] = tool_id


def active_tool_jobs(db: Session) -> Dict[str, int]:
    """{tool id: job id} for the tool jobs still pending or running."""
    if not ACTIVE_TOOL_JOBS:
        return {}
    live = {job.id for job in db.query(Job.id).filter(Job.id.in_(list(ACTIVE_TOOL_JOBS)), Job.status.in_((JOB_STATUS_PENDING, JOB_STATUS_RUNNING)))}
    for job_id in [j for j in ACTIVE_TOOL_JOBS if j not in live]:
        ACTIVE_TOOL_JOBS.pop(job_id, None)
    return {tool_id: job_id for job_id, tool_id in ACTIVE_TOOL_JOBS.items()}
ARR_GROUPS = ("sonarr", "radarr")


def group_instance_names(db: Session, group: str) -> List[str]:
    """The configured instances behind a card: media servers for plex/jellyfin, arrs otherwise."""
    return arr_instance_names(db, group) if group in ARR_GROUPS else instance_names(db, group)


def _has_servers(server_type: str) -> Callable[[Session], Tuple[bool, str]]:
    def ready(db: Session) -> Tuple[bool, str]:
        if not instance_names(db, server_type):
            return False, f"No {server_type_label(server_type)} server is configured."
        return True, ""
    return ready


def _has_arr(kind: str) -> Callable[[Session], Tuple[bool, str]]:
    def ready(db: Session) -> Tuple[bool, str]:
        if not arr_instance_names(db, kind):
            return False, f"No {kind.capitalize()} instance is configured."
        return True, ""
    return ready


def _plex_cleanup_ready(db: Session) -> Tuple[bool, str]:
    ok, reason = _has_servers("plex")(db)
    if not ok:
        return ok, reason
    if not load_plex_metadata_paths(db):
        return False, "Set the Plex Metadata folder for at least one server first."
    return True, ""


def _plex_cleanup(db: Session, progress: ProgressCallback, job_id: Optional[int], dry_run: bool, settings: Settings, instance: Optional[str] = None) -> str:
    stats = PosterTools(db).plex_cleanup(kinds=settings.get("kinds"), dry_run=dry_run, progress=progress, job_id=job_id)
    if stats["problems"] and len(stats["stopped"]) + len(stats["skipped"]) >= stats["instances"]:
        raise RuntimeError(" ".join(stats["problems"].values()))  # every server was given up on: the job failed
    if stats["unwritable"] and not stats["plex_pruned"]:
        raise RuntimeError(f"Nothing {'could be' if dry_run else 'was'} removed: {stats['unwritable']} bundle folder(s) are not writable. {WRITE_ACCESS_HINT}.")
    detail = f"{stats['plex_pruned']} superseded upload(s) {'found' if dry_run else 'removed'}, {stats['plex_freed_bytes'] / 1024 / 1024:.1f} MB"
    if stats["stopped"]:
        detail += f"; stopped for {', '.join(stats['stopped'])} (check the Metadata folder)"
    if stats["skipped"]:
        detail += f"; {', '.join(stats['skipped'])} skipped (not a Metadata folder)"
    if stats["unwritable"]:
        detail += f"; {stats['unwritable']} bundle folder(s) not writable (see the log)"
    return detail


def _reset_posters(server_type: str) -> Execute:
    def execute(db: Session, progress: ProgressCallback, job_id: Optional[int], dry_run: bool, settings: Settings, instance: Optional[str] = None) -> str:
        stats = PosterTools(db).reset_posters(server_type=server_type, kinds=settings.get("kinds"), dry_run=dry_run, progress=progress, job_id=job_id)
        return f"{stats['reset']} reset, {stats['failed']} failed"
    return execute


def _arr_instances(db: Session, kind: str, instance: Optional[str]) -> Optional[List[Dict[str, str]]]:
    """The instances a run covers: None = every configured one, or the one the run names."""
    if not instance:
        return None
    chosen = [i for i in load_arr_instances(db, kind) if (i.get("name") or i.get("url")) == instance]
    if not chosen:
        raise RuntimeError(f"{kind.capitalize()} instance '{instance}' is not configured")
    return chosen


def _arr_rename(kind: str) -> Execute:
    def execute(db: Session, progress: ProgressCallback, job_id: Optional[int], dry_run: bool, settings: Settings, instance: Optional[str] = None) -> str:
        renamer = ArrRenamer(db, kind, per_instance=settings.get("instances"), instances=_arr_instances(db, kind, instance))
        stats = renamer.run(dry_run=dry_run, progress=progress)  # cancels land through the progress callback
        verb = "would be renamed" if dry_run else "renamed"
        detail = f"{stats['files']} file(s) in {stats['items']} of {stats['checked']} item(s) {verb}"
        if stats["folders"]:
            detail += f", {stats['folders']} folder(s) {verb}"
        if stats["held"]:
            detail += f", {stats['held']} held back"
        if stats["later"]:
            detail += f", {stats['later']} left for later runs"
        if stats["deleted"]:
            detail += f", {stats['deleted']} deleted mid-run"
        if stats["missing"]:
            detail += f", {stats['missing']} not on disk"
        if stats["failed"]:
            detail += f", {stats['failed']} failed"
        if stats["stopped"]:
            detail += f", {stats['stopped']} instance(s) stopped"
        if stats["failed_instances"]:
            detail += f", {stats['failed_instances']} instance(s) failed"
        if stats["unreachable"]:
            detail += f", {stats['unreachable']} instance(s) unreachable"
        return detail
    return execute


def _arr_upgrade(kind: str) -> Execute:
    def execute(db: Session, progress: ProgressCallback, job_id: Optional[int], dry_run: bool, settings: Settings, instance: Optional[str] = None) -> str:
        upgrader = ArrUpgrader(db, kind, per_instance=settings.get("instances"), instances=_arr_instances(db, kind, instance))
        stats = upgrader.run(dry_run=dry_run, progress=progress)  # cancels land through the progress callback
        if dry_run:
            detail = f"{stats['searched']} item(s) would be searched"
        else:
            detail = f"{stats['searched']} item(s) searched, {stats['grabbed']} release(s) grabbed"
        if stats["held"]:
            detail += f", {stats['held']} held back"
        if stats["passed"]:
            detail += f", {stats['passed']} with nothing to search"
        if stats["failed"]:
            detail += f", {stats['failed']} failed"
        if stats["cycles"]:
            detail += f", {stats['cycles']} cycle(s) restarted"
        if stats["imported"]:
            detail += f", {stats['imported']} earlier grab(s) imported"
        if stats["not_imported"]:
            detail += f", {stats['not_imported']} earlier grab(s) not imported"
        if stats["stuck"]:
            detail += f", {stats['stuck']} stuck in the queue"
        if stats["skipped"]:
            detail += f", {stats['skipped']} instance(s) skipped as busy"
        if stats["stopped"]:
            detail += f", {stats['stopped']} instance(s) stopped"
        if stats["failed_instances"]:
            detail += f", {stats['failed_instances']} instance(s) failed"
        if stats["unreachable"]:
            detail += f", {stats['unreachable']} instance(s) unreachable"
        return detail
    return execute


def _jellyfin_clean_image_cache(db: Session, progress: ProgressCallback, job_id: Optional[int], dry_run: bool, settings: Settings, instance: Optional[str] = None) -> str:
    stats = PosterTools(db).clean_jellyfin_image_cache()
    return f"{stats['asked']} server(s) asked to clean their image cache" + (f", {stats['failed']} unreachable" if stats["failed"] else "")


# ── profile settings: what a tool's profiles hold ─────────────────────────────
# Each shape is three functions: the defaults, a normalizer that coerces whatever was stored or
# sent (raising ValueError for what no client should send), and the reason a set cannot be saved.

def _kinds_settings(all_kinds: Tuple[str, ...]) -> Tuple[Callable, Callable, Callable]:
    """An image tool: {"kinds": [...]} in the tool's order; every kind when none were ever chosen."""
    def default(db: Session) -> Settings:
        return {"kinds": list(all_kinds)}

    def normalize(db: Session, raw: Any) -> Settings:
        given = raw.get("kinds") if isinstance(raw, dict) else None
        if given is None:
            return default(db)
        if not isinstance(given, list):
            raise ValueError("kinds must be a list of image kinds")
        unknown = [str(k) for k in given if k not in all_kinds]
        if unknown:
            raise ValueError(f"Unknown image kind(s): {', '.join(unknown)}")
        return {"kinds": [k for k in all_kinds if k in given]}

    def problem(settings: Settings) -> str:
        return "" if settings.get("kinds") else "Switch on at least one image type."

    return default, normalize, problem


def _arr_settings(kind: str) -> Tuple[Callable, Callable, Callable]:
    """An arr renamer: the rename settings of one instance. Each instance keeps profiles of its
    own, and a run gets them as {"instances": {instance name: settings}}."""
    def default(db: Session) -> Settings:
        return normalize_one({}, kind)

    def normalize(db: Session, raw: Any) -> Settings:
        return normalize_one(raw, kind)

    def problem(settings: Settings) -> str:
        return tag_problem(settings)

    return default, normalize, problem


def _upgrade_settings(kind: str) -> Tuple[Callable, Callable, Callable]:
    """An arr upgrader: the search settings of one instance, kept per instance like the renamer's."""
    def default(db: Session) -> Settings:
        return normalize_upgrade({}, kind)

    def normalize(db: Session, raw: Any) -> Settings:
        return normalize_upgrade(raw, kind)

    return default, normalize, upgrade_problem


@dataclass(frozen=True)
class Tool:
    id: str
    label: str
    description: str
    action: str  # verb in the job's start message ("plex cleanup")
    execute: Execute
    group: str  # media server type the tool belongs to (see GROUPS)
    ready: Callable[[Session], Tuple[bool, str]]
    job_type: str = JOB_TYPE_TOOLS
    confirm: Optional[str] = None  # confirm button text for tools that ask first; None = runs straight away
    destructive: bool = True  # a tool that asks first changes or removes things; False = it asks, but its buttons are not red
    dry_run: bool = False  # offers a dry run
    kinds: Tuple[str, ...] = ()  # image kinds a profile can switch on and off; () = not applicable
    run_label: str = "Run"  # the main button
    per_instance: bool = False  # a run or a schedule may name one configured instance instead of all of them
    # The tool's settings, held in named profiles (see services.tool_profiles); all None = no settings, no profiles.
    default_settings: Optional[Callable[[Session], Settings]] = None
    normalize_settings: Optional[Callable[[Session, Any], Settings]] = None
    settings_problem: Optional[Callable[[Settings], str]] = None

    def instance_names(self, db: Session) -> List[str]:
        return group_instance_names(db, self.group)

    def describe(self, db: Session) -> Dict[str, Any]:
        available, reason = self.ready(db)
        return {
            "id": self.id, "label": self.label, "description": self.description, "job_type": self.job_type, "group": self.group,
            "confirm": self.confirm, "destructive": bool(self.confirm) and self.destructive, "dry_run": self.dry_run, "kinds": list(self.kinds), "run_label": self.run_label,
            "per_instance": self.per_instance, "instances": self.instance_names(db),
            "available": available, "reason": reason,
            "profiles": [describe_profile(db, self, row) for row in every_profile(db, self)],
            "defaults": self.default_settings(db) if self.default_settings else None,
        }

    def action_for(self, settings: Settings, profile_name: Optional[str] = None, instance: Optional[str] = None) -> str:
        """The verb for the job message: the profile that ran, or the image kinds when a run named
        them directly, and the one instance when the run was limited to it."""
        if profile_name:
            action = f"{self.action} ({profile_name})"
        else:
            kinds = settings.get("kinds") if self.kinds else None
            action = f"{self.action} ({', '.join(KIND_LABELS.get(k, k) for k in self.kinds if k in kinds)})" if kinds and set(kinds) < set(self.kinds) else self.action
        return f"{action} on {instance}" if instance else action


def _with_settings(shape: Tuple[Callable, Callable, Callable]) -> Dict[str, Callable]:
    default, normalize, problem = shape
    return {"default_settings": default, "normalize_settings": normalize, "settings_problem": problem}


TOOLS: Dict[str, Tool] = {tool.id: tool for tool in (
    Tool(
        id="plex_cleanup",
        label="Clean up Plex uploads",
        description="Plex keeps every poster, background, logo and square art ever uploaded to an item. This removes the superseded ones from each movie, show, season and collection bundle in every enabled library, one image kind or all of them. The images Plex shows stay.",
        action="plex cleanup",
        execute=_plex_cleanup,
        group="plex",
        ready=_plex_cleanup_ready,
        dry_run=True,
        kinds=IMAGE_KINDS,
        run_label="Clean",
        **_with_settings(_kinds_settings(IMAGE_KINDS)),
    ),
    Tool(
        id="plex_reset_posters",
        label="Reset Plex images",
        description="Puts one image kind, or all of them, of every item in every enabled Plex library back to the server's own artwork. Badges and Posterflow's uploads of that kind are discarded. The next Asset Upload run pushes yours again, badged where overlays apply.",
        action="plex image reset",
        execute=_reset_posters("plex"),
        group="plex",
        ready=_has_servers("plex"),
        confirm="Yes, reset them",
        dry_run=True,
        kinds=IMAGE_KINDS,
        run_label="Reset",
        **_with_settings(_kinds_settings(IMAGE_KINDS)),
    ),
    Tool(
        id="jellyfin_clean_image_cache",
        label="Clean Jellyfin image cache",
        description="Jellyfin keeps one poster file per item, so replaced posters only leave resized copies in its image cache. This asks each server to run its own cache clean now.",
        action="jellyfin cache clean",
        execute=_jellyfin_clean_image_cache,
        group="jellyfin",
        ready=_has_servers("jellyfin"),
    ),
    Tool(
        id="jellyfin_reset_posters",
        label="Reset Jellyfin images",
        description="Deletes one image kind, or all of them, of every item in every enabled Jellyfin library and asks the server to fetch its own again from its metadata providers. Badge records are dropped. The next Asset Upload run pushes yours again, badged where overlays apply.",
        action="jellyfin image reset",
        execute=_reset_posters("jellyfin"),
        group="jellyfin",
        ready=_has_servers("jellyfin"),
        confirm="Yes, reset them",
        dry_run=True,
        kinds=JELLYFIN_IMAGE_KINDS,
        run_label="Reset",
        **_with_settings(_kinds_settings(JELLYFIN_IMAGE_KINDS)),
    ),
    Tool(
        id="sonarr_rename",
        label="Rename Sonarr files",
        description="Asks each Sonarr instance which episode files and series folders no longer match its naming format and has it rename the ones you picked, then checks that every file is still there. Processed series get the cycle tag so the next run moves on; once every series carries it, the tags are cleared and the cycle starts over.",
        action="sonarr rename",
        execute=_arr_rename("sonarr"),
        group="sonarr",
        ready=_has_arr("sonarr"),
        confirm="Yes, rename them",
        dry_run=True,
        per_instance=True,
        **_with_settings(_arr_settings("sonarr")),
    ),
    Tool(
        id="radarr_rename",
        label="Rename Radarr files",
        description="Asks each Radarr instance which movie files and folders no longer match its naming format and has it rename the ones you picked, then checks that every file is still there. Processed movies get the cycle tag so the next run moves on; once every movie carries it, the tags are cleared and the cycle starts over.",
        action="radarr rename",
        execute=_arr_rename("radarr"),
        group="radarr",
        ready=_has_arr("radarr"),
        confirm="Yes, rename them",
        dry_run=True,
        per_instance=True,
        **_with_settings(_arr_settings("radarr")),
    ),
    Tool(
        id="sonarr_upgrade",
        label="Search Sonarr for upgrades",
        description="Has each Sonarr instance search for better releases of a few series per run, one search at a time, and reports what it grabbed against the files it replaces. Later runs say whether each grab imported. Searched series get the search tag so the next run moves on; once every series carries it, the tags are cleared and the cycle starts over. Sonarr decides what is an upgrade.",
        action="sonarr upgrade search",
        execute=_arr_upgrade("sonarr"),
        group="sonarr",
        ready=_has_arr("sonarr"),
        confirm="Yes, search",
        destructive=False,
        dry_run=True,
        per_instance=True,
        run_label="Search",
        **_with_settings(_upgrade_settings("sonarr")),
    ),
    Tool(
        id="radarr_upgrade",
        label="Search Radarr for upgrades",
        description="Has each Radarr instance search for better releases of a few movies per run, one search at a time, and reports what it grabbed against the file it replaces. Later runs say whether each grab imported. Searched movies get the search tag so the next run moves on; once every movie carries it, the tags are cleared and the cycle starts over. Radarr decides what is an upgrade.",
        action="radarr upgrade search",
        execute=_arr_upgrade("radarr"),
        group="radarr",
        ready=_has_arr("radarr"),
        confirm="Yes, search",
        destructive=False,
        dry_run=True,
        per_instance=True,
        run_label="Search",
        **_with_settings(_upgrade_settings("radarr")),
    ),
)}


def _build_progress_callback(db: Session, job: Job) -> ProgressCallback:
    def progress(phase: str, current: int, total: int, message: str) -> None:
        check_cancelled(job.id)
        if total > 0:
            target = min(max(int((current / total) * 100), 0), 99)
            current_progress = int(job.progress or 0)
            next_progress = max(current_progress, target)
            if next_progress != current_progress or message != str(job.message or ""):
                update_job_state(db, job, progress=next_progress, message=message)
    return progress


def run_tool_background_job(job_id: int, tool_id: str, dry_run: bool = False, settings: Optional[Settings] = None,
                            profile_name: Optional[str] = None, instance: Optional[str] = None) -> None:
    """`settings` is the profile's (or the run's own) settings, already normalized; `profile_name`
    names the run; `instance` limits a per-instance tool to that one instance."""
    remember_tool_job(job_id, tool_id)  # a scheduled run is only known from here
    settings = settings or {}
    db = SessionLocal()
    handler_id = add_job_log_handler("tools", job_id, "Tools")
    success = False
    tool = TOOLS.get(tool_id)
    label = tool.label if tool else tool_id
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log_error(LogTags.TOOLS, f"Job {job_id} not found in database")
            return
        if tool is None:
            raise ValueError(f"unknown tool '{tool_id}'")
        update_job_state(db, job, status=JOB_STATUS_RUNNING, message=format_start_message(tool.action_for(settings, profile_name, instance), dry_run=dry_run))
        log_section_start(LogTags.TOOLS, f"Background {label} Job {job_id}" + (f" (profile '{profile_name}')" if profile_name else ""))
        detail = tool.execute(db, _build_progress_callback(db, job), job_id, dry_run, settings, instance)
        update_job_state(db, job, status=JOB_STATUS_COMPLETED, progress=100,
                         message=format_complete_message(label, detail + (" (Dry Run)" if dry_run else "")),
                         completed_at=datetime.now(timezone.utc))
        success = True
        log_section_end(LogTags.TOOLS, f"Background {label} Complete")
    except JobCancelled:
        db.rollback()
        finalize_job_cancelled(db, job_id)
        log_section_end(LogTags.TOOLS, f"Background {label} Stopped")
        raise
    except Exception as e:
        log_error(LogTags.TOOLS, f"Background {label.lower()} job failed: {e}\n{traceback.format_exc()}")
        db.rollback()
        try:
            mark_job_failed(db, job_id, e)
        except Exception as commit_error:
            log_error(LogTags.TOOLS, f"Failed to update job status: {commit_error}\n{traceback.format_exc()}")
            db.rollback()
    finally:
        ACTIVE_TOOL_JOBS.pop(job_id, None)
        remove_job_log_handler(handler_id, "tools", success=success)
        db.close()
