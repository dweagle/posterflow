from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import Float, func
from typing import Any, Dict
from pathlib import Path
from datetime import datetime, timezone, timedelta, time as dt_time
import re
from database import get_db
from models.job import (
    JOB_STATUS_COMPLETED,
    JOB_TYPE_POSTER_RENAMER,
    JOB_TYPE_BORDER_REPLACER,
    JOB_TYPE_GDRIVE_SYNC,
)
from models.drive import Drive
from models.poster import Poster
from models.job import Job

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _is_path_within(parent: Path, child: Path) -> bool:
    """Return True when child is inside parent (or equal)."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


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

    safe_limit = max(1, min(limit, 500))
    raw_limit = min(safe_limit * 8, 4000)

    rows = (
        db.query(Poster, Drive)
        .join(Drive, Poster.drive_id == Drive.drive_id)
        .filter(Poster.file_name.ilike(f"%{query}%"))
        .order_by(Poster.file_name.asc(), Drive.name.asc())
        .limit(raw_limit)
        .all()
    )

    grouped: Dict[str, Dict[str, Any]] = {}

    for poster, drive in rows:
        drive_root: Path | None = None
        poster_path: Path | None = None
        try:
            drive_root = drive.get_local_path(validate=False).resolve()
            poster_path = Path(poster.file_path).resolve()
        except Exception:
            drive_root = None
            poster_path = None

        if drive_root is None or poster_path is None:
            continue

        # Strict guard: only include files physically inside the drive's local folder.
        if not _is_path_within(drive_root, poster_path):
            continue

        # Extra guard for explicit temp/asset folder names anywhere in path.
        path_parts = {part.lower() for part in poster_path.parts}
        if "assets" in path_parts or "tmp" in path_parts or "temp" in path_parts:
            continue

        poster_name = Path(poster.file_name).stem
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


@router.get("/recent-posters")
def get_recent_synced_posters(limit: int = 10, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Get most recent posters for dashboard carousel.

    Ordering prioritizes the poster content timestamp (file mtime) and falls back
    to DB download timestamp when mtime is unavailable.
    """
    safe_limit = max(1, min(limit, 100))
    raw_limit = min(safe_limit * 8, 800)
    content_recency = func.coalesce(
        Poster.file_mtime,
        func.strftime('%s', Poster.downloaded_at).cast(Float),
        0.0,
    )

    recent_rows = (
        db.query(Poster, Drive)
        .join(Drive, Poster.drive_id == Drive.drive_id)
        .filter(Drive.subscribed == True)
        .order_by(content_recency.desc(), Poster.downloaded_at.desc(), Poster.id.desc())
        .limit(raw_limit)
        .all()
    )

    items = []
    for poster, drive in recent_rows:
        drive_root: Path | None = None
        poster_path: Path | None = None
        try:
            drive_root = drive.get_local_path(validate=False).resolve()
            poster_path = Path(poster.file_path).resolve()
        except Exception:
            drive_root = None
            poster_path = None

        if drive_root is None or poster_path is None:
            continue

        if not poster_path.exists() or not poster_path.is_file():
            continue

        if not _is_path_within(drive_root, poster_path):
            continue

        items.append(
            {
                "id": poster.id,
                "file_name": poster.file_name,
                "drive_id": poster.drive_id,
                "drive_name": drive.name,
                "downloaded_at": poster.downloaded_at.isoformat() if poster.downloaded_at else None,
                "file_mtime": poster.file_mtime,
                "image_url": f"/api/stats/posters/{poster.id}/image",
            }
        )

        if len(items) >= safe_limit:
            break

    return {
        "items": items,
        "count": len(items),
    }


@router.get("/posters/{poster_id}/image")
def get_poster_image(poster_id: int, db: Session = Depends(get_db)) -> FileResponse:
    """Stream poster image by poster record id."""
    poster = db.query(Poster).filter(Poster.id == poster_id).first()
    if not poster:
        raise HTTPException(status_code=404, detail="Poster not found")

    drive = db.query(Drive).filter(Drive.drive_id == poster.drive_id).first()
    if not drive:
        raise HTTPException(status_code=404, detail="Drive not found for poster")

    try:
        drive_root = drive.get_local_path(validate=False).resolve()
        poster_path = Path(poster.file_path).resolve()
    except Exception:
        raise HTTPException(status_code=404, detail="Poster path invalid")

    if not _is_path_within(drive_root, poster_path):
        raise HTTPException(status_code=404, detail="Poster file not found")

    if not poster_path.exists() or not poster_path.is_file():
        raise HTTPException(status_code=404, detail="Poster file not found")

    return FileResponse(
        str(poster_path),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


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
