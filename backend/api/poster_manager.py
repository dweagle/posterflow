from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Any, Callable, Dict, Optional, List
from pydantic import BaseModel, Field
import traceback
import os
import json
import re
import unicodedata
import requests as http_requests
from pathlib import Path
import threading
from datetime import datetime, timezone
from difflib import SequenceMatcher

from database import get_db
from core.job_queue import job_queue
from core.logging import LogTags, log_warning, log_error, log_user_action, log_info
from models.job import (
    Job,
    JOB_TYPE_POSTER_WORKFLOW,
    JOB_STATUS_PENDING,
    JOB_STATUSES_ACTIVE,
    create_job,
    JOB_TYPE_POSTER_RENAMER,
    JOB_TYPE_BORDER_REPLACER,
    JOB_TYPE_UNMATCHED_DETECTION,
    format_start_message,
)
from models.drive import Drive
from models.poster import Poster
from models.setting import get_setting, upsert_setting
from modules.renamer import run_rename_background_job
from modules.border import run_border_replacer_background_job
from modules.unmatched import run_unmatched_detection_background_job
from modules.flow import run_flow_background_job
from services.unmatched_assets import UnmatchedAssetsService

router = APIRouter(prefix="/api/posterflow", tags=["poster-manager"])

SETTING_POSTER_DESTINATION = "poster_destination"
SETTING_POSTER_ACTION_TYPE = "poster_action_type"
SETTING_POSTER_ASSET_FOLDERS = "poster_asset_folders"
SETTING_POSTER_RENAMER_STATS = "poster_renamer_stats"
SETTING_POSTER_DRIVE_PRIORITY = "poster_drive_priority"
SETTING_AUTO_RUN_BORDER = "auto_run_border"
SETTING_BORDER_REPLACER_MODE = "border_replacer_mode"
SETTING_POSTER_FLOW_CONFIG = "poster_flow_config"
SETTING_TMDB_API_KEY = "tmdb_api_key"

# Global lock to prevent multiple flow executions
_flow_lock = threading.Lock()
_flow_running = False
_flow_started_at = None


def job_started_response(
    job_id: int,
    message: str,
    include_status: bool = True,
    status: str = "pending",
) -> Dict[str, Any]:
    response: Dict[str, Any] = {
        "success": True,
        "job_id": job_id,
        "message": message,
    }
    if include_status:
        response["status"] = status
    return response


def log_job_queued(tag: str, label: str, job_id: int, suffix: str = "") -> None:
    log_info(tag, f"{label} queued in background (Job ID: {job_id}{suffix})")


def _require_destination_setting(
    db: Session,
    missing_detail: str,
    require_value: bool = False,
) -> str:
    setting = get_setting(db, SETTING_POSTER_DESTINATION)
    if not setting or (require_value and not setting.value):
        raise HTTPException(status_code=400, detail=missing_detail)
    return setting.value


def _require_existing_path(
    path: str,
    missing_detail: str,
    exists_check: Optional[Callable[[str], bool]] = None,
) -> None:
    checker = exists_check or os.path.exists
    if not checker(path):
        raise HTTPException(status_code=400, detail=missing_detail)


class PosterConfig(BaseModel):
    """Configuration for poster renaming."""
    destination: Optional[str] = None
    destination_dir: Optional[str] = None
    action_type: Optional[str] = "copy"
    asset_folders: Optional[bool] = True
    dry_run: Optional[bool] = False
    match_threshold: Optional[float] = 0.8


class RenameRequestPayload(BaseModel):
    """Payload wrapper for rename requests."""
    config: PosterConfig


class BorderReplacerRunRequest(BaseModel):
    """Payload for border replacer runs."""
    dry_run: bool = False
    mode: Optional[str] = None


class DrivePriority(BaseModel):
    drive_ids: List[int]
    enabled_styles: List[str]


class FlowJobConfig(BaseModel):
    enabled: bool = True
    stop_on_error: bool = True


class IdarrFlowJobConfig(BaseModel):
    enabled: bool = False
    stop_on_error: bool = False
    scope_indices: List[int] = Field(default_factory=list)
    sync_after_run: bool = False


class FlowConfig(BaseModel):
    idarr: IdarrFlowJobConfig = Field(default_factory=IdarrFlowJobConfig)
    sync_drives: FlowJobConfig = FlowJobConfig(enabled=True, stop_on_error=True)
    rename_posters: FlowJobConfig = FlowJobConfig(enabled=True, stop_on_error=True)
    detect_unmatched: FlowJobConfig = FlowJobConfig(enabled=True, stop_on_error=True)
    border_replacer: FlowJobConfig = FlowJobConfig(enabled=False, stop_on_error=True)
    plex_upload: FlowJobConfig = FlowJobConfig(enabled=False, stop_on_error=False)


class FlowRunRequest(BaseModel):
    dry_run: bool = False


@router.post("/rename")
async def rename_posters(
    payload: RenameRequestPayload,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Start Poster Renamer job in background.
    Returns immediately with job_id while rename executes asynchronously.
    """
    try:
        config = payload.config

        dest_dir = config.destination or config.destination_dir
        if not dest_dir:
            raise HTTPException(status_code=400, detail="Destination directory not specified")

        priority_setting = get_setting(db, SETTING_POSTER_DRIVE_PRIORITY)

        if not priority_setting:
            log_warning(LogTags.POSTER_RENAMER, "No drive priority configured")
            raise HTTPException(
                status_code=400,
                detail="No drive priority configured. Please configure drive priority in Poster Manager → Drive Priority tab."
            )

        try:
            priority_data = json.loads(priority_setting.value)
            drive_ids = priority_data.get("drive_ids", [])

            if not drive_ids:
                log_warning(LogTags.POSTER_RENAMER, "Drive priority list is empty")
                raise HTTPException(
                    status_code=400,
                    detail="Drive priority list is empty. Please add drives to the priority list in Poster Manager → Drive Priority tab."
                )

            drives_available = db.query(Drive).filter(
                Drive.id.in_(drive_ids),
                Drive.subscribed == True
            ).count()

            if drives_available == 0:
                log_warning(LogTags.POSTER_RENAMER, "No drives in priority list are subscribed (endpoint validation)")
                raise HTTPException(
                    status_code=400,
                    detail="No drives in priority list are subscribed. Please subscribe to drives in the GDrives page and add them to Poster Manager → Drive Priority tab."
                )

        except json.JSONDecodeError:
            log_error(LogTags.POSTER_RENAMER, "Failed to parse drive priority data (endpoint)")
            raise HTTPException(
                status_code=400,
                detail="Drive priority configuration is invalid. Please reconfigure in Poster Manager → Drive Priority tab."
            )

        job = create_job(
            db,
            job_type=JOB_TYPE_POSTER_RENAMER,
            message=format_start_message("poster renamer"),
        )
        job_id = job.id

        log_job_queued(LogTags.POSTER_RENAMER, "Poster Renamer", job_id)

        config_data = config.model_dump()
        job_queue.submit(run_rename_background_job, job_id, job_id, config_data)

        return job_started_response(
            job_id,
            "Poster Renamer queued in background. Monitor progress via job status.",
        )

    except HTTPException:
        raise
    except Exception as e:
        log_error(LogTags.POSTER_RENAMER, f"Failed to start Poster Renamer: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to start Poster Renamer")


@router.post("/border-replacer/run")
async def run_border_replacer(
    payload: Optional[BorderReplacerRunRequest] = None,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Start border replacer job in background.
    Returns immediately with job_id while border replacer executes asynchronously.
    """
    try:
        source_dir = _require_destination_setting(
            db,
            "No destination directory configured. Please configure in Poster Manager settings.",
            require_value=True,
        )
        _require_existing_path(source_dir, f"Destination directory does not exist: {source_dir}")

        dry_run = payload.dry_run if payload else False
        mode = payload.mode if payload else None

        if not mode:
            mode_setting = get_setting(db, SETTING_BORDER_REPLACER_MODE)
            mode = mode_setting.value if mode_setting else "incremental"

        if mode not in ["full", "incremental"]:
            mode = "incremental"

        mode_label = " (incremental)" if mode == "incremental" else " (full)"
        job = create_job(
            db,
            job_type=JOB_TYPE_BORDER_REPLACER,
            message=format_start_message("border replacer", dry_run=dry_run, qualifier=mode_label.strip()),
        )
        job_id = job.id

        log_job_queued(LogTags.POSTER_RENAMER, "Border replacer", job_id, f", mode: {mode}")

        job_queue.submit(run_border_replacer_background_job, job_id, job_id, dry_run, mode)

        return job_started_response(
            job_id,
            "Border replacer queued in background. Monitor progress via job status.",
        )

    except HTTPException:
        raise
    except Exception as e:
        log_error(LogTags.POSTER_RENAMER, f"Failed to start border replacer: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to start border replacer")


@router.get("/config")
async def get_rename_config(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Get Poster Renamer configuration.
    """
    try:
        config: Dict[str, Any] = {}

        dest_dir_setting = get_setting(db, SETTING_POSTER_DESTINATION)
        config["destination"] = dest_dir_setting.value if dest_dir_setting else ""

        action_type_setting = get_setting(db, SETTING_POSTER_ACTION_TYPE)
        config["action_type"] = action_type_setting.value if action_type_setting else "copy"

        asset_folders_setting = get_setting(db, SETTING_POSTER_ASSET_FOLDERS)
        config["asset_folders"] = asset_folders_setting.value.lower() == "true" if asset_folders_setting else True

        config["dry_run"] = False
        config["match_threshold"] = 0.8

        return config

    except Exception as e:
        log_error(LogTags.POSTER_RENAMER, f"Error getting Poster Renamer config: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Error getting Poster Renamer config")


@router.post("/config")
async def save_rename_config(
    config: PosterConfig,
    db: Session = Depends(get_db)
) -> Dict[str, bool]:
    """
    Save Poster Renamer configuration.
    """
    try:
        dest = config.destination or config.destination_dir
        if dest is not None:
            upsert_setting(db, SETTING_POSTER_DESTINATION, dest)

        if config.action_type is not None:
            upsert_setting(db, SETTING_POSTER_ACTION_TYPE, config.action_type)

        if config.asset_folders is not None:
            upsert_setting(db, SETTING_POSTER_ASSET_FOLDERS, "true" if config.asset_folders else "false")

        db.commit()
        return {"success": True}

    except Exception as e:
        db.rollback()
        log_error(LogTags.POSTER_RENAMER, f"Error saving Poster Renamer config: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Error saving Poster Renamer config")


@router.get("/stats")
async def get_poster_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Get poster statistics from last rename operation, or scan organized folder if no stats stored.
    """
    try:
        subscribed_drives = db.query(Drive).filter(Drive.subscribed == True).all()

        total_posters = 0
        for drive in subscribed_drives:
            count = db.query(Poster).filter(Poster.drive_id == drive.drive_id).count()
            total_posters += count

        last_scan = None
        for drive in subscribed_drives:
            if drive.last_synced and (not last_scan or drive.last_synced > last_scan):
                last_scan = drive.last_synced

        stats_setting = get_setting(db, SETTING_POSTER_RENAMER_STATS)

        matched_posters = 0
        collections = 0
        movies = 0
        series = 0
        style_counts: Dict[str, Any] = {}
        style_fallbacks: Dict[str, Any] = {}

        if stats_setting and stats_setting.value:
            try:
                stored_stats = json.loads(stats_setting.value)
                matched_posters = stored_stats.get("total_matched", 0)
                collections = stored_stats.get("collections", 0)
                movies = stored_stats.get("movies", 0)
                series = stored_stats.get("series", 0)
                style_counts = stored_stats.get("style_counts", {})
                style_fallbacks = stored_stats.get("style_fallbacks", {})
            except json.JSONDecodeError:
                pass
        else:
            dest_setting = get_setting(db, SETTING_POSTER_DESTINATION)

            if dest_setting and dest_setting.value:
                dest_dir = Path(dest_setting.value)

                if dest_dir.exists():
                    for item in dest_dir.rglob("*"):
                        if item.is_file() and item.suffix.lower() in ['.jpg', '.jpeg', '.png']:
                            matched_posters += 1

                            path_str = str(item.relative_to(dest_dir)).lower()
                            filename = item.stem.lower()

                            if 'collection' in path_str or 'collection' in filename:
                                collections += 1
                            elif 'season' in path_str or 'season' in filename:
                                series += 1
                            else:
                                parent_has_seasons = False
                                if item.parent != dest_dir:
                                    for sibling in item.parent.iterdir():
                                        if sibling.is_dir() and 'season' in sibling.name.lower():
                                            parent_has_seasons = True
                                            break

                                if parent_has_seasons:
                                    series += 1
                                else:
                                    movies += 1

        unmatched_posters = max(0, total_posters - matched_posters)

        return {
            "total_posters": total_posters,
            "matched_posters": matched_posters,
            "unmatched_posters": unmatched_posters,
            "collections": collections,
            "movies": movies,
            "series": series,
            "last_scan": last_scan.isoformat() if last_scan else None,
            "style_counts": style_counts,
            "style_fallbacks": style_fallbacks,
        }

    except Exception as e:
        log_error(LogTags.POSTER_RENAMER, f"Error getting poster stats: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Error getting poster stats")


@router.get("/priority")
async def get_drive_priority(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Get drive priority configuration.
    """
    try:
        priority_setting = get_setting(db, SETTING_POSTER_DRIVE_PRIORITY)

        if priority_setting:
            priority_data = json.loads(priority_setting.value)
            return {
                "drive_ids": priority_data.get("drive_ids", []),
                "enabled_styles": priority_data.get("enabled_styles", ["MM2K", "CL2K", "Custom"])
            }

        return {
            "drive_ids": [],
            "enabled_styles": ["MM2K", "CL2K", "Custom"]
        }

    except Exception as e:
        log_error(LogTags.POSTER_RENAMER, f"Error getting drive priority: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Error getting drive priority")


@router.post("/priority")
async def save_drive_priority(
    priority: DrivePriority,
    db: Session = Depends(get_db)
) -> Dict[str, bool]:
    """
    Save drive priority configuration.
    """
    try:
        priority_data = {
            "drive_ids": priority.drive_ids,
            "enabled_styles": priority.enabled_styles
        }

        upsert_setting(db, SETTING_POSTER_DRIVE_PRIORITY, json.dumps(priority_data))

        db.commit()

        log_user_action(
            f"Updated drive priority: {len(priority.drive_ids)} drives, {len(priority.enabled_styles)} styles enabled",
            drives=len(priority.drive_ids),
            styles=len(priority.enabled_styles)
        )

        return {"success": True}

    except Exception as e:
        db.rollback()
        log_error(LogTags.POSTER_RENAMER, f"Error saving drive priority: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Error saving drive priority")


@router.get("/auto-run-border")
async def get_auto_run_border(db: Session = Depends(get_db)) -> Dict[str, bool]:
    """
    Get auto-run border replacer setting.
    """
    try:
        setting = get_setting(db, SETTING_AUTO_RUN_BORDER)
        return {"enabled": setting.value.lower() == "true" if setting else False}
    except Exception as e:
        log_error(LogTags.POSTER_RENAMER, f"Error getting auto-run border setting: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Error getting auto-run border setting")


@router.post("/auto-run-border")
async def save_auto_run_border(
    enabled: bool,
    db: Session = Depends(get_db)
) -> Dict[str, bool]:
    """
    Save auto-run border replacer setting.
    """
    try:
        upsert_setting(db, SETTING_AUTO_RUN_BORDER, "true" if enabled else "false")

        db.commit()

        log_user_action(
            f"Auto-run border replacer {'enabled' if enabled else 'disabled'}",
            setting_key=SETTING_AUTO_RUN_BORDER,
            value=enabled
        )

        return {"success": True}
    except Exception as e:
        db.rollback()
        log_error(LogTags.POSTER_RENAMER, f"Error saving auto-run border setting: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Error saving auto-run border setting")


@router.get("/scan")
async def scan_poster_directory(source_path: Optional[str] = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Scan poster directory (placeholder for future implementation).
    """
    return {"scanned": 0, "found": []}


@router.get("/unmatched-stats")
async def get_unmatched_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Get cached unmatched media statistics.
    """
    try:
        unmatched_service = UnmatchedAssetsService(db)
        return unmatched_service.get_cached_results()
    except Exception as e:
        log_error(LogTags.UNMATCHED, f"Error getting stats: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Error getting unmatched stats")


@router.post("/detect-unmatched")
async def start_unmatched_detection(
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Start unmatched media detection in background.
    Returns immediately with job_id while detection executes asynchronously.
    Finds media without matching posters in organized folder.
    """
    try:
        destination_dir = _require_destination_setting(
            db,
            "No destination directory configured. Please configure in Poster Manager → Overview tab.",
        )

        _require_existing_path(
            destination_dir,
            f"Destination directory does not exist: {destination_dir}. Run 'Rename Posters' first to create organized posters.",
        )

        job = create_job(
            db,
            job_type=JOB_TYPE_UNMATCHED_DETECTION,
            message=format_start_message("unmatched detection"),
        )
        job_id = job.id

        log_job_queued(LogTags.UNMATCHED, "Detection", job_id)

        job_queue.submit(run_unmatched_detection_background_job, job_id, job_id)

        return job_started_response(
            job_id,
            "Unmatched detection queued in background. Monitor progress via job status.",
        )

    except HTTPException:
        raise
    except Exception as e:
        log_error(LogTags.UNMATCHED, f"Error during detection: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Error starting unmatched detection")


class UnmatchedTmdbSearchRequest(BaseModel):
    title: str
    year: Optional[int] = None
    type: str  # "movie", "show", "collection"


def _get_unmatched_tmdb_key(db: Session) -> str:
    """Get global TMDB API key."""
    setting = get_setting(db, SETTING_TMDB_API_KEY)
    if not setting or not setting.value:
        return ""
    return setting.value.strip()


_TMDB_TITLE_ALIASES: Dict[str, str] = {
    "&": "and", "and": "and",
    "vs.": "versus", "vs": "versus",
    "ep.": "episode", "ep": "episode",
    "vol.": "volume", "vol": "volume",
    "pt.": "part", "pt": "part",
    "dr.": "doctor", "dr": "doctor", "doctor": "doctor",
}


def _tmdb_normalize(value: str) -> str:
    """Normalize a title for comparison: strip accents, punctuation, apply canonical aliases."""
    normalized = re.sub(r"[''`\u02b9\u02bc]", "", str(value or ""))
    normalized = normalized.replace(":", "")
    normalized = unicodedata.normalize("NFKD", normalized).encode("ASCII", "ignore").decode()
    words = re.split(r"(\W+)", normalized)
    mapped = [_TMDB_TITLE_ALIASES.get(t.lower(), t.lower()) if t.strip() else t for t in words]
    return re.sub(r"\s+", " ", "".join(mapped)).strip().lower()


def _tmdb_word_jaccard(left: str, right: str) -> float:
    left_words = set(re.findall(r"\w+", left.lower()))
    right_words = set(re.findall(r"\w+", right.lower()))
    if not left_words or not right_words:
        return 0.0
    return len(left_words & right_words) / len(left_words | right_words)


def _tmdb_score_candidates(
    title: str,
    year: Optional[int],
    candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Score and sort TMDB candidate results using fuzzy title + year matching."""
    requested_norm = _tmdb_normalize(title)
    scored = []

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        candidate_title = str(candidate.get("title") or candidate.get("name") or "").strip()
        candidate_norms = [
            _tmdb_normalize(candidate_title),
            _tmdb_normalize(str(candidate.get("original_title") or "")),
            _tmdb_normalize(str(candidate.get("original_name") or "")),
        ]
        candidate_norms = [v for v in candidate_norms if v]
        if not candidate_norms:
            continue

        release_text = str(candidate.get("release_date") or candidate.get("first_air_date") or "").strip()
        candidate_year = int(release_text[:4]) if len(release_text) >= 4 and release_text[:4].isdigit() else None

        ratio = max(
            (SequenceMatcher(None, requested_norm, n).ratio() for n in candidate_norms),
            default=0.0,
        ) if requested_norm else 0.0
        jaccard = max(
            (_tmdb_word_jaccard(requested_norm, n) for n in candidate_norms),
            default=0.0,
        ) if requested_norm else 0.0

        exact_title = bool(requested_norm and any(requested_norm == n for n in candidate_norms))
        year_match = bool(isinstance(year, int) and isinstance(candidate_year, int) and year == candidate_year)
        year_close = bool(isinstance(year, int) and isinstance(candidate_year, int) and abs(year - candidate_year) <= 1)

        score = int(ratio * 60) + int(jaccard * 20)
        if exact_title:
            score += 15
        if year_match:
            score += 20
        elif year_close:
            score += 10

        if exact_title and year_match:
            reason = "exact"
        elif exact_title:
            reason = "exact_title"
        elif ratio >= 0.90 and (year_match or year_close):
            reason = "fuzzy_title_year"
        elif ratio >= 0.90:
            reason = "fuzzy_title"
        elif jaccard >= 0.75 and (year_match or year_close):
            reason = "token_overlap_year"
        elif year_match:
            reason = "year_match_only"
        else:
            reason = "rank_fallback"

        scored.append({"candidate": candidate, "score": score, "reason": reason})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


@router.post("/unmatched-tmdb-search")
async def search_unmatched_tmdb(payload: UnmatchedTmdbSearchRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Search TMDB for candidates matching an unmatched media item.
    Returns up to 10 candidate results with title, year, poster URL, and TMDB link.
    """
    title = (payload.title or "").strip()
    year = payload.year if isinstance(payload.year, int) else None
    media_type = (payload.type or "").strip().lower()

    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    if media_type not in {"movie", "show", "collection"}:
        raise HTTPException(status_code=400, detail="type must be one of: movie, show, collection")

    # Strip trailing (YYYY) from title if year is already provided separately,
    # so shows like "INVINCIBLE (2021)" don't get searched as "INVINCIBLE (2021)" + year=2021.
    import re as _re
    if year:
        title = _re.sub(r'\s*\(\d{4}\)\s*$', '', title).strip()

    tmdb_api_key = _get_unmatched_tmdb_key(db)
    if not tmdb_api_key:
        log_warning(LogTags.UNMATCHED, "Unmatched TMDB search blocked: TMDB API key is not configured")
        raise HTTPException(status_code=400, detail="TMDB API key is not configured. Add it in Settings → General → API Keys.")

    if media_type == "show":
        endpoint = "/search/tv"
        query_params: Dict[str, Any] = {"query": title, "include_adult": "false"}
        if year:
            query_params["first_air_date_year"] = year
        tmdb_entity = "tv"
        response_media_type = "show"
    elif media_type == "collection":
        endpoint = "/search/collection"
        query_params = {"query": title, "include_adult": "false"}
        tmdb_entity = "collection"
        response_media_type = "collection"
    else:
        endpoint = "/search/movie"
        query_params = {"query": title, "include_adult": "false"}
        if year:
            query_params["year"] = year
        tmdb_entity = "movie"
        response_media_type = "movie"

    try:
        response = http_requests.get(
            f"https://api.themoviedb.org/3{endpoint}",
            params={"api_key": tmdb_api_key, **query_params},
            timeout=20,
        )
        response.raise_for_status()
        tmdb_data = response.json()
    except http_requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"TMDB search failed: {exc}")

    results = tmdb_data.get("results") if isinstance(tmdb_data, dict) else []
    if not isinstance(results, list):
        results = []

    tmdb_ext_cache: Dict[int, Dict[str, Any]] = {}

    def fetch_external_ids(tmdb_id: int) -> Dict[str, Any]:
        if tmdb_id in tmdb_ext_cache:
            return tmdb_ext_cache[tmdb_id]
        if tmdb_entity == "collection":
            tmdb_ext_cache[tmdb_id] = {}
            return {}
        try:
            ext_resp = http_requests.get(
                f"https://api.themoviedb.org/3/{tmdb_entity}/{tmdb_id}/external_ids",
                params={"api_key": tmdb_api_key},
                timeout=12,
            )
            ext_resp.raise_for_status()
            ext_data = ext_resp.json()
            if isinstance(ext_data, dict):
                tmdb_ext_cache[tmdb_id] = ext_data
                return ext_data
        except http_requests.RequestException:
            pass
        tmdb_ext_cache[tmdb_id] = {}
        return {}

    # Score and sort results using fuzzy title + year matching
    scored = _tmdb_score_candidates(title, year, results[:10])

    candidates = []
    for entry in scored:
        item = entry["candidate"]
        reason = entry["reason"]
        tmdb_id = item.get("id")
        if not isinstance(tmdb_id, int):
            continue
        ext = fetch_external_ids(tmdb_id)
        imdb_id = ext.get("imdb_id") if isinstance(ext.get("imdb_id"), str) else None
        tvdb_raw = ext.get("tvdb_id")
        tvdb_id = tvdb_raw if isinstance(tvdb_raw, int) else None
        candidate_title = str(item.get("title") or item.get("name") or "").strip()
        release_text = str(item.get("release_date") or item.get("first_air_date") or "").strip()
        release_year = int(release_text[:4]) if len(release_text) >= 4 and release_text[:4].isdigit() else None
        poster_path = item.get("poster_path")
        poster_url = f"https://image.tmdb.org/t/p/w185{poster_path}" if isinstance(poster_path, str) and poster_path else None
        candidates.append({
            "tmdb_id": tmdb_id,
            "tvdb_id": tvdb_id,
            "imdb_id": imdb_id,
            "title": candidate_title,
            "year": release_year,
            "poster_url": poster_url,
            "overview": str(item.get("overview") or "").strip(),
            "popularity": float(item.get("popularity") or 0),
            "media_type": response_media_type,
            "match_reason": reason,
        })

    log_info(LogTags.UNMATCHED, f"TMDB search for '{title}' ({media_type}): {len(candidates)} candidates", year=year)
    return {"candidates": candidates}


@router.get("/flow/config")
async def get_flow_config(db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        default_step_config: Dict[str, Dict[str, bool]] = {
            "sync_drives": {"enabled": True, "stop_on_error": True},
            "rename_posters": {"enabled": True, "stop_on_error": True},
            "detect_unmatched": {"enabled": True, "stop_on_error": True},
            "border_replacer": {"enabled": False, "stop_on_error": True},
            "plex_upload": {"enabled": False, "stop_on_error": False},
        }
        default_idarr_config: Dict[str, Any] = {
            "enabled": False,
            "stop_on_error": False,
            "scope_indices": [],
            "sync_after_run": False,
        }
        flow_setting = get_setting(db, SETTING_POSTER_FLOW_CONFIG)

        if flow_setting:
            parsed = json.loads(flow_setting.value)
            if isinstance(parsed, dict):
                normalized: Dict[str, Any] = {"idarr": dict(default_idarr_config)}
                idarr_value = parsed.get("idarr")
                if isinstance(idarr_value, dict):
                    raw_indices = idarr_value.get("scope_indices") or []
                    normalized["idarr"] = {
                        "enabled": bool(idarr_value.get("enabled", False)),
                        "stop_on_error": bool(idarr_value.get("stop_on_error", False)),
                        "scope_indices": [int(i) for i in raw_indices if isinstance(i, int)],
                        "sync_after_run": bool(idarr_value.get("sync_after_run", False)),
                    }
                for key in default_step_config.keys():
                    value = parsed.get(key)
                    if isinstance(value, dict):
                        normalized[key] = {
                            "enabled": bool(value.get("enabled", default_step_config[key]["enabled"])),
                            "stop_on_error": bool(value.get("stop_on_error", default_step_config[key]["stop_on_error"])),
                        }
                    else:
                        normalized[key] = dict(default_step_config[key])
                return normalized

        return {"idarr": default_idarr_config, **default_step_config}

    except Exception as e:
        log_error(LogTags.WORKFLOW, f"Error getting flow config: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Error getting flow config")


@router.post("/flow/config")
async def save_flow_config(
    flow_config: FlowConfig,
    db: Session = Depends(get_db)
) -> Dict[str, bool]:
    try:
        flow_data = flow_config.model_dump()

        upsert_setting(db, SETTING_POSTER_FLOW_CONFIG, json.dumps(flow_data))

        db.commit()
        log_user_action("Saved flow configuration")
        return {"success": True}

    except Exception as e:
        db.rollback()
        log_error(LogTags.WORKFLOW, f"Error saving flow config: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Error saving flow config")


@router.post("/flow/run")
async def run_flow(
    payload: Optional[FlowRunRequest] = None,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    global _flow_running, _flow_started_at

    with _flow_lock:
        if _flow_running:
            lock_age_seconds = (datetime.now(timezone.utc) - _flow_started_at).total_seconds() if _flow_started_at else 0

            running_jobs = db.query(Job).filter(
                Job.job_type == JOB_TYPE_POSTER_WORKFLOW,
                Job.status.in_(JOB_STATUSES_ACTIVE)
            ).all()

            if not running_jobs:
                log_warning(LogTags.WORKFLOW, "Stale lock detected - no running jobs found. Clearing lock.", lock_held_for=f"{lock_age_seconds:.1f}s")
                _flow_running = False
                _flow_started_at = None
            elif lock_age_seconds > 900:
                log_error(LogTags.WORKFLOW, f"Lock held for {lock_age_seconds / 60:.1f} minutes - forcing release", running_jobs=[j.id for j in running_jobs])
                _flow_running = False
                _flow_started_at = None
            else:
                running_job_ids = [j.id for j in running_jobs]
                log_warning(LogTags.WORKFLOW, f"Workflow already running: Job(s) {running_job_ids}", lock_age=f"{lock_age_seconds:.1f}s")
                raise HTTPException(
                    status_code=409,
                    detail="Workflow is already running. Please wait for it to complete before starting another."
                )

        _flow_running = True
        _flow_started_at = datetime.now(timezone.utc)

    try:
        dry_run = payload.dry_run if payload else False

        job = create_job(
            db,
            job_type=JOB_TYPE_POSTER_WORKFLOW,
            status=JOB_STATUS_PENDING,
            message="Workflow pending (dry run)..." if dry_run else "Workflow pending...",
        )
        job_id = job.id

        log_job_queued(LogTags.WORKFLOW, "Workflow", job_id)

        def release_flow_lock() -> None:
            global _flow_running, _flow_started_at
            with _flow_lock:
                _flow_running = False
                _flow_started_at = None

        job_queue.submit(run_flow_background_job, job_id, job_id, dry_run, release_flow_lock)

        return job_started_response(
            job_id,
            "Workflow queued in background. Monitor progress via job status.",
        )

    except Exception as e:
        with _flow_lock:
            _flow_running = False
            _flow_started_at = None
        log_error(LogTags.WORKFLOW, f"Failed to start workflow: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to start workflow")
