"""Overlays: status and rating badges on the media servers' posters, composed from
Posterflow's own poster files.

The destination folder holds the clean poster Posterflow placed for each item (what the
renamer and border replacer produced) and the uploader pushes it to the servers. Overlays
badge that file, never the server's copy:
- at upload time the uploader asks the engine for the composite and pushes it instead
  (one upload, nothing downloaded);
- a run re-composes the items whose badge changed (a show's status moved on, settings were
  edited) from the same file and uploads;
- removing overlays uploads the clean file again.
Items without a Posterflow poster are left alone. State per item: the badge signature, the
source file and its hash, and the hash of the composite on the server."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import threading
from contextlib import nullcontext
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from PIL import Image
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.job_cancel import check_cancelled
from core.logging import LogTags, log_debug, log_info, log_warning
from models.overlay import OverlayItem
from models.plex_upload import PlexUploadRecord
from models.setting import get_setting_value
from services.plex_bundles import is_plex_metadata_root, load_plex_metadata_paths, prune_plex_uploads, upload_gone
from services.server_posters import PLEX_NO_POSTER, poster_key, stable_poster_key, upload_sha
from util.library_configs import media_libraries_only
from util.media_server.client import create_media_server_client
from util.media_server.instances import instance_type

from .base import ItemContext, OverlayBadge, OverlayType
from .config import SETTING_TMDB_API_KEY, overlay_signature
from .finales import FinaleLookup
from .metadata import MetadataProvider, describe_record
from .registry import all_types
from .render import join_lines, render_badges, stand_in_mark

ProgressCallback = Callable[[str, int, int, str], None]
_STATE_LOCK = threading.Lock()  # process-wide: two runs may flush at once (workflow + webhook)
_ARTWORK_WORDS = ("logo", "background", "backdrop", "square")  # upload records that are not posters
_SEASON_FILE = re.compile(r"^season\s*0*(\d+)$", re.I)  # SeasonNN.jpg beside the show's poster.jpg
# A badge composed on the media server's own poster (no Posterflow file) records its source as
# this prefix plus the server's reference to that poster. The image itself is never kept.
_SERVER_SOURCE = "server:"
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


class ServerOverlayEngine:
    """One engine per run or per upload job. `run()` covers the selected libraries;
    `compose_for_upload()` / `record_upload()` wrap one upload. Call `flush()` when done."""

    def __init__(
        self,
        db: Session,
        config: Dict[str, Any],
        *,
        metadata: Optional[MetadataProvider] = None,
        instances: Optional[List[Dict[str, Any]]] = None,
        library_configs: Optional[List[Dict[str, Any]]] = None,
        client_factory: Optional[Callable[[Dict[str, Any]], Any]] = None,
        sources: Optional[Dict[Tuple[str, str], str]] = None,
    ) -> None:
        self.db = db
        self.config = config
        self.active: List[Tuple[OverlayType, Dict[str, Any]]] = [
            (otype, config["types"][key])
            for key, otype in all_types().items()
            if config["types"].get(key, {}).get("enabled")
        ]
        needs = [otype.needs_for(cfg["options"]) for otype, tcfg in self.active for cfg in self._type_configs(tcfg)]
        self._needs_tmdb = any("tmdb" in n for n in needs)
        self._needs_media = any("media" in n for n in needs)  # resolution / audio badges read the items' media files
        self._media: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}  # (instance, item id) → its media files, once looked up
        self.metadata = metadata or MetadataProvider(get_setting_value(db, SETTING_TMDB_API_KEY, "") if self._needs_tmdb else "", db=db,
                                                     finales=FinaleLookup.from_db(db) if self._needs_tmdb else None,
                                                     providers=any("providers" in n for n in needs))
        self._instances = instances
        self._library_configs = library_configs
        self._client_factory = client_factory or (lambda inst: create_media_server_client(inst))
        self._sources = sources  # tests: {(instance name, item id): poster file}; else the upload records
        self.server_sources = bool(config.get("badge_server_posters"))  # badge items without a Posterflow file too
        self.prune_plex = bool(config.get("prune_plex_uploads", True))  # tidy Plex bundles after each badge
        self._selected: Optional[Dict[str, List[Dict[str, Any]]]] = None
        self._source_index: Optional[Dict[Tuple[str, str], str]] = None
        self._sha_cache: Dict[str, Tuple[float, int, str]] = {}
        self._pending: Dict[str, Optional[Dict[str, Any]]] = {}  # uploads in flight, by item key
        self.off_turn: Callable[[], Any] = nullcontext  # the uploader's: lets its other uploads run meanwhile
        self._lock = threading.Lock()
        self._state = self._load_state()
        self._upserts: Dict[str, Dict[str, Any]] = {}
        self._deletes: Set[str] = set()
        self._overlay_hashes: Dict[int, str] = {}  # id(overlay definition) → overlay_signature, computed once
        self.stats: Dict[str, Any] = {
            "applied": 0, "refreshed": 0, "restored": 0, "current": 0,
            "no_badge": 0, "no_data": 0, "no_poster": 0, "failed": 0, "by_state": {}, "uploads_by_instance": {},
            "server_sourced": 0,
            "plex_pruned": 0, "plex_freed_bytes": 0, "plex_shared": 0, "reuploaded": 0,
        }
        self._plex_path_warned: Set[str] = set()
        self.plex_metadata_paths: Dict[str, str] = load_plex_metadata_paths(db)
        self._plex_prune_roots: Dict[str, Optional[str]] = {}  # per server: the Metadata folder to prune under, None = leave bundles alone this run
        self._no_data_examples: List[str] = []
        self._summary_logged = False
        self.lost_on_server: Set[str] = set()  # items whose badged poster the server no longer shows this run

    # ── configuration ──────────────────────────────────────────────────────
    def instances(self) -> List[Dict[str, Any]]:
        if self._instances is not None:
            return self._instances
        raw = get_setting_value(self.db, "plex_instances")
        try:
            parsed = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            return []
        out = []
        for inst in parsed if isinstance(parsed, list) else []:
            if not isinstance(inst, dict):
                continue
            name, url, key = (str(inst.get(k) or "").strip() for k in ("name", "url", "api_key"))
            if name and url and key:
                out.append({"name": name, "url": url, "api_key": key, "type": instance_type(inst)})
        return out

    def library_configs(self) -> List[Dict[str, Any]]:
        if self._library_configs is not None:
            return self._library_configs
        raw = get_setting_value(self.db, "plex_library_config")
        try:
            parsed = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            return []
        return media_libraries_only(parsed) if isinstance(parsed, list) else []

    def selected_libraries(self) -> Dict[str, List[Dict[str, Any]]]:
        """{instance name: [library dicts]} — every enabled movie/show library when
        `all_libraries` is on, otherwise only the config's `libraries` ("instance:key")."""
        all_libraries = bool(self.config.get("all_libraries", True))
        wanted = set(self.config.get("libraries") or [])
        names = {inst["name"] for inst in self.instances()}
        out: Dict[str, List[Dict[str, Any]]] = {}
        for cfg in self.library_configs():
            name = str(cfg.get("instance_name") or "").strip()
            if name not in names:
                continue
            libs = []
            for lib in cfg.get("libraries") or []:
                if not isinstance(lib, dict) or not lib.get("enabled"):
                    continue
                if str(lib.get("type")) not in ("movie", "show"):
                    continue
                if all_libraries or f"{name}:{lib.get('key')}" in wanted:
                    libs.append(lib)
            if libs:
                out[name] = libs
        return out

    # ── logging helpers ────────────────────────────────────────────────────
    @staticmethod
    def _label(ctx: ItemContext) -> str:
        base = f"{ctx.title} ({ctx.year})" if ctx.year else ctx.title
        if ctx.is_season:
            base += " · Specials" if ctx.season_number == 0 else f" · Season {ctx.season_number}"
        return base

    @staticmethod
    def _texts(sig: str) -> str:
        parts = [p.split("|")[1] for p in sig.split("\n") if "|" in p]
        return ", ".join(parts) or "—"

    @staticmethod
    def _log_item(message: str) -> None:
        """Per-item outcomes are debug: INFO carries the run's shape and totals only."""
        log_debug(LogTags.OVERLAYS, message)

    _COUNTED = ("applied", "refreshed", "restored", "current", "no_badge", "no_data", "no_poster", "failed")

    def _snapshot(self) -> Dict[str, int]:
        return {k: self.stats[k] for k in self._COUNTED}

    def _delta_text(self, before: Dict[str, int]) -> str:
        parts = [f"{self.stats[k] - before[k]} {k.replace('_', ' ')}" for k in self._COUNTED if self.stats[k] - before[k]]
        return ", ".join(parts) or "nothing to do"

    def log_summary(self) -> None:
        if self._summary_logged:
            return
        self._summary_logged = True
        s = self.stats
        self.metadata.log_stats()
        if not any(s[k] for k in self._COUNTED):
            return
        log_info(
            LogTags.OVERLAYS,
            f"Overlays: {s['applied']} applied, {s['refreshed']} refreshed, {s['restored']} restored to the clean poster, "
            f"{s['current']} already current; {s['no_badge']} item(s) with no badge by rule, {s['no_data']} without TMDB data, "
            f"{s['no_poster']} without a {'poster to badge' if self.server_sources else 'Posterflow poster'}, {s['failed']} failed",
        )
        if s["server_sourced"]:
            log_info(LogTags.OVERLAYS, f"Badged on the media server's own poster (no Posterflow poster): {s['server_sourced']} item(s)")
        if s["by_state"]:
            by_state = ", ".join(f"{k} {v}" for k, v in sorted(s["by_state"].items(), key=lambda kv: -kv[1]))
            log_info(LogTags.OVERLAYS, f"Badges on the server after this run: {by_state}")
        if s["plex_pruned"]:
            log_info(LogTags.OVERLAYS, f"Plex bundles: removed {s['plex_pruned']} superseded upload(s), freed {s['plex_freed_bytes'] / 1024 / 1024:.1f} MB")
        if s["plex_shared"]:
            log_info(LogTags.OVERLAYS, f"Plex bundles: {s['plex_shared']} item(s) left as they are — Plex could not say if a copy of the title in another library "
                                       f"shares the bundle, or what that copy shows")
        if s["reuploaded"]:
            log_info(LogTags.OVERLAYS, f"Uploaded {s['reuploaded']} badge(s) again that Plex showed no poster for (none selected, or the file gone from its bundle)")
        if self._no_data_examples:
            more = s["no_data"] - len(self._no_data_examples)
            log_warning(
                LogTags.OVERLAYS,
                "No TMDB data for: " + ", ".join(self._no_data_examples) + (f" (+{more} more)" if more > 0 else "")
                + " — the server item has no usable ids, or the TMDB key is missing",
            )

    # ── state ──────────────────────────────────────────────────────────────
    def _load_state(self) -> Dict[str, Any]:
        """Every badged item, keyed "<instance>|<item id>"."""
        try:
            items = {row.key: row.to_entry() for row in self.db.query(OverlayItem)}
        except SQLAlchemyError as exc:
            self.db.rollback()
            log_warning(LogTags.OVERLAYS, f"Could not read the overlay state: {exc}")
            items = {}
        return {"items": items}

    def badged_count(self) -> int:
        return len(self._state.get("items") or {})

    def forget(self, instance_name: str, item_id: str) -> None:
        """Drop an item's badge record (its poster was reset by a tool)."""
        key = f"{instance_name}|{item_id}"
        if key in self._state["items"]:
            self._drop_entry(key)

    def forget_gone(self, instance_name: str, lib_key: str, items: List[Any], *, collections: bool = False) -> int:
        """Drop the badge records of items no longer in a library, given everything it lists
        now: deleted on the server, or added again under a new id (a renamed folder does that).
        Collection records only when the listing holds the collections too."""
        seen = {f"{instance_name}|{item.item_id}" for item in items}
        libraries = {lib_key} | {self._library_of(item, lib_key) for item in items}
        gone = [k for k, e in list(self._state["items"].items())
                if e.get("instance") == instance_name and e.get("library") in libraries and k not in seen
                and (collections or e.get("item_type") != "collection")]
        for k in gone:
            self._drop_entry(k)
        return len(gone)

    def tracks(self, instance_name: str, item_id: Any) -> bool:
        """Does a badge record exist for this item? Then the engine verifies its server poster
        itself (see badge_stale) and the uploader need not."""
        return f"{instance_name}|{item_id}" in self._state["items"]

    def _set_entry(self, key: str, entry: Dict[str, Any]) -> None:
        with self._lock:
            self._state["items"][key] = entry
            self._upserts[key] = entry
            self._deletes.discard(key)

    def _drop_entry(self, key: str) -> None:
        with self._lock:
            self._state["items"].pop(key, None)
            self._upserts.pop(key, None)
            self._deletes.add(key)

    def _commit_state(self) -> None:
        """Write the items this engine touched to the overlay_items table: only those, so a
        webhook upload finishing mid-workflow can't wipe the other's work."""
        with _STATE_LOCK, self._lock:
            if not self._upserts and not self._deletes:
                return
            try:
                for key in self._deletes:
                    name, _, item_id = key.partition("|")
                    self.db.query(OverlayItem).filter_by(instance=name, item_id=item_id).delete()
                for key, entry in self._upserts.items():
                    name, _, item_id = key.partition("|")
                    row = self.db.query(OverlayItem).filter_by(instance=name, item_id=item_id).one_or_none()
                    if row is None:
                        row = OverlayItem(instance=name, item_id=item_id)
                        self.db.add(row)
                    row.apply(entry)
                self.db.commit()
            except SQLAlchemyError as exc:
                self.db.rollback()
                log_warning(LogTags.OVERLAYS, f"Could not write the overlay state: {exc}")
            self._upserts, self._deletes = {}, set()  # the in-memory state already carries them

    def flush(self) -> None:
        self.metadata.flush()
        self._commit_state()

    # ── sources: Posterflow's poster file for a server item ────────────────
    def _upload_index(self) -> Dict[Tuple[str, str], str]:
        """(server id, item id) → the poster file the uploader last pushed there, from its records."""
        if self._source_index is None:
            index: Dict[Tuple[str, str], str] = {}
            try:
                for rec in self.db.query(PlexUploadRecord):
                    stem = os.path.splitext(os.path.basename(str(rec.file_path or "")))[0].lower()
                    # Season files are recorded under the SHOW's id; they are found from the show's folder instead.
                    if stem.startswith(_ARTWORK_WORDS) or _SEASON_FILE.match(stem):
                        continue
                    d = rec.to_dict()
                    keys = [str(k) for k in d.get("uploaded_to_rating_keys", [])]
                    servers = {str(lk).split(":", 1)[0] for lk in d.get("uploaded_to_library_keys", []) if ":" in str(lk)} or {""}
                    for sid in servers:
                        for rk in keys:
                            index[(sid, rk)] = str(rec.file_path)
            except SQLAlchemyError as exc:
                self.db.rollback()
                log_warning(LogTags.OVERLAYS, f"Could not read the upload records: {exc}")
            self._source_index = index
        return self._source_index

    def source_for(self, client: Any, item: Any, parent: Any = None) -> Optional[str]:
        """The clean poster file Posterflow placed for this item, or None when it has none. A
        season's file is SeasonNN.<ext> in its show's folder."""
        item_id = str(item.item_id)
        if self._sources is not None:
            path = self._sources.get((str(getattr(client, "instance_name", "") or ""), item_id))
            return path if path and os.path.isfile(path) else None
        if item.item_type == "season":
            if parent is None or item.index is None:
                return None
            show_file = self.source_for(client, parent)
            if not show_file:
                return None
            return self._season_file(os.path.dirname(show_file), int(item.index))
        index = self._upload_index()
        path = index.get((str(getattr(client, "server_id", "") or ""), item_id)) or index.get(("", item_id))
        return path if path and os.path.isfile(path) else None

    @staticmethod
    def _season_file(folder: str, number: int) -> Optional[str]:
        for name in (f"Season{number:02d}", f"Season{number}"):
            for ext in _IMAGE_EXTS:
                path = os.path.join(folder, name + ext)
                if os.path.isfile(path):
                    return path
        try:
            for entry in os.listdir(folder):
                stem, ext = os.path.splitext(entry)
                m = _SEASON_FILE.match(stem)
                if m and int(m.group(1)) == number and ext.lower() in _IMAGE_EXTS:
                    return os.path.join(folder, entry)
        except OSError:
            pass
        return None

    def _sha1_of(self, path: str) -> str:
        try:
            st = os.stat(path)
        except OSError:
            return ""
        cached = self._sha_cache.get(path)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            return cached[2]
        with open(path, "rb") as f:
            digest = hashlib.sha1(f.read()).hexdigest()
        self._sha_cache[path] = (st.st_mtime, st.st_size, digest)
        return digest

    @staticmethod
    def _library_of(item: Any, fallback: str = "") -> str:
        return str(item.library_key or "").split(":", 1)[-1] or fallback

    def _library_selected(self, instance_name: str, item: Any, parent: Any = None) -> bool:
        """Is the item's library among the ones overlays cover? Library keys are compared by
        their last segment: items carry "/library/sections/N", the config keeps "N" (Plex) or
        the library's GUID (Jellyfin)."""
        if self._selected is None:
            self._selected = self.selected_libraries()
        mine = (self._library_of(item) or (self._library_of(parent) if parent is not None else "")).rsplit("/", 1)[-1]
        return bool(mine) and any(str(lib.get("key", "")).rsplit("/", 1)[-1] == mine for lib in self._selected.get(instance_name, []))

    # ── resolution (shared with the preview) ───────────────────────────────
    @staticmethod
    def _type_configs(tcfg: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Every switched-on overlay of the type (each with its own placement/options)."""
        return [o for o in (tcfg.get("overlays") or []) if o.get("enabled", True)]

    @staticmethod
    def _overlay_for(tcfg: Dict[str, Any], library_id: str) -> Optional[Dict[str, Any]]:
        """The type's overlay for an item: the one naming the item's library, else the first set
        to all libraries; None when neither exists. Library keys compare by their last segment
        (see _library_selected)."""
        instance, _, key = (library_id or "").partition(":")
        key = key.rsplit("/", 1)[-1]
        overlays = ServerOverlayEngine._type_configs(tcfg)  # an overlay switched off is as good as absent
        if instance and key:
            for overlay in overlays:
                if overlay.get("all_libraries"):
                    continue
                for lib in overlay.get("libraries") or []:
                    o_instance, _, o_key = str(lib).partition(":")
                    if o_instance == instance and o_key.rsplit("/", 1)[-1] == key:
                        return overlay
        return next((o for o in overlays if o.get("all_libraries")), None)

    def _resolve(self, ctx: ItemContext) -> Tuple[List[Tuple[OverlayType, OverlayBadge, Dict[str, Any]]], bool, bool, Dict[str, Any]]:
        """(badges, had_data, applicable, meta)."""
        out: List[Tuple[OverlayType, OverlayBadge, Dict[str, Any]]] = []
        relevant = [(otype, cfg) for otype, tcfg in self.active if ctx.target in otype.targets
                    for cfg in [self._overlay_for(tcfg, ctx.library_id)] if cfg is not None]
        if not relevant:
            return out, True, False, {}
        meta: Dict[str, Any] = {}
        had_data = True
        if any("tmdb" in otype.needs_for(cfg["options"]) for otype, cfg in relevant):  # media-only overlays ask TMDB nothing
            meta = self.metadata.for_item(ctx)
            had_data = bool(meta)
        for otype, cfg in relevant:
            try:
                badge = otype.resolve(ctx, cfg["options"], meta)
            except Exception as exc:
                log_warning(LogTags.OVERLAYS, f"{otype.label} overlay failed for {self._label(ctx)}: {exc}")
                continue
            if badge is not None:
                out.append((otype, badge, cfg["placement"]))
        return out, had_data, True, meta

    @staticmethod
    def _badge_lines(sig: str) -> List[str]:
        """The badge lines of a signature, without the settings lines."""
        return [line for line in (sig or "").split("\n") if line and not line.startswith("cfg:")]

    def _signature(self, ctx: ItemContext, resolved: List[Tuple[OverlayType, OverlayBadge, Dict[str, Any]]]) -> str:
        """What was drawn, and the settings it was drawn with: one line per badge, then one
        "cfg:<type>:<hash>" line per badge for the overlay definition that produced it. A settings
        edit changes the lines of every item it touches, so any run or upload sees it per item."""
        lines = [f"{otype.key}:{badge.signature}{stand_in_mark(badge, placement)}" for otype, badge, placement in resolved]
        for otype, _badge, _placement in resolved:
            tcfg = next((t for o, t in self.active if o.key == otype.key), None)
            overlay = self._overlay_for(tcfg, ctx.library_id) if tcfg else None
            if overlay is not None:
                lines.append(f"cfg:{otype.key}:{self._overlay_hashes.setdefault(id(overlay), overlay_signature(overlay))}")
        return "\n".join(lines)

    def _count_states(self, resolved: List[Tuple[OverlayType, OverlayBadge, Dict[str, Any]]], step: int = 1) -> None:
        for _otype, badge, _pl in resolved:
            state = badge.key.upper().replace("_", " ")
            count = self.stats["by_state"].get(state, 0) + step
            if count > 0:
                self.stats["by_state"][state] = count
            else:
                self.stats["by_state"].pop(state, None)

    @staticmethod
    def _server_ref(entry: Dict[str, Any]) -> Optional[str]:
        """The server's reference to the poster an entry was composed on; None for a file source."""
        source = str((entry or {}).get("source_path") or "")
        return source[len(_SERVER_SOURCE):] if source.startswith(_SERVER_SOURCE) else None

    @staticmethod
    def _fetch_server_poster(client: Any, item: Any, ref: str) -> Optional[Tuple[str, bytes]]:
        """The media server's own clean poster for the item, (reference, bytes); None when it has none."""
        fetch = getattr(client, "server_poster", None)
        if not callable(fetch):
            return None
        try:
            got = fetch(item, ref)
        except Exception as exc:
            log_debug(LogTags.OVERLAYS, f"Could not fetch the server's own poster for {item.title}: {exc}")
            return None
        return (str(got[0]), bytes(got[1])) if got and got[1] else None

    # ── compose and upload ─────────────────────────────────────────────────
    @staticmethod
    def _compose_bytes(source: Any, resolved: List[Tuple[OverlayType, OverlayBadge, Dict[str, Any]]]) -> Tuple[bytes, str]:
        """`source` is a poster file path, or the bytes of the media server's own poster."""
        with Image.open(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source) as img:
            out = img.convert("RGB")
        layers = join_lines([(otype.key, badge, placement) for otype, badge, placement in resolved])  # badges that share a line become one
        out, _rects = render_badges(out, [(badge, placement) for _key, badge, placement in layers])  # the edge band once, then every badge
        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=92)
        data = buf.getvalue()
        return data, hashlib.sha1(data).hexdigest()

    def _upload_file(self, client: Any, item: Any, path: str) -> None:
        if not client.upload_image(item, "poster", path):
            raise RuntimeError("the server rejected the poster upload")
        name = str(getattr(client, "instance_name", "") or "")
        self.stats["uploads_by_instance"][name] = self.stats["uploads_by_instance"].get(name, 0) + 1

    def _upload_bytes(self, client: Any, item: Any, data: bytes) -> None:
        fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        try:
            with open(tmp_path, "wb") as f:
                f.write(data)
            self._upload_file(client, item, tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def _plex_prune_root(self, instance_name: str) -> Optional[str]:
        """The Metadata folder bundles are pruned under for this server, decided once per run:
        None when the switch is off, no folder is set, the folder is not a Metadata folder, or
        a delete already failed, so no bundle folder is looked at after that."""
        if instance_name in self._plex_prune_roots:
            return self._plex_prune_roots[instance_name]
        root = self.plex_metadata_paths.get(instance_name) if self.prune_plex else None
        if root and not is_plex_metadata_root(root):
            log_warning(LogTags.OVERLAYS, f"{instance_name}: {root} is not a Plex Metadata folder (no Movies or TV Shows folder inside) — "
                                          f"its bundles are left alone this run; check the folder on the Tools page")
            root = None
        self._plex_prune_roots[instance_name] = root
        return root

    def _upload_lost(self, instance_name: str, item: Any, parent: Any, sha: str) -> bool:
        """Is our badged poster gone from the item's Plex bundle (deleted on disk), so the server
        shows no poster? Only told when the server's Metadata folder is set and the bundle is there."""
        root = self.plex_metadata_paths.get(instance_name)
        return bool(root) and is_plex_metadata_root(root) and upload_gone(root, item, parent, sha)

    def _prune_plex_uploads(self, instance_name: str, client: Any, item: Any, parent: Any, keep: Optional[Set[str]], label: str) -> None:
        """Tidy the item's Plex bundle after a change, keeping the poster on the server now.
        Needs the instance's Metadata folder (Tools page); a wrong path deletes nothing. A bundle
        another library's copy of the title also uses keeps what that copy shows too."""
        root = self._plex_prune_root(instance_name)
        if not root or client is None:
            return
        result = prune_plex_uploads(root, item, parent, keep, client=client)
        if result.shared:
            self.stats["plex_shared"] += 1
            return
        if result.error:
            self._plex_prune_roots[instance_name] = None  # no more folders are tried this run
            log_warning(LogTags.OVERLAYS, f"{instance_name}: {result.error} — Posterflow (uid {os.getuid()}) cannot delete in the Plex bundle folders; "
                                          f"its bundles are left alone this run (give it the Plex user's group: PGID or EXTRA_GROUPS)")
            return
        if result.missing:
            if instance_name not in self._plex_path_warned:
                self._plex_path_warned.add(instance_name)
                log_warning(LogTags.OVERLAYS, f"{instance_name}: no upload bundle found for {label} under the Plex metadata path "
                                              f"({root}) — check the path on the Tools page; nothing was pruned")
            return
        if result.removed:
            self.stats["plex_pruned"] += result.removed
            self.stats["plex_freed_bytes"] += result.freed
            log_debug(LogTags.OVERLAYS, f"{label} | pruned {result.removed} superseded upload(s) from the Plex bundle ({result.freed / 1024 / 1024:.1f} MB)")

    def _tidy_servers(self, clients: Dict[str, Any]) -> None:
        """After uploading: ask Jellyfin to clean its image cache, where every replaced poster
        leaves resized copies behind (Plex offers no API for its old uploads)."""
        for name, count in self.stats["uploads_by_instance"].items():
            client = clients.get(name)
            if not count or client is None or getattr(client, "server_type", "") != "jellyfin":
                continue
            try:
                if client.run_scheduled_task("DeleteCacheFiles"):
                    log_info(LogTags.OVERLAYS, f"{name}: asked Jellyfin to clean its image cache after {count} upload(s)")
            except Exception as exc:
                log_debug(LogTags.OVERLAYS, f"{name}: could not start Jellyfin's cache clean: {exc}")

    # ── is the poster on the server still ours? ───────────────────────────
    def _server_poster_changed(self, key: str, item: Any, entry: Dict[str, Any], label: str, *, persist: bool) -> str:
        """Did someone replace the poster on the server since we uploaded ours ('replaced'), or
        does Plex show no poster at all ('blank')? '' when it is still ours or the server could
        not say. The listing's cheap hint decides whether to look at all; only when it moved does
        Plex get asked which poster is selected (one small request). Jellyfin's hint is the image
        itself."""
        hint = poster_key(item)
        recorded = str(entry.get("server_key") or "")
        if hint == recorded:
            return ""
        if not hint:
            changed = "replaced" if recorded else ""  # we had confirmed ours and now there is no image at all
        elif hint.startswith("jf:"):
            changed = "replaced" if recorded else ""  # a known tag that moved is a different image; none known = first look after our upload
        else:
            selected = stable_poster_key(item)
            if not selected:
                return ""  # the server could not tell us: leave the item alone
            changed = "blank" if selected == PLEX_NO_POSTER else "replaced" if upload_sha(selected) != entry.get("composite_sha1") else ""
        if changed:
            self.lost_on_server.add(key)
            self._log_item(f"{label} | " + ("Plex shows no poster for it" if changed == "blank" else "the poster on the server is no longer ours"))
            return changed
        if persist:
            self._set_entry(key, {**entry, "server_key": hint})
        return ""

    def _server_key_after_upload(self, client: Any, item: Any) -> str:
        """Jellyfin's image tag is a content hash: fetch it now so later listings tell a replaced
        image at a glance. Plex is confirmed on the next listing instead (its thumb timestamp
        alone can't be trusted)."""
        if getattr(client, "server_type", "") != "jellyfin":
            return ""
        try:
            with self.off_turn():
                fresh = client.get_item(item.item_id)
        except Exception:
            return ""
        return poster_key(fresh) if fresh is not None else ""

    # ── the item's context: ids, ratings, and its media files when an overlay reads them ──
    def _load_library_media(self, instance_name: str, client: Any, library_key: str, items: List[Any]) -> None:
        """One listing per library fills in the media files of all its items."""
        try:
            by_id = client.library_media(library_key) or {}
        except Exception as exc:
            log_warning(LogTags.OVERLAYS, f"{instance_name}: could not read the media details of library {library_key}: {exc}")
            return
        for item in items:
            self._media[(instance_name, str(item.item_id))] = by_id.get(str(item.item_id), [])

    def _context(self, instance_name: str, item: Any, parent: Any = None, client: Any = None) -> ItemContext:
        ctx = ItemContext.from_server_item(item, instance_name, parent)
        if self._needs_media:
            key = (instance_name, str(item.item_id))
            if key not in self._media:  # outside a run (an upload): asked for this one item
                source = client or getattr(item, "client", None)
                try:
                    self._media[key] = list(source.item_media(item) or []) if source is not None and callable(getattr(source, "item_media", None)) else []
                except Exception as exc:
                    log_debug(LogTags.OVERLAYS, f"Could not read the media details of {item.title}: {exc}")
                    self._media[key] = []
            ctx.media = self._media[key]
        return ctx

    # ── the per-item decision ──────────────────────────────────────────────
    def _process(self, instance_name: str, client: Any, item: Any, *, library: str, source: Optional[str],
                 parent: Any = None, mode: str = "incremental", dry_run: bool = False) -> None:
        ctx = self._context(instance_name, item, parent, client)
        label = self._label(ctx)
        key = ctx.rel_path
        resolved, had_data, applicable, meta = self._resolve(ctx)
        entry = (self._state["items"].get(key) or {})
        if not applicable:
            if entry:  # overlays off (or this kind no longer targeted): give the clean poster back
                self._restore(instance_name, client, item, key, entry, label, dry_run, parent)
            return
        sig = self._signature(ctx, resolved)
        ids = ", ".join(f"{k} {v}" for k, v in (("tmdb", ctx.tmdb_id), ("tvdb", ctx.tvdb_id), ("imdb", ctx.imdb_id)) if v) or "no ids"
        # One debug line per item: "Title (Year) → BADGE | outcome | TMDB how: record".
        tmdb_part = f" | TMDB {meta['lookup']}: {describe_record(ctx.media_type, meta.get('tmdb'))}" if meta.get("lookup") else ""
        text = self._texts(sig)

        if not sig:
            if had_data:
                self.stats["no_badge"] += 1
                self._log_item(f"{label} → no badge | no rule matched{tmdb_part}")
            else:
                self.stats["no_data"] += 1
                if len(self._no_data_examples) < 12:
                    self._no_data_examples.append(label)
                self._log_item(f"{label} → no badge | no TMDB record ({ids}){tmdb_part}")
            if entry:
                self._restore(instance_name, client, item, key, entry, label, dry_run, parent)
            elif not dry_run and source and self._plex_prune_root(instance_name):
                # Never badged, but Posterflow's: its bundle may hold old uploads (Asset Upload
                # replacing posters over time). Keep just the poster Plex shows now.
                self._prune_plex_uploads(instance_name, client, item, parent, {upload_sha(stable_poster_key(item))}, label)
            return

        # No Posterflow file: with the option on, the badge is composed on the server's own poster.
        on_server = source is None
        ref = self._server_ref(entry)
        if on_server and not self.server_sources:
            if ref is not None:  # badged on the server's poster while the option was on: give it back
                self._restore(instance_name, client, item, key, entry, label, dry_run, parent)
                return
            self.stats["no_poster"] += 1
            self._log_item(f"{label} → {text} | no Posterflow poster, left alone"
                           + (" (badged by an earlier upload; its file is gone)" if entry else ""))
            return

        self._count_states(resolved)
        source_sha = "" if on_server else self._sha1_of(source)
        change = self._server_poster_changed(key, item, entry, label, persist=not dry_run) if entry else ""
        replaced = change == "replaced"
        # A server poster is fetched only to draw: unchanged means the same badge on the same kind of source.
        same_source = (ref is not None) if on_server else entry.get("source_sha1") == source_sha
        # Plex shows no poster: none is selected, or it still points at our upload whose file is gone on disk.
        lost = change == "blank" or (bool(entry) and not replaced and self._upload_lost(instance_name, item, parent, str(entry.get("composite_sha1") or "")))
        lost_why = "Plex showed no poster" if change == "blank" else "its poster file was gone from the Plex bundle"
        if entry and not replaced and not lost and mode == "incremental" and entry.get("sig") == sig and same_source:
            self.stats["current"] += 1
            self._log_item(f"{label} → {text} | current{tmdb_part}")
            if not dry_run:
                self._prune_plex_uploads(instance_name, client, item, parent, {entry.get("composite_sha1", "")}, label)
            return

        if dry_run:
            if not entry:
                self.stats["applied"] += 1
                self._log_item(f"{label} → {text} | would apply ({ids}){tmdb_part}")
            elif lost:
                self.stats["refreshed"] += 1
                self._log_item(f"{label} → {text} | would upload again ({lost_why}){tmdb_part}")
            else:
                self.stats["refreshed"] += 1
                self._log_item(f"{label} → {text} | would refresh (was {self._texts(entry.get('sig') or '')}){tmdb_part}")
            return

        if on_server:
            # The poster recorded before, unless someone changed the item's poster: then the server's pick now.
            fetched = self._fetch_server_poster(client, item, "" if replaced else (ref or ""))
            if fetched is None:
                self._count_states(resolved, -1)
                self.stats["no_poster"] += 1
                self._log_item(f"{label} → {text} | no Posterflow poster and {instance_name} has none of its own, left alone")
                return
            source_path, image = _SERVER_SOURCE + fetched[0], fetched[1]
            source_sha, source_name = hashlib.sha1(image).hexdigest(), f"{instance_name}'s own poster"
        else:
            source_path, image, source_name = source, source, os.path.basename(source)
        try:
            data, composite_sha = self._compose_bytes(image, resolved)
        except Exception as exc:
            self.stats["failed"] += 1
            log_warning(LogTags.OVERLAYS, f"Could not compose the badge for {label} from {source_name}: {exc}")
            return
        record = {
            "sig": sig, "source_path": source_path, "source_sha1": source_sha, "composite_sha1": composite_sha,
            "title": label, "instance": instance_name, "library": library, "item_type": item.item_type,
            "server_key": entry.get("server_key", "") if entry else "",
        }
        if entry and not replaced and not lost and mode != "rebuild" and entry.get("composite_sha1") == composite_sha:
            # Same pixels as what is already on the server (a settings change that did not touch
            # this badge): no upload, no new poster entry on the server. A rebuild uploads anyway.
            self.stats["current"] += 1
            self._log_item(f"{label} → {text} | current (recomposed identically, no upload){tmdb_part}")
            self._set_entry(key, record)
            self._prune_plex_uploads(instance_name, client, item, parent, {composite_sha}, label)
            return
        try:
            self._upload_bytes(client, item, data)
        except Exception as exc:
            self.stats["failed"] += 1
            log_warning(LogTags.OVERLAYS, f"Failed to badge {label} on {instance_name}: {exc}")
            return
        if on_server:
            self.stats["server_sourced"] += 1
        if not entry:
            self.stats["applied"] += 1
            self._log_item(f"{label} → {text} | applied ({ids}; composed from {source_name}, uploaded){tmdb_part}")
        else:
            self.stats["refreshed"] += 1
            was = self._texts(entry.get("sig") or "")
            source_changed = f"{instance_name}'s own poster changed" if on_server and ref is not None else "poster file changed"
            why = ("poster replaced on the server" if replaced else lost_why if lost
                   else source_changed if entry.get("source_sha1") != source_sha else "settings changed" if was == text else f"was {was}")
            if lost:
                self.stats["reuploaded"] += 1
            if mode == "rebuild" and why == "settings changed":
                why = "rebuild"
            self._log_item(f"{label} → {text} | refreshed ({why}; recomposed, uploaded){tmdb_part}")
        record["server_key"] = self._server_key_after_upload(client, item)
        self._set_entry(key, record)
        self._prune_plex_uploads(instance_name, client, item, parent, {composite_sha}, label)

    def _restore(self, instance_name: str, client: Any, item: Any, key: str,
                 entry: Dict[str, Any], label: str, dry_run: bool, parent: Any = None) -> None:
        """Put the clean poster back and forget the badge: the Posterflow file is uploaded again,
        or, for a badge composed on the server's own poster, the server shows its own again."""
        was = self._texts(entry.get("sig") or "")
        if dry_run:
            self.stats["restored"] += 1
            self._log_item(f"{label} → clean poster | would restore (was {was})")
            return
        ref = self._server_ref(entry)
        if ref is not None:
            # Composed on the server's own poster: nothing of ours to upload, the server shows its own again.
            try:
                ok = bool(client.restore_server_poster(item, ref))
            except Exception as exc:
                ok = False
                log_debug(LogTags.OVERLAYS, f"{label}: putting {instance_name}'s own poster back failed: {exc}")
            if not ok:
                self.stats["failed"] += 1
                log_warning(LogTags.OVERLAYS, f"Failed to put {instance_name}'s own poster back on {label}")
                return
            self.stats["restored"] += 1
            self._log_item(f"{label} → {instance_name}'s own poster | restored (was {was})")
            self._prune_plex_uploads(instance_name, client, item, parent, None, label)  # every upload of ours can go
            self._drop_entry(key)
            return
        source = str(entry.get("source_path") or "")
        if not source or not os.path.isfile(source):
            self.stats["failed"] += 1
            log_warning(LogTags.OVERLAYS, f"Cannot restore {label}: its poster file is gone ({source or 'unknown'}) — "
                                          "upload a poster for it or reset the server's posters; forgetting its badge")
            self._drop_entry(key)
            return
        try:
            self._upload_file(client, item, source)
        except Exception as exc:
            self.stats["failed"] += 1
            log_warning(LogTags.OVERLAYS, f"Failed to restore {label} on {instance_name}: {exc}")
            return
        self.stats["restored"] += 1
        self._log_item(f"{label} → clean poster | restored (was {was}; {os.path.basename(source)} uploaded)")
        self._prune_plex_uploads(instance_name, client, item, parent, {self._sha1_of(source)}, label)
        self._drop_entry(key)

    # ── at upload time: the uploader hands us the file it is about to push ──
    def compose_for_upload(self, instance_name: str, item: Any, upload_file: str, *, source_path: Optional[str] = None,
                           parent: Any = None) -> Optional[str]:
        """The composite to upload instead of `upload_file`, or None to push the file as it is.
        `source_path` is the poster file in the destination folder (what `upload_file` was made
        from, when the uploader had to convert it). Call `record_upload()` afterwards."""
        ctx = self._context(instance_name, item, parent)
        key = ctx.rel_path
        label = self._label(ctx)
        self._discard_pending(key)
        if not self._library_selected(instance_name, item, parent):
            self._pending[key] = None  # a library overlays don't cover: the plain poster goes up
            return None
        resolved, had_data, applicable, _meta = self._resolve(ctx)
        sig = self._signature(ctx, resolved) if applicable else ""
        if not sig:
            if applicable and not had_data:
                self.stats["no_data"] += 1
            self._pending[key] = None  # a clean upload: any badge record goes once it is through
            return None
        source = source_path or upload_file
        try:
            with self.off_turn():
                data, composite_sha = self._compose_bytes(upload_file, resolved)
        except Exception as exc:
            log_warning(LogTags.OVERLAYS, f"Could not badge {label} at upload ({exc}); uploading the plain poster")
            self._pending[key] = None
            return None
        fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        with open(tmp_path, "wb") as f:
            f.write(data)
        self._pending[key] = {
            "sig": sig, "source_path": source, "source_sha1": self._sha1_of(source), "composite_sha1": composite_sha,
            "title": label, "instance": instance_name, "item_type": item.item_type, "tmp": tmp_path, "resolved": resolved,
        }
        return tmp_path

    def badges_seasons(self) -> bool:
        return self._apply_to_seasons()

    def badge_stale(self, instance_name: str, item: Any, source_path: str, parent: Any = None) -> bool:
        """For a poster the uploader would otherwise skip (its file is unchanged): does the badge
        on the server need redoing? A status that moved on, edited settings, or a badge that no
        longer applies (then the clean poster must go back up)."""
        if not self._library_selected(instance_name, item, parent):
            return False
        entry = self._state["items"].get(f"{instance_name}|{item.item_id}")
        ctx = self._context(instance_name, item, parent)
        resolved, _had_data, applicable, _meta = self._resolve(ctx)
        sig = self._signature(ctx, resolved) if applicable else ""
        if not sig:
            return bool(entry)
        if not entry:
            return True
        key = f"{instance_name}|{item.item_id}"
        if entry.get("sig") != sig:
            # Only the settings changed, on the file the badge was made from: redo it when that
            # changes the pixels; otherwise just note the settings the badge now stands for.
            if self._badge_lines(entry.get("sig")) != self._badge_lines(sig) or entry.get("source_path") != source_path:
                return True
            try:
                _data, composite_sha = self._compose_bytes(source_path, resolved)
            except Exception:
                return True
            if composite_sha != entry.get("composite_sha1"):
                return True
            self._set_entry(key, {**entry, "sig": sig})
            self._commit_state()
            entry = self._state["items"][key]
        if self._server_ref(entry) is not None:
            return True  # badged on the server's own poster before this file existed: redo it on the file
        # Two placed folders can feed the same item (a duplicate the cleanup step removes later):
        # only the file the badge was composed from counts as "changed" when its bytes differ.
        if entry.get("source_path") == source_path and entry.get("source_sha1") != self._sha1_of(source_path):
            return True
        if self._server_poster_changed(key, item, entry, ctx and self._label(ctx), persist=True):
            return True
        self._commit_state()  # a confirmed hint is worth keeping even when nothing else changes
        return False

    def _discard_pending(self, key: str) -> None:
        pending = self._pending.pop(key, None)
        if pending and os.path.exists(pending.get("tmp", "")):
            os.remove(pending["tmp"])

    def record_upload(self, instance_name: str, item: Any, ok: bool, *, parent: Any = None) -> None:
        """After the uploader pushed what `compose_for_upload()` returned: keep the badge record
        in step with what is on the server now."""
        key = f"{instance_name}|{item.item_id}"
        if key not in self._pending:
            return
        pending = self._pending.pop(key)
        if pending and os.path.exists(pending.get("tmp", "")):
            os.remove(pending["tmp"])
        if not ok:
            return
        entry = self._state["items"].get(key)
        label = str((pending or {}).get("title") or (entry or {}).get("title") or key)
        if pending is None:
            if entry:  # the clean poster is on the server now
                self.stats["restored"] += 1
                self._log_item(f"{label} → clean poster | badge dropped (plain poster uploaded)")
                self._drop_entry(key)
                self._commit_state()
            return
        self._count_states(pending["resolved"])
        self.stats["applied" if not entry else "refreshed"] += 1
        self._log_item(f"{label} → {self._texts(pending['sig'])} | badged at upload")
        record = {k: v for k, v in pending.items() if k not in ("tmp", "resolved")}
        record["library"] = self._library_of(item)
        record["server_key"] = self._server_key_after_upload(getattr(item, "client", None), item)
        self._set_entry(key, record)
        self._prune_plex_uploads(instance_name, getattr(item, "client", None), item, parent, {pending["composite_sha1"]}, label)
        self._commit_state()

    # ── entry points ───────────────────────────────────────────────────────
    def _apply_to_seasons(self) -> bool:
        for otype, tcfg in self.active:
            if "season" in otype.targets and any(cfg["options"].get("apply_to_seasons") for cfg in self._type_configs(tcfg)):
                return True
        return False

    def restore_all(self, *, dry_run: bool = False, progress: Optional[ProgressCallback] = None,
                    job_id: Optional[int] = None) -> Dict[str, Any]:
        """Overlays are off: upload the clean poster for every badged item and forget it."""
        entries = dict(self._state["items"])
        if not entries:
            log_info(LogTags.OVERLAYS, "No item carries a badge — nothing to restore")
            return self.stats
        why = "Overlays are off" if not self.active else "Remove overlays requested"
        log_info(LogTags.OVERLAYS, f"{why} — uploading the clean poster for {len(entries)} badged item(s)")
        clients: Dict[str, Any] = {}
        self._restore_entries(entries, clients, dry_run=dry_run, progress=progress, job_id=job_id)
        if not dry_run:
            self._tidy_servers({k: v for k, v in clients.items() if v is not None})
        return self.stats

    def _restore_entries(self, entries: Dict[str, Dict[str, Any]], clients: Dict[str, Any], *, dry_run: bool,
                         progress: Optional[ProgressCallback], job_id: Optional[int]) -> None:
        """Put the clean poster back for these badge records and forget them. `clients` is
        filled in as servers are connected to. A record of a server that is no longer
        configured is dropped: there is nothing to put the poster back on."""
        instances = {inst["name"]: inst for inst in self.instances()}
        unconfigured: Dict[str, int] = {}
        for n, (key, entry) in enumerate(entries.items(), start=1):
            if job_id is not None:
                check_cancelled(job_id)
            name, _, item_id = key.partition("|")
            label = str(entry.get("title") or key)
            if progress:
                progress("restoring", n, len(entries), f"Restoring: {label}")
            instance = instances.get(name)
            if instance is None:
                unconfigured[name] = unconfigured.get(name, 0) + 1
                if not dry_run:
                    self._drop_entry(key)
                continue
            if name not in clients:
                clients[name] = self._client_factory(instance)
                if clients[name] is not None:
                    clients[name].instance_name = name
            client = clients[name]
            if client is None:
                log_warning(LogTags.OVERLAYS, f"Cannot restore {label}: {name} is unreachable")
                self.stats["failed"] += 1
                continue
            item = client.get_item(item_id)
            if item is None:
                self._drop_entry(key)
                self._log_item(f"{label} → clean poster | forgot (no longer on {name})")
                continue
            self._restore(name, client, item, key, entry, label, dry_run)
        for name, count in unconfigured.items():
            log_warning(LogTags.OVERLAYS, f"{count} badge record(s) of media server '{name}' {'would be ' if dry_run else ''}dropped: "
                                          "it is no longer configured, so there is nothing to put the clean poster back on")

    def _left_out(self, selected: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
        """Badge records in libraries overlays no longer cover (unticked, or their server gone)."""
        keys = {name: {str(lib.get("key", "")).rsplit("/", 1)[-1] for lib in libs} for name, libs in selected.items()}
        return {k: e for k, e in self._state["items"].items()
                if e.get("library") and str(e["library"]).rsplit("/", 1)[-1] not in keys.get(str(e.get("instance")), set())}

    def run(self, *, mode: str = "incremental", dry_run: bool = False,
            progress: Optional[ProgressCallback] = None, job_id: Optional[int] = None) -> Dict[str, Any]:
        """incremental: skip items whose badge, settings, poster file and server poster are
        unchanged (a settings edit is seen per item: each record carries the settings it was drawn
        with). full: recompose every badge, upload the ones whose pixels changed. rebuild:
        recompose and upload every badge, whatever is on the server, asking TMDB again for every
        title instead of trusting the cache."""
        if not self.active:
            return self.restore_all(dry_run=dry_run, progress=progress, job_id=job_id)
        selected = self.selected_libraries()
        if not selected:
            log_warning(LogTags.OVERLAYS, "No media-server libraries selected for overlays — tick some on the Overlays page (or enable libraries under Settings → media servers)")
            return self.stats
        names = ", ".join(otype.label for otype, _ in self.active)
        source = "TMDB key set" if (not self._needs_tmdb or self.metadata.available) else "no TMDB key — status badges will be skipped"
        log_info(LogTags.OVERLAYS, f"Overlays on: {names} ({source}); mode: {mode}{' (dry run)' if dry_run else ''}")
        self.metadata.begin_run(refresh=mode == "rebuild")
        if mode == "rebuild" and self._needs_tmdb and self.metadata.available:
            log_info(LogTags.OVERLAYS, "Rebuild: the TMDB cache is set aside, every title is asked again")

        instances = {inst["name"]: inst for inst in self.instances()}
        clients: Dict[str, Any] = {}
        work: List[Tuple[str, Any, str, List[Any], Dict[str, Any]]] = []
        seasons_wanted = self._apply_to_seasons()
        for name, libs in selected.items():
            instance = instances[name]
            client = self._client_factory(instance)
            if client is None:
                log_warning(LogTags.OVERLAYS, f"Cannot connect to {name} — skipping its libraries")
                continue
            client.instance_name = name
            clients[name] = client
            for lib in libs:
                lib_key = str(lib.get("key"))
                try:
                    items = [i for i in client.get_library_items(lib_key) if i.item_type in ("movie", "show")]
                except Exception as exc:
                    log_warning(LogTags.OVERLAYS, f"{name} library '{lib.get('title')}': could not list items: {exc}")
                    continue
                shows = {i.item_id: i for i in items if i.item_type == "show"}
                seasons: Dict[str, Any] = {}
                # Seasons badged while "Season posters too" was on are still listed after it is
                # switched off, so they get their clean poster back instead of being forgotten.
                lib_keys = {lib_key} | {self._library_of(i, lib_key) for i in items[:1]}
                badged_seasons = set() if seasons_wanted else {
                    k for k, e in self._state["items"].items()
                    if e.get("instance") == name and e.get("item_type") == "season" and e.get("library") in lib_keys}
                if shows and (seasons_wanted or badged_seasons):
                    try:
                        for season in client.get_library_seasons(lib_key):
                            if season.parent_id in shows and (seasons_wanted or f"{name}|{season.item_id}" in badged_seasons):
                                seasons[season.item_id] = season
                    except Exception as exc:
                        log_warning(LogTags.OVERLAYS, f"{name} library '{lib.get('title')}': could not list seasons: {exc}")
                log_info(LogTags.OVERLAYS, f"{name} · {lib.get('title')}: {len(items)} item(s)" + (f", {len(seasons)} season(s)" if seasons else ""))
                if self._needs_media:
                    self._load_library_media(name, client, lib_key, items + list(seasons.values()))
                work.append((name, client, lib_key, items + list(seasons.values()), shows))

        total = sum(len(w[3]) for w in work)
        done = 0
        for name, client, lib_key, items, shows in work:
            before = self._snapshot()
            for item in items:
                if job_id is not None:
                    check_cancelled(job_id)
                done += 1
                if progress:
                    progress("processing", done, total, f"Overlays: {item.title}")
                parent = shows.get(item.parent_id) if item.item_type == "season" else None
                library = self._library_of(item, lib_key)
                try:
                    self._process(name, client, item, library=library, source=self.source_for(client, item, parent),
                                  parent=parent, mode=mode, dry_run=dry_run)
                except Exception as exc:
                    self.stats["failed"] += 1
                    log_warning(LogTags.OVERLAYS, f"Overlay failed for {item.title}: {exc}")
            lib_title = next((str(l.get("title")) for l in selected.get(name, []) if str(l.get("key")) == lib_key), lib_key)
            log_info(LogTags.OVERLAYS, f"{name} · {lib_title}: {self._delta_text(before)}")
            # Collections are not walked, so their records are handled below.
            if not dry_run:
                self.forget_gone(name, lib_key, items)
        # Collections never carry a badge: a record one has gets its clean poster back and goes.
        badged_collections = {k: e for k, e in self._state["items"].items() if e.get("item_type") == "collection"}
        if badged_collections:
            log_info(LogTags.OVERLAYS, f"{len(badged_collections)} collection(s) carry a badge: putting the clean poster back")
            self._restore_entries(badged_collections, clients, dry_run=dry_run, progress=progress, job_id=job_id)
        left_out = self._left_out(selected)
        if left_out:
            log_info(LogTags.OVERLAYS, f"{len(left_out)} badged item(s) in libraries overlays no longer cover: putting the clean poster back")
            self._restore_entries(left_out, clients, dry_run=dry_run, progress=progress, job_id=job_id)
        if not dry_run:
            self._tidy_servers({k: v for k, v in clients.items() if v is not None})
        return self.stats


def build_server_engine(db: Session) -> Optional[ServerOverlayEngine]:
    """The engine for a run or an upload job. With overlays off it still comes back while items
    carry a badge, so clean uploads can drop their records and a run can restore them; None
    only when there is nothing to do."""
    from .config import load_config, overlays_active

    config = load_config(db)
    if overlays_active(config):
        return ServerOverlayEngine(db, config)
    engine = ServerOverlayEngine(db, {**config, "types": {k: {**v, "enabled": False} for k, v in config["types"].items()}})
    return engine if engine.badged_count() else None
