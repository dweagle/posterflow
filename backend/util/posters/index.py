from typing import Any, Dict, List, Optional

from core.logging import log_info, LogTags
from util.constants import common_words
from util.data.normalization import normalize_titles

Asset = Dict[str, Any]
PrefixIndex = Dict[str, List[Asset]]

prefix_length: int = 3


def create_new_empty_index() -> PrefixIndex:
    """Create and return an empty search index structure.

    Returns:
        PrefixIndex: An empty dictionary.
    """
    return {}


def remove_common_words(text: str) -> str:
    """Remove any word that matches an entry in common_words (case-insensitive).

    Only removes complete words, does not touch substrings or special characters.

    Args:
        text (str): Input text to filter.

    Returns:
        str: Text with common words removed.
    """
    words = text.split()
    filtered = [
        word for word in words if word.lower() not in {w.lower() for w in common_words}
    ]
    return " ".join(filtered)


def build_search_index(
    prefix_index: PrefixIndex,
    title: str,
    asset: Asset,
    debug_items: Optional[List[str]] = None,
) -> None:
    """Populate the search index with normalized forms of the asset title and TMDB/TVDB IDs.

    Args:
        prefix_index (PrefixIndex): The overall index to update.
        title (str): Original title to normalize and index.
        asset (Asset): Dictionary containing asset metadata.
        debug_items (Optional[List[str]]): List of normalized titles to enable debug logging on.
    """
    title = remove_common_words(title)
    processed = normalize_titles(title)
    debug_build_index = bool(
        debug_items and len(debug_items) > 0 and processed in debug_items
    )

    if debug_build_index:
        log_info(LogTags.SCANNER, "debug_build_search_index", processed=processed, asset=asset)

    if asset.get("tmdb_id"):
        key = f"tmdb:{asset.get('tmdb_id')}"
        prefix_index.setdefault(key, []).append(asset)
        if debug_build_index:
            log_info(LogTags.SCANNER, f"Indexed by {key}", key=key)

    if asset.get("tvdb_id"):
        key = f"tvdb:{asset.get('tvdb_id')}"
        prefix_index.setdefault(key, []).append(asset)
        if debug_build_index:
            log_info(LogTags.SCANNER, f"Indexed by {key}", key=key)

    words = processed.split()
    if debug_build_index:
        log_info(LogTags.SCANNER, "Words for indexing", words=words)

    for word in words:
        prefix_index.setdefault(word, []).append(asset)
        if len(word) > prefix_length:
            prefix = word[:prefix_length]
            if debug_build_index:
                log_info(LogTags.SCANNER, "Using prefix", prefix=prefix)
            prefix_index.setdefault(prefix, []).append(asset)
        break


def search_matches(
    prefix_index: PrefixIndex,
    title: str,
    tmdb_id: Optional[int] = None,
    tvdb_id: Optional[int] = None,
) -> List[Asset]:
    """Search for matching assets in the index.

    If a TMDB or TVDB ID is provided, search strictly by that ID and return results.
    Only perform title-based search if neither ID is provided.

    Args:
        prefix_index (PrefixIndex): The populated search index.
        title (str): The title to search for.
        tmdb_id (Optional[int]): TMDB ID for direct lookup.
        tvdb_id (Optional[int]): TVDB ID for direct lookup.

    Returns:
        List[Asset]: List of matching assets from the index.
    """
    if tmdb_id is not None:
        key = f"tmdb:{tmdb_id}"
        filtered_assets = [
            a for a in prefix_index.get(key, []) if a.get("tmdb_id") == tmdb_id
        ]
        return filtered_assets
    if tvdb_id is not None:
        key = f"tvdb:{tvdb_id}"
        filtered_assets = [
            a for a in prefix_index.get(key, []) if a.get("tvdb_id") == tvdb_id
        ]
        return filtered_assets

    title = remove_common_words(title)
    processed_title = normalize_titles(title)
    words = processed_title.split()
    matches: List[Asset] = []
    for word in words:
        if len(word) > prefix_length:
            prefix = word[:prefix_length]
            if prefix in prefix_index:
                matches.extend(prefix_index[prefix])
                return matches
        if word in prefix_index:
            matches.extend(prefix_index[word])
        break
    return matches
