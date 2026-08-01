"""Batch Artwork Pull — fetch missing logos / backgrounds / square art for a set of items into an
IDarr artwork scope, then (optionally) queue an IDarr run to upload them. Mirrors the structure of
modules/idarr.py::run_idarr_background_job.."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

from core.job_cancel import JobCancelled, check_cancelled
from core.job_queue import job_queue
from core.logging import (
    LogTags, add_job_log_handler, log_error, log_info, log_section_end, log_section_start,
    log_warning, remove_job_log_handler,
)
from database import SessionLocal
from models.drive import Drive
from models.job import (
    JOB_STATUS_COMPLETED, JOB_STATUS_RUNNING, JOB_TYPE_IDARR, Job, create_job,
    finalize_job_cancelled, mark_job_failed, update_job_state,
)
from models.setting import get_setting
from services import artwork_finder as af
from util.data.extract import extract_ids, extract_year
from util.data.normalization import normalize_titles
from util.posters.scanner import process_files

TAG = LogTags.ARTWORK_PULL
_ALL_TYPES = ["logo", "background", "squareart"]
_SCANNER_TYPE = {"movies": "movie", "series": "tv", "collections": "collection"}
_IDARR_TYPE = {"movie": "movie", "tv_series": "tv", "collection": "collection"}


# ------------------------------------------------------------------ item sources


def _finder_from_scanner(d: dict) -> Optional[af.FinderItem]:
    title = str(d.get("title") or "").strip()
    if not title:
        return None
    return af.FinderItem(title=title, year=d.get("year") if isinstance(d.get("year"), int) else None,
                         tmdb_id=d.get("tmdb_id"), tvdb_id=d.get("tvdb_id"), imdb_id=d.get("imdb_id"),
                         media_type=_SCANNER_TYPE.get(str(d.get("type") or ""), "movie"))


def _finder_from_idarr(parsed: dict) -> Optional[af.FinderItem]:
    title = str(parsed.get("title") or "").strip()
    if not title:
        return None
    return af.FinderItem(title=title, year=parsed.get("year") if isinstance(parsed.get("year"), int) else None,
                         tmdb_id=parsed.get("tmdb_id"), tvdb_id=parsed.get("tvdb_id"), imdb_id=parsed.get("imdb_id"),
                         media_type=_IDARR_TYPE.get(str(parsed.get("type") or "").lower(), "movie"))


def _dedup_key(fi: af.FinderItem) -> str:
    if fi.tmdb_id:
        return f"{fi.media_type}|tmdb-{fi.tmdb_id}"
    if fi.tvdb_id:
        return f"tvdb-{fi.tvdb_id}"
    if fi.imdb_id:
        return f"imdb-{fi.imdb_id}"
    return f"t|{fi.title.lower()}|{fi.year}"


def _items_from_poster_drives(db, drive_ids) -> list[af.FinderItem]:
    q = db.query(Drive).filter(Drive.subscribed == True, Drive.is_deprecated == False)  # noqa: E712
    if drive_ids:
        q = q.filter(Drive.id.in_([int(x) for x in drive_ids]))
    out: dict[str, af.FinderItem] = {}
    for d in q.all():
        try:
            path = d.get_local_path(validate=False)
        except Exception:
            continue
        if not path.is_dir():
            continue
        for it in (process_files(str(path)) or []):
            fi = _finder_from_scanner(it)
            if fi:
                out.setdefault(_dedup_key(fi), fi)
    return list(out.values())


def _add_scope_item(out: dict[str, af.FinderItem], fi: af.FinderItem) -> None:
    """Index a scope file's item, keeping BOTH sides of an id clash instead of dropping one.

    A scope item legitimately spans the three folders under one name, so a repeat key is normally
    the same item. A repeat key under a *different* title is not — it's two titles wearing one id,
    which happens when a show's file carries only a {tmdb-…}: the number is namespaced by TMDB, so
    tv 79063 ("Cunk on...") and movie 79063 ("The Unkabogable Praybeyt Benjamin") collide once the
    filename's missing tvdb tag makes both look like movies. setdefault silently ate one of them."""
    key = _dedup_key(fi)
    kept = out.get(key)
    if kept is None:
        out[key] = fi
    elif normalize_titles(kept.title) != normalize_titles(fi.title):
        out.setdefault(f"{key}|{normalize_titles(fi.title)}", fi)


def _namespace_is_guessed(item: af.FinderItem, source: str) -> bool:
    """True when a scope item's movie/tv namespace came from the filename shape, not from an id.

    Asset filenames prove the type only with a {tvdb-…} tag (series) or a "Collection" in the name;
    everything else defaults to movie. A bare {tmdb-…} proves nothing on its own — the same number
    is a different title in each TMDB namespace — so these have to be verified before the pull, or
    it writes one title's artwork under another's name. Files carrying an {imdb-tt…} are left
    trusted: an IMDb id is namespace-specific, and IDarr wrote it alongside the tmdb id."""
    return (source == "scope" and item.media_type == "movie"
            and bool(item.tmdb_id) and not item.tvdb_id and not item.imdb_id)


def _items_from_scope(source_dir: Path, identity: Optional[dict] = None) -> list[af.FinderItem]:
    from services.idarr_runner import IMAGE_EXTENSIONS
    out: dict[str, af.FinderItem] = {}
    for sub in ("logos", "backgrounds", "squareart"):
        folder = source_dir / sub
        if not folder.is_dir():
            continue
        for f in folder.iterdir():
            if not f.is_file() or f.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            try:
                parsed = af._parse_with_identity(f, identity)
            except Exception:
                continue
            fi = _finder_from_idarr(parsed)
            if fi:
                _add_scope_item(out, fi)
    return list(out.values())


def _items_from_list(paste: str) -> list[af.FinderItem]:
    out: dict[str, af.FinderItem] = {}
    for line in (paste or "").splitlines():
        line = line.strip()
        if not line:
            continue
        tmdb, tvdb, _imdb = extract_ids(line)           # imdb ids are not supported for pasted lists
        year = extract_year(line)
        title = re.sub(r"\{[^}]*\}", "", line)          # strip {tmdb-…} etc.
        title = re.sub(r"\(\d{4}\)", "", title).strip(" -")
        # A "… Collection" line is a COLLECTION (its {tmdb-…} is a collection id, not a movie id);
        # TMDB collections yield backgrounds only.
        is_collection = bool(re.search(r"\bcollections?\b", line, re.IGNORECASE))
        # Require specificity: a title + an id, plus a year for movies/shows (collections have none).
        # Bare ids and bare titles are skipped so nothing is guessed.
        if not title or not (tmdb or tvdb) or (not is_collection and not year):
            continue
        fi = af.FinderItem(title=title, year=year, tmdb_id=tmdb, tvdb_id=tvdb, imdb_id=None,
                           media_type="collection" if is_collection else "movie")
        out.setdefault(_dedup_key(fi), fi)
    return list(out.values())


def _items_from_libraries(db) -> list[af.FinderItem]:
    from services.poster_renamer import PosterRenameService
    media = PosterRenameService(db).get_media_from_instances(log_tag=TAG, setting_key="asset_renamer_libraries")
    out: dict[str, af.FinderItem] = {}
    for group, mt in (("movies", "movie"), ("series", "tv"), ("collections", "collection")):
        for m in (media.get(group) or []):
            title = str(m.get("title") or "").strip()
            if not title:
                continue
            fi = af.FinderItem(title=title, year=m.get("year") if isinstance(m.get("year"), int) else None,
                               tmdb_id=m.get("tmdb_id"), tvdb_id=m.get("tvdb_id"), imdb_id=m.get("imdb_id"),
                               media_type=mt, status=m.get("status"))
            out.setdefault(_dedup_key(fi), fi)
    return list(out.values())


def _resolve_items(db, source: str, config_data: dict, identity: Optional[dict] = None) -> list[af.FinderItem]:
    if source == "poster_drives":
        return _items_from_poster_drives(db, config_data.get("drive_ids"))
    if source == "scope":
        return _items_from_scope(Path(str(config_data.get("source_dir") or "")), identity)
    if source == "list":
        return _items_from_list(str(config_data.get("paste") or ""))
    if source == "libraries":
        return _items_from_libraries(db)
    return []


def _queue_idarr_for_scope(db, index: int, tmdb_key: str) -> None:
    """After a pull, queue a standard IDarr run over the scope so the new files upload to Drive
    (rename is a no-op since they're already convention-named)."""
    from api.idarr import SETTING_MAKER_IDARR_CONFIG
    from models.idarr import resolve_idarr_scope_token
    from modules.idarr import run_idarr_background_job

    setting = get_setting(db, SETTING_MAKER_IDARR_CONFIG)
    if not setting or not setting.value:
        return
    try:
        config = json.loads(setting.value)
    except (ValueError, TypeError):
        return
    targets = [t for t in (config.get("sync_targets") or []) if isinstance(t, dict)]
    if index < 0 or index >= len(targets):
        return
    target = targets[index]
    config.update({
        "dry_run": False,
        "source_dir": str(target.get("source_dir") or ""),
        "sync_target_index": index,
        "scope_token": resolve_idarr_scope_token(target, index),
        "is_asset_drive": bool(target.get("is_asset_drive")),
        "sync_after_run": True,
        "tmdb_api_key": tmdb_key,
    })
    new_job = create_job(db, JOB_TYPE_IDARR, "IDarr run after Artwork Pull")
    job_queue.submit(run_idarr_background_job, new_job.id, new_job.id, config)
    log_info(TAG, f"Queued IDarr run for the scope to rename + upload (job {new_job.id})")


# ------------------------------------------------------------------ job


def run_artwork_pull_background_job(job_id: int, config_data: dict[str, Any]) -> None:
    db = SessionLocal()
    handler_id = add_job_log_handler("artwork_pull", job_id, "Artwork Pull")
    success = False
    try:
        log_section_start(TAG, f"Artwork Pull Started (job_id={job_id})")
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log_error(TAG, f"Job {job_id} not found")
            return
        update_job_state(db, job, status=JOB_STATUS_RUNNING, progress=1, message="Starting artwork pull...")

        source_dir = Path(str(config_data.get("source_dir") or ""))
        is_asset_drive = bool(config_data.get("is_asset_drive", True))
        tmdb_key = str(config_data.get("tmdb_api_key") or "").strip()
        if not tmdb_key:
            s = get_setting(db, "tmdb_api_key")
            tmdb_key = str((s.value if s else "") or "").strip()
        types = [t for t in (config_data.get("types") or _ALL_TYPES) if t in af.SUBTYPE_EXT]
        min_w = int(config_data.get("min_backdrop_width") or 1920)
        make_white = bool(config_data.get("make_white_logos"))
        skip_unreleased = bool(config_data.get("skip_unreleased"))
        force_refetch = bool(config_data.get("force_refetch"))
        dry_run = bool(config_data.get("dry_run"))
        source = str(config_data.get("source") or "poster_drives")

        if not tmdb_key:
            raise RuntimeError("TMDB API key is not configured.")
        if not str(source_dir) or not is_asset_drive:
            raise RuntimeError("Invalid artwork scope (needs an IDarr asset scope).")

        update_job_state(db, job, progress=3, message="Collecting items...")
        # IDarr's cached per-filename identity corrects what asset names can't express (a tmdb id's
        # namespace); both the item list and the already-have index have to read the same one.
        identity = af.build_scope_identity_index(db, int(config_data.get("sync_target_index") or 0), source_dir)
        items = _resolve_items(db, source, config_data, identity)
        log_info(TAG, f"Source '{source}': {len(items)} unique items; types={types}; dry_run={dry_run}"
                      f"{'; FORCE re-fetch (overwriting existing)' if force_refetch else ''}")
        update_job_state(db, job, progress=5, message=f"{len(items)} items to check")

        session = requests.Session()
        token = af.get_plex_token(db)
        plex = af.PlexMetadataProvider(token, session) if token else None
        # Id-based already-have index (not exact filenames): a source whose metadata carries a
        # different imdb/tmdb than the file on disk must still count as "have".
        scope_index = af.build_scope_artwork_index(source_dir, identity)
        if "squareart" in types and plex is None:
            log_warning(TAG, "No Plex token — square art will be skipped (Plex is its only source).")

        saved = {t: 0 for t in _ALL_TYPES}
        complete = 0
        misses = {t: 0 for t in _ALL_TYPES}
        unresolved = 0
        skipped_unreleased = 0
        total = len(items)

        known_identity = source != "list"   # pasted lists carry no trusted type; resolve it
        for n, item in enumerate(items, 1):
            check_cancelled(job_id)
            if skip_unreleased and item.is_unreleased:
                skipped_unreleased += 1     # before resolve: no TMDB call spent on future items
            elif af.resolve_tmdb_identity(
                    item, tmdb_key,
                    known_identity=known_identity and not _namespace_is_guessed(item, source)) is None:
                unresolved += 1
            else:
                # force_refetch ignores the already-have index: everything is re-pulled and existing
                # files are archived to duplicates on overwrite.
                wanted = list(types) if force_refetch else \
                    [t for t in types if not af.scope_has_artwork(scope_index, item, t)]
                if item.is_collection:
                    wanted = [t for t in wanted if t == "background"]   # collections: backgrounds only
                if not wanted:
                    complete += 1
                else:
                    # Name new files with TMDB's canonical title — arr titles are unidecode-degraded
                    # (WALL·E arrives as WALLE), which would bake the wrong name into the filename.
                    af.canonicalize_title(item, tmdb_key)
                    result = af.auto_pick_missing(item, wanted, tmdb_api_key=tmdb_key, plex=plex, session=session,
                                                  min_backdrop_width=min_w, make_white_logos=make_white)
                    for subtype, pick in result["picks"].items():
                        if dry_run:
                            saved[subtype] += 1
                            log_info(TAG, f"WOULD add {subtype}: {item.display}")
                            continue
                        try:
                            res = af.save_candidate(source_dir=source_dir, is_asset_drive=is_asset_drive, item=item,
                                                    subtype=subtype, source=pick["source"], ref=pick["ref"],
                                                    session=session, make_white=pick["make_white"], confirm_overwrite=True)
                            if res["status"] == "added":
                                saved[subtype] += 1
                                log_info(TAG, f"Added {subtype}: {res['written']}")
                        except ValueError as exc:
                            log_warning(TAG, f"{item.display}: {subtype} failed: {exc}")
                    for subtype, reason in result["misses"].items():
                        misses[subtype] += 1
                        # An unreleased title having no art yet is expected — name the real cause,
                        # matching how the renamer-side stats classify these items.
                        log_info(TAG, f"{item.display}: no {subtype} ({'unreleased' if item.is_unreleased else reason})")

            if total and (n % 10 == 0 or n == total):
                update_job_state(db, job, progress=5 + int(90 * n / total),
                                 message=f"[{n}/{total}] logo={saved['logo']} bg={saved['background']} square={saved['squareart']}")

        summary = (f"Artwork Pull {'(dry run) ' if dry_run else ''}done — "
                   f"logos {saved['logo']}, backgrounds {saved['background']}, square art {saved['squareart']}"
                   f" ({complete} already complete, {unresolved} unresolved"
                   f"{f', {skipped_unreleased} unreleased skipped' if skipped_unreleased else ''})")
        log_info(TAG, summary)
        for t in _ALL_TYPES:
            if misses[t]:
                log_info(TAG, f"  {t}: {misses[t]} with none usable")

        pulled_any = saved["logo"] or saved["background"] or saved["squareart"]
        if config_data.get("sync_after_run") and not dry_run and pulled_any:
            _queue_idarr_for_scope(db, int(config_data.get("sync_target_index") or 0), tmdb_key)

        update_job_state(db, job, status=JOB_STATUS_COMPLETED, progress=100, message=summary,
                         completed_at=datetime.now(timezone.utc))
        success = True
    except JobCancelled:
        db.rollback()
        finalize_job_cancelled(db, job_id)
        log_section_end(TAG, "Artwork Pull cancelled")
    except Exception as exc:
        log_error(TAG, f"Artwork Pull failed: {exc}")
        mark_job_failed(db, job_id, exc)
    finally:
        remove_job_log_handler(handler_id, job_type="artwork_pull", success=success)
        db.close()
