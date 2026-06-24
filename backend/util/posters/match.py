import os
import re
from typing import Any, Dict, List, Optional, Tuple

from core.logging import log_debug, LogTags
from util.constants import folder_year_regex
from util.posters.index import search_matches
from util.data.normalization import normalize_titles


def media_source_refs(media: Dict[str, Any]) -> Dict[str, Any]:
    """Authoritative external IDs + poster carried from the Plex/*arr source.

    Surfaced on matched/unmatched items so a published community card uses real
    IDs (and a poster preview) instead of guessing via TMDB title search — never
    from the poster filename. Series tmdb lives on tmdb_id_ref (kept off the
    matcher), so fall back to it here for display.
    """
    mtype = media.get("type")
    if mtype == "movies":
        available = bool(media.get("has_file", False))
    elif mtype == "series":
        available = bool(media.get("has_episodes", False))
    else:
        available = None  # collections (Plex) have no arr file concept
    return {
        "tmdb_id": media.get("tmdb_id") or media.get("tmdb_id_ref"),
        "tvdb_id": media.get("tvdb_id"),
        "imdb_id": media.get("imdb_id"),
        "poster_url": media.get("poster_url"),
        # False = tracked in Sonarr/Radarr but not downloaded ("Missing in Arr").
        "available": available,
    }


def compare_strings(string1: str, string2: str) -> bool:
    """Loosely compare two strings by removing non-alphanumeric characters and comparing lowercase."""
    string1 = re.sub(r"\W+", "", string1)
    string2 = re.sub(r"\W+", "", string2)
    return string1.lower() == string2.lower()


_COLLECTION_SUFFIXES = (" collection", " collections")


def collection_title_variants(title: str) -> List[str]:
    """Return title variants for collection suffix matching.

    Source drives often name collections "X Collection" while the destination
    folder may be plain "X" (or vice versa).  Returns a list that covers both
    naming conventions so callers can search under either form.

    Examples:
        "Avengers Collection" → ["Avengers Collection", "Avengers"]
        "Avengers"            → ["Avengers", "Avengers Collection"]
    """
    variants = [title]
    for suffix in _COLLECTION_SUFFIXES:
        if title.lower().endswith(suffix):
            stripped = title[: len(title) - len(suffix)].strip()
            if stripped:
                variants.append(stripped)
            return variants
    # No suffix present — add the "Collection" variant
    variants.append(f"{title} Collection")
    return variants


def match_tmdb_collection(
    plex_title: str, tmdb_results: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Exact-name match a Plex collection against TMDB /search/collection results.

    Plex names collections inconsistently ("Star Wars" vs "Star Wars Collection")
    while TMDB always uses the "X Collection" form. Normalize both sides and try
    each naming variant so either convention matches, but only on an exact
    normalized title — anything uncertain returns None so it stays a custom
    collection (no TMDB id).

    Args:
        plex_title: The collection title as it appears in Plex.
        tmdb_results: The "results" list from TMDB's /search/collection response.

    Returns:
        The matching TMDB result dict, or None if no exact match.
    """
    if not plex_title:
        return None
    variants = {
        normalize_titles(v) for v in collection_title_variants(plex_title) if v
    }
    variants.discard("")
    if not variants:
        return None
    for result in tmdb_results:
        if not isinstance(result, dict):
            continue
        name = result.get("name") or result.get("original_name") or ""
        if name and normalize_titles(name) in variants:
            return result
    return None


def is_match(
    asset: Dict[str, Any],
    media: Dict[str, Any],
    strict_folder_match: bool = False,
) -> Tuple[bool, str]:
    """Determine if a media entry and an asset match based on ID, title, and year heuristics.

    Args:
      asset: Asset dictionary.
      media: Media dictionary.
      strict_folder_match: Only consider match if asset's folder matches media's folder.

    Returns:
      Tuple of (True, reason) if matched, else (False, "").
    """
    if media.get("folder"):
        folder_base_name = os.path.basename(media["folder"])
        match = re.search(folder_year_regex, folder_base_name)
        if match:
            media["folder_title"], media["folder_year"] = match.groups()
            media["folder_year"] = (
                int(media["folder_year"]) if media["folder_year"] else None
            )
            media["normalized_folder_title"] = normalize_titles(media["folder_title"])

    def year_matches() -> bool:
        asset_year = asset.get("year")
        media_years = [
            media.get(key) for key in ["year", "secondary_year", "folder_year"]
        ]
        if asset_year is None and all(year is None for year in media_years):
            return True
        return any(asset_year == year for year in media_years if year is not None)

    def has_any_valid_id(d: Dict[str, Any]) -> bool:
        for k in ["tmdb_id", "tvdb_id", "imdb_id"]:
            v = d.get(k)
            if k == "imdb_id":
                if v and isinstance(v, str) and v.startswith("tt"):
                    return True
            else:
                if v and str(v).isdigit() and int(v) > 0:
                    return True
        return False

    has_asset_ids = has_any_valid_id(asset)
    has_media_ids = has_any_valid_id(media)

    if strict_folder_match:
        match_criteria = [
            (
                asset.get("media_folder") == media.get("folder"),
                "by strict folder match (media_folder)",
            ),
            (
                asset.get("folder") == media.get("folder"),
                "by strict folder match",
            ),
        ]
        for condition, reason in match_criteria:
            if condition and year_matches():
                return True, reason
        return False, ""

    if has_asset_ids and has_media_ids:
        _asset_type = asset.get("type") or ""
        _media_type = media.get("type") or ""
        tmdb_types_compatible = (
            not _asset_type
            or not _media_type
            or (_asset_type == "series") == (_media_type == "series")
        )
        id_match_criteria = [
            (
                media.get("tvdb_id")
                and asset.get("tvdb_id")
                and media.get("tvdb_id") == asset.get("tvdb_id"),
                "by tvdb_id",
            ),
            (
                media.get("tmdb_id")
                and asset.get("tmdb_id")
                and media.get("tmdb_id") == asset.get("tmdb_id")
                and tmdb_types_compatible,
                "by tmdb_id",
            ),
            (
                media.get("imdb_id")
                and asset.get("imdb_id")
                and media["imdb_id"] == asset["imdb_id"],
                "by imdb_id",
            ),
        ]
        for matched, reason in id_match_criteria:
            if matched:
                return True, reason
        return False, ""

    match_criteria = [
        (asset.get("title") == media.get("title"), "by exact title"),
        (
            asset.get("title") in media.get("alternate_titles", []),
            "by alternate title",
        ),
        (asset.get("title") == media.get("folder"), "by folder name"),
        (
            asset.get("title") == media.get("original_title"),
            "by original title",
        ),
        (
            asset.get("normalized_title") == media.get("normalized_title"),
            "by normalized title",
        ),
        (
            asset.get("normalized_title") == media.get("normalized_folder_title"),
            "by normalized folder",
        ),
        (
            asset.get("normalized_title")
            in media.get("normalized_alternate_titles", []),
            "by normalized alternate title",
        ),
        (
            any(
                assets == media.get("title")
                for assets in asset.get("alternate_titles", [])
            ),
            "by asset alternate title",
        ),
        (
            any(
                assets == media.get("normalized_title")
                for assets in asset.get("normalized_alternate_titles", [])
            ),
            "by asset normalized alternate title",
        ),
        (
            any(
                media_alt == asset.get("title")
                for media_alt in media.get("alternate_titles", [])
            ),
            "by media alternate title",
        ),
        (
            any(
                media_alt == asset.get("normalized_title")
                for media_alt in media.get("normalized_alternate_titles", [])
            ),
            "by media normalized alternate title",
        ),
        (
            compare_strings(media.get("title", ""), asset.get("title", "")),
            "by loose comparison",
        ),
        (
            compare_strings(
                media.get("normalized_title", ""), asset.get("normalized_title", "")
            ),
            "by normalized loose comparison",
        ),
    ]
    for condition, reason in match_criteria:
        if condition and year_matches():
            return True, reason
    return False, ""


def match_assets_to_media(
    media_dict: Dict[str, List[Dict[str, Any]]],
    prefix_index: Dict[str, Any],
    strict_folder_match: bool = False,
) -> Dict[str, List[Dict[str, Any]]]:
    """Match assets to media entries.

    Args:
      media_dict: Dictionary of media grouped by type.
      prefix_index: Search index for assets.
      strict_folder_match: If True, only match if folder matches.

    Returns:
      Dictionary of matched assets by type.
    """
    asset_types = ["movies", "series", "collections"]
    matched: Dict[str, List[Dict[str, Any]]] = {atype: [] for atype in asset_types}
    
    use_asset_types = [t for t in media_dict if media_dict[t] is not None]
    
    for asset_type in use_asset_types:
        if asset_type in media_dict:
            matched_dict: List[Dict[str, Any]] = []
            media_data = media_dict[asset_type]
            
            for media in media_data:
                found_match = False
                search_asset = None
                seasons = media.get("seasons") or []
                media_seasons_numbers = [
                    season["season_number"] for season in seasons
                ]
                
                tmdb_id = media.get("tmdb_id")
                tvdb_id = media.get("tvdb_id")
                candidates = []
                id_candidates = []
                
                if tmdb_id or tvdb_id:
                    id_candidates = search_matches(
                        prefix_index,
                        media.get("title", ""),
                        tmdb_id=tmdb_id,
                        tvdb_id=tvdb_id,
                    )

                    if tmdb_id:
                        id_candidates = [
                            c for c in id_candidates
                            if not c.get("type") or c.get("type") == asset_type
                        ]
                    for candidate in id_candidates:
                        is_matched, reason = is_match(
                            candidate, media, strict_folder_match
                        )
                        if is_matched:
                            log_debug(LogTags.MATCH,
                                f"✓ Matched {reason}: {media['title']} ({media['year']}) <-> {candidate['title']} ({candidate.get('year')})",
                                media_title=media['title'],
                                media_year=media['year'],
                                asset_title=candidate['title'],
                                asset_year=candidate.get('year'),
                                reason=reason
                            )
                            search_asset = candidate
                            found_match = True
                            asset_season_numbers = search_asset.get(
                                "season_numbers", None
                            )
                            if asset_season_numbers and media_seasons_numbers:
                                handle_series_match(
                                    search_asset,
                                    media_seasons_numbers,
                                    asset_season_numbers,
                                )
                            break
                
                if not found_match and not id_candidates:
                    titles_to_check = [media["title"]] + media.get(
                        "alternate_titles", []
                    )
                    for title in titles_to_check:
                        candidate_list = search_matches(
                            prefix_index, title
                        )
                        candidates.extend(candidate_list)
                    
                    type_candidates = [
                        a for a in candidates if a.get("type") == asset_type
                    ]
                    if type_candidates:
                        candidates = type_candidates
                    
                    for search_asset in candidates:
                        is_matched, reason = is_match(
                            search_asset, media, strict_folder_match
                        )
                        if is_matched:
                            log_debug(LogTags.MATCH,
                                f"✓ Matched {reason}: {media['title']} ({media['year']}) <-> {search_asset['title']} ({search_asset.get('year')})",
                                media_title=media['title'],
                                media_year=media['year'],
                                asset_title=search_asset['title'],
                                asset_year=search_asset.get('year'),
                                reason=reason
                            )
                            asset_season_numbers = search_asset.get(
                                "season_numbers", None
                            )
                            if (
                                not asset_season_numbers
                                or not media_seasons_numbers
                                or (
                                    asset_season_numbers
                                    and media_seasons_numbers
                                )
                            ):
                                found_match = True
                                if (
                                    asset_season_numbers
                                    and media_seasons_numbers
                                ):
                                    handle_series_match(
                                        search_asset,
                                        media_seasons_numbers,
                                        asset_season_numbers,
                                    )
                                break
                
                if found_match:
                    matched_dict.append(
                        {
                            "title": media["title"],
                            "year": media["year"],
                            "folder": media.get("folder"),
                            "files": search_asset["files"],
                            "seasons_numbers": (
                                search_asset.get("season_numbers", None)
                                if search_asset
                                else None
                            ),
                            "asset_ref": search_asset,
                            # Authoritative refs from the Plex/*arr record (IDs +
                            # poster) so style-fallback publishing matches exactly.
                            **media_source_refs(media),
                        }
                    )
            
            matched[asset_type] = matched_dict
    
    return matched


def handle_series_match(
    asset: Dict[str, Any],
    media_seasons_numbers: List[int],
    asset_season_numbers: List[int],
) -> None:
    """Prune asset data to remove files/seasons not present in the media entry.

    Args:
      asset: Asset dictionary with file and season data.
      media_seasons_numbers: List of seasons found in the media source.
      asset_season_numbers: List of seasons declared in the asset.
    """
    files_to_remove = []
    seasons_to_remove = []
    
    for file in asset.get("files", []):
        if re.search(r" - Season| - Specials", file):
            match = re.search(r"Season (\d+)", file)
            if match:
                season_number = int(match.group(1))
            elif "Specials" in file:
                season_number = 0
            else:
                continue
            
            if season_number not in media_seasons_numbers:
                files_to_remove.append(file)
    
    for file in files_to_remove:
        asset["files"].remove(file)
    
    for season in asset_season_numbers:
        if season not in media_seasons_numbers:
            seasons_to_remove.append(season)
    
    for season in seasons_to_remove:
        asset_season_numbers.remove(season)
