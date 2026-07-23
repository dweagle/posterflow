import os
import re
from typing import Any, Dict, List, Optional

from util.constants import prefixes, season_number_regex, suffixes
from util.data.normalization import normalize_titles


def generate_title_variants(title: str) -> Dict[str, List[str]]:
    """Generate alternate and normalized title variants for a given media title.

    Args:
        title (str): The original media title.

    Returns:
        Dict[str, List[str]]: Dictionary with 'alternate_titles' and
            'normalized_alternate_titles' keys.
    """
    stripped_prefix = next(
        (title[len(p) + 1 :].strip() for p in prefixes if title.startswith(p + " ")),
        title,
    )
    stripped_suffix = next(
        (title[: -(len(s) + 1)].strip() for s in suffixes if title.endswith(" " + s)),
        title,
    )
    stripped_both = next(
        (
            stripped_prefix[: -(len(s) + 1)].strip()
            for s in suffixes
            if stripped_prefix.endswith(" " + s)
        ),
        stripped_prefix,
    )
    alternate_titles = [stripped_prefix, stripped_suffix, stripped_both]
    if not title.lower().endswith("collection"):
        alternate_titles.append(f"{title} Collection")
    normalized_alternate_titles = [normalize_titles(alt) for alt in alternate_titles]
    alternate_titles = list(dict.fromkeys(alternate_titles))
    normalized_alternate_titles = list(dict.fromkeys(normalized_alternate_titles))
    return {
        "alternate_titles": alternate_titles,
        "normalized_alternate_titles": normalized_alternate_titles,
    }


# ── The box: a media item's files in named slots ────────────────────────────
# Instead of a flat file list whose meaning is re-derived at placement time, an
# item carries explicit slots. Item-level slots (poster/logo/background/square)
# are one file each, matched by the item and dropped in its folder; season
# posters live under `seasons`, keyed by number (0 = specials). poster/seasons
# come from the poster drives; logo/background/square from the artwork drives —
# all the same item, just different slots.
SLOT_POSTER = "poster"
SLOT_LOGO = "logo"
SLOT_BACKGROUND = "background"
SLOT_SQUARE = "square"
ITEM_SLOTS = (SLOT_POSTER, SLOT_LOGO, SLOT_BACKGROUND, SLOT_SQUARE)


SLOT_SEASON = "season"  # pseudo-slot used only for naming a season poster


def build_slots(
    poster: Optional[str] = None,
    logo: Optional[str] = None,
    background: Optional[str] = None,
    square: Optional[str] = None,
    seasons: Optional[Dict[int, str]] = None,
) -> Dict[str, Any]:
    """One media item's source files in named slots (the 'box' everything hangs off)."""
    return {
        SLOT_POSTER: poster,
        SLOT_LOGO: logo,
        SLOT_BACKGROUND: background,
        SLOT_SQUARE: square,
        "seasons": dict(seasons) if seasons else {},
    }


def slot_dest_name(slot: str, ext: str, folder: str, asset_folders: bool, season: Optional[int] = None) -> str:
    """The destination filename for a slot — the single source of truth for output naming,
    shared by every placer. Nested (asset_folders=True) drops a bare name in the item folder;
    flat prefixes the folder name with the slot's separator (poster: none, season: '_Season',
    logo/background/square: '-<slot>'). These reproduce the existing on-disk conventions.
    """
    if slot == SLOT_SEASON:
        nn = f"{int(season):02d}"
        return f"Season{nn}{ext}" if asset_folders else f"{folder}_Season{nn}{ext}"
    if slot == SLOT_POSTER:
        return f"poster{ext}" if asset_folders else f"{folder}{ext}"
    # logo / background / square keep their slot name as the base (Kometa-compatible; no spaces).
    return f"{slot}{ext}" if asset_folders else f"{folder}-{slot}{ext}"


def create_collection(
    title: str,
    tmdb_id: int,
    normalized_title: str,
    files: List[str],
    parent_folder: Optional[str] = None,
    media_folder: Optional[str] = None,
) -> Dict[str, Any]:
    """Construct a standardized dictionary representing a collection entry.

    Args:
        title (str): Display title of the collection.
        tmdb_id (int): TMDB identifier.
        normalized_title (str): Normalized version of the title.
        files (List[str]): Associated media file paths.
        parent_folder (Optional[str]): Folder containing the files.

    Returns:
        Dict[str, Any]: Dictionary with metadata fields for a collection.
    """
    variants = generate_title_variants(title)
    return {
        "type": "collections",
        "title": title,
        "year": None,
        "normalized_title": normalized_title,
        "files": [files[-1]],
        "slots": build_slots(poster=files[-1] if files else None),
        "alternate_titles": variants["alternate_titles"],
        "normalized_alternate_titles": variants["normalized_alternate_titles"],
        "tmdb_id": tmdb_id,
        "folder": parent_folder,
        "media_folder": media_folder,
    }


def create_series(
    title: str,
    year: Optional[int],
    tvdb_id: Optional[int],
    imdb_id: Optional[str],
    normalized_title: str,
    files: List[str],
    parent_folder: Optional[str] = None,
    media_folder: Optional[str] = None,
    tmdb_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Construct a standardized dictionary representing a series entry.

    Args:
        title (str): Series title.
        year (Optional[int]): Release year of the series.
        tvdb_id (Optional[int]): TVDB identifier.
        imdb_id (Optional[str]): IMDB identifier.
        normalized_title (str): Normalized version of the title.
        files (List[str]): List of associated media file paths.
        parent_folder (Optional[str]): Folder containing the files.

    Returns:
        Dict[str, Any]: Dictionary with metadata fields for a series.
    """
    season_numbers_dict = {}
    series_poster = None
    for file_path in files:
        base = os.path.basename(file_path)
        if "Specials" in base:
            season_numbers_dict[0] = file_path
        else:
            match = re.search(season_number_regex, base)
            if match:
                season_numbers_dict[int(match.group(1))] = file_path
            else:
                series_poster = file_path

    season_numbers = sorted(season_numbers_dict.keys())
    final_files = list(season_numbers_dict.values())
    if series_poster:
        final_files.append(series_poster)
    return {
        "type": "series",
        "title": title,
        "year": year,
        "tmdb_id": tmdb_id,
        "tvdb_id": tvdb_id,
        "imdb_id": imdb_id,
        "normalized_title": normalized_title,
        "files": final_files,
        "season_numbers": season_numbers,
        "slots": build_slots(poster=series_poster, seasons=season_numbers_dict),
        "folder": parent_folder,
        "media_folder": media_folder,
    }


def create_movie(
    title: str,
    year: Optional[int],
    tmdb_id: Optional[int],
    imdb_id: Optional[str],
    normalized_title: str,
    files: List[str],
    parent_folder: Optional[str] = None,
    media_folder: Optional[str] = None,
) -> Dict[str, Any]:
    """Construct a standardized dictionary representing a movie entry.

    Args:
        title (str): Movie title.
        year (Optional[int]): Release year of the movie.
        tmdb_id (Optional[int]): TMDB identifier.
        imdb_id (Optional[str]): IMDB identifier.
        normalized_title (str): Normalized version of the title.
        files (List[str]): List of associated media file paths.
        parent_folder (Optional[str]): Folder containing the files.

    Returns:
        Dict[str, Any]: Dictionary with metadata fields for a movie.
    """
    return {
        "type": "movies",
        "title": title,
        "year": year,
        "tmdb_id": tmdb_id,
        "imdb_id": imdb_id,
        "normalized_title": normalized_title,
        "files": [files[-1]],
        "slots": build_slots(poster=files[-1] if files else None),
        "folder": parent_folder,
        "media_folder": media_folder,
    }
