"""Community poster requests API - connects to Supabase for cross-instance request sharing."""
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
import httpx

from database import get_db
from models.setting import get_setting_value, upsert_setting
from core.logging import LogTags, log_info, log_error, log_user_action

# Supabase project configuration.
# The publishable key is intentionally public — it is scoped to read-only access
# via Row Level Security. All writes go through a SECURITY DEFINER function.
SUPABASE_URL = "https://qwudwkxfqowjtisdlplv.supabase.co"
SUPABASE_KEY = "sb_publishable_N83-fB74swOKM5XGbMhO7A_qk7LXgel"
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

DAILY_REQUEST_LIMIT = 5
SETTING_RATE_DATE = "community_rate_date"
SETTING_RATE_COUNT = "community_rate_count"

router = APIRouter(prefix="/api/community", tags=["community"])


def check_and_increment_rate_limit(db: Session) -> None:
    """Enforce a local daily limit on community request submissions.

    Raises HTTP 429 if the limit is reached. Resets automatically on a new UTC day.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stored_date = get_setting_value(db, SETTING_RATE_DATE)
    stored_count = int(get_setting_value(db, SETTING_RATE_COUNT) or "0")

    if stored_date != today:
        # New day — reset counter
        stored_count = 0
        upsert_setting(db, SETTING_RATE_DATE, today)

    if stored_count >= DAILY_REQUEST_LIMIT:
        raise HTTPException(status_code=429, detail=f"Daily request limit reached ({DAILY_REQUEST_LIMIT} per day)")

    upsert_setting(db, SETTING_RATE_COUNT, str(stored_count + 1))
    db.commit()


class PosterRequestPayload(BaseModel):
    tmdb_id: Optional[int] = None
    media_type: str  # movie | show | season | collection | person
    title: str
    year: Optional[int] = None
    season_number: Optional[int] = None
    poster_path: Optional[str] = None
    imdb_id: Optional[str] = None
    tvdb_id: Optional[int] = None
    notes: Optional[str] = None
    style_tags: Optional[list[str]] = None
    requested_by: Optional[str] = None
    requested_by_discord_id: Optional[str] = None
    ping_discord_id: Optional[str] = None


@router.get("/requests/count")
async def get_community_requests_count():
    """Return a lightweight count of open (pending + in_progress) community requests."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/poster_requests",
                headers={**SUPABASE_HEADERS, "Prefer": "count=exact"},
                params={
                    "select": "id",
                    "status": "in.(pending,in_progress)",
                    "limit": 1,
                },
            )
            resp.raise_for_status()
            count = int(resp.headers.get("content-range", "0/0").split("/")[-1] or 0)
            return {"count": count}
    except Exception:
        return {"count": 0}


@router.get("/requests")
async def get_community_requests(
    status: Optional[str] = Query(None),
    media_type: Optional[str] = Query(None),
    sort: str = Query("recent"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: Session = Depends(get_db),
):
    """Fetch community poster requests from Supabase."""
    show_all = status == "all"
    specific_status = status if status and status not in ("all",) else None

    params: dict = {
        "select": "*",
        "limit": limit,
        "offset": offset,
        "order": "created_at.desc",
    }
    if show_all:
        # No status filter — return everything
        pass
    elif specific_status:
        params["status"] = f"eq.{specific_status}"
    else:
        # Active view: hide fulfilled requests older than 24 hours.
        # Compute the cutoff as an ISO timestamp — PostgREST cannot evaluate
        # PostgreSQL expressions like now()-interval '24 hours' in filter values.
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        params["or"] = f"(status.neq.fulfilled,fulfilled_at.gt.{cutoff})"
    if media_type:
        params["media_type"] = f"eq.{media_type}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/poster_requests",
                headers=SUPABASE_HEADERS,
                params=params,
            )
            resp.raise_for_status()
            requests_list = resp.json()

            # Post-filter: hide rejected items older than 24 hours (active view only)
            if not show_all and not specific_status:
                cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=24)
                def _keep(r: dict) -> bool:
                    if r.get("status") != "rejected":
                        return True
                    fa = r.get("fulfilled_at")
                    if not fa:
                        return False
                    try:
                        fa_dt = datetime.fromisoformat(fa.replace("Z", "+00:00"))
                        return fa_dt >= cutoff_dt
                    except ValueError:
                        return False
                requests_list = [r for r in requests_list if _keep(r)]

            return {"requests": requests_list}

    except httpx.HTTPStatusError as e:
        log_error(LogTags.API, f"Supabase fetch failed: {e.response.text}", status_code=e.response.status_code)
        raise HTTPException(status_code=502, detail="Failed to fetch community requests")
    except httpx.RequestError as e:
        log_error(LogTags.API, f"Supabase connection error: {e}")
        raise HTTPException(status_code=502, detail="Could not connect to community service")


@router.post("/requests")
async def submit_community_request(
    payload: PosterRequestPayload,
    db: Session = Depends(get_db),
):
    """Submit a new poster request to the shared community database."""
    if payload.media_type not in ("movie", "show", "season", "collection", "person"):
        raise HTTPException(status_code=400, detail="Invalid media_type")
    if not payload.title or not payload.title.strip():
        raise HTTPException(status_code=400, detail="Title is required")

    check_and_increment_rate_limit(db)

    log_user_action(
        f"Submitting community poster request: {payload.title}",
        media_type=payload.media_type,
        tmdb_id=payload.tmdb_id,
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Block season requests when an active show-level request already exists
            if payload.media_type == "season" and payload.tmdb_id is not None:
                conflict_resp = await client.get(
                    f"{SUPABASE_URL}/rest/v1/poster_requests",
                    headers=SUPABASE_HEADERS,
                    params={
                        "select": "id",
                        "tmdb_id": f"eq.{payload.tmdb_id}",
                        "media_type": "eq.show",
                        "status": "not.in.(fulfilled,rejected)",
                        "limit": 1,
                    },
                )
                conflict_resp.raise_for_status()
                if conflict_resp.json():
                    raise HTTPException(
                        status_code=409,
                        detail="A show-level request already exists for this series — season requests aren't needed separately.",
                    )

            # Block duplicate requests — if an active request for this item already exists globally, reject
            if payload.tmdb_id is not None:
                dup_params: dict = {
                    "select": "id",
                    "tmdb_id": f"eq.{payload.tmdb_id}",
                    "media_type": f"eq.{payload.media_type}",
                    "status": "not.in.(fulfilled,rejected)",
                    "limit": 1,
                }
                if payload.media_type == "season" and payload.season_number is not None:
                    dup_params["season_number"] = f"eq.{payload.season_number}"
                dup_resp = await client.get(
                    f"{SUPABASE_URL}/rest/v1/poster_requests",
                    headers=SUPABASE_HEADERS,
                    params=dup_params,
                )
                dup_resp.raise_for_status()
                if dup_resp.json():
                    raise HTTPException(
                        status_code=409,
                        detail=f"'{payload.title.strip()}' has already been requested.",
                    )
            else:
                # Custom item (no TMDB ID) — deduplicate by exact title + media_type
                dup_resp = await client.get(
                    f"{SUPABASE_URL}/rest/v1/poster_requests",
                    headers=SUPABASE_HEADERS,
                    params={
                        "select": "id",
                        "tmdb_id": "is.null",
                        "title": f"ilike.{payload.title.strip()}",
                        "media_type": f"eq.{payload.media_type}",
                        "status": "not.in.(fulfilled,rejected)",
                        "limit": 1,
                    },
                )
                dup_resp.raise_for_status()
                if dup_resp.json():
                    raise HTTPException(
                        status_code=409,
                        detail=f"'{payload.title.strip()}' has already been requested.",
                    )

            # All writes go through the submit-request edge function.
            # The edge function enforces server-side IP rate limiting and
            # calls the RPC with the service role key.  Direct anon calls
            # to the RPC have been revoked at the Postgres level.
            resp = await client.post(
                f"{SUPABASE_URL}/functions/v1/submit-request",
                headers=SUPABASE_HEADERS,
                json={
                    "p_tmdb_id": payload.tmdb_id,
                    "p_media_type": payload.media_type,
                    "p_title": payload.title.strip(),
                    "p_year": payload.year,
                    "p_season_number": payload.season_number,
                    "p_poster_path": payload.poster_path,
                    "p_imdb_id": payload.imdb_id,
                    "p_tvdb_id": payload.tvdb_id,
                    "p_notes": payload.notes,
                    "p_style_tags": payload.style_tags,
                    "p_requested_by": payload.requested_by,
                    "p_requested_by_discord_id": payload.requested_by_discord_id,
                    "p_ping_discord_id": payload.ping_discord_id,
                },
            )
            resp.raise_for_status()
            result = resp.json()

            if result.get("is_new"):
                log_info(LogTags.API, f"New community request: {payload.title}", tmdb_id=payload.tmdb_id)
                return {"status": "created", "request_id": result["request_id"]}
            # Race condition: another instance submitted between our check and insert
            return {"status": "already_requested", "request_id": result.get("request_id", "")}

    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        log_error(LogTags.API, f"Supabase submit failed: {e.response.text}", status_code=status)
        # Pass through rate-limit and conflict responses from the edge function
        if status == 429:
            try:
                detail = e.response.json().get("error", "Daily submission limit reached")
            except Exception:
                detail = "Daily submission limit reached"
            raise HTTPException(status_code=429, detail=detail)
        if status == 409:
            try:
                detail = e.response.json().get("error", "Request already exists")
            except Exception:
                detail = "Request already exists"
            raise HTTPException(status_code=409, detail=detail)
        raise HTTPException(status_code=502, detail="Failed to submit request")
    except httpx.RequestError as e:
        log_error(LogTags.API, f"Supabase connection error: {e}")
        raise HTTPException(status_code=502, detail="Could not connect to community service")
