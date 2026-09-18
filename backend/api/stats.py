from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import Float, func, and_
from typing import Any, Dict, List, Optional
from pathlib import Path
from datetime import datetime, timezone, timedelta, time as dt_time
import html
import os
import re
import threading
from unidecode import unidecode
from PIL import Image, UnidentifiedImageError
from core.config import settings as app_settings
from database import get_db
from util.constants import common_words, illegal_chars_regex, remove_special_chars
from models.job import (
    JOB_STATUS_COMPLETED,
    JOB_TYPE_POSTER_RENAMER,
    JOB_TYPE_BORDER_REPLACER,
    JOB_TYPE_GDRIVE_SYNC,
)
from models.drive import Drive
from models.poster import Poster
from models.artwork import Artwork
from models.artwork_drive import ArtworkDrive, ARTWORK_TYPES
from models.job import Job

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _is_path_within(parent: Path, child: Path) -> bool:
    """Return True when child is inside parent (or equal)."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _normalize_for_search(text: str) -> str:
    """Fold a title or filename to a comparable key so accents, colons, apostrophes
    and ampersands don't block a match (e.g. "Pokémon: The Movie" -> "pokemonthemovie").
    Per-character only (no word/year stripping) so it preserves substring containment."""
    cleaned = unidecode(html.unescape(text)).replace("&", " and ")
    cleaned = illegal_chars_regex.sub("", cleaned)
    cleaned = remove_special_chars.sub("", cleaned)
    return cleaned.replace(" ", "").lower().strip()


_ID_TAG_RE = re.compile(r"\{[^}]*\}")
_ID_QUERY_RE = re.compile(r"tmdb|tvdb|imdb|\btt\d", re.IGNORECASE)


def _stem_matches(normalized_query: str, raw_query: str, stem: str) -> bool:
    """Precise match for a search hit: the title part of the file name matches anywhere; the
    {tmdb-…}/{tvdb-…}/{imdb-…} tags only as a whole id, or when the query names the id source.
    Otherwise a numeric title like "1883" drags in every file whose id contains those digits."""
    if normalized_query in _normalize_for_search(_ID_TAG_RE.sub("", stem)):
        return True
    tags = _ID_TAG_RE.findall(stem)
    if not tags:
        return False
    if _ID_QUERY_RE.search(raw_query):
        return normalized_query in _normalize_for_search(" ".join(tags))
    return normalized_query in {_normalize_for_search(tag.strip("{}").split("-", 1)[-1]) for tag in tags}


def _like_pattern(anchor: str) -> str:
    """Escape LIKE wildcards so a title's %/_ don't act as wildcards."""
    escaped = anchor.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _search_anchors(query: str) -> List[str]:
    """Distinctive words the filename must ALL contain, taken as the query's raw
    alphanumeric runs. AND-ed in SQL so the prefilter stays tight — a broad OR
    would match more rows than the row cap and starve alphabetically-later true
    matches before the precise Python pass runs. Runs are matched literally, so we
    split the RAW query (not the unidecoded form): a symbol/accent unidecode drops
    or changes mid-word (e.g. "YUME∞MITA", "Amélie") stays in the stored filename,
    and the surrounding ASCII runs match whichever way either side keeps it.
    Falls back to every word when none are distinctive."""
    words = re.findall(r"[A-Za-z0-9]+", query)
    common = {w.lower() for w in common_words}
    anchors: List[str] = []
    seen = set()
    for word in words:
        lowered = word.lower()
        if len(word) < 2 or lowered in common or lowered in seen:
            continue
        seen.add(lowered)
        anchors.append(word)
    fallback = words or [_normalize_for_search(query)]
    return (anchors or fallback)[:8]


@router.get("/poster-search")
def search_posters(
    q: str,
    limit: int = 200,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Search synced poster records by filename and return grouped matches with drive locations.
    Results are limited strictly to files located within configured drive folders.
    """
    query = q.strip()
    if not query:
        return {"query": "", "count": 0, "items": []}

    normalized_query = _normalize_for_search(query)
    if not normalized_query:
        return {"query": query, "count": 0, "items": []}

    safe_limit = max(1, min(limit, 500))
    raw_limit = min(safe_limit * 8, 4000)

    # Tight SQL prefilter: filename must contain every distinctive query word.
    # The precise, accent-insensitive match happens in Python via _normalize_for_search below.
    prefilter = and_(
        *[Poster.file_name.ilike(_like_pattern(a), escape="\\") for a in _search_anchors(query)]
    )
    rows = (
        db.query(Poster, Drive)
        .join(Drive, Poster.drive_id == Drive.drive_id)
        .filter(prefilter)
        .order_by(Poster.file_name.asc(), Drive.name.asc())
        .limit(raw_limit)
        .all()
    )

    grouped: Dict[str, Dict[str, Any]] = {}

    for poster, drive in rows:
        poster_name = Path(poster.file_name).stem
        # Precise, accent/punctuation-insensitive match (the SQL prefilter is loose).
        if not _stem_matches(normalized_query, query, poster_name):
            continue

        if _searchable_file(drive, poster.file_path) is None:
            continue

        group_key = poster_name.casefold()

        if group_key not in grouped:
            grouped[group_key] = {
                "poster_name": poster_name,
                "drives": {},
            }

        drives_map = grouped[group_key]["drives"]
        if drive.drive_id not in drives_map:
            drive_type = "custom" if drive.is_custom else drive.style_type.lower()
            drives_map[drive.drive_id] = {
                "drive_id": drive.drive_id,
                "drive_name": drive.name,
                "drive_type": drive_type,
                "style_type": drive.style_type,
                "is_custom": drive.is_custom,
                "poster_id": poster.id,
                "image_url": f"/api/stats/posters/{poster.id}/image",
                "file_path": poster.file_path,
            }

    items = []
    for group in grouped.values():
        drives = sorted(group["drives"].values(), key=lambda item: item["drive_name"].casefold())
        items.append(
            {
                "poster_name": group["poster_name"],
                "drives": drives,
                "drive_count": len(drives),
            }
        )

    items.sort(key=lambda item: item["poster_name"].casefold())
    items = items[:safe_limit]

    return {
        "query": query,
        "count": len(items),
        "items": items,
    }

@router.get("/artwork-search")
def search_artwork(
    q: str,
    types: Optional[str] = None,
    limit: int = 200,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Search synced artwork records (logos / backgrounds / square art) by filename and return
    matches grouped by name + type with every drive that offers them. Mirrors /poster-search,
    including its inside-the-drive-folder guard. `types` narrows to a comma list of artwork types."""
    query = q.strip()
    if not query:
        return {"query": "", "count": 0, "items": []}

    normalized_query = _normalize_for_search(query)
    if not normalized_query:
        return {"query": query, "count": 0, "items": []}

    wanted = [t.strip() for t in (types or "").split(",") if t.strip() in ARTWORK_TYPES] or list(ARTWORK_TYPES)
    safe_limit = max(1, min(limit, 500))
    raw_limit = min(safe_limit * 8, 4000)

    prefilter = and_(
        *[Artwork.file_name.ilike(_like_pattern(a), escape="\\") for a in _search_anchors(query)]
    )
    rows = (
        db.query(Artwork, ArtworkDrive)
        .join(ArtworkDrive, Artwork.artwork_drive_id == ArtworkDrive.drive_id)
        .filter(prefilter, Artwork.artwork_type.in_(wanted))
        .order_by(Artwork.file_name.asc(), ArtworkDrive.name.asc())
        .limit(raw_limit)
        .all()
    )

    grouped: Dict[tuple, Dict[str, Any]] = {}
    for artwork, drive in rows:
        name = Path(artwork.file_name).stem
        if not _stem_matches(normalized_query, query, name):
            continue
        if _searchable_file(drive, artwork.file_path) is None:
            continue

        group = grouped.setdefault(
            (name.casefold(), artwork.artwork_type),
            {"artwork_name": name, "artwork_type": artwork.artwork_type, "drives": {}},
        )
        group["drives"].setdefault(drive.drive_id, {
            "drive_id": drive.drive_id,
            "drive_name": drive.display_name or drive.name,
            "drive_type": "custom" if drive.is_custom else "artwork",
            "is_custom": bool(drive.is_custom),
            "artwork_id": artwork.id,
            "image_url": f"/api/stats/artwork/{artwork.id}/image",
            "file_path": artwork.file_path,
        })

    items = []
    for group in grouped.values():
        drives = sorted(group["drives"].values(), key=lambda item: item["drive_name"].casefold())
        items.append({
            "artwork_name": group["artwork_name"],
            "artwork_type": group["artwork_type"],
            "drives": drives,
            "drive_count": len(drives),
        })
    items.sort(key=lambda item: (item["artwork_name"].casefold(), ARTWORK_TYPES.index(item["artwork_type"])))
    items = items[:safe_limit]

    return {"query": query, "count": len(items), "items": items}


@router.get("/")
def get_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Get comprehensive statistics for the dashboard"""
    
    # Get drive statistics
    all_drives = db.query(Drive).all()
    subscribed_drives = [d for d in all_drives if d.subscribed]
    synced_drives = [d for d in all_drives if d.subscribed and d.last_synced is not None]
    
    # Count by type for all drives
    all_cl2k = len([d for d in all_drives if d.style_type == 'CL2K'])
    all_mm2k = len([d for d in all_drives if d.style_type == 'MM2K'])
    all_custom = len([d for d in all_drives if d.is_custom])
    
    # Count by type for subscribed drives
    sub_cl2k = len([d for d in subscribed_drives if d.style_type == 'CL2K'])
    sub_mm2k = len([d for d in subscribed_drives if d.style_type == 'MM2K'])
    sub_custom = len([d for d in subscribed_drives if d.is_custom])
    
    # Get poster counts (source drives only - excludes destination/tmp/processed artifacts)
    total_posters = db.query(func.count(Poster.id)).join(
        Drive, Poster.drive_id == Drive.drive_id
    ).scalar() or 0
    
    # Get posters by drive type for subscribed drives
    posters_cl2k = db.query(func.count(Poster.id)).join(
        Drive, Poster.drive_id == Drive.drive_id
    ).filter(
        Drive.style_type == 'CL2K',
        Drive.subscribed == True
    ).scalar() or 0
    
    posters_mm2k = db.query(func.count(Poster.id)).join(
        Drive, Poster.drive_id == Drive.drive_id
    ).filter(
        Drive.style_type == 'MM2K',
        Drive.subscribed == True
    ).scalar() or 0
    
    posters_custom = db.query(func.count(Poster.id)).join(
        Drive, Poster.drive_id == Drive.drive_id
    ).filter(
        Drive.is_custom == True,
        Drive.subscribed == True
    ).scalar() or 0
    
    subscribed_posters = posters_cl2k + posters_mm2k + posters_custom
    
    return {
        "total_posters": total_posters,
        "subscribed_posters": subscribed_posters,
        "posters_by_type": {
            "cl2k": posters_cl2k,
            "mm2k": posters_mm2k,
            "custom": posters_custom
        },
        "drives": {
            "total": len(all_drives),
            "subscribed": len(subscribed_drives),
            "synced": len(synced_drives),
            "by_type": {
                "cl2k": all_cl2k,
                "mm2k": all_mm2k,
                "custom": all_custom
            }
        },
        "subscribed_drives_by_type": {
            "cl2k": sub_cl2k,
            "mm2k": sub_mm2k,
            "custom": sub_custom
        }
    }


def _local_file_within_drive(drive: Drive | ArtworkDrive, file_path: str) -> Optional[Path]:
    """The record's file when it still exists inside its drive's local root, else None."""
    try:
        drive_root = drive.get_local_path(validate=False).resolve()
        path = Path(file_path).resolve()
    except Exception:
        return None
    if not _is_path_within(drive_root, path) or not path.is_file():
        return None
    return path


def _searchable_file(drive, file_path: str) -> Optional[Path]:
    """The indexed file when it sits inside its drive's local folder and not under a temp or
    assets folder — the only files the searches may expose."""
    try:
        drive_root = drive.get_local_path(validate=False).resolve()
        path = Path(file_path).resolve()
    except Exception:
        return None
    if not _is_path_within(drive_root, path):
        return None
    parts = {part.lower() for part in path.parts}
    if "assets" in parts or "tmp" in parts or "temp" in parts:
        return None
    return path


def _content_recency(model):
    """Newest content first: file mtime, falling back to the DB download time."""
    return func.coalesce(model.file_mtime, func.strftime('%s', model.downloaded_at).cast(Float), 0.0)


def _recent_synced_items(rows, limit: int, image_route: str) -> List[Dict[str, Any]]:
    """Walk (record, drive) rows newest-first, keeping only files still on disk inside their drive root."""
    items: List[Dict[str, Any]] = []
    for record, drive in rows:
        if _local_file_within_drive(drive, record.file_path) is None:
            continue
        items.append(
            {
                "id": record.id,
                "file_name": record.file_name,
                "drive_id": drive.drive_id,
                "drive_name": drive.name,
                "downloaded_at": record.downloaded_at.isoformat() if record.downloaded_at else None,
                "file_mtime": record.file_mtime,
                "image_url": f"/api/stats/{image_route}/{record.id}/image",
            }
        )
        if len(items) >= limit:
            break
    return items


@router.get("/recent-posters")
def get_recent_synced_posters(limit: int = 10, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Most recent posters for the dashboard carousel, newest content (file mtime) first."""
    safe_limit = max(1, min(limit, 100))
    rows = (
        db.query(Poster, Drive)
        .join(Drive, Poster.drive_id == Drive.drive_id)
        .filter(Drive.subscribed == True)
        .order_by(_content_recency(Poster).desc(), Poster.downloaded_at.desc(), Poster.id.desc())
        .limit(min(safe_limit * 8, 800))
        .all()
    )
    items = _recent_synced_items(rows, safe_limit, "posters")
    return {"items": items, "count": len(items)}


@router.get("/recent-artwork")
def get_recent_synced_artwork(
    artwork_type: str = Query(alias="type"),
    limit: int = 10,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Most recent artwork of one type (logo / background / squareart) for the dashboard carousel."""
    if artwork_type not in ARTWORK_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown artwork type: {artwork_type}")
    safe_limit = max(1, min(limit, 100))
    rows = (
        db.query(Artwork, ArtworkDrive)
        .join(ArtworkDrive, Artwork.artwork_drive_id == ArtworkDrive.drive_id)
        .filter(ArtworkDrive.subscribed == True, Artwork.artwork_type == artwork_type)
        .order_by(_content_recency(Artwork).desc(), Artwork.downloaded_at.desc(), Artwork.id.desc())
        .limit(min(safe_limit * 8, 800))
        .all()
    )
    items = _recent_synced_items(rows, safe_limit, "artwork")
    return {"items": items, "count": len(items)}


_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _thumbnail_for(cache_key: str, image_path: Path, width: int) -> Path:
    """Cached LANCZOS-downscaled thumbnail, regenerated when the source changes. Images with
    transparency (logos) stay PNG; everything else becomes JPEG."""
    cache_dir = app_settings.config_dir / "cache" / "poster_thumbs"
    cache_dir.mkdir(parents=True, exist_ok=True)

    mtime_ns = image_path.stat().st_mtime_ns
    stem = f"{cache_key}_{mtime_ns}_{width}"
    for ext in ("jpg", "png"):
        cached = cache_dir / f"{stem}.{ext}"
        if cached.is_file():
            return cached

    with Image.open(image_path) as img:
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        img = img.convert("RGBA" if has_alpha else "RGB")
        if img.width > width:
            height = max(1, round(img.height * width / img.width))
            img = img.resize((width, height), Image.LANCZOS)
        thumb_path = cache_dir / f"{stem}.{'png' if has_alpha else 'jpg'}"
        # Write-then-rename so concurrent requests never read a half-written thumb
        tmp_path = thumb_path.with_name(f".{thumb_path.name}.{threading.get_ident()}.tmp")
        if has_alpha:
            img.save(tmp_path, "PNG")
        else:
            img.save(tmp_path, "JPEG", quality=85)
    os.replace(tmp_path, thumb_path)

    # Drop variants from older versions of this image
    for stale in cache_dir.glob(f"{cache_key}_*_{width}.*"):
        if stale != thumb_path:
            stale.unlink(missing_ok=True)

    return thumb_path


def _serve_image(cache_key: str, image_path: Path, width: Optional[int]) -> FileResponse:
    """The full file, or a cached thumbnail when a width is requested and the image decodes."""
    if width is not None:
        try:
            return FileResponse(str(_thumbnail_for(cache_key, image_path, width)), headers=_NO_CACHE_HEADERS)
        except (UnidentifiedImageError, OSError):
            pass
    return FileResponse(str(image_path), headers=_NO_CACHE_HEADERS)


@router.get("/posters/{poster_id}/image")
def get_poster_image(
    poster_id: int,
    w: Optional[int] = Query(default=None, ge=50, le=1000),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Stream poster image by poster record id; `w` serves a downscaled thumbnail."""
    poster = db.query(Poster).filter(Poster.id == poster_id).first()
    if not poster:
        raise HTTPException(status_code=404, detail="Poster not found")
    drive = db.query(Drive).filter(Drive.drive_id == poster.drive_id).first()
    poster_path = _local_file_within_drive(drive, poster.file_path) if drive else None
    if poster_path is None:
        raise HTTPException(status_code=404, detail="Poster file not found")
    return _serve_image(str(poster_id), poster_path, w)


@router.get("/artwork/{artwork_id}/image")
def get_artwork_image(
    artwork_id: int,
    w: Optional[int] = Query(default=None, ge=50, le=1000),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Stream an artwork file by record id; `w` serves a downscaled thumbnail."""
    artwork = db.query(Artwork).filter(Artwork.id == artwork_id).first()
    if not artwork:
        raise HTTPException(status_code=404, detail="Artwork not found")
    drive = db.query(ArtworkDrive).filter(ArtworkDrive.drive_id == artwork.artwork_drive_id).first()
    artwork_path = _local_file_within_drive(drive, artwork.file_path) if drive else None
    if artwork_path is None:
        raise HTTPException(status_code=404, detail="Artwork file not found")
    return _serve_image(f"artwork_{artwork_id}", artwork_path, w)


@router.get("/poster-daily-activity")
def get_poster_daily_activity(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Get daily and weekly poster activity summary for dashboard card."""
    # Use local system time to determine day boundaries so "today" matches the
    # user's calendar day regardless of UTC offset. DB timestamps are UTC-naive,
    # so we convert local boundaries back to UTC for range comparisons.
    now_local = datetime.now().astimezone()
    today_local = now_local.date()
    local_tz = now_local.tzinfo
    today_start_utc = (
        datetime.combine(today_local, dt_time.min, tzinfo=local_tz)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    tomorrow_start_utc = (
        datetime.combine(today_local + timedelta(days=1), dt_time.min, tzinfo=local_tz)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    week_ago_start_utc = today_start_utc - timedelta(days=7)
    month_ago_start_utc = today_start_utc - timedelta(days=30)

    # --- Renamed stats (from Poster.last_processed) ---
    posters_renamed_today = (
        db.query(func.count(Poster.id))
        .filter(Poster.last_processed.isnot(None))
        .filter(Poster.last_processed >= today_start_utc)
        .filter(Poster.last_processed < tomorrow_start_utc)
        .scalar()
        or 0
    )

    posters_renamed_week = (
        db.query(func.count(Poster.id))
        .filter(Poster.last_processed.isnot(None))
        .filter(Poster.last_processed >= week_ago_start_utc)
        .scalar()
        or 0
    )

    # --- Completed jobs for today and this week ---
    completed_jobs_today = (
        db.query(Job.job_type, Job.message)
        .filter(Job.status == JOB_STATUS_COMPLETED)
        .filter(Job.completed_at.isnot(None))
        .filter(Job.completed_at >= today_start_utc)
        .filter(Job.completed_at < tomorrow_start_utc)
        .all()
    )

    completed_jobs_week = (
        db.query(Job.job_type, Job.message)
        .filter(Job.status == JOB_STATUS_COMPLETED)
        .filter(Job.completed_at.isnot(None))
        .filter(Job.completed_at >= week_ago_start_utc)
        .all()
    )

    completed_jobs_month = (
        db.query(Job.job_type, Job.message)
        .filter(Job.status == JOB_STATUS_COMPLETED)
        .filter(Job.completed_at.isnot(None))
        .filter(Job.completed_at >= month_ago_start_utc)
        .all()
    )

    posters_matched_today = 0
    posters_borders_replaced_today = 0
    synced_new_today = 0
    synced_replaced_today = 0
    synced_deleted_today = 0

    matched_pattern = re.compile(r"(\d+)\s+posters\s+organized", re.IGNORECASE)
    border_pattern = re.compile(r"Border replacer complete:\s*(\d+)\s+changed", re.IGNORECASE)
    sync_added_pattern = re.compile(r"(\d+)\s+added", re.IGNORECASE)
    sync_updated_pattern = re.compile(r"(\d+)\s+updated", re.IGNORECASE)
    sync_deleted_pattern = re.compile(r"(\d+)\s+deleted", re.IGNORECASE)

    for job_type, message in completed_jobs_today:
        if not message:
            continue

        if job_type == JOB_TYPE_POSTER_RENAMER:
            matched_match = matched_pattern.search(message)
            if matched_match:
                posters_matched_today += int(matched_match.group(1))

        if job_type == JOB_TYPE_BORDER_REPLACER:
            border_match = border_pattern.search(message)
            if border_match:
                posters_borders_replaced_today += int(border_match.group(1))

        if job_type.startswith("Sync") or job_type == JOB_TYPE_GDRIVE_SYNC:
            added_match = sync_added_pattern.search(message)
            if added_match:
                synced_new_today += int(added_match.group(1))
            updated_match = sync_updated_pattern.search(message)
            if updated_match:
                synced_replaced_today += int(updated_match.group(1))
            deleted_match = sync_deleted_pattern.search(message)
            if deleted_match:
                synced_deleted_today += int(deleted_match.group(1))

    synced_new_week = 0
    synced_replaced_week = 0
    synced_deleted_week = 0

    for job_type, message in completed_jobs_week:
        if not message:
            continue

        if job_type.startswith("Sync") or job_type == JOB_TYPE_GDRIVE_SYNC:
            added_match = sync_added_pattern.search(message)
            if added_match:
                synced_new_week += int(added_match.group(1))
            updated_match = sync_updated_pattern.search(message)
            if updated_match:
                synced_replaced_week += int(updated_match.group(1))
            deleted_match = sync_deleted_pattern.search(message)
            if deleted_match:
                synced_deleted_week += int(deleted_match.group(1))

    synced_new_month = 0
    synced_replaced_month = 0
    synced_deleted_month = 0

    for job_type, message in completed_jobs_month:
        if not message:
            continue

        if job_type.startswith("Sync") or job_type == JOB_TYPE_GDRIVE_SYNC:
            added_match = sync_added_pattern.search(message)
            if added_match:
                synced_new_month += int(added_match.group(1))
            updated_match = sync_updated_pattern.search(message)
            if updated_match:
                synced_replaced_month += int(updated_match.group(1))
            deleted_match = sync_deleted_pattern.search(message)
            if deleted_match:
                synced_deleted_month += int(deleted_match.group(1))

    return {
        "posters_matched_today": posters_matched_today,
        "posters_renamed_today": posters_renamed_today,
        "posters_renamed_week": posters_renamed_week,
        "posters_borders_replaced_today": posters_borders_replaced_today,
        "synced_new_today": synced_new_today,
        "synced_replaced_today": synced_replaced_today,
        "synced_deleted_today": synced_deleted_today,
        "synced_new_week": synced_new_week,
        "synced_replaced_week": synced_replaced_week,
        "synced_deleted_week": synced_deleted_week,
        "synced_new_month": synced_new_month,
        "synced_replaced_month": synced_replaced_month,
        "synced_deleted_month": synced_deleted_month,
    }
