from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from util.data.normalization import normalize_titles

from database import Base


class IdarrRun(Base):
    """Stores a historical record of Idarr runs and their summary payload."""

    __tablename__ = "idarr_runs"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, nullable=True, index=True)
    success = Column(Boolean, nullable=False, default=False)
    source_dir = Column(String, nullable=True)
    destination_dir = Column(String, nullable=True)
    scope_token = Column(String, nullable=True, index=True)
    stats_json = Column(Text, nullable=True)
    details_json = Column(Text, nullable=True)
    warnings_json = Column(Text, nullable=True)
    unmatched_count = Column(Integer, nullable=False, default=0)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class IdarrPendingMatch(Base):
    """Stores pending/unmatched Idarr assets for explicit resolver workflows."""

    __tablename__ = "idarr_pending_matches"

    id = Column(Integer, primary_key=True, index=True)
    asset_key = Column(String, nullable=False, unique=True, index=True)
    title = Column(String, nullable=False)
    year = Column(Integer, nullable=True)
    asset_type = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class IdarrAssetCache(Base):
    """Stores Idarr metadata cache records, separate from poster manager state."""

    __tablename__ = "idarr_asset_cache"

    id = Column(Integer, primary_key=True, index=True)
    asset_key = Column(String, nullable=False, unique=True, index=True)
    title = Column(String, nullable=False)
    year = Column(Integer, nullable=True)
    asset_type = Column(String, nullable=False)
    tmdb_id = Column(Integer, nullable=True)
    tvdb_id = Column(Integer, nullable=True)
    imdb_id = Column(String, nullable=True)
    matched = Column(Boolean, nullable=False, default=False)
    payload_json = Column(Text, nullable=True)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


def upsert_idarr_pending_match(
    db: Session,
    *,
    asset_key: str,
    title: str,
    year: int | None,
    asset_type: str,
) -> IdarrPendingMatch:
    """Upsert a pending match row by asset key."""
    for pending in db.new:
        if isinstance(pending, IdarrPendingMatch) and pending.asset_key == asset_key:
            pending.title = title
            pending.year = year
            pending.asset_type = asset_type
            pending.updated_at = datetime.now(timezone.utc)
            return pending

    row = db.query(IdarrPendingMatch).filter(IdarrPendingMatch.asset_key == asset_key).first()
    if row:
        row.title = title
        row.year = year
        row.asset_type = asset_type
        row.updated_at = datetime.now(timezone.utc)
        return row

    row = IdarrPendingMatch(
        asset_key=asset_key,
        title=title,
        year=year,
        asset_type=asset_type,
        updated_at=datetime.now(timezone.utc),
    )
    db.add(row)
    return row


def upsert_idarr_asset_cache(
    db: Session,
    *,
    asset_key: str,
    title: str,
    year: int | None,
    asset_type: str,
    tmdb_id: int | None,
    tvdb_id: int | None,
    imdb_id: str | None,
    matched: bool,
    payload_json: str | None,
    touch_checked_at: bool = True,
) -> IdarrAssetCache:
    """Upsert an Idarr asset cache record by asset key."""
    now = datetime.now(timezone.utc)

    for pending in db.new:
        if isinstance(pending, IdarrAssetCache) and pending.asset_key == asset_key:
            pending.title = title
            pending.year = year
            pending.asset_type = asset_type
            pending.tmdb_id = tmdb_id
            pending.tvdb_id = tvdb_id
            pending.imdb_id = imdb_id
            pending.matched = matched
            pending.payload_json = payload_json
            if touch_checked_at:
                pending.last_checked_at = now
            pending.updated_at = now
            return pending

    row = db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == asset_key).first()
    if row:
        row.title = title
        row.year = year
        row.asset_type = asset_type
        row.tmdb_id = tmdb_id
        row.tvdb_id = tvdb_id
        row.imdb_id = imdb_id
        row.matched = matched
        row.payload_json = payload_json
        if touch_checked_at:
            row.last_checked_at = now
        row.updated_at = now
        return row

    row = IdarrAssetCache(
        asset_key=asset_key,
        title=title,
        year=year,
        asset_type=asset_type,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
        imdb_id=imdb_id,
        matched=matched,
        payload_json=payload_json,
        last_checked_at=now if touch_checked_at else None,
        updated_at=now,
    )
    db.add(row)
    return row


def create_idarr_run(
    db: Session,
    *,
    job_id: int | None,
    success: bool,
    source_dir: str | None,
    destination_dir: str | None,
    scope_token: str | None,
    stats_json: str | None,
    details_json: str | None,
    warnings_json: str | None,
    unmatched_count: int,
) -> IdarrRun:
    """Create and add an Idarr run record."""
    run = IdarrRun(
        job_id=job_id,
        success=success,
        source_dir=source_dir,
        destination_dir=destination_dir,
        scope_token=scope_token,
        stats_json=stats_json,
        details_json=details_json,
        warnings_json=warnings_json,
        unmatched_count=unmatched_count,
        completed_at=datetime.now(timezone.utc),
    )
    db.add(run)
    return run


def prune_idarr_run_history(
    db: Session,
    *,
    keep_latest: int,
    scope_token: str | None,
) -> int:
    """Prune old IdarrRun rows, keeping only the newest rows in a scope bucket.

    Scope bucket semantics:
    - Non-empty ``scope_token``: rows matching that exact token.
    - Empty/None ``scope_token``: legacy rows with NULL/blank scope token.
    """
    try:
        keep_count = int(keep_latest)
    except Exception:
        keep_count = 0

    if keep_count < 1:
        return 0

    normalized_scope_token = sanitize_idarr_scope_token(scope_token)

    query = db.query(IdarrRun.id)
    if normalized_scope_token:
        query = query.filter(IdarrRun.scope_token == normalized_scope_token)
    else:
        query = query.filter((IdarrRun.scope_token.is_(None)) | (func.trim(IdarrRun.scope_token) == ""))

    stale_rows = (
        query
        .order_by(
            IdarrRun.completed_at.desc().nullslast(),
            IdarrRun.created_at.desc().nullslast(),
            IdarrRun.id.desc(),
        )
        .offset(keep_count)
        .all()
    )
    stale_ids = [int(row.id) for row in stale_rows if getattr(row, "id", None) is not None]
    if not stale_ids:
        return 0

    deleted = db.query(IdarrRun).filter(IdarrRun.id.in_(stale_ids)).delete(synchronize_session=False)
    return int(deleted or 0)


def compact_idarr_run_details_history(
    db: Session,
    *,
    keep_full_latest: int,
    scope_token: str | None,
) -> int:
    """Compact heavy `details_json.file_operations` payloads for older runs.

    Keeps the newest ``keep_full_latest`` runs in a scope bucket untouched so
    latest-run export/revert behavior remains unchanged.
    """
    try:
        keep_count = int(keep_full_latest)
    except Exception:
        keep_count = 1

    if keep_count < 1:
        keep_count = 1

    normalized_scope_token = sanitize_idarr_scope_token(scope_token)

    query = db.query(IdarrRun)
    if normalized_scope_token:
        query = query.filter(IdarrRun.scope_token == normalized_scope_token)
    else:
        query = query.filter((IdarrRun.scope_token.is_(None)) | (func.trim(IdarrRun.scope_token) == ""))

    target_runs = (
        query
        .order_by(
            IdarrRun.completed_at.desc().nullslast(),
            IdarrRun.created_at.desc().nullslast(),
            IdarrRun.id.desc(),
        )
        .offset(keep_count)
        .all()
    )

    compacted = 0
    for run in target_runs:
        raw_details = str(run.details_json or "").strip()
        if not raw_details:
            continue

        try:
            details_payload = json.loads(raw_details)
        except json.JSONDecodeError:
            continue
        if not isinstance(details_payload, dict):
            continue

        file_operations = details_payload.get("file_operations")
        if not isinstance(file_operations, list) or not file_operations:
            continue

        status_counts: dict[str, int] = {}
        operation_counts: dict[str, int] = {}
        reason_counts: dict[str, int] = {}

        for row in file_operations:
            if not isinstance(row, dict):
                continue
            operation = str(row.get("operation") or "unknown").strip().lower() or "unknown"
            status = str(row.get("status") or "unknown").strip().lower() or "unknown"
            reason = str(row.get("reason") or "unknown").strip().lower() or "unknown"
            operation_counts[operation] = int(operation_counts.get(operation, 0)) + 1
            status_counts[status] = int(status_counts.get(status, 0)) + 1
            reason_key = f"{operation}:{status}:{reason}"
            reason_counts[reason_key] = int(reason_counts.get(reason_key, 0)) + 1

        top_reason_counts = [
            {"key": key, "count": int(count)}
            for key, count in sorted(reason_counts.items(), key=lambda item: int(item[1]), reverse=True)[:25]
        ]

        details_payload["file_operations_sample"] = file_operations[:25]
        details_payload["file_operation_summary"] = {
            "total_operations": len(file_operations),
            "operation_counts": operation_counts,
            "status_counts": status_counts,
            "top_reason_counts": top_reason_counts,
        }
        details_payload["file_operations_compacted"] = True
        details_payload.pop("file_operations", None)

        run.details_json = json.dumps(details_payload)
        compacted += 1

    return int(compacted)


def idarr_run_to_dict(run: IdarrRun) -> dict[str, Any]:
    """Convert an IdarrRun ORM row into API-safe payload."""
    return {
        "id": run.id,
        "job_id": run.job_id,
        "success": bool(run.success),
        "source_dir": run.source_dir,
        "destination_dir": run.destination_dir,
        "scope_token": run.scope_token,
        "stats_json": run.stats_json,
        "details_json": run.details_json,
        "warnings_json": run.warnings_json,
        "unmatched_count": run.unmatched_count,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


def sanitize_idarr_scope_token(value: Any) -> str | None:
    """Return a normalized scope token string or None when missing."""
    if not isinstance(value, str):
        return None
    token = value.strip()
    return token or None


def normalize_idarr_asset_type(asset_type: Any) -> str | None:
    """Return the canonical Idarr asset type string for *asset_type*, or ``None`` if not recognised.

    Canonical values: ``"movie"``, ``"tv_series"``, ``"collection"``, ``"pending"``.
    """
    raw = str(asset_type or "").strip().lower()
    if raw in {"movie", "movies"}:
        return "movie"
    if raw in {"tv_series", "series", "tv", "show", "shows", "season", "seasons"}:
        return "tv_series"
    if raw in {"collection", "collections"}:
        return "collection"
    if raw == "pending":
        return "pending"
    return None


def build_idarr_asset_key(
    asset_type: str,
    title: str,
    year: int | None,
    scope_token: str | None = None,
    *,
    tmdb_id: int | None = None,
    tvdb_id: int | None = None,
    imdb_id: str | None = None,
) -> str:
    """Build the Idarr asset key for cache/pending lookups.

    Resolved (ID known): ``<type>::tmdb=<id>[::scope=<token>]`` (tvdb=/imdb= when no tmdb).
    Provisional (no ID): ``<type>::<normalised_title>::<year>[::scope=<token>]``.

    Keying resolved rows by id (not title) means one item = one row per scope regardless of
    filename. Unresolved rows stay title/year keyed until matched.
    """
    asset_type_part = str(asset_type or "").strip().lower()

    # ID known → key by id alone so name changes can't fork an item.
    if isinstance(tmdb_id, int):
        key = f"{asset_type_part}::tmdb={tmdb_id}"
    elif isinstance(tvdb_id, int):
        key = f"{asset_type_part}::tvdb={tvdb_id}"
    elif isinstance(imdb_id, str) and str(imdb_id).strip():
        key = f"{asset_type_part}::imdb={imdb_id.strip()}"
    else:
        normalized_title = normalize_titles(str(title or "").strip())
        year_part = str(year) if isinstance(year, int) else ""
        key = f"{asset_type_part}::{normalized_title}::{year_part}"

    if scope_token:
        return f"{key}::scope={scope_token}"
    return key


def build_idarr_scope_token(sync_target_index: int, source_dir: str) -> str:
    """Build a stable scope token that ties a sync-target index to its source directory."""
    normalized_source = str(source_dir or "").strip().lower()
    digest = hashlib.sha1(
        f"{sync_target_index}|{normalized_source}".encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:16]
    return f"t{sync_target_index}_{digest}"


def resolve_idarr_scope_token(sync_target: dict[str, Any], sync_target_index: int) -> str | None:
    """Resolve a stable scope token for a sync target.

    Priority order:
    1) Existing persisted ``scope_token``
    2) Legacy token derived from ``sync_target_index`` + ``source_dir``
    3) None for targets without a source directory
    """
    persisted = sanitize_idarr_scope_token(sync_target.get("scope_token"))
    if persisted:
        return persisted

    source_dir = str(sync_target.get("source_dir") or "").strip()
    if source_dir:
        return build_idarr_scope_token(sync_target_index, source_dir)

    return None


def make_pending_entry_payload(title: str, year: int | None = None, files: Any = None) -> dict[str, str]:
    """Build the standard pending-entry payload dict for an unmatched asset."""
    query = f"{title} {year}" if isinstance(year, int) else title
    google_search = f"https://www.google.com/search?q={quote_plus(query + ' site:themoviedb.org')}"

    file_str = ""
    if isinstance(files, list) and files:
        first = str(files[0]).strip()
        file_str = str(Path(first).resolve()) if first else ""
    elif isinstance(files, str) and files.strip():
        file_str = str(Path(files.strip()).resolve())

    return {
        "add_tmdb_url_here": "add_tmdb_url_here",
        "google_search": google_search,
        "files": file_str,
    }
