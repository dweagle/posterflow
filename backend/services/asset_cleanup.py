"""Service for cleaning up orphaned/stale poster asset folders.

This is the inverse of the Poster Renamer: it scans the organized destination
folder (and its ``tmp/`` staging twin) and removes asset folders that no longer
correspond to any live Radarr movie, Sonarr series, or Plex collection, plus
stale duplicate folders left behind after an arr/Plex rename.

Matching reuses the exact production matchers (``search_matches`` + ``is_match``)
so keep/remove decisions stay identical to how the renamer assigns posters.
"""

import json
import os
import re
import shutil
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from pathvalidate import is_valid_filename, sanitize_filename
from sqlalchemy.orm import Session

from core.logging import (
    LogTags,
    log_debug,
    log_error,
    log_info,
    log_section_end,
    log_section_start,
    log_success,
    log_warning,
)
from models.plex_upload import PlexUploadRecord
from models.poster import Poster
from models.setting import get_setting, upsert_setting
from util.constants import folder_year_regex, season_pattern
from util.posters.assets import get_assets_files
from util.posters.index import search_matches
from util.posters.match import collection_title_variants, is_match
from util.posters.scanner import artwork_type_of, artwork_flat_base, poster_slot_of

MEDIA_TYPES = ("movies", "series", "collections")


class AssetCleanupService:
    """Find and remove orphaned/stale poster asset folders in the destination."""

    ProgressCallback = Callable[[str, int, int, str], None]

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── settings helpers (mirrors UnmatchedAssetsService) ──────────────────

    def _get_list_setting(self, key: str) -> List[str]:
        """Load a list setting, supporting JSON arrays and newline/comma strings."""
        setting = get_setting(self.db, key)
        if not setting or not setting.value:
            return []
        raw_value = setting.value.strip()
        if not raw_value:
            return []
        try:
            parsed = json.loads(raw_value)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:
            log_debug(LogTags.CLEANUP, f"Invalid JSON for list setting '{key}', using delimiter fallback")
        return [item.strip() for item in re.split(r"[\n,]+", raw_value) if item.strip()]

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _entry_size(path: str) -> str:
        """Human-readable size of a file or directory tree."""
        try:
            total = 0.0
            if os.path.isdir(path):
                for root, _dirs, files in os.walk(path):
                    for f in files:
                        try:
                            total += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
            else:
                total = os.path.getsize(path)
            for unit in ("B", "KB", "MB", "GB"):
                if total < 1024:
                    return f"{total:.0f} {unit}"
                total /= 1024
            return f"{total:.1f} TB"
        except Exception:
            return "?"

    @staticmethod
    def _asset_folder_path(asset: Dict[str, Any]) -> Optional[str]:
        """Full path to the per-item folder for a nested asset, else None (flat layout)."""
        # Nested scans set asset["folder"] to the item folder name; flat scans leave it None.
        if not asset.get("folder"):
            return None
        files = asset.get("files") or []
        if not files:
            return None
        return os.path.dirname(files[0])

    @staticmethod
    def _asset_folder_name(asset: Dict[str, Any]) -> str:
        """On-disk folder name for a nested asset (e.g. 'Workin Moms (2018)')."""
        return str(asset.get("folder") or "")

    @staticmethod
    def _canonical_name(media: Dict[str, Any]) -> str:
        """The folder name the renamer would currently create for this media item.

        Mirrors PosterRenameService.rename_files: basename of the arr path, with
        collection names sanitized. This is the *current* name a stale duplicate
        is compared against.
        """
        folder = str(media.get("folder") or "")
        name = os.path.basename(folder.rstrip("/")) if folder else ""
        if not name:
            title = str(media.get("title") or "")
            year = media.get("year")
            name = f"{title} ({year})" if year else title
        if media.get("type") == "collections" and name and not is_valid_filename(name):
            name = sanitize_filename(name)
        return name

    @classmethod
    def _canonical_names(cls, media: Dict[str, Any]) -> List[str]:
        """Every folder name the renamer currently creates for this item: the primary
        plus duplicate-copy folders (extra_folders from split versions / multi-instance arrs)."""
        names = [cls._canonical_name(media)]
        names += [str(n) for n in (media.get("extra_folders") or []) if n]
        return list(dict.fromkeys(n for n in names if n))

    @staticmethod
    def _has_id_tag(asset: Dict[str, Any]) -> bool:
        """True if the asset carries an explicit tmdb/tvdb/imdb id."""
        for key in ("tmdb_id", "tvdb_id", "imdb_id"):
            value = asset.get(key)
            if key == "imdb_id":
                if value and isinstance(value, str) and value.startswith("tt"):
                    return True
            elif value and str(value).isdigit() and int(value) > 0:
                return True
        return False

    def _is_confident_orphan(self, asset: Dict[str, Any]) -> bool:
        """Year-bearing or id-tagged movie/series → confident it was a real arr item."""
        asset_type = str(asset.get("type") or "")
        if asset_type not in ("movies", "series"):
            return False
        return bool(asset.get("year")) or self._has_id_tag(asset)

    # ── matching ───────────────────────────────────────────────────────────

    def _group_assets_to_media(
        self,
        media_dict: Dict[str, List[Dict[str, Any]]],
        assets_dict: List[Dict[str, Any]],
        prefix_index: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Group every scanned asset under the live media item(s) it matches.

        Lenient by design (matches the renamer/unmatched semantics): an asset is
        only an orphan when NO media matches it, which biases toward keeping.

        Returns (groups, orphans) where each group is
        ``{"media": media, "assets": [asset, ...]}``.
        """
        groups: List[Dict[str, Any]] = []
        matched_ids: set[int] = set()

        for media_type in MEDIA_TYPES:
            for media in media_dict.get(media_type, []):
                title = str(media.get("title") or "")
                candidates: List[Dict[str, Any]] = []

                tmdb_id = media.get("tmdb_id")
                tvdb_id = media.get("tvdb_id")
                if tmdb_id:
                    candidates += search_matches(prefix_index, title, tmdb_id=tmdb_id)
                if tvdb_id:
                    candidates += search_matches(prefix_index, title, tvdb_id=tvdb_id)

                if media_type == "collections":
                    titles_to_try = collection_title_variants(title)
                else:
                    titles_to_try = [title]
                titles_to_try += media.get("alternate_titles", []) or []

                # Destination folder titles can differ from the server's display title
                # (folder "Se7en", Plex "Seven") — search by folder titles too
                for folder_source in [media.get("folder"), *(media.get("extra_folders") or [])]:
                    folder_base = os.path.basename(str(folder_source or "").rstrip("/"))
                    if not folder_base:
                        continue
                    year_match = re.search(folder_year_regex, folder_base)
                    folder_title = (year_match.group(1) if year_match else folder_base).strip()
                    if folder_title and folder_title not in titles_to_try:
                        titles_to_try.append(folder_title)

                for candidate_title in titles_to_try:
                    if candidate_title:
                        candidates += search_matches(prefix_index, candidate_title)

                canonical_names = {n.lower() for n in self._canonical_names(media)}

                seen: set[int] = set()
                matched_assets: List[Dict[str, Any]] = []
                for candidate in candidates:
                    if id(candidate) in seen:
                        continue
                    seen.add(id(candidate))
                    if is_match(candidate, media)[0]:
                        matched_assets.append(candidate)
                    elif (self._asset_folder_name(candidate) or "").lower() in canonical_names:
                        # The renamer creates exactly these folder names for this item, so a
                        # same-named folder is its asset folder even without a shared id
                        matched_assets.append(candidate)

                if matched_assets:
                    groups.append({"media": media, "assets": matched_assets})
                    for asset in matched_assets:
                        matched_ids.add(id(asset))

        orphans = [a for a in assets_dict if id(a) not in matched_ids]
        return groups, orphans

    # ── core ───────────────────────────────────────────────────────────────

    def cleanup(
        self,
        destination_dir: str,
        *,
        dry_run: bool = True,
        delete_unknown: bool = False,
        media_dict: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        fetch_failed_types: Optional[set] = None,
        library_setting_key: str = "asset_renamer_libraries",
        progress_callback: Optional[ProgressCallback] = None,
        artwork_boxes: Optional[List[Dict[str, Any]]] = None,
        artwork_sourced: Optional[Dict[int, Dict[str, set]]] = None,
        poster_sourced: Optional[Dict[int, Dict[Any, set]]] = None,
    ) -> Dict[str, Any]:
        """Reconcile the destination assets folder against the live media library.

        Args:
            destination_dir: Organized poster destination (``poster_destination``).
            dry_run: When True, report only — delete nothing.
            delete_unknown: When True, also remove unmatched stray/collection folders.
            media_dict: Pre-fetched live media; fetched here when None.
            progress_callback: Optional ``(phase, current, total, message)`` callback.
        """
        start = datetime.now(timezone.utc)
        log_section_start(LogTags.CLEANUP, "Asset Cleanup Starting")

        result = self._empty_result(dry_run)

        if not destination_dir or not os.path.isdir(destination_dir):
            log_warning(LogTags.CLEANUP, f"Destination folder not found, nothing to clean: {destination_dir}")
            result["error"] = f"Destination directory does not exist: {destination_dir}"
            log_section_end(LogTags.CLEANUP, "Asset Cleanup Skipped")
            return result

        if progress_callback:
            progress_callback("fetching", 0, 100, "Fetching live media library...")

        if media_dict is None:
            from services.poster_renamer import PosterRenameService

            # Same library selection the Asset Renamer uses, so cleanup reconciles against the
            # SAME set of items it placed — otherwise items in one selection but not the other
            # get their artwork pruned as "source removed" and re-placed next run (a loop).
            rename_service = PosterRenameService(self.db)
            media_dict = rename_service.get_media_from_instances(
                log_tag=LogTags.CLEANUP, setting_key=library_setting_key
            )
            fetch_failed_types = rename_service.media_fetch_failed_types

        total_media = sum(len(media_dict.get(t, [])) for t in MEDIA_TYPES)

        # ── Safety gate: never delete against an empty media set ───────────
        if total_media == 0:
            log_warning(
                LogTags.CLEANUP,
                "Aborting cleanup — no media returned from any source (safety guard). "
                "Nothing was deleted. Check media sources in Settings → Media tab and "
                "this feature's library selection.",
            )
            result["skipped_for_safety"] = True
            log_section_end(LogTags.CLEANUP, "Asset Cleanup Skipped (safety)")
            return result

        # Per-type guard: a type whose media list is empty is treated as unhealthy,
        # so we never remove that type's folders (e.g. Radarr down but Sonarr/Plex up).
        # A partial media-server fetch (hybrid mode, server down) is just as unsafe.
        unsafe_types = {t for t in MEDIA_TYPES if not media_dict.get(t)} | set(fetch_failed_types or ())
        if unsafe_types:
            if unsafe_types == {"collections"} and not fetch_failed_types:
                # Common and benign: the sources are healthy, there just are no collections
                log_info(
                    LogTags.CLEANUP,
                    "No collections found from any source — collection folder removals skipped (nothing to compare against)",
                    unsafe_types=sorted(unsafe_types),
                )
            else:
                log_warning(
                    LogTags.CLEANUP,
                    f"Skipping removals for types that returned no media (source unreachable, or none exist): {sorted(unsafe_types)} — their folders are kept",
                    unsafe_types=sorted(unsafe_types),
                )
            result["skipped_types"] = sorted(unsafe_types)

        ignore_set = {self._normalize_for_ignore(name) for name in self._get_list_setting("asset_cleanup_ignore")}

        # Artwork types the drives still source per item, so orphaned artwork in matched folders
        # can be pruned. A rename+cleanup run hands its own match verdicts across (one library
        # match, no place/prune disagreement); standalone cleanup re-derives them here.
        # Only expected failures degrade to "prune nothing" (ValueError = no subscribed drives, OSError = unreadable drive); defects propagate.
        if artwork_sourced is None:
            try:
                from services.artwork_scan import sourced_types_by_media
                artwork_sourced = sourced_types_by_media(self.db, media_dict, artwork_boxes)
            except (ValueError, OSError) as exc:
                log_warning(LogTags.CLEANUP, f"No artwork sources available; skipping artwork reconciliation: {exc}")
                artwork_sourced = {}
        # Per-type guard: only prune a type some item still sources (an empty type can't be
        # told apart from a down source, so it's never mass-deleted).
        globally_sourced_artwork = {t for types in artwork_sourced.values() for t in types}

        # Poster slots the drives still source per item — the poster mirror of the artwork
        # reconciliation, so posters placed from a since-unsubscribed drive get pruned too.
        # Same expected-failure handling: no subscribed/readable drives → prune nothing.
        if poster_sourced is None:
            try:
                from services.poster_renamer import sourced_poster_slots_by_media
                poster_sourced = sourced_poster_slots_by_media(self.db, media_dict)
            except (ValueError, OSError) as exc:
                log_warning(LogTags.CLEANUP, f"No poster sources available; skipping poster reconciliation: {exc}")
                poster_sourced = {}

        roots = [destination_dir]
        tmp_dir = os.path.join(destination_dir, "tmp")
        if os.path.isdir(tmp_dir):
            roots.append(tmp_dir)

        for index, root in enumerate(roots):
            if progress_callback:
                progress_callback(
                    "scanning", 10 + int(index / len(roots) * 80), 100,
                    f"Scanning {os.path.basename(root) or root}...",
                )
            self._reconcile_root(
                root,
                media_dict,
                dry_run=dry_run,
                delete_unknown=delete_unknown,
                unsafe_types=unsafe_types,
                ignore_set=ignore_set,
                result=result,
                # Artwork lives in the item folders under the main destination, never tmp/ —
                # it's placed straight to the real destination, so tmp/ artwork is residue the
                # renamer's own staging prune removes.
                artwork_sourced=artwork_sourced if root == destination_dir else {},
                globally_sourced_artwork=globally_sourced_artwork if root == destination_dir else set(),
                # Posters ARE reconciled in tmp/. It is not transient — it's preserved across
                # runs as the border replacer's incremental source, so a staged poster whose
                # drive was unsubscribed is copied back to the destination on the next run
                # (or re-rendered by the border pass as "missing_destination") right after this
                # prune removed it. Pruning the staged copy too ends that place/prune loop.
                poster_sourced=poster_sourced,
            )

        # ── Empty-folder sweep ─────────────────────────────────────────────
        if not dry_run:
            result["counts"]["empty_swept"] = self._sweep_empty_dirs(destination_dir)

        self._persist_stats(result)

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        counts = result["counts"]
        if progress_callback:
            progress_callback("completed", 100, 100, "Cleanup complete")
        log_success(
            LogTags.CLEANUP,
            (
                f"Cleanup {'(dry run) ' if dry_run else ''}complete in {elapsed:.2f}s — "
                f"removed {counts['removed_orphans']} orphan(s), {counts['removed_stale']} stale duplicate(s), "
                f"{counts['removed_artwork']} orphaned artwork, {counts['removed_posters']} unsourced poster(s); "
                f"kept {counts['unknown_kept']} unknown for review"
            ),
            **counts,
            dry_run=dry_run,
            time_sec=f"{elapsed:.2f}",
        )
        log_section_end(LogTags.CLEANUP, "Asset Cleanup Complete")
        return result

    def _reconcile_root(
        self,
        root: str,
        media_dict: Dict[str, List[Dict[str, Any]]],
        *,
        dry_run: bool,
        delete_unknown: bool,
        unsafe_types: set[str],
        ignore_set: set[str],
        result: Dict[str, Any],
        artwork_sourced: Optional[Dict[int, Dict[str, set]]] = None,
        globally_sourced_artwork: Optional[set] = None,
        poster_sourced: Optional[Dict[int, Dict[Any, set]]] = None,
    ) -> None:
        """Run grouping + classification + deletion for one scan root."""
        artwork_sourced = artwork_sourced or {}
        globally_sourced_artwork = globally_sourced_artwork or set()
        poster_sourced = poster_sourced or {}
        log_info(LogTags.CLEANUP, f"Scanning {root} ...")
        try:
            assets_dict, prefix_index = get_assets_files([root], merge=False)
        except Exception as exc:
            log_error(LogTags.CLEANUP, f"Failed to scan {root}: {exc}", root=root, error=str(exc))
            return

        if not assets_dict:
            log_debug(LogTags.CLEANUP, f"No assets found in {root}")
            return

        groups, orphans = self._group_assets_to_media(media_dict, assets_dict, prefix_index)

        # A flat series main poster/artwork ('Show (2020).jpg', 'Show (2020) - logo.png') has
        # no season marker or tvdb id, so it classifies as 'movies' and a down Sonarr would
        # delete it. Only guard against that when the destination shows the user actually has
        # series (a confidently-typed series asset), so movie-only libraries aren't affected.
        has_series_evidence = any(str(a.get("type")) == "series" for a in assets_dict)

        to_remove: List[Tuple[Dict[str, Any], str]] = []  # (asset, reason)

        # ── Stale duplicate resolution ─────────────────────────────────────
        # Compute keepers first (canonical folder, or all when none is canonical),
        # so an asset that is canonical/sole in any group is never removed.
        keepers: set[int] = set()
        group_stale: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []  # (asset, media)
        for group in groups:
            media = group["media"]
            assets = group["assets"]
            # Only nested folders participate in folder-level dedup.
            nested = [a for a in assets if self._asset_folder_path(a) is not None]
            if len(nested) < 2:
                for asset in assets:
                    keepers.add(id(asset))
                continue
            canonical_names = {n.lower() for n in self._canonical_names(media)}
            canonical = [a for a in nested if self._asset_folder_name(a).lower() in canonical_names]
            if not canonical:
                # No folder matches the current name — keep all, report, never guess.
                for asset in assets:
                    keepers.add(id(asset))
                log_debug(
                    LogTags.CLEANUP,
                    f"Duplicate folders for '{media.get('title')}' but none match current name "
                    f"'{self._canonical_name(media)}' — keeping all",
                )
                continue
            for asset in canonical:
                keepers.add(id(asset))
            for asset in nested:
                if id(asset) not in {id(c) for c in canonical}:
                    group_stale.append((asset, media))

        for asset, media in group_stale:
            if id(asset) in keepers:
                continue  # canonical/sole keeper in some other group
            if str(asset.get("type")) in unsafe_types:
                continue
            to_remove.append((asset, f"stale duplicate of '{media.get('title')}' (current: {self._canonical_name(media)})"))

        # ── Orphan classification ──────────────────────────────────────────
        for asset in orphans:
            name = self._asset_folder_name(asset) or os.path.basename(asset.get("files", [""])[0])
            if self._normalize_for_ignore(name) in ignore_set:
                log_debug(LogTags.CLEANUP, f"Ignored (config): {name}")
                continue
            asset_type = str(asset.get("type") or "")
            if asset_type in unsafe_types:
                continue
            # 'movies' is indistinguishable from a series whose source is down (see above).
            if asset_type == "movies" and "series" in unsafe_types and has_series_evidence:
                continue
            if self._is_confident_orphan(asset) or (delete_unknown and self._asset_folder_path(asset) is not None):
                to_remove.append((asset, "orphan (no matching media)"))
            else:
                result["unknown_kept"].append(self._describe(asset))
                result["counts"]["unknown_kept"] += 1
                log_info(LogTags.CLEANUP, f"  ? Kept for review (no match, low confidence): {name}")

        # ── Execute removals ───────────────────────────────────────────────
        for asset, reason in to_remove:
            is_stale = reason.startswith("stale")
            entry = self._describe(asset, reason=reason)
            label = "Would remove" if dry_run else "Removing"
            log_info(LogTags.CLEANUP, f"  {label}: {entry['name']}  ({entry['size']}) — {reason}")
            removed_ok = True
            if not dry_run:
                removed_ok = self._delete_asset(asset)
            if removed_ok:
                result["removed"].append(entry)
                if is_stale:
                    result["counts"]["removed_stale"] += 1
                else:
                    result["counts"]["removed_orphans"] += 1

        result["counts"]["kept"] += len(keepers)

        if globally_sourced_artwork:
            self._prune_orphaned_artwork(
                groups, root, artwork_sourced, globally_sourced_artwork, ignore_set,
                dry_run=dry_run, result=result,
            )

        # Non-empty means the poster drive scan/match succeeded — an empty map is a down
        # source (or no drives) and must never mass-delete.
        if poster_sourced:
            self._prune_unsourced_posters(
                groups, poster_sourced, ignore_set, dry_run=dry_run, result=result,
            )

    def _remove_orphan_artwork(self, file_path: str, atype: str, *, item: str, dry_run: bool, result: Dict[str, Any],
                               why: str = "source removed from drives") -> None:
        fname = os.path.basename(file_path)
        reason = f"orphaned {atype} ({why})"
        log_info(LogTags.CLEANUP, f"  {'Would remove' if dry_run else 'Removing'}: {item} — {fname} — {reason}")
        if not dry_run:
            try:
                os.remove(file_path)
            except OSError as exc:
                log_warning(LogTags.CLEANUP, f"Failed to remove orphaned artwork {file_path}: {exc}")
                return
        result["removed"].append({"name": fname, "path": file_path, "reason": reason})
        result["counts"]["removed_artwork"] += 1

    @staticmethod
    def _stale_artwork_why(fname: str, exts: Optional[set]) -> Optional[str]:
        """Why a placed artwork file of a globally-sourced type should be removed, or None to
        keep it. ``exts`` is what the item's sources produce for that type (None = type not
        sourced for this item). Placement copies the source extension, so a sourced type with
        a non-matching extension is a stale leftover (e.g. background.png beside the live
        background.jpg — or minted by an outside tool like Kometa's dimensional_asset_rename)."""
        if exts is None:
            return "source removed from drives"
        if os.path.splitext(fname)[1].lower() not in exts:
            return f"stale extension; drives source {'/'.join(sorted(exts))}"
        return None

    def _prune_orphaned_artwork(
        self,
        groups: List[Dict[str, Any]],
        destination_dir: str,
        artwork_sourced: Dict[int, Dict[str, set]],
        globally_sourced: set,
        ignore_set: set[str],
        *,
        dry_run: bool,
        result: Dict[str, Any],
    ) -> None:
        """Remove artwork the drives no longer provide — a type nothing sources anymore, or a
        wrong-extension leftover for a type they still do — in both nested (item folder) and
        flat (``{name} - {type}.ext`` at the root) layouts. Only matched (live-media)
        folders/items are visited, so a down media source is never touched; globally_sourced
        gates per type so an empty source can't mass-delete."""
        # A folder/name can match several media; union what any of them source (type -> source
        # extensions) so a file some matched item still provides is never deleted.
        def _merge(dst: Dict[str, set], src: Dict[str, set]) -> None:
            for atype, exts in src.items():
                dst.setdefault(atype, set()).update(exts)

        provided_by_asset: Dict[int, Dict[str, set]] = {}   # nested: id(asset) -> type -> exts
        assets_by_id: Dict[int, Dict[str, Any]] = {}
        provided_by_base: Dict[str, Dict[str, set]] = {}    # flat: canonical name -> type -> exts
        for group in groups:
            provided = artwork_sourced.get(id(group["media"]), {})
            for canonical in self._canonical_names(group["media"]):
                _merge(provided_by_base.setdefault(canonical, {}), provided)
            for asset in group["assets"]:
                _merge(provided_by_asset.setdefault(id(asset), {}), provided)
                assets_by_id[id(asset)] = asset

        # ── Nested: scan each matched item folder (artwork is filtered out of asset["files"]).
        for asset_id, asset in assets_by_id.items():
            folder_path = self._asset_folder_path(asset)
            if not folder_path:
                continue
            folder_name = self._asset_folder_name(asset)
            if folder_name and self._normalize_for_ignore(folder_name) in ignore_set:
                continue
            provided = provided_by_asset.get(asset_id, {})
            try:
                entries = os.listdir(folder_path)
            except OSError:
                continue  # folder already gone (e.g. removed as a stale duplicate)
            for fname in entries:
                atype = artwork_type_of(fname)  # internal type, or None for non-artwork
                if atype is None or atype not in globally_sourced:
                    continue
                why = self._stale_artwork_why(fname, provided.get(atype))
                if why is None:
                    continue
                file_path = os.path.join(folder_path, fname)
                if os.path.isfile(file_path):
                    self._remove_orphan_artwork(file_path, atype, item=folder_name or os.path.basename(folder_path), dry_run=dry_run, result=result, why=why)

        # ── Flat: reconcile "{canonical}-square.ext" files at the destination root.
        try:
            root_entries = os.listdir(destination_dir)
        except OSError:
            root_entries = []
        for fname in root_entries:
            atype = artwork_type_of(fname)
            if not atype or atype not in globally_sourced:
                continue
            base = artwork_flat_base(fname)
            if base is None or self._normalize_for_ignore(base) in ignore_set:
                continue
            provided = provided_by_base.get(base)  # None → not a matched healthy item; leave it
            if provided is None:
                continue
            why = self._stale_artwork_why(fname, provided.get(atype))
            if why is None:
                continue
            file_path = os.path.join(destination_dir, fname)
            if os.path.isfile(file_path):
                self._remove_orphan_artwork(file_path, atype, item=base, dry_run=dry_run, result=result, why=why)

    @staticmethod
    def _stale_poster_why(fname: str, exts: Optional[set]) -> Optional[str]:
        """Why a placed poster/season file should be removed, or None to keep it. ``exts`` is
        what the item's sources produce for that slot (None = slot not sourced). Placement
        copies the source extension, so a sourced slot with a non-matching extension is a stale
        leftover (e.g. poster.png beside the live poster.jpg) — the poster mirror of
        ``_stale_artwork_why``. The border replacer preserves the filename, so a bordered
        destination file still carries its source extension."""
        if exts is None:
            return "source removed from drives"
        if os.path.splitext(fname)[1].lower() not in exts:
            return f"stale extension; drives source {'/'.join(sorted(exts))}"
        return None

    def _remove_unsourced_poster(self, file_path: str, slot, *, item: str, dry_run: bool, result: Dict[str, Any],
                                 why: str = "source removed from drives") -> None:
        fname = os.path.basename(file_path)
        slot_desc = "poster" if slot == "poster" else ("specials poster" if slot == 0 else f"season {slot} poster")
        reason = f"unsourced {slot_desc} ({why})"
        log_info(LogTags.CLEANUP, f"  {'Would remove' if dry_run else 'Removing'}: {item} — {fname} — {reason}")
        if not dry_run:
            try:
                os.remove(file_path)
            except OSError as exc:
                log_warning(LogTags.CLEANUP, f"Failed to remove unsourced poster {file_path}: {exc}")
                return
            self._purge_db_rows(file_path, like=False)
        result["removed"].append({"name": fname, "path": file_path, "reason": reason})
        result["counts"]["removed_posters"] += 1

    def _prune_unsourced_posters(
        self,
        groups: List[Dict[str, Any]],
        poster_sourced: Dict[int, Dict[Any, set]],
        ignore_set: set[str],
        *,
        dry_run: bool,
        result: Dict[str, Any],
    ) -> None:
        """Remove destination poster/season files the subscribed drives no longer provide — a
        slot nothing sources anymore, or a wrong-extension leftover for a slot they still do.
        The poster mirror of the artwork prune (the typical cause: a drive was unsubscribed but
        its placed posters remained, so items still looked covered). Only matched (live-media)
        assets are visited, per slot, so another drive's files and still-sourced seasons stay."""
        # A folder/name can match several media; union what any of them source (slot -> source
        # extensions) so a file some matched item still provides is never deleted.
        provided_by_asset: Dict[int, Dict[Any, set]] = {}
        assets_by_id: Dict[int, Dict[str, Any]] = {}
        for group in groups:
            provided = poster_sourced.get(id(group["media"]), {})
            for asset in group["assets"]:
                merged = provided_by_asset.setdefault(id(asset), {})
                for slot, exts in provided.items():
                    merged.setdefault(slot, set()).update(exts)
                assets_by_id[id(asset)] = asset

        for asset_id, asset in assets_by_id.items():
            folder_name = self._asset_folder_name(asset)
            if folder_name and self._normalize_for_ignore(folder_name) in ignore_set:
                continue
            provided = provided_by_asset.get(asset_id, {})
            for file_path in asset.get("files") or []:
                fname = os.path.basename(file_path)
                if artwork_type_of(fname) is not None:
                    continue  # the artwork reconciliation's concern
                # Ignore-list identity: the item folder (nested) or the season-stripped
                # flat base ('Show (2020)_Season01' → 'Show (2020)').
                base = folder_name or season_pattern.split(os.path.splitext(fname)[0])[0].strip()
                if not folder_name and base and self._normalize_for_ignore(base) in ignore_set:
                    continue
                slot = poster_slot_of(fname)
                why = self._stale_poster_why(fname, provided.get(slot))
                if why is None:
                    continue
                if os.path.isfile(file_path):
                    self._remove_unsourced_poster(file_path, slot, item=base or fname, dry_run=dry_run, result=result, why=why)

    # ── deletion + db reconciliation ───────────────────────────────────────

    def _delete_asset(self, asset: Dict[str, Any]) -> bool:
        """Delete the asset's folder (nested) or file group (flat), then purge DB rows."""
        folder_path = self._asset_folder_path(asset)
        try:
            if folder_path:
                self._purge_db_rows(folder_path)
                shutil.rmtree(folder_path)
            else:
                for file_path in asset.get("files", []):
                    self._purge_db_rows(file_path, like=False)
                    try:
                        os.remove(file_path)
                    except FileNotFoundError:
                        pass
            return True
        except Exception as exc:
            log_error(LogTags.CLEANUP, f"Failed to delete {folder_path or asset.get('files')}: {exc}")
            return False

    def _purge_db_rows(self, path: str, like: bool = True) -> None:
        """Delete Poster / PlexUploadRecord rows for a deleted destination path."""
        try:
            for model in (Poster, PlexUploadRecord):
                query = self.db.query(model)
                if like:
                    pattern = path.rstrip("/") + "/%"
                    query = query.filter(model.file_path.like(pattern))
                else:
                    query = query.filter(model.file_path == path)
                query.delete(synchronize_session=False)
            self.db.commit()
        except Exception as exc:
            log_warning(LogTags.CLEANUP, f"Failed to purge DB rows for {path}: {exc}")
            self.db.rollback()

    def _sweep_empty_dirs(self, destination_dir: str) -> int:
        """Remove empty directories under destination, skipping root and tmp."""
        removed = 0
        skip = {os.path.abspath(destination_dir), os.path.abspath(os.path.join(destination_dir, "tmp"))}
        for current_root, dirs, _files in os.walk(destination_dir, topdown=False):
            for dir_name in dirs:
                dir_path = os.path.join(current_root, dir_name)
                if os.path.abspath(dir_path) in skip:
                    continue
                try:
                    if not os.listdir(dir_path):
                        os.rmdir(dir_path)
                        removed += 1
                        log_debug(LogTags.CLEANUP, f"Swept empty folder: {dir_name}")
                except OSError:
                    pass
        return removed

    # ── reporting ──────────────────────────────────────────────────────────

    def _describe(self, asset: Dict[str, Any], reason: str = "") -> Dict[str, Any]:
        folder_path = self._asset_folder_path(asset)
        name = self._asset_folder_name(asset) or (
            os.path.basename(asset["files"][0]) if asset.get("files") else "?"
        )
        path = folder_path or (asset.get("files", [""])[0])
        return {
            "name": name,
            "type": asset.get("type"),
            "year": asset.get("year"),
            "path": path,
            "size": self._entry_size(path) if path else "?",
            "reason": reason,
        }

    @staticmethod
    def _normalize_for_ignore(name: str) -> str:
        name = name.lower()
        name = re.sub(r"&", "and", name)
        name = re.sub(r"[^\w\s]", "", name)
        return re.sub(r"\s+", " ", name).strip()

    def _empty_result(self, dry_run: bool) -> Dict[str, Any]:
        return {
            "dry_run": dry_run,
            "skipped_for_safety": False,
            "skipped_types": [],
            "removed": [],
            "unknown_kept": [],
            "counts": {
                "kept": 0,
                "removed_orphans": 0,
                "removed_stale": 0,
                "removed_artwork": 0,
                "removed_posters": 0,
                "unknown_kept": 0,
                "empty_swept": 0,
            },
            "error": None,
            "last_run": datetime.now(timezone.utc).isoformat(),
        }

    def _persist_stats(self, result: Dict[str, Any]) -> None:
        try:
            payload = dict(result)
            payload["last_run"] = datetime.now(timezone.utc).isoformat()
            upsert_setting(self.db, "poster_cleanup_stats", json.dumps(payload))
            self.db.commit()
        except Exception as exc:
            log_warning(LogTags.CLEANUP, f"Failed to persist cleanup stats: {exc}")
            self.db.rollback()
