"""A custom overlay's conditions: which items get its badge.

A rule is {field, op, value}. One that reads a Radarr or Sonarr tag, or something a media
server holds (a label, a genre, a collection, a studio, a content rating, an edition), may name
the `instance` it means: that Arr instance's tag, that server's label. Without one, every
instance of the app is asked, and any server's posters can match. A chart that TMDB keeps per region may name its `region` (a two-letter country code;
without one it is TMDB's own, all regions together). `also` holds more values,
each {value, instance}: the comparison then holds when it holds for any one of them (a negative
one, "is not" or "does not contain", when it holds for none). An overlay holds a list of rules and a mode: every rule must
hold ("all") or any one of them ("any"). No rule at all = every item. FIELDS is the catalog
the page builds its editor from, each field with the comparisons it offers.

What an item carries (a label, a genre, a tag...) is compared by its whole name ("is") or by
part of it (contains, begins with, ends with, matches a regex): any one name that fits will do.

What an item does not carry itself (a label on the server, an Arr tag, a list) is asked through
the engine's Lookups (lookups.py). A source that does not answer raises Unavailable: the item
is then left as it is for the run, so an outage never strips or adds a badge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from core.logging import LogTags, log_warning

from .base import ItemContext, MEDIA_MOVIE
from .media import AUDIO_CODECS, DEFAULT_PERCENT, RESOLUTIONS, VIDEO_CODECS, audio_badge, resolution_badge, spoken, typical, video_codec

MAX_RULES = 20
MAX_VALUES = 20  # values one rule may hold
MULTI_KINDS = ("tag", "text", "choice")  # the kinds of field a rule may hold several values of
MODES = [("all", "All of them"), ("any", "Any of them")]
MODE_KEYS = [m[0] for m in MODES]

OP_LABELS = {
    "is": "is", "is_not": "is not",
    "contains": "contains", "not_contains": "does not contain", "begins": "begins with", "ends": "ends with", "regex": "matches (regex)",
    "gte": "is at least", "lte": "is at most", "gt": "is more than", "lt": "is less than",
    "within": "in the last", "older": "more than", "before": "before", "after": "after",
}
_NEGATIVE = {"is_not": "is", "not_contains": "contains"}  # a negative comparison → the one it negates

_HAS = ("is", "is_not")
_TEXT = ("contains", "not_contains", "is", "is_not", "begins", "ends", "regex")
_TAG = ("is", "is_not", "contains", "not_contains", "begins", "ends", "regex")  # a name the item carries, whole or in part
_RANGE = ("gte", "lte", "gt", "lt", "is", "is_not")
_COUNT = ("is", "is_not", "gte", "lte", "gt", "lt")
_DAYS = ("within", "older", "before", "after", "regex")
DATE_OPS = ("before", "after")  # a "days" field compared with a date (YYYY-MM-DD); within and older take a number of days
DATE_FORMAT = "%m/%d/%Y"  # how a date is written for a regex: 12/25/2024
_RANK = ("lte", "gt")  # a chart: in its top N, or not
_RANK_LABELS = {"lte": "is in the top", "gt": "is not in the top"}

ARR_TAG_FIELDS = {"radarr_tag": "radarr", "sonarr_tag": "sonarr"}  # the field → the Arr it asks
SERVER_FIELDS = ("label", "genre", "collection", "studio", "content_rating", "edition")  # what a media server holds
MDBLIST = re.compile(r"^https://mdblist\.com/lists/[\w.~-]+(/[\w.~-]+){1,2}$")

_HDR = [("any", "Any HDR"), ("dv", "Dolby Vision"), ("plus", "HDR10+"), ("hlg", "HLG")]
_HDR_FLAVORS = {"dv": ("dv", "dvhdr", "dvhdrplus"), "plus": ("plus", "dvhdrplus"), "hlg": ("hlg",)}
_TMDB_STATUSES = ["Released", "Returning Series", "Ended", "Canceled", "In Production", "Post Production", "Planned", "Rumored", "Pilot"]
_TMDB_TYPES = ["Scripted", "Reality", "Documentary", "News", "Talk Show", "Miniseries", "Video"]
TMDB_LIST = re.compile(r"^https://www\.themoviedb\.org/list/\d+$")
# A chart's field → where TMDB keeps it, for movies and for shows.
CHARTS: Dict[str, Dict[str, str]] = {
    "tmdb_trending_day": {"movie": "/trending/movie/day", "show": "/trending/tv/day"},
    "tmdb_trending_week": {"movie": "/trending/movie/week", "show": "/trending/tv/week"},
    "tmdb_popular": {"movie": "/movie/popular", "show": "/tv/popular"},
    "tmdb_top_rated": {"movie": "/movie/top_rated", "show": "/tv/top_rated"},
    "tmdb_now_playing": {"movie": "/movie/now_playing", "show": "/tv/on_the_air"},
    "tmdb_upcoming": {"movie": "/movie/upcoming", "show": "/tv/airing_today"},
}
# The charts TMDB keeps per region, for movies only. It has none for shows or for trending, and
# its popular and top rated charts come back the same for every region (seen Nov 2026).
REGION_CHARTS = ("tmdb_now_playing", "tmdb_upcoming")
_EDITION_TAG = re.compile(r"\{edition-([^}]+)\}", re.I)

G_SERVER, G_ARR, G_ITEM, G_MEDIA, G_TMDB, G_CHART, G_LIST = "Media server", "Radarr and Sonarr", "Item", "Video and audio", "TMDB", "TMDB charts", "Lists"
_DATE_REGEX_HELP = " Matches (regex) reads the date as month/day/year from its start: 12/25 is every December 25, and 12/ is every December."


class Unavailable(Exception):
    """A source a rule asks did not answer."""


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    group: str
    kind: str  # tag (the item carries it or not) | text | number | days (a number of days, or a date) | choice
    ops: Tuple[str, ...]
    needs: Tuple[str, ...] = ()  # what the engine must fetch for it (see OverlayType.needs)
    options: Tuple[Tuple[str, str], ...] = ()
    help: str = ""
    placeholder: str = ""
    step: float = 1  # number: a fraction = a decimal field
    pattern: str = ""  # what a value must look like; the page marks one that does not
    # The page offers values to pick from (see lookups.rule_values): "list" = the ones there
    # are, "search" = looked up as the user types. "" = the value is typed.
    suggest: str = ""
    op_labels: Dict[str, str] = dc_field(default_factory=dict)
    start: Any = None  # the value a new condition on the field starts with, where the kind's own would not do
    region: bool = False  # a condition on the field may name a region (see REGION_CHARTS)


    def describe(self) -> Dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "group": self.group, "kind": self.kind,
            "ops": [{"value": op, "label": self.op_labels.get(op, OP_LABELS[op])} for op in self.ops],
            "options": [{"value": v, "label": l} for v, l in self.options],
            "help": self.help, "placeholder": self.placeholder, "step": self.step, "pattern": self.pattern,
            "needs": list(self.needs), "suggest": self.suggest, "multi": self.kind in MULTI_KINDS, "start": self.start, "region": self.region,
        }


FIELD_LIST: List[Field] = [
    Field("label", "Label or tag", G_SERVER, "tag", _TAG, suggest="list",
          help="A Plex label or a Jellyfin tag on the movie or show. The number beside a Plex label is how many movies and shows carry it."),
    Field("genre", "Genre", G_SERVER, "tag", _TAG, suggest="list", help="A genre as your media server names it."),
    Field("collection", "Collection", G_SERVER, "tag", _TAG, suggest="list", help="The name of a collection the movie or show is in."),
    Field("studio", "Studio or network", G_SERVER, "tag", _TAG, suggest="list", help="The studio of a movie or the network of a show, as your media server names it."),
    Field("content_rating", "Content rating", G_SERVER, "text", _TAG, suggest="list", placeholder="R, TV-MA", help="The age rating as your media server shows it."),
    Field("audience_rating", "Audience rating", G_SERVER, "number", _RANGE, step=0.1, help="The audience score your media server holds, 0 to 10."),
    Field("critic_rating", "Critic rating", G_SERVER, "number", _RANGE, step=0.1, help="The critic score your media server holds, 0 to 10."),
    Field("user_rating", "User rating", G_SERVER, "number", _RANGE, step=0.1, help="Your own rating on the media server, 0 to 10."),
    Field("collected", "In a collection", G_SERVER, "choice", ("is",), options=(("yes", "Yes"), ("no", "No")),
          help="Whether the movie or show is in any collection on your media server."),
    Field("radarr_tag", "Radarr tag", G_ARR, "tag", _TAG, suggest="list",
          help="A tag on the movie in one of your Radarr instances. Movies only."),
    Field("sonarr_tag", "Sonarr tag", G_ARR, "tag", _TAG, suggest="list",
          help="A tag on the show in one of your Sonarr instances. Shows and their seasons only."),
    Field("title", "Title", G_ITEM, "text", _TEXT),
    Field("year", "Year", G_ITEM, "number", _COUNT),
    Field("edition", "Edition", G_ITEM, "text", _TEXT, suggest="list", placeholder="Director's Cut",
          help="The edition your media server names, or the {edition-...} tag in the file name. Movies only."),
    Field("has_edition", "Has an edition", G_ITEM, "choice", ("is",), options=(("yes", "Yes"), ("no", "No")),
          help="Whether the movie has any edition: one your media server names, or an {edition-...} tag in the file name. Movies only."),
    Field("filename", "File name", G_ITEM, "text", ("contains", "not_contains", "begins", "ends", "regex"), needs=("media",),
          help="The file's full path. A show or season counts when most of its episodes match."),
    Field("released", "Released", G_ITEM, "days", _DAYS,
          help="The release date your media server holds: within a number of days, or before or after a date. For a show that is its first episode."
               + _DATE_REGEX_HELP),
    Field("added", "Added", G_ITEM, "days", _DAYS,
          help="When the item was added to your media server: within a number of days, or before or after a date." + _DATE_REGEX_HELP),
    Field("last_aired", "Last aired", G_ITEM, "days", _DAYS, needs=("tmdb",),
          help="When the show's latest episode aired, from TMDB: within a number of days, or before or after a date. Shows only." + _DATE_REGEX_HELP),
    Field("resolution", "Resolution", G_MEDIA, "choice", _HAS, needs=("media",), options=tuple((key, label) for key, label, _p in RESOLUTIONS)),
    Field("hdr", "HDR format", G_MEDIA, "choice", _HAS, needs=("media",), options=tuple(_HDR)),
    Field("video_codec", "Video codec", G_MEDIA, "choice", _HAS, needs=("media",), options=tuple((key, label) for key, label, _p in VIDEO_CODECS)),
    Field("audio", "Audio", G_MEDIA, "choice", _HAS, needs=("media",), options=tuple((key, label) for key, label, _w, _p in AUDIO_CODECS),
          help="The best audio format the item has."),
    Field("audio_channels", "Audio channels", G_MEDIA, "number", _RANGE, needs=("media",),
          help="The most channels an audio track has: 2 is stereo, 6 is 5.1, 8 is 7.1."),
    Field("audio_language", "Audio language", G_MEDIA, "tag", _HAS, needs=("media",), suggest="list", placeholder="en, ja",
          help="A language the item has an audio track in. A show or season counts when most of its episodes have it."),
    Field("subtitle_language", "Subtitle language", G_MEDIA, "tag", _HAS, needs=("media",), suggest="list", placeholder="en, ja",
          help="A language the item has subtitles in. A show or season counts when most of its episodes have it."),
    Field("audio_language_count", "Audio languages", G_MEDIA, "number", _COUNT, needs=("media",),
          help="How many languages the item has audio in. 2 is dual audio."),
    Field("subtitle_language_count", "Subtitle languages", G_MEDIA, "number", _COUNT, needs=("media",), help="How many languages the item has subtitles in."),
    Field("aspect_ratio", "Aspect ratio", G_MEDIA, "number", _RANGE, needs=("media",), step=0.01, help="Width divided by height: 1.78 is 16:9, 2.39 is scope."),
    Field("runtime", "Runtime (minutes)", G_MEDIA, "number", _RANGE, needs=("media",), help="A movie's length, or a show's typical episode."),
    Field("bitrate", "Bitrate (Mbps)", G_MEDIA, "number", _RANGE, needs=("media",), step=0.1, help="A movie's best file, or a show's typical episode."),
    Field("video_width", "Video width", G_MEDIA, "number", _RANGE, needs=("media",), help="The picture's width in pixels."),
    Field("video_height", "Video height", G_MEDIA, "number", _RANGE, needs=("media",), help="The picture's height in pixels."),
    Field("versions", "Versions", G_MEDIA, "number", _COUNT, needs=("media",), help="How many versions of a movie you have. Movies only."),
    Field("tmdb_genre", "TMDB genre", G_TMDB, "tag", _TAG, needs=("tmdb", "details"), suggest="list", placeholder="Animation"),
    Field("tmdb_keyword", "TMDB keyword", G_TMDB, "tag", _TAG, needs=("tmdb", "keywords", "show_keywords"), suggest="search", placeholder="based on novel or book",
          help="Type a few letters and pick the keyword as TMDB spells it."),
    Field("original_language", "Original language", G_TMDB, "text", _HAS, needs=("tmdb", "details"), suggest="list", placeholder="en, ja",
          help="The language TMDB lists as the original one, kept as its two-letter code."),
    Field("country", "Country", G_TMDB, "tag", _HAS, needs=("tmdb", "details"), suggest="list", placeholder="US, JP",
          help="A country TMDB lists the movie or show as coming from, kept as its two-letter code."),
    Field("tmdb_network", "TMDB network", G_TMDB, "tag", _TAG, needs=("tmdb", "details"), placeholder="HBO", help="A network the show is on, as TMDB names it. Shows only."),
    Field("tmdb_company", "TMDB company", G_TMDB, "tag", _TAG, needs=("tmdb", "details"), suggest="search", placeholder="Pixar",
          help="A production company, as TMDB names it. Type a few letters and pick it."),
    Field("tmdb_collection", "TMDB collection", G_TMDB, "tag", _TAG, needs=("tmdb", "details"), suggest="search", placeholder="Toy Story Collection",
          help="The collection TMDB puts the movie in. Type a few letters and pick it. Movies only."),
    Field("tmdb_type", "TMDB show type", G_TMDB, "choice", _HAS, needs=("tmdb", "details"), options=tuple((t, t) for t in _TMDB_TYPES), help="Shows only."),
    Field("tmdb_status", "TMDB status", G_TMDB, "choice", _HAS, needs=("tmdb",), options=tuple((s, s) for s in _TMDB_STATUSES)),
    Field("tmdb_rating", "TMDB score", G_TMDB, "number", _RANGE, needs=("tmdb",), step=0.1, help="TMDB's user score, 0 to 10."),
    Field("tmdb_votes", "TMDB votes", G_TMDB, "number", _RANGE, needs=("tmdb",), help="How many votes TMDB's score rests on."),
    Field("tmdb_trending_day", "Trending today", G_CHART, "number", _RANK, op_labels=_RANK_LABELS, start=20, help="TMDB's trending chart of the day. At most the top 500."),
    Field("tmdb_trending_week", "Trending this week", G_CHART, "number", _RANK, op_labels=_RANK_LABELS, start=20, help="TMDB's trending chart of the week. At most the top 500."),
    Field("tmdb_popular", "Popular", G_CHART, "number", _RANK, op_labels=_RANK_LABELS, start=20, help="TMDB's popular chart, the same for every region. At most the top 500."),
    Field("tmdb_top_rated", "Top rated", G_CHART, "number", _RANK, op_labels=_RANK_LABELS, start=20, help="TMDB's top rated chart, the same for every region. At most the top 500."),
    Field("tmdb_now_playing", "Now playing", G_CHART, "number", _RANK, op_labels=_RANK_LABELS, start=20, region=True,
          help="TMDB's chart of movies in theaters now, and of shows on the air. At most the top 500. For movies you can name a region. Shows have one chart for all."),
    Field("tmdb_upcoming", "Upcoming", G_CHART, "number", _RANK, op_labels=_RANK_LABELS, start=20, region=True,
          help="TMDB's chart of upcoming movies, and of shows airing today. At most the top 500. For movies you can name a region. Shows have one chart for all."),
    Field("list", "MDBList list", G_LIST, "tag", _HAS, placeholder="https://mdblist.com/lists/name/list", pattern=MDBLIST.pattern,
          op_labels={"is": "has the item", "is_not": "does not have it"},
          help="The address of a public MDBList list. Lists are read again every six hours."),
    Field("tmdb_list", "TMDB list", G_LIST, "tag", _HAS, placeholder="https://www.themoviedb.org/list/12345", pattern=TMDB_LIST.pattern,
          op_labels={"is": "has the item", "is_not": "does not have it"},
          help="The address of a public TMDB list, or just its number. Lists are read again every six hours."),
    Field("id_list", "List of ids", G_LIST, "tag", _HAS, placeholder="https://example.com/list.txt",
          op_labels={"is": "has the item", "is_not": "does not have it"},
          help="A text file with one id per line: an IMDb id like tt0113277, or tmdb:949, movie:tmdb:949, show:tmdb:1399, tvdb:121361. "
               "Give its web address, or the name of a file you put in config/overlays/lists."),
]
FIELDS: Dict[str, Field] = {f.key: f for f in FIELD_LIST}


def describe_fields() -> List[Dict[str, Any]]:
    return [f.describe() for f in FIELD_LIST]


def list_address(value: str) -> str:
    """A list address as it is kept: https, no query, no trailing slash or /json."""
    url = str(value or "").strip().split("?", 1)[0].split("#", 1)[0].rstrip("/")
    if url.lower().endswith("/json"):
        url = url[:-5].rstrip("/")
    url = re.sub(r"^(https?://)?(www\.)?mdblist\.com", "https://mdblist.com", url, flags=re.I)
    return url


def tmdb_list_address(value: str) -> str:
    """A TMDB list as it is kept: its address, from that or from just its number."""
    text = str(value or "").strip()
    found = re.fullmatch(r"(?:https?://(?:www\.)?themoviedb\.org/list/)?(\d+)(?:[-/?#].*)?", text, flags=re.I)
    return f"https://www.themoviedb.org/list/{found.group(1)}" if found else text


def _clean_value(spec: Field, value: Any, op: str = "") -> Any:
    """A value as a rule keeps it, or None for one that is no value."""
    if spec.kind == "days" and op in DATE_OPS:
        day = _day(value)
        return day.isoformat() if day else None
    if spec.kind == "days" and op == "regex":
        return str(value or "").strip()[:300] or None
    if spec.kind in ("number", "days"):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if spec.kind == "days":
            return max(1, min(36500, int(number)))
        return int(number) if spec.step >= 1 else round(number, 2)
    if spec.kind == "choice":
        value = str(value or "")
        return value if value in dict(spec.options) else None
    value = str(value or "").strip()[:300]
    if spec.key == "list":
        value = list_address(value)
    elif spec.key == "tmdb_list":
        value = tmdb_list_address(value)
    return value or None


def normalize_rules(raw: Any) -> List[Dict[str, Any]]:
    """Valid rules only: an unknown field or no value at all drops the rule."""
    out: List[Dict[str, Any]] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or len(out) >= MAX_RULES:
            continue
        spec = FIELDS.get(str(entry.get("field") or ""))
        if spec is None:
            continue
        op = str(entry.get("op") or "")
        op = op if op in spec.ops else spec.ops[0]
        named = spec.key in ARR_TAG_FIELDS or spec.key in SERVER_FIELDS
        more = entry.get("also") if spec.kind in MULTI_KINDS and isinstance(entry.get("also"), list) else []
        values: List[Dict[str, Any]] = []
        seen: Set[Tuple[str, str]] = set()
        for part in [entry, *more]:
            value = _clean_value(spec, part.get("value"), op) if isinstance(part, dict) else None
            if value is None or len(values) >= MAX_VALUES:
                continue
            instance = str(part.get("instance") or "").strip()[:100] if named else ""
            if (str(value).casefold(), instance) in seen:
                continue  # said twice
            seen.add((str(value).casefold(), instance))
            values.append({"value": value, **({"instance": instance} if instance else {})})
        if not values:
            continue
        rule = {"field": spec.key, "op": op, **values[0]}
        if len(values) > 1:
            rule["also"] = values[1:]
        region = re.sub(r"[^A-Za-z]", "", str(entry.get("region") or "")).upper()
        if spec.region and len(region) == 2:
            rule["region"] = region
        out.append(rule)
    return out


def needs(rules: Iterable[Dict[str, Any]]) -> Tuple[str, ...]:
    found: List[str] = []
    for rule in rules:
        spec = FIELDS.get(str(rule.get("field") or ""))
        for need in (spec.needs if spec else ()):
            if need not in found:
                found.append(need)
    return tuple(found)


def item_keys(ctx: ItemContext) -> Set[str]:
    """The item's ids as lists and the Arrs are compared by. TMDB and TVDB number movies and
    shows separately, so those carry the kind."""
    keys: Set[str] = set()
    if ctx.tmdb_id:
        keys.add(f"{ctx.media_type}:tmdb:{ctx.tmdb_id}")
    if ctx.tvdb_id:
        keys.add(f"{ctx.media_type}:tvdb:{ctx.tvdb_id}")
    if ctx.imdb_id:
        keys.add(f"imdb:{ctx.imdb_id}")
    return keys


def _day(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


_BAD_PATTERNS: Set[str] = set()  # the regexes already said to be broken (see forget_bad_patterns)


def forget_bad_patterns() -> None:
    """A new run says again which patterns are broken."""
    _BAD_PATTERNS.clear()


def _pattern(text: str, flags: int = 0) -> Optional["re.Pattern[str]"]:
    """A regex ready to use, or None for one that is not valid: said once in the log."""
    try:
        return re.compile(text, flags)
    except re.error as exc:
        if text not in _BAD_PATTERNS:
            _BAD_PATTERNS.add(text)
            log_warning(LogTags.OVERLAYS, f"Custom overlays: the pattern '{text}' is not a valid regex ({exc}). Conditions that use it match nothing.")
        return None


def _text_hit(op: str, wanted: str, have: Iterable[str]) -> bool:
    values = [str(v) for v in have if v]
    if op == "regex":
        pattern = _pattern(wanted, re.I)
        return pattern is not None and any(pattern.search(v) for v in values)
    folded = wanted.casefold()
    if op == "contains":
        return any(folded in v.casefold() for v in values)
    if op == "begins":
        return any(v.casefold().startswith(folded) for v in values)
    if op == "ends":
        return any(v.casefold().endswith(folded) for v in values)
    return any(folded == v.casefold() for v in values)


def _places(spec: Field) -> int:
    """How many decimals a number field is kept to (its step): 1 → 0, 0.1 → 1, 0.01 → 2."""
    text = f"{spec.step:g}"
    return len(text.split(".", 1)[1]) if "." in text else 0


def _number_hit(op: str, have: Any, value: Any, places: int = 2) -> bool:
    """A number compared; one that is not known never fits. "Is" compares to the decimals the
    field is kept to, so an aspect ratio of 2.388 is 2.39."""
    try:
        have, value = float(have), float(value)
    except (TypeError, ValueError):
        return False
    return {"gte": have >= value, "lte": have <= value, "gt": have > value, "lt": have < value}.get(op, round(have, places) == round(value, places))


def _date_hit(op: str, value: Any, day: Optional[date], today: date) -> bool:
    """A date compared with another one (before, after), with today (within so many days,
    longer ago), or as it is written (a regex from the start of MM/DD/YYYY)."""
    if day is None:
        return False
    if op == "regex":
        pattern = _pattern(str(value))
        return pattern is not None and pattern.match(day.strftime(DATE_FORMAT)) is not None
    if op in DATE_OPS:
        limit = _day(value)
        return limit is not None and (day < limit if op == "before" else day > limit)
    try:
        days = int(value)
    except (TypeError, ValueError):
        return False
    age = (today - day).days
    return 0 <= age <= days if op == "within" else age > days


def _files(ctx: ItemContext) -> List[Dict[str, Any]]:
    return [f for f in (ctx.media or []) if isinstance(f, dict)]


def _lookups(ctx: ItemContext) -> Any:
    if ctx.lookups is None:
        raise Unavailable("nothing to ask the media server, the Arrs or lists with")
    return ctx.lookups


def _holds(spec: Field, op: str, value: Any, ctx: ItemContext, meta: Dict[str, Any], today: date, instance: str = "", region: str = "") -> bool:
    """Does the positive comparison hold? (The caller turns a negative one around.)"""
    tmdb = meta.get("tmdb") or {}
    key = spec.key
    if instance and key in SERVER_FIELDS and instance != ctx.rel_path.split("|", 1)[0]:
        if ctx.lookups is not None:
            ctx.lookups.check_server(instance)  # warns when that server is gone
        return False  # another server's label (genre, rating...): this poster is not on that server
    fits = None if op == "is" else (lambda name: _text_hit(op, str(value), [name]))  # a name compared in part
    if key in ("label", "genre", "collection", "studio"):
        if fits is None and key == "studio":  # a server asks for a studio by its exact spelling: found among the library's names
            whole = str(value).strip().casefold()
            fits = lambda name: name.strip().casefold() == whole  # noqa: E731
        return _lookups(ctx).on_server(ctx, key, str(value)) if fits is None else _lookups(ctx).on_server_like(ctx, key, fits)
    if key in ARR_TAG_FIELDS:
        return _lookups(ctx).arr_tagged(ctx, ARR_TAG_FIELDS[key], str(value), instance, fits)
    if key == "list":
        return _lookups(ctx).listed(ctx, str(value))
    if key == "content_rating":
        return _text_hit(op, str(value), [ctx.content_rating or ""])
    if key == "title":
        return _text_hit(op, str(value), [ctx.title])
    if key in ("edition", "has_edition"):
        tags = [m.group(1).replace("-", " ") for p in [*ctx.paths, *(str(f.get("path") or "") for f in _files(ctx))] for m in _EDITION_TAG.finditer(p)]
        if key == "has_edition":
            return (ctx.media_type == MEDIA_MOVIE and any(str(name or "").strip() for name in [ctx.edition, *tags])) == (value == "yes")
        return ctx.media_type == MEDIA_MOVIE and _text_hit(op, str(value), [ctx.edition, *tags])
    if key == "filename":
        files = _files(ctx)
        if not files:
            return _text_hit(op, str(value), ctx.paths)
        hits = sum(1 for f in files if _text_hit(op, str(value), [str(f.get("path") or "")]))
        return hits > 0 if ctx.media_type == MEDIA_MOVIE else hits * 100 >= DEFAULT_PERCENT * len(files)
    if key in ("released", "added", "last_aired"):
        if key == "last_aired":
            when = (tmdb.get("last_episode_to_air") or {}).get("air_date") or tmdb.get("last_air_date") if ctx.media_type != MEDIA_MOVIE else None
        else:
            when = ctx.release_date if key == "released" else ctx.added_at
        return _date_hit(op, value, _day(when), today)
    episodes = ctx.media_type != MEDIA_MOVIE
    if key in ("resolution", "hdr"):
        found = resolution_badge(_files(ctx), episodes=episodes)
        res, flavor = found if found else ("", "")
        if key == "resolution":
            return res == value
        return bool(flavor) if value == "any" else flavor in _HDR_FLAVORS.get(str(value), ())
    if key == "audio":
        return audio_badge(_files(ctx), episodes=episodes) == value
    if key == "video_codec":
        return video_codec(_files(ctx), episodes=episodes) == value
    if key in ("audio_language", "subtitle_language"):
        return _text_hit("is", str(value), spoken(_files(ctx), key.split("_")[0], episodes=episodes))
    if key == "collected":
        return _lookups(ctx).collected(ctx) == (value == "yes")
    if key in CHARTS:  # in the chart's top N, or not
        try:
            top = int(float(value))
        except (TypeError, ValueError):
            return False
        rank = _lookups(ctx).chart_rank(ctx, key, top, region if key in REGION_CHARTS else "")
        return rank is not None and rank <= top if op == "lte" else rank is None or rank > top
    if key == "tmdb_list":
        return _lookups(ctx).in_tmdb_list(ctx, str(value))
    if key == "id_list":
        return _lookups(ctx).in_id_list(ctx, str(value))
    named = {"tmdb_genre": "genres", "tmdb_keyword": "keywords", "country": "countries", "tmdb_network": "networks", "tmdb_company": "companies"}
    if key in named:
        return _text_hit(op, str(value), tmdb.get(named[key]) or [])
    one = {"original_language": "original_language", "tmdb_status": "status", "tmdb_type": "type", "tmdb_collection": "collection"}
    if key in one:
        return _text_hit(op, str(value), [str(tmdb.get(one[key]) or "")])
    measured = {"audio_channels": "channels", "aspect_ratio": "aspect", "runtime": "minutes", "bitrate": "bitrate", "video_width": "width", "video_height": "height"}
    if key in measured:
        return _number_hit(op, typical(_files(ctx), measured[key], episodes=episodes) or None, value, _places(spec))  # 0 = the server did not say
    numbers: Dict[str, Callable[[], Any]] = {
        "year": lambda: ctx.year,
        "audience_rating": lambda: ctx.ratings.get("audience"),
        "critic_rating": lambda: ctx.ratings.get("critic"),
        "user_rating": lambda: ctx.ratings.get("user"),
        "versions": lambda: len(_files(ctx)) if ctx.media_type == MEDIA_MOVIE and ctx.media is not None else None,
        "audio_language_count": lambda: len(spoken(_files(ctx), "audio", episodes=episodes)) if ctx.media is not None else None,
        "subtitle_language_count": lambda: len(spoken(_files(ctx), "subtitle", episodes=episodes)) if ctx.media is not None else None,
        "tmdb_rating": lambda: tmdb.get("vote_average") if tmdb.get("vote_count") else None,
        "tmdb_votes": lambda: tmdb.get("vote_count") if tmdb else None,
    }
    if key in numbers:
        return _number_hit(op, numbers[key](), value, _places(spec))
    return False


def rule_holds(rule: Dict[str, Any], ctx: ItemContext, meta: Dict[str, Any], today: Optional[date] = None) -> bool:
    spec = FIELDS.get(str(rule.get("field") or ""))
    if spec is None:
        return False
    op = str(rule.get("op") or "")
    positive = _NEGATIVE.get(op)
    # Any one of the rule's values will do. A source that does not answer only matters when no other value settles it.
    hit, unanswered = False, None
    for part in [rule, *(p for p in rule.get("also") or [] if isinstance(p, dict))]:
        try:
            if _holds(spec, positive or op, part.get("value"), ctx, meta or {}, today or date.today(), str(part.get("instance") or ""), str(rule.get("region") or "")):
                hit = True
                break
        except Unavailable as exc:
            unanswered = exc
    if not hit and unanswered is not None:
        raise unanswered
    return not hit if positive else hit


def matches(rules: List[Dict[str, Any]], mode: str, ctx: ItemContext, meta: Dict[str, Any], today: Optional[date] = None) -> bool:
    """Do an overlay's conditions hold for the item? May raise Unavailable."""
    if not rules:
        return True
    results = (rule_holds(rule, ctx, meta, today) for rule in rules)
    return any(results) if mode == "any" else all(results)
