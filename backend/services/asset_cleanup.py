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
from util.posters.assets import get_assets_files
from util.posters.index import search_matches
from util.posters.match import collection_title_variants, is_match

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
                for candidate_title in titles_to_try:
                    if candidate_title:
                        candidates += search_matches(prefix_index, candidate_title)

                seen: set[int] = set()
                matched_assets: List[Dict[str, Any]] = []
                for candidate in candidates:
                    if id(candidate) in seen:
                        continue
                    seen.add(id(candidate))
                    if is_match(candidate, media)[0]:
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
        progress_callback: Optional[ProgressCallback] = None,
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

            media_dict = PosterRenameService(self.db).get_media_from_instances(
                log_tag=LogTags.CLEANUP, setting_key="poster_renamer_libraries"
            )

        total_media = sum(len(media_dict.get(t, [])) for t in MEDIA_TYPES)

        # ── Safety gate: never delete against an empty media set ───────────
        if total_media == 0:
            log_warning(
                LogTags.CLEANUP,
                "Aborting cleanup — no media returned from any source (safety guard). "
                "Nothing was deleted.",
            )
            result["skipped_for_safety"] = True
            log_section_end(LogTags.CLEANUP, "Asset Cleanup Skipped (safety)")
            return result

        # Per-type guard: a type whose media list is empty is treated as unhealthy,
        # so we never remove that type's folders (e.g. Radarr down but Sonarr/Plex up).
        unsafe_types = {t for t in MEDIA_TYPES if not media_dict.get(t)}
        if unsafe_types:
            log_warning(
                LogTags.CLEANUP,
                f"Skipping removals for types with no media (source down/empty): {sorted(unsafe_types)}",
                unsafe_types=sorted(unsafe_types),
            )
            result["skipped_types"] = sorted(unsafe_types)

        ignore_set = {self._normalize_for_ignore(name) for name in self._get_list_setting("asset_cleanup_ignore")}

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
                f"removed {counts['removed_orphans']} orphan(s), {counts['removed_stale']} stale duplicate(s); "
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
    ) -> None:
        """Run grouping + classification + deletion for one scan root."""
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
            canonical_name = self._canonical_name(media).lower()
            canonical = [a for a in nested if self._asset_folder_name(a).lower() == canonical_name]
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
            if str(asset.get("type")) in unsafe_types:
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
