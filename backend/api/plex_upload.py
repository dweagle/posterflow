import json
from pathlib import Path
import re
import traceback
from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.job_queue import job_queue
from core.logging import (
    LogTags,
    log_error,
    log_info,
)
from database import get_db
from models.job import (
    JOB_TYPE_PLEX_UPLOAD,
    JOB_TYPE_PLEX_UPLOAD_SINGLE,
    JOB_TYPE_PLEX_UPLOAD_WEBHOOK,
    create_job,
    format_start_message,
)
from models.drive import Drive
from models.poster import Poster
from models.setting import get_setting, upsert_setting
from modules.upload import (
    PLEX_WEBHOOK_RETRY_ATTEMPTS,
    PLEX_WEBHOOK_RETRY_ATTEMPTS_MAX,
    PLEX_WEBHOOK_RETRY_DELAY_SECONDS,
    PLEX_WEBHOOK_RETRY_DELAY_SECONDS_MAX,
    POSTER_ID_PATTERN,
    SETTING_PLEX_LIBRARY_CONFIG,
    SETTING_PLEX_UPLOAD_LIBRARY_OVERRIDE,
    SETTING_PLEX_UPLOAD_MANUAL_DRY_RUN,
    SETTING_PLEX_UPLOAD_MANUAL_REAPPLY,
    SETTING_PLEX_UPLOAD_MANUAL_REMOVE_OVERLAY_LABEL,
    SETTING_PLEX_WEBHOOK_ENABLED,
    SETTING_PLEX_WEBHOOK_REMOVE_OVERLAY_LABEL,
    SETTING_PLEX_WEBHOOK_RENAME_THEN_UPLOAD,
    SETTING_PLEX_WEBHOOK_ADOPT_EXISTING_PROCESSED,
    SETTING_PLEX_WEBHOOK_RETRY_ATTEMPTS,
    SETTING_PLEX_WEBHOOK_RETRY_DELAY_SECONDS,
    _build_cache_clear_response as build_cache_clear_response,
    _build_cache_export_payload as build_cache_export_payload,
    _cache_summary as cache_summary,
    _fast_cache_summary as fast_cache_summary,
    _clear_webhook_dedupe_cache as clear_webhook_dedupe_cache,
    _coerce_webhook_retry_int as coerce_webhook_retry_int,
    _increment_webhook_stat as increment_webhook_stat,
    _is_duplicate_webhook_event as is_duplicate_webhook_event,
    _is_webhook_enabled as is_webhook_enabled,
    _is_webhook_remove_overlay_label_enabled as is_webhook_remove_overlay_label_enabled,
    _is_webhook_rename_then_upload_enabled as is_webhook_rename_then_upload_enabled,
    _is_webhook_adopt_existing_processed_enabled as is_webhook_adopt_existing_processed_enabled,
    _load_library_configs_setting as load_library_configs_setting,
    _load_plex_upload_file_cache as load_plex_upload_file_cache,
    _load_plex_upload_library_override as load_plex_upload_library_override,
    _load_priority_source_dir_context as load_priority_source_dir_context,
    _load_priority_source_dirs as load_priority_source_dirs,
    _load_webhook_stats as load_webhook_stats,
    _parse_arr_webhook_payload as parse_arr_webhook_payload,
    _reset_webhook_stats as reset_webhook_stats,
    _search_webhook_dedupe_entries as search_webhook_dedupe_entries,
    _get_manual_dry_run_enabled as get_manual_dry_run_enabled,
    _get_manual_reapply_enabled as get_manual_reapply_enabled,
    _get_manual_remove_overlay_label_enabled as get_manual_remove_overlay_label_enabled,
    _get_webhook_retry_attempts as get_webhook_retry_attempts,
    _get_webhook_retry_delay_seconds as get_webhook_retry_delay_seconds,
    _extract_season_number_from_file_name as extract_season_number_from_file_name,
    run_plex_upload_background_job as run_plex_upload_background_job_module,
    run_plex_webhook_background_job as run_plex_webhook_background_job_module,
    run_plex_single_manual_background_job as run_plex_single_manual_background_job_module,
)

router = APIRouter(prefix="/api/posterflow", tags=["plex-upload"])


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


def bool_to_setting(value: bool) -> str:
    return "true" if value else "false"


def build_manual_settings_response(db: Session) -> Dict[str, Any]:
    return {
        "dry_run": get_manual_dry_run_enabled(db),
        "reapply": get_manual_reapply_enabled(db),
        "remove_overlay_label": get_manual_remove_overlay_label_enabled(db),
    }


def build_webhook_settings_response(db: Session) -> Dict[str, Any]:
    return {
        "enabled": is_webhook_enabled(db),
        "remove_overlay_label": is_webhook_remove_overlay_label_enabled(db),
        "rename_then_upload": is_webhook_rename_then_upload_enabled(db),
        "adopt_existing_processed": is_webhook_adopt_existing_processed_enabled(db),
        "retry_attempts": get_webhook_retry_attempts(db),
        "retry_delay_seconds": get_webhook_retry_delay_seconds(db),
    }


def build_library_override_response(db: Session, override: Dict[str, Any]) -> Dict[str, Any]:
    global_configs = load_library_configs_setting(db, SETTING_PLEX_LIBRARY_CONFIG)
    return {
        "enabled": bool(override.get("enabled", False)),
        "configs": override.get("configs", []),
        "global_configs": global_configs,
    }


def raise_internal_error(context: str, error: Exception) -> None:
    log_error(LogTags.UPLOADER, f"{context}: {error}\n{traceback.format_exc()}")
    raise HTTPException(status_code=500, detail=context)


def get_required_source_dirs(db: Session, empty_message: str) -> list[str]:
    source_dirs, source_dirs_error = load_priority_source_dirs(db)
    if source_dirs_error:
        raise HTTPException(status_code=400, detail=source_dirs_error)
    if not source_dirs:
        raise HTTPException(status_code=400, detail=empty_message)
    return source_dirs


def normalize_single_manual_payload(
    payload: "PlexUploadSingleManualRequest",
) -> tuple[str, str, Dict[str, Any], str]:
    media_type = payload.media_type.strip().lower()
    if media_type not in {"movie", "series", "collection"}:
        raise HTTPException(status_code=400, detail="media_type must be 'movie', 'series', or 'collection'.")

    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required.")

    run_payload = payload.model_dump()
    run_payload["media_type"] = media_type
    run_payload["title"] = title
    normalized_imdb = run_payload.get("imdb_id")
    if isinstance(normalized_imdb, str):
        run_payload["imdb_id"] = normalized_imdb.strip().lower() or None

    item_label = f"{media_type}: {title}"
    if isinstance(payload.season_number, int):
        item_label += f" (Season {payload.season_number})"

    return media_type, title, run_payload, item_label


def resolve_authorized_source_image_path(source_path_raw: str, source_dirs: list[str]) -> Path:
    try:
        requested_path = Path(source_path_raw).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid source image path.")

    if not requested_path.exists() or not requested_path.is_file():
        raise HTTPException(status_code=404, detail="Source image not found.")

    for source_dir in source_dirs:
        source_root: Path | None = None
        try:
            source_root = Path(source_dir).resolve()
            requested_path.relative_to(source_root)
            return requested_path
        except Exception:
            source_root = None

    raise HTTPException(status_code=403, detail="Source image is outside allowed source folders.")


def _extract_source_metadata(source_file: Path) -> Dict[str, Any]:
    generic_file_stems = {
        "poster",
        "folder",
        "background",
        "fanart",
        "cover",
        "season",
    }

    file_stem_raw = source_file.stem.strip()
    file_stem_clean = re.sub(r"\{[^}]+\}", "", file_stem_raw).strip()
    file_stem_normalized = file_stem_clean.lower().replace("_", " ").replace(".", " ").strip()
    file_stem_is_generic = any(
        file_stem_normalized == generic
        or file_stem_normalized.startswith(f"{generic} ")
        for generic in generic_file_stems
    )

    parent_name = source_file.parent.name
    parent_clean = re.sub(r"\{[^}]+\}", "", parent_name).strip()

    grandparent_name = source_file.parent.parent.name if source_file.parent.parent else ""
    grandparent_clean = re.sub(r"\{[^}]+\}", "", grandparent_name).strip()

    candidate_title = file_stem_clean if file_stem_clean and not file_stem_is_generic else parent_clean
    if not candidate_title or candidate_title.lower() in {"assets", "posters", "custom"}:
        candidate_title = grandparent_clean or parent_clean or file_stem_clean or parent_name

    year_match = re.search(r"\((\d{4})\)", candidate_title)
    year_value = int(year_match.group(1)) if year_match else None
    title_value = re.sub(r"\(\d{4}\)", "", candidate_title).strip() or candidate_title
    title_value = re.sub(r"[_\.]+", " ", title_value).strip()

    ids: Dict[str, Optional[str]] = {"tmdb": None, "tvdb": None, "imdb": None}
    for source, value in POSTER_ID_PATTERN.findall(str(source_file)):
        key = source.strip().lower()
        parsed_value = value.strip()
        ids[key] = parsed_value.lower() if key == "imdb" else parsed_value

    season_number = extract_season_number_from_file_name(source_file.name)

    if season_number is not None or ids.get("tvdb"):
        media_type = "series"
    elif not year_value:
        media_type = "collection"
    else:
        media_type = "movie"

    return {
        "title": title_value,
        "year": year_value,
        "season_number": season_number,
        "media_type": media_type,
        "tmdb_id": int(ids["tmdb"]) if isinstance(ids.get("tmdb"), str) and str(ids["tmdb"]).isdigit() else None,
        "tvdb_id": int(ids["tvdb"]) if isinstance(ids.get("tvdb"), str) and str(ids["tvdb"]).isdigit() else None,
        "imdb_id": ids["imdb"],
    }


class PlexUploadRunRequest(BaseModel):
    """Payload for manual Plex upload runs."""
    dry_run: bool = False
    reapply: bool = False
    remove_overlay_label: bool = False


class PlexWebhookSettingsRequest(BaseModel):
    """Payload for Plex webhook settings."""
    enabled: bool
    remove_overlay_label: bool = False
    rename_then_upload: bool = False
    adopt_existing_processed: bool = False
    retry_attempts: int = PLEX_WEBHOOK_RETRY_ATTEMPTS
    retry_delay_seconds: int = PLEX_WEBHOOK_RETRY_DELAY_SECONDS


class PlexManualSettingsRequest(BaseModel):
    """Payload for persisted manual run options on the Plex Upload page."""
    dry_run: bool = True
    reapply: bool = False
    remove_overlay_label: bool = False


class PlexLibraryConfigItem(BaseModel):
    """Per-instance Plex library selection payload."""
    instance_name: str
    libraries: list[Dict[str, Any]]


class PlexLibraryOverrideSettingsRequest(BaseModel):
    """Payload for Plex Upload page-specific library override settings."""
    enabled: bool
    configs: list[PlexLibraryConfigItem] = []


class PlexUploadCacheClearRequest(BaseModel):
    """Payload for clearing Plex upload file cache."""
    file_path: Optional[str] = None


class PlexWebhookDedupeClearRequest(BaseModel):
    """Payload for clearing webhook duplicate suppression cache."""
    clear_all: bool = False
    source: Optional[str] = None
    event_type: Optional[str] = None
    media_type: Optional[str] = None
    title: Optional[str] = None
    year: Optional[int] = None
    season_number: Optional[int] = None


class PlexUploadSingleManualRequest(BaseModel):
    """Payload for manually uploading a single source poster target to Plex."""
    media_type: str
    title: str
    year: Optional[int] = None
    season_number: Optional[int] = None
    tmdb_id: Optional[int] = None
    tvdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    plex_rating_key: Optional[int] = None
    dry_run: bool = False
    reapply: bool = False
    remove_overlay_label: bool = False


@router.post("/plex-upload/run")
async def run_plex_upload(
    payload: Optional[PlexUploadRunRequest] = None,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Start manual Plex upload job in background.
    Returns immediately with job_id while upload executes asynchronously.
    """
    try:
        dry_run = payload.dry_run if payload else False
        reapply = payload.reapply if payload else False
        remove_overlay_label = payload.remove_overlay_label if payload else False

        job = create_job(
            db,
            job_type=JOB_TYPE_PLEX_UPLOAD,
            message=format_start_message(
                "Plex upload",
                dry_run=dry_run,
                qualifier="(reapply)" if reapply else None,
            ),
        )
        job_id = job.id

        log_job_queued(LogTags.UPLOADER, "Plex upload", job_id)

        job_queue.submit(
            run_plex_upload_background_job_module,
            job_id,
            job_id,
            dry_run,
            reapply,
            remove_overlay_label,
        )

        return job_started_response(
            job_id,
            "Plex upload queued in background. Monitor progress via job status.",
        )

    except HTTPException:
        raise
    except Exception as e:
        raise_internal_error("Failed to start plex upload", e)


@router.get("/plex-upload/source-search")
async def search_plex_upload_source_assets(
    q: str,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Search prioritized source poster records from database for single-poster Plex upload candidates."""
    try:
        query = q.strip()
        if not query:
            return {
                "query": "",
                "count": 0,
                "items": [],
            }

        safe_limit = max(1, min(limit, 300))
        raw_limit = min(safe_limit * 8, 4000)
        source_dir_context, source_dirs_error = load_priority_source_dir_context(db)
        if source_dirs_error:
            raise HTTPException(status_code=400, detail=source_dirs_error)

        allowed_drive_ids = [int(entry["drive_id"]) for entry in source_dir_context if isinstance(entry.get("drive_id"), int)]
        if not allowed_drive_ids:
            return {
                "query": query,
                "count": 0,
                "items": [],
            }

        source_roots: list[tuple[Path, str, str]] = []
        for entry in source_dir_context:
            raw_path = entry.get("path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            source_root: Path | None = None
            try:
                source_root = Path(raw_path).resolve()
            except Exception:
                source_root = None
            if source_root is None:
                continue
            source_roots.append(
                (
                    source_root,
                    str(entry.get("drive_name") or "Unknown"),
                    str(entry.get("drive_type") or "custom"),
                )
            )

        rows = (
            db.query(Poster, Drive)
            .join(Drive, Poster.drive_id == Drive.drive_id)
            .filter(
                Drive.id.in_(allowed_drive_ids),
                or_(
                    Poster.file_name.ilike(f"%{query}%"),
                    Poster.file_path.ilike(f"%{query}%"),
                ),
            )
            .order_by(Poster.file_path.asc())
            .limit(raw_limit)
            .all()
        )

        items: list[Dict[str, Any]] = []
        for poster, _drive in rows:
            source_file: Path | None = None
            try:
                source_file = Path(poster.file_path).resolve()
            except Exception:
                source_file = None
            if source_file is None:
                continue

            drive_name = "Unknown"
            drive_type = "custom"
            in_allowed_root = False
            for source_root, source_drive_name, source_drive_type in source_roots:
                is_within_source_root = False
                try:
                    source_file.relative_to(source_root)
                    is_within_source_root = True
                except Exception:
                    is_within_source_root = False
                if is_within_source_root:
                    in_allowed_root = True
                    drive_name = source_drive_name
                    drive_type = source_drive_type
                    break

            if not in_allowed_root:
                continue

            metadata = _extract_source_metadata(source_file)
            title_lower = str(metadata.get("title") or "").lower()
            file_name_lower = source_file.name.lower()
            if query.lower() not in title_lower and query.lower() not in file_name_lower:
                continue

            items.append(
                {
                    "media_type": metadata["media_type"],
                    "title": metadata["title"],
                    "year": metadata["year"],
                    "season_number": metadata["season_number"],
                    "tmdb_id": metadata["tmdb_id"],
                    "tvdb_id": metadata["tvdb_id"],
                    "imdb_id": metadata["imdb_id"],
                    "source_file": str(source_file),
                    "source_file_name": source_file.name,
                    "preview_url": f"/api/posterflow/plex-upload/source-image?path={quote(str(source_file), safe='')}",
                    "drive_name": drive_name,
                    "drive_type": drive_type,
                }
            )

        deduped: list[Dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for item in items:
            key = (
                item.get("media_type"),
                item.get("title"),
                item.get("year"),
                item.get("season_number"),
                item.get("tmdb_id"),
                item.get("tvdb_id"),
                item.get("imdb_id"),
                item.get("source_file"),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= safe_limit:
                break

        return {
            "query": query,
            "count": len(deduped),
            "items": deduped,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise_internal_error("Failed searching plex source assets", e)


@router.get("/plex-upload/source-image")
async def get_plex_upload_source_image(
    path: str,
    db: Session = Depends(get_db),
) -> FileResponse:
    """Stream a source poster image when it exists inside configured priority source folders."""
    source_path_raw = path.strip()
    if not source_path_raw:
        raise HTTPException(status_code=400, detail="path is required")

    source_dirs = get_required_source_dirs(db, "No source folders available for preview.")
    requested_path = resolve_authorized_source_image_path(source_path_raw, source_dirs)

    return FileResponse(str(requested_path))


@router.get("/plex-upload/plex-search")
async def search_plex_items(
    q: str,
    media_type: Optional[str] = None,
    limit: int = 25,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Search Plex directly for movies, shows, and collections matching the query."""
    try:
        from plexapi.server import PlexServer

        query = q.strip()
        if not query:
            return {"query": "", "count": 0, "items": []}

        safe_limit = max(1, min(limit, 100))

        plex_instances_setting = get_setting(db, 'plex_instances')
        if not plex_instances_setting or not plex_instances_setting.value:
            return {"query": query, "count": 0, "items": [], "error": "No Plex instances configured."}

        try:
            plex_instances = json.loads(plex_instances_setting.value)
        except Exception:
            return {"query": query, "count": 0, "items": [], "error": "Invalid Plex instances configuration."}

        # Plex libtype codes: 1=movie, 2=show, 18=collection
        mt = (media_type or "").strip().lower()
        if mt == "movie":
            libtypes = ["movie"]
        elif mt in {"series", "show"}:
            libtypes = ["show"]
        elif mt == "collection":
            libtypes = ["collection"]
        else:
            libtypes = ["movie", "show", "collection"]

        items: list[Dict[str, Any]] = []
        seen_keys: set[str] = set()

        for instance in plex_instances:
            if not isinstance(instance, dict):
                continue
            url = str(instance.get("url", "")).strip()
            api_key = str(instance.get("api_key", "")).strip()
            instance_name = str(instance.get("name", "Plex")).strip()
            if not url or not api_key:
                continue

            try:
                plex = PlexServer(url, api_key)
            except Exception as e:
                log_error(LogTags.UPLOADER, f"Plex search: failed to connect to '{instance_name}': {e}")
                continue

            for libtype in libtypes:
                try:
                    results = plex.search(query, mediatype=libtype, limit=safe_limit)
                except Exception as e:
                    log_error(LogTags.UPLOADER, f"Plex search: error searching '{libtype}' on '{instance_name}': {e}")
                    continue

                for result in results:
                    rating_key = getattr(result, "ratingKey", None)
                    item_key = f"{instance_name}:{rating_key}"
                    if item_key in seen_keys:
                        continue
                    seen_keys.add(item_key)

                    item_type = str(getattr(result, "type", libtype)).lower()
                    if item_type == "movie":
                        mapped_type = "movie"
                    elif item_type == "show":
                        mapped_type = "series"
                    elif item_type == "collection":
                        mapped_type = "collection"
                    else:
                        mapped_type = item_type

                    title = str(getattr(result, "title", ""))
                    year = getattr(result, "year", None)
                    library_name = str(getattr(result, "librarySectionTitle", ""))

                    # Extract guids for ID-based matching
                    tmdb_id: Optional[int] = None
                    tvdb_id: Optional[int] = None
                    imdb_id: Optional[str] = None
                    raw_guids = getattr(result, "guids", None) or []
                    for guid in raw_guids:
                        guid_str = str(getattr(guid, "id", guid)).strip().lower()
                        if "://" in guid_str:
                            src, val = guid_str.split("://", 1)
                        elif ":" in guid_str:
                            src, val = guid_str.split(":", 1)
                        else:
                            continue
                        if src == "tmdb" and val.isdigit():
                            tmdb_id = int(val)
                        elif src == "tvdb" and val.isdigit():
                            tvdb_id = int(val)
                        elif src == "imdb":
                            imdb_id = val

                    thumb = getattr(result, "thumb", None)
                    thumb_url = f"/api/posterflow/plex-upload/plex-thumb?instance={instance_name}&key={quote(str(thumb), safe='')}" if thumb else None

                    items.append({
                        "media_type": mapped_type,
                        "title": title,
                        "year": year,
                        "tmdb_id": tmdb_id,
                        "tvdb_id": tvdb_id,
                        "imdb_id": imdb_id,
                        "plex_rating_key": rating_key,
                        "plex_instance": instance_name,
                        "library_name": library_name,
                        "thumb_url": thumb_url,
                    })

                    if len(items) >= safe_limit:
                        break

                if len(items) >= safe_limit:
                    break

        return {"query": query, "count": len(items), "items": items}

    except HTTPException:
        raise
    except Exception as e:
        raise_internal_error("Failed to search Plex", e)


@router.get("/plex-upload/plex-thumb")
async def get_plex_thumb(
    instance: str,
    key: str,
    db: Session = Depends(get_db),
) -> Response:
    """Proxy a Plex thumbnail image through the backend to avoid CORS issues."""
    try:
        import requests as _requests
        from plexapi.server import PlexServer

        plex_instances_setting = get_setting(db, 'plex_instances')
        if not plex_instances_setting or not plex_instances_setting.value:
            raise HTTPException(status_code=404, detail="No Plex instances configured.")

        plex_cfg = next(
            (i for i in json.loads(plex_instances_setting.value) if i.get("name") == instance),
            None,
        )
        if not plex_cfg:
            raise HTTPException(status_code=404, detail="Plex instance not found.")

        url = str(plex_cfg["url"]).rstrip("/")
        token = str(plex_cfg["api_key"])

        # Validate key to prevent SSRF: must be a relative path starting with /
        # No scheme separators, credential separators, or control characters allowed
        if not key.startswith("/") or any(c in key for c in ("@", "://", "\n", "\r", "\x00")):
            raise HTTPException(status_code=400, detail="Invalid thumbnail key.")

        thumb_url = f"{url}{key}?X-Plex-Token={token}"

        resp = _requests.get(thumb_url, timeout=10)
        if resp.status_code != 200:
            raise HTTPException(status_code=404, detail="Thumbnail not found.")

        return Response(
            content=resp.content,
            media_type=resp.headers.get("content-type", "image/jpeg"),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise_internal_error("Failed to fetch Plex thumbnail", e)


@router.post("/plex-upload/run-single")
async def run_plex_single_manual_upload(
    payload: PlexUploadSingleManualRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Queue a manual single-poster Plex upload, including webhook-style rename/border prep."""
    try:
        _media_type, _title, run_payload, item_label = normalize_single_manual_payload(payload)
        get_required_source_dirs(db, "No source folders available for single upload.")

        job = create_job(
            db,
            job_type=JOB_TYPE_PLEX_UPLOAD_SINGLE,
            message=f"Queued manual single upload for {item_label}",
        )
        job_id = job.id

        log_job_queued(LogTags.UPLOADER, "Plex single upload", job_id)
        job_queue.submit(
            run_plex_single_manual_background_job_module,
            job_id,
            job_id,
            run_payload,
        )

        return job_started_response(
            job_id,
            f"Manual single upload queued for {item_label}.",
        )

    except HTTPException:
        raise
    except Exception as e:
        raise_internal_error("Failed to start plex single upload", e)


@router.post("/plex-upload/webhook")
async def handle_plex_upload_webhook(
    payload: Dict[str, Any],
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Handle ARR webhook and queue single-item Plex upload.
    This endpoint is intended for Radarr/Sonarr webhook payloads.
    """
    try:
        increment_webhook_stat(db, "received")

        if not is_webhook_enabled(db):
            increment_webhook_stat(db, "rejected_disabled", last_error="Webhook disabled")
            raise HTTPException(status_code=403, detail="Plex webhook is disabled. Enable it in Plex Upload settings.")

        parsed = parse_arr_webhook_payload(payload)

        if parsed.get("skip"):
            increment_webhook_stat(db, "skipped_test")
            return {
                "success": True,
                "queued": False,
                "message": parsed.get("reason", "Webhook ignored"),
            }

        if is_duplicate_webhook_event(db, parsed):
            log_info(
                LogTags.UPLOADER,
                f"Skipping duplicate webhook event for {parsed.get('media_type')}: {parsed.get('title')}",
                event_type=parsed.get("event_type"),
                source=parsed.get("source"),
            )
            increment_webhook_stat(db, "duplicates")
            return {
                "success": True,
                "queued": False,
                "duplicate": True,
                "message": "Duplicate webhook event ignored.",
            }

        media_label = f"{parsed.get('media_type')}: {parsed.get('title')}"
        job = create_job(
            db,
            job_type=JOB_TYPE_PLEX_UPLOAD_WEBHOOK,
            message=f"Queued webhook upload for {media_label}",
        )
        job_id = job.id

        log_job_queued(LogTags.UPLOADER, "Plex webhook upload", job_id)
        job_queue.submit(
            run_plex_webhook_background_job_module,
            job_id,
            job_id,
            parsed,
            is_webhook_remove_overlay_label_enabled(db),
            is_webhook_rename_then_upload_enabled(db),
            get_webhook_retry_attempts(db),
            get_webhook_retry_delay_seconds(db),
        )
        increment_webhook_stat(db, "queued", queued=True)

        return {
            "success": True,
            "queued": True,
            "job_id": job_id,
            "message": f"Webhook upload queued for {media_label}.",
        }

    except ValueError as e:
        increment_webhook_stat(db, "parse_errors", last_error=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        increment_webhook_stat(db, "internal_errors", last_error=str(e))
        raise_internal_error("Failed processing plex upload webhook", e)


@router.get("/plex-upload/webhook-settings")
async def get_plex_webhook_settings(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Get webhook settings for Plex auto-upload endpoint."""
    try:
        return build_webhook_settings_response(db)
    except Exception as e:
        raise_internal_error("Failed to get plex webhook settings", e)


@router.get("/plex-upload/manual-settings")
async def get_plex_manual_settings(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Get persisted manual run options for the Plex Upload page."""
    try:
        return build_manual_settings_response(db)
    except Exception as e:
        raise_internal_error("Failed to get plex manual settings", e)


@router.post("/plex-upload/manual-settings")
async def save_plex_manual_settings(
    payload: PlexManualSettingsRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Save persisted manual run options for the Plex Upload page."""
    try:
        upsert_setting(db, SETTING_PLEX_UPLOAD_MANUAL_DRY_RUN, bool_to_setting(payload.dry_run))
        upsert_setting(db, SETTING_PLEX_UPLOAD_MANUAL_REAPPLY, bool_to_setting(payload.reapply))
        upsert_setting(
            db,
            SETTING_PLEX_UPLOAD_MANUAL_REMOVE_OVERLAY_LABEL,
            bool_to_setting(payload.remove_overlay_label),
        )
        db.commit()

        return {
            "success": True,
            **build_manual_settings_response(db),
        }
    except Exception as e:
        db.rollback()
        raise_internal_error("Failed to save plex manual settings", e)


@router.post("/plex-upload/webhook-settings")
async def save_plex_webhook_settings(
    payload: PlexWebhookSettingsRequest,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Save webhook settings for Plex auto-upload endpoint."""
    try:
        upsert_setting(db, SETTING_PLEX_WEBHOOK_ENABLED, bool_to_setting(payload.enabled))
        upsert_setting(
            db,
            SETTING_PLEX_WEBHOOK_REMOVE_OVERLAY_LABEL,
            bool_to_setting(payload.remove_overlay_label),
        )
        upsert_setting(
            db,
            SETTING_PLEX_WEBHOOK_RENAME_THEN_UPLOAD,
            bool_to_setting(payload.rename_then_upload),
        )
        upsert_setting(
            db,
            SETTING_PLEX_WEBHOOK_ADOPT_EXISTING_PROCESSED,
            bool_to_setting(payload.adopt_existing_processed),
        )
        upsert_setting(
            db,
            SETTING_PLEX_WEBHOOK_RETRY_ATTEMPTS,
            str(
                coerce_webhook_retry_int(
                    payload.retry_attempts,
                    default=PLEX_WEBHOOK_RETRY_ATTEMPTS,
                    minimum=1,
                    maximum=PLEX_WEBHOOK_RETRY_ATTEMPTS_MAX,
                )
            ),
        )
        upsert_setting(
            db,
            SETTING_PLEX_WEBHOOK_RETRY_DELAY_SECONDS,
            str(
                coerce_webhook_retry_int(
                    payload.retry_delay_seconds,
                    default=PLEX_WEBHOOK_RETRY_DELAY_SECONDS,
                    minimum=1,
                    maximum=PLEX_WEBHOOK_RETRY_DELAY_SECONDS_MAX,
                )
            ),
        )

        db.commit()

        return {
            "success": True,
            **build_webhook_settings_response(db),
        }
    except Exception as e:
        db.rollback()
        raise_internal_error("Failed to save plex webhook settings", e)


@router.get("/plex-upload/webhook-stats")
async def get_plex_webhook_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Get webhook processing stats for Plex auto-upload endpoint."""
    try:
        return load_webhook_stats(db)
    except Exception as e:
        raise_internal_error("Failed to get plex webhook stats", e)


@router.post("/plex-upload/webhook-stats/reset")
async def reset_plex_webhook_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Reset webhook processing stats back to default values."""
    try:
        stats = reset_webhook_stats(db)
        return {
            "success": True,
            **stats,
        }
    except Exception as e:
        db.rollback()
        raise_internal_error("Failed to reset plex webhook stats", e)


@router.post("/plex-upload/webhook-dedupe/clear")
async def clear_plex_webhook_dedupe(
    payload: Optional[PlexWebhookDedupeClearRequest] = None,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Clear webhook duplicate suppression cache globally or for one target item."""
    try:
        clear_all = bool(payload.clear_all) if payload else False
        source = payload.source if payload else None
        event_type = payload.event_type if payload else None
        media_type = payload.media_type.strip().lower() if payload and payload.media_type else None
        title = payload.title.strip() if payload and payload.title else None
        year = payload.year if payload else None
        season_number = payload.season_number if payload else None

        if media_type and media_type not in {"movie", "series"}:
            raise HTTPException(status_code=400, detail="media_type must be 'movie' or 'series'.")

        if not clear_all and not any([source, event_type, media_type, title, year is not None, season_number is not None]):
            raise HTTPException(
                status_code=400,
                detail="Provide clear_all=true or at least one filter (title/media_type/year/season/source/event_type).",
            )

        return clear_webhook_dedupe_cache(
            db,
            clear_all=clear_all,
            source=source,
            event_type=event_type,
            media_type=media_type,
            title=title,
            year=year,
            season_number=season_number,
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise_internal_error("Failed to clear webhook dedupe cache", e)


@router.get("/plex-upload/webhook-dedupe/entries")
async def get_plex_webhook_dedupe_entries(
    q: str = "",
    media_type: Optional[str] = None,
    limit: int = 25,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Search active webhook duplicate suppression lock entries from database cache."""
    try:
        normalized_media_type = media_type.strip().lower() if isinstance(media_type, str) and media_type.strip() else None
        if normalized_media_type and normalized_media_type not in {"movie", "series"}:
            raise HTTPException(status_code=400, detail="media_type must be 'movie' or 'series'.")

        return search_webhook_dedupe_entries(
            db,
            query=q,
            media_type=normalized_media_type,
            limit=limit,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise_internal_error("Failed to search webhook dedupe entries", e)


@router.get("/plex-upload/library-override")
async def get_plex_upload_library_override_settings(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Get Plex Upload page-specific library override settings and global library config."""
    try:
        override = load_plex_upload_library_override(db)
        return build_library_override_response(db, override)
    except Exception as e:
        raise_internal_error("Failed to get plex upload library override settings", e)


@router.post("/plex-upload/library-override")
async def save_plex_upload_library_override_settings(
    payload: PlexLibraryOverrideSettingsRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Save Plex Upload page-specific library override settings."""
    try:
        normalized_payload = {
            "enabled": payload.enabled,
            "configs": [item.model_dump() for item in payload.configs],
        }
        upsert_setting(db, SETTING_PLEX_UPLOAD_LIBRARY_OVERRIDE, json.dumps(normalized_payload))
        db.commit()

        return {
            "success": True,
            **build_library_override_response(db, normalized_payload),
        }
    except Exception as e:
        db.rollback()
        raise_internal_error("Failed to save plex upload library override settings", e)


@router.get("/plex-upload/upload-cache")
async def get_plex_upload_cache(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Get persisted per-file Plex upload cache used for skip logic."""
    try:
        return fast_cache_summary(db)
    except Exception as e:
        raise_internal_error("Failed to get plex upload file cache", e)


@router.get("/plex-upload/upload-cache/entries")
async def get_plex_upload_cache_entries(
    q: str = "",
    limit: int = 25,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Get a paginated, optionally filtered view of persisted Plex upload cache entries."""
    try:
        safe_limit = max(1, min(limit, 200))
        safe_offset = max(0, offset)
        query = q.strip().lower()

        cache = load_plex_upload_file_cache(db)
        summary = cache_summary(cache)
        entries = summary.get("entries", [])

        if query:
            filtered_entries = []
            for entry in entries:
                file_path = str(entry.get("file_path", ""))
                libraries = [str(item) for item in entry.get("uploaded_to_libraries", [])]
                editions = [str(item) for item in entry.get("uploaded_editions", [])]
                media_types = [str(item) for item in entry.get("uploaded_media_types", [])]
                haystack = " ".join([file_path, *libraries, *editions, *media_types]).lower()
                if query in haystack:
                    filtered_entries.append(entry)
        else:
            filtered_entries = entries

        total = len(filtered_entries)
        page_entries = filtered_entries[safe_offset:safe_offset + safe_limit]

        return {
            "query": q,
            "total": total,
            "offset": safe_offset,
            "limit": safe_limit,
            "has_more": safe_offset + len(page_entries) < total,
            "entries": page_entries,
        }
    except Exception as e:
        raise_internal_error("Failed to get paginated plex upload file cache entries", e)


@router.post("/plex-upload/upload-cache/clear")
async def clear_plex_upload_cache(
    payload: Optional[PlexUploadCacheClearRequest] = None,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Clear persisted per-file Plex upload cache entirely or for one file path."""
    try:
        from models.plex_upload import PlexUploadRecord
        target_file = payload.file_path.strip() if payload and payload.file_path else None

        removed = 0
        if target_file:
            removed = db.query(PlexUploadRecord).filter(PlexUploadRecord.file_path == target_file).delete()
        else:
            removed = db.query(PlexUploadRecord).count()
            db.query(PlexUploadRecord).delete()
        db.commit()

        cache = load_plex_upload_file_cache(db)
        return build_cache_clear_response(
            cache,
            removed=removed,
            cleared_file_path=target_file,
        )
    except Exception as e:
        db.rollback()
        raise_internal_error("Failed to clear plex upload file cache", e)


@router.get("/plex-upload/upload-cache/export")
async def export_plex_upload_cache(db: Session = Depends(get_db)) -> Response:
    """Export persisted Plex upload cache as downloadable JSON."""
    try:
        cache = load_plex_upload_file_cache(db)
        payload = build_cache_export_payload(cache)
        pretty_json = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        return Response(
            content=pretty_json,
            media_type="application/json",
            headers={
                "Content-Disposition": "attachment; filename=plex_upload_cache_export.json"
            },
        )
    except Exception as e:
        raise_internal_error("Failed to export plex upload file cache", e)
