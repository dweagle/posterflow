"""Community poster requests API - connects to Supabase for cross-instance request sharing."""
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
import httpx

from database import get_db
from models.setting import upsert_setting
from core.logging import LogTags, log_info, log_error, log_user_action

# Supabase project configuration.
# The publishable key is intentionally public — it is scoped to read-only access
# via Row Level Security. All writes go through the submit-request edge function,
# which verifies a signed Discord token and enforces per-user and per-IP daily
# limits server-side. Direct anon calls to the RPCs are revoked at the Postgres
# level by a one-off Supabase migration (applied out-of-band).
SUPABASE_URL = "https://qwudwkxfqowjtisdlplv.supabase.co"
SUPABASE_KEY = "sb_publishable_N83-fB74swOKM5XGbMhO7A_qk7LXgel"
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

router = APIRouter(prefix="/api/community", tags=["community"])

# Stores this instance's connected Discord identity {discord_user_id, discord_username}
# so headless reconciliation knows whose published list items to clean up.
SETTING_DISCORD_IDENTITY = "community_discord_identity"


def _request_style(tags: Optional[list]) -> str:
    """The community poster style (CL2K/MM2K) in a request's style tags.
    Mirrors the poster_request_style() SQL helper — CL2K wins if both appear."""
    t = set(tags or [])
    if t & {"CL2K Style", "CL2K"}:
        return "CL2K"
    if t & {"MM2K Style", "MM2K"}:
        return "MM2K"
    return ""


class DiscordIdentityPayload(BaseModel):
    discord_user_id: str
    discord_username: Optional[str] = None
    discord_token: Optional[str] = None


@router.post("/identity")
def store_discord_identity(payload: DiscordIdentityPayload, db: Session = Depends(get_db)):
    """Persist the connected Discord identity + signed token so background scan
    jobs can reconcile this user's own published list items headlessly.

    The token is the user's own (already held in their browser) and is used only
    to authorize removal of their own items through the update-list-item edge
    function — the very same auth a manual "remove" uses. Stored in the settings
    table alongside other app credentials; no extra setup required."""
    uid = payload.discord_user_id.strip()
    if not uid:
        raise HTTPException(status_code=400, detail="discord_user_id is required")
    upsert_setting(db, SETTING_DISCORD_IDENTITY, json.dumps({
        "discord_user_id": uid,
        "discord_username": (payload.discord_username or "").strip() or None,
        "discord_token": (payload.discord_token or "").strip() or None,
    }))
    db.commit()
    return {"ok": True}


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
    ping_discord_id: Optional[str] = None
    # Signed Discord token from discord-oauth — required by the submit-request
    # edge function, which derives the requester's identity from it.
    discord_token: Optional[str] = None


class ListItemPayload(BaseModel):
    tmdb_id: Optional[int] = None
    media_type: str  # movie | show | season | collection
    title: str
    year: Optional[int] = None
    season_number: Optional[int] = None
    poster_path: Optional[str] = None
    imdb_id: Optional[str] = None
    tvdb_id: Optional[int] = None
    style_tag: Optional[str] = None
    source: Optional[str] = None  # unmatched | style_fallback
    notes: Optional[str] = None


class SubmitListItemsPayload(BaseModel):
    items: list[ListItemPayload]
    # Signed Discord token — the submit-list-items edge function derives the
    # publisher's identity from it (never from client-supplied fields).
    discord_token: Optional[str] = None


@router.get("/requests/count")
async def get_community_requests_count():
    """Return a lightweight count of unclaimed (pending) community requests.

    Only pending requests count toward the sidebar badge — once a request is
    claimed (in_progress) it drops off, since a maker is already on it.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/poster_requests",
                headers={**SUPABASE_HEADERS, "Prefer": "count=exact"},
                params={
                    "select": "id",
                    "status": "eq.pending",
                    "limit": 1,
                },
            )
            resp.raise_for_status()
            count = int(resp.headers.get("content-range", "0/0").split("/")[-1] or 0)
            return {"count": count}
    except Exception:
        return {"count": 0}


@router.get("/requests/my-counts")
async def get_my_request_counts(discord_id: str = ""):
    """Counts of the caller's own active requests, split by status.

    Powers the requester sidebar badges (own pending + in_progress). The
    discord_id is only used to filter a public read — it's not a security
    boundary, just which counts to show.
    """
    result = {"pending": 0, "in_progress": 0}
    if not discord_id.strip():
        return result
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/poster_requests",
                headers=SUPABASE_HEADERS,
                params={
                    "select": "status",
                    "requested_by_discord_id": f"eq.{discord_id.strip()}",
                    "status": "in.(pending,in_progress)",
                },
            )
            resp.raise_for_status()
            for row in resp.json():
                status = row.get("status")
                if status in result:
                    result[status] += 1
            return result
    except Exception:
        return result


@router.get("/requests/fulfilled-styles")
async def get_fulfilled_request_styles(
    media_type: str = Query(...),
    tmdb_id: Optional[int] = Query(None),
    season_number: Optional[int] = Query(None),
    title: Optional[str] = Query(None),
):
    """Styles (CL2K/MM2K) this item has already been fulfilled in.

    Powers the "already made" warning in the request modals. An empty string in
    the list means a fulfilled request whose style is unknown (no style tags).
    Best-effort: returns an empty list on errors so the modal never blocks.
    """
    params: dict = {
        "select": "style_tags",
        "media_type": f"eq.{media_type}",
        "status": "eq.fulfilled",
    }
    if tmdb_id is not None:
        params["tmdb_id"] = f"eq.{tmdb_id}"
        if media_type == "season" and season_number is not None:
            params["season_number"] = f"eq.{season_number}"
    elif title and title.strip():
        params["tmdb_id"] = "is.null"
        params["title"] = f"ilike.{title.strip()}"
    else:
        return {"styles": []}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/poster_requests",
                headers=SUPABASE_HEADERS,
                params=params,
            )
            resp.raise_for_status()
            return {"styles": sorted({_request_style(r.get("style_tags")) for r in resp.json()})}
    except Exception:
        return {"styles": []}


@router.get("/requests")
async def get_community_requests(
    status: Optional[str] = Query(None),
    media_type: Optional[str] = Query(None),
    claimed_by_discord_id: Optional[str] = Query(None),
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
        # id.asc is a unique tiebreaker so offset pages don't overlap when requests
        # share a created_at (see the lists query for the same fix).
        "order": "created_at.desc,id.asc",
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
    # "Claimed" filter (maker viewing their own claims; caller pairs it with
    # status=in_progress).
    if claimed_by_discord_id:
        params["claimed_by_discord_id"] = f"eq.{claimed_by_discord_id}"

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
async def submit_community_request(payload: PosterRequestPayload):
    """Submit a new poster request to the shared community database."""
    if payload.media_type not in ("movie", "show", "season", "collection", "person"):
        raise HTTPException(status_code=400, detail="Invalid media_type")
    if not payload.title or not payload.title.strip():
        raise HTTPException(status_code=400, detail="Title is required")
    if not payload.discord_token:
        raise HTTPException(
            status_code=401,
            detail="Connect your Discord account to submit a request",
        )

    log_user_action(
        f"Submitting community poster request: {payload.title}",
        media_type=payload.media_type,
        tmdb_id=payload.tmdb_id,
    )

    style = _request_style(payload.style_tags)
    style_label = f"{style} " if style else ""

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Block season requests when an active show-level request in the same
            # style already exists (a show-level request covers all its seasons)
            if payload.media_type == "season" and payload.tmdb_id is not None:
                conflict_resp = await client.get(
                    f"{SUPABASE_URL}/rest/v1/poster_requests",
                    headers=SUPABASE_HEADERS,
                    params={
                        "select": "id,style_tags",
                        "tmdb_id": f"eq.{payload.tmdb_id}",
                        "media_type": "eq.show",
                        "status": "not.in.(fulfilled,rejected)",
                    },
                )
                conflict_resp.raise_for_status()
                if any(_request_style(r.get("style_tags")) == style for r in conflict_resp.json()):
                    raise HTTPException(
                        status_code=409,
                        detail=f"A show-level {style_label}request already exists for this series — season requests aren't needed separately.",
                    )

            # Block duplicates — only an ACTIVE request for this item in the SAME
            # style rejects; CL2K and MM2K requests can be pending side by side,
            # and fulfilled/rejected history never blocks a new request.
            if payload.tmdb_id is not None:
                dup_params: dict = {
                    "select": "id,style_tags",
                    "tmdb_id": f"eq.{payload.tmdb_id}",
                    "media_type": f"eq.{payload.media_type}",
                    "status": "not.in.(fulfilled,rejected)",
                }
                if payload.media_type == "season" and payload.season_number is not None:
                    dup_params["season_number"] = f"eq.{payload.season_number}"
                dup_resp = await client.get(
                    f"{SUPABASE_URL}/rest/v1/poster_requests",
                    headers=SUPABASE_HEADERS,
                    params=dup_params,
                )
            else:
                # Custom item (no TMDB ID) — deduplicate by exact title + media_type
                dup_resp = await client.get(
                    f"{SUPABASE_URL}/rest/v1/poster_requests",
                    headers=SUPABASE_HEADERS,
                    params={
                        "select": "id,style_tags",
                        "tmdb_id": "is.null",
                        "title": f"ilike.{payload.title.strip()}",
                        "media_type": f"eq.{payload.media_type}",
                        "status": "not.in.(fulfilled,rejected)",
                    },
                )
            dup_resp.raise_for_status()
            if any(_request_style(r.get("style_tags")) == style for r in dup_resp.json()):
                raise HTTPException(
                    status_code=409,
                    detail=f"'{payload.title.strip()}' already has an active {style_label}request.",
                )

            # All writes go through the submit-request edge function.
            # The edge function verifies the signed Discord token, derives the
            # requester's identity from it, and enforces per-user and per-IP
            # daily limits before calling the RPC with the service role key.
            resp = await client.post(
                f"{SUPABASE_URL}/functions/v1/submit-request",
                headers=SUPABASE_HEADERS,
                json={
                    "token": payload.discord_token,
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
        # Pass through auth, rate-limit and conflict responses from the edge function
        if status == 401:
            try:
                detail = e.response.json().get("error", "Discord authentication required")
            except Exception:
                detail = "Discord authentication required"
            raise HTTPException(status_code=401, detail=detail)
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


# ── Community Lists ──────────────────────────────────────────────────────────
# A lightweight, bulk worklist that makers claim/complete from the Lists tab.
# Same Supabase split as requests: public reads here, writes via token-verified
# edge functions (submit-list-items / update-list-item).

def _list_status_filter(status: Optional[str]) -> str:
    """PostgREST status filter for the lists views (rejected always hidden)."""
    if status == "in_progress":
        return "eq.in_progress"
    if status == "fulfilled":
        return "eq.fulfilled"
    if status == "all":
        return "in.(open,in_progress,fulfilled)"
    return "in.(open,in_progress)"  # active


def _list_status_values(status: Optional[str]) -> list[str]:
    """Same status mapping as _list_status_filter, as a list for the search RPC."""
    if status == "in_progress":
        return ["in_progress"]
    if status == "fulfilled":
        return ["fulfilled"]
    if status == "all":
        return ["open", "in_progress", "fulfilled"]
    return ["open", "in_progress"]  # active


def _safe_like(s: str) -> str:
    """Sanitize a free-text search for use in a PostgREST ilike value — strip the
    chars that have meaning in filter syntax (commas, parens, wildcards, quotes)
    so it can't break out of the filter or inject wildcards. Returns '' if empty."""
    s = (s or "").strip()[:100]
    for ch in ',()*%"\\':
        s = s.replace(ch, " ")
    return " ".join(s.split())


async def _search_community_list_items(
    q: str,
    status: Optional[str],
    media_type: Optional[str],
    added_by_discord_id: Optional[str],
    claimed_by_discord_id: Optional[str],
    sort: str,
    limit: int,
    offset: int,
):
    """Title-or-wanter-name search via the search_community_list_items Postgres
    function. Returns {items, total} with wanters embedded — same shape as the
    plain read — but the whole match/filter/page/count runs server-side, so a
    common term can't overflow the request URL (400) the way an id=in.(...) list
    would, and there are no per-page match sub-queries to freeze the worker."""
    payload = {
        "p_q": q,
        "p_status": _list_status_values(status),
        "p_media_type": media_type,
        "p_added_by_discord_id": added_by_discord_id,
        "p_claimed_by_discord_id": claimed_by_discord_id,
        "p_sort": sort,
        "p_limit": limit,
        "p_offset": offset,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{SUPABASE_URL}/rest/v1/rpc/search_community_list_items",
                headers=SUPABASE_HEADERS,
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        log_error(LogTags.API, f"Supabase list search failed: {e.response.text}", status_code=e.response.status_code)
        raise HTTPException(status_code=502, detail="Failed to search community lists")
    except httpx.RequestError as e:
        log_error(LogTags.API, f"Supabase connection error: {e}")
        raise HTTPException(status_code=502, detail="Could not connect to community service")


@router.get("/lists")
async def get_community_lists(
    media_type: Optional[str] = Query(None),
    added_by_discord_id: Optional[str] = Query(None),
    claimed_by_discord_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort: str = Query("newest"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    """Fetch a page of community list items from Supabase.

    status filter: active (default) = open+in_progress, fulfilled, or all.
    Returns {items, total} — total is the full match count, so the UI knows when
    there's more to load.
    """
    # Free-text search (title OR a wanter's name) isn't expressible as a bounded
    # PostgREST filter, so it runs server-side in a Postgres function.
    q = _safe_like(search) if search else ""
    if q:
        return await _search_community_list_items(
            q, status, media_type, added_by_discord_id,
            claimed_by_discord_id, sort, limit, offset,
        )

    # Each poster is one shared row now; embed its wanters so the UI can show
    # "wanted by N". added_by_discord_id is reinterpreted as "items I want".
    # claim_rank.asc keeps claimed (in_progress) items pinned at the top; the
    # newest/oldest choice then orders within each rank group.
    date_order = "created_at.asc" if sort == "oldest" else "created_at.desc"
    select = "*,poster_list_wanters(discord_id,name)"
    params: dict = {
        "status": _list_status_filter(status),
        "limit": limit,
        "offset": offset,
        # id.asc is a unique tiebreaker — without it, items sharing a created_at
        # (bulk publishes) slice inconsistently across offsets, so "Load more"
        # overlaps the previous page and appears to load a random count.
        "order": f"claim_rank.asc,{date_order},id.asc",
    }
    if media_type:
        params["media_type"] = f"eq.{media_type}"
    # "Claimed" filter (maker viewing their own claims). Caller also sends
    # status=in_progress so completed/released items don't slip in.
    if claimed_by_discord_id:
        params["claimed_by_discord_id"] = f"eq.{claimed_by_discord_id}"
    # "Mine" filter → items this user wants. 
    if added_by_discord_id:
        select += ",mine:poster_list_wanters!inner(discord_id)"
        params["mine.discord_id"] = f"eq.{added_by_discord_id}"
    params["select"] = select

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/poster_list_items",
                headers={**SUPABASE_HEADERS, "Prefer": "count=exact"},
                params=params,
            )
            resp.raise_for_status()
            total = int(resp.headers.get("content-range", "*/0").split("/")[-1] or 0)
            items = resp.json()
            for row in items:
                row.pop("mine", None)  # internal join alias, not for the client
            return {"items": items, "total": total}
    except httpx.HTTPStatusError as e:
        log_error(LogTags.API, f"Supabase list fetch failed: {e.response.text}", status_code=e.response.status_code)
        raise HTTPException(status_code=502, detail="Failed to fetch community lists")
    except httpx.RequestError as e:
        log_error(LogTags.API, f"Supabase connection error: {e}")
        raise HTTPException(status_code=502, detail="Could not connect to community service")


@router.get("/lists/owners")
async def get_community_list_owners(
    status: Optional[str] = Query(None),
    media_type: Optional[str] = Query(None),
):
    """Distinct people who *want* items in the current list view (id, name, count)
    — populates the wanter filter independent of which items are loaded."""
    # PostgREST caps every response at 1000 rows regardless of limit
    params: dict = {
        "select": "discord_id,name,poster_list_items!inner(status,media_type)",
        "poster_list_items.status": _list_status_filter(status),
        "order": "id.asc",
    }
    if media_type:
        params["poster_list_items.media_type"] = f"eq.{media_type}"

    PAGE = 1000
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            owners: dict[str, dict] = {}
            offset = 0
            while True:
                resp = await client.get(
                    f"{SUPABASE_URL}/rest/v1/poster_list_wanters",
                    headers=SUPABASE_HEADERS,
                    params={**params, "limit": PAGE, "offset": offset},
                )
                resp.raise_for_status()
                rows = resp.json()
                for row in rows:
                    did = row.get("discord_id")
                    if not did:
                        continue
                    entry = owners.get(did)
                    if entry is None:
                        owners[did] = {"id": did, "name": row.get("name") or did, "count": 1}
                    else:
                        entry["count"] += 1
                if len(rows) < PAGE:
                    break
                offset += PAGE
            result = sorted(owners.values(), key=lambda o: (o["name"] or "").lower())
            return {"owners": result}
    except httpx.HTTPStatusError as e:
        log_error(LogTags.API, f"Supabase list owners fetch failed: {e.response.text}", status_code=e.response.status_code)
        raise HTTPException(status_code=502, detail="Failed to fetch list owners")
    except httpx.RequestError as e:
        log_error(LogTags.API, f"Supabase connection error: {e}")
        raise HTTPException(status_code=502, detail="Could not connect to community service")


@router.post("/lists")
async def submit_community_list_items(payload: SubmitListItemsPayload):
    """Publish a bulk worklist to the shared community Lists tab."""
    if not payload.items:
        raise HTTPException(status_code=400, detail="No items to add")
    if not payload.discord_token:
        raise HTTPException(
            status_code=401,
            detail="Connect your Discord account to publish a list",
        )

    valid_types = {"movie", "show", "season", "collection"}
    items: list[dict] = []
    for item in payload.items:
        if item.media_type not in valid_types or not item.title or not item.title.strip():
            continue
        items.append(item.model_dump(exclude_none=True))
    if not items:
        raise HTTPException(status_code=400, detail="No valid items to add")

    log_user_action(f"Publishing {len(items)} item(s) to a community list")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{SUPABASE_URL}/functions/v1/submit-list-items",
                headers=SUPABASE_HEADERS,
                json={"token": payload.discord_token, "items": items},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        log_error(LogTags.API, f"Supabase list submit failed: {e.response.text}", status_code=status)
        if status in (400, 401, 429):
            try:
                detail = e.response.json().get("error", "Failed to publish list")
            except Exception:
                detail = "Failed to publish list"
            raise HTTPException(status_code=status, detail=detail)
        raise HTTPException(status_code=502, detail="Failed to publish list")
    except httpx.RequestError as e:
        log_error(LogTags.API, f"Supabase connection error: {e}")
        raise HTTPException(status_code=502, detail="Could not connect to community service")
