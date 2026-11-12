"""Rename files and folders in Sonarr/Radarr from the Tools page, the way the Renameinatorr
script does (BZ00001's standalone take on Drazzilb08's DAPS renameinatorr, matched to its
1.6.0), with the checks riyu963's arr_rename.py adds. The arr decides what needs renaming
and does the renaming with its own naming format; Posterflow asks per item, holds back
renames that look wrong (a folder name that drops the year or an id, an episode title that
is still TBA), can keep to the files whose TBA title has since been filled in (Sonarr), renames
an item not downloaded yet in the arr only, skips an item whose folder
is gone while the arr still has files on record there, confirms that every file is still
there afterwards, and tags what it processed so the next run moves on. The run that checks
the last untagged items takes the tag off them all, so the next run starts a new pass.
Settings are per instance, as in the script's yml: a 4K Radarr can run a different count
than the main one."""

from __future__ import annotations

import difflib
import json
import re
import time
import unicodedata
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import quote

from sqlalchemy.orm import Session

from core.logging import LogTags, log_debug, log_info, log_warning
from models.setting import get_setting_value

ProgressCallback = Callable[[str, int, int, str], None]

KINDS = ("sonarr", "radarr")
SCOPES = ("files", "folders", "both", "tba")  # tba: Sonarr only, the files whose episode title was TBA and now has a real one
SCOPE_TEXT = {"files": "files", "folders": "folders", "both": "files and folders", "tba": "TBA titles only"}
DEFAULTS: Dict[str, Any] = {
    "count": 25,            # items per run (0 = all)
    "batch": False,         # whole library in chunks of count, in one run
    "tag_name": "renamed",  # tag applied to processed items; blank = no cycle, every run checks everything
    "ignore_tag": "rename-ignore",
    "scope": "files",       # files | folders (files inside a renamed folder go with it) | both | tba (Sonarr)
    "max_renames": 0,       # items renamed per run at most; the rest wait for the next runs (0 = no limit)
    "scan_to_limit": False,  # check as many items as it takes to rename max_renames of them; count and batch then do not apply
    "ignore_case": False,   # a rename that only changes capitals is left alone
    "ignore_punctuation": False,  # and one that only changes special characters
    "allow_irregular": False,  # rename a folder even when its new name drops the year, an id or the title
    "hold_tba": True,       # Sonarr: a file whose new name still says TBA waits for the real title
    "refresh_first": False,  # metadata refresh before the rename check
    "title_filter": "",     # only items whose title contains this (for trying one item)
    "notify": False,        # a Discord / Apprise summary after every run
    "notify_all": False,    # every rename goes out, as several messages when long; off = one message, trimmed
}
MAX_COUNT = 1000
_DEFAULT_CHUNK = 100
_SCAN_CHUNK = 25  # scanning to the rename limit: items checked between tag updates, and refreshed at once
_POLL = 1
_COMMAND_TIMEOUT = 3600  # seconds to wait for an arr command; a stuck arr stops the run instead of holding it forever
_BUSY = ("queued", "started")
_TAG_RULE = re.compile(r"[A-Za-z0-9-]+")  # the arr makes no other tag, so a run with one could never move on
NOTIFY_FEATURE = "tools"

# Folder and file names: the id tags and the year the arr writes, and what counts as punctuation.
_ID_TAG = re.compile(r"[\[{](tvdb|tmdb|tvmaze|imdb)(?:id)?[-=](tt\d+|\d*)[\]}]", re.IGNORECASE)
_ID_FIELDS = {"tvdb": "tvdbId", "tmdb": "tmdbId", "tvmaze": "tvMazeId", "imdb": "imdbId"}
_YEAR = re.compile(r"\((\d{4})\)")
_SPECIAL = re.compile(rf"({_ID_TAG.pattern}|{_YEAR.pattern})|[^\w\s]|_", re.IGNORECASE)
_TBA = re.compile(r"(?<= )TBA(?= |\.\w+$|$)")  # a TBA that is a word of its own: FOOTBALL has none
_PART_ENDING = re.compile(r":?\s?(?:\(\d+\)|(?:Part|Pt\.?)\s?\d+)$", re.IGNORECASE)  # "(1)" / "Part 1": Sonarr drops it when one file name holds several titles
_EMPTY_YEAR = re.compile(r"\(\s*\)")  # "()" where the arr had no year to write
_PART = re.compile(r"\[[^\]]*\]|\{[^}]*\}|\([^)]*\)|[^\s\[\]{}().]+")  # a bracketed group, or a word between spaces and dots
_SEPARATOR = re.compile(r"[-–_.,:;!]+")


# ── settings ───────────────────────────────────────────────────────────────
def normalize_one(given: Any, kind: Optional[str] = None) -> Dict[str, Any]:
    """One instance's settings with the defaults filled in and every value coerced to its type."""
    given = given if isinstance(given, dict) else {}

    def number(key: str, top: int) -> int:
        try:
            return min(max(int(given.get(key, DEFAULTS[key])), 0), top)
        except (TypeError, ValueError):
            return DEFAULTS[key]

    scope = str(given.get("scope", DEFAULTS["scope"]) or "").strip().lower()
    return {
        "count": number("count", MAX_COUNT),
        "batch": bool(given.get("batch", DEFAULTS["batch"])),
        "tag_name": str(given.get("tag_name", DEFAULTS["tag_name"]) or "").strip(),
        "ignore_tag": str(given.get("ignore_tag", DEFAULTS["ignore_tag"]) or "").strip(),
        "scope": scope if scope in SCOPES and not (scope == "tba" and kind == "radarr") else DEFAULTS["scope"],  # movies have no episode titles
        "max_renames": number("max_renames", MAX_COUNT),
        "scan_to_limit": bool(given.get("scan_to_limit", DEFAULTS["scan_to_limit"])),
        "ignore_case": bool(given.get("ignore_case", DEFAULTS["ignore_case"])),
        "ignore_punctuation": bool(given.get("ignore_punctuation", DEFAULTS["ignore_punctuation"])),
        "allow_irregular": bool(given.get("allow_irregular", DEFAULTS["allow_irregular"])),
        "hold_tba": bool(given.get("hold_tba", DEFAULTS["hold_tba"])),
        "refresh_first": bool(given.get("refresh_first", DEFAULTS["refresh_first"])),
        "title_filter": str(given.get("title_filter", DEFAULTS["title_filter"]) or "").strip()[:200],
        "notify": bool(given.get("notify", DEFAULTS["notify"])),
        "notify_all": bool(given.get("notify_all", DEFAULTS["notify_all"])),
    }


def _phrase_change(old: str, new: str) -> str:
    """One changed phrase: just the words that changed when that is one small edit ("& → and",
    "adds Reloaded"), or both phrases whole when the edits are scattered or cover most of it
    ("The Trouble with Harry → A Little Town with an Accent")."""
    a, b = old.split(" "), new.split(" ")
    edits = [op for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != "equal"]
    if len(edits) == 1:
        _op, i1, i2, j1, j2 = edits[0]
        if max(i2 - i1, j2 - j1) * 2 < max(len(a), len(b)):
            gone, came = " ".join(a[i1:i2]), " ".join(b[j1:j2])
            return f"{gone} → {came}" if gone and came else f"adds {came}" if came else f"drops {gone}"
    return f"{old} → {new}"


def _changes(gone: List[str], came: List[str]) -> List[str]:
    """What one stretch of a name became: each id tag and the year on their own ("adds tmdb id 5",
    "year 2019 → 2020"), then the rest as old → new, adds or drops."""
    out = []
    labels = {}  # what an id tag or the year is called: "tmdb id", "year"
    old_values, new_values, old_raw, new_raw = {}, {}, {}, {}
    for parts, values, raw in ((gone, old_values, old_raw), (came, new_values, new_raw)):
        for part in parts:
            tag, year = _ID_TAG.fullmatch(part), _YEAR.fullmatch(part)
            if tag:
                key = tag.group(1).lower()
                labels[key], values[key], raw[key] = f"{key} id", tag.group(2) or "(empty)", part
            elif year:
                labels["year"], values["year"], raw["year"] = "year", year.group(1), part
    for key in labels:
        before, after = old_values.get(key), new_values.get(key)
        if before is None:
            out.append(f"adds {labels[key]} {after}")
        elif after is None:
            out.append(f"drops {labels[key]} {before}")
        elif before != after:
            out.append(f"{labels[key]} {before} → {after}")
        elif old_raw[key] != new_raw[key]:  # the same id, written another way: [tmdbid-5] → {tmdb-5}
            out.append(f"{old_raw[key]} → {new_raw[key]}")
    rest_gone = [p for p in gone if not (_ID_TAG.fullmatch(p) or _YEAR.fullmatch(p))]
    rest_came = [p for p in came if not (_ID_TAG.fullmatch(p) or _YEAR.fullmatch(p))]
    bracketed = lambda part: part[:1] in "[{("  # noqa: E731
    if len(rest_gone) == len(rest_came) and all(bracketed(a) == bracketed(b) for a, b in zip(rest_gone, rest_came)):
        out += [f"{a} → {b}" if bracketed(a) else _phrase_change(a, b) for a, b in zip(rest_gone, rest_came) if a != b]  # part for part
    elif rest_gone and rest_came:
        out.append(f"{' '.join(rest_gone)} → {' '.join(rest_came)}")
    elif rest_came or rest_gone:
        out.append(f"adds {' '.join(rest_came)}" if rest_came else f"drops {' '.join(rest_gone)}")
    return out


def _parts(name: str) -> List[str]:
    """A name's parts: its bracketed groups and the words between spaces and dots. Spacing inside
    a part does not count as a change."""
    return [" ".join(part.split()) for part in _PART.findall(name)]


def _units(parts: List[str]) -> List[str]:
    """The parts, with each run of plain words joined into one phrase, so an episode title is
    compared whole. Separators end a phrase and are left out."""
    units: List[str] = []
    words: List[str] = []
    for part in parts:
        separator = bool(_SEPARATOR.fullmatch(part))
        if separator or part[:1] in "[{(":
            if words:
                units.append(" ".join(words))
                words = []
            if not separator:
                units.append(part)
        else:
            words.append(part)
    if words:
        units.append(" ".join(words))
    return units


def _marks(text: str) -> List[str]:
    return [c for c in unicodedata.normalize("NFC", text) if unicodedata.category(c)[0] in "PS"]


def _punctuation_change(old: str, new: str) -> str:
    """ "punctuation", with the marks that went or came: "punctuation, drops '", "punctuation : → -"."""
    before, after = Counter(_marks(old)), Counter(_marks(new))
    gone, came = "".join((before - after).elements()), "".join((after - before).elements())
    if gone and came:
        return f"punctuation {gone} → {came}"
    return f"punctuation, drops {gone}" if gone else f"punctuation, adds {came}" if came else "punctuation"


def _name_changes(a: str, b: str) -> List[str]:
    """What changed between two names (no folder or extension): capitals, accents, spacing or
    punctuation when that is all, the order of their parts, or else each id tag, the year and the
    rest on their own, a run of words (an episode title) as one phrase."""
    if a == b:
        return []
    if _lower(a) == _lower(b):
        return (["accents encoded differently"] if a.lower() != b.lower() else []) + \
            (["capitals"] if unicodedata.normalize("NFC", a) != unicodedata.normalize("NFC", b) else [])
    old_parts, new_parts = _parts(a), _parts(b)
    if old_parts == new_parts:
        return ["spacing"]
    if unpunctuated(a) == unpunctuated(b):
        return [_punctuation_change(a, b)]
    if sorted(old_parts) == sorted(new_parts):
        moved = [part for part, other in zip(old_parts, new_parts) if part != other]
        return ["id order" if all(_ID_TAG.fullmatch(part) for part in moved) else "order"]
    out: List[str] = []
    old_units, new_units = _units(old_parts), _units(new_parts)
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_units, new_units, autojunk=False).get_opcodes():
        if op != "equal":
            out += _changes(old_units[i1:i2], new_units[j1:j2])
    return out


def _title(name: str) -> str:
    """A name's title in its own capitals: without its id tags, its year and an empty ()."""
    rest = _ID_TAG.sub("", name)
    years = list(_YEAR.finditer(rest))
    if years:
        rest = rest[:years[-1].start()] + rest[years[-1].end():]
    return " ".join(_EMPTY_YEAR.sub(" ", rest).split())


def _title_change(name: str, expected: str) -> List[str]:
    """How a folder's title differs from the one the arr wants: punctuation alone (and capitals),
    or the change in their own capitals, small or whole ("title: & → and")."""
    old, new = _title(name), _title(expected)
    if _lower(unpunctuated(old)) == _lower(unpunctuated(new)):
        return [_punctuation_change(old, new)] + (["capitals"] if unpunctuated(old) != unpunctuated(new) else [])
    return [f"title: {_phrase_change(old, new)}"]


def _stem(name: str) -> Tuple[str, str]:
    stem, dot, ext = name.rpartition(".")
    return (stem, f".{ext}") if dot and stem else (name, "")


def rename_reasons(old: str, new: str) -> str:
    """Why the arr renames a file, read off the two names: the folder it moves to, then what the
    new name adds, drops or changes ("adds tmdb id 5", "TBA → Pilot", "[WEBDL-1080p] → [Bluray-1080p]")."""
    reasons = []
    old_dir, old_file = _split(old)
    new_dir, new_file = _split(new)
    if old_dir != new_dir:
        reasons.append(f"moves from {old_dir or 'the main folder'} to {new_dir or 'the main folder'}")
    (a, a_ext), (b, b_ext) = _stem(old_file), _stem(new_file)
    if a_ext.lower() != b_ext.lower():
        reasons.append(f"{a_ext or 'no extension'} → {b_ext or 'no extension'}")
    reasons += _name_changes(a, b)
    return "; ".join(reasons) or "formatted differently"


def _loose(text: str) -> str:
    """Only the letters and digits of `text`, in lower case and without accents, for comparing a
    title with the way a naming format writes it ("Marnûkh" may come out as "Marnukh"). Accents
    come off Latin, Greek and Cyrillic letters only: a Japanese mark changes the sound."""
    out = []
    for char in unicodedata.normalize("NFC", text):
        base, *marks = unicodedata.normalize("NFD", char)
        out.append(base if marks and ord(base) < 0x530 and all(unicodedata.combining(m) for m in marks) else char)
    return re.sub(r"[\W_]", "", "".join(out)).casefold()


def _changed_part(old: str, new: str) -> Tuple[str, str]:
    """The one stretch that differs between two names, each side widened to whole words:
    "S01E01 - TBA.mkv" and "S01E01 - Pilot.mkv" give ("TBA", "Pilot")."""
    start = 0
    while start < min(len(old), len(new)) and old[start] == new[start]:
        start += 1
    while start and old[start - 1].isalnum():
        start -= 1
    end = 0
    while end < min(len(old), len(new)) - start and old[len(old) - 1 - end] == new[len(new) - 1 - end]:
        end += 1
    while end and old[len(old) - end].isalnum():
        end -= 1
    return old[start:len(old) - end], new[start:len(new) - end]


def _only_titles(text: str, titles: List[str]) -> bool:
    """Is `text` the episode titles and nothing else, the way Sonarr writes them in a file name?
    One title, or the titles of a file holding several episodes: joined, or merged as Sonarr merges
    them (each once, without a "(1)" / "Part 1" ending). A text ending in "..." may be any of
    those cut short. Capitals, punctuation and accents do not count."""
    merged = dict.fromkeys(_PART_ENDING.sub("", t).strip() for t in titles)
    forms = {_loose(t) for t in titles} | {_loose("".join(titles)), _loose("".join(merged))}
    got = _loose(text)
    if text.rstrip().endswith(("...", "…")):
        return bool(got) and any(form.startswith(got) for form in forms)
    return bool(got) and got in forms


def tba_fill(old: str, new: str, titles: List[str]) -> Tuple[str, str]:
    """What renaming the file `old` to `new` is, for a run that only fills in TBA titles. `titles`
    are the file's episode titles as the arr has them now.
    ("fill", "")     the one change is a TBA title getting its real title
    ("unsure", why)  a TBA goes, but not clearly as that: a person should look
    ("", "")         nothing to do with a TBA title being filled in"""
    moved = _split(old)[0] != _split(new)[0]
    was, now = _basename(old), _basename(new)
    if not _TBA.search(was) or _loose(was) == _loose(now):
        return "", ""
    if moved:
        return "unsure", "it also moves to another season folder"
    known = [t for t in titles if t and t != "TBA"]
    if any(_TBA.search(f" {t}") and t not in was for t in known):
        return "unsure", "its new title has TBA in it"
    if known:
        longest_first = sorted(set(known), key=len, reverse=True)  # a short title must not match inside a longer one
        back = re.sub("|".join(re.escape(t) for t in longest_first), "TBA", now)
        if back == was and back != now:
            return "fill", ""
        gone, came = _changed_part(was, now)
        if gone == "TBA" and not _TBA.search(f" {came} ") and _only_titles(came, known):
            return "fill", ""
    if len(_TBA.findall(was)) > len(_TBA.findall(now)):
        return "unsure", "more than the episode title changes" if known else "the arr gave no episode title to check it against"
    return "", ""


def scans_to_limit(settings: Dict[str, Any]) -> bool:
    """Does a run check as many items as it takes to reach the rename limit? Only with a limit."""
    return bool(settings.get("scan_to_limit") and settings.get("max_renames"))


def tag_label_problem(label: str) -> str:
    """Why an arr could never make this tag, or "" when it can: letters, digits and - only."""
    return "" if _TAG_RULE.fullmatch(label) else f"The tag '{label}' can only have letters, digits and -."


def tag_problem(settings: Dict[str, Any]) -> str:
    """Why these tags cannot work: letters, digits and - only (the arr makes no other tag), and
    the cycle tag cannot be the ignore tag (every item would be ignored once processed). "" = fine."""
    for key, what in (("tag_name", "cycle tag"), ("ignore_tag", "ignore tag")):
        tag = settings.get(key) or ""
        if tag and not _TAG_RULE.fullmatch(tag):
            return f"The {what} '{tag}' can only have letters, digits and -."
    if settings.get("tag_name") and settings["tag_name"].lower() == (settings.get("ignore_tag") or "").lower():
        return "The cycle tag and the ignore tag must differ."
    return ""


def load_arr_instances(db: Session, kind: str) -> List[Dict[str, str]]:
    """The configured Sonarr or Radarr instances that have a url and an api key."""
    raw = get_setting_value(db, f"{kind}_instances")
    try:
        parsed = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        parsed = []
    instances = [
        {"name": str(i.get("name") or "").strip(), "url": str(i.get("url") or "").strip(), "api_key": str(i.get("api_key") or "").strip()}
        for i in (parsed if isinstance(parsed, list) else []) if isinstance(i, dict)
    ]
    return [i for i in instances if i["url"] and i["api_key"]]


def arr_instance_names(db: Session, kind: str) -> List[str]:
    return [i["name"] or i["url"] for i in load_arr_instances(db, kind)]


def _connect(instance: Dict[str, str], kind: str) -> Any:
    from util.arr.client import create_arr_client

    return create_arr_client(instance["url"], instance["api_key"], kind, None)


# ── names: what a rename changes, and what it would lose ───────────────────
def _fold(s: str) -> str:
    """Lower case with the accents stored one way, for spotting clashes (matches ß with ss too)."""
    return unicodedata.normalize("NFC", s).casefold()


def _split(path: str) -> Tuple[str, str]:
    """(parent, name) of a path with either separator, neither with a trailing one."""
    path = path.rstrip("/\\")
    cut = max(path.rfind("/"), path.rfind("\\"))
    return (path[:cut], path[cut + 1:]) if cut >= 0 else ("", path)


def _basename(path: str) -> str:
    return _split(path)[1]


def _join(parent: str, name: str) -> str:
    sep = "\\" if "\\" in parent and "/" not in parent else "/"
    return f"{parent}{sep}{name}"


def _lower(s: str) -> str:
    return unicodedata.normalize("NFC", s).lower()


def _id_value(value: Any) -> Any:
    """An id in a form that compares: a number, or lower-case text (IMDb's tt...); None when missing."""
    if isinstance(value, str):
        return int(value) if value.isdigit() else (value.casefold() or None)
    return value or None


def parse_name(name: str) -> Tuple[str, Optional[int], Dict[str, Any]]:
    """A folder or file name split into (lower-case title, year, {tvdb: id, ...})."""
    ids = {k.lower(): _id_value(v) for k, v in _ID_TAG.findall(name)}
    rest = _ID_TAG.sub("", name)
    years = list(_YEAR.finditer(rest))
    year = None
    if years:
        year = int(years[-1].group(1))
        rest = rest[:years[-1].start()] + rest[years[-1].end():]
    rest = _EMPTY_YEAR.sub(" ", rest)
    return " ".join(_lower(rest).split()), year, ids


def unpunctuated(name: str) -> str:
    """`name` without its special characters, except inside its year and id tags: "What's Up! (2020)"
    becomes "Whats Up (2020)". An accent mark stored apart from its letter is part of the letter."""
    def kept(m: "re.Match[str]") -> str:
        return m.group(1) or (m.group() if unicodedata.category(m.group()).startswith("M") else "")
    return " ".join(_SPECIAL.sub(kept, name).split())


def title_key(name: str) -> Tuple[str, Optional[int]]:
    """A name's title and year without capitals or punctuation: "Mission - Impossible (1996)" and
    "Mission: Impossible (1996)" give the same key."""
    title, year, _ids = parse_name(name)
    return _fold(unpunctuated(title)), year


def passed_over(name: str, expected: str) -> Tuple[str, ...]:
    """How two names differ when `problems` let them pass: ("capitals",), ("punctuation",), both, or ()."""
    if name == expected:
        return ()
    if _lower(name) == _lower(expected):
        return ("capitals",)
    if unpunctuated(name) == unpunctuated(expected):
        return ("punctuation",)
    return ("punctuation", "capitals")


def problems(name: str, expected: str, ids: Optional[Dict[str, Any]] = None, ignore_case: bool = False,
             ignore_punctuation: bool = False) -> Tuple[List[str], List[str]]:
    """Compare a name with the arr's `expected` one: (what is wrong with it, what the new name would
    lose). Both empty when they match. `ids` are the arr's ids for the item, to tell a wrong id from
    a changed one. With `ignore_case` a difference only in capitals is no problem; with
    `ignore_punctuation` neither is one only in special characters."""
    a, b = (unpunctuated(name), unpunctuated(expected)) if ignore_punctuation else (name, expected)
    if a == b:
        return [], []
    if _lower(a) == _lower(b):
        out = ["accents encoded differently"] if a.lower() != b.lower() else []
        if not ignore_case and unicodedata.normalize("NFC", a) != unicodedata.normalize("NFC", b):
            out.append("capitals")
        return out, []
    title, year, have = parse_name(name)
    want_title, want_year, want = parse_name(expected)
    out = []
    if title != want_title:
        out += _title_change(name, expected)
    if year is not None and want_year is not None and year != want_year:
        out.append(f"year {year} should be {want_year}")
    elif year is None and want_year is not None:
        out.append("no year")
    for k, v in have.items():
        known = _id_value((ids or {}).get(k))
        if known and v != known:
            out.append(f"{k} id {v} should be {known}" if v else f"empty {k} id")
    out += [f"no {k} id" for k in want if k not in have]
    irregular = []
    if title and not want_title:
        irregular.append("the new name has no title")
    if year is not None and want_year is None:
        irregular.append(f"the new name drops the year {year}")
    irregular += [f"the new name drops the {k} id {v}" for k, v in have.items() if k not in want]
    irregular += [f"the new name has an empty {k} id" for k, v in want.items() if not v]
    return out or _name_changes(name, expected) or ["formatted differently"], irregular


# ── the arr ────────────────────────────────────────────────────────────────
class _Stopped(Exception):
    """A rename that did not check out, or an arr that stopped answering: this instance's run stops
    here, so nothing else is moved until someone has looked."""


class _Arr:
    """The v3 calls the renamer needs, on top of a connected arr client (anything with `url`
    and make_get/post/put_request)."""

    def __init__(self, client: Any, kind: str) -> None:
        self.client = client
        self.kind = kind
        self.noun = "movie" if kind == "radarr" else "series"
        self.id_param = "movieId" if kind == "radarr" else "seriesId"
        self.file_key = "movieFileId" if kind == "radarr" else "episodeFileId"
        self.file_noun = "moviefile" if kind == "radarr" else "episodefile"
        self.refresh_name = "RefreshMovie" if kind == "radarr" else "RefreshSeries"
        self.move_names = ("BulkMoveMovie", "MoveMovie") if kind == "radarr" else ("BulkMoveSeries", "MoveSeries")

    def _url(self, path: str) -> str:
        return f"{self.client.url}/api/v3/{path}"

    def _get(self, path: str) -> Any:
        return self.client.make_get_request(self._url(path))

    def _read(self, path: str, what: str) -> Any:
        """A read the run depends on. The client gives None for any failure, which must not pass for 'nothing to do'."""
        raw = self._get(path)
        if raw is None:
            raise _Stopped(f"the arr did not answer when asked for {what}")
        return raw

    def _post_command(self, body: Dict[str, Any]) -> Optional[int]:
        resp = self.client.make_post_request(self._url("command"), body)
        return int(resp["id"]) if isinstance(resp, dict) and resp.get("id") else None

    @staticmethod
    def _row(raw: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": int(raw["id"]), "title": str(raw.get("title") or "Unknown"), "year": raw.get("year") or 0,
            "tags": [int(t) for t in (raw.get("tags") or [])], "path": str(raw.get("path") or "").rstrip("/\\"),
            "root": str(raw.get("rootFolderPath") or ""),
            "ids": {k: _id_value(raw.get(field)) for k, field in _ID_FIELDS.items()},
        }

    def media(self) -> List[Dict[str, Any]]:
        rows = self._read(self.noun, "its library")
        return [self._row(r) for r in (rows if isinstance(rows, list) else []) if isinstance(r, dict) and r.get("id")]

    def media_one(self, media_id: int) -> Optional[Dict[str, Any]]:
        raw = self._get(f"{self.noun}/{media_id}")
        return self._row(raw) if isinstance(raw, dict) and raw.get("id") else None

    def gone(self, media_id: int) -> bool:
        """Was the item deleted from the arr? No when the arr itself stopped answering."""
        return self._get(f"{self.noun}/{media_id}") is None and self._get("system/status") is not None

    def folder_name(self, media_id: int) -> str:
        """The folder name the arr's naming format gives the item now."""
        raw = self._get(f"{self.noun}/{media_id}/folder")
        if not isinstance(raw, dict) or raw.get("folder") is None:
            raise _Stopped("the arr gave no folder name. Folder renames need Sonarr 4.0.11 or Radarr 5.22.3 or newer")
        return str(raw["folder"])

    def root_folders(self) -> List[str]:
        rows = self._read("rootfolder", "its root folders")
        return [str(r.get("path") or "").rstrip("/\\") for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def folders_in(self, parent: str) -> List[str]:
        """The folders inside `parent` as the arr sees them (none for a folder that is not there)."""
        raw = self._read(f"filesystem?path={quote(parent.rstrip('/') + '/', safe='/')}&includeFiles=false", f"the folders in {parent}")
        return [str(d.get("name") or "") for d in raw.get("directories") or [] if isinstance(d, dict)] if isinstance(raw, dict) else []

    def tracked(self, media_id: int) -> Dict[int, str]:
        """The files the arr has on record for the item: {file id: path relative to its folder}."""
        rows = self._read(f"{self.file_noun}?{self.id_param}={media_id}", "its file records")
        return {int(f["id"]): str(f.get("relativePath") or "") for f in rows if isinstance(f, dict) and f.get("id")} if isinstance(rows, list) else {}

    def on_disk(self, path: str) -> List[str]:
        """The video files under `path` as the arr sees them on disk, relative to it (none for a folder that is not there)."""
        rows = self._read(f"filesystem/mediafiles?path={quote(path, safe='/')}", f"the files in {path}")
        return [str(f.get("relativePath") or "") for f in rows if isinstance(f, dict)] if isinstance(rows, list) else []

    def tag_id(self, label: str, *, create: bool) -> Optional[int]:
        for tag in self._read("tag", "its tags"):
            if isinstance(tag, dict) and str(tag.get("label", "")).lower() == label.lower():
                return int(tag["id"])
        if not create:
            return None
        made = self.client.make_post_request(self._url("tag"), {"label": label})
        return int(made["id"]) if isinstance(made, dict) and made.get("id") else None

    def set_tags(self, media_ids: List[int], tag_id: int, *, add: bool) -> bool:
        if not media_ids:
            return True
        body = {f"{self.noun}Ids": media_ids, "tags": [tag_id], "applyTags": "add" if add else "remove"}
        return self.client.make_put_request(self._url(f"{self.noun}/editor"), body) is not None

    def rename_list(self, media_id: int) -> List[Tuple[int, str, str]]:
        """What the arr would rename for the item: (file id, old path, new path), relative to its folder."""
        rows = self._read(f"rename?{self.id_param}={media_id}", "what needs renaming")
        return [(int(r[self.file_key]), str(r.get("existingPath") or ""), str(r.get("newPath") or ""))
                for r in rows if isinstance(r, dict) and r.get(self.file_key)] if isinstance(rows, list) else []

    def episode_titles(self, series_id: int) -> Dict[int, List[str]]:
        """Sonarr: the episode titles of each file of a series, {file id: titles in episode order}."""
        rows = self._read(f"episode?seriesId={series_id}", "the episode titles")
        titles: Dict[int, List[str]] = {}
        episodes = [r for r in rows if isinstance(r, dict) and r.get("episodeFileId")] if isinstance(rows, list) else []
        for episode in sorted(episodes, key=lambda e: (e.get("seasonNumber") or 0, e.get("episodeNumber") or 0)):
            titles.setdefault(int(episode["episodeFileId"]), []).append(str(episode.get("title") or ""))
        return titles

    def rename_files(self, media_id: int, file_ids: List[int]) -> Optional[int]:
        return self._post_command({"name": "RenameFiles", self.id_param: media_id, "files": file_ids})

    def refresh(self, media_ids: List[int]) -> List[int]:
        if self.kind == "radarr":
            cid = self._post_command({"name": "RefreshMovie", "movieIds": media_ids})
            return [cid] if cid else []
        return [cid for cid in (self._post_command({"name": "RefreshSeries", "seriesId": m}) for m in media_ids) if cid]

    def move_to_root(self, media_ids: List[int], root: str) -> bool:
        """The arr renames the folders itself when items are 'moved' to the root they are in."""
        body = {f"{self.noun}Ids": media_ids, "rootFolderPath": root, "moveFiles": True}
        return self.client.make_put_request(self._url(f"{self.noun}/editor"), body) is not None

    def command_status(self, command_id: int) -> Optional[str]:
        raw = self._get(f"command/{command_id}")
        return str(raw.get("status") or "") if isinstance(raw, dict) else None

    def busy(self, names: Tuple[str, ...]) -> bool:
        """Is a command of one of these names queued or running?"""
        rows = self._read("command", "its running commands")
        return any(isinstance(c, dict) and c.get("name") in names and c.get("status") in _BUSY for c in rows) if isinstance(rows, list) else False


# ── the run ────────────────────────────────────────────────────────────────
class ArrRenamer:
    """One Tools run over every configured instance of one arr kind."""

    def __init__(self, db: Session, kind: str, *, instances: Optional[List[Dict[str, str]]] = None,
                 settings: Optional[Dict[str, Any]] = None, per_instance: Optional[Dict[str, Dict[str, Any]]] = None, client_factory: Optional[Callable[[Dict[str, str]], Any]] = None,
                 sleep: Callable[[float], None] = time.sleep, notifier: Optional[Callable[..., Any]] = None) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown arr kind '{kind}'")
        self.db = db
        self.kind = kind
        self.label = kind.capitalize()
        self.noun = "movie" if kind == "radarr" else "series"
        self.nouns = "movies" if kind == "radarr" else "series"
        # `settings` given = the same settings for every instance (tests, previews); else the
        # profile's `per_instance` map, the defaults for an instance it does not name.
        self._flat = normalize_one(settings, kind) if settings is not None else None
        self._saved = {} if settings is not None else {str(name): normalize_one(one, kind) for name, one in (per_instance or {}).items()}
        self.instances = list(instances) if instances is not None else load_arr_instances(db, kind)
        self._client_factory = client_factory or (lambda inst: _connect(inst, kind))
        self._sleep = sleep
        self._notifier = notifier
        self._progress: Optional[ProgressCallback] = None
        self._done = 0
        self._total = 0
        self._message = ""
        self.results: Dict[str, Dict[str, Any]] = {}  # instance name → what happened, for the summary and the notification

    def settings_for(self, name: str) -> Dict[str, Any]:
        return self._flat if self._flat is not None else (self._saved.get(name) or normalize_one({}, self.kind))

    @staticmethod
    def _new_result() -> Dict[str, Any]:
        return {"checked": 0, "items": [], "error": "", "stopped": "", "not_reviewed": 0, "tagged": 0, "later": 0, "blocked": 0, "blocked_files": 0, "unsure": 0, "deleted": 0, "missing": 0, "rounds": None,
                "ignored": {"capitals": 0, "punctuation": 0}, "ignored_files": {"capitals": 0, "punctuation": 0}, "lines": []}

    # ── progress ───────────────────────────────────────────────────────────
    def _tick(self, message: Optional[str] = None, advance: int = 0) -> None:
        """Report progress (and give a cancel a chance to land, also while waiting on the arr)."""
        self._done += advance
        if message is not None:
            self._message = message
        if self._progress:
            self._progress("rename", self._done, max(self._total, 1), self._message)

    # ── run ────────────────────────────────────────────────────────────────
    def run(self, *, dry_run: bool = False, progress: Optional[ProgressCallback] = None) -> Dict[str, Any]:
        self._progress = progress
        started = time.monotonic()
        stats = {"instances": 0, "unreachable": 0, "failed_instances": 0, "stopped": 0, "checked": 0, "items": 0, "files": 0, "folders": 0,
                 "held": 0, "forced": 0, "later": 0, "deleted": 0, "missing": 0, "ignored": 0, "ignored_files": 0, "failed": 0, "rescans": 0, "tagged": 0, "cycles": 0}
        mode = "dry run" if dry_run else "run"
        if not self.instances:
            log_warning(LogTags.TOOLS, f"No {self.label} instance is configured — nothing to rename")
            return stats

        # Plan first so the progress total covers every instance. An instance that fails is
        # reported and skipped; the others still run.
        plans = []
        for instance in self.instances:
            name = instance.get("name") or instance.get("url", self.label)
            s = self.settings_for(name)
            result = self.results[name] = self._new_result()
            pace = (f"as many item(s) as it takes to rename {s['max_renames']}" if scans_to_limit(s)
                    else f"{s['count'] or 'all'} item(s) per run" + (", whole library in batches" if s["batch"] else ""))
            log_info(LogTags.TOOLS, f"── {name} ({self.label}) ── {mode}: {pace}" + (f", cycle tag '{s['tag_name']}'" if s["tag_name"] else ", no cycle tag")
                     + (f", ignoring '{s['ignore_tag']}'" if s["ignore_tag"] else "") + f", renaming {SCOPE_TEXT[s['scope']]}"
                     + (f", at most {s['max_renames']} rename(s)" if s["max_renames"] and not scans_to_limit(s) else "")
                     + (", ignoring capitals" if s["ignore_case"] else "") + (", ignoring punctuation" if s["ignore_punctuation"] else "")
                     + (", renaming irregular folders" if s["allow_irregular"] else "")
                     + (", holding TBA titles" if s["hold_tba"] and self.kind == "sonarr" else "")
                     + (", refreshing metadata first" if s["refresh_first"] else "") + (f", titles containing '{s['title_filter']}'" if s["title_filter"] else ""))
            problem = tag_problem(s)
            if problem:
                stats["failed_instances"] += 1
                result["error"] = f"did not start: {problem}"
                log_warning(LogTags.TOOLS, f"{name}: not starting — {problem}")
                continue
            try:
                client = self._client_factory(instance)
            except Exception as exc:
                log_debug(LogTags.TOOLS, f"{name}: connection failed: {exc}")
                client = None
            if client is None:
                stats["unreachable"] += 1
                result["error"] = "cannot connect"
                log_warning(LogTags.TOOLS, f"Cannot connect to {self.label} '{name}' — skipping it")
                continue
            arr = _Arr(client, self.kind)
            try:
                chunks, cycle_tag = self._select(arr, name, s, dry_run, stats)
            except Exception as exc:
                stats["failed_instances"] += 1
                result["error"] = str(exc) or exc.__class__.__name__
                log_warning(LogTags.TOOLS, f"{name}: run failed: {exc}")
                continue
            plans.append((name, arr, s, chunks, cycle_tag))
            stats["instances"] += 1
        self._total = sum(len(chunk) for _, _, _, chunks, _ in plans for chunk in chunks)
        self._tick(f"{self.label} rename: checking {self._total} item(s)")

        for name, arr, s, chunks, cycle_tag in plans:
            instance_started = time.monotonic()
            result = self.results[name]
            run = _InstanceRun(self, arr, name, s, cycle_tag, dry_run, stats, result)
            try:
                for index, chunk in enumerate(chunks, 1):
                    if run.full:
                        break
                    if len(chunks) > 1 and not scans_to_limit(s):
                        log_info(LogTags.TOOLS, f"{name}: batch {index}/{len(chunks)} ({len(chunk)} item(s))")
                    run.process_chunk(chunk)
                if run.full:
                    unchecked = self._total_left(chunks, result["checked"])
                    log_info(LogTags.TOOLS, f"{name}: reached the rename limit of {s['max_renames']} after checking {result['checked']} item(s)"
                             + (f"; the other {unchecked} wait for the next runs" if unchecked else ""))
                    self._done += unchecked
                    self._tick()
                run.close_pass()
            except _Stopped as exc:
                stats["stopped"] += 1
                result["stopped"] = str(exc)
                result["not_reviewed"] = self._total_left(chunks, result["checked"])
                self._done += result["not_reviewed"]
                log_warning(LogTags.TOOLS, f"{name}: stopping — {exc}. Nothing else is renamed until you have looked at this"
                            + (f"; {result['not_reviewed']} item(s) not checked" if result["not_reviewed"] else ""))
            except Exception as exc:
                stats["failed_instances"] += 1
                result["error"] = str(exc) or exc.__class__.__name__
                log_warning(LogTags.TOOLS, f"{name}: run failed: {exc}")
                continue
            run.finish()
            for line in result["lines"]:
                log_info(LogTags.TOOLS, f"{name}: {line}")
            log_info(LogTags.TOOLS, f"{name}: finished in {time.monotonic() - instance_started:.1f}s")

        verb = "would be renamed" if dry_run else "renamed"
        log_info(LogTags.TOOLS, f"{self.label} rename ({mode}): {stats['instances']} instance(s), {stats['checked']} item(s) checked, {stats['files']} file(s) in {stats['items']} item(s) {verb}"
                 + (f", {stats['folders']} folder(s) {verb}" if stats["folders"] else "")
                 + (f", {stats['held']} held back" if stats["held"] else "") + (f", {stats['forced']} irregular" if stats["forced"] else "")
                 + (f", {stats['later']} left for later runs" if stats["later"] else "") + (f", {stats['deleted']} deleted mid-run" if stats["deleted"] else "")
                 + (f", {stats['missing']} not on disk" if stats["missing"] else "")
                 + (f", {stats['ignored']} folder(s) ignored" if stats["ignored"] else "") + (f", {stats['ignored_files']} file(s) ignored" if stats["ignored_files"] else "")
                 + (f", {stats['failed']} failed" if stats["failed"] else "") + (f", {stats['rescans']} rescanned" if stats["rescans"] else "")
                 + (f", {stats['tagged']} tagged" if stats["tagged"] else "") + (f", {stats['stopped']} instance(s) stopped" if stats["stopped"] else "")
                 + (f", {stats['unreachable']} instance(s) unreachable" if stats["unreachable"] else "")
                 + (f", {stats['failed_instances']} instance(s) failed" if stats["failed_instances"] else "")
                 + f", {time.monotonic() - started:.1f}s")
        self._notify(dry_run)
        return stats

    @staticmethod
    def _total_left(chunks: List[List[Dict[str, Any]]], checked: int) -> int:
        return max(sum(len(c) for c in chunks) - checked, 0)

    # ── notification: one summary per run, as the scripts post to Discord ──
    _PAGE_CHARS = 3300  # a diff block that fits an embed description beside its head and fences

    def _notify(self, dry_run: bool) -> None:
        wanted = {name: result for name, result in self.results.items() if self.settings_for(name).get("notify")}
        if not wanted:
            return
        fields: List[Dict[str, Any]] = []
        reasons: List[str] = []  # what a person has to look at; any makes this an error event, so the mention settings for errors decide on a ping
        changed_any = False
        pages: List[Tuple[str, str]] = []    # (head, diff text): one message each, before the summary
        trimmed: List[Tuple[str, List[str]]] = []  # (head, item blocks): what fits of them goes into the summary message
        for name, result in wanted.items():
            if result["error"]:
                reasons.append(f"{name}: the run failed")
                fields.append(self._field(f"{name}: run failed", result["error"]))
                continue
            if result["stopped"]:
                reasons.append(f"{name}: the run stopped")
                fields.append(self._field(f"{name}: run stopped", result["stopped"] + (f"\n{result['not_reviewed']} item(s) not checked" if result["not_reviewed"] else "")))
            fields.append(self._field(f"{name}: settings", self._settings_text(name, dry_run)))
            changed = [i for i in result["items"] if (i["files"] or i["folder"]) and not i["later"] and not i["failed"]]
            changed_any = changed_any or bool(changed)
            if changed:
                head = f"{name}: {len(changed)} of {result['checked']} {'would change' if dry_run else 'changed'}"
                blocks = [self._diff_block(i) for i in changed]
                counts = self._counts_text(changed)
                if self.settings_for(name).get("notify_all"):
                    own = self._pages(blocks, self._PAGE_CHARS)
                    pages.extend((head, page) for page in own)
                    fields.append(self._field(head, f"{counts}\nListed in the {len(own)} message(s) below"))
                else:
                    fields.append(self._field(head, counts))
                    trimmed.append((head, blocks))
            forced = [i for i in result["items"] if i["forced"]]
            if forced:
                reasons.append(f"{name}: {len(forced)} irregular folder(s) {'would be ' if dry_run else ''}renamed anyway")
                fields.append(self._field(f"{name}: {len(forced)} irregular", "\n".join(f"{i['label']}: {i['forced']}" for i in forced)))
            held = [i for i in result["items"] if i["held"] or i["waiting"]]
            irregular = sum(i["held"].startswith("irregular") for i in held)
            if irregular:
                reasons.append(f"{name}: {irregular} irregular folder(s) held back")
            unsure = sum(i["unsure"] for i in held)
            if unsure:
                reasons.append(f"{name}: {unsure} file(s) held back, more than a TBA title may change")
            if held:
                fields.append(self._field(f"{name}: {len(held)} held back", "\n".join(f"{i['label']}: {i['held'] or i['waiting']}" for i in held)))
            later = [i for i in result["items"] if i["later"]]
            if later:
                fields.append(self._field(f"{name}: {len(later)} left for the next runs", "\n".join(i["label"] for i in later)))
            deleted = [i for i in result["items"] if i["gone"]]
            if deleted:
                reasons.append(f"{name}: {len(deleted)} deleted from {self.label} mid-run")
                fields.append(self._field(f"{name}: {len(deleted)} deleted mid-run", "\n".join(i["label"] for i in deleted)))
            summary = "\n".join(result["lines"]) or (f"No items needed renaming ({result['checked']} checked)" if not changed else "")
            if summary:
                fields.append(self._field(f"{name}: this run", summary))
        if not fields and not pages and not trimmed:
            return
        send = self._notifier
        if send is None:
            from services.notifications import send_notification

            send = lambda **kw: send_notification(self.db, **kw)  # noqa: E731
        # An error event pings per the feature's mention settings; a plain rename is a success; a dry run only informs.
        event = "error" if reasons else "success" if changed_any and not dry_run else "info"
        color = {"error": 0xE74C3C, "success": 0x2ECC71, "info": 0x3498DB}[event]
        title = f"{self.label} rename" + (" (dry run)" if dry_run else "")
        parts = ["Needs a look: " + "; ".join(reasons)] if reasons else []
        if trimmed:  # the instances share the description; what does not fit is counted
            budget = self._PAGE_CHARS // len(trimmed)
            parts.extend(self._diff_text(head, blocks, budget) for head, blocks in trimmed)
        try:
            # The summary goes first and carries any ping; every page follows as its own message without one.
            send(feature_key=NOTIFY_FEATURE, event_type=event, title=title, description="\n\n".join(parts), fields=fields, color=color)
            for n, (head, page) in enumerate(pages, start=1):
                self._sleep(0.4)  # webhooks take a few messages a second
                send(feature_key=NOTIFY_FEATURE, event_type=event, title=f"{title} · changes {n} of {len(pages)}",
                     description=self._diff_text(head, [page], None), fields=[], color=color, mention=False)
        except Exception as exc:
            log_debug(LogTags.TOOLS, f"{self.label} rename: notification failed: {exc}")

    @staticmethod
    def _field(name: str, value: str) -> Dict[str, Any]:
        return {"name": name, "value": value if len(value) <= 1000 else value[:996] + "\n…", "inline": False}

    def _settings_text(self, name: str, dry_run: bool) -> str:
        """The settings the instance ran with, as one line: what is renamed, the switches that
        are on, the pace, the tags, and whether anything was actually changed."""
        s = self.settings_for(name)
        parts = [SCOPE_TEXT[s["scope"]]]
        if s["ignore_case"]:
            parts.append("ignore capitals")
        if s["ignore_punctuation"]:
            parts.append("ignore punctuation")
        if s["allow_irregular"]:
            parts.append("irregular folders renamed")
        if self.kind == "sonarr" and s["hold_tba"]:
            parts.append("hold TBA")
        if s["refresh_first"]:
            parts.append("refresh first")
        if scans_to_limit(s):
            parts.append("scan until the rename limit")
        else:
            parts.append((f"{s['count']} per run" + (" in batches" if s["batch"] else "")) if s["count"] else "whole library")
        if s["max_renames"]:
            parts.append(f"rename limit {s['max_renames']}")
        parts.append(f"cycle tag '{s['tag_name']}'" if s["tag_name"] else "no cycle tag")
        if s["ignore_tag"]:
            parts.append(f"ignore tag '{s['ignore_tag']}'")
        if s["title_filter"]:
            parts.append(f"titles containing '{s['title_filter']}'")
        parts.append("dry run" if dry_run else "apply")
        return ", ".join(parts)

    def _counts_text(self, changed: List[Dict[str, Any]]) -> str:
        """What changed, in numbers: folders renamed, items with file renames and the file total."""
        folders = sum(1 for i in changed if i["folder"])
        with_files = [i for i in changed if i["files"]]
        files = sum(len(i["files"]) for i in with_files)
        lines = []
        if folders:
            lines.append(f"{folders} folder{'s' if folders != 1 else ''} renamed")
        if with_files:
            lines.append(f"{len(with_files)} {self.noun if len(with_files) == 1 else self.nouns} with file renames, {files} file{'s' if files != 1 else ''} total")
        return "\n".join(lines)

    @staticmethod
    def _diff_block(item: Dict[str, Any]) -> str:
        """One item's renames: its name in bold over a diff code block with the old name on a -
        line and the new one on a + line, which Discord shows in red and green, and why under them."""
        title = item["label"] + (f" ({item['note']})" if item["note"] else "")
        lines = []
        if item["folder"]:
            lines += [f"- {_basename(item['folder'][0])}", f"+ {_basename(item['folder'][1])}"] + ([f"  {item['why']}"] if item.get("why") else [])
        for old, new in item["files"]:
            lines += [f"- {_basename(old)}", f"+ {_basename(new)}", f"  {rename_reasons(old, new)}"]
        body = "\n".join(line.replace("`", "'") for line in lines)
        return f"**{title.replace('`', chr(39))}**\n```diff\n{body}\n```"

    @staticmethod
    def _split_block(block: str, limit: int) -> List[str]:
        """An item whose block is longer than a page, cut between its renames (a - line, a + line
        and the reason under them); every piece keeps the item's name and its own fences."""
        title, _, rest = block.partition("\n```diff\n")
        renames: List[List[str]] = []
        for line in rest[: -len("\n```")].split("\n"):
            if line.startswith("- ") or not renames:
                renames.append([line])
            else:
                renames[-1].append(line)
        pieces: List[str] = []
        room = limit - len(title) - len("\n```diff\n\n```")
        current: List[str] = []
        for lines in renames:
            if current and len("\n".join(current + lines)) > room:
                pieces.append(f"{title}\n```diff\n" + "\n".join(current) + "\n```")
                current = []
            current += lines
        if current:
            pieces.append(f"{title}\n```diff\n" + "\n".join(current) + "\n```")
        return pieces

    @classmethod
    def _pages(cls, blocks: List[str], limit: int) -> List[str]:
        """The blocks one under the other, split into pages of at most `limit` characters."""
        pages: List[str] = []
        current = ""
        for block in blocks:
            for piece in ([block] if len(block) <= limit else cls._split_block(block, limit)):
                if current and len(current) + 1 + len(piece) > limit:
                    pages.append(current)
                    current = ""
                current = f"{current}\n{piece}" if current else piece
        if current:
            pages.append(current)
        return pages

    @classmethod
    def _diff_text(cls, head: str, blocks: List[str], limit: Optional[int]) -> str:
        """The head in bold over the item blocks. With a limit, only the items that fit are
        listed and the rest are counted under them."""
        if limit is None:
            return f"**{head}**\n{blocks[0]}"
        shown: List[str] = []
        used = 0
        for block in blocks:
            if shown and used + 1 + len(block) > limit:
                break
            if not shown and len(block) > limit:
                block = cls._split_block(block, limit)[0]
            shown.append(block)
            used += len(block) + 1
        left = len(blocks) - len(shown)
        text = f"**{head}**\n" + "\n".join(shown)
        return text + (f"\n…{left} more item(s) not shown. Send every rename lists them all." if left else "")

    # ── selection: ignore tag, cycle tag, count/batches ────────────────────
    def _select(self, arr: _Arr, name: str, s: Dict[str, Any], dry_run: bool, stats: Dict[str, Any]):
        media = arr.media()
        self.results[name]["taken"] = {_fold(m["path"]) for m in media if m["path"]}  # every item's folder: a rename cannot land on one
        log_info(LogTags.TOOLS, f"{name}: {len(media)} {'movie(s)' if arr.kind == 'radarr' else 'series'} in the library")
        if s["title_filter"]:
            needle = s["title_filter"].lower()
            kept = [m for m in media if needle in m["title"].lower()]
            log_info(LogTags.TOOLS, f"{name}: title filter '{s['title_filter']}' matches {len(kept)} of {len(media)} item(s)")
            if not kept:
                log_warning(LogTags.TOOLS, f"{name}: nothing matches the title filter — nothing to do")
                return [], None
            media = kept
        if s["ignore_tag"]:
            # The tag is created when missing (not on a dry run), so it can be picked in the app's own tag list.
            ignore_id = arr.tag_id(s["ignore_tag"], create=not dry_run)
            if ignore_id is not None:
                kept = [m for m in media if ignore_id not in m["tags"]]
                if len(kept) != len(media):
                    log_info(LogTags.TOOLS, f"{name}: skipping {len(media) - len(kept)} item(s) tagged '{s['ignore_tag']}'")
                media = kept
        pool = len(media)
        pool_ids = {m["id"] for m in media}
        cycle_tag = None
        if s["tag_name"]:
            cycle_tag = arr.tag_id(s["tag_name"], create=not dry_run)
            if cycle_tag is None and not dry_run:
                log_warning(LogTags.TOOLS, f"{name}: could not create the tag '{s['tag_name']}' — items will not be tagged this run")
            untagged = [m for m in media if cycle_tag is None or cycle_tag not in m["tags"]]
            if media and not untagged:
                stats["cycles"] += 1
                log_info(LogTags.TOOLS, f"{name}: every item carries '{s['tag_name']}' — "
                         + ("a real run would clear the tags and start over" if dry_run else "clearing the tags to start a new cycle"))
                if not dry_run and not arr.set_tags([m["id"] for m in media], cycle_tag, add=False):
                    log_warning(LogTags.TOOLS, f"{name}: could not clear the tag '{s['tag_name']}'")
            else:
                if len(untagged) != len(media):
                    log_info(LogTags.TOOLS, f"{name}: {len(untagged)} of {len(media)} item(s) still untagged this cycle")
                media = untagged
        media.sort(key=lambda m: (m["title"].lower(), m["year"], m["id"]))
        count = s["count"]
        if scans_to_limit(s):
            # As many as it takes: every untagged item in turn, a few at a time, until the rename limit stops the run.
            chunks = [media[i:i + _SCAN_CHUNK] for i in range(0, len(media), _SCAN_CHUNK)]
        elif s["batch"]:
            if not count:
                log_warning(LogTags.TOOLS, f"{name}: 0 items per run with batches on is not supported — using batches of {_DEFAULT_CHUNK}. Set 50 to 75.")
            size = count or _DEFAULT_CHUNK
            chunks = [media[i:i + size] for i in range(0, len(media), size)]
        else:
            if not count and len(media) > _DEFAULT_CHUNK:
                log_warning(LogTags.TOOLS, f"{name}: 0 items per run with {len(media)} items — the whole library goes in one pass. For a large library set 50 to 75.")
            chunks = [media[:count] if count else media]
        chunks = [c for c in chunks if c]
        if s["tag_name"]:  # how far through the pass this run gets, for the summary
            self.results[name]["rounds"] = {"pool": pool, "pool_ids": pool_ids, "untagged": len(media), "picked": sum(len(c) for c in chunks), "count": count,
                                            "scan": scans_to_limit(s)}
        if not chunks:
            log_info(LogTags.TOOLS, f"{name}: nothing to check")
        return chunks, cycle_tag


class _InstanceRun:
    """The work on one instance: check each item, hold back what looks wrong, rename the rest
    through the arr and confirm it, tag what was processed, and sum the run up."""

    def __init__(self, owner: ArrRenamer, arr: _Arr, name: str, s: Dict[str, Any], cycle_tag: Optional[int], dry_run: bool,
                 stats: Dict[str, Any], result: Dict[str, Any]) -> None:
        self.owner = owner
        self.arr = arr
        self.name = name
        self.s = s
        self.cycle_tag = cycle_tag
        self.dry_run = dry_run
        self.stats = stats
        self.result = result
        self.kind = arr.kind
        self.folders = s["scope"] in ("folders", "both")
        self.files = s["scope"] in ("files", "both", "tba")
        self.tba_only = s["scope"] == "tba"  # of the files the arr would rename, only a TBA title being filled in
        self.taken = result.pop("taken", set())
        self.roots: Optional[List[str]] = None
        self._siblings: Dict[str, List[str]] = {}  # parent folder → the folders in it, read once
        self._on_disk: Dict[str, Set[str]] = {}  # parent folder → those names, NFC, to look an item's folder up
        self.renamed = 0  # items renamed this run, against the limit
        self._note = ""

    @property
    def full(self) -> bool:
        """Scanning to the rename limit, and the limit is reached: the run checks nothing more."""
        return scans_to_limit(self.s) and self.renamed >= self.s["max_renames"]

    # ── one chunk ──────────────────────────────────────────────────────────
    def process_chunk(self, chunk: List[Dict[str, Any]]) -> None:
        ids = [m["id"] for m in chunk]
        if self.s["refresh_first"] and not self.dry_run:
            log_info(LogTags.TOOLS, f"{self.name}: refreshing metadata of {len(ids)} item(s) before the rename check")
            self.arr.refresh(ids)
            self._wait_idle((self.arr.refresh_name,), "the metadata refresh")
        done: List[int] = []  # processed and not left for later: what gets the cycle tag
        try:
            for item in chunk:
                if self.full:
                    break
                self._process_item(item, done)
        finally:
            if not self.dry_run and self.cycle_tag is not None and done:
                if self.arr.set_tags(done, self.cycle_tag, add=True):
                    self.stats["tagged"] += len(done)
                    self.result["tagged"] += len(done)
                else:
                    log_warning(LogTags.TOOLS, f"{self.name}: could not tag {len(done)} item(s) '{self.s['tag_name']}'")
            elif self.dry_run:
                self.result["tagged"] += len(done)  # what a real run would tag

    def _process_item(self, item: Dict[str, Any], done: List[int]) -> None:
        label = f"{item['title']} ({item['year']})" if item["year"] else item["title"]
        self.owner._tick(f"{self.owner.label} rename: {label}", advance=1)
        prefix = f"{self.name} | {label} |"
        row = {"label": label, "files": [], "folder": None, "why": "", "held": "", "waiting": "", "forced": "", "later": False, "gone": False, "missing": False,
               "failed": "", "note": "", "unsure": 0}
        try:
            plan = self._plan(item)
        except _Stopped:
            if not self.arr.gone(item["id"]):
                raise
            plan = None
        self.stats["checked"] += 1
        self.result["checked"] += 1
        self.result["items"].append(row)
        if plan is None:
            self._mark_gone(row, prefix)
            return
        if plan["missing"]:
            # The arr has files on record in a folder the disk does not have (a share that is not
            # mounted, or a folder renamed by hand). Renaming would only change the arr's path and
            # lose track of the files, so it is left alone.
            row["missing"] = True
            self.result["missing"] += 1
            self.stats["missing"] += 1
            why = f"; the arr has {plan['on_record']} file(s) on record there" if plan["on_record"] else ""
            log_info(LogTags.TOOLS, f"{prefix} not on disk ({item['path'] or 'no folder set'}){why}, skipped")
            done.append(item["id"])  # checked: it comes round again next pass
            return
        if plan["ignored"]:
            for why in plan["ignored"][1]:
                self.result["ignored"][why] += 1
            self.stats["ignored"] += 1
            log_info(LogTags.TOOLS, f"{prefix} folder left alone, only {' and '.join(plan['ignored'][1])} differ: {plan['ignored'][0]}")
        for old, new, why in plan["skipped"]:
            for w in why:
                self.result["ignored_files"][w] += 1
            self.stats["ignored_files"] += 1
            log_info(LogTags.TOOLS, f"{prefix} file left alone, only {' and '.join(why)} differ: {old} → {new}")
        if plan["blocker"]:
            self.result["blocked"] += 1
            log_warning(LogTags.TOOLS, f"{prefix} folder cannot be renamed to '{plan['expected']}': {plan['blocker']}")
        for old, new in plan["blocked_files"]:
            self.result["blocked_files"] += 1
            log_warning(LogTags.TOOLS, f"{prefix} file cannot be renamed: {old} → {new} | a file with that name already exists")
        if plan["held"] or plan["held_files"] or plan["unsure_files"]:
            self.stats["held"] += 1
        for _fid, old, new in plan["held_files"]:
            log_info(LogTags.TOOLS, f"{prefix} waiting for its episode title: {old} → {new}")
        row["unsure"] = len(plan["unsure_files"])
        self.result["unsure"] += len(plan["unsure_files"])
        for old, new, why in plan["unsure_files"]:
            log_warning(LogTags.TOOLS, f"{prefix} file left alone, more than a TBA title may change: {old} → {new} | {why}")
        if plan["held"]:
            row["held"] = plan["held"]
            log_warning(LogTags.TOOLS, f"{prefix} held back — {plan['held']}")
            if plan["folder"]:
                log_info(LogTags.TOOLS, f"{prefix} waiting: folder {_basename(item['path'])} → {plan['expected']}")
            for _fid, old, new in plan["renames"]:
                log_info(LogTags.TOOLS, f"{prefix} waiting: {old} → {new}")
            done.append(item["id"])  # held ones come round again next cycle, like the rest
            return
        waiting = ([f"{len(plan['held_files'])} file(s) whose episode title is still TBA"] if plan["held_files"] else []) \
            + ([f"{len(plan['unsure_files'])} file(s) where more than a TBA title may change"] if plan["unsure_files"] else [])
        if waiting:
            row["waiting"] = "; ".join(waiting)
        if not (plan["folder"] or plan["renames"]):
            if not plan["blocked_files"]:
                log_debug(LogTags.TOOLS, f"{prefix} up to date")
            done.append(item["id"])
            return
        limit = self.s["max_renames"]
        if limit and self.renamed >= limit:
            if not self.result["later"]:
                log_info(LogTags.TOOLS, f"{self.name}: reached the rename limit of {limit} — the rest wait for the next runs")
            row["later"] = True
            self.result["later"] += 1
            self.stats["later"] += 1
            log_info(LogTags.TOOLS, f"{prefix} later")
            return  # not tagged: the next run checks it again
        self.renamed += 1
        if plan["forced"]:
            row["forced"] = plan["forced"]
            log_warning(LogTags.TOOLS, f"{prefix} irregular, renaming it anyway: {plan['forced']}")
        if plan["folder"]:
            row["folder"] = (item["path"], _join(_split(item["path"])[0], plan["expected"]))
            row["why"] = "; ".join(plan["problems"])
            if plan["empty"]:
                row["note"] = "not downloaded yet"
            log_info(LogTags.TOOLS, f"{prefix} folder {'would be' if self.dry_run else 'to be'} renamed"
                     + (" in the arr only, as there is no folder or file yet" if plan["empty"] else "")
                     + f": {_basename(item['path'])} → {plan['expected']}" + (f" | {'; '.join(plan['problems'])}" if plan["problems"] else ""))
        if plan["renames"]:
            log_info(LogTags.TOOLS, f"{prefix} {len(plan['renames'])} file(s) to rename")
        for _fid, old, new in plan["renames"]:  # every rename, old → new and why: what a dry run is for
            row["files"].append((old, new))
            log_info(LogTags.TOOLS, f"{prefix} {old} → {new} | {rename_reasons(old, new)}")
        if self.dry_run:
            if plan["folder"]:  # as the real run would leave the names, so what it says of the later items holds
                self._name_moved(item["path"], row["folder"][1], on_disk=not plan["empty"])
            self._count(plan)
            done.append(item["id"])
            return
        try:
            notes = [row["note"]]
            if plan["folder"]:
                new_path = self._move_folder(item, plan, prefix)
                self._name_moved(item["path"], new_path, on_disk=not plan["empty"])
                row["folder"] = (item["path"], new_path)
                item = {**item, "path": new_path}
                notes.append(self._note)
            if plan["renames"]:
                self._rename_files(item, plan, prefix)
                notes.append(self._note)
            row["note"] = ", ".join(dict.fromkeys(n for n in notes if n))
        except _Stopped as exc:
            if self.arr.gone(item["id"]):
                self._mark_gone(row, prefix)
                return
            row["failed"] = str(exc)
            self.stats["failed"] += 1
            raise _Stopped(f"{label}: {exc}") from None
        self._count(plan)
        done.append(item["id"])

    def _count(self, plan: Dict[str, Any]) -> None:
        self.stats["items"] += bool(plan["renames"])
        self.stats["files"] += len(plan["renames"])
        self.stats["folders"] += bool(plan["folder"])
        self.stats["forced"] += bool(plan["forced"])

    def _mark_gone(self, row: Dict[str, Any], prefix: str) -> None:
        """Deleted from the arr since the run started: nothing to do, and nothing to tag."""
        row.update(gone=True, files=[], folder=None, forced="")
        self.result["deleted"] += 1
        self.stats["deleted"] += 1
        log_warning(LogTags.TOOLS, f"{prefix} deleted from the arr mid-run — skipped")

    # ── the plan for one item ──────────────────────────────────────────────
    def _plan(self, item: Dict[str, Any]) -> Dict[str, Any]:
        s = self.s
        plan: Dict[str, Any] = {"expected": "", "folder": False, "problems": [], "irregular": [], "blocker": "", "ignored": (),
                                "renames": [], "held_files": [], "unsure_files": [], "skipped": [], "blocked_files": [], "held": "", "forced": "",
                                "missing": False, "on_record": 0, "empty": False}
        if not self._present(item["path"]):
            if item["path"]:
                plan["on_record"] = len(self.arr.tracked(item["id"]))
                plan["empty"] = not plan["on_record"]  # not downloaded yet: a rename only changes the name the arr has
            if not plan["empty"]:
                plan["missing"] = True
                return plan
        if self.folders and item["path"]:
            parent, folder = _split(item["path"])
            expected = self.arr.folder_name(item["id"])
            plan["expected"] = expected
            found, irregular = problems(folder, expected, item["ids"], s["ignore_case"], s["ignore_punctuation"])
            plan["problems"], plan["irregular"] = found, irregular
            if found:
                plan["blocker"] = self._blocker(item, parent, folder, expected)
            elif folder != expected:
                plan["ignored"] = (f"{folder} → {expected}", passed_over(folder, expected))
            plan["folder"] = bool(found) and not plan["blocker"]
        if not plan["empty"] and (self.files or plan["folder"]):
            listed = self.arr.rename_list(item["id"])
            if self.tba_only:
                listed = self._tba_fills(item["id"], listed, plan)
            for fid, old, new in listed:
                if (s["ignore_case"] or s["ignore_punctuation"]) and not problems(old, new, None, s["ignore_case"], s["ignore_punctuation"])[0]:
                    plan["skipped"].append((old, new, passed_over(old, new) or ("capitals",)))
                elif s["hold_tba"] and self.kind == "sonarr" and _TBA.search(_basename(new)):
                    plan["held_files"].append((fid, old, new))
                else:
                    plan["renames"].append((fid, old, new))
        if plan["renames"]:
            # The arr cannot rename a file onto a name another file has (a second copy of an episode):
            # that rename is not sent. A name one of these renames frees is not taken.
            taken = set(self.arr.on_disk(item["path"])) - {old for _fid, old, _new in plan["renames"]}
            plan["blocked_files"] = [(old, new) for _fid, old, new in plan["renames"] if new in taken]
            plan["renames"] = [r for r in plan["renames"] if r[2] not in taken]
        if plan["folder"] and plan["irregular"] and s["allow_irregular"]:
            plan["forced"] = "; ".join(plan["irregular"])
        elif plan["folder"] and plan["irregular"]:
            plan["held"] = "irregular folder name, check the item's metadata in the arr: " + "; ".join(plan["irregular"])
        elif plan["held_files"] and not (plan["folder"] or plan["renames"]):
            plan["held"] = f"{len(plan['held_files'])} file(s) whose episode title is still TBA"
        elif plan["unsure_files"] and not (plan["folder"] or plan["renames"]):
            plan["held"] = f"{len(plan['unsure_files'])} file(s) where more than a TBA title may change"
        return plan

    def _tba_fills(self, series_id: int, listed: List[Tuple[int, str, str]], plan: Dict[str, Any]) -> List[Tuple[int, str, str]]:
        """Of the renames the arr lists, the ones that only fill in a TBA title. The unclear ones go
        on the plan to be reported; the rest are not this run's. The episode titles are read only
        when a listed file still says TBA, which most series never do."""
        candidates = [r for r in listed if _TBA.search(_basename(r[1]))]
        if not candidates:
            return []
        titles = self.arr.episode_titles(series_id)
        fills = []
        for fid, old, new in candidates:
            kind, why = tba_fill(old, new, titles.get(fid, []))
            if kind == "fill":
                fills.append((fid, old, new))
            elif kind == "unsure":
                plan["unsure_files"].append((old, new, why))
        return fills

    def _listing(self, parent: str) -> List[str]:
        if parent not in self._siblings:
            self._siblings[parent] = self.arr.folders_in(parent)
        return self._siblings[parent]

    def _present(self, path: str) -> bool:
        """Is the item's folder on disk? Looked up in its parent's listing, read once per parent.
        A folder listed in other capitals is the item's only when the arr sees video files
        through its own path, as on a disk that ignores capitals."""
        if not path:
            return False
        parent, folder = _split(path)
        if parent not in self._on_disk:
            self._on_disk[parent] = {unicodedata.normalize("NFC", n) for n in self._listing(parent)}
        name = unicodedata.normalize("NFC", folder)
        if name in self._on_disk[parent]:
            return True
        return any(_fold(n) == _fold(name) for n in self._on_disk[parent]) and bool(self.arr.on_disk(path))

    def _name_moved(self, old: str, new: str, *, on_disk: bool) -> None:
        """A folder was renamed: its new name is taken and its old one is free again, so a later
        item of this run can be given the name this one gave up. The listings read earlier follow
        when the folder is on disk (an item not downloaded yet has none)."""
        self.taken.discard(_fold(old))  # before the add: a capitals change keeps the one entry
        self.taken.add(_fold(new))
        if not on_disk:
            return
        (was_in, was), (now_in, now) = _split(old), _split(new)
        if was_in in self._siblings:
            self._siblings[was_in] = [n for n in self._siblings[was_in] if n != was]
        if now_in in self._siblings and now not in self._siblings[now_in]:
            self._siblings[now_in].append(now)
        if was_in in self._on_disk:
            self._on_disk[was_in].discard(unicodedata.normalize("NFC", was))
        if now_in in self._on_disk:
            self._on_disk[now_in].add(unicodedata.normalize("NFC", now))

    def _blocker(self, item: Dict[str, Any], parent: str, folder: str, expected: str) -> str:
        """Why the arr could not rename this folder in place."""
        if not expected.strip() or "/" in expected or "\\" in expected:
            return f"the arr gives an unusable folder name ('{expected}')"
        if self.roots is None:
            self.roots = self.arr.root_folders()
        if self.roots and parent not in self.roots:  # the editor only renames by moving to a root folder
            return f"it is inside {parent}, not directly in a root folder"
        if _fold(expected) != _fold(folder) and _fold(_join(parent, expected)) in self.taken:
            return f"another {self.owner.noun} in the arr already uses that folder"
        others = {_fold(n) for n in self._listing(parent) if n != folder}
        if _fold(expected) in others:
            return "a folder with that name already exists"
        return ""

    # ── renaming, confirmed ────────────────────────────────────────────────
    def _move_folder(self, item: Dict[str, Any], plan: Dict[str, Any], prefix: str) -> str:
        """Rename the item's folder through the arr, then check that no file went missing. Returns
        the new path; raises _Stopped on any problem."""
        arr, old = self.arr, item["path"]
        self._note = ""
        # A refresh running during the move (the arr's scheduled one) could save the old path back.
        self._wait_idle((arr.refresh_name,), "the metadata refresh")
        db_before = arr.tracked(item["id"])
        disk_before = arr.on_disk(old)
        if not arr.move_to_root([item["id"]], item["root"] or _split(old)[0]):
            raise _Stopped("the folder rename request was not accepted")
        self._wait_idle(arr.move_names, "the folder move")
        now = arr.media_one(item["id"])
        new = now["path"] if now else ""
        if not new or new == old:
            raise _Stopped("the arr kept the old folder, so the move did not happen (see the arr's logs)")
        disk_after = set(arr.on_disk(new))
        left_behind = set(arr.on_disk(old))
        if _fold(new) == _fold(old) and left_behind == disk_after:
            left_behind = set()  # a capitals change on a disk that ignores capitals: the old name finds the new folder
        missing = set(disk_before) - disk_after - left_behind
        if left_behind or missing:
            for rel in sorted(left_behind):
                log_warning(LogTags.TOOLS, f"{prefix} left behind in {old}: {rel}")
            for rel in sorted(missing):
                log_warning(LogTags.TOOLS, f"{prefix} missing: {rel}")
            raise _Stopped(f"files did not all arrive in {new} ({len(left_behind)} left behind, {len(missing)} missing). "
                           "Not refreshing, as a refresh would drop them from the arr: move them by hand, then refresh the item")
        self._check_records(item["id"], db_before, dict(db_before), disk_after, prefix)
        log_info(LogTags.TOOLS, f"{prefix} folder renamed{' in the arr, made under that name when the first file comes in' if plan['empty'] else ''}: "
                 f"{_basename(old)} → {_basename(new)}")
        return new

    def _rename_files(self, item: Dict[str, Any], plan: Dict[str, Any], prefix: str) -> None:
        """Rename the planned files with the arr's RenameFiles command, then check that every file
        is still there. Raises _Stopped on any problem."""
        arr, path = self.arr, item["path"]
        self._note = ""
        renames = {fid: (old, new) for fid, old, new in plan["renames"]}
        self._wait_idle((arr.refresh_name,), "the metadata refresh")
        db_before = arr.tracked(item["id"])
        disk_before = arr.on_disk(path)
        cid = arr.rename_files(item["id"], list(renames))
        if cid is None:
            raise _Stopped("the rename command was not accepted")
        status = self._wait_command(cid, "the rename")
        if status != "completed":
            raise _Stopped(f"the rename command ended '{status}' (see the arr's logs)")
        disk_after = set(arr.on_disk(path))
        olds = {old for old, _new in renames.values()}
        missing = sorted({old for old, new in renames.values() if old not in disk_after and new not in disk_after} | ((set(disk_before) - olds) - disk_after))
        not_renamed = [old for old, new in renames.values() if old in disk_after and new not in disk_after]
        if missing:
            for rel in missing:
                log_warning(LogTags.TOOLS, f"{prefix} missing: {rel}")
            raise _Stopped(f"{len(missing)} file(s) are gone from {path}. Not refreshing, as a refresh would drop them from the arr")
        if not_renamed:
            for rel in not_renamed:
                log_warning(LogTags.TOOLS, f"{prefix} not renamed: {rel}")
            raise _Stopped(f"{len(not_renamed)} file(s) were not renamed (see the arr's logs)")
        want = {fid: renames[fid][1] if fid in renames else rel for fid, rel in db_before.items()}
        self._check_records(item["id"], db_before, want, disk_after, prefix)
        log_info(LogTags.TOOLS, f"{prefix} {len(renames)} file(s) renamed, all accounted for")

    def _check_records(self, media_id: int, db_before: Dict[int, str], want: Dict[int, str], disk: Any, prefix: str) -> None:
        """The arr's file records should match `want` now; refresh the item when they drifted, and
        stop when the arr lost track of a file that is still on disk."""
        db_after = self.arr.tracked(media_id)
        if db_after != want:
            self._note = "rescan needed"
            self.stats["rescans"] += 1
            log_warning(LogTags.TOOLS, f"{prefix} the arr had {len(db_before)} file(s) on record before and {len(db_after)} that match after — refreshing it")
            self.arr.refresh([media_id])
            self._wait_idle((self.arr.refresh_name,), "the metadata refresh")
            db_after = self.arr.tracked(media_id)
            log_info(LogTags.TOOLS, f"{prefix} refreshed: {len(db_after)} file(s) on record")
        lost = sorted({rel for rel in want.values() if rel in disk} - set(db_after.values()))
        if lost:
            for rel in lost:
                log_warning(LogTags.TOOLS, f"{prefix} the arr lost track of: {rel}")
            raise _Stopped(f"the arr lost track of {len(lost)} file(s)")

    # ── waiting on the arr ─────────────────────────────────────────────────
    def _wait_command(self, command_id: int, what: str) -> str:
        deadline = time.monotonic() + _COMMAND_TIMEOUT
        status = self.arr.command_status(command_id)
        while status in _BUSY and time.monotonic() < deadline:
            self.owner._sleep(_POLL)
            self.owner._tick()
            status = self.arr.command_status(command_id)
        if status in _BUSY:
            raise _Stopped(f"{what} command still was not done after {_COMMAND_TIMEOUT // 60} minutes (see System, Tasks in the arr)")
        return status or "unknown"

    def _wait_idle(self, names: Tuple[str, ...], what: str) -> None:
        """Wait until no command of these names is queued or running."""
        deadline = time.monotonic() + _COMMAND_TIMEOUT
        while self.arr.busy(names):
            if time.monotonic() >= deadline:
                raise _Stopped(f"{what} still was not done after {_COMMAND_TIMEOUT // 60} minutes (see System, Tasks in the arr)")
            self.owner._sleep(_POLL)
            self.owner._tick()

    # ── the instance's summary lines: progress, limit, what was ignored ────
    def close_pass(self) -> None:
        """The run checked the last untagged items of the pass: take the cycle tag off every item
        now rather than at the start of the next run, so items added before then join a new pass
        instead of being all that run checks. Items deleted mid-run no longer need checking."""
        rounds = self.result["rounds"]
        if not rounds or not rounds["picked"] or self.result["tagged"] + self.result["deleted"] < rounds["untagged"]:
            return
        tag = self.s["tag_name"]
        self.stats["cycles"] += 1
        if self.dry_run:
            log_info(LogTags.TOOLS, f"{self.name}: this run checks the last of the pass; a real run would take the tag '{tag}' off every item, so the next run starts a new pass")
            return
        if self.cycle_tag is None:
            return
        try:  # read again: items deleted since the run started cannot go in the request
            ids = [m["id"] for m in self.arr.media() if m["id"] in rounds["pool_ids"] and self.cycle_tag in m["tags"]]
        except _Stopped as exc:
            log_warning(LogTags.TOOLS, f"{self.name}: could not take the tag '{tag}' off ({exc}); the next run clears it before it starts")
            return
        if ids and not self.arr.set_tags(ids, self.cycle_tag, add=False):
            log_warning(LogTags.TOOLS, f"{self.name}: could not take the tag '{tag}' off {len(ids)} item(s); the next run clears it before it starts")
            return
        log_info(LogTags.TOOLS, f"{self.name}: every item has been checked this pass; took the tag '{tag}' off {len(ids)} item(s), so the next run starts a new pass")

    def finish(self) -> None:
        result, s, lines = self.result, self.s, self.result["lines"]
        nouns = self.owner.nouns
        did = "Would check" if self.dry_run else "Checked"
        rounds = result["rounds"]
        if rounds and rounds["picked"]:
            tagged = result["tagged"]  # a dry run: what it would tag
            left = max(rounds["untagged"] - tagged - result["deleted"], 0)  # a deleted item is not left to check
            picked, pool, count = rounds["picked"], rounds["pool"], rounds["count"]
            if rounds["scan"]:
                picked = result["checked"]  # it stops at the rename limit: not every item lined up gets checked
            if left and rounds["scan"]:  # how many more runs depends on how many need renaming
                lines.append(f"{did} {picked:,} {nouns} this run; total {pool - left:,} of {pool:,} {nouns} checked, {left:,} left to check")
            elif left:
                runs = -(-left // (count or left))
                lines.append(f"{did} {picked:,} {nouns} this run; total {pool - left:,} of {pool:,} {nouns} checked, "
                             f"{left:,} left to check, {runs:,} more run{'s' if runs != 1 else ''}" + (f" at {count:,} a run" if count else ""))
            elif picked == pool:
                lines.append(f"{did} all {pool:,} {nouns} this run; next run starts over")
            else:
                lines.append(f"{did} {picked:,} {nouns} this run; total {pool:,} of {pool:,} {nouns} checked, next run starts over")
        if scans_to_limit(s):
            n = self.renamed
            lines.append(f"Rename limit {s['max_renames']:,} - " + ("reached" if self.full else
                                                                     f"not reached, {n:,} {self.owner.noun if n == 1 else nouns} {'would be ' if self.dry_run else ''}renamed"))
        elif s["max_renames"]:
            later = result["later"]
            runs = -(-later // s["max_renames"])
            lines.append(f"Rename limit {s['max_renames']:,} - " + (f"{later:,} more to rename in {runs:,} run{'s' if runs != 1 else ''}" if later else "none left to rename"))
        cannot = [f"{n:,} {what}{'s' if n != 1 else ''}" for n, what in ((result["blocked"], "folder"), (result["blocked_files"], "file")) if n]
        if cannot:
            lines.append(f"Cannot rename: {', '.join(cannot)} (see the log)")
        if result["unsure"]:
            lines.append(f"Left alone: {result['unsure']:,} file{'s' if result['unsure'] != 1 else ''} where more than a TBA title may change (see the log)")
        if result["missing"]:
            lines.append(f"Not on disk: {result['missing']:,} {self.owner.noun if result['missing'] == 1 else nouns} skipped")
        if result["deleted"]:
            lines.append(f"Deleted mid-run: {result['deleted']:,}")
        ignored = {why: n for why, n in result["ignored"].items() if n}
        if ignored:
            lines.append("Ignored folders: " + ", ".join(f"{n} {why}" for why, n in ignored.items()))
        ignored_files = {why: n for why, n in result["ignored_files"].items() if n}
        if ignored_files:
            lines.append("Ignored files: " + ", ".join(f"{n} {why}" for why, n in ignored_files.items()))


# ── the renamer's tags, set by hand from the Tools page ───────────────────
def _instance_arr(db: Session, kind: str, instance: str) -> _Arr:
    found = next((i for i in load_arr_instances(db, kind) if (i["name"] or i["url"]) == instance), None)
    if found is None:
        raise LookupError(f"No {kind.capitalize()} instance named '{instance}'.")
    try:
        client = _connect(found, kind)
    except Exception as exc:
        log_debug(LogTags.TOOLS, f"{instance}: connection failed: {exc}")
        client = None
    if client is None:
        raise ConnectionError(f"Cannot connect to {kind.capitalize()} '{instance}'.")
    return _Arr(client, kind)


def tag_list(db: Session, kind: str, instance: str, tag: str) -> Dict[str, Any]:
    """Every movie or series of one instance, in title order, each marked when it carries `tag`."""
    arr = _instance_arr(db, kind, instance)
    try:
        tag_id = arr.tag_id(tag, create=False)
        media = arr.media()
    except _Stopped as exc:
        raise ConnectionError(f"{instance}: {exc}.") from None
    items = [{"id": m["id"], "title": m["title"], "year": m["year"] or None, "tagged": tag_id is not None and tag_id in m["tags"]} for m in media]
    items.sort(key=lambda i: (i["title"].lower(), i["year"] or 0, i["id"]))
    return {"instance": instance, "tag": tag, "items": items}


def title_list(db: Session, kind: str, instance: str) -> Dict[str, Any]:
    """Every movie or series of one instance, in title order, to try a title filter against."""
    arr = _instance_arr(db, kind, instance)
    try:
        media = arr.media()
    except _Stopped as exc:
        raise ConnectionError(f"{instance}: {exc}.") from None
    items = [{"id": m["id"], "title": m["title"], "year": m["year"] or None} for m in media]
    items.sort(key=lambda i: (i["title"].lower(), i["year"] or 0, i["id"]))
    return {"instance": instance, "items": items}


def set_tagged(db: Session, kind: str, instance: str, tag: str, add: List[int], remove: List[int]) -> Dict[str, int]:
    """Put `tag` on the items in `add` and take it off the ones in `remove`. The arr gets the tag
    when the first item needs it."""
    arr = _instance_arr(db, kind, instance)
    try:
        tag_id = arr.tag_id(tag, create=bool(add))
    except _Stopped as exc:
        raise ConnectionError(f"{instance}: {exc}.") from None
    if tag_id is None:
        if add:
            raise ConnectionError(f"{instance}: could not create the tag '{tag}'.")
        return {"added": 0, "removed": 0}  # no item can carry a tag the arr does not have
    if add and not arr.set_tags(add, tag_id, add=True):
        raise ConnectionError(f"{instance}: could not put the tag '{tag}' on.")
    if remove and not arr.set_tags(remove, tag_id, add=False):
        raise ConnectionError(f"{instance}: could not take the tag '{tag}' off.")
    return {"added": len(add), "removed": len(remove)}
