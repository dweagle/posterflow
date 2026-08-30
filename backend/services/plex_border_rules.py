"""Plex label/genre/collection border rules.

Apply a specific border style to only the items matching a Plex label, genre, or
collection (a per-item override), leaving everything else to its normal border
treatment. Each rule has a mode:

  - "include": items MATCHING the criteria get the rule's border style.
  - "except":  items NOT matching the criteria get the rule's border style.
  - "skip":    items MATCHING the criteria are left completely untouched.

Rule order = priority (the first matching rule wins). The set of matching items is
queried from Plex ONCE per run (server-side filtered) and cached on the matcher — never
per poster.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from core.logging import LogTags, log_info, log_warning, log_error
from models.setting import get_setting_value
from util.constants import POSTER_ID_PATTERN  # canonical {tmdb-..}/{imdb-..}/{tvdb-..} token parser
from util.data.normalization import normalize_titles  # canonical title normalization (shared with poster_renamer)

RULE_RUN_TYPES = ("workflow", "webhook", "manual", "autorun", "scheduled")
_MATCH_TYPES = ("label", "genre", "collection")
_MODES = ("include", "except", "skip")


# Matching mirrors poster_renamer._asset_matches_target so border rules identify items the
# same way the rest of the app does:
#   - External IDs take precedence. tvdb and imdb are definitive for either media type;
#     tmdb is trusted only for MOVIES (movie and TV tmdb IDs share numbers).
#   - Title is a last resort, scoped by year AND media type (normalize_titles strips the
#     year, so a 1989 movie and a 2026 show that share a title would otherwise collide).
# The poster folder's media type isn't in its name, so it's inferred: a {tvdb-..} token
# means a show (movies don't carry tvdb); otherwise a movie.


def _title_key(raw_title: str, year: str, media: str) -> Optional[str]:
    """Year+type-scoped title fallback key (uses the shared normalize_titles)."""
    norm = normalize_titles(raw_title or "")
    if not norm:
        return None
    return f"title:{norm}:{year or ''}:{media}"


def folder_match_keys(folder: str) -> Set[str]:
    """Match keys for a renamed poster folder: its embedded external IDs (tmdb movie-only,
    a {tvdb-..} token marking a show) plus a year+type-scoped normalized title."""
    keys: Set[str] = set()
    if not folder:
        return keys
    found: Dict[str, List[str]] = {}
    for kind, value in POSTER_ID_PATTERN.findall(folder):
        found.setdefault(kind.lower(), []).append(value)
    media = "show" if found.get("tvdb") else "movie"
    for kind, values in found.items():
        if kind == "tmdb" and media == "show":
            continue  # a show folder's tmdb id must not match a movie's tmdb id
        for value in values:
            keys.add(f"{kind}:{value.lower()}")
    year_match = re.search(r"\((\d{4})\)", folder)
    year = year_match.group(1) if year_match else ""
    title_key = _title_key(folder, year, media)
    if title_key:
        keys.add(title_key)
    return keys


def _item_match_keys(item: Any, is_movie: bool) -> Set[str]:
    """Match keys for a Plex item, following the same ID precedence as
    poster_renamer._asset_matches_target: tvdb/imdb definitive, tmdb movie-only, plus a
    year+type-scoped title fallback.

    A collection is keyed by title as a yearless MOVIE regardless of the library it lives in:
    its poster folder carries no year and no tvdb token, so folder_match_keys always infers
    media='movie' for it. Keying a TV-library collection as 'show' would never match its own
    poster."""
    keys: Set[str] = set()
    is_collection = item.item_type == "collection"
    media = "movie" if (is_movie or is_collection) else "show"
    year = None if is_collection else item.year
    title_key = _title_key(item.title or "", str(year) if year else "", media)
    if title_key:
        keys.add(title_key)
    for kind in ("tmdb", "imdb", "tvdb"):
        value = (item.provider_ids or {}).get(kind)
        if not value:
            continue
        if kind == "tmdb" and not (is_movie or is_collection):
            continue  # tmdb ids collide across movie/TV namespaces
        keys.add(f"{kind}:{value}")
    return keys


@dataclass
class PlexBorderRule:
    name: str
    match: str            # "label" | "genre" | "collection"
    value: str
    mode: str             # "include" | "except" | "skip"
    colors: List[str]
    style_opts: Dict[str, Any]
    match_keys: Set[str] = field(default_factory=set)


@dataclass
class RuleMatch:
    """Result of resolving a poster against the rules."""
    name: str
    skip: bool                     # True → leave the poster untouched
    colors: List[str]              # border colors to cycle (empty when skip)
    style_opts: Dict[str, Any]     # border style/effect opts (empty when skip)


class PlexRuleMatcher:
    """Resolves a poster folder to the winning rule (first match wins)."""

    def __init__(self, rules: List[PlexBorderRule]):
        self.rules = rules

    def resolve(self, folder: Optional[str]) -> Optional[RuleMatch]:
        keys = folder_match_keys(folder or "")
        for rule in self.rules:
            matched = bool(keys & rule.match_keys)
            if rule.mode == "include" and matched:
                return RuleMatch(rule.name, False, rule.colors, rule.style_opts)
            if rule.mode == "except" and not matched:
                return RuleMatch(rule.name, False, rule.colors, rule.style_opts)
            if rule.mode == "skip" and matched:
                return RuleMatch(rule.name, True, [], {})
        return None


def load_plex_rules(db: Session) -> List[PlexBorderRule]:
    """Parse the border_replacer_plex_rules setting into rules (without match_keys)."""
    from services.border_replacer import build_holiday_style_opts  # reuse the style parser

    raw = get_setting_value(db, "border_replacer_plex_rules", "[]") or "[]"
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []

    rules: List[PlexBorderRule] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        match = str(item.get("match", "")).strip().lower()
        value = str(item.get("value", "")).strip()
        mode = str(item.get("mode", "include")).strip().lower()
        if match not in _MATCH_TYPES or mode not in _MODES or not value:
            continue
        colors = [str(c).strip() for c in (item.get("colors") or []) if str(c).strip()]
        style_raw = item.get("style") if isinstance(item.get("style"), dict) else None
        style_opts = build_holiday_style_opts(style_raw, None)
        rules.append(
            PlexBorderRule(
                name=str(item.get("name", "")).strip() or f"{match}:{value}",
                match=match,
                value=value,
                mode=mode,
                colors=colors,
                style_opts=style_opts,
            )
        )
    return rules


def rules_enabled_for(db: Session, run_type: str) -> bool:
    """Whether Plex border rules apply for this run entry point.

    Fail-safe: an absent/empty setting means rules are OFF for every run type — they only
    run for run types the user explicitly enabled. (The tab shows all toggles on as a
    starting point, but rules can't run until rules + libraries are also configured and
    saved, and that save always writes this setting, so the two converge in every real
    flow.)"""
    raw = get_setting_value(db, "border_replacer_rule_run_types", "")
    if not raw:
        return False
    enabled = {t.strip().lower() for t in str(raw).split(",") if t.strip()}
    return run_type in enabled


def _selected_library_keys(db: Session) -> Set[str]:
    """Set of 'instance:key' library keys to scan. ONLY the checked libraries are scanned;
    an empty set means scan nothing (rules do not run)."""
    raw = get_setting_value(db, "border_replacer_rule_libraries", "")
    if not raw:
        return set()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return set()
    if isinstance(parsed, list):
        return {str(x) for x in parsed}
    return set()


def _search_section(client: Any, library: Any, match: str, value: str) -> List[Any]:
    """Server-side query for the items in a library matching one rule.

    A label/genre rule also matches COLLECTIONS carrying that label/genre, so a label put on
    a collection borders the collection's OWN poster (member items are bordered only if they
    individually carry the label). Plex stores collection labels in a namespace separate from
    item labels, so a plain label search alone never returns them."""
    if match == "collection":
        # Match a collection by title → border its member items.
        matched: List[Any] = []
        for coll in client.get_collections(library.key):
            if str(coll.title or "").strip().lower() == value.strip().lower():
                matched.extend(client.get_collection_items(coll))
        return matched
    # label / genre → items carrying the field (server-side), plus any collection
    # carrying it, keyed to its own poster.
    return client.find_by_field(match, value, library.key, include_collections=True)


def _query_plex_matches(db: Session, rules: List[PlexBorderRule]) -> tuple[int, bool]:
    """Populate each rule's match_keys by querying Plex server-side (once per rule per
    scanned library). Never called per poster.

    Returns (scanned_sections, had_errors): how many selected libraries we successfully
    reached, and whether any connection or per-rule query raised. The caller uses these to
    fail closed — an empty match set from a query FAILURE must not be mistaken for "nothing
    matched" (which would make an 'except' rule paint the whole library)."""
    from util.media_server.client import create_media_server_client

    raw = get_setting_value(db, "plex_instances", "[]") or "[]"
    try:
        instances = json.loads(raw)
    except (ValueError, TypeError):
        instances = []
    instances = [i for i in instances if isinstance(i, dict) and i.get("url") and i.get("api_key")]
    if not instances:
        log_warning(LogTags.BORDER_REPLACER, "Border rules: no media server instances configured; rules skipped")
        return 0, True

    lib_scope = _selected_library_keys(db)
    scanned = 0
    had_errors = False

    from util.media_server.instances import instance_label

    for instance in instances:
        name = str(instance.get("name", "")).strip()
        label = instance_label(instance)
        try:
            client = create_media_server_client(instance)
            if client is None:
                raise RuntimeError("connection failed")
            libraries = [l for l in client.get_libraries() if l.type in ("movie", "show")]
        except Exception as e:
            log_warning(LogTags.BORDER_REPLACER, f"Border rules: cannot reach {label}: {e}")
            had_errors = True
            continue
        for library in libraries:
            # Only scan libraries the user explicitly checked.
            if f"{name}:{library.key}" not in lib_scope:
                continue
            is_movie = library.type == "movie"
            scanned += 1
            for rule in rules:
                try:
                    for it in _search_section(client, library, rule.match, rule.value):
                        rule.match_keys |= _item_match_keys(it, is_movie)
                except Exception as e:
                    log_warning(
                        LogTags.BORDER_REPLACER,
                        f"Border rules: query failed ({rule.match}={rule.value}) on {label} library {library.title}: {e}",
                    )
                    had_errors = True
    return scanned, had_errors


def build_plex_matcher(db: Session, run_type: str) -> Optional[PlexRuleMatcher]:
    """If Plex border rules exist and are enabled for this run type, query Plex once,
    build the matched-item sets, and return a matcher. Returns None when there is nothing
    to do (no rules, disabled for this run type, no libraries selected, or Plex unreachable)."""
    if not rules_enabled_for(db, run_type):
        return None
    rules = load_plex_rules(db)
    if not rules:
        return None
    if not _selected_library_keys(db):
        log_info(LogTags.BORDER_REPLACER, "Border rules: no libraries selected; rules skipped")
        return None
    try:
        scanned, had_errors = _query_plex_matches(db, rules)
    except Exception as e:
        log_error(LogTags.BORDER_REPLACER, f"Border rules: matcher build failed: {e}")
        return None

    # Fail closed: if we couldn't actually scan any selected library, don't return a
    # matcher — its empty match sets would make every 'except' rule match everything and
    # (via per-item tracking) revert rule-styled posters to default borders.
    if scanned == 0:
        log_warning(LogTags.BORDER_REPLACER, "Border rules: no selected library could be scanned; rules skipped this run")
        return None
    # A partial query failure is only dangerous for 'except' rules (missing matches → too
    # many items painted). Include/skip rules degrade safely (an item just misses its border
    # this run and gets it on the next clean run), so only bail when an 'except' rule exists.
    if had_errors and any(r.mode == "except" for r in rules):
        log_warning(
            LogTags.BORDER_REPLACER,
            "Border rules: server query incomplete and an 'except' rule is present; rules skipped this run to avoid over-applying borders",
        )
        return None

    total = sum(len(r.match_keys) for r in rules)
    log_info(LogTags.BORDER_REPLACER, f"Plex border rules active: {len(rules)} rule(s), {total} matched item key(s)")
    return PlexRuleMatcher(rules)
