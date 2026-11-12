"""Standalone poster tools (Tools page): prune superseded Plex uploads, reset posters to the
servers' own artwork. Both cover every enabled movie/show library; reset also forgets the
overlay badge records of the items it touches."""

from __future__ import annotations

import json
import os
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from core.job_cancel import JobCancelled, check_cancelled
from core.logging import LogTags, log_debug, log_info, log_warning
from models.setting import get_setting_value
from services.overlays.base import ItemContext
from services.overlays.config import load_config
from services.overlays.server import ServerOverlayEngine
from services.plex_bundles import UPLOAD_FOLDERS, is_plex_metadata_root, plex_uploads_dir, prune_plex_uploads
from services.server_posters import stable_poster_key, upload_sha
from util.media_server.types import IMAGE_KIND_BACKGROUND, IMAGE_KIND_LOGO, IMAGE_KIND_POSTER, IMAGE_KIND_SQUAREART

IMAGE_KINDS = (IMAGE_KIND_POSTER, IMAGE_KIND_BACKGROUND, IMAGE_KIND_LOGO, IMAGE_KIND_SQUAREART)  # every kind Posterflow uploads, in UI order
JELLYFIN_IMAGE_KINDS = (IMAGE_KIND_POSTER, IMAGE_KIND_BACKGROUND, IMAGE_KIND_LOGO)  # Jellyfin has no square art
KIND_LABELS = {IMAGE_KIND_POSTER: "posters", IMAGE_KIND_BACKGROUND: "backgrounds", IMAGE_KIND_LOGO: "logos", IMAGE_KIND_SQUAREART: "square art"}
RESET_WORKERS = 4  # titles reset at the same time
RESET_BATCH = 24  # titles unlocked with one request, then handed to the workers together
WRITE_ACCESS_HINT = (f"Posterflow runs as uid {os.getuid()} (gid {os.getgid()}) and must be able to delete files in the Plex bundle folders: "
                      "run it with the Plex user's group (PGID), or add that group with EXTRA_GROUPS")


def _uploads_in(folder: Any) -> int:
    """How many uploads an upload folder holds; none when it is not there."""
    if folder is None:
        return 0
    try:
        with os.scandir(folder) as entries:
            return sum(1 for entry in entries if entry.is_file())
    except OSError:
        return 0


def kind_applies(client: Any, item: Any, kind: str) -> bool:
    """Seasons only carry posters; Jellyfin has no square art."""
    if getattr(item, "item_type", "") == "season":
        return kind == IMAGE_KIND_POSTER
    return not (kind == IMAGE_KIND_SQUAREART and getattr(client, "server_type", "") == "jellyfin")
from util.media_server.client import create_media_server_client
from util.media_server.instances import instance_type, server_type_label

ProgressCallback = Callable[[str, int, int, str], None]


def instance_names(db: Session, server_type: str) -> List[str]:
    """Names of the configured media servers of one type (the same list the engine uses)."""
    raw = get_setting_value(db, "plex_instances")
    try:
        parsed = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        return []
    out = []
    for inst in parsed if isinstance(parsed, list) else []:
        if not isinstance(inst, dict):
            continue
        name, url, key = (str(inst.get(k) or "").strip() for k in ("name", "url", "api_key"))
        if name and url and key and instance_type(inst) == server_type:
            out.append(name)
    return out


class PosterTools:
    """One instance per job. The overlay engine is only the keeper of badge records and of the
    Plex metadata paths here; the libraries covered are every enabled one."""

    def __init__(
        self,
        db: Session,
        *,
        instances: Optional[List[Dict[str, Any]]] = None,
        library_configs: Optional[List[Dict[str, Any]]] = None,
        client_factory: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> None:
        self.db = db
        self._client_factory = client_factory or (lambda inst: create_media_server_client(inst))
        self.engine = ServerOverlayEngine(db, load_config(db), instances=instances, library_configs=library_configs,
                                          client_factory=self._client_factory)
        self.stats: Dict[str, Any] = {"items": 0, "reset": 0, "failed": 0, "plex_pruned": 0, "plex_freed_bytes": 0,
                                      "instances": 0, "stopped": [], "skipped": [], "problems": {},  # per Plex server: stopped mid-run / skipped for its folder, and why
                                      "unwritable": 0,  # bundle folders the process may not delete in
                                      "shared": 0}  # items left alone: Plex could not say what another library's copy of the title shows
        self._warned: Set[Tuple[str, str]] = set()

    def instances_of(self, server_type: str) -> List[Dict[str, Any]]:
        return [inst for inst in self.engine.instances() if inst.get("type") == server_type]

    def enabled_libraries(self) -> Dict[str, List[Dict[str, Any]]]:
        """{instance name: [library dicts]} for every enabled movie/show library."""
        names = {inst["name"] for inst in self.engine.instances()}
        out: Dict[str, List[Dict[str, Any]]] = {}
        for cfg in self.engine.library_configs():
            name = str(cfg.get("instance_name") or "").strip()
            if name not in names:
                continue
            libs = [lib for lib in (cfg.get("libraries") or [])
                    if isinstance(lib, dict) and lib.get("enabled") and str(lib.get("type")) in ("movie", "show")]
            if libs:
                out[name] = libs
        return out

    def _warn_once(self, key: Tuple[str, str], message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log_warning(LogTags.TOOLS, message)

    def _problem(self, name: str, outcome: str, message: str) -> None:
        """An instance the run gave up on ('stopped' mid-run or 'skipped' outright), and why."""
        self.stats[outcome].append(name)
        self.stats["problems"][name] = message
        log_warning(LogTags.TOOLS, message)

    @staticmethod
    def _label(item: Any, instance_name: str, parent: Any) -> str:
        return ServerOverlayEngine._label(ItemContext.from_server_item(item, instance_name, parent))

    def _walk(self, progress: Optional[ProgressCallback], job_id: Optional[int], what: str,
              *, server_type: Optional[str], include_collections: bool = False,
              listed: Optional[List[Tuple[str, str, List[Any]]]] = None) -> Iterator[Tuple[Dict[str, Any], Any, Any, Any]]:
        """Yield (instance, client, item, parent) for every movie, show and season (and, when
        asked, collection) on the servers of one type (every server when None). `listed` gets
        (instance name, library key, everything it lists) per library before the first item."""
        instances = {inst["name"]: inst for inst in self.engine.instances()}
        work: List[Tuple[Dict[str, Any], Any, List[Any], Dict[str, Any]]] = []
        for name, libs in self.enabled_libraries().items():
            instance = instances[name]
            if server_type and instance.get("type") != server_type:
                continue
            client = self._client_factory(instance)
            if client is None:
                log_warning(LogTags.TOOLS, f"Cannot connect to {name} — skipping its libraries")
                continue
            client.instance_name = name
            for lib in libs:
                lib_key = str(lib.get("key"))
                try:
                    items = [i for i in client.get_library_items(lib_key) if i.item_type in ("movie", "show")]
                    shows = {i.item_id: i for i in items if i.item_type == "show"}
                    seasons = [s for s in (client.get_library_seasons(lib_key) if shows else []) if s.parent_id in shows]
                    list_collections = getattr(client, "get_collections", None) if include_collections else None
                    collections = [c for c in (list_collections(lib_key) if callable(list_collections) else []) if c.item_type == "collection"]
                except Exception as exc:
                    log_warning(LogTags.TOOLS, f"{name} library '{lib.get('title')}': could not list items: {exc}")
                    continue
                log_info(LogTags.TOOLS, f"{name} · {lib.get('title')}: {len(items)} item(s)" + (f", {len(seasons)} season(s)" if seasons else "")
                         + (f", {len(collections)} collection(s)" if collections else ""))
                work.append((instance, client, items + seasons + collections, shows))
                if listed is not None:
                    listed.append((name, lib_key, items + seasons + collections))
        total = sum(len(w[2]) for w in work)
        done = 0
        for instance, client, items, shows in work:
            for item in items:
                if job_id is not None:
                    check_cancelled(job_id)
                done += 1
                if progress:
                    progress(what, done, total, f"{what}: {item.title}")
                yield instance, client, item, shows.get(item.parent_id) if item.item_type == "season" else None

    def plex_cleanup(self, *, kinds: Optional[List[str]] = None, dry_run: bool = False, progress: Optional[ProgressCallback] = None,
                     job_id: Optional[int] = None) -> Dict[str, Any]:
        """Prune superseded uploads of the chosen kinds (every kind when None: posters,
        backgrounds, logos, square art) from every movie, show, season and collection bundle,
        keeping the image Plex shows."""
        kinds = [k for k in (kinds or IMAGE_KINDS) if k in IMAGE_KINDS]
        paths = dict(self.engine.plex_metadata_paths)
        if not paths:
            log_warning(LogTags.TOOLS, "No Plex metadata path is set — nothing to clean (Tools page → Plex Metadata folders)")
            return self.stats
        self.stats["instances"] = len(paths)
        for name, root in list(paths.items()):
            if not is_plex_metadata_root(root):
                self._problem(name, "skipped", f"{name}: {root} is not a Plex Metadata folder (no Movies or TV Shows folder inside) — its libraries were skipped")
                paths.pop(name)
        log_info(LogTags.TOOLS, f"Cleaning superseded {', '.join(KIND_LABELS[k] for k in kinds)} uploads from Plex bundles{' (dry run)' if dry_run else ''}")
        verified: Set[str] = set()  # servers where an item's bundle folder was found: the path is theirs
        for instance, client, item, parent in self._walk(progress, job_id, "Cleaning", server_type="plex", include_collections=True):
            name = instance["name"]
            root = paths.get(name)
            if not root:
                if name not in self.stats["skipped"]:
                    self._warn_once((name, "path"), f"{name}: no Plex metadata path set — its libraries were skipped")
                continue
            if name in self.stats["stopped"]:
                continue
            self.stats["items"] += 1
            label = self._label(item, name, parent)
            folder = plex_uploads_dir(root, item, parent, IMAGE_KIND_POSTER)
            bundle = next((p for p in folder.parents if p.name.endswith(".bundle")), None) if folder is not None else None
            if bundle is not None and bundle.is_dir():
                verified.add(name)
            removed, freed, pruned_kinds = 0, 0, []
            for kind in kinds:
                if not kind_applies(client, item, kind):
                    continue
                # With fewer than two uploads nothing in the folder can be superseded, so Plex is not
                # asked what it shows. Until a bundle of this server has been seen, posters are asked
                # about anyway: an upload Plex shows with no folder for it is how a wrong path shows.
                if _uploads_in(plex_uploads_dir(root, item, parent, kind)) < 2 and (kind != IMAGE_KIND_POSTER or name in verified):
                    continue
                if kind == IMAGE_KIND_POSTER:
                    selected = stable_poster_key(item)
                else:
                    lookup = getattr(client, "selected_image_key", None)
                    selected = lookup(item, kind) if callable(lookup) else ""
                result = prune_plex_uploads(root, item, parent, {upload_sha(selected)}, kind=kind, dry_run=dry_run, client=client)
                if result.shared:  # what a copy in another library shows is unknown: the bundle is left alone
                    self.stats["shared"] += 1
                    log_debug(LogTags.TOOLS, f"{label} | Plex could not say if a copy in another library shares its bundle, or what that copy shows — left alone")
                    break
                if result.error:  # a folder it may not write to is skipped; the rest of the server is still tried
                    self.stats["unwritable"] += 1
                    self._warn_once((name, "write"), f"{name}: {result.error} — {WRITE_ACCESS_HINT}; folders like it are skipped and counted")
                    continue
                if result.missing and kind == IMAGE_KIND_POSTER:
                    miss = f"no upload bundle found for {label} under the Plex metadata path ({root}) — check the folder on the Tools page"
                    if name in verified:
                        self._warn_once((name, "bundle"), f"{name}: {miss}; nothing was pruned for it")
                    else:  # not one bundle of this server has been seen under the path: it is the wrong folder
                        self._problem(name, "stopped", f"Stopping the cleanup for {name}: {miss}; nothing was pruned on {name}")
                    break
                if result.removed:
                    removed += result.removed
                    freed += result.freed
                    pruned_kinds.append(UPLOAD_FOLDERS[kind])
            if removed:
                self.stats["plex_pruned"] += removed
                self.stats["plex_freed_bytes"] += freed
                log_debug(LogTags.TOOLS, f"{label} | {'would prune' if dry_run else 'pruned'} {removed} superseded upload(s) ({freed / 1024 / 1024:.1f} MB; {', '.join(pruned_kinds)})")
        s = self.stats
        summary = (f"Plex cleanup{' (dry run)' if dry_run else ''}: {s['items']} item(s) checked, "
                   f"{'would remove' if dry_run else 'removed'} {s['plex_pruned']} superseded upload(s), {s['plex_freed_bytes'] / 1024 / 1024:.1f} MB")
        if s["stopped"]:
            summary += f"; stopped for {', '.join(s['stopped'])} (check the Metadata folder on the Tools page)"
        if s["skipped"]:
            summary += f"; {', '.join(s['skipped'])} skipped (not a Metadata folder)"
        if s["unwritable"]:
            summary += f"; {s['unwritable']} bundle folder(s) not writable by uid {os.getuid()}"
        if s["shared"]:
            summary += f"; {s['shared']} item(s) left alone: Plex could not say if a copy in another library shares their bundle, or what that copy shows"
        log_info(LogTags.TOOLS, summary)
        return self.stats

    def reset_posters(self, *, server_type: Optional[str] = None, kinds: Optional[List[str]] = None, dry_run: bool = False,
                      progress: Optional[ProgressCallback] = None, job_id: Optional[int] = None) -> Dict[str, Any]:
        """Put every item on the servers of one type (every server when None) back on the
        server's own images of the chosen kinds (every kind when None): badges and Posterflow
        uploads of those kinds are discarded. Titles go a batch at a time: one request unlocks
        the batch, then a few titles are reset at once, and each is reported when it is done. A
        run that is stopped finishes its batch and still forgets what it had reset by then."""
        kinds = [k for k in (kinds or IMAGE_KINDS) if k in IMAGE_KINDS]
        which = f"{server_type_label(server_type)} servers" if server_type else "the media servers"
        log_info(LogTags.TOOLS, f"Resetting {', '.join(KIND_LABELS[k] for k in kinds)} on {which} to their own defaults" + (" (dry run)" if dry_run else ""))
        forgotten: Dict[str, set] = {}  # kind → library keys where it was reset
        reports: deque = deque()  # each picked title's progress report, passed on once the title is done
        listed: List[Tuple[str, str, List[Any]]] = []
        walk = self._walk(lambda *report: reports.append(report), job_id, "Resetting", server_type=server_type, include_collections=True, listed=listed)
        try:
            with ThreadPoolExecutor(max_workers=RESET_WORKERS) as pool:
                while batch := list(islice(walk, RESET_BATCH)):
                    plans = [(entry, [k for k in kinds if kind_applies(entry[1], entry[2], k)]) for entry in batch]
                    self.stats["items"] += len(plans)
                    if dry_run:
                        self.stats["reset"] += sum(len(applicable) for _entry, applicable in plans)
                        if progress:
                            progress(*reports[-1])
                        reports.clear()
                        continue
                    locked = self._unlock(plans)
                    outcomes = pool.map(lambda plan: self._reset_one(plan, locked), plans)
                    stopped: Optional[JobCancelled] = None
                    for (entry, applicable), (done, error) in zip(plans, outcomes):
                        self._after_reset(entry, applicable, done, error, forgotten)
                        report = reports.popleft()
                        if progress and stopped is None:
                            try:
                                progress(*report)
                            except JobCancelled as exc:  # the batch is under way on the server: account for all of it first
                                stopped = exc
                    if stopped is not None:
                        raise stopped
        finally:
            if not dry_run:
                if IMAGE_KIND_POSTER in kinds:
                    # A badge record of an item the server no longer lists would never be reset.
                    # An empty listing says nothing about what is gone.
                    gone = sum(self.engine.forget_gone(name, lib_key, items, collections=True) for name, lib_key, items in listed if items)
                    if gone:
                        log_info(LogTags.TOOLS, f"Forgot {gone} badge record(s) of items the server no longer lists (removed, or added again under a new id)")
                self.engine.flush()
                from services.plex_upload import PlexUploadService

                uploader = PlexUploadService(self.db)
                touched = sum(uploader.forget_uploads(keys, [kind]) for kind, keys in forgotten.items())
                log_info(LogTags.TOOLS, f"Forgot {touched} upload record(s) for the reset libraries and kinds — the next Asset Upload run pushes those again, nothing else")
            s = self.stats
            log_info(LogTags.TOOLS, f"Reset: {s['reset']} image(s) back to the server default, {s['failed']} failed"
                                    + (f"; removed {s['plex_pruned']} Plex upload(s), freed {s['plex_freed_bytes'] / 1024 / 1024:.1f} MB" if s["plex_pruned"] else ""))
        return self.stats

    @staticmethod
    def _unlock(plans: List[Tuple[Tuple[Dict[str, Any], Any, Any, Any], List[str]]]) -> Set[Tuple[int, str]]:
        """Unlock a batch's images: one request per server for the titles that share their kinds
        (seasons only carry posters). Returns (client, item id) of the titles that stayed locked."""
        groups: Dict[Tuple[int, Tuple[str, ...]], Tuple[Any, List[Any]]] = {}
        for (_instance, client, item, _parent), applicable in plans:
            if applicable:
                groups.setdefault((id(client), tuple(applicable)), (client, []))[1].append(item)
        locked: Set[Tuple[int, str]] = set()
        for (client_id, applicable), (client, items) in groups.items():
            try:
                failed = client.unlock_images(items, list(applicable))
            except Exception as exc:
                log_debug(LogTags.TOOLS, f"Unlocking {len(items)} title(s) failed: {exc}")
                failed = {item.item_id for item in items}
            locked.update((client_id, item_id) for item_id in failed)
        return locked

    @staticmethod
    def _reset_one(plan: Tuple[Tuple[Dict[str, Any], Any, Any, Any], List[str]], locked: Set[Tuple[int, str]]) -> Tuple[Dict[str, bool], str]:
        """One title's reset, on a worker: ({kind: done}, why it failed). Only talks to the server."""
        (_instance, client, item, _parent), applicable = plan
        if not applicable:
            return {}, ""
        if (id(client), item.item_id) in locked:
            return {}, "its images could not be unlocked"
        try:
            return client.reset_images(item, applicable, unlocked=True), ""
        except Exception as exc:
            return {}, str(exc)

    def _after_reset(self, entry: Tuple[Dict[str, Any], Any, Any, Any], applicable: List[str], done: Dict[str, bool], error: str,
                     forgotten: Dict[str, set]) -> None:
        """Count and log one title's reset, tidy its Plex bundle and drop its badge record."""
        instance, client, item, parent = entry
        name = instance["name"]
        label = self._label(item, name, parent)
        root = self.engine.plex_metadata_paths.get(name) if instance.get("type") == "plex" else None
        if error:
            log_debug(LogTags.TOOLS, f"{label}: reset failed: {error}")
        for kind in applicable:
            if not done.get(kind):
                self.stats["failed"] += 1
                log_warning(LogTags.TOOLS, f"{name}: could not reset the {KIND_LABELS[kind]} of {label}")
                continue
            self.stats["reset"] += 1
            log_debug(LogTags.TOOLS, f"{label} → server default | {KIND_LABELS[kind]} reset")
            forgotten.setdefault(kind, set()).add(str(item.library_key or ""))
            if root:  # a bundle another library's copy uses keeps what that copy shows
                result = prune_plex_uploads(root, item, parent, None, kind=kind, client=client)
                self.stats["shared"] += int(result.shared)
                self.stats["plex_pruned"] += result.removed
                self.stats["plex_freed_bytes"] += result.freed
                if result.error:
                    self._warn_once((name, "prune"), f"{name}: {result.error} — {WRITE_ACCESS_HINT}; its old uploads stay in the Plex bundles")
            if kind == IMAGE_KIND_POSTER:
                self.engine.forget(name, item.item_id)

    def clean_jellyfin_image_cache(self) -> Dict[str, Any]:
        """Ask every Jellyfin server to run its own cache clean: replaced posters leave resized
        copies in the image cache, and the server's task removes the stale ones."""
        self.stats.update({"asked": 0, "failed": 0})
        for instance in self.instances_of("jellyfin"):
            name = instance["name"]
            client = self._client_factory(instance)
            try:
                ok = client is not None and client.run_scheduled_task("DeleteCacheFiles")
            except Exception as exc:
                ok = False
                log_debug(LogTags.TOOLS, f"{name}: cache clean request failed: {exc}")
            if ok:
                self.stats["asked"] += 1
                log_info(LogTags.TOOLS, f"{name}: asked Jellyfin to clean its image cache")
            else:
                self.stats["failed"] += 1
                log_warning(LogTags.TOOLS, f"{name}: could not start Jellyfin's cache clean (is the server reachable?)")
        return self.stats
