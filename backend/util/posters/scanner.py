import datetime
import html
import os
import re
from collections import defaultdict
from typing import Dict, List, Optional

from unidecode import unidecode

from core.logging import log_debug, log_error, log_info, LogTags
from util.constants import (
    illegal_chars_regex,
    remove_special_chars,
    season_pattern,
    unknown_year_regex,
    year_regex,
)
from util.data.construct import create_collection, create_movie, create_series
from util.data.extract import extract_ids, extract_year
from util.data.normalization import normalize_titles

# Artwork files placed alongside posters; poster consumers (e.g. Unmatched) exclude these.
# Square art is written as 'square' — the only squareart spelling Kometa detects case-sensitively.
ARTWORK_TYPE_TO_NAME: Dict[str, str] = {"logo": "logo", "background": "background", "squareart": "square"}

# On-disk basename → internal type, for recognising placed files.
_ARTWORK_NAME_TO_TYPE: Dict[str, str] = {name: atype for atype, name in ARTWORK_TYPE_TO_NAME.items()}
ARTWORK_STEMS = set(_ARTWORK_NAME_TO_TYPE)


def artwork_type_of(filename: str) -> Optional[str]:
    """Return the internal artwork type ('logo'|'background'|'squareart') for an artwork
    file, else None. Nested ('square.png') and flat ('Title (Year) {tmdb-1}-square.png')."""
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    if stem in _ARTWORK_NAME_TO_TYPE:
        return _ARTWORK_NAME_TO_TYPE[stem]
    for name, atype in _ARTWORK_NAME_TO_TYPE.items():
        if stem.endswith(f"-{name}"):
            return atype
    return None


def artwork_flat_base(filename: str) -> Optional[str]:
    """For flat artwork '{base}-square.ext' return {base}, else None (original case)."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    low = stem.lower()
    for name in _ARTWORK_NAME_TO_TYPE:
        if low.endswith(f"-{name}"):
            return stem[: -len(f"-{name}")]
    return None


def is_artwork_file(filename: str) -> bool:
    """True for artwork (logo/background/squareart), nested ('square.png') or flat
    ('Title (Year) {tmdb-1}-square.png') form."""
    return artwork_type_of(filename) is not None


def scan_files_in_flat_folder(folder_path: str, exclude_artwork: bool = False, artwork_out: Optional[Dict[str, List[str]]] = None) -> List[Dict]:
    """Scan a flat directory structure (no subfolders) for media assets.

    Args:
      folder_path (str): Path to the folder containing files.
      exclude_artwork (bool): Skip Artwork Renamer files when building poster groups.
      artwork_out (dict|None): When given, collect placed artwork item names per type in the
        same walk (parallel to scan_destination_artwork).

    Returns:
      List[Dict]: List of parsed media asset dictionaries.
    """
    try:
        files = os.listdir(folder_path)
    except FileNotFoundError:
        return []
    except Exception as exc:
        import traceback
        log_error(LogTags.SCANNER, f"Unexpected error listing files in folder {folder_path}: {exc}", folder=folder_path, error=str(exc), traceback=traceback.format_exc())
        return []

    groups = defaultdict(list)
    normalized_map = {}
    assets_dict = []

    for file in files:
        try:
            if re.match(r"^\.[^.]", file):
                continue
            if artwork_out is not None:
                atype = artwork_type_of(file)
                if atype:
                    base = artwork_flat_base(file)
                    if base:
                        artwork_out[atype].append(base)
            if exclude_artwork and is_artwork_file(file):
                continue
            title = file.rsplit(".", 1)[0]
            title = unidecode(html.unescape(title))
            title = re.sub(illegal_chars_regex, "", title)
            raw_title = season_pattern.split(title)[0].strip()
            normalized_title = remove_special_chars.sub("", raw_title.lower())
            if normalized_title in normalized_map:
                match_key = normalized_map[normalized_title]
                groups[match_key].append(file)
            else:
                groups[raw_title].append(file)
                normalized_map[normalized_title] = raw_title
        except Exception as exc:
            import traceback
            log_error(LogTags.SCANNER, f"Error processing file '{file}' in folder {folder_path}: {exc}", file=file, folder=folder_path, error=str(exc), traceback=traceback.format_exc())
            continue

    groups = dict(sorted(groups.items(), key=lambda x: x[0].lower()))

    for base_name, files in groups.items():
        try:
            assets_dict.append(parse_file_group(folder_path, base_name, files))
        except Exception as exc:
            import traceback
            log_error(LogTags.SCANNER, f"Error parsing file group '{base_name}' in folder {folder_path}: {exc}", base_name=base_name, folder=folder_path, error=str(exc), traceback=traceback.format_exc())
            continue

    return assets_dict


def scan_files_in_nested_folders(folder_path: str, exclude_artwork: bool = False, artwork_out: Optional[Dict[str, List[str]]] = None) -> Optional[List[Dict]]:
    """Scan a directory with subfolders representing grouped assets (e.g., per movie/series).

    Args:
      folder_path (str): Path to the base folder.
      exclude_artwork (bool): Skip Artwork Renamer files when building poster groups.
      artwork_out (dict|None): When given, collect placed artwork item names per type in the
        same walk (parallel to scan_destination_artwork), so a single traversal feeds both the
        poster index and the artwork indexes.

    Returns:
      Optional[List[Dict]]: List of parsed asset dictionaries from subfolders, or None on error.
    """
    assets_dict = []
    try:
        entries = list(os.scandir(folder_path))

        for dir_entry in entries:
            if (
                not dir_entry.is_dir()
                or dir_entry.name.startswith(".")
                or dir_entry.name == "tmp"
            ):
                continue
            base_name = os.path.basename(dir_entry.path)
            try:
                # One pass: collect placed artwork per type (extension-agnostic, parallel to
                # scan_destination_artwork) AND the poster image files (hidden excluded).
                files = []
                present_artwork: set = set()
                for f in os.scandir(dir_entry.path):
                    if not f.is_file():
                        continue
                    if artwork_out is not None:
                        atype = artwork_type_of(f.name)
                        if atype:
                            present_artwork.add(atype)
                    if (
                        not f.name.startswith('.')
                        and os.path.splitext(f.name)[1].lower() in ['.jpg', '.jpeg', '.png']
                        and not (exclude_artwork and is_artwork_file(f.name))
                    ):
                        files.append(f.name)
            except Exception as exc:
                import traceback
                log_error(LogTags.SCANNER, f"Failed to scan nested folder {dir_entry.path}: {exc}", path=dir_entry.path, error=str(exc), traceback=traceback.format_exc())
                continue
            # Record artwork before the poster-only empty check, so artwork-only folders count.
            if artwork_out is not None:
                for t in present_artwork:
                    artwork_out[t].append(base_name)
            if not files:
                log_debug(LogTags.SCANNER, f"Skipping empty folder: {dir_entry.path}", path=dir_entry.path)
                continue
            try:
                assets_dict.append(parse_folder_group(dir_entry.path, base_name, files))
            except Exception as exc:
                import traceback
                log_error(LogTags.SCANNER, f"Failed to parse folder group {dir_entry.path}: {exc}", path=dir_entry.path, error=str(exc), traceback=traceback.format_exc())
                continue
    except Exception as exc:
        import traceback
        log_error(LogTags.SCANNER, f"Error scanning folder {folder_path}: {exc}", folder=folder_path, error=str(exc), traceback=traceback.format_exc())
        return None
    return assets_dict


def parse_folder_group(folder_path: str, base_name: str, files: List[str]) -> Dict:
    """Parse metadata and build a structured dictionary for assets within a folder.

    Args:
      folder_path (str): Path to the folder.
      base_name (str): Base name of the folder.
      files (List[str]): List of file names.

    Returns:
      Dict: Structured asset dictionary.
    """
    try:
        title = re.sub(year_regex, "", base_name)
        title = unknown_year_regex.sub("", title)
        title = unidecode(html.unescape(title))
        year = extract_year(base_name)
        tmdb_id, tvdb_id, imdb_id = extract_ids(base_name)
        normalized_title = normalize_titles(base_name)
        full_paths = sorted(
            [
                os.path.join(folder_path, file)
                for file in files
                if not file.startswith(".")
            ]
        )
        parent_folder = os.path.basename(folder_path)

        if not full_paths:
            raise ValueError("No valid files found in folder")

        is_series = len(files) > 1 and any(
            "Season" in os.path.basename(file) for file in files
        )
        is_collection = not year

        if is_collection:
            return create_collection(
                title, tmdb_id, normalized_title, full_paths, parent_folder
            )
        if is_series or tvdb_id:
            return create_series(
                title,
                year,
                tvdb_id,
                imdb_id,
                normalized_title,
                full_paths,
                parent_folder,
                tmdb_id=tmdb_id,
            )
        return create_movie(
            title, year, tmdb_id, imdb_id, normalized_title, full_paths, parent_folder
        )
    except Exception as exc:
        raise ValueError(
            f"Error parsing folder group. Folder: {folder_path}, Base name: {base_name}, Exception: {exc}"
        )


def parse_file_group(folder_path: str, base_name: str, files: List[str]) -> Dict:
    """Parse a group of files in a flat folder into structured metadata.

    Args:
      folder_path (str): Path to the containing folder.
      base_name (str): Group title.
      files (List[str]): List of file names.

    Returns:
      Dict: Structured media dictionary.
    """
    try:
        id_cleaned_name = re.sub(r"\{(?:tmdb|tvdb|imdb)-\w+\}", "", base_name).strip()
        title = re.sub(year_regex, "", id_cleaned_name).strip()
        title = unknown_year_regex.sub("", title).strip()
        title = unidecode(html.unescape(title))
        year = extract_year(base_name)
        tmdb_id, tvdb_id, imdb_id = extract_ids(base_name)
        normalized_title = normalize_titles(base_name)
        files = sorted(
            [
                os.path.join(folder_path, file)
                for file in files
                if not re.match(r"^\.[^.]", file)
            ]
        )
        is_series = any(season_pattern.search(file) for file in files)
        is_collection = not year
        non_season_file = next((f for f in files if not season_pattern.search(f)), None)
        if non_season_file:
            media_folder = os.path.splitext(os.path.basename(non_season_file))[0]
        else:
            media_folder = (
                os.path.splitext(os.path.basename(files[0]))[0] if files else ""
            )

        if is_collection:
            return create_collection(
                title,
                tmdb_id,
                normalized_title,
                files,
                parent_folder=None,
                media_folder=media_folder,
            )
        if is_series or tvdb_id:
            return create_series(
                title,
                year,
                tvdb_id,
                imdb_id,
                normalized_title,
                files,
                parent_folder=None,
                media_folder=media_folder,
                tmdb_id=tmdb_id,
            )
        return create_movie(
            title,
            year,
            tmdb_id,
            imdb_id,
            normalized_title,
            files,
            parent_folder=None,
            media_folder=media_folder,
        )
    except Exception as exc:
        raise ValueError(
            f"Error parsing file group. Folder: {folder_path}, Base name: {base_name}, Exception: {exc}"
        )


def process_files(folder_path: str, exclude_artwork: bool = False, artwork_out: Optional[Dict[str, List[str]]] = None) -> Optional[List[Dict]]:
    """Determine folder structure and route to the appropriate scanning logic.

    Args:
      folder_path (str): Path to the folder to scan.
      exclude_artwork (bool): Skip Artwork Renamer files (logo/background/squareart) so
        poster consumers scanning the shared destination don't count artwork as posters.
      artwork_out (dict|None): When given, collect placed artwork item names per type in the
        same walk, so one traversal feeds both poster and artwork indexes.

    Returns:
      Optional[List[Dict]]: List of structured asset dictionaries, or None on failure.
    """
    asset_folders = _is_asset_folders(folder_path)
    log_debug(LogTags.SCANNER, f"Folder Path: {folder_path} | Asset Folder: {asset_folders}", folder=folder_path, is_asset_folder=asset_folders)
    start_time = datetime.datetime.now()

    if not asset_folders:
        assets_dict = scan_files_in_flat_folder(folder_path, exclude_artwork=exclude_artwork, artwork_out=artwork_out)
    else:
        assets_dict = scan_files_in_nested_folders(folder_path, exclude_artwork=exclude_artwork, artwork_out=artwork_out)

    end_time = datetime.datetime.now()
    if assets_dict:
        elapsed_time = (end_time - start_time).total_seconds()
        item_count = (
            sum(len(asset.get("files", [])) for asset in assets_dict)
            if assets_dict
            else 0
        )
        items_per_second = item_count / elapsed_time if elapsed_time > 0 else 0
        log_info(LogTags.SCANNER, 
                f"Processed {item_count} files in {elapsed_time:.2f} seconds ({items_per_second:.2f} items/s) "
                f"in folder '{os.path.basename(folder_path.rstrip('/'))}' ",
                file_count=item_count, elapsed_sec=f"{elapsed_time:.2f}", items_per_sec=f"{items_per_second:.2f}", folder=os.path.basename(folder_path.rstrip('/')))
        return assets_dict
    return None


def _is_asset_folders(folder_path: str) -> bool:
    """Check if the folder contains asset folders.

    Args:
      folder_path (str): The path to the folder to check.

    Returns:
      bool: True if the folder contains asset folders, False otherwise.
    """
    try:
        if not os.path.exists(folder_path):
            return False
        for item in os.listdir(folder_path):
            if (
                (len(item) > 1 and item[0] == "." and item[1] != ".")
                or item.startswith("@")
                or item == "tmp"
            ):
                log_debug(LogTags.SCANNER, f"Skipping hidden item: {item}", item=item)
                continue
            if os.path.isdir(os.path.join(folder_path, item)):
                return True
        return False
    except Exception as exc:
        log_error(LogTags.SCANNER, f"Error checking asset folders in {folder_path}: {exc}", folder=folder_path, error=str(exc))
        return False
