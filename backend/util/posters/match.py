import os
import re
from typing import Any, Dict, List, Optional, Tuple

from core.logging import log_debug, log_warning, log_phase, LogTags
from util.constants import folder_year_regex
from util.posters.index import search_matches
from util.data.normalization import normalize_titles

# is_match rejection reason: titles agree but the two sides share no id, so the id lock
# skipped the title fallback. Reported rather than dropped — it's usually a tagging gap.
NO_SHARED_ID = "no shared id"

# is_match rejection reason: a title criterion passed but no year lined up. Usually the
# *arr path has no "(year)" suffix, so folder_year is unavailable to corroborate.
YEAR_MISMATCH = "year mismatch"

# is_match rejection reason: title and year agree but a shared id type disagrees. One side
# is mistagged (stale poster tag or a remapped *arr entry) — without this report the id
# lock rejects the pair completely silently.
ID_CONFLICT = "id conflict"

# Cap the per-item near-miss detail in the summary; the rest stay at debug level.
_NEAR_MISS_DETAIL_LIMIT = 15


def _id_tags(d: Dict[str, Any]) -> str:
    """Render an asset/media dict's ids as filename-style tags, or '(none)'."""
    tags = [f"{{{k}-{d[f'{k}_id']}}}" for k in ("tmdb", "tvdb", "imdb") if d.get(f"{k}_id")]
    return " ".join(tags) if tags else "(none)"


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
        # ISO dates for modal sorting: arr added date + digital/physical (or firstAired) release
        "added": media.get("added"),
        "release_date": media.get("release_date"),
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
        primary_year = media.get("year")
        # *arr reports year 0 when the release year is unknown (unaired/unannounced).
        # Treat 0 as "no year" rather than a literal year, for two reasons:
        #   1. Round-trip: the renamer copies a poster into "Title"/poster.jpg, which
        #      re-scans with no year. Comparing that yearless asset against the year-0
        #      media must succeed, or Asset Cleanup orphans and deletes the folder every
        #      run while the renamer recreates it — a rename loop.
        #   2. The folder_year parsed from the *arr path can be stale/guessed when the
        #      real year is unknown, so don't let it validate a fuzzy title match.
        # ID matches run earlier and are unaffected, so an ID-tagged poster still grabs.
        if primary_year == 0:
            media_years = [media.get("secondary_year")]
        else:
            media_years = [
                media.get(key) for key in ["year", "secondary_year", "folder_year"]
            ]
        if asset_year is None and all(year is None for year in media_years):
            return True
        return any(asset_year == year for year in media_years if year is not None)

    def title_matches() -> Tuple[bool, str]:
        title_hit = False
        for condition, reason in _title_criteria(asset, media):
            if condition:
                if year_matches():
                    return True, reason
                title_hit = True
        # A title agreed but every year did — report it instead of dropping silently.
        return False, YEAR_MISMATCH if title_hit else ""

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

    # Collection tmdb sits on tmdb_id_ref so collection POSTERS stay title-matched; type-less
    # assets are artwork (id in filename, title won't fuzzy-match) so they may use the ref, like series.
    _asset_type = asset.get("type") or ""
    _media_type = media.get("type") or ""
    _media_tmdb = media.get("tmdb_id") or (
        media.get("tmdb_id_ref")
        if (_media_type == "series" or not _asset_type)
        else None
    )

    has_asset_ids = has_any_valid_id(asset)
    has_media_ids = has_any_valid_id(media) or bool(
        _media_tmdb and str(_media_tmdb).isdigit() and int(_media_tmdb) > 0
    )

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
        # TMDB movie and TV ids are separate namespaces (movie 114478 and tv 114478 are
        # different titles), so a tmdb match needs the sides to agree on series-ness.
        # A type-less side (artwork) bearing a tvdb id is TV; without one it stays unknown.
        def _series_side(d: Dict[str, Any], dtype: str) -> Optional[bool]:
            if dtype:
                return dtype == "series"
            if d.get("tvdb_id"):
                return True
            return None

        _asset_series = _series_side(asset, _asset_type)
        _media_series = _series_side(media, _media_type)
        tmdb_types_compatible = (
            _asset_series is None
            or _media_series is None
            or _asset_series == _media_series
        )
        id_match_criteria = [
            (
                media.get("tvdb_id")
                and asset.get("tvdb_id")
                and media.get("tvdb_id") == asset.get("tvdb_id"),
                "by tvdb_id",
            ),
            (
                _media_tmdb
                and asset.get("tmdb_id")
                and _media_tmdb == asset.get("tmdb_id")
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
        # The lifted collection ref is a hint, not an identity lock: when it is the media's
        # ONLY id and it disagrees, fall back to titles. The renamer's pass sees a typed
        # merged box (no ref lift, title-matched); without this, the artwork-only cleanup
        # pass silently rejects the same pairing and prunes what the rename just placed.
        if not has_any_valid_id(media):
            return title_matches()
        # Rejected. Distinguish the causes, because the id lock skips the title fallback
        # below and two of them deserve a report instead of silence:
        #   - a shared id type disagrees but title AND year agree -> one side is mistagged.
        #     (Title-only agreement stays silent — that's the remake-with-new-id case.)
        #   - no id type on both sides -> nothing could be compared, so an agreeing title
        #     is almost certainly a tagging gap (e.g. imdb-only poster vs tvdb-only item).
        shares_an_id_type = any(
            media_value and asset.get(key)
            for key, media_value in (
                ("tvdb_id", media.get("tvdb_id")),
                ("tmdb_id", _media_tmdb),
                ("imdb_id", media.get("imdb_id")),
            )
        )
        title_ok, title_reason = title_matches()
        if shares_an_id_type:
            if title_ok:
                return False, ID_CONFLICT
            return False, ""
        if title_ok:
            return False, NO_SHARED_ID
        if title_reason == YEAR_MISMATCH:
            return False, YEAR_MISMATCH
        return False, ""

    return title_matches()


def _title_criteria(asset: Dict[str, Any], media: Dict[str, Any]) -> List[Tuple[Any, str]]:
    """Title/alias equivalence tests, shared by the fallback path and near-miss detection."""
    return [
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


def match_assets_to_media(
    media_dict: Dict[str, List[Dict[str, Any]]],
    prefix_index: Dict[str, Any],
    strict_folder_match: bool = False,
    label: str = "",
    report_near_misses: bool = True,
) -> Dict[str, List[Dict[str, Any]]]:
    """Match assets to media entries.

    Args:
      media_dict: Dictionary of media grouped by type.
      prefix_index: Search index for assets.
      strict_folder_match: If True, only match if folder matches.
      report_near_misses: Emit the "add the year to the *arr path / poster filename"
        WARNING report. Only the renamer's poster pass should — in the artwork-only
        reconciliation passes an id-less collection title-brushing a yeared title is
        routine and the advice doesn't apply.

    Returns:
      Dictionary of matched assets by type.
    """
    asset_types = ["movies", "series", "collections"]
    matched: Dict[str, List[Dict[str, Any]]] = {atype: [] for atype in asset_types}
    near_misses: List[Tuple[Dict[str, Any], Dict[str, Any], str]] = []

    use_asset_types = [t for t in media_dict if media_dict[t] is not None]
    # The label says WHAT is being matched — the unmatched check runs this once per artwork
    # type, and three identical "Matching N media item(s)" lines tell the reader nothing.
    total = sum(len(media_dict[t] or []) for t in use_asset_types)
    log_phase(LogTags.MATCH, f"Matching {total} media item(s){f' against {label}' if label else ''}")

    for asset_type in use_asset_types:
        if asset_type in media_dict:
            matched_dict: List[Dict[str, Any]] = []
            # Process/log items alphabetically by title (matches the scanner's ordering).
            media_data = sorted(media_dict[asset_type], key=lambda m: (m.get("title") or "").lower())

            for media in media_data:
                found_match = False
                match_reason = ""
                search_asset = None
                near_miss: Optional[Tuple[Dict[str, Any], str]] = None
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
                                f"✓ Matched {reason}: {media['title']} ({media.get('year')}) <-> {candidate['title']} ({candidate.get('year')})",
                                media_title=media['title'],
                                media_year=media.get('year'),
                                asset_title=candidate['title'],
                                asset_year=candidate.get('year'),
                                reason=reason
                            )
                            search_asset = candidate
                            found_match = True
                            match_reason = reason
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
                        near_miss = _keep_near_miss(near_miss, candidate, reason)

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
                                f"✓ Matched {reason}: {media['title']} ({media.get('year')}) <-> {search_asset['title']} ({search_asset.get('year')})",
                                media_title=media['title'],
                                media_year=media.get('year'),
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
                                match_reason = reason
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
                        near_miss = _keep_near_miss(near_miss, search_asset, reason)

                if not found_match and near_miss is not None:
                    near_misses.append((media, near_miss[0], near_miss[1]))

                if found_match:
                    matched_dict.append(
                        {
                            "title": media["title"],
                            "year": media.get("year"),
                            "folder": media.get("folder"),
                            # Existence-check assets (destination artwork) carry no files.
                            "files": search_asset.get("files") or [],
                            "seasons_numbers": (
                                search_asset.get("season_numbers", None)
                                if search_asset
                                else None
                            ),
                            "asset_ref": search_asset,
                            # The live media object this matched, so callers can key results
                            # back to their own media_dict by identity (Asset Cleanup does).
                            "media_ref": media,
                            # How it matched, for callers that report it (unmatched's "via X").
                            "match_reason": match_reason,
                            # Authoritative refs from the Plex/*arr record (IDs +
                            # poster) so style-fallback publishing matches exactly.
                            **media_source_refs(media),
                        }
                    )
            
            matched[asset_type] = matched_dict

    if report_near_misses:
        _log_near_misses(near_misses)
    return matched


# Near-miss priority: an id conflict names the exact wrong id, an id gap names the tag to
# add, a year gap may just be a stale *arr year.
_NEAR_MISS_RANK = {ID_CONFLICT: 0, NO_SHARED_ID: 1, YEAR_MISMATCH: 2}


def _keep_near_miss(
    current: Optional[Tuple[Dict[str, Any], str]],
    asset: Dict[str, Any],
    reason: str,
) -> Optional[Tuple[Dict[str, Any], str]]:
    """Keep the most actionable near-miss seen so far for one media item (ties: first wins)."""
    if reason not in _NEAR_MISS_RANK:
        return current
    if current is None or _NEAR_MISS_RANK[reason] < _NEAR_MISS_RANK[current[1]]:
        return (asset, reason)
    return current


def _year_detail(media: Dict[str, Any], asset: Dict[str, Any]) -> str:
    """Explain which years were compared, and call out a yearless *arr path."""
    library_years = [
        f"{label}={media.get(key)}"
        for key, label in (("year", "year"), ("secondary_year", "secondary"), ("folder_year", "folder"))
        if media.get(key) is not None
    ]
    line = (
        f"        poster  year={asset.get('year')}\n"
        f"        library {', '.join(library_years) or 'no year'}"
    )
    # The common cause: Sonarr/Radarr path lacks " (YYYY)", so no folder_year corroborates.
    if media.get("folder") and media.get("folder_year") is None:
        line += (
            f"\n        note    *arr path has no (year): "
            f"{os.path.basename(media['folder'])}"
        )
    return line


def _log_near_misses(
    near_misses: List[Tuple[Dict[str, Any], Dict[str, Any], str]]
) -> None:
    """Report items that had a plausible poster but were rejected, and why.

    Both sides are named because the fix depends on which one is short — the matcher
    rejects on absence of corroborating evidence, not on contradictory evidence.
    """
    if not near_misses:
        return

    groups = (
        (
            ID_CONFLICT,
            "carry a DIFFERENT id (title and year agree), so they were treated as "
            "separate items. One side is mistagged — fix the poster tag or the *arr mapping:",
        ),
        (
            NO_SHARED_ID,
            "share no id with the library item, so they were skipped. "
            "Add the library id to the poster filename:",
        ),
        (
            YEAR_MISMATCH,
            "matched by title but no year lined up, so they were skipped. "
            "Add the year to the *arr folder path or the poster filename:",
        ),
    )

    for reason, guidance in groups:
        group = [(m, a) for m, a, r in near_misses if r == reason]
        if not group:
            continue
        log_warning(
            LogTags.MATCH,
            f"{len(group)} item(s) had a matching poster but {guidance}",
            count=len(group),
            reason=reason,
        )
        for index, (media, asset) in enumerate(group):
            source = (
                os.path.basename((asset.get("files") or [""])[0])
                or asset.get("title", "")
            )
            if reason in (ID_CONFLICT, NO_SHARED_ID):
                body = (
                    f"        poster  {_id_tags(asset)}  {source}\n"
                    f"        library {_id_tags(media)}"
                )
            else:
                body = f"{_year_detail(media, asset)}\n        file    {source}"
            detail = f"    {media.get('title')} ({media.get('year')})\n{body}"
            if index < _NEAR_MISS_DETAIL_LIMIT:
                log_warning(LogTags.MATCH, detail, media_title=media.get("title"))
            else:
                log_debug(LogTags.MATCH, detail, media_title=media.get("title"))
        if len(group) > _NEAR_MISS_DETAIL_LIMIT:
            log_warning(
                LogTags.MATCH,
                f"    ... and {len(group) - _NEAR_MISS_DETAIL_LIMIT} more (enable debug logging for the full list)",
            )


def handle_series_match(
    asset: Dict[str, Any],
    media_seasons_numbers: List[int],
    asset_season_numbers: List[int],
) -> None:
    """Prune asset data to remove files/seasons not present in the media entry.

    Prunes ``slots["seasons"]`` alongside ``files`` — by season number, using the box's own
    parsing rather than re-reading filenames. Without this the two disagree after a match
    (files pruned, slots not), and anything walking slots would place a season the library
    doesn't have.

    Args:
      asset: Asset dictionary with file and season data.
      media_seasons_numbers: List of seasons found in the media source.
      asset_season_numbers: List of seasons declared in the asset.
    """
    files_to_remove = []
    seasons_to_remove = []

    slot_seasons = (asset.get("slots") or {}).get("seasons")
    if slot_seasons:
        for season in [s for s in slot_seasons if s not in media_seasons_numbers]:
            del slot_seasons[season]
    
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
