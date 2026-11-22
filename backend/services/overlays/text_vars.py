"""Variables in a custom badge's wording, filled in per poster: `<<runtime>>`,
`<<audience_rating%>>`, `<<originally_available[%b %d]>>`, `<<season_numberW>>` ...

The names and the endings that format them follow Kometa's special text, as far as the facts
are at hand here:

- words (title, show_title, content_rating, edition): as they are, or U upper case, L lower
  case, P title case;
- scores (audience_rating, critic_rating, user_rating, tmdb_rating; 0 to 10): one decimal, or
  % as a percentage, # with a trailing .0 dropped, / out of five;
- runtime (a movie's, or a show's or season's average episode) and total_runtime (a show's or
  season's episodes together): minutes, or H the hours and M the minutes left over;
- counts (season_number, episode_count, versions, year): the number, or 0 and 00 padded to two
  and three digits, or W / WU / WL in words;
- bitrate (kbps): the first file's, or H the highest, L the lowest;
- originally_available: the release date, `[...]` giving its strftime format.

A poster that has no value for a variable gets no badge from that wording.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from .base import ItemContext, MEDIA_MOVIE

_VAR = re.compile(r"<<([a-z_]+)(WU|WL|00|[%#/HMULPW0])?(?:\[([^\]]*)\])?>>")
_WORDS = ("title", "show_title", "content_rating", "edition")
_SCORES = ("audience_rating", "critic_rating", "user_rating", "tmdb_rating")
_COUNTS = ("season_number", "episode_count", "versions", "year")
_RUNTIMES = ("runtime", "total_runtime")
NAMES = (*_WORDS, *_SCORES, *_RUNTIMES, *_COUNTS, "bitrate", "originally_available")
_MEDIA = ("runtime", "total_runtime", "episode_count", "versions", "bitrate")  # read from the item's files

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")

# What the page's preview fills in.
SAMPLE: Dict[str, Any] = {
    "title": "Movie Title", "show_title": "Show Title", "content_rating": "PG-13", "edition": "Director's Cut", "audience_rating": 8.4, "critic_rating": 7.9,
    "user_rating": 9.0, "tmdb_rating": 7.6, "runtime": 127.0, "total_runtime": 610.0, "season_number": 2, "episode_count": 10, "versions": 2, "year": 2024,
    "bitrate": [12.7, 40.2], "originally_available": date(2024, 7, 19),
}


def variables(text: str) -> List[str]:
    """The variables a wording names (known ones only)."""
    return [name for name, _mod, _fmt in _VAR.findall(str(text or "")) if name in NAMES]


def needs(text: str) -> Tuple[str, ...]:
    """What the engine must fetch to fill a wording in (see OverlayType.needs)."""
    named = variables(text)
    return (*(("media",) if any(name in _MEDIA for name in named) else ()), *(("tmdb",) if "tmdb_rating" in named else ()))


def _words(number: int) -> str:
    """A whole number below a thousand in English words."""
    if number < 0 or number > 999:
        return str(number)
    if number < 20:
        return _ONES[number]
    if number < 100:
        return _TENS[number // 10] + (f"-{_ONES[number % 10]}" if number % 10 else "")
    rest = number % 100
    return f"{_ONES[number // 100]} hundred" + (f" {_words(rest)}" if rest else "")


def values(ctx: ItemContext, meta: Dict[str, Any]) -> Dict[str, Any]:
    """What each variable holds for an item; a name is left out when there is nothing to say."""
    tmdb = (meta or {}).get("tmdb") or {}
    files = [f for f in (ctx.media or []) if isinstance(f, dict)]
    minutes = [float(f.get("minutes") or 0) for f in files if float(f.get("minutes") or 0) > 0]
    movie = ctx.media_type == MEDIA_MOVIE
    try:
        released = date.fromisoformat(str(ctx.release_date or "")[:10])
    except ValueError:
        released = None
    season = None if not ctx.is_season else "Specials" if ctx.season_number == 0 else f"Season {ctx.season_number}"
    found: Dict[str, Any] = {
        "title": season or ctx.title,
        "show_title": ctx.title if not movie else None,
        "content_rating": ctx.content_rating,
        "edition": ctx.edition if movie else None,
        "audience_rating": ctx.ratings.get("audience"),
        "critic_rating": ctx.ratings.get("critic"),
        "user_rating": ctx.ratings.get("user"),
        "tmdb_rating": tmdb.get("vote_average") if tmdb.get("vote_count") else None,
        "runtime": (max(minutes) if movie else sum(minutes) / len(minutes)) if minutes else None,
        "total_runtime": sum(minutes) if minutes and not movie else None,
        "season_number": ctx.season_number,
        "episode_count": len(files) if files and not movie else None,
        "versions": len(files) if files and movie else None,
        "year": ctx.year,
        "bitrate": [float(f.get("bitrate") or 0) for f in files if float(f.get("bitrate") or 0) > 0] or None,
        "originally_available": released,
    }
    return {name: value for name, value in found.items() if value not in (None, "")}


def _format(name: str, mod: str, fmt: Optional[str], value: Any) -> str:
    if name == "originally_available":
        return value.strftime(fmt or "%Y-%m-%d")
    if name in _RUNTIMES:
        whole = int(value)
        return str(whole // 60 if mod == "H" else whole % 60 if mod == "M" else whole)
    if name == "bitrate":
        return str(int((max(value) if mod == "H" else min(value) if mod == "L" else value[0]) * 1000))
    if name in _SCORES:
        score = float(value)
        if mod == "%":
            return str(int(score * 10))
        if mod == "/":
            return f"{score / 2:.1f}"
        text = f"{score:.1f}"
        return text[:-2] if mod == "#" and text.endswith(".0") else text
    if name in _COUNTS:
        number = int(value)
        if mod in ("W", "WU", "WL"):
            said = _words(number)
            return said.upper() if mod == "WU" else said
        return f"{number:02}" if mod == "0" else f"{number:03}" if mod == "00" else str(number)
    text = str(value)
    return text.upper() if mod == "U" else text.lower() if mod == "L" else text.title() if mod == "P" else text


def fill(text: str, found: Dict[str, Any]) -> Optional[str]:
    """The wording with its variables filled in from `found` (see values); None when one of
    them has no value. A name that is no variable stays as it was typed."""
    missing = False

    def swap(match: "re.Match[str]") -> str:
        nonlocal missing
        name, mod, fmt = match.group(1), match.group(2) or "", match.group(3)
        if name not in NAMES:
            return match.group(0)
        if name not in found:
            missing = True
            return ""
        try:
            return _format(name, mod, fmt, found[name])
        except (TypeError, ValueError):
            missing = True
            return ""

    out = _VAR.sub(swap, str(text or ""))
    return None if missing else out


def render(text: str, ctx: ItemContext, meta: Dict[str, Any]) -> Optional[str]:
    """A wording for one poster; None when the poster has no value for one of its variables."""
    return fill(text, values(ctx, meta)) if "<<" in str(text or "") else str(text or "")


def sample(text: str) -> str:
    """A wording as the preview shows it."""
    return fill(text, SAMPLE) or ""
