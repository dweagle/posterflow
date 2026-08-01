"""Artwork Finder API — browse logos / backgrounds / square art (TMDB + Plex Gracenote, or TheTVDB) and
add a chosen one into an IDarr artwork scope. New, self-contained router; delete the file to
remove the endpoints.
"""
import json
import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from api.idarr import SETTING_MAKER_IDARR_CONFIG
from core.job_queue import job_queue
from core.logging import log_user_action
from database import get_db
from models.job import JOB_STATUSES_ACTIVE, JOB_TYPE_ARTWORK_PULL, Job, create_job
from models.setting import get_setting
from services import artwork_finder as af
from services import tvdb

router = APIRouter(prefix="/api/artwork-finder", tags=["artwork-finder"])


# ------------------------------------------------------------------ schemas

class Candidate(BaseModel):
    source: str            # "tmdb" | "gracenote" | "tvdb"
    ref: str               # tmdb file_path ("/xxx.png"), or an absolute gracenote/TVDB CDN url
    width: Optional[int] = None
    height: Optional[int] = None
    language: Optional[str] = None          # ISO 639-1, or None when the image is textless
    off_white_pct: Optional[float] = None   # logos only, when evaluated
    is_white: Optional[bool] = None


# Listable types include "poster" (a crop-to-square source), which is NOT a savable subtype.
LISTABLE_TYPES = {"logo", "background", "squareart", "poster"}


def _year_or_none(v):
    """The UI passes the TMDB result's year verbatim, which is a STRING and '' when unknown
    (collections especially) — coerce blank/invalid to None instead of 422ing."""
    if v is None or (isinstance(v, str) and not v.strip().isdigit()):
        return None
    return int(v)


class CandidatesResponse(BaseModel):
    logos: list[Candidate]
    backgrounds: list[Candidate]
    squareart: list[Candidate]
    posters: list[Candidate]
    plex_available: bool    # False when no Plex token -> no square art from Plex


class CropSquareRequest(BaseModel):
    sync_target_index: int
    title: str
    media_type: str        # movie | tv | collection
    source: str            # tmdb | gracenote | tvdb
    ref: str
    x: int                 # crop rect in the SOURCE image's own pixels
    y: int
    size: int              # square side
    year: Optional[int] = None
    tmdb_id: Optional[int] = None
    tvdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    confirm_overwrite: bool = False

    _year = field_validator("year", mode="before")(_year_or_none)


class AddRequest(BaseModel):
    sync_target_index: int
    title: str
    media_type: str        # movie | tv | collection
    subtype: str           # logo | background | squareart
    source: str            # tmdb | gracenote | tvdb
    ref: str
    year: Optional[int] = None
    tmdb_id: Optional[int] = None
    tvdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    make_white: bool = False
    confirm_overwrite: bool = False   # True after the user OKs overwriting an existing file

    _year = field_validator("year", mode="before")(_year_or_none)


class AddResponse(BaseModel):
    success: bool
    status: str        # "added" | "exists" (exists = same-named file present, needs confirm)
    written: str
    subfolder: str
    archived: bool
    source_dir: str


# ------------------------------------------------------------------ helpers

def _tmdb_key(db: Session) -> str:
    setting = get_setting(db, "tmdb_api_key")
    return str((setting.value if setting else "") or "").strip()


def _require_tmdb_key(db: Session) -> str:
    key = _tmdb_key(db)
    if not key:
        raise HTTPException(status_code=400,
                            detail="TMDB API key not configured. Set it in Settings → General → API Keys.")
    return key


def _make_item(*, title: str, media_type: str, year: Optional[int], tmdb_id: Optional[int],
               tvdb_id: Optional[int], imdb_id: Optional[str]) -> af.FinderItem:
    mt = str(media_type or "").strip().lower()
    if mt not in ("movie", "tv", "collection"):
        raise HTTPException(status_code=400, detail="media_type must be movie, tv, or collection")
    return af.FinderItem(title=title.strip(), year=year, tmdb_id=tmdb_id,
                         tvdb_id=tvdb_id, imdb_id=(imdb_id or None), media_type=mt)


def _resolve_artwork_scope(db: Session, index: int) -> tuple[Path, bool, str]:
    """(source_dir, is_asset_drive, label) for the selected IDarr sync target, or 400."""
    setting = get_setting(db, SETTING_MAKER_IDARR_CONFIG)
    if not setting or not setting.value:
        raise HTTPException(status_code=400, detail="IDarr is not configured. Add an artwork scope on the IDarr page first.")
    try:
        config = json.loads(setting.value)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="IDarr configuration is invalid JSON.")
    targets = [t for t in (config.get("sync_targets") or []) if isinstance(t, dict)]
    if not targets:
        raise HTTPException(status_code=400, detail="No IDarr sync targets configured.")
    if index < 0 or index >= len(targets):
        raise HTTPException(status_code=400, detail=f"sync_target_index must be 0..{len(targets) - 1}")
    target = targets[index]
    source_dir = str(target.get("source_dir") or "").strip()
    if not source_dir:
        raise HTTPException(status_code=400, detail="Selected scope is missing source_dir.")
    if not bool(target.get("is_asset_drive")):
        raise HTTPException(status_code=400,
                            detail="Selected scope is not an artwork scope. Enable 'Assets Drive' on that IDarr sync target.")
    return Path(source_dir), True, str(target.get("label") or "")


# ------------------------------------------------------------------ endpoints

@router.get("/candidates", response_model=CandidatesResponse)
def get_candidates(
    tmdb_id: int,
    media_type: str,
    title: str,
    year: Optional[int] = None,
    tvdb_id: Optional[int] = None,
    imdb_id: Optional[str] = None,
    types: str = "logo,background,squareart,poster",
    evaluate_white: bool = True,
    source: str = "tmdb",
    db: Session = Depends(get_db),
) -> CandidatesResponse:
    """Browse logo / background / square-art / poster candidates for one title, from one source.

    ``source=tmdb`` (the default) lists TMDB logos/backgrounds/posters plus square art from Plex's
    Gracenote provider. ``source=tvdb`` lists TheTVDB's instead — it's only ever called when the
    user picks that tab, and it carries no square art.

    Backgrounds are listed unfiltered here (textless first), unlike the batch pull's strict
    textless/min-width rule — browsing is a human choice, and the strict rule left obscure titles
    showing nothing at all."""
    src = str(source or "tmdb").strip().lower()
    if src not in ("tmdb", "tvdb"):
        raise HTTPException(status_code=400, detail="source must be tmdb or tvdb")

    item = _make_item(title=title, media_type=media_type, year=year, tmdb_id=tmdb_id,
                      tvdb_id=tvdb_id, imdb_id=imdb_id)
    wanted = [t.strip() for t in types.split(",") if t.strip() in LISTABLE_TYPES]

    key = "" if src == "tvdb" else _require_tmdb_key(db)
    tvdb_creds: Optional[tuple[str, str]] = None
    if src == "tvdb":
        tvdb_key, tvdb_pin = tvdb.get_tvdb_credentials(db)
        if not tvdb_key:
            raise HTTPException(status_code=400,
                                detail="TheTVDB API key not configured. Set it in Settings → General → API Keys.")
        tvdb_creds = (tvdb_key, tvdb_pin)

    session = requests.Session()
    token = af.get_plex_token(db)
    plex = af.PlexMetadataProvider(token, session) if token else None
    try:
        result = af.list_candidates(item, wanted, tmdb_api_key=key, plex=plex, session=session,
                                    evaluate_white=evaluate_white, source=src, tvdb_creds=tvdb_creds,
                                    textless_backgrounds=False)
    except tvdb.TvdbError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    return CandidatesResponse(**result)


@router.post("/add", response_model=AddResponse)
def add_artwork(request: AddRequest, db: Session = Depends(get_db)) -> AddResponse:
    """Download the chosen candidate (optionally recolored to white) and write it into the
    selected artwork scope so IDarr can rename + upload it."""
    if request.subtype not in af.SUBTYPE_EXT:
        raise HTTPException(status_code=400, detail="subtype must be logo, background, or squareart")
    if request.source not in ("tmdb", "gracenote", "tvdb"):
        raise HTTPException(status_code=400, detail="source must be tmdb, gracenote, or tvdb")

    source_dir, is_asset_drive, label = _resolve_artwork_scope(db, request.sync_target_index)
    item = _make_item(title=request.title, media_type=request.media_type, year=request.year,
                      tmdb_id=request.tmdb_id, tvdb_id=request.tvdb_id, imdb_id=request.imdb_id)

    session = requests.Session()
    try:
        result = af.save_candidate(source_dir=source_dir, is_asset_drive=is_asset_drive, item=item,
                                   subtype=request.subtype, source=request.source, ref=request.ref,
                                   session=session, make_white=request.make_white,
                                   confirm_overwrite=request.confirm_overwrite)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if result["status"] == "added":
        log_user_action("Added artwork via Artwork Finder", scope=label or str(source_dir),
                        subtype=request.subtype, written=result["written"])
    return AddResponse(success=True, source_dir=str(source_dir), **result)


@router.post("/crop-square", response_model=AddResponse)
def crop_square(request: CropSquareRequest, db: Session = Depends(get_db)) -> AddResponse:
    """Crop a chosen image (usually a poster/background) to a square and save it as the item's
    square art — for titles where Plex has no square art."""
    if request.source not in ("tmdb", "gracenote", "tvdb"):
        raise HTTPException(status_code=400, detail="source must be tmdb, gracenote, or tvdb")
    if request.size <= 0:
        raise HTTPException(status_code=400, detail="crop size must be positive")

    source_dir, is_asset_drive, label = _resolve_artwork_scope(db, request.sync_target_index)
    item = _make_item(title=request.title, media_type=request.media_type, year=request.year,
                      tmdb_id=request.tmdb_id, tvdb_id=request.tvdb_id, imdb_id=request.imdb_id)

    session = requests.Session()
    try:
        result = af.save_candidate(source_dir=source_dir, is_asset_drive=is_asset_drive, item=item,
                                   subtype="squareart", source=request.source, ref=request.ref,
                                   session=session, confirm_overwrite=request.confirm_overwrite,
                                   crop=(request.x, request.y, request.size, request.size))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if result["status"] == "added":
        log_user_action("Cropped square art via Artwork Finder", scope=label or str(source_dir),
                        written=result["written"])
    return AddResponse(success=True, source_dir=str(source_dir), **result)


@router.get("/scope-items")
def scope_items(sync_target_index: int, db: Session = Depends(get_db)) -> dict:
    """Enumerate the selected artwork scope's items with what each is missing — the browsable
    counterpart of the batch 'scope backfill'. Presence uses the same id-based index as the
    batch pull, so id spelling differences between files never misreport a gap."""
    source_dir, _is_asset_drive, label = _resolve_artwork_scope(db, sync_target_index)
    from modules.artwork_pull import _items_from_scope

    index = af.build_scope_artwork_index(source_dir)
    items = []
    for it in _items_from_scope(source_dir):
        # Collections only ever get backgrounds (TMDB has no collection logos/square art),
        # so logo/squareart gaps aren't reported for them.
        types = ["background"] if it.is_collection else ["logo", "background", "squareart"]
        missing = [t for t in types if not af.scope_has_artwork(index, it, t)]
        items.append({
            "title": it.title,
            "year": it.year,
            "media_type": it.media_type,
            "tmdb_id": it.tmdb_id,
            "tvdb_id": it.tvdb_id,
            "imdb_id": it.imdb_id,
            "missing": missing,
        })
    items.sort(key=lambda x: (x["title"] or "").lower())
    return {"items": items, "total": len(items), "scope_label": label}


@router.get("/tmdb-download")
def tmdb_tagged_download(
    path: str,
    role: str,
    title: str,
    media_type: str,
    year: Optional[int] = None,
    tmdb_id: Optional[int] = None,
    tvdb_id: Optional[int] = None,
    imdb_id: Optional[str] = None,
    season: Optional[int] = None,
):
    """Stream a TMDB or TheTVDB image with a canonical download filename (ids + artwork tag), so a
    manually downloaded logo/backdrop/poster is already named the way the rest of the app expects.

    ``path`` is source-qualified the same way PSD export refs are: a TMDB file_path ('/abc.jpg')
    or an absolute TVDB artwork URL."""
    is_tmdb = path.startswith("/")
    if not is_tmdb and not tvdb.is_tvdb_image_url(path):
        raise HTTPException(status_code=400, detail="Invalid image path")
    item = _make_item(title=title, media_type=media_type, year=year, tmdb_id=tmdb_id,
                      tvdb_id=tvdb_id, imdb_id=imdb_id)
    src_ext = os.path.splitext(urlparse(path).path if not is_tmdb else path)[1] or ".jpg"
    filename = af.build_download_filename(item, role, season, src_ext)
    try:
        resp = requests.get(f"https://image.tmdb.org/t/p/original{path}" if is_tmdb else path,
                            stream=True, timeout=30)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch image: {exc}")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Image fetch failed (HTTP {resp.status_code}).")
    # RFC 5987: ascii fallback + utf-8 form, so unicode titles don't break the (latin-1) header.
    ascii_name = filename.encode("ascii", "ignore").decode() or "artwork"
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(resp.iter_content(chunk_size=8192),
                             media_type=resp.headers.get("content-type", "image/jpeg"),
                             headers={"Content-Disposition": disposition})


@router.get("/gracenote-image-proxy")
def gracenote_image_proxy(url: str = Query(...)):
    """Proxy a Gracenote (*.plex.tv) image so the browser can preview square art / clear logos
    without mixed-content / CORS issues. Host-allowlisted to *.plex.tv."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "plex.tv" or host.endswith(".plex.tv")):
        raise HTTPException(status_code=400, detail="Only https *.plex.tv image URLs are allowed.")
    try:
        resp = requests.get(url, stream=True, timeout=30)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch image: {exc}")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Image fetch failed (HTTP {resp.status_code}).")
    return StreamingResponse(resp.iter_content(chunk_size=8192),
                             media_type=resp.headers.get("content-type", "image/jpeg"))


class BatchPullRequest(BaseModel):
    sync_target_index: int
    source: str = "poster_drives"          # poster_drives | libraries | scope | list
    drive_ids: Optional[list[int]] = None  # poster_drives
    paste: Optional[str] = None            # list
    types: list[str] = ["logo", "background", "squareart"]
    min_backdrop_width: int = 1920
    make_white_logos: bool = False         # recolor the least-colored logo to white when none pass
    skip_unreleased: bool = True           # skip arr items whose status is announced/upcoming/tba
    force_refetch: bool = False            # ignore the already-have check; re-pull + overwrite everything
    sync_after_run: bool = False           # queue an IDarr run to upload afterwards
    dry_run: bool = False


@router.post("/batch-pull")
def batch_pull(request: BatchPullRequest, db: Session = Depends(get_db)) -> dict:
    """Queue a background job that fetches missing logos/backgrounds/square art for a set of items
    (poster drives / Plex libraries / the scope's own items / a pasted list) into the scope."""
    key = _require_tmdb_key(db)
    if request.source not in ("poster_drives", "libraries", "scope", "list"):
        raise HTTPException(status_code=400, detail="source must be poster_drives, libraries, scope, or list")
    types = [t for t in request.types if t in af.SUBTYPE_EXT]
    if not types:
        raise HTTPException(status_code=400, detail="Select at least one artwork type.")
    source_dir, is_asset_drive, label = _resolve_artwork_scope(db, request.sync_target_index)

    active = (db.query(Job)
              .filter(Job.job_type == JOB_TYPE_ARTWORK_PULL, Job.status.in_(JOB_STATUSES_ACTIVE))
              .first())
    if active:
        raise HTTPException(status_code=409, detail=f"An Artwork Pull job ({active.id}) is already {active.status}.")

    config_data = {
        "sync_target_index": request.sync_target_index,
        "source": request.source,
        "drive_ids": request.drive_ids,
        "paste": request.paste,
        "types": types,
        "min_backdrop_width": request.min_backdrop_width,
        "make_white_logos": request.make_white_logos,
        "skip_unreleased": request.skip_unreleased,
        "force_refetch": request.force_refetch,
        "sync_after_run": request.sync_after_run,
        "dry_run": request.dry_run,
        "source_dir": str(source_dir),
        "is_asset_drive": is_asset_drive,
        "tmdb_api_key": key,
    }
    from modules.artwork_pull import run_artwork_pull_background_job
    job = create_job(db, JOB_TYPE_ARTWORK_PULL, f"Artwork Pull ({request.source})")
    job_queue.submit(run_artwork_pull_background_job, job.id, job.id, config_data)
    log_user_action("Started Artwork Pull", scope=label or str(source_dir), source=request.source, types=types)
    return {"job_id": job.id, "message": f"Artwork Pull queued ({request.source})"}
