"""Maker actions on community requests: upload posters for a claimed request or list item, and mark
it complete. One implementation for the web app's Upload and Complete buttons and for the editor
panels (the Photoshop plugin and the in-app Photopea panel), which also find the claimed request
for the PSD they have open.

An upload does what the web app's Upload is set to do (Maker Tools settings): post to the request's
Discord thread, add to IDarr, or both. The web app says which posters to send. A panel sends only
the posters the request asks for, chosen by the upload picker's rule
(api.maker_tools.list_request_exports).

The web app sends its own Discord login with each call, so a server shared by several makers acts
as the one who pressed the button. It sends a large upload in parts of ten posters, a request each,
so no request is big or slow enough to trip a reverse proxy; IDarr still runs once, from the last
part. The panels hold nothing from Discord or the community service:
for them this server uses the login the web app stored (POST /api/community/identity). The routes
sit under /api/maker-tools, next to the panels' other calls.
"""
import json
import re
import threading
import time
import zlib
from collections import OrderedDict
from datetime import datetime
from pathlib import Path, PurePosixPath

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.community import SETTING_DISCORD_IDENTITY, SUPABASE_HEADERS, SUPABASE_URL, _cache_clear
from api.idarr import load_runtime_config, store_idarr_uploads
from api.jobs import StartIdarrRequest, start_idarr_job
from api.maker_tools import (_EXPORT_COLLECTION, _export_looks_like_person, _export_requested_slots, _export_slot,
                             _export_slot_requested, _resolve_export_file, list_request_exports)
from core.logging import LogTags, log_error, log_user_action
from database import get_db
from models.setting import get_setting_value
from services.community_reconcile import _load_json_setting, _token_exp
from util.constants import year_regex
from util.data.extract import extract_ids

router = APIRouter(prefix="/api/maker-tools/community", tags=["community"])

MAX_FILES = 10                  # Discord's attachments per message
MAX_BYTES = 8 * 1024 * 1024     # the post-poster function's per-file limit
MAX_STAGED = 60                 # posters one finish may send (60 seasons, Specials and the main poster fit twice over in practice)
STAGE_TTL = 600.0               # seconds staged posters wait for their send
PICK_TTL = 60.0                 # seconds an Upload button's pick waits for its second press
PART_TTL = 600.0                # seconds an upload sent in parts waits for its next part
MAX_PARTS = 200                 # parts one upload may be sent in
MAX_UPLOAD_FILES = 200          # posters one request from the web app may name or carry
MAX_UPLOAD_BYTES = 256 * 1024 * 1024   # and their size together, held in memory while they go out
MESSAGE_GAP = 1.5               # seconds between Discord messages, as the web app paces them
ATTEMPTS = 3

# The web app's upload settings: its "Adds to IDarr" switch, which IDarr flavour applies while it is
# on ("idarr" = IDarr only), and the IDarr drive picked in the app (the scope picker's own value).
SETTING_UPLOAD_ADDS_IDARR = "idarr_quick_add_community"
SETTING_UPLOAD_ACTION = "community_upload_action"
SETTING_IDARR_DRIVE = "idarr_selected_sync_target"
_ACTIONS = ("discord", "discord_idarr", "idarr")
_BATCH_ID = re.compile(r"^[0-9a-f]{16,64}$")

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
# What a finish of each kind can fulfil. A person poster is finished like a movie; the PSD name tells them apart.
_KIND_TYPES = {"movie": ["movie"], "show": ["show", "season"], "collection": ["collection"]}
_ALL_TYPES = ["movie", "show", "season", "collection", "person"]
# The two kinds of claimed row, by the path segment the panels use for them.
_TABLES = {"requests": "poster_requests", "list-items": "poster_list_items"}
_COLUMNS = "id,title,year,media_type,season_number,notes,tmdb_id,tvdb_id,status"
_EDGE_HEADERS = {k: v for k, v in SUPABASE_HEADERS.items() if k.lower() != "content-type"}

# In memory like the Photoshop queue: one worker, and nothing here is worth keeping across a restart.
# Posters sent by a finish, waiting for their upload: "where/id" -> {"at": monotonic, "files": name -> bytes}.
_staged: dict[str, dict] = {}
# What an Upload button's first press picked, waiting for the second: "where/id" -> {"at", "files": [(source, name)]}.
_picked: dict[str, dict] = {}
# Posters added to IDarr by an upload whose Discord post then failed: "where/id" -> their signature.
# The retry does not add them again.
_idarr_sent: dict[str, tuple] = {}
# What the earlier parts of an upload sent in parts put in IDarr, for the one run its last part starts:
# the upload's own id -> {"at", "names"}.
_parts: dict[str, dict] = {}
_lock = threading.Lock()
_sleep = time.sleep


def _check(where: str, row_id: str) -> str:
    """The key of a claimed row, or 4xx for a path that names none."""
    if where not in _TABLES:
        raise HTTPException(status_code=404, detail="Not found.")
    if not _UUID.match(row_id or ""):
        raise HTTPException(status_code=400, detail="Invalid id.")
    return f"{where}/{row_id}"


def _login(db: Session) -> dict:
    """The Discord login stored by the web app: state "ok" with id and token, else "not_connected" or "expired"."""
    identity = _load_json_setting(db, SETTING_DISCORD_IDENTITY) or {}
    if not isinstance(identity, dict) or not identity.get("discord_user_id") or not identity.get("discord_token"):
        return {"state": "not_connected"}
    exp = _token_exp(identity["discord_token"])
    if exp is not None and exp <= int(time.time()):
        return {"state": "expired"}
    return {"state": "ok", "id": str(identity["discord_user_id"]), "token": identity["discord_token"]}


def _require_login(db: Session, token: str | None = None) -> dict:
    """The Discord login to act with: the caller's own when the web app sent it, else the stored one."""
    if token:
        exp = _token_exp(token)
        if exp is not None and exp <= int(time.time()):
            raise HTTPException(status_code=409, detail="Your Discord login has expired. Reconnect Discord.")
        return {"state": "ok", "token": token}
    login = _login(db)
    if login["state"] == "not_connected":
        raise HTTPException(status_code=409, detail="Discord is not connected in Posterflow.")
    if login["state"] == "expired":
        raise HTTPException(status_code=409, detail="The Discord login in Posterflow has expired. Reconnect Discord there.")
    return login


def _label(row: dict, list_item: bool = False) -> str:
    text = row.get("title") or "Untitled"
    if row.get("year"):
        text += f" ({row['year']})"
    if row.get("media_type") == "season" and row.get("season_number") is not None:
        text += f" S{row['season_number']}"
    return text + (" · list" if list_item else "")


def _edge_error(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("error") or f"HTTP {resp.status_code}")
    except Exception:
        return f"HTTP {resp.status_code}"


def _posters(count: int) -> str:
    return f"{count} poster" + ("" if count == 1 else "s")


# ---- What an upload does: the web app's upload settings ----

def _upload_action(db: Session, given: str | None = None) -> str:
    """What the web app's Upload button is set to do: "discord", "discord_idarr" or "idarr".
    The web app gives it as it stands on screen; a panel leaves it to the saved settings."""
    if given:
        if given not in _ACTIONS:
            raise HTTPException(status_code=400, detail="action must be discord, discord_idarr or idarr")
        return given
    if str(get_setting_value(db, SETTING_UPLOAD_ADDS_IDARR) or "").strip().lower() != "true":
        return "discord"
    return "idarr" if str(get_setting_value(db, SETTING_UPLOAD_ACTION) or "").strip().lower() == "idarr" else "discord_idarr"


def _idarr_drive(db: Session, given: str | None = None) -> tuple[int, str] | None:
    """The poster drive the web app has picked for IDarr, else the first poster drive: its place among
    the sync targets and its label. Artwork and PSD drives never take posters. The web app gives its
    pick as it stands on screen; a panel leaves it to the saved one."""
    targets = load_runtime_config(db).sync_targets
    stored = str(given or get_setting_value(db, SETTING_IDARR_DRIVE) or "").strip()

    def legacy(t: dict) -> str:
        return "::".join(str(t.get(k) or "").strip() for k in ("personal_drive_id", "source_dir", "label"))

    def current(t: dict) -> str:
        token = str(t.get("scope_token") or "").strip()
        return f"scope:{token}" if token else legacy(t)

    def takes_posters(t: dict) -> bool:
        return not t.get("is_asset_drive") and not t.get("is_psd_drive")

    index = -1
    if stored:
        index = next((i for i, t in enumerate(targets) if current(t) == stored), -1)
        if index < 0:
            index = next((i for i, t in enumerate(targets) if legacy(t) == stored), -1)
    if index < 0 or not takes_posters(targets[index]):
        index = next((i for i, t in enumerate(targets) if takes_posters(t)), -1)
    if index < 0:
        return None
    t = targets[index]
    return index, str(t.get("label") or "").strip() or str(t.get("source_dir") or "").strip() or f"Drive {index + 1}"


def _upload_plan(db: Session, list_item: bool, has_thread: bool, label: str,
                 action: str | None = None, drive: str | None = None) -> dict:
    """What an upload for a claimed row would do right now. `discord` / `idarr`: the halves that run.
    `drive`: (index, label) for IDarr. `will`: it in words. `aside`: a half the settings ask for that
    cannot run. `problem`: why nothing can run. `action` and `drive` are the web app's own, when it asks."""
    action = _upload_action(db, action)
    wants_discord, wants_idarr = action != "idarr", action != "discord"
    drive = _idarr_drive(db, drive) if wants_idarr else None
    discord = wants_discord and not list_item and has_thread
    idarr = wants_idarr and drive is not None
    no_thread = f"{label} is a list item. It has no Discord thread" if list_item else f"{label} has no Discord thread"
    plan = {"discord": discord, "idarr": idarr, "drive": drive, "will": "", "aside": "", "problem": ""}
    if not discord and not idarr:
        if not wants_idarr:
            plan["problem"] = no_thread + (", and Adds to IDarr is off in Posterflow" if list_item else "")
        elif not wants_discord:
            plan["problem"] = "IDarr has no poster drive in Posterflow"
        else:
            plan["problem"] = no_thread + ", and IDarr has no poster drive in Posterflow"
        return plan
    to_idarr = f"add to IDarr ({drive[1]})" if idarr else ""
    plan["will"] = " and ".join(part for part in ("post to Discord" if discord else "", to_idarr) if part)
    if wants_discord and not discord:
        plan["aside"] = "No Discord thread, so nothing goes to Discord"
    elif wants_idarr and not idarr:
        plan["aside"] = "IDarr has no poster drive, so nothing goes to IDarr"
    return plan


# ---- The claimed request (else list item) for a PSD ----

def _claimed_rows(client: httpx.Client, table: str, select: str, discord_id: str, tmdb_id: int, types: list[str]) -> list[dict]:
    """This maker's claimed rows for one title, in progress or already fulfilled."""
    resp = client.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=SUPABASE_HEADERS,
        params={
            "select": select,
            "claimed_by_discord_id": f"eq.{discord_id}",
            "tmdb_id": f"eq.{tmdb_id}",
            "media_type": f"in.({','.join(types)})",
            "status": "in.(in_progress,fulfilled)",
            "limit": 50,
        },
    )
    resp.raise_for_status()
    return resp.json() or []


def _fits_name(row: dict, name: str) -> bool:
    """Whether a claimed row can be the title a PSD is named for. TMDB numbers repeat across movies,
    shows, people and collections, so the id alone does not say."""
    if (row.get("media_type") in ("person", "collection")) != _export_looks_like_person(name):
        return False
    named = year_regex.search(name)
    return not (named and row.get("year") and int(named.group(1)) != int(row["year"]))


def _match(db: Session, where: str, row: dict) -> dict:
    list_item = where != "requests"
    label = _label(row, list_item)
    has_thread = not list_item and bool(row.get("discord_message_id"))
    plan = _upload_plan(db, list_item, has_thread, label)
    return {"status": "match", "type": "list_item" if list_item else "request", "where": where, "id": row["id"], "label": label,
            "has_thread": has_thread, "upload": {key: plan[key] for key in ("will", "aside", "problem")}}


def _find_claim(db: Session, kind: str | None, name: str) -> tuple[dict, dict | None]:
    """The answer for a PSD's claim lookup, and the row when exactly one matched.
    `kind` is the finish that asks (movie, show, collection); None looks across every type."""
    if kind is None:
        types = _ALL_TYPES
    elif kind in _KIND_TYPES:
        types = _KIND_TYPES[kind]
    else:
        raise HTTPException(status_code=400, detail="kind must be movie, show, or collection")
    tmdb_id, _, _ = extract_ids(name)
    if not tmdb_id:
        return {"status": "no_id"}, None
    if kind == "movie" and _export_looks_like_person(name):
        types = ["person"]   # TMDB ids are per namespace: a person poster never answers a movie request
    login = _login(db)
    if login["state"] != "ok":
        return {"status": login["state"]}, None
    fits = (lambda row: _fits_name(row, name)) if kind is None else (lambda row: True)
    try:
        with httpx.Client(timeout=15.0) as client:
            requests = [r for r in _claimed_rows(client, "poster_requests", _COLUMNS + ",discord_message_id", login["id"], tmdb_id, types) if fits(r)]
            active = [r for r in requests if r.get("status") == "in_progress"]
            if len(active) > 1:
                return {"status": "several", "type": "request", "labels": [_label(r) for r in active]}, None
            if active:
                return _match(db, "requests", active[0]), active[0]
            items = [i for i in _claimed_rows(client, "poster_list_items", _COLUMNS, login["id"], tmdb_id, types) if fits(i)]
    except (httpx.HTTPError, ValueError) as exc:
        log_error(LogTags.API, f"Community claim lookup failed: {exc}")
        raise HTTPException(status_code=502, detail="Could not read your claimed requests.")
    active_items = [i for i in items if i.get("status") == "in_progress"]
    if len(active_items) > 1:
        return {"status": "several", "type": "list_item", "labels": [_label(i, True) for i in active_items]}, None
    if active_items:
        return _match(db, "list-items", active_items[0]), active_items[0]
    if requests:
        return {"status": "done", "label": _label(requests[0])}, None
    if items:
        return {"status": "done", "label": _label(items[0], True)}, None
    return {"status": "none"}, None


@router.get("/claim")
def find_claim(name: str = Query(...), kind: str | None = Query(None), db: Session = Depends(get_db)) -> dict:
    """The maker's claimed, in-progress request (else list item) for a PSD, found by the {tmdb-…} id in
    its name. A finish says its kind; a panel button leaves it out and every type is looked at.
    status: match | several | done | none | no_id | not_connected | expired. A match says where it
    lives (`where`: requests | list-items) and what an upload for it would do (`upload`)."""
    return _find_claim(db, kind, name)[0]


def _read_row(where: str, row_id: str) -> dict:
    """A claimed row, read fresh: what it asks for, and whether it has a thread."""
    columns = "id,title,year,media_type,season_number,notes" + (",discord_message_id" if where == "requests" else "")
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{SUPABASE_URL}/rest/v1/{_TABLES[where]}", headers=SUPABASE_HEADERS,
                              params={"select": columns, "id": f"eq.{row_id}", "limit": 1})
            resp.raise_for_status()
            rows = resp.json() or []
    except (httpx.HTTPError, ValueError) as exc:
        log_error(LogTags.API, f"Community request read failed: {exc}")
        raise HTTPException(status_code=502, detail="Could not read the request.")
    if not rows:
        raise HTTPException(status_code=404, detail="The request was not found.")
    return rows[0]


# ---- The upload itself: IDarr first, then Discord, as the web app does it ----

def _post_message(client: httpx.Client, token: str, request_id: str, batch: list[tuple[str, bytes]]) -> str:
    """One Discord message of up to ten images. Returns '' or why it failed. A refusal (4xx) is final;
    a busy or failing service is tried again."""
    error = ""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            resp = client.post(
                f"{SUPABASE_URL}/functions/v1/post-poster",
                headers=_EDGE_HEADERS,
                data={"token": token, "request_id": request_id, "filenames": json.dumps([name for name, _ in batch])},
                files=[("file", (name, data, _IMAGE_TYPES[PurePosixPath(name).suffix.lower()])) for name, data in batch],
            )
            if resp.is_success:
                return ""
            error = _edge_error(resp)
            if resp.status_code < 500 and resp.status_code != 429:
                return error
        except httpx.HTTPError as exc:
            error = str(exc) or exc.__class__.__name__
        if attempt < ATTEMPTS:
            _sleep(MESSAGE_GAP * attempt)
    return error


def _post_files(token: str, request_id: str, files: list[tuple[str, bytes]], part: tuple[str, int, int] | None = None) -> int:
    """Post images to the request's Discord thread, ten per message, in order. Returns the message
    count. A part of a larger upload is one message of it, and a failure is numbered that way."""
    batches = [files[i:i + MAX_FILES] for i in range(0, len(files), MAX_FILES)]
    with httpx.Client(timeout=60.0) as client:
        for i, batch in enumerate(batches, 1):
            if i > 1:
                _sleep(MESSAGE_GAP)
            error = _post_message(client, token, request_id, batch)
            if error:
                n, of = (part[1], part[2]) if part and len(batches) == 1 else (i, len(batches))
                earlier = " (earlier messages were posted)" if n > 1 else ""
                log_error(LogTags.API, f"Community poster post failed at message {n}/{of}: {error}")
                raise HTTPException(status_code=502, detail=f"Discord post failed at message {n} of {of}{earlier}: {error}")
    return len(batches)


def _part(batch: str | None, part: int | None, of: int | None) -> tuple[str, int, int] | None:
    """Which part of an upload sent in several requests this one is: (the upload's id, n, of). None for a whole upload."""
    if part is None and of is None:
        return None
    if not batch or not _BATCH_ID.match(batch) or part is None or of is None or not 1 <= part <= of <= MAX_PARTS:
        raise HTTPException(status_code=400, detail="Invalid upload part.")
    return batch, part, of


def _add_to_idarr(db: Session, index: int, files: list[tuple[str, bytes]],
                  part: tuple[str, int, int] | None = None) -> tuple[int, dict | None, str]:
    """The IDarr quick add: the files go into the drive's folder, then IDarr runs on them when its
    quick-add rename is on. An upload sent in parts runs IDarr once, from its last part, on what
    every part added. Returns how many were added, the run it started ({id, index, sync_after},
    which the web app watches for its notice), and '' or why a run did not start."""
    stored = store_idarr_uploads(db, index, files)
    if part:
        batch, n, of = part
        now = time.monotonic()
        with _lock:
            _purge(_parts, PART_TTL, now)
            entry = _parts.setdefault(batch, {"at": now, "names": []})
            entry["at"] = now
            entry["names"] += [name for name in stored["uploaded"] if name not in entry["names"]]
            if n < of:
                return stored["uploaded_count"], None, ""
            stored = dict(stored, uploaded=list(_parts.pop(batch)["names"]))
    config = load_runtime_config(db)
    if not config.auto_rename_quick_add:
        return stored["uploaded_count"], None, ""
    sync_after = bool(config.auto_upload_quick_add)
    try:
        job = start_idarr_job(StartIdarrRequest(sync_target_index=index, source_filenames=stored["uploaded"], sync_after_run=sync_after), db)
    except HTTPException as exc:
        return stored["uploaded_count"], None, str(exc.detail)
    return stored["uploaded_count"], {"id": job.id, "index": index, "sync_after": sync_after}, ""


def _did(result: dict, label: str) -> str:
    posted, added, drive = result["posted"], result["idarr"], result["drive"]
    if posted and added:
        text = f"posted {_posters(posted)} to {label} and added {'it' if added == 1 else 'them'} to IDarr ({drive})"
    elif posted:
        text = f"posted {_posters(posted)} to {label}"
    else:
        text = f"added {_posters(added)} for {label} to IDarr ({drive})"
    return ". ".join([text] + result["warnings"])


def _upload(db: Session, where: str, row: dict, files: list[tuple[str, bytes]], action: str | None = None,
            drive: str | None = None, token: str | None = None, part: tuple[str, int, int] | None = None) -> dict:
    """Upload posters for a claimed row as the web app's Upload button is set. Returns the counts
    (`posted`, `messages`, `idarr`), the IDarr `drive` and `idarr_job`, `warnings`, and `did`: all of
    it in words. `action`, `drive`, `token` and `part` (_part: these posters are one part of a larger
    upload) are the web app's own, when it is the one asking."""
    key = f"{where}/{row['id']}"
    list_item = where != "requests"
    label = _label(row, list_item)
    plan = _upload_plan(db, list_item, bool(row.get("discord_message_id")), label, action, drive)
    if plan["problem"]:
        raise HTTPException(status_code=409, detail=plan["problem"] + ".")
    login = _require_login(db, token) if plan["discord"] else None
    if plan["discord"]:
        for name, data in files:
            if PurePosixPath(name).suffix.lower() not in _IMAGE_TYPES:
                raise HTTPException(status_code=400, detail=f'"{name}" is not a JPG, PNG or WebP image.')
            if len(data) > MAX_BYTES:
                raise HTTPException(status_code=413, detail=f'"{name}" is over the 8 MB Discord limit.')
    result = {"posted": 0, "messages": 0, "idarr": 0, "drive": "", "idarr_job": None, "warnings": []}
    signature = tuple((name, len(data), zlib.crc32(data)) for name, data in files)
    in_idarr = False
    if plan["idarr"]:
        index, result["drive"] = plan["drive"]
        with _lock:
            in_idarr = _idarr_sent.get(key) == signature
        if in_idarr:
            result["warnings"].append(f"They were already in IDarr ({result['drive']})")
        else:
            try:
                result["idarr"], result["idarr_job"], no_run = _add_to_idarr(db, index, files, part)
                in_idarr = True
                if no_run:
                    result["warnings"].append(f"The IDarr run did not start: {no_run.rstrip('.')}")
            except HTTPException as exc:
                if not plan["discord"]:
                    raise HTTPException(status_code=502, detail=f"IDarr add failed: {exc.detail}")
                result["warnings"].append(f"IDarr add failed: {str(exc.detail).rstrip('.')}")
    if plan["discord"]:
        try:
            result["messages"] = _post_files(login["token"], row["id"], files, part)
        except HTTPException as exc:
            if not in_idarr:
                raise
            with _lock:
                _idarr_sent[key] = signature
            raise HTTPException(status_code=exc.status_code, detail=f"{exc.detail}. The posters are in IDarr ({result['drive']})")
        result["posted"] = len(files)
    with _lock:
        _idarr_sent.pop(key, None)
    log_user_action("Uploaded posters for a community request", row=key, posted=result["posted"], idarr=result["idarr"])
    result["did"] = _did(result, label)
    return result


# ---- The Upload button: the newest export of each poster the request asks for, from the server's export folders ----

def _slot_label(slot: int | str | None) -> str:
    if slot is None:
        return "main"
    if slot == _EXPORT_COLLECTION:
        return "Collection"
    return "Specials" if slot == 0 else f"Season {slot}"


def _slot_order(slot: int | str | None) -> tuple:
    """Posting order: main, seasons ascending, Specials, Collection."""
    if slot is None:
        return (0, 0)
    if slot == _EXPORT_COLLECTION:
        return (3, 0)
    return (2, 0) if slot == 0 else (1, slot)


def _age(mtime: float, now: float | None = None) -> str:
    """Short age of a file, as the upload picker shows it."""
    seconds = max(0, round((time.time() if now is None else now) - mtime))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    if seconds < 7 * 86400:
        return f"{seconds // 86400}d ago"
    return datetime.fromtimestamp(mtime).strftime("%b %d, %Y").replace(" 0", " ")


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pick_exports(db: Session, row: dict) -> tuple[list[dict], int]:
    """What the upload picker would pre-select for this row, in posting order, and how many export
    folders are set. Blocking FS work."""
    media_type = str(row.get("media_type") or "")
    listing = list_request_exports(db, tmdb_id=_as_int(row.get("tmdb_id")), tvdb_id=_as_int(row.get("tvdb_id")),
                                   title=str(row.get("title") or ""), year=_as_int(row.get("year")), media_type=media_type,
                                   season_number=_as_int(row.get("season_number")), notes=row.get("notes"))
    picks = [dict(f, slot=_export_slot(Path(f["name"]).stem, media_type)) for f in listing["files"] if f["selected"]]
    return sorted(picks, key=lambda f: _slot_order(f["slot"])), len(listing["folders"])


def _purge(held: dict, ttl: float, now: float) -> None:
    for key in [k for k, v in held.items() if now - v["at"] > ttl]:
        del held[key]


@router.get("/upload")
def pick_upload(name: str = Query(...), db: Session = Depends(get_db)) -> dict:
    """What the Upload button would do for a PSD: the claim answer (as GET /claim with no kind), and
    when an upload can run, the picked exports as `files` [{name, poster, age}] plus `folders`, the
    number of export folders set. The pick is held for POST /{where}/{id}/upload."""
    answer, row = _find_claim(db, None, name)
    if row is None or answer["upload"]["problem"]:
        return answer
    picks, folders = _pick_exports(db, row)
    now = time.monotonic()
    with _lock:
        _purge(_picked, PICK_TTL, now)
        _picked[f"{answer['where']}/{row['id']}"] = {"at": now, "files": [(f["source"], f["name"]) for f in picks]}
    answer["folders"] = folders
    answer["files"] = [{"name": f["name"], "poster": _slot_label(f["slot"]), "age": _age(f["mtime"])} for f in picks]
    return answer


class ExportRef(BaseModel):
    source: str
    name: str


class UploadRequest(BaseModel):
    """What the web app sends with an upload from its picker: the exports ticked there, what its
    Upload and its IDarr drive are set to at that moment, its Discord login, and which part of the
    whole upload this is when it sends more than ten posters."""
    files: list[ExportRef] | None = None
    action: str | None = None
    drive: str | None = None
    token: str | None = None
    batch: str | None = None   # the whole upload's id, of the web app's making
    part: int | None = None
    of: int | None = None


class CompleteRequest(BaseModel):
    token: str | None = None   # the web app's Discord login


@router.post("/{where}/{row_id}/upload")
def upload_exports(where: str, row_id: str, body: UploadRequest | None = None, db: Session = Depends(get_db)) -> dict:
    """Upload exports from the server's export folders for this row: the ones the web app names, else
    (a panel's second press) what GET /upload picked."""
    key = _check(where, row_id)
    if body is not None and body.files is not None:
        refs = [(f.source, f.name) for f in body.files]
        if not refs or len(refs) > MAX_UPLOAD_FILES:
            raise HTTPException(status_code=400, detail="Choose between 1 and %d posters." % MAX_UPLOAD_FILES)
    else:
        with _lock:
            _purge(_picked, PICK_TTL, time.monotonic())
            entry = _picked.pop(key, None)
        if not entry or not entry["files"]:
            raise HTTPException(status_code=409, detail="Nothing is picked for this request. Press Upload again.")
        refs = entry["files"]
    sent = body or UploadRequest()
    part = _part(sent.batch, sent.part, sent.of)
    paths = [(name, _resolve_export_file(db, source, name)) for source, name in refs]
    if sum(path.stat().st_size for _, path in paths) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Too much for one upload. Send fewer posters at a time.")
    files = [(name, path.read_bytes()) for name, path in paths]
    return _upload(db, where, _read_row(where, row_id), files, sent.action, sent.drive, sent.token, part)


@router.post("/{where}/{row_id}/upload-files")
def upload_files(where: str, row_id: str, files: list[UploadFile] = File(...), action: str | None = Form(None),
                 drive: str | None = Form(None), token: str | None = Form(None), batch: str | None = Form(None),
                 part: int | None = Form(None), of: int | None = Form(None), db: Session = Depends(get_db)) -> dict:
    """Upload posters the web app sends from the maker's computer (a drop on the card, the file dialog)."""
    _check(where, row_id)
    part_of = _part(batch, part, of)
    if not files or len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=400, detail="Choose between 1 and %d posters." % MAX_UPLOAD_FILES)
    named: list[tuple[str, bytes]] = []
    total = 0
    for upload in files:
        name = Path(str(upload.filename or "")).name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="A file has no name.")
        data = upload.file.read()
        total += len(data)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Too much for one upload. Send fewer posters at a time.")
        named.append((name, data))
    return _upload(db, where, _read_row(where, row_id), named, action, drive, token, part_of)


# ---- A finish's own JPGs: sent one by one, then uploaded together ----

@router.put("/{where}/{row_id}/posters/{filename}")
async def stage_poster(where: str, row_id: str, filename: str, request: Request) -> dict:
    """Hold one exported poster for the row's next upload. A name sent twice keeps the later bytes."""
    key = _check(where, row_id)
    if PurePosixPath(filename).name != filename or "\\" in filename or PurePosixPath(filename).suffix.lower() not in _IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="The file must be a JPG, PNG or WebP image.")
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body.")
    if len(body) > MAX_BYTES:
        raise HTTPException(status_code=413, detail=f'"{filename}" is over the 8 MB Discord limit.')
    now = time.monotonic()
    with _lock:
        _purge(_staged, STAGE_TTL, now)
        entry = _staged.setdefault(key, {"at": now, "files": OrderedDict()})
        if filename not in entry["files"] and len(entry["files"]) >= MAX_STAGED:
            raise HTTPException(status_code=400, detail=f"More than {MAX_STAGED} posters for one request.")
        entry["at"] = now
        entry["files"][filename] = body
        return {"staged": len(entry["files"])}


@router.post("/{where}/{row_id}/posters")
def upload_staged(where: str, row_id: str, db: Session = Depends(get_db)) -> dict:
    """Upload the staged posters the row asks for, in the order they were sent. `skipped` names the
    staged posters it did not ask for; with none asked for, nothing is uploaded."""
    key = _check(where, row_id)
    with _lock:
        _purge(_staged, STAGE_TTL, time.monotonic())
        staged = bool(_staged.get(key, {}).get("files"))
    if not staged:
        raise HTTPException(status_code=400, detail="No posters were sent for this request.")
    row = _read_row(where, row_id)
    with _lock:
        entry = _staged.pop(key, None)
    files = list(entry["files"].items()) if entry else []
    media_type = str(row.get("media_type") or "")
    slots = _export_requested_slots(media_type, _as_int(row.get("season_number")), row.get("notes"))
    wanted = [(name, data) for name, data in files if _export_slot_requested(_export_slot(Path(name).stem, media_type), slots)]
    skipped = [name for name, _ in files if name not in {n for n, _ in wanted}]
    if not wanted:
        return {"posted": 0, "messages": 0, "idarr": 0, "drive": "", "warnings": [], "did": "", "skipped": skipped}
    return dict(_upload(db, where, row, wanted), skipped=skipped)


# ---- Complete ----

@router.post("/{where}/{row_id}/complete")
def complete(where: str, row_id: str, body: CompleteRequest | None = None, db: Session = Depends(get_db)) -> dict:
    """Mark the maker's claimed request or list item complete."""
    _check(where, row_id)
    function, id_field = ("update-request-status", "request_id") if where == "requests" else ("update-list-item", "item_id")
    login = _require_login(db, body.token if body else None)
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{SUPABASE_URL}/functions/v1/{function}", headers=SUPABASE_HEADERS,
                               json={"token": login["token"], id_field: row_id, "action": "complete"})
    except httpx.HTTPError as exc:
        log_error(LogTags.API, f"Community complete failed: {exc}")
        raise HTTPException(status_code=502, detail="Could not reach the community service.")
    if not resp.is_success:
        raise HTTPException(status_code=resp.status_code if 400 <= resp.status_code < 500 else 502, detail=_edge_error(resp))
    _cache_clear()   # the web app's request lists drop it on their next read
    log_user_action("Completed a community request", row=f"{where}/{row_id}")
    try:
        answer = resp.json()   # the row's new status and names: the web app updates its card from it
    except ValueError:
        answer = None
    return answer if isinstance(answer, dict) else {"ok": True}
