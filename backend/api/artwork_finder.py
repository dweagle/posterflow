"""Artwork Finder API — browse logos / backgrounds / square art (TMDB + Plex Gracenote, TheTVDB, or fanart.tv) and
add a chosen one into an IDarr artwork scope. New, self-contained router; delete the file to
remove the endpoints.
"""
import base64
import io
import json
import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from api.idarr import SETTING_MAKER_IDARR_CONFIG
from core.config import settings as app_settings
from core.job_queue import job_queue
from core.logging import log_user_action
from database import get_db
from models.job import JOB_STATUSES_ACTIVE, JOB_TYPE_ARTWORK_PULL, Job, create_job
from models.setting import get_setting, upsert_setting
from services import artwork_finder as af
from services import fanart
from services import text_logo
from services import tvdb

router = APIRouter(prefix="/api/artwork-finder", tags=["artwork-finder"])


# ------------------------------------------------------------------ schemas

class Candidate(BaseModel):
    source: str            # "tmdb" | "gracenote" | "tvdb" | "fanart"
    ref: str               # tmdb file_path ("/xxx.png"), or an absolute gracenote/TVDB/fanart.tv CDN url
    width: Optional[int] = None
    height: Optional[int] = None
    language: Optional[str] = None          # ISO 639-1, or None when the image is textless
    off_white_pct: Optional[float] = None   # logos only, when evaluated
    is_white: Optional[bool] = None
    origin: Optional[str] = None            # collection logos: the member movie this came from


# Listable types include "poster" (a crop-to-square source), which is NOT a savable subtype.
LISTABLE_TYPES = {"logo", "background", "squareart", "poster"}

# Sources the browser can list, and the wider set a chosen candidate may come from.
BROWSE_SOURCES = ("tmdb", "tvdb", "fanart")
CANDIDATE_SOURCES = ("tmdb", "gracenote", "tvdb", "fanart")


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
    source: str            # tmdb | gracenote | tvdb | fanart
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
    source: str            # tmdb | gracenote | tvdb | fanart
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


def _resolve_sync_target(db: Session, index: int) -> dict:
    """The IDarr sync target at `index`, validated to exist and carry a source_dir, or 400."""
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
    if not str(targets[index].get("source_dir") or "").strip():
        raise HTTPException(status_code=400, detail="Selected scope is missing source_dir.")
    return targets[index]


def _resolve_artwork_scope(db: Session, index: int) -> tuple[Path, bool, str]:
    """(source_dir, is_asset_drive, label) for the selected IDarr sync target, or 400."""
    target = _resolve_sync_target(db, index)
    if not bool(target.get("is_asset_drive")):
        raise HTTPException(status_code=400,
                            detail="Selected scope is not an artwork scope. Enable 'Assets Drive' on that IDarr sync target.")
    return Path(str(target["source_dir"]).strip()), True, str(target.get("label") or "")


def _resolve_poster_scope(db: Session, index: int) -> Path:
    """source_dir of a poster sync target — one that holds posters, not artwork or PSDs."""
    target = _resolve_sync_target(db, index)
    if target.get("is_asset_drive") or target.get("is_psd_drive"):
        raise HTTPException(status_code=400, detail="item_scope_index must be a poster scope (not an artwork or PSD scope).")
    return Path(str(target["source_dir"]).strip())


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
    language: str = "en+textless",   # TMDB image language: all | en+textless | an ISO code
    db: Session = Depends(get_db),
) -> CandidatesResponse:
    """Browse logo / background / square-art / poster candidates for one title, from one source.

    ``source=tmdb`` (the default) lists TMDB logos/backgrounds/posters plus square art from Plex's
    Gracenote provider. ``source=tvdb`` and ``source=fanart`` list TheTVDB's or fanart.tv's
    instead — each is only ever called when the user picks that tab. TVDB carries no square art;
    fanart.tv lists only its textless squares (nearly all of them carry the title).

    Backgrounds are listed unfiltered here (textless first), unlike the batch pull's strict
    textless/min-width rule — browsing is a human choice, and the strict rule left obscure titles
    showing nothing at all."""
    src = str(source or "tmdb").strip().lower()
    if src not in BROWSE_SOURCES:
        raise HTTPException(status_code=400, detail="source must be tmdb, tvdb, or fanart")

    item = _make_item(title=title, media_type=media_type, year=year, tmdb_id=tmdb_id,
                      tvdb_id=tvdb_id, imdb_id=imdb_id)
    wanted = [t.strip() for t in types.split(",") if t.strip() in LISTABLE_TYPES]

    key = _require_tmdb_key(db) if src == "tmdb" else ""
    tvdb_creds: Optional[tuple[str, str]] = None
    fanart_key = ""
    if src == "tvdb":
        tvdb_key, tvdb_pin = tvdb.get_tvdb_credentials(db)
        if not tvdb_key:
            raise HTTPException(status_code=400,
                                detail="TheTVDB API key not configured. Set it in Settings → General → API Keys.")
        tvdb_creds = (tvdb_key, tvdb_pin)
    elif src == "fanart":
        fanart_key = fanart.get_fanart_key(db)
        if not fanart_key:
            raise HTTPException(status_code=400,
                                detail="fanart.tv API key not configured. Set it in Settings → General → API Keys.")

    session = requests.Session()
    token = af.get_plex_token(db)
    plex = af.PlexMetadataProvider(token, session) if token else None
    try:
        result = af.list_candidates(item, wanted, tmdb_api_key=key, plex=plex, session=session,
                                    evaluate_white=evaluate_white, source=src, tvdb_creds=tvdb_creds,
                                    fanart_api_key=fanart_key,
                                    textless_backgrounds=False,
                                    image_language=af.tmdb_image_language(language))
    except tvdb.TvdbError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    except fanart.FanartError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    return CandidatesResponse(**result)


@router.post("/add", response_model=AddResponse)
def add_artwork(request: AddRequest, db: Session = Depends(get_db)) -> AddResponse:
    """Download the chosen candidate (optionally recolored to white) and write it into the
    selected artwork scope so IDarr can rename + upload it."""
    if request.subtype not in af.SUBTYPE_EXT:
        raise HTTPException(status_code=400, detail="subtype must be logo, background, or squareart")
    if request.source not in CANDIDATE_SOURCES:
        raise HTTPException(status_code=400, detail="source must be tmdb, gracenote, tvdb, or fanart")

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
    if request.source not in CANDIDATE_SOURCES:
        raise HTTPException(status_code=400, detail="source must be tmdb, gracenote, tvdb, or fanart")
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


SCOPE_ITEM_SOURCES = ("scope", "poster_scope", "poster_drives")


@router.get("/scope-items")
def scope_items(sync_target_index: int, source: str = "scope",
                item_scope_index: Optional[int] = None,
                db: Session = Depends(get_db)) -> dict:
    """Enumerate items with what artwork each is missing from the selected scope — the browsable
    counterpart of the batch pull, over the same item sources it offers.

    ``source=scope`` walks the artwork scope's own files, so it can only ever report gaps for items
    that already have some artwork there. The rest enumerate elsewhere — one poster sync target
    (``poster_scope`` + ``item_scope_index``) or every subscribed drive (``poster_drives``) — which
    is the only way an item with NO artwork yet shows up at all. Presence always uses the batch
    pull's id-based index, so id spelling differences never misreport a gap."""
    src = str(source or "scope").strip().lower()
    if src not in SCOPE_ITEM_SOURCES:
        raise HTTPException(status_code=400, detail=f"source must be one of {', '.join(SCOPE_ITEM_SOURCES)}")
    source_dir, is_asset_drive, label = _resolve_artwork_scope(db, sync_target_index)
    poster_dir = ""
    if src == "poster_scope":
        if item_scope_index is None:
            raise HTTPException(status_code=400, detail="source=poster_scope needs item_scope_index.")
        poster_dir = str(_resolve_poster_scope(db, item_scope_index))
    from modules.artwork_pull import _resolve_items

    # IDarr's cache carries the type it actually resolved each file to, which the filename can't
    # always say — a series TheTVDB doesn't list has no {tvdb-…} tag and otherwise reads as a movie.
    identity = af.build_scope_identity_index(db, sync_target_index, source_dir)
    index = af.build_scope_artwork_index(source_dir, identity)
    config_data = {"source_dir": str(source_dir), "is_asset_drive": is_asset_drive,
                   "poster_source_dir": poster_dir}
    items = []
    for it in _resolve_items(db, src, config_data, identity):
        # Every item is checked for all three types, collections included. No source serves
        # collection logos or square art, so the batch pull still clamps collections to
        # backgrounds — but this is the browse view, and hiding the gap hid the drive's
        # hand-made collection logos too, making the tab look like it had found nothing.
        missing = [t for t in ("logo", "background", "squareart")
                   if not af.scope_has_artwork(index, it, t)]
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
    return {"items": items, "total": len(items), "scope_label": label, "source": src}


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
    """Stream a TMDB, TheTVDB or fanart.tv image with a canonical download filename (ids + artwork
    tag), so a manually downloaded logo/backdrop/poster is already named the way the rest of the
    app expects.

    ``path`` is source-qualified the same way PSD export refs are: a TMDB file_path ('/abc.jpg')
    or an absolute TVDB / fanart.tv artwork URL."""
    is_tmdb = path.startswith("/")
    if not is_tmdb and not tvdb.is_tvdb_image_url(path) and not fanart.is_fanart_image_url(path):
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


@router.get("/fanart-image-proxy")
def fanart_image_proxy(url: str = Query(...)):
    """Proxy a fanart.tv asset download so the browser gets a proper filename. Host-allowlisted to
    assets.fanart.tv."""
    if not fanart.is_fanart_image_url(url):
        raise HTTPException(status_code=400, detail="Only https assets.fanart.tv image URLs are allowed.")
    try:
        resp = requests.get(url, stream=True, timeout=30)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch image: {exc}")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Image fetch failed (HTTP {resp.status_code}).")
    filename = urlparse(url).path.rstrip("/").split("/")[-1] or "artwork.jpg"
    return StreamingResponse(resp.iter_content(chunk_size=8192),
                             media_type=resp.headers.get("content-type", "image/jpeg"),
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ------------------------------------------------------------------ local picker folder

SETTING_ARTWORK_PICKER_FOLDER = "artwork_picker_folder"


# Picker roots, in listing order. "bundled" ships with the app, "art" is the user's own reusable
# stash under config, "folder" is whatever folder they point the picker at.
BUNDLED_ARTWORK_DIR = Path(__file__).resolve().parent.parent / "assets" / "artwork"
PICKER_SOURCES = ("bundled", "art", "folder")


class PickerFile(BaseModel):
    name: str
    path: str          # relative to its own root
    source: str        # bundled | art | folder — which root `path` is relative to
    width: Optional[int] = None
    height: Optional[int] = None


class PickerFolderResponse(BaseModel):
    folder: str
    art_dir: str = ""             # config/artwork/art — where reusable artwork can be dropped
    backgrounds: list[PickerFile] = []
    squareart: list[PickerFile] = []
    truncated: bool = False
    error: Optional[str] = None   # folder configured but currently unusable


class SetPickerFolderRequest(BaseModel):
    folder: str


class AddLocalRequest(BaseModel):
    sync_target_index: int
    title: str
    media_type: str        # movie | tv | collection
    subtype: str           # background | squareart (logos aren't picked from folders)
    path: str              # relative to the root named by `source`
    source: str = "folder"
    year: Optional[int] = None
    tmdb_id: Optional[int] = None
    tvdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    confirm_overwrite: bool = False

    _year = field_validator("year", mode="before")(_year_or_none)


def _picker_folder(db: Session) -> Optional[Path]:
    setting = get_setting(db, SETTING_ARTWORK_PICKER_FOLDER)
    raw = str((setting.value if setting else "") or "").strip()
    return Path(raw) if raw else None


def _art_dir() -> Path:
    """The user's reusable artwork stash, alongside the text-logo fonts."""
    return app_settings.config_dir / "artwork" / "art"


def _picker_root(db: Session, source: str) -> Path:
    """The directory a picker `source` names, or 400 when it's unusable."""
    src = str(source or "folder").strip().lower()
    if src not in PICKER_SOURCES:
        raise HTTPException(status_code=400, detail=f"source must be one of {', '.join(PICKER_SOURCES)}")
    if src == "bundled":
        return BUNDLED_ARTWORK_DIR
    if src == "art":
        return _art_dir()
    folder = _picker_folder(db)
    if folder is None:
        raise HTTPException(status_code=400, detail="No artwork folder configured. Set one in the picker first.")
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Artwork folder not found on the server: {folder}")
    return folder


def _resolve_picker_file(folder: Path, rel: str) -> Path:
    """`rel` resolved inside one picker root, or 4xx. The containment check is the security
    boundary — /local-image is auth-exempt like the other source-image endpoints.
    `rel` is joined VERBATIM — filenames legitimately start with spaces, so stripping here
    breaks them (it 404'd real files in the field)."""
    raw = str(rel or "")
    if not raw.strip():
        raise HTTPException(status_code=400, detail="path is required")
    root = folder.resolve()
    target = (root / raw).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=403, detail="path is outside the configured artwork folder")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    if target.suffix.lower() not in af.PICKER_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="not an image file")
    return target


def _picker_listing(db: Session, folder: Optional[Path]) -> dict:
    """Every picker root merged into one listing: the bundled artwork first, then the user's
    config/artwork/art stash, then the chosen folder."""
    backgrounds: list[dict] = []
    squareart: list[dict] = []
    truncated = False
    roots: list[tuple[Path, str]] = [(BUNDLED_ARTWORK_DIR, "bundled"), (_art_dir(), "art")]
    if folder is not None and folder.is_dir():
        roots.append((folder, "folder"))
    for root, source in roots:
        if not root.is_dir():
            continue
        scan = af.scan_picker_folder(root, source=source)
        backgrounds.extend(scan["backgrounds"])
        squareart.extend(scan["squareart"])
        truncated = truncated or scan["truncated"]
    return {"backgrounds": backgrounds, "squareart": squareart, "truncated": truncated,
            "art_dir": str(_art_dir())}


@router.get("/local-folder", response_model=PickerFolderResponse)
def get_picker_folder(db: Session = Depends(get_db)) -> PickerFolderResponse:
    """The picker's images — bundled artwork and the config/artwork/art stash always, plus the
    configured folder when one is set and usable. A set-but-missing folder still returns its path
    plus an error string, so the UI can prefill the input for fixing."""
    folder = _picker_folder(db)
    error = "Folder not found on the server." if folder is not None and not folder.is_dir() else None
    return PickerFolderResponse(folder=str(folder) if folder else "", error=error,
                                **_picker_listing(db, folder))


@router.put("/local-folder", response_model=PickerFolderResponse)
def set_picker_folder(request: SetPickerFolderRequest, db: Session = Depends(get_db)) -> PickerFolderResponse:
    """Save the picker folder path (absolute, no '..', must exist server-side) and list it."""
    raw = str(request.folder or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="folder is required")
    folder = Path(raw)
    if not folder.is_absolute() or ".." in folder.parts:
        raise HTTPException(status_code=400, detail="folder must be an absolute path without '..'")
    folder = folder.resolve()
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Folder not found on the server: {folder}")
    upsert_setting(db, SETTING_ARTWORK_PICKER_FOLDER, str(folder))
    db.commit()
    return PickerFolderResponse(folder=str(folder), **_picker_listing(db, folder))


@router.get("/local-image")
def picker_image(path: str, source: str = "folder", db: Session = Depends(get_db)) -> FileResponse:
    """Stream an image from inside one of the picker roots for thumbnails / preview."""
    target = _resolve_picker_file(_picker_root(db, source), path)
    return FileResponse(str(target), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@router.post("/add-local", response_model=AddResponse)
def add_local_artwork(request: AddLocalRequest, db: Session = Depends(get_db)) -> AddResponse:
    """Copy a picked file from one of the picker roots into the selected artwork scope under
    IDarr's canonical name — the folder-picker counterpart of /add."""
    if request.subtype not in ("background", "squareart"):
        raise HTTPException(status_code=400, detail="subtype must be background or squareart")
    src = _resolve_picker_file(_picker_root(db, request.source), request.path)
    source_dir, is_asset_drive, label = _resolve_artwork_scope(db, request.sync_target_index)
    item = _make_item(title=request.title, media_type=request.media_type, year=request.year,
                      tmdb_id=request.tmdb_id, tvdb_id=request.tvdb_id, imdb_id=request.imdb_id)
    try:
        result = af.save_local_file(source_dir=source_dir, is_asset_drive=is_asset_drive, item=item,
                                    subtype=request.subtype, src=src,
                                    confirm_overwrite=request.confirm_overwrite)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if result["status"] == "added":
        log_user_action("Added artwork from local folder", scope=label or str(source_dir),
                        subtype=request.subtype, written=result["written"], source_file=src.name)
    return AddResponse(success=True, source_dir=str(source_dir), **result)


# ------------------------------------------------------------------ text logo

class TextLogoRequest(BaseModel):
    top: str = ""       # small condensed line above the title
    main: str           # the big Bebas line
    suffix: str = ""    # the spread-out "COLLECTION" line
    # Optional styling overrides for the title lines (the suffix stays fixed). Tracking is
    # Photoshop 1/1000-em units; scale is a horizontal-width percent for squeezing long titles;
    # fonts are ids from GET /text-logo/fonts.
    top_tracking: Optional[int] = None
    top_scale: Optional[int] = None
    main_tracking: Optional[int] = None
    main_scale: Optional[int] = None
    top_font: Optional[str] = None
    main_font: Optional[str] = None


class TextLogoPreviewResponse(BaseModel):
    png_base64: str
    width: int
    height: int


class AddTextLogoRequest(TextLogoRequest):
    sync_target_index: int
    title: str
    media_type: str        # movie | tv | collection
    year: Optional[int] = None
    tmdb_id: Optional[int] = None
    tvdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    confirm_overwrite: bool = False

    _year = field_validator("year", mode="before")(_year_or_none)


class TextLogoFont(BaseModel):
    id: str        # filename stem — what the render endpoints take as top_font / main_font
    label: str     # the font's own family/style name
    source: str    # config | bundled


@router.get("/text-logo/fonts")
def text_logo_fonts() -> dict:
    """Fonts available to the text-logo dialog's pickers: config/artwork/fonts first (those win
    name collisions with the bundled set)."""
    return {"fonts": [TextLogoFont(**f) for f in text_logo.list_fonts()]}


@router.post("/text-logo/preview", response_model=TextLogoPreviewResponse)
def text_logo_preview(request: TextLogoRequest) -> TextLogoPreviewResponse:
    """Render the PSD-style text logo and return it inline (base64), so the dialog can live-preview
    without an extra authed image round-trip."""
    try:
        image = text_logo.render_text_logo(
            request.top, request.main, request.suffix,
            top_tracking=request.top_tracking, top_scale=request.top_scale,
            main_tracking=request.main_tracking, main_scale=request.main_scale,
            top_font=request.top_font, main_font=request.main_font)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return TextLogoPreviewResponse(png_base64=base64.b64encode(buf.getvalue()).decode(),
                                   width=image.width, height=image.height)


@router.post("/text-logo", response_model=AddResponse)
def add_text_logo(request: AddTextLogoRequest, db: Session = Depends(get_db)) -> AddResponse:
    """Render the text logo and write it into the selected artwork scope under IDarr's canonical
    name — same save contract as /add."""
    if not request.main.strip():
        raise HTTPException(status_code=400, detail="The main line is required.")
    source_dir, is_asset_drive, label = _resolve_artwork_scope(db, request.sync_target_index)
    item = _make_item(title=request.title, media_type=request.media_type, year=request.year,
                      tmdb_id=request.tmdb_id, tvdb_id=request.tvdb_id, imdb_id=request.imdb_id)
    try:
        png = text_logo.render_text_logo_png(
            request.top, request.main, request.suffix,
            top_tracking=request.top_tracking, top_scale=request.top_scale,
            main_tracking=request.main_tracking, main_scale=request.main_scale,
            top_font=request.top_font, main_font=request.main_font)
        result = af.save_artwork_bytes(source_dir=source_dir, is_asset_drive=is_asset_drive,
                                       item=item, subtype="logo", data=png,
                                       confirm_overwrite=request.confirm_overwrite)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if result["status"] == "added":
        log_user_action("Added text logo via Artwork Finder", scope=label or str(source_dir),
                        written=result["written"], text=request.main)
    return AddResponse(success=True, source_dir=str(source_dir), **result)


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
