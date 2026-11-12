"""Tools page API: the tool catalog with availability and each tool's profiles, running a tool
as a background job, the profiles themselves, the items carrying one of an arr tool's tags (the
renamer's cycle tag, the upgrader's search tag, either one's ignore tag), the titles, tags and
quality profiles an arr instance has to set a filter from, and the Plex Metadata folder paths the Plex tools (and the overlay engine's tidying) need."""

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from core.job_queue import job_queue
from core.logging import log_user_action
from models.job import create_job, format_start_message
from models.tool_profile import get_tool_profile
from modules.tools import GROUPS, TOOLS, Tool, active_tool_jobs, group_instance_names, remember_tool_job, run_tool_background_job
from services.arr_renamer import KINDS as ARR_KINDS, set_tagged, tag_label_problem, tag_list, title_list
from services.arr_upgrader import arr_options
from services.tool_profiles import create_profile, delete_profile, describe_profile, has_profiles, per_instance, run_settings, update_profile
from services.plex_bundles import is_plex_metadata_root, load_plex_metadata_paths, plex_uploads_dir, resolve_plex_metadata_root, save_plex_metadata_paths
from services.poster_tools import PosterTools, instance_names
from util.media_server.client import create_media_server_client

router = APIRouter(prefix="/api/tools", tags=["tools"])


class ToolRunRequest(BaseModel):
    dry_run: bool = False
    confirm: bool = False
    profile_id: Optional[int] = None  # the profile whose settings run; None = the default profile
    settings: Optional[Dict[str, Any]] = None  # this run's own settings instead of a profile's (the Overlays page cleans posters only)
    instance: Optional[str] = None  # one configured instance to run instead of all of them (per-instance tools only)
    profiles: Optional[Dict[str, int]] = None  # a run of every instance: the profile each one runs (its default when not named)


class ToolProfileCreateRequest(BaseModel):
    name: str
    settings: Optional[Dict[str, Any]] = None  # None = the tool's defaults
    instance: Optional[str] = None  # the instance the profile belongs to (tools that keep profiles per instance)


class ToolProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None
    is_default: Optional[bool] = None


class RenameTagRequest(BaseModel):
    instance: str
    tag: str  # an arr tool's cycle, search or ignore tag
    add: List[int] = []  # item ids that get the tag
    remove: List[int] = []  # and the ones it comes off


class PlexPathsRequest(BaseModel):
    paths: Dict[str, str]


class PlexPathCheckRequest(BaseModel):
    instance: str
    path: str


@router.get("")
def list_tools(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """One card per app: its configured instances and its tools, each with the job it is
    busy with right now (so the page shows it after a reload)."""
    active = active_tool_jobs(db)
    return {"groups": [
        {"id": gid, "label": label, "instances": group_instance_names(db, gid),
         "tools": [{**tool.describe(db), "active_job": active.get(tool.id)} for tool in TOOLS.values() if tool.group == gid]}
        for gid, label in GROUPS
    ]}


@router.post("/{tool_id}/run")
def run_tool(tool_id: str, payload: Optional[ToolRunRequest] = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    tool = TOOLS.get(tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool '{tool_id}'")
    available, reason = tool.ready(db)
    if not available:
        raise HTTPException(status_code=400, detail=reason)
    if tool.confirm and not (payload and payload.confirm):
        raise HTTPException(status_code=400, detail=f"Pass confirm=true to run {tool.label}")
    dry_run = bool(payload and payload.dry_run and tool.dry_run)
    instance = ((payload.instance or "").strip() or None) if payload else None
    if instance:
        if not tool.per_instance:
            raise HTTPException(status_code=400, detail=f"{tool.label} runs on every instance.")
        if instance not in group_instance_names(db, tool.group):
            raise HTTPException(status_code=400, detail=f"No {tool.group.capitalize()} instance named '{instance}'. Reload the page.")
    settings: Dict[str, Any] = {}
    profile_name: Optional[str] = None
    if payload and payload.settings is not None and has_profiles(tool):
        try:
            settings = tool.normalize_settings(db, payload.settings)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        problem = tool.settings_problem(settings) if tool.settings_problem else ""
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        if per_instance(tool):  # the same settings for the instance named, or for every one
            settings = {"instances": {name: settings for name in ([instance] if instance else tool.instance_names(db))}}
    elif has_profiles(tool):
        # Each named profile has to be one of the list it is run from: the tool's, or the instance's.
        named = {instance: payload.profile_id} if payload and payload.profile_id is not None else {}
        if payload and payload.profiles and per_instance(tool) and not instance:
            named = dict(payload.profiles)
        for scope, profile_id in named.items():
            if per_instance(tool) and not scope:
                raise HTTPException(status_code=400, detail=f"Each {tool.group.capitalize()} instance has its own profiles. Name the instance too.")
            row = get_tool_profile(db, tool.id, profile_id)
            if row is None or row.instance != (scope if per_instance(tool) else None):
                raise HTTPException(status_code=404, detail="That profile no longer exists. Reload the page.")
        profile_name, settings, _gone = run_settings(db, tool, instance=instance, profile_id=payload.profile_id if payload else None,
                                                     chosen=named if not instance else None)
    job = create_job(db, job_type=tool.job_type, message=format_start_message(tool.action_for(settings, profile_name, instance), dry_run=dry_run))
    log_user_action(f"Queued {tool.label} (job {job.id}{', dry run' if dry_run else ''}{f', profile {profile_name}' if profile_name else ''}{f', instance {instance}' if instance else ''})")
    remember_tool_job(job.id, tool.id)
    job_queue.submit(run_tool_background_job, job.id, job.id, tool.id, dry_run, settings, profile_name, instance)
    return {"success": True, "job_id": job.id, "message": f"{tool.label} queued."}


def _profiled_tool(tool_id: str) -> Tool:
    tool = TOOLS.get(tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool '{tool_id}'")
    if not has_profiles(tool):
        raise HTTPException(status_code=400, detail=f"{tool.label} has no settings, so no profiles.")
    return tool


def _profile_of(db: Session, tool: Tool, profile_id: int):
    row = get_tool_profile(db, tool.id, profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return row


@router.post("/{tool_id}/profiles")
def create_tool_profile(tool_id: str, payload: ToolProfileCreateRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    tool = _profiled_tool(tool_id)
    try:
        row = create_profile(db, tool, payload.name, payload.settings, payload.instance)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    log_user_action(f"Created {tool.label} profile '{row.name}'" + (f" for {row.instance}" if row.instance else ""))
    return describe_profile(db, tool, row)


@router.put("/{tool_id}/profiles/{profile_id}")
def update_tool_profile(tool_id: str, profile_id: int, payload: ToolProfileUpdateRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    tool = _profiled_tool(tool_id)
    row = _profile_of(db, tool, profile_id)
    try:
        row = update_profile(db, tool, row, name=payload.name, raw_settings=payload.settings, is_default=payload.is_default)
    except ValueError as exc:  # a taken name, or a tag the arr could never make
        raise HTTPException(status_code=400, detail=str(exc))
    log_user_action(f"Saved {tool.label} profile '{row.name}'" + (f" of {row.instance}" if row.instance else ""))
    return describe_profile(db, tool, row)


@router.delete("/{tool_id}/profiles/{profile_id}")
def delete_tool_profile(tool_id: str, profile_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    tool = _profiled_tool(tool_id)
    row = _profile_of(db, tool, profile_id)
    name, owner = row.name, row.instance
    try:
        delete_profile(db, tool, row)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    log_user_action(f"Deleted {tool.label} profile '{name}'" + (f" of {owner}" if owner else ""))
    return {"success": True}


def _tag_target(db: Session, tool_id: str, instance: str, tag: str) -> tuple:
    """The arr kind, instance and tag of a tag-list request, or the 4xx that refuses it."""
    tool = TOOLS.get(tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool '{tool_id}'")
    if tool.group not in ARR_KINDS:
        raise HTTPException(status_code=400, detail=f"{tool.label} has no tags to set.")
    instance = instance.strip()
    if instance not in group_instance_names(db, tool.group):
        raise HTTPException(status_code=400, detail=f"No {tool.group.capitalize()} instance named '{instance}'. Reload the page.")
    tag = tag.strip()
    if not tag:
        raise HTTPException(status_code=400, detail="Set the tag first.")
    problem = tag_label_problem(tag)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    return tool.group, instance, tag


@router.get("/{tool_id}/tagged")
def get_rename_tagged(tool_id: str, instance: str, tag: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The instance's movies or series, each marked when it carries the tag."""
    kind, instance, tag = _tag_target(db, tool_id, instance, tag)
    try:
        return tag_list(db, kind, instance, tag)
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.put("/{tool_id}/tagged")
def put_rename_tagged(tool_id: str, payload: RenameTagRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Put the tag on some items and take it off others, in the arr itself."""
    kind, instance, tag = _tag_target(db, tool_id, payload.instance, payload.tag)
    add, remove = sorted(set(payload.add)), sorted(set(payload.remove))
    if set(add) & set(remove):
        raise HTTPException(status_code=400, detail="An item cannot get the tag and lose it at once.")
    try:
        done = set_tagged(db, kind, instance, tag, add, remove)
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    log_user_action(f"{instance}: tag '{tag}' put on {done['added']} item(s), taken off {done['removed']}")
    return {"success": True, **done}


def _arr_instance(db: Session, tool_id: str, instance: str) -> tuple:
    """The arr kind and instance a filter is set from, or the 4xx that refuses the request."""
    tool = TOOLS.get(tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool '{tool_id}'")
    if tool.group not in ARR_KINDS:
        raise HTTPException(status_code=400, detail=f"{tool.label} has no Sonarr or Radarr instance to read.")
    instance = instance.strip()
    if instance not in group_instance_names(db, tool.group):
        raise HTTPException(status_code=400, detail=f"No {tool.group.capitalize()} instance named '{instance}'. Reload the page.")
    return tool.group, instance


@router.get("/{tool_id}/arr-options")
def get_arr_options(tool_id: str, instance: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The instance's tags and quality profiles, each with how many items carry or use it."""
    kind, instance = _arr_instance(db, tool_id, instance)
    try:
        return arr_options(db, kind, instance)
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/{tool_id}/titles")
def get_arr_titles(tool_id: str, instance: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The instance's movies or series, in title order, to try a title filter against."""
    kind, instance = _arr_instance(db, tool_id, instance)
    try:
        return title_list(db, kind, instance)
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/plex-metadata-paths")
def get_plex_metadata_paths(db: Session = Depends(get_db)) -> Dict[str, Any]:
    return {"instances": instance_names(db, "plex"), "paths": load_plex_metadata_paths(db, resolve=False)}


@router.put("/plex-metadata-paths")
def put_plex_metadata_paths(payload: PlexPathsRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    paths = save_plex_metadata_paths(db, payload.paths)
    log_user_action(f"Saved Plex metadata folders for {', '.join(sorted(paths)) or 'no server'}")
    return {"success": True, "paths": paths}


@router.post("/plex-metadata-paths/check")
def check_plex_metadata_path(payload: PlexPathCheckRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Does this look like Plex's Metadata folder, and does it hold a bundle of an item in
    this server's libraries? Deletes nothing."""
    typed = payload.path.strip().rstrip("/\\")
    if not typed or not os.path.isdir(typed):
        return {"ok": False, "reason": "Folder not found (as Posterflow sees it)."}
    root = resolve_plex_metadata_root(typed)
    if not is_plex_metadata_root(root):
        return {"ok": False, "reason": "Not a Plex Metadata folder: no Movies or TV Shows folder inside."}
    found = "Found the Metadata folder." if root == typed else "Found the Metadata folder inside it."
    tools = PosterTools(db)
    instance = next((i for i in tools.engine.instances() if i["name"] == payload.instance and i.get("type") == "plex"), None)
    if instance is None:
        return {"ok": True, "reason": found, "verified_title": None}
    client = create_media_server_client(instance)
    if client is None:
        return {"ok": False, "reason": f"Found the Metadata folder, but could not connect to {payload.instance}."}
    # Quietly confirm the folder belongs to this server (a bundle of one of its items is in it)
    # and that Posterflow's user may delete there.
    verified, bundle_folder = None, None
    for lib in tools.enabled_libraries().get(payload.instance, []):
        try:
            items = client.get_library_items(str(lib.get("key")))
        except Exception:
            items = []
        for item in items[:50]:
            folder = plex_uploads_dir(root, item)
            if folder is not None and folder.is_dir():
                verified, bundle_folder = item.title, folder
                break
        if verified:
            break
    if bundle_folder is not None and not os.access(bundle_folder, os.W_OK):
        return {"ok": False, "reason": f"Found the Metadata folder, but Posterflow (uid {os.getuid()}) cannot delete files in it. "
                                       "Run it with the Plex user's group: PGID, or EXTRA_GROUPS."}
    return {"ok": True, "reason": f"Connected to {payload.instance}. {found}", "verified_title": verified}
