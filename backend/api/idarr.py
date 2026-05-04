import csv
import json
import re
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Literal
from urllib.parse import quote
from uuid import uuid4

import requests
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from core.config import settings as app_settings
from core.logging import LogTags, log_debug, log_error, log_user_action, log_warning
from database import get_db
from models.idarr import IdarrAssetCache, IdarrPendingMatch, IdarrRun, upsert_idarr_asset_cache, upsert_idarr_pending_match, make_pending_entry_payload, resolve_idarr_scope_token, normalize_idarr_asset_type, build_idarr_asset_key
from models.setting import get_setting, upsert_setting
from util.data.normalization import normalize_titles

router = APIRouter(prefix="/api/idarr", tags=["idarr"])

SETTING_MAKER_IDARR_CONFIG = "maker_tools_idarr_config"
SETTING_MAKER_IDARR_IGNORED_TITLES = "maker_tools_idarr_ignored_titles"
IDARR_UPLOAD_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class MakerIdarrConfig(BaseModel):
    sync_targets: list[dict[str, str]] = Field(default_factory=list)
    tmdb_api_key: str = ""
    auto_rename_quick_add: bool = True
    auto_upload_quick_add: bool = False
    remove_non_image_files: bool = False
    show_unmatched: bool = False
    pending_matches: bool = False
    skip_collections: bool = False
    limit: int | None = None
    frequency_days: int = 30
    tvdb_frequency: int = 7


class IdarrPendingResolveRequest(BaseModel):
    asset_key: str
    action: Literal["resolve", "dismiss", "ignore"] = "resolve"
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    imdb_id: str | None = None
    tmdb_input: str | None = None
    sync_target_index: int | None = None


class IdarrCacheMaintenanceRequest(BaseModel):
    action: Literal["clear_all", "prune_unmatched", "purge_stale", "prune_targeted"]
    days: int | None = None
    title: str | None = None
    asset_key: str | None = None
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    imdb_id: str | None = None
    sync_target_index: int | None = None


class IdarrExportRequest(BaseModel):
    output_dir: str | None = None
    sync_target_index: int | None = None


class IdarrRevertRequest(BaseModel):
    dry_run: bool = False
    sync_target_index: int | None = None


class IdarrIgnoredTitleRequest(BaseModel):
    title: str
    year: int | None = None
    type: str
    asset_key: str | None = None
    sync_target_index: int | None = None


class IdarrIgnoredTitlesBulkRequest(BaseModel):
    titles: list[str] = Field(default_factory=list)
    sync_target_index: int | None = None


class IdarrPendingCandidatesRequest(BaseModel):
    asset_key: str | None = None
    title: str
    year: int | None = None
    type: str
    sync_target_index: int | None = None


class IdarrPendingCandidateReviewRequest(BaseModel):
    asset_key: str
    tmdb_id: int
    action: Literal["accept", "reject", "clear"]
    note: str | None = None
    sync_target_index: int | None = None


def env_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return default


def _parse_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except Exception:
        return default


def _parse_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return int(value)
    except Exception:
        return None


def _sanitize_maker_idarr_config(payload: Any) -> MakerIdarrConfig:
    defaults = MakerIdarrConfig()
    if not isinstance(payload, dict):
        return defaults

    data: dict[str, Any] = {}

    for key in ("tmdb_api_key",):
        if key in payload and payload.get(key) is not None:
            data[key] = str(payload.get(key))

    sync_targets: list[dict[str, str]] = []
    raw_targets = payload.get("sync_targets")
    if isinstance(raw_targets, list):
        for index, item in enumerate(raw_targets):
            if not isinstance(item, dict):
                continue
            personal_drive_id = str(item.get("personal_drive_id") or "").strip()
            source_dir = str(item.get("source_dir") or "").strip()
            label = str(item.get("label") or "").strip()
            scope_token = resolve_idarr_scope_token(item, index)
            if not personal_drive_id and not source_dir:
                continue
            normalized_target: dict[str, str] = {
                "personal_drive_id": personal_drive_id,
                "source_dir": source_dir,
            }
            if label:
                normalized_target["label"] = label
            if scope_token:
                normalized_target["scope_token"] = scope_token
            sync_targets.append(normalized_target)

    data["sync_targets"] = sync_targets

    for key in ("auto_rename_quick_add", "auto_upload_quick_add", "remove_non_image_files", "show_unmatched", "pending_matches", "skip_collections"):
        if key in payload:
            data[key] = env_bool(payload.get(key), getattr(defaults, key))

    if "limit" in payload:
        data["limit"] = _parse_optional_int(payload.get("limit"))

    data["frequency_days"] = _parse_positive_int(payload.get("frequency_days"), defaults.frequency_days)
    data["tvdb_frequency"] = _parse_positive_int(payload.get("tvdb_frequency"), defaults.tvdb_frequency)

    return MakerIdarrConfig(**{**defaults.model_dump(), **data})


def _extract_cached_candidates(cache_row: IdarrAssetCache | None) -> list[dict[str, Any]]:
    if not cache_row or not cache_row.payload_json:
        return []
    try:
        payload = json.loads(cache_row.payload_json)
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    candidates = payload.get("candidate_results")
    if not isinstance(candidates, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for candidate in candidates[:10]:
        if isinstance(candidate, dict) and isinstance(candidate.get("tmdb_id"), int):
            cleaned.append(candidate)
    return cleaned


def _extract_resolution_history(cache_row: IdarrAssetCache | None) -> list[dict[str, Any]]:
    if not cache_row or not cache_row.payload_json:
        return []
    try:
        payload = json.loads(cache_row.payload_json)
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    history = payload.get("resolution_history")
    if not isinstance(history, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in history[:20]:
        if isinstance(item, dict):
            cleaned.append(item)
    return cleaned


def _extract_candidate_reviews(cache_row: IdarrAssetCache | None) -> dict[str, dict[str, Any]]:
    payload = _extract_payload_dict(cache_row)
    reviews = payload.get("candidate_reviews")
    if not isinstance(reviews, dict):
        return {}
    cleaned: dict[str, dict[str, Any]] = {}
    for key, value in reviews.items():
        if isinstance(key, str) and isinstance(value, dict):
            cleaned[key] = value
    return cleaned


def _extract_payload_dict(cache_row: IdarrAssetCache | None) -> dict[str, Any]:
    if not cache_row or not cache_row.payload_json:
        return {}
    try:
        payload = json.loads(cache_row.payload_json)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _candidate_reason(
    requested_title: str,
    requested_year: int | None,
    candidate_title: str,
    candidate_year: int | None,
) -> dict[str, Any]:
    requested_norm = normalize_titles(requested_title)
    candidate_norm = normalize_titles(candidate_title)
    exact_title = bool(requested_norm and requested_norm == candidate_norm)
    year_match = bool(
        isinstance(requested_year, int)
        and isinstance(candidate_year, int)
        and requested_year == candidate_year
    )
    title_contains = bool(
        requested_norm and candidate_norm
        and (requested_norm in candidate_norm or candidate_norm in requested_norm)
    )

    score = 0
    if exact_title:
        score += 70
    elif title_contains:
        score += 40
    if year_match:
        score += 30

    reasons: list[str] = []
    if exact_title:
        reasons.append("exact_title")
    elif title_contains:
        reasons.append("title_close")
    if year_match:
        reasons.append("year_match")
    if not reasons:
        reasons.append("tmdb_rank")

    return {
        "score": score,
        "reasons": reasons,
        "summary": " + ".join(reasons),
    }


def _idarr_asset_key(asset_type: str, title: str, year: int | None, scope_token: str | None = None) -> str:
    """Thin wrapper — delegates to the canonical ``build_idarr_asset_key`` in models."""
    return build_idarr_asset_key(asset_type, title, year, scope_token)


def _asset_key_in_scope(asset_key: str | None, scope_token: str | None) -> bool:
    key = str(asset_key or "").strip()
    if not key:
        return False
    if scope_token:
        return key.endswith(f"::scope={scope_token}")
    return "::scope=" not in key


def _resolve_scope_context(
    db: Session,
    sync_target_index: int | None,
) -> tuple[str | None, str | None]:
    """Return ``(scope_token, source_dir)`` for a sync target in a single config load.

    Raises ``HTTPException(400)`` when ``sync_target_index`` is None or out of range.
    Returns ``(None, None)`` when no sync targets are configured.
    """
    if sync_target_index is None:
        raise HTTPException(status_code=400, detail="sync_target_index is required")

    config = load_runtime_config(db)
    raw_targets = config.sync_targets if isinstance(config.sync_targets, list) else []
    sync_targets = [item for item in raw_targets if isinstance(item, dict)]
    if not sync_targets:
        return None, None

    index = int(sync_target_index)
    if index < 0 or index >= len(sync_targets):
        raise HTTPException(
            status_code=400,
            detail=f"sync_target_index must be between 0 and {max(len(sync_targets) - 1, 0)}",
        )

    target = sync_targets[index]
    scope_token = resolve_idarr_scope_token(target, index)
    source_dir = str(target.get("source_dir") or "").strip() or None
    return scope_token, source_dir


def _resolve_scope_token(db: Session, sync_target_index: int | None) -> str | None:
    """Return only the scope token for a sync target. Prefer ``_resolve_scope_context``
    when both scope token and source dir are needed."""
    scope_token, _ = _resolve_scope_context(db, sync_target_index)
    return scope_token


def _apply_idarr_run_scope_filter(run_query: Any, scope_token: str | None, source_dir: str | None) -> Any:
    """Apply scope-aware WHERE clauses to a query against ``IdarrRun``.

    Handles the legacy case where old runs carry ``source_dir`` but no ``scope_token``.
    """
    if scope_token:
        if source_dir:
            return run_query.filter(
                or_(
                    IdarrRun.scope_token == scope_token,
                    and_(IdarrRun.scope_token.is_(None), IdarrRun.source_dir == source_dir),
                )
            )
        return run_query.filter(IdarrRun.scope_token == scope_token)
    if source_dir:
        return run_query.filter(IdarrRun.source_dir == source_dir)
    return run_query


def _filter_cache_query_by_scope(query: Any, scope_token: str | None):
    if scope_token:
        return query.filter(IdarrAssetCache.asset_key.like(f"%::scope={scope_token}"))
    return query.filter(~IdarrAssetCache.asset_key.like("%::scope=%"))


def _filter_pending_query_by_scope(query: Any, scope_token: str | None):
    if scope_token:
        return query.filter(IdarrPendingMatch.asset_key.like(f"%::scope={scope_token}"))
    return query.filter(~IdarrPendingMatch.asset_key.like("%::scope=%"))


def _extract_payload_filenames(cache_row: IdarrAssetCache) -> set[str]:
    filenames: set[str] = set()
    if not isinstance(cache_row.payload_json, str) or not cache_row.payload_json.strip():
        return filenames

    try:
        payload = json.loads(cache_row.payload_json)
    except Exception:
        return filenames

    if not isinstance(payload, dict):
        return filenames

    for field_name in ("current_filenames", "original_filenames"):
        field_value = payload.get(field_name)
        if isinstance(field_value, list):
            for item in field_value:
                if isinstance(item, str) and item.strip():
                    filenames.add(Path(item.strip()).name)

    pending_entry = payload.get("pending_entry")
    if isinstance(pending_entry, dict):
        pending_file = pending_entry.get("files")
        if isinstance(pending_file, str) and pending_file.strip():
            filenames.add(Path(pending_file.strip()).name)

    return filenames


def _normalize_idarr_asset_type(asset_type: str | None) -> str:
    """Return the canonical Idarr asset type, falling back to the raw lowercased value."""
    return normalize_idarr_asset_type(asset_type) or str(asset_type or "").strip().lower()


def _normalize_pending_entry_payload(entry: Any, *, title: str, year: int | None, files: Any) -> dict[str, str]:
    normalized = make_pending_entry_payload(title=title, year=year, files=files)
    if not isinstance(entry, dict):
        return normalized

    add_value = entry.get("add_tmdb_url_here")
    if isinstance(add_value, str):
        trimmed = add_value.strip()
        if trimmed == "ignore" or trimmed.startswith("https://"):
            normalized["add_tmdb_url_here"] = trimmed

    if isinstance(entry.get("google_search"), str) and entry.get("google_search").strip():
        normalized["google_search"] = entry.get("google_search").strip()

    if "files" in entry:
        file_value = entry.get("files")
        if isinstance(file_value, str) and file_value.strip():
            normalized["files"] = str(Path(file_value.strip()).resolve())

    return normalized


def _extract_pending_reason(cache_payload: dict[str, Any], pending_entry: dict[str, str]) -> str | None:
    raw_reason = cache_payload.get("pending_reason") if isinstance(cache_payload, dict) else None
    if not isinstance(raw_reason, str) and isinstance(pending_entry, dict):
        raw_reason = pending_entry.get("reason")

    if not isinstance(raw_reason, str):
        return None

    normalized = raw_reason.strip().lower()
    if not normalized:
        return None

    if normalized in {"rename_conflict", "in_place_conflict_kept_existing"}:
        return "rename_conflict"

    if normalized in {"low_confidence_alternate", "review_required_low_confidence_alternate"}:
        return "low_confidence_alternate"

    return normalized


def _parse_tmdb_input(value: str | None) -> tuple[int | None, str | None]:
    raw = str(value or "").strip()
    if not raw:
        return None, None

    inferred_type: str | None = None
    lowered = raw.lower()
    if "/tv/" in lowered:
        inferred_type = "tv_series"
    elif "/movie/" in lowered:
        inferred_type = "movie"
    elif "/collection/" in lowered:
        inferred_type = "collection"

    match = re.search(r"/(tv|movie|collection)/(\d+)", lowered)
    if match:
        tmdb_id = int(match.group(2))
        if match.group(1) == "tv":
            inferred_type = "tv_series"
        elif match.group(1) == "movie":
            inferred_type = "movie"
        elif match.group(1) == "collection":
            inferred_type = "collection"
        return tmdb_id, inferred_type

    numeric = re.search(r"(\d+)", raw)
    if numeric:
        return int(numeric.group(1)), inferred_type

    return None, inferred_type


def _load_ignored_titles_raw(db: Session) -> list[dict[str, Any]]:
    setting = get_setting(db, SETTING_MAKER_IDARR_IGNORED_TITLES)
    if not setting or not setting.value:
        return []
    try:
        payload = json.loads(setting.value)
        return payload if isinstance(payload, list) else []
    except Exception:
        return []


def _load_ignored_titles(db: Session, scope_token: str | None = None) -> list[dict[str, Any]]:
    payload = _load_ignored_titles_raw(db)
    if scope_token is None:
        return payload
    return [
        item
        for item in payload
        if isinstance(item, dict) and _asset_key_in_scope(item.get("asset_key"), scope_token)
    ]


def _save_ignored_titles(db: Session, items: list[dict[str, Any]], scope_token: str | None = None) -> None:
    if scope_token is None:
        merged_items = items
    else:
        existing_items = _load_ignored_titles_raw(db)
        preserved_items = [
            item
            for item in existing_items
            if isinstance(item, dict) and not _asset_key_in_scope(item.get("asset_key"), scope_token)
        ]
        merged_items = preserved_items + [item for item in items if isinstance(item, dict)]

    upsert_setting(db, SETTING_MAKER_IDARR_IGNORED_TITLES, json.dumps(merged_items))
    db.commit()


def _parse_ignored_title_with_optional_year(value: str) -> tuple[str, int | None]:
    raw = str(value or "").strip()
    if not raw:
        return "", None
    year_match = re.match(r"^(.*)\((\d{4})\)$", raw)
    if not year_match:
        return raw, None
    normalized_title = str(year_match.group(1) or "").strip()
    try:
        parsed_year = int(year_match.group(2))
    except Exception:
        parsed_year = None
    return normalized_title, parsed_year


def _build_ignored_entry(*, title: str, year: int | None, asset_type: str, asset_key: str | None = None, scope_token: str | None = None) -> dict[str, Any]:
    normalized_title = str(title or "").strip()
    normalized_type = _normalize_idarr_asset_type(asset_type)
    normalized_year = year if isinstance(year, int) else None
    key = str(asset_key).strip() if isinstance(asset_key, str) and str(asset_key).strip() else _idarr_asset_key(normalized_type, normalized_title, normalized_year, scope_token)
    return {
        "asset_key": key,
        "title": normalized_title,
        "year": normalized_year,
        "type": normalized_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _resolve_ignored_entries_for_title(db: Session, raw_title: str, scope_token: str | None = None) -> list[dict[str, Any]]:
    normalized_title, parsed_year = _parse_ignored_title_with_optional_year(raw_title)
    if not normalized_title:
        return []

    title_lower = normalized_title.lower()

    cache_query = _filter_cache_query_by_scope(
        db.query(IdarrAssetCache).filter(func.lower(IdarrAssetCache.title) == title_lower),
        scope_token,
    )
    pending_query = _filter_pending_query_by_scope(
        db.query(IdarrPendingMatch).filter(func.lower(IdarrPendingMatch.title) == title_lower),
        scope_token,
    )

    if isinstance(parsed_year, int):
        cache_query = cache_query.filter(IdarrAssetCache.year == parsed_year)
        pending_query = pending_query.filter(IdarrPendingMatch.year == parsed_year)

    resolved_entries: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for row in cache_query.all():
        row_title = str(row.title or "").strip() or normalized_title
        row_year = row.year if isinstance(row.year, int) else parsed_year
        row_type = _normalize_idarr_asset_type(row.asset_type)
        entry = _build_ignored_entry(
            title=row_title,
            year=row_year,
            asset_type=row_type,
            asset_key=str(row.asset_key or "").strip() or None,
            scope_token=scope_token,
        )
        key = str(entry.get("asset_key") or "").strip()
        if key and key not in seen_keys:
            seen_keys.add(key)
            resolved_entries.append(entry)

    for row in pending_query.all():
        row_title = str(row.title or "").strip() or normalized_title
        row_year = row.year if isinstance(row.year, int) else parsed_year
        row_type = _normalize_idarr_asset_type(row.asset_type)
        entry = _build_ignored_entry(
            title=row_title,
            year=row_year,
            asset_type=row_type,
            asset_key=str(row.asset_key or "").strip() or None,
            scope_token=scope_token,
        )
        key = str(entry.get("asset_key") or "").strip()
        if key and key not in seen_keys:
            seen_keys.add(key)
            resolved_entries.append(entry)

    if resolved_entries:
        return resolved_entries

    if isinstance(parsed_year, int):
        return [
            _build_ignored_entry(title=normalized_title, year=parsed_year, asset_type="movie", scope_token=scope_token),
        ]

    return [_build_ignored_entry(title=normalized_title, year=None, asset_type="collection", scope_token=scope_token)]


def _normalize_bulk_ignored_titles(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        title = str(value or "").strip()
        if not title:
            continue
        dedupe_key = title.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(title)
    return normalized


def _merge_ignored_items(existing_items: list[dict[str, Any]], incoming_items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    merged = [item for item in existing_items if isinstance(item, dict)]
    existing_keys = {
        str(item.get("asset_key") or "").strip()
        for item in merged
        if isinstance(item, dict) and str(item.get("asset_key") or "").strip()
    }
    added = 0
    for item in incoming_items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("asset_key") or "").strip()
        if not key or key in existing_keys:
            continue
        merged.append(item)
        existing_keys.add(key)
        added += 1
    return merged, added


def _get_maker_idarr_tmdb_key(db: Session) -> str:
    setting = get_setting(db, "tmdb_api_key")
    return str(setting.value or "").strip() if setting else ""


def load_runtime_config(db: Session) -> MakerIdarrConfig:
    setting = get_setting(db, SETTING_MAKER_IDARR_CONFIG)
    if not setting or not setting.value:
        return MakerIdarrConfig()

    try:
        payload = json.loads(setting.value)
        return _sanitize_maker_idarr_config(payload)
    except Exception as e:
        log_error(LogTags.API, f"Failed to parse maker idarr config: {e}\n{traceback.format_exc()}")
        return MakerIdarrConfig()


@router.get("/")
async def get_maker_idarr_config(db: Session = Depends(get_db)) -> MakerIdarrConfig:
    """Get Maker Tools IDarr configuration."""
    return load_runtime_config(db)


@router.post("/")
async def save_maker_idarr_config(config: MakerIdarrConfig, db: Session = Depends(get_db)) -> Dict[str, bool]:
    """Save Maker Tools IDarr configuration."""
    try:
        incoming_payload = config.model_dump()
        incoming_targets = incoming_payload.get("sync_targets")
        existing_config = load_runtime_config(db)
        existing_targets = existing_config.sync_targets if isinstance(existing_config.sync_targets, list) else []
        if isinstance(incoming_targets, list):
            for index, target in enumerate(incoming_targets):
                if not isinstance(target, dict):
                    continue
                if str(target.get("scope_token") or "").strip():
                    continue
                if index >= len(existing_targets):
                    continue
                existing_target = existing_targets[index]
                if not isinstance(existing_target, dict):
                    continue
                existing_scope_token = str(existing_target.get("scope_token") or "").strip()
                if existing_scope_token:
                    target["scope_token"] = existing_scope_token

        sanitized_config = _sanitize_maker_idarr_config(incoming_payload)

        # If a non-empty TMDB key was submitted, promote it to the global setting
        # and strip it from the per-config JSON so the canonical location is Settings → General.
        incoming_key = sanitized_config.tmdb_api_key.strip()
        if incoming_key:
            upsert_setting(db, "tmdb_api_key", incoming_key)
        sanitized_config = sanitized_config.model_copy(update={"tmdb_api_key": ""})

        upsert_setting(db, SETTING_MAKER_IDARR_CONFIG, sanitized_config.model_dump_json())
        db.commit()
        log_user_action("Saved Maker Tools IDarr configuration")
        return {"success": True}
    except Exception as e:
        db.rollback()
        log_error(LogTags.API, f"Failed to save maker idarr config: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to save IDarr configuration")


@router.post("/upload")
async def upload_maker_idarr_files(
    sync_target_index: int = Form(...),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Upload poster-maker files into the selected IDarr sync target source folder."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    config_setting = get_setting(db, SETTING_MAKER_IDARR_CONFIG)
    if not config_setting or not config_setting.value:
        raise HTTPException(status_code=400, detail="IDarr is not configured. Save settings first.")

    try:
        config_data = json.loads(config_setting.value)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="IDarr configuration is invalid JSON. Save settings again.")

    if not isinstance(config_data, dict):
        raise HTTPException(status_code=400, detail="IDarr configuration payload is invalid.")

    raw_targets = config_data.get("sync_targets")
    sync_targets = [item for item in raw_targets if isinstance(item, dict)] if isinstance(raw_targets, list) else []

    if not sync_targets:
        raise HTTPException(status_code=400, detail="No sync targets configured for IDarr.")

    if sync_target_index < 0 or sync_target_index >= len(sync_targets):
        raise HTTPException(
            status_code=400,
            detail=f"sync_target_index must be between 0 and {max(len(sync_targets) - 1, 0)}",
        )

    selected_target = sync_targets[sync_target_index]
    source_dir_value = str(selected_target.get("source_dir") or "").strip()
    if not source_dir_value:
        raise HTTPException(status_code=400, detail="Selected sync target is missing source_dir.")

    source_dir = Path(source_dir_value)
    source_dir.mkdir(parents=True, exist_ok=True)

    uploaded: list[str] = []
    skipped: list[str] = []

    for upload in files:
        filename = Path(str(upload.filename or "")).name.strip()
        if not filename:
            skipped.append("unnamed-file")
            continue

        suffix = Path(filename).suffix.lower()
        if suffix not in IDARR_UPLOAD_IMAGE_EXTENSIONS:
            skipped.append(filename)
            continue

        destination = source_dir / filename
        if destination.exists():
            destination = source_dir / f"{destination.stem}_{uuid4().hex[:8]}{suffix}"

        try:
            payload = await upload.read()
            if len(payload) > 50 * 1024 * 1024:
                skipped.append(filename)
                log_warning(LogTags.API, f"IDarr upload skipped (exceeds 50 MB): {filename}")
                continue
            destination.write_bytes(payload)
            uploaded.append(destination.name)
        except Exception as exc:
            log_error(LogTags.API, f"Failed to save uploaded IDarr file '{filename}': {exc}\n{traceback.format_exc()}")
            skipped.append(filename)

    if not uploaded:
        raise HTTPException(status_code=400, detail="No valid image files were uploaded")

    log_user_action(
        "Uploaded files to IDarr source folder",
        sync_target_index=sync_target_index,
        source_dir=str(source_dir),
        uploaded_count=len(uploaded),
        skipped_count=len(skipped),
    )

    return {
        "success": True,
        "source_dir": str(source_dir),
        "uploaded_count": len(uploaded),
        "skipped_count": len(skipped),
        "uploaded": uploaded,
        "skipped": skipped,
    }


@router.get("/last-run")
async def get_maker_idarr_last_run(sync_target_index: int | None = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Get last IDarr run details for expandable UI display."""
    run_query = db.query(IdarrRun)
    scope_token, scoped_source_dir = _resolve_scope_context(db, sync_target_index)
    run_query = _apply_idarr_run_scope_filter(run_query, scope_token, scoped_source_dir)
    latest_run = run_query.order_by(IdarrRun.completed_at.desc(), IdarrRun.id.desc()).first()
    if latest_run:
        try:
            stats_payload = json.loads(latest_run.stats_json) if latest_run.stats_json else {}
            details_payload = json.loads(latest_run.details_json) if latest_run.details_json else {}
            warnings_payload = json.loads(latest_run.warnings_json) if latest_run.warnings_json else []
            return {
                "completed_at": latest_run.completed_at.isoformat() if latest_run.completed_at else None,
                "stats": stats_payload if isinstance(stats_payload, dict) else {},
                "details": details_payload if isinstance(details_payload, dict) else {},
                "warnings": warnings_payload if isinstance(warnings_payload, list) else [],
                "unmatched_count": int(latest_run.unmatched_count or 0),
            }
        except Exception as e:
            log_error(LogTags.API, f"Failed to parse idarr_runs payload: {e}\n{traceback.format_exc()}")

    return {}


@router.get("/pending-matches/count")
async def get_maker_idarr_pending_count(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Return total count of pending matches across all sync targets (for sidebar badge)."""
    count = db.query(IdarrPendingMatch).count()
    return {"count": count}


@router.get("/pending-matches")
async def get_maker_idarr_pending_matches(sync_target_index: int | None = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """List current IDarr pending matches from dedicated table."""
    scope_token = _resolve_scope_token(db, sync_target_index)
    return {"items": _build_idarr_pending_items_payload(db, scope_token, sync_target_index)}


@router.get("/pending-matches/source-image")
async def get_maker_idarr_pending_source_image(
    path: str,
    sync_target_index: int | None = None,
    db: Session = Depends(get_db),
) -> FileResponse:
    """Stream a pending source image when it exists inside configured IDarr source folders."""
    source_path_raw = path.strip()
    if not source_path_raw:
        raise HTTPException(status_code=400, detail="path is required")

    source_dirs = _get_idarr_source_dirs(db, sync_target_index)
    if not source_dirs:
        raise HTTPException(status_code=400, detail="No IDarr source folders available for preview.")

    requested_path = _resolve_authorized_idarr_source_image_path(source_path_raw, source_dirs)
    return FileResponse(
        str(requested_path),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.post("/pending-matches/clear-all")
async def clear_maker_idarr_pending_matches(sync_target_index: int | None = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Clear all pending IDarr unmatched rows from the pending match queue."""
    scope_token = _resolve_scope_token(db, sync_target_index)
    deleted = _filter_pending_query_by_scope(db.query(IdarrPendingMatch), scope_token).delete(synchronize_session=False)
    db.commit()

    log_user_action(
        "Cleared all IDarr pending unmatched entries",
        deleted=int(deleted or 0),
    )

    return {
        "success": True,
        "deleted": int(deleted or 0),
    }


def _build_idarr_pending_items_payload(
    db: Session,
    scope_token: str | None = None,
    sync_target_index: int | None = None,
) -> list[dict[str, Any]]:
    """Build the full pending-match item list for the API response and snapshot export.

    Currently lives in the API layer because it uses ``_get_idarr_source_dirs`` for preview URL
    resolution. The pure path-resolution logic has been extracted into ``_source_dirs_from_targets``
    to facilitate a future move of this function to a dedicated service module.
    """
    rows = _filter_pending_query_by_scope(
        db.query(IdarrPendingMatch),
        scope_token,
    ).order_by(IdarrPendingMatch.created_at.desc(), IdarrPendingMatch.id.desc()).all()
    keys = [row.asset_key for row in rows if row.asset_key]
    cache_rows = _filter_cache_query_by_scope(
        db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key.in_(keys)),
        scope_token,
    ).all() if keys else []
    cache_by_key = {row.asset_key: row for row in cache_rows}
    source_dirs = _get_idarr_source_dirs(db, sync_target_index)

    recent_runs = db.query(IdarrRun).order_by(IdarrRun.completed_at.desc(), IdarrRun.id.desc()).limit(50).all()
    run_history: dict[str, dict[str, Any]] = {}
    for run in recent_runs:
        details_payload: dict[str, Any] | None = None
        try:
            details_payload = json.loads(run.details_json) if run.details_json else {}
        except Exception:
            details_payload = None
        if details_payload is None:
            continue
        if not isinstance(details_payload, dict):
            continue
        unmatched_items = details_payload.get("unmatched_items") if isinstance(details_payload.get("unmatched_items"), list) else []
        for item in unmatched_items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            item_type = str(item.get("type") or "").strip().lower()
            year = item.get("year") if isinstance(item.get("year"), int) else None
            if not title or not item_type:
                continue
            key = _idarr_asset_key(item_type, title, year, scope_token)
            history = run_history.get(key, {"unmatched_run_count": 0, "last_unmatched_at": None})
            history["unmatched_run_count"] = int(history.get("unmatched_run_count", 0)) + 1
            if not history.get("last_unmatched_at") and run.completed_at:
                history["last_unmatched_at"] = run.completed_at.isoformat()
            run_history[key] = history

    items: list[dict[str, Any]] = []
    for row in rows:
        cache_row = cache_by_key[row.asset_key] if row.asset_key in cache_by_key else None
        cache_payload = _extract_payload_dict(cache_row)
        pending_entry = _normalize_pending_entry_payload(
            cache_payload.get("pending_entry"),
            title=row.title,
            year=row.year if isinstance(row.year, int) else None,
            files=cache_payload.get("current_filenames"),
        )

        # Build the list of source file basenames so the UI can trigger a targeted single-file rename
        raw_current_files = cache_payload.get("current_filenames")
        source_filenames: list[str] | None = sorted(set(
            Path(str(f)).name
            for f in (raw_current_files if isinstance(raw_current_files, list) else [])
            if isinstance(f, str) and f.strip()
        )) or None

        items.append(
            {
                "asset_key": row.asset_key,
                "title": row.title,
                "year": row.year,
                "type": row.asset_type,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "suggested_ids": {
                    "tmdb_id": cache_row.tmdb_id if cache_row else None,
                    "tvdb_id": cache_row.tvdb_id if cache_row else None,
                    "imdb_id": cache_row.imdb_id if cache_row else None,
                },
                "cache_context": {
                    "matched": bool(cache_row.matched) if cache_row else False,
                    "last_checked_at": cache_row.last_checked_at.isoformat() if cache_row and cache_row.last_checked_at else None,
                    "candidate_refreshed_at": cache_payload.get("candidate_refreshed_at"),
                },
                "preview_url": _build_idarr_pending_preview_url_with_fallback(
                    cache_payload,
                    source_dirs,
                    title=row.title,
                    year=row.year if isinstance(row.year, int) else None,
                    cache_buster=int(cache_row.updated_at.timestamp()) if cache_row and cache_row.updated_at else (
                        int(row.updated_at.timestamp()) if row.updated_at else None
                    ),
                ),
                "source_filenames": source_filenames,
                "pending_entry": pending_entry,
                "pending_reason": _extract_pending_reason(cache_payload, pending_entry),
                "candidates": _extract_cached_candidates(cache_row) if cache_row else [],
                "candidate_reviews": _extract_candidate_reviews(cache_row) if cache_row else {},
                "resolution_history": _extract_resolution_history(cache_row) if cache_row else [],
                "history": run_history.get(row.asset_key, {"unmatched_run_count": 0, "last_unmatched_at": None}),
            }
        )

    return items


def _source_dirs_from_targets(targets: list[dict[str, Any]]) -> list[Path]:
    """Return a deduplicated list of existing source-dir ``Path`` objects from a list of sync targets.

    Pure function: no HTTP exceptions, no config loading — safe to call from service layer code.
    Targets without a ``source_dir``, or whose ``source_dir`` doesn't exist on disk, are silently skipped.
    """
    source_dirs: list[Path] = []
    seen: set[str] = set()
    for target in targets:
        if not isinstance(target, dict):
            continue
        source_dir_value = str(target.get("source_dir") or "").strip()
        if not source_dir_value:
            continue
        source_dir: Path | None = None
        try:
            source_dir = Path(source_dir_value).resolve()
        except Exception:
            source_dir = None
        if source_dir is None:
            continue
        if not source_dir.exists() or not source_dir.is_dir():
            continue
        source_dir_key = str(source_dir)
        if source_dir_key in seen:
            continue
        seen.add(source_dir_key)
        source_dirs.append(source_dir)
    return source_dirs


def _get_idarr_source_dirs(db: Session, sync_target_index: int | None = None) -> list[Path]:
    """Load config, validate ``sync_target_index``, and delegate to ``_source_dirs_from_targets``."""
    config = load_runtime_config(db)
    targets = list(config.sync_targets) if isinstance(config.sync_targets, list) else []
    if isinstance(sync_target_index, int):
        if sync_target_index < 0 or sync_target_index >= len(targets):
            raise HTTPException(
                status_code=400,
                detail=f"sync_target_index must be between 0 and {max(len(targets) - 1, 0)}",
            )
        targets = [targets[sync_target_index]]
    return _source_dirs_from_targets(targets)


def _resolve_authorized_idarr_source_image_path(source_path_raw: str, source_dirs: list[Path]) -> Path:
    try:
        requested_path = Path(source_path_raw).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid source image path.")

    if not requested_path.exists() or not requested_path.is_file():
        raise HTTPException(status_code=404, detail="Source image not found.")

    for source_dir in source_dirs:
        is_within_source_dir = False
        try:
            requested_path.relative_to(source_dir)
            is_within_source_dir = True
        except Exception:
            is_within_source_dir = False
        if is_within_source_dir:
            return requested_path

    raise HTTPException(status_code=403, detail="Source image is outside allowed IDarr source folders.")


def _build_idarr_pending_preview_url(cache_payload: dict[str, Any], source_dirs: list[Path], cache_buster: int | None = None) -> str | None:
    if not source_dirs or not isinstance(cache_payload, dict):
        return None

    raw_candidates: list[str] = []

    pending_entry = cache_payload.get("pending_entry")
    if isinstance(pending_entry, dict):
        pending_file = pending_entry.get("files")
        if isinstance(pending_file, str) and pending_file.strip():
            raw_candidates.append(pending_file.strip())

    for field_name in ("current_filenames", "original_filenames"):
        field_value = cache_payload.get(field_name)
        if isinstance(field_value, list):
            for entry in field_value:
                if isinstance(entry, str) and entry.strip():
                    raw_candidates.append(entry.strip())

    seen: set[str] = set()
    for raw_candidate in raw_candidates:
        if raw_candidate in seen:
            continue
        seen.add(raw_candidate)

        candidate_path: Path | None = None
        try:
            candidate_path = Path(raw_candidate)
        except Exception:
            candidate_path = None
        if candidate_path is None:
            continue

        if candidate_path.is_absolute():
            resolved: Path | None = None
            try:
                resolved = candidate_path.resolve()
            except Exception:
                resolved = None
            if resolved is None:
                continue

            if not resolved.exists() or not resolved.is_file():
                continue

            for source_dir in source_dirs:
                in_source_dir = False
                try:
                    resolved.relative_to(source_dir)
                    in_source_dir = True
                except Exception:
                    in_source_dir = False
                if in_source_dir:
                    try:
                        _cb = f"&t={cache_buster}" if isinstance(cache_buster, int) else ""
                        return f"/api/idarr/pending-matches/source-image?path={quote(str(resolved), safe='')}{_cb}"
                    except Exception as e:
                        log_debug(LogTags.API, f"Failed building IDarr pending preview URL: {e}")
            continue

        for source_dir in source_dirs:
            resolved: Path | None = None
            try:
                resolved = (source_dir / raw_candidate).resolve()
            except Exception:
                resolved = None
            if resolved is None:
                continue

            is_within_source_dir = False
            try:
                resolved.relative_to(source_dir)
            except Exception:
                is_within_source_dir = False
            else:
                is_within_source_dir = True
            if not is_within_source_dir:
                continue

            if resolved.exists() and resolved.is_file():
                _cb = f"&t={cache_buster}" if isinstance(cache_buster, int) else ""
                return f"/api/idarr/pending-matches/source-image?path={quote(str(resolved), safe='')}{_cb}"

    return None


def _build_idarr_pending_preview_url_with_fallback(
    cache_payload: dict[str, Any],
    source_dirs: list[Path],
    *,
    title: str,
    year: int | None,
    cache_buster: int | None = None,
) -> str | None:
    preview_url = _build_idarr_pending_preview_url(cache_payload, source_dirs, cache_buster=cache_buster)
    if preview_url:
        return preview_url

    normalized_title = normalize_titles(str(title or "").strip())
    if not normalized_title:
        return None

    year_text = str(year) if isinstance(year, int) else ""
    best_score = 0
    best_path: Path | None = None

    for source_dir in source_dirs:
        try:
            entries = source_dir.iterdir()
        except Exception:
            continue

        for entry in entries:
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in IDARR_UPLOAD_IMAGE_EXTENSIONS:
                continue

            # Skip files that already have ID tags — they're already enriched/renamed
            # and cannot be the plain pending file we want to preview.
            stem_raw = str(entry.stem or "")
            if re.search(r'\{(?:tmdb|tvdb|imdb)[-_]', stem_raw, re.IGNORECASE):
                continue

            candidate_stem = normalize_titles(entry.stem)
            if not candidate_stem:
                continue

            score = 0
            if candidate_stem == normalized_title:
                score += 80
            elif normalized_title in candidate_stem or candidate_stem in normalized_title:
                score += 40
            else:
                continue

            if year_text and year_text in stem_raw:
                score += 20

            if score > best_score:
                best_score = score
                best_path = entry

    if best_path is None:
        return None

    try:
        _cb = f"&t={cache_buster}" if isinstance(cache_buster, int) else ""
        return f"/api/idarr/pending-matches/source-image?path={quote(str(best_path.resolve()), safe='')}{_cb}"
    except Exception:
        return None


@router.get("/pending-matches/snapshot")
async def get_maker_idarr_pending_matches_snapshot(sync_target_index: int | None = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Return a structured pending-workflow snapshot payload for external review/audit."""
    scope_token = _resolve_scope_token(db, sync_target_index)
    items = _build_idarr_pending_items_payload(db, scope_token, sync_target_index)
    return {
        "format": "idarr_pending_workflow_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(items),
        "items": items,
    }


@router.post("/pending-matches/resolve")
async def resolve_maker_idarr_pending_match(payload: IdarrPendingResolveRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Resolve or dismiss a pending IDarr match entry."""
    scope_token = _resolve_scope_token(db, payload.sync_target_index)
    return resolve_pending_matches(payload, db, scope_token)


def resolve_pending_matches(payload: IdarrPendingResolveRequest, db: Session, scope_token: str | None = None) -> Dict[str, Any]:
    """Resolve or dismiss a pending IDarr match entry."""
    asset_key = payload.asset_key.strip()
    if not asset_key:
        raise HTTPException(status_code=400, detail="asset_key is required")

    row = _filter_pending_query_by_scope(
        db.query(IdarrPendingMatch).filter(IdarrPendingMatch.asset_key == asset_key),
        scope_token,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Pending match not found")

    existing_cache = _filter_cache_query_by_scope(
        db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == row.asset_key),
        scope_token,
    ).first()
    cache_payload = _extract_payload_dict(existing_cache)
    resolution_history = cache_payload.get("resolution_history")
    if not isinstance(resolution_history, list):
        resolution_history = []

    current_tmdb = existing_cache.tmdb_id if existing_cache and isinstance(existing_cache.tmdb_id, int) else None
    current_tvdb = existing_cache.tvdb_id if existing_cache and isinstance(existing_cache.tvdb_id, int) else None
    current_imdb = existing_cache.imdb_id.strip() if existing_cache and isinstance(existing_cache.imdb_id, str) and existing_cache.imdb_id.strip() else None

    requested_action = payload.action
    tmdb_input = payload.tmdb_input.strip() if isinstance(payload.tmdb_input, str) and payload.tmdb_input.strip() else None
    if requested_action == "resolve" and isinstance(tmdb_input, str) and tmdb_input.lower() == "ignore":
        requested_action = "ignore"

    parsed_tmdb_id: int | None = None
    inferred_type: str | None = None
    if requested_action == "resolve" and tmdb_input:
        parsed_tmdb_id, inferred_type = _parse_tmdb_input(tmdb_input)

    resolved_tmdb_id = payload.tmdb_id if isinstance(payload.tmdb_id, int) else parsed_tmdb_id
    resolved_tvdb_id = payload.tvdb_id if isinstance(payload.tvdb_id, int) else None
    resolved_imdb_id = payload.imdb_id.strip() if payload.imdb_id and payload.imdb_id.strip() else None

    if requested_action == "resolve":
        if resolved_tmdb_id is None and resolved_tvdb_id is None and not resolved_imdb_id:
            raise HTTPException(status_code=400, detail="Provide at least one ID to resolve")

        selected_source = "manual"
        selected_candidate: dict[str, Any] | None = None
        if isinstance(resolved_tmdb_id, int):
            candidate_rows = cache_payload.get("candidate_results")
            if isinstance(candidate_rows, list):
                for candidate in candidate_rows:
                    if isinstance(candidate, dict) and candidate.get("tmdb_id") == resolved_tmdb_id:
                        selected_source = "candidate"
                        selected_candidate = candidate
                        break
            if selected_source == "manual" and tmdb_input:
                selected_source = "tmdb_input"

        # Populate tvdb/imdb from the matching candidate if not explicitly provided in the payload
        candidate_tvdb: int | None = None
        candidate_imdb: str | None = None
        if isinstance(selected_candidate, dict):
            cand_tvdb = selected_candidate.get("tvdb_id")
            candidate_tvdb = cand_tvdb if isinstance(cand_tvdb, int) else None
            cand_imdb = selected_candidate.get("imdb_id")
            candidate_imdb = cand_imdb.strip() if isinstance(cand_imdb, str) and cand_imdb.strip() else None

        # Extract canonical title/year for renaming: use candidate data when available,
        # otherwise fetch directly from TMDB so the runner uses the official title
        # instead of the original (dirty) parsed filename title.
        canonical_title_for_resolve: str | None = None
        canonical_year_for_resolve: int | None = None
        if isinstance(selected_candidate, dict):
            raw_title = str(selected_candidate.get("title") or "").strip()
            if raw_title:
                canonical_title_for_resolve = raw_title
            raw_year = selected_candidate.get("year")
            if isinstance(raw_year, int):
                canonical_year_for_resolve = raw_year
        elif isinstance(resolved_tmdb_id, int):
            # Manual TMDB ID not matched to a cached candidate — fetch from TMDB directly.
            tmdb_api_key_for_resolve = _get_maker_idarr_tmdb_key(db)
            if tmdb_api_key_for_resolve:
                target_type_for_resolve = inferred_type or row.asset_type or "movie"
                tmdb_entity = "tv" if target_type_for_resolve == "tv_series" else ("collection" if target_type_for_resolve == "collection" else "movie")
                try:
                    tmdb_resp = requests.get(
                        f"https://api.themoviedb.org/3/{tmdb_entity}/{resolved_tmdb_id}",
                        params={"api_key": tmdb_api_key_for_resolve},
                        timeout=10,
                    )
                    tmdb_resp.raise_for_status()
                    tmdb_data = tmdb_resp.json()
                    if isinstance(tmdb_data, dict):
                        fetched_title = str(tmdb_data.get("title") or tmdb_data.get("name") or "").strip()
                        if fetched_title:
                            canonical_title_for_resolve = fetched_title
                        release_text = str(tmdb_data.get("release_date") or tmdb_data.get("first_air_date") or "").strip()
                        if len(release_text) >= 4 and release_text[:4].isdigit():
                            canonical_year_for_resolve = int(release_text[:4])
                except Exception:
                    pass  # Silently continue; runner will re-verify on next run

        resolution_event = {
            "resolved_at": datetime.now(timezone.utc).isoformat(),
            "source": selected_source,
            "tmdb_id": resolved_tmdb_id if isinstance(resolved_tmdb_id, int) else current_tmdb,
            "tvdb_id": resolved_tvdb_id if isinstance(resolved_tvdb_id, int) else (candidate_tvdb if isinstance(candidate_tvdb, int) else current_tvdb),
            "imdb_id": resolved_imdb_id if resolved_imdb_id else (candidate_imdb if candidate_imdb else current_imdb),
            "candidate_reason": selected_candidate.get("match_reason") if isinstance(selected_candidate, dict) else None,
            "action": "resolve",
            "tmdb_input": tmdb_input,
        }
        resolution_history.insert(0, resolution_event)
        cache_payload["resolution_history"] = resolution_history[:20]
        cache_payload["resolved_manually"] = True
        cache_payload["last_resolved_at"] = resolution_event["resolved_at"]
        cache_payload["status"] = "found"
        cache_payload["last_checked"] = resolution_event["resolved_at"]
        cache_payload["title"] = row.title
        cache_payload["year"] = row.year
        cache_payload["type"] = row.asset_type
        cache_payload["tmdb_id"] = resolution_event["tmdb_id"]
        cache_payload["tvdb_id"] = resolution_event["tvdb_id"]
        cache_payload["imdb_id"] = resolution_event["imdb_id"]
        if canonical_title_for_resolve:
            cache_payload["canonical_title"] = canonical_title_for_resolve
        if isinstance(canonical_year_for_resolve, int):
            cache_payload["canonical_year"] = canonical_year_for_resolve
        cache_payload["pending_entry"] = _normalize_pending_entry_payload(
            cache_payload.get("pending_entry"),
            title=row.title,
            year=row.year if isinstance(row.year, int) else None,
            files=cache_payload.get("current_filenames") or cache_payload.get("original_filenames") or cache_payload.get("files"),
        )
        cache_payload.pop("candidates", None)

        matched = bool(
            isinstance(resolution_event["tmdb_id"], int)
            or isinstance(resolution_event["tvdb_id"], int)
            or (isinstance(resolution_event["imdb_id"], str) and resolution_event["imdb_id"])
        )

        target_asset_type = _normalize_idarr_asset_type(inferred_type or row.asset_type)
        if not target_asset_type:
            target_asset_type = _normalize_idarr_asset_type(row.asset_type)
        target_asset_key = _idarr_asset_key(target_asset_type, row.title, row.year, scope_token)

        upsert_idarr_asset_cache(
            db,
            asset_key=target_asset_key,
            title=row.title,
            year=row.year,
            asset_type=target_asset_type,
            tmdb_id=resolution_event["tmdb_id"] if isinstance(resolution_event["tmdb_id"], int) else None,
            tvdb_id=resolution_event["tvdb_id"] if isinstance(resolution_event["tvdb_id"], int) else None,
            imdb_id=resolution_event["imdb_id"] if isinstance(resolution_event["imdb_id"], str) and resolution_event["imdb_id"] else None,
            matched=matched,
            payload_json=json.dumps(cache_payload),
        )
        if target_asset_key != row.asset_key and existing_cache:
            db.delete(existing_cache)
    elif requested_action == "ignore":
        ignored = _load_ignored_titles(db, scope_token)
        key = _idarr_asset_key(row.asset_type, row.title, row.year, scope_token)
        ignored_at = datetime.now(timezone.utc).isoformat()
        if not any(isinstance(item, dict) and item.get("asset_key") == key for item in ignored):
            ignored.append(_build_ignored_entry(
                title=row.title,
                year=row.year if isinstance(row.year, int) else None,
                asset_type=row.asset_type,
                asset_key=key,
            ))
            _save_ignored_titles(db, ignored, scope_token)
        # No else-commit needed: the unconditional db.commit() at the end of this
        # function covers the cache upsert and pending-row deletion.

        ignore_event = {
            "resolved_at": ignored_at,
            "source": "manual",
            "action": "ignore",
            "tmdb_id": current_tmdb,
            "tvdb_id": current_tvdb,
            "imdb_id": current_imdb,
            "candidate_reason": None,
        }
        resolution_history.insert(0, ignore_event)
        cache_payload["resolution_history"] = resolution_history[:20]
        cache_payload["status"] = "ignored"
        cache_payload["last_checked"] = ignored_at
        cache_payload["title"] = row.title
        cache_payload["year"] = row.year
        cache_payload["type"] = row.asset_type
        cache_payload["pending_entry"] = _normalize_pending_entry_payload(
            cache_payload.get("pending_entry"),
            title=row.title,
            year=row.year if isinstance(row.year, int) else None,
            files=cache_payload.get("current_filenames") or cache_payload.get("original_filenames") or cache_payload.get("files"),
        )
        if isinstance(cache_payload.get("pending_entry"), dict):
            cache_payload["pending_entry"]["add_tmdb_url_here"] = "ignore"
        cache_payload.pop("candidates", None)

        upsert_idarr_asset_cache(
            db,
            asset_key=row.asset_key,
            title=row.title,
            year=row.year,
            asset_type=row.asset_type,
            tmdb_id=current_tmdb,
            tvdb_id=current_tvdb,
            imdb_id=current_imdb,
            matched=bool(existing_cache.matched) if existing_cache else False,
            payload_json=json.dumps(cache_payload),
        )
    else:
        dismissed_at = datetime.now(timezone.utc).isoformat()
        dismiss_event = {
            "resolved_at": dismissed_at,
            "source": "manual",
            "action": "dismiss",
            "tmdb_id": current_tmdb,
            "tvdb_id": current_tvdb,
            "imdb_id": current_imdb,
            "candidate_reason": None,
        }
        resolution_history.insert(0, dismiss_event)
        cache_payload["resolution_history"] = resolution_history[:20]
        cache_payload["status"] = "dismissed"
        cache_payload["last_checked"] = dismissed_at
        cache_payload["title"] = row.title
        cache_payload["year"] = row.year
        cache_payload["type"] = row.asset_type
        cache_payload["pending_entry"] = _normalize_pending_entry_payload(
            cache_payload.get("pending_entry"),
            title=row.title,
            year=row.year if isinstance(row.year, int) else None,
            files=cache_payload.get("current_filenames") or cache_payload.get("original_filenames") or cache_payload.get("files"),
        )
        cache_payload.pop("candidates", None)

        upsert_idarr_asset_cache(
            db,
            asset_key=row.asset_key,
            title=row.title,
            year=row.year,
            asset_type=row.asset_type,
            tmdb_id=current_tmdb,
            tvdb_id=current_tvdb,
            imdb_id=current_imdb,
            matched=bool(existing_cache.matched) if existing_cache else False,
            payload_json=json.dumps(cache_payload),
        )

    db.delete(row)
    db.commit()

    log_user_action(
        f"IDarr pending match {requested_action}",
        asset_key=asset_key,
        requested_action=requested_action,
    )

    return {"success": True, "action": requested_action, "asset_key": asset_key}


@router.post("/pending-matches/candidates")
async def get_maker_idarr_pending_candidates(payload: IdarrPendingCandidatesRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Lookup candidate TMDB matches for a pending IDarr item."""
    title = payload.title.strip()
    requested_media_type = payload.type.strip().lower()
    normalized_media_type = _normalize_idarr_asset_type(requested_media_type)
    year = payload.year if isinstance(payload.year, int) else None
    scope_token = _resolve_scope_token(db, payload.sync_target_index)
    asset_key = payload.asset_key.strip() if payload.asset_key and payload.asset_key.strip() else _idarr_asset_key(normalized_media_type, title, year, scope_token)

    if not title:
        raise HTTPException(status_code=400, detail="title is required")

    if normalized_media_type not in {"movie", "tv_series", "collection"}:
        raise HTTPException(status_code=400, detail="type must be one of: movie, show, season, collection, tv_series")

    tmdb_lookup_type = "show" if normalized_media_type == "tv_series" else normalized_media_type

    tmdb_api_key = _get_maker_idarr_tmdb_key(db)
    if not tmdb_api_key:
        log_warning(LogTags.MODULE, "idarr TMDB search blocked: TMDB API key is not configured")
        raise HTTPException(status_code=400, detail="TMDB API key is not configured. Add it in Settings → General → API Keys.")

    endpoint = "/search/movie"
    query_params: dict[str, Any] = {"query": title, "include_adult": "false"}

    if tmdb_lookup_type == "show":
        endpoint = "/search/tv"
        if year:
            query_params["first_air_date_year"] = year
    elif tmdb_lookup_type == "collection":
        endpoint = "/search/collection"
    else:
        endpoint = "/search/movie"
        if year:
            query_params["year"] = year

    try:
        response = requests.get(
            f"https://api.themoviedb.org/3{endpoint}",
            params={
                "api_key": tmdb_api_key,
                **query_params,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload_json = response.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"TMDB candidate lookup failed: {exc}")

    results = payload_json.get("results") if isinstance(payload_json, dict) else []
    if not isinstance(results, list):
        results = []

    candidates: list[dict[str, Any]] = []
    tmdb_external_ids_cache: dict[int, dict[str, Any]] = {}

    def fetch_tmdb_external_ids(tmdb_id: int) -> dict[str, Any]:
        if tmdb_id in tmdb_external_ids_cache:
            return tmdb_external_ids_cache[tmdb_id]

        if tmdb_lookup_type == "collection":
            tmdb_external_ids_cache[tmdb_id] = {}
            return {}

        tmdb_entity = "tv" if tmdb_lookup_type == "show" else "movie"
        try:
            external_response = requests.get(
                f"https://api.themoviedb.org/3/{tmdb_entity}/{tmdb_id}/external_ids",
                params={"api_key": tmdb_api_key},
                timeout=12,
            )
            external_response.raise_for_status()
            external_payload = external_response.json()
            if isinstance(external_payload, dict):
                tmdb_external_ids_cache[tmdb_id] = external_payload
                return external_payload
        except requests.RequestException:
            pass

        tmdb_external_ids_cache[tmdb_id] = {}
        return {}

    for item in results[:10]:
        if not isinstance(item, dict):
            continue
        tmdb_id = item.get("id") if isinstance(item.get("id"), int) else None
        if tmdb_id is None:
            continue
        external_ids_payload = fetch_tmdb_external_ids(tmdb_id)
        imdb_id = external_ids_payload.get("imdb_id") if isinstance(external_ids_payload.get("imdb_id"), str) else None
        tvdb_raw = external_ids_payload.get("tvdb_id")
        tvdb_id = tvdb_raw if isinstance(tvdb_raw, int) else (int(tvdb_raw) if isinstance(tvdb_raw, str) and tvdb_raw.isdigit() else None)
        candidate_title = str(item.get("title") or item.get("name") or "").strip()
        release_text = str(item.get("release_date") or item.get("first_air_date") or "").strip()
        release_year = int(release_text[:4]) if len(release_text) >= 4 and release_text[:4].isdigit() else None
        reason = _candidate_reason(title, year, candidate_title, release_year)
        candidates.append(
            {
                "tmdb_id": tmdb_id,
                "tvdb_id": tvdb_id,
                "imdb_id": imdb_id,
                "title": candidate_title,
                "year": release_year,
                "poster_url": f"https://image.tmdb.org/t/p/w185{item.get('poster_path')}" if isinstance(item.get("poster_path"), str) and item.get("poster_path") else None,
                "overview": str(item.get("overview") or "").strip(),
                "popularity": float(item.get("popularity") or 0),
                "vote_average": float(item.get("vote_average") or 0),
                "media_type": "show" if tmdb_lookup_type == "show" else ("collection" if tmdb_lookup_type == "collection" else "movie"),
                "match_reason": reason,
            }
        )

    cache_row = _filter_cache_query_by_scope(
        db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == asset_key),
        scope_token,
    ).first()
    cache_payload: dict[str, Any] = {}
    if cache_row and cache_row.payload_json:
        try:
            parsed_payload = json.loads(cache_row.payload_json)
            if isinstance(parsed_payload, dict):
                cache_payload = parsed_payload
        except Exception:
            cache_payload = {}

    cache_payload["candidate_results"] = candidates
    cache_payload["candidate_refreshed_at"] = datetime.now(timezone.utc).isoformat()

    existing_reviews = cache_payload.get("candidate_reviews")
    if not isinstance(existing_reviews, dict):
        existing_reviews = {}

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = candidate.get("tmdb_id")
        key = str(candidate_id) if isinstance(candidate_id, int) else ""
        if not key:
            continue
        review_entry = existing_reviews.get(key)
        if isinstance(review_entry, dict):
            candidate["review"] = review_entry

    upsert_idarr_asset_cache(
        db,
        asset_key=asset_key,
        title=title,
        year=year,
        asset_type=normalized_media_type,
        tmdb_id=cache_row.tmdb_id if cache_row else None,
        tvdb_id=cache_row.tvdb_id if cache_row else None,
        imdb_id=cache_row.imdb_id if cache_row else None,
        matched=bool(cache_row.matched) if cache_row else False,
        payload_json=json.dumps(cache_payload),
        touch_checked_at=False,
    )
    db.commit()

    log_user_action(
        "Requested IDarr pending candidates",
        asset_key=asset_key,
        type=normalized_media_type,
        candidates=len(candidates),
    )

    # candidate_reviews and resolution_history are already available in cache_payload
    # which was just written to the DB — no need for two additional re-queries.
    return {
        "asset_key": asset_key,
        "title": title,
        "year": year,
        "type": normalized_media_type,
        "candidates": candidates,
        "candidate_reviews": cache_payload.get("candidate_reviews") or {},
        "resolution_history": cache_payload.get("resolution_history") or [],
    }


@router.post("/pending-matches/candidates/review")
async def review_maker_idarr_pending_candidate(payload: IdarrPendingCandidateReviewRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Persist accept/reject review state for a pending candidate suggestion."""
    asset_key = payload.asset_key.strip()
    if not asset_key:
        raise HTTPException(status_code=400, detail="asset_key is required")

    scope_token = _resolve_scope_token(db, payload.sync_target_index)
    pending_row = _filter_pending_query_by_scope(
        db.query(IdarrPendingMatch).filter(IdarrPendingMatch.asset_key == asset_key),
        scope_token,
    ).first()
    cache_row = _filter_cache_query_by_scope(
        db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == asset_key),
        scope_token,
    ).first()

    if not pending_row and not cache_row:
        raise HTTPException(status_code=404, detail="Pending match not found")

    title = pending_row.title if pending_row else (cache_row.title if cache_row else "")
    year = pending_row.year if pending_row else (cache_row.year if cache_row else None)
    asset_type = pending_row.asset_type if pending_row else (cache_row.asset_type if cache_row else "movie")

    cache_payload = _extract_payload_dict(cache_row)
    candidate_reviews = cache_payload.get("candidate_reviews")
    if not isinstance(candidate_reviews, dict):
        candidate_reviews = {}

    review_key = str(payload.tmdb_id)
    if payload.action == "clear":
        candidate_reviews.pop(review_key, None)
    else:
        candidate_reviews[review_key] = {
            "status": payload.action,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "note": payload.note.strip() if payload.note and payload.note.strip() else None,
        }

    cache_payload["candidate_reviews"] = candidate_reviews

    upsert_idarr_asset_cache(
        db,
        asset_key=asset_key,
        title=title,
        year=year,
        asset_type=asset_type,
        tmdb_id=cache_row.tmdb_id if cache_row else None,
        tvdb_id=cache_row.tvdb_id if cache_row else None,
        imdb_id=cache_row.imdb_id if cache_row else None,
        matched=bool(cache_row.matched) if cache_row else False,
        payload_json=json.dumps(cache_payload),
        touch_checked_at=False,
    )
    db.commit()

    log_user_action(
        "Reviewed IDarr pending candidate",
        asset_key=asset_key,
        tmdb_id=payload.tmdb_id,
        review_action=payload.action,
    )

    return {
        "success": True,
        "asset_key": asset_key,
        "tmdb_id": payload.tmdb_id,
        "action": payload.action,
        "candidate_reviews": candidate_reviews,
    }


@router.get("/ignored-titles")
async def get_maker_idarr_ignored_titles(sync_target_index: int | None = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """List ignored IDarr titles that are excluded from processing/pending lists."""
    scope_token = _resolve_scope_token(db, sync_target_index)
    items = _load_ignored_titles(db, scope_token)
    return {"items": items}


@router.post("/ignored-titles/add")
async def add_maker_idarr_ignored_title(payload: IdarrIgnoredTitleRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Add an ignored IDarr title entry."""
    title = payload.title.strip()
    # Normalize so add/remove/runner all agree on the canonical type string.
    normalized_type = _normalize_idarr_asset_type(payload.type) or payload.type.strip().lower()
    year = payload.year if isinstance(payload.year, int) else None
    if not title or not normalized_type:
        raise HTTPException(status_code=400, detail="title and type are required")

    scope_token = _resolve_scope_token(db, payload.sync_target_index)
    key = payload.asset_key.strip() if payload.asset_key and payload.asset_key.strip() else _idarr_asset_key(normalized_type, title, year, scope_token)
    items = _load_ignored_titles(db, scope_token)
    if not any(isinstance(item, dict) and item.get("asset_key") == key for item in items):
        items.append(_build_ignored_entry(title=title, year=year, asset_type=normalized_type, asset_key=key))
        _save_ignored_titles(db, items, scope_token)

    log_user_action("Added IDarr ignored title", asset_key=key)
    return {"success": True, "asset_key": key}


@router.post("/ignored-titles/remove")
async def remove_maker_idarr_ignored_title(payload: IdarrIgnoredTitleRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Remove an ignored IDarr title entry."""
    scope_token = _resolve_scope_token(db, payload.sync_target_index)
    normalized_type = _normalize_idarr_asset_type(payload.type)
    normalized_title = str(payload.title or "").strip()
    normalized_year = payload.year if isinstance(payload.year, int) else None
    key = payload.asset_key.strip() if payload.asset_key and payload.asset_key.strip() else _idarr_asset_key(normalized_type, normalized_title, normalized_year, scope_token)

    items = _load_ignored_titles(db, scope_token)
    removed_item = next((item for item in items if isinstance(item, dict) and item.get("asset_key") == key), None)
    remaining = [item for item in items if not (isinstance(item, dict) and item.get("asset_key") == key)]
    _save_ignored_titles(db, remaining, scope_token)

    restored_title = normalized_title or str(removed_item.get("title") or "").strip() if isinstance(removed_item, dict) else normalized_title
    restored_year = normalized_year
    if not isinstance(restored_year, int) and isinstance(removed_item, dict) and isinstance(removed_item.get("year"), int):
        restored_year = int(removed_item.get("year"))
    restored_type = normalized_type
    if not restored_type and isinstance(removed_item, dict):
        restored_type = _normalize_idarr_asset_type(str(removed_item.get("type") or ""))

    restored_pending = False
    cache_row = _filter_cache_query_by_scope(
        db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == key),
        scope_token,
    ).first()
    if cache_row:
        cache_payload = _extract_payload_dict(cache_row)
        cache_payload["status"] = "not_found"
        cache_payload["pending_entry"] = _normalize_pending_entry_payload(
            cache_payload.get("pending_entry"),
            title=restored_title or str(cache_row.title or ""),
            year=restored_year if isinstance(restored_year, int) else (cache_row.year if isinstance(cache_row.year, int) else None),
            files=cache_payload.get("current_filenames") or cache_payload.get("original_filenames") or cache_payload.get("files"),
        )

        upsert_idarr_asset_cache(
            db,
            asset_key=str(cache_row.asset_key or key),
            title=restored_title or str(cache_row.title or ""),
            year=restored_year if isinstance(restored_year, int) else (cache_row.year if isinstance(cache_row.year, int) else None),
            asset_type=restored_type or _normalize_idarr_asset_type(cache_row.asset_type),
            tmdb_id=cache_row.tmdb_id if isinstance(cache_row.tmdb_id, int) else None,
            tvdb_id=cache_row.tvdb_id if isinstance(cache_row.tvdb_id, int) else None,
            imdb_id=cache_row.imdb_id if isinstance(cache_row.imdb_id, str) and cache_row.imdb_id.strip() else None,
            matched=bool(cache_row.matched),
            payload_json=json.dumps(cache_payload),
            touch_checked_at=False,
        )

        is_resolved = bool(
            cache_row.matched
            or isinstance(cache_row.tmdb_id, int)
            or isinstance(cache_row.tvdb_id, int)
            or (isinstance(cache_row.imdb_id, str) and cache_row.imdb_id.strip())
        )
        if not is_resolved and (restored_title or str(cache_row.title or "").strip()) and (restored_type or _normalize_idarr_asset_type(cache_row.asset_type)):
            upsert_idarr_pending_match(
                db,
                asset_key=str(cache_row.asset_key or key),
                title=restored_title or str(cache_row.title or "").strip(),
                year=restored_year if isinstance(restored_year, int) else (cache_row.year if isinstance(cache_row.year, int) else None),
                asset_type=restored_type or _normalize_idarr_asset_type(cache_row.asset_type),
            )
            restored_pending = True
    elif restored_title and restored_type:
        upsert_idarr_pending_match(
            db,
            asset_key=key,
            title=restored_title,
            year=restored_year,
            asset_type=restored_type,
        )
        restored_pending = True

    db.commit()

    log_user_action("Removed IDarr ignored title", asset_key=key, restored_pending=restored_pending)
    return {"success": True, "asset_key": key, "restored_pending": restored_pending}


@router.post("/ignored-titles/import")
async def import_maker_idarr_ignored_titles(payload: IdarrIgnoredTitlesBulkRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Import ignored titles in bulk (exact title matches with optional year)."""
    normalized_titles = _normalize_bulk_ignored_titles(payload.titles)
    if not normalized_titles:
        raise HTTPException(status_code=400, detail="No ignored titles were provided")

    scope_token = _resolve_scope_token(db, payload.sync_target_index)
    resolved_items: list[dict[str, Any]] = []
    for title in normalized_titles:
        resolved_items.extend(_resolve_ignored_entries_for_title(db, title, scope_token))

    existing_items = _load_ignored_titles(db, scope_token)
    merged_items, added_count = _merge_ignored_items(existing_items, resolved_items)
    _save_ignored_titles(db, merged_items, scope_token)

    log_user_action(
        "Imported IDarr ignored titles",
        requested_titles=len(normalized_titles),
        resolved_entries=len(resolved_items),
        added=added_count,
        total=len(merged_items),
    )

    return {
        "success": True,
        "requested_titles": len(normalized_titles),
        "resolved_entries": len(resolved_items),
        "added": added_count,
        "total": len(merged_items),
    }


@router.post("/ignored-titles/replace")
async def replace_maker_idarr_ignored_titles(payload: IdarrIgnoredTitlesBulkRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Replace ignored titles from a manually edited list."""
    normalized_titles = _normalize_bulk_ignored_titles(payload.titles)

    scope_token = _resolve_scope_token(db, payload.sync_target_index)
    resolved_items: list[dict[str, Any]] = []
    for title in normalized_titles:
        resolved_items.extend(_resolve_ignored_entries_for_title(db, title, scope_token))

    deduped_items, _ = _merge_ignored_items([], resolved_items)
    _save_ignored_titles(db, deduped_items, scope_token)

    log_user_action(
        "Replaced IDarr ignored titles",
        requested_titles=len(normalized_titles),
        resolved_entries=len(resolved_items),
        total=len(deduped_items),
    )

    return {
        "success": True,
        "requested_titles": len(normalized_titles),
        "resolved_entries": len(resolved_items),
        "total": len(deduped_items),
    }


@router.get("/cache/stats")
async def get_maker_idarr_cache_stats(sync_target_index: int | None = None, db: Session = Depends(get_db)) -> Dict[str, int]:
    """Get cache stats for IDarr cache maintenance controls."""
    scope_token = _resolve_scope_token(db, sync_target_index)
    base_query = _filter_cache_query_by_scope(db.query(IdarrAssetCache), scope_token)
    total = base_query.count()
    matched = _filter_cache_query_by_scope(db.query(IdarrAssetCache).filter(IdarrAssetCache.matched.is_(True)), scope_token).count()
    unmatched = _filter_cache_query_by_scope(db.query(IdarrAssetCache).filter(IdarrAssetCache.matched.is_(False)), scope_token).count()
    no_check = _filter_cache_query_by_scope(db.query(IdarrAssetCache).filter(IdarrAssetCache.last_checked_at.is_(None)), scope_token).count()
    return {
        "total": int(total),
        "matched": int(matched),
        "unmatched": int(unmatched),
        "never_checked": int(no_check),
    }


@router.post("/cache/maintenance")
async def run_maker_idarr_cache_maintenance(payload: IdarrCacheMaintenanceRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Run IDarr cache maintenance actions in dedicated cache table."""
    scope_token = _resolve_scope_token(db, payload.sync_target_index)
    deleted = 0
    purged_items: list[dict[str, Any]] = []

    if payload.action == "clear_all":
        deleted = _filter_cache_query_by_scope(db.query(IdarrAssetCache), scope_token).delete(synchronize_session=False)
    elif payload.action == "prune_unmatched":
        deleted = _filter_cache_query_by_scope(
            db.query(IdarrAssetCache).filter(IdarrAssetCache.matched.is_(False)),
            scope_token,
        ).delete(synchronize_session=False)
    elif payload.action == "purge_stale":
        days = int(payload.days if payload.days is not None else 30)
        if days < 1:
            raise HTTPException(status_code=400, detail="days must be >= 1")
        threshold = datetime.now(timezone.utc) - timedelta(days=days)
        deleted = _filter_cache_query_by_scope(
            db.query(IdarrAssetCache)
            .filter(
                or_(
                    IdarrAssetCache.last_checked_at.is_(None),
                    IdarrAssetCache.last_checked_at < threshold,
                )
            ),
            scope_token,
        ).delete(synchronize_session=False)
    elif payload.action == "prune_targeted":
        filter_clauses: list[Any] = []

        title_value = str(payload.title or "").strip().lower()
        if title_value:
            filter_clauses.append(func.lower(IdarrAssetCache.title) == title_value)

        asset_key_value = str(payload.asset_key or "").strip().lower()
        if asset_key_value:
            filter_clauses.append(func.lower(IdarrAssetCache.asset_key) == asset_key_value)

        if payload.tmdb_id is not None:
            filter_clauses.append(IdarrAssetCache.tmdb_id == int(payload.tmdb_id))

        if payload.tvdb_id is not None:
            filter_clauses.append(IdarrAssetCache.tvdb_id == int(payload.tvdb_id))

        imdb_value = str(payload.imdb_id or "").strip().lower()
        if imdb_value:
            filter_clauses.append(func.lower(func.coalesce(IdarrAssetCache.imdb_id, "")) == imdb_value)

        if not filter_clauses:
            raise HTTPException(
                status_code=400,
                detail="Provide at least one criterion (title, asset_key, tmdb_id, tvdb_id, imdb_id)",
            )

        scoped_query = _filter_cache_query_by_scope(db.query(IdarrAssetCache), scope_token)
        matching_rows = (
            scoped_query
            .filter(and_(*filter_clauses))
            .order_by(IdarrAssetCache.id.desc())
            .limit(20)
            .all()
        )
        purged_items = [
            {
                "title": str(row.title or ""),
                "year": row.year,
                "asset_key": str(row.asset_key or ""),
                "tmdb_id": row.tmdb_id,
                "tvdb_id": row.tvdb_id,
                "imdb_id": row.imdb_id,
            }
            for row in matching_rows
        ]

        deleted = scoped_query.filter(and_(*filter_clauses)).delete(synchronize_session=False)
    else:
        raise HTTPException(status_code=400, detail="Unsupported maintenance action")

    db.commit()

    remaining = _filter_cache_query_by_scope(db.query(IdarrAssetCache), scope_token).count()
    log_user_action(
        "IDarr cache maintenance action",
        maintenance_action=payload.action,
        deleted=int(deleted or 0),
        remaining=int(remaining),
    )

    return {
        "success": True,
        "action": payload.action,
        "deleted": int(deleted or 0),
        "remaining": int(remaining),
        "purged_items": purged_items,
    }


@router.post("/exports/csvs")
async def export_maker_idarr_csvs(payload: IdarrExportRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Export latest IDarr run details to CSV files."""
    run_query = db.query(IdarrRun)
    scope_token, scoped_source_dir = _resolve_scope_context(db, payload.sync_target_index)
    run_query = _apply_idarr_run_scope_filter(run_query, scope_token, scoped_source_dir)
    latest_run = run_query.order_by(IdarrRun.completed_at.desc(), IdarrRun.id.desc()).first()
    if not latest_run:
        raise HTTPException(status_code=404, detail="No IDarr run data available for export")

    try:
        stats_payload = json.loads(latest_run.stats_json) if latest_run.stats_json else {}
        details_payload = json.loads(latest_run.details_json) if latest_run.details_json else {}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to parse IDarr run payload")

    if not isinstance(stats_payload, dict):
        stats_payload = {}
    if not isinstance(details_payload, dict):
        details_payload = {}

    completed_str = latest_run.completed_at.strftime("%Y%m%d_%H%M%S") if latest_run.completed_at else datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    export_root = Path(payload.output_dir).expanduser() if payload.output_dir and payload.output_dir.strip() else (app_settings.config_dir / "idarr_exports")
    export_root.mkdir(parents=True, exist_ok=True)
    export_dir = export_root / f"run_{completed_str}"
    export_dir.mkdir(parents=True, exist_ok=True)

    generated_files = export_csvs(
        stats_payload=stats_payload,
        details_payload=details_payload,
        export_dir=export_dir,
    )

    log_user_action(
        "Exported IDarr CSV reports",
        export_dir=str(export_dir),
        file_count=len(generated_files),
    )

    return {
        "success": True,
        "export_dir": str(export_dir),
        "files": generated_files,
    }


def export_csvs(*, stats_payload: dict[str, Any], details_payload: dict[str, Any], export_dir: Path) -> list[str]:
    generated_files: list[str] = []

    def _normalize_files_cell(value: Any) -> str:
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(";") if part.strip()]
            return ";".join(Path(part).name for part in parts)
        if isinstance(value, list):
            parts = [str(part).strip() for part in value if str(part).strip()]
            return ";".join(Path(part).name for part in parts)
        return ""

    def _build_fieldnames(base_fields: list[str], rows: list[dict[str, Any]]) -> list[str]:
        extra_keys: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            extra_keys.update(str(key) for key in row.keys() if str(key) not in base_fields)
        return base_fields + sorted(extra_keys)

    summary_csv = export_dir / "summary.csv"
    with summary_csv.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["key", "value"])
        for key, value in stats_payload.items():
            writer.writerow([key, json.dumps(value) if isinstance(value, (dict, list)) else value])
    generated_files.append(str(summary_csv))

    enriched_items = details_payload.get("enriched_items") if isinstance(details_payload.get("enriched_items"), list) else []
    enriched_csv = export_dir / "enriched_items.csv"
    with enriched_csv.open("w", newline="", encoding="utf-8") as csvfile:
        enriched_rows: list[dict[str, Any]] = []
        for row in enriched_items:
            if not isinstance(row, dict):
                continue
            before = row.get("before") if isinstance(row.get("before"), dict) else {}
            after = row.get("after") if isinstance(row.get("after"), dict) else {}
            export_row: dict[str, Any] = {
                "title": row.get("title"),
                "type": row.get("type"),
                "year": row.get("year"),
                "before_tmdb": before.get("tmdb"),
                "before_tvdb": before.get("tvdb"),
                "before_imdb": before.get("imdb"),
                "after_tmdb": after.get("tmdb"),
                "after_tvdb": after.get("tvdb"),
                "after_imdb": after.get("imdb"),
                "match_confidence": row.get("match_confidence"),
                "match_reason": row.get("match_reason"),
                "match_score": row.get("match_score"),
            }
            for key, value in row.items():
                if key not in {"before", "after"}:
                    export_row[key] = value
            enriched_rows.append(export_row)

        fieldnames = _build_fieldnames(
            [
                "title",
                "type",
                "year",
                "before_tmdb",
                "before_tvdb",
                "before_imdb",
                "after_tmdb",
                "after_tvdb",
                "after_imdb",
                "match_confidence",
                "match_reason",
                "match_score",
            ],
            enriched_rows,
        )
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for row in enriched_rows:
            writer.writerow(row)
    generated_files.append(str(enriched_csv))

    enrichment_payload = details_payload.get("enrichment") if isinstance(details_payload.get("enrichment"), dict) else {}
    reason_counts = enrichment_payload.get("match_reason_counts") if isinstance(enrichment_payload.get("match_reason_counts"), dict) else {}
    reasons_csv = export_dir / "match_reason_breakdown.csv"
    with reasons_csv.open("w", newline="", encoding="utf-8") as csvfile:
        fieldnames = ["reason", "count"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for reason, count in sorted(reason_counts.items(), key=lambda item: int(item[1] or 0), reverse=True):
            writer.writerow({"reason": str(reason), "count": int(count or 0)})
    generated_files.append(str(reasons_csv))

    unmatched_items = details_payload.get("unmatched_items") if isinstance(details_payload.get("unmatched_items"), list) else []
    unmatched_csv = export_dir / "unmatched_items.csv"
    with unmatched_csv.open("w", newline="", encoding="utf-8") as csvfile:
        unmatched_rows: list[dict[str, Any]] = []
        for row in unmatched_items:
            if not isinstance(row, dict):
                continue
            export_row = dict(row)
            if "files" in export_row:
                export_row["files"] = _normalize_files_cell(export_row.get("files"))
            unmatched_rows.append(export_row)

        fieldnames = _build_fieldnames(
            ["files", "title", "year", "type", "tmdb_id", "imdb_id", "tvdb_id", "match_reason"],
            unmatched_rows,
        )
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for row in unmatched_rows:
            writer.writerow(row)
    generated_files.append(str(unmatched_csv))

    duplicate_items = details_payload.get("duplicate_conflicts") if isinstance(details_payload.get("duplicate_conflicts"), list) else []
    duplicate_csv = export_dir / "duplicate_conflicts.csv"
    with duplicate_csv.open("w", newline="", encoding="utf-8") as csvfile:
        duplicate_rows: list[dict[str, Any]] = []
        for row in duplicate_items:
            if not isinstance(row, dict):
                continue
            export_row = dict(row)
            if "files" in export_row:
                export_row["files"] = _normalize_files_cell(export_row.get("files"))
            duplicate_rows.append(export_row)

        fieldnames = _build_fieldnames(
            ["timestamp", "source_path", "target_path", "resolution", "archived_path", "action_mode", "dry_run"],
            duplicate_rows,
        )
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for row in duplicate_rows:
            writer.writerow(row)
    generated_files.append(str(duplicate_csv))

    file_operations = details_payload.get("file_operations") if isinstance(details_payload.get("file_operations"), list) else []
    operation_reason_counts: dict[tuple[str, str, str], int] = {}
    for row in file_operations:
        if not isinstance(row, dict):
            continue
        operation = str(row.get("operation") or "unknown").strip().lower() or "unknown"
        status = str(row.get("status") or "unknown").strip().lower() or "unknown"
        reason = str(row.get("reason") or "unknown").strip().lower() or "unknown"
        key = (operation, status, reason)
        operation_reason_counts[key] = int(operation_reason_counts.get(key, 0)) + 1

    operation_reasons_csv = export_dir / "file_operation_reason_breakdown.csv"
    with operation_reasons_csv.open("w", newline="", encoding="utf-8") as csvfile:
        fieldnames = ["operation", "status", "reason", "count"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for (operation, status, reason), count in sorted(
            operation_reason_counts.items(),
            key=lambda item: int(item[1]),
            reverse=True,
        ):
            writer.writerow(
                {
                    "operation": operation,
                    "status": status,
                    "reason": reason,
                    "count": int(count),
                }
            )
    generated_files.append(str(operation_reasons_csv))

    return generated_files


@router.post("/revert-latest")
async def revert_maker_idarr_latest_run(payload: IdarrRevertRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Revert latest IDarr run using recorded file operation history."""
    run_query = db.query(IdarrRun)
    scope_token, scoped_source_dir = _resolve_scope_context(db, payload.sync_target_index)
    run_query = _apply_idarr_run_scope_filter(run_query, scope_token, scoped_source_dir)
    latest_run = run_query.order_by(IdarrRun.completed_at.desc(), IdarrRun.id.desc()).first()
    if not latest_run:
        raise HTTPException(status_code=404, detail="No IDarr run data available for revert")

    try:
        details_payload = json.loads(latest_run.details_json) if latest_run.details_json else {}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to parse IDarr run details")

    if not isinstance(details_payload, dict):
        details_payload = {}

    operations = details_payload.get("file_operations") if isinstance(details_payload.get("file_operations"), list) else []
    fallback_history_rows: list[tuple[IdarrAssetCache, dict[str, Any]]] = []

    if not operations:
        base_dir_raw = str(latest_run.destination_dir or latest_run.source_dir or "").strip()
        if base_dir_raw:
            base_dir = Path(base_dir_raw)
            cache_rows = _filter_cache_query_by_scope(db.query(IdarrAssetCache), scope_token).all()
            for row in cache_rows:
                if not isinstance(row.payload_json, str) or not row.payload_json.strip():
                    continue
                row_payload: dict[str, Any] | None = None
                try:
                    row_payload = json.loads(row.payload_json)
                except Exception:
                    row_payload = None
                if row_payload is None:
                    continue
                if not isinstance(row_payload, dict):
                    continue

                rename_history = row_payload.get("rename_history")
                if not isinstance(rename_history, list) or not rename_history:
                    continue

                row_has_valid_entries = False
                for history_entry in reversed(rename_history):
                    if not isinstance(history_entry, dict):
                        continue
                    to_name = str(history_entry.get("to") or "").strip()
                    from_name = str(history_entry.get("from") or "").strip()
                    if not to_name or not from_name:
                        continue

                    row_has_valid_entries = True
                    operations.append(
                        {
                            "operation": "rename",
                            "status": "success",
                            "revert_supported": True,
                            "from_path": str(base_dir / from_name),
                            "to_path": str(base_dir / to_name),
                            "reason": "cache_rename_history",
                        }
                    )

                if row_has_valid_entries:
                    fallback_history_rows.append((row, row_payload))

    if not operations:
        raise HTTPException(status_code=400, detail="Latest run has no reversible file operations")

    reverted, skipped, errors, actions = perform_revert(operations, dry_run=payload.dry_run)

    if fallback_history_rows and not payload.dry_run:
        for row, row_payload in fallback_history_rows:
            row_payload["rename_history"] = []
            row.payload_json = json.dumps(row_payload)
        db.commit()

    log_user_action(
        "Reverted latest IDarr run",
        run_id=latest_run.id,
        dry_run=payload.dry_run,
        reverted=reverted,
        skipped=skipped,
        errors=errors,
    )

    return {
        "success": errors == 0,
        "dry_run": payload.dry_run,
        "run_id": latest_run.id,
        "reverted": reverted,
        "skipped": skipped,
        "errors": errors,
        "used_cache_rename_history": bool(fallback_history_rows),
        "actions": actions[:100],
    }


def perform_revert(operations: list[dict[str, Any]], *, dry_run: bool) -> tuple[int, int, int, list[dict[str, Any]]]:
    reverted = 0
    skipped = 0
    errors = 0
    actions: list[dict[str, Any]] = []

    for op in reversed(operations):
        if not isinstance(op, dict):
            continue

        operation = str(op.get("operation") or "").strip().lower()
        status = str(op.get("status") or "").strip().lower()
        revert_supported = op.get("revert_supported")
        if not isinstance(revert_supported, bool):
            revert_supported = operation in {"move", "rename", "copy"}

        if not revert_supported or status != "success":
            skipped += 1
            actions.append({"operation": op.get("operation"), "status": "skipped", "reason": "not_revertable"})
            continue

        from_path_raw = str(op.get("from_path") or "").strip()
        to_path_raw = str(op.get("to_path") or "").strip()

        if not from_path_raw or not to_path_raw:
            skipped += 1
            actions.append({"operation": operation, "status": "skipped", "reason": "missing_paths"})
            continue

        from_path = Path(from_path_raw)
        to_path = Path(to_path_raw)

        try:
            if operation in {"move", "rename"}:
                if to_path.exists():
                    if not dry_run:
                        from_path.parent.mkdir(parents=True, exist_ok=True)
                        to_path.replace(from_path)
                    reverted += 1
                    actions.append({"operation": operation, "status": "reverted", "from": str(to_path), "to": str(from_path)})
                else:
                    skipped += 1
                    actions.append({"operation": operation, "status": "skipped", "reason": "target_missing"})
            elif operation == "copy":
                if to_path.exists():
                    if not dry_run:
                        to_path.unlink()
                    reverted += 1
                    actions.append({"operation": operation, "status": "reverted", "deleted": str(to_path)})
                else:
                    skipped += 1
                    actions.append({"operation": operation, "status": "skipped", "reason": "target_missing"})
            else:
                skipped += 1
                actions.append({"operation": operation, "status": "skipped", "reason": "unsupported_operation"})
        except Exception as e:
            errors += 1
            actions.append({"operation": operation, "status": "error", "reason": str(e)})

    return reverted, skipped, errors, actions