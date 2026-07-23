"""Unified Asset Renamer — runs the poster and/or artwork renamers per the user's
Include selection (posters / logos / backgrounds / square art), both using the shared
`asset_renamer_libraries` selection. Reuses the existing engines untouched."""

from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.job_queue import job_queue
from core.logging import log_user_action
from database import get_db
from models.job import create_job, format_start_message, JOB_TYPE_POSTER_RENAMER
from models.setting import get_setting_value
from util.poster_settings import get_asset_include, get_poster_destination
from modules.renamer import run_rename_background_job

router = APIRouter(prefix="/api/posterflow", tags=["asset-rename"])

ASSET_RENAMER_LIBRARIES = "asset_renamer_libraries"


class AssetRenameRequest(BaseModel):
    dry_run: bool = False


@router.post("/asset-rename")
def start_asset_rename(payload: AssetRenameRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Queue one background job that renames/organizes the selected asset types (posters and/or
    artwork) in a single pass over the shared media library."""
    include = get_asset_include(db)
    dry_run = payload.dry_run

    if not include:
        return {"jobs": [], "status": "noop", "message": "No asset types selected to rename"}

    asset_folders_val = get_setting_value(db, "poster_asset_folders")
    config_data = {
        "destination": get_poster_destination(db),
        "action_type": get_setting_value(db, "poster_action_type", "copy"),
        "asset_folders": asset_folders_val.lower() == "true" if asset_folders_val else True,
        "dry_run": dry_run,
        "library_setting_key": ASSET_RENAMER_LIBRARIES,
        "include": include,
    }
    job = create_job(db, job_type=JOB_TYPE_POSTER_RENAMER, message=format_start_message("asset renamer", dry_run=dry_run))
    job_queue.submit(run_rename_background_job, job.id, job.id, config_data)

    log_user_action("Asset rename requested", dry_run=dry_run, include=include)
    return {
        "jobs": [{"job_id": job.id, "type": "assets"}],
        "job_id": job.id,
        "status": "queued",
        "message": f"Asset rename queued ({', '.join(include)})",
    }
