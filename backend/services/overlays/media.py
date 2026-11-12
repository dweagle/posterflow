"""What the media behind a poster looks like: its resolution, HDR flavor and audio codec,
decided the way Kometa's default `resolution` and `audio_codec` overlays decide it.

A *file* is one playable file as the media server reports it:

    {"resolution": "4k" | "1080" | "720" | "576" | "480" | "sd" | "",  # Plex's videoResolution
     "hdr": bool,             # the server calls the video HDR
     "dovi": bool,            # a video stream carries Dolby Vision
     "path": str,             # the file's path
     "audio_titles": [str]}   # the audio tracks' display titles

A movie's files are its versions; a show's and a season's files are their episodes'.

Kometa's rules, kept as they are:
- The resolution and plain HDR come from the server. For a show or season they count when
  ANY episode has them (a Plex search on the episodes).
- Dolby Vision is a stream flag; HDR10+, HLG and the DV+HDR mixes are read from the file
  name. The audio codec is read from an audio track's title or from the file name. For a
  show or season these count when at least `percent` % of the episodes match (Kometa's
  default_percent, 60).
- Every candidate has a weight and the heaviest one that matches is the badge.

A `source` says where the facts come from: "server" (only what the media server reports:
the resolution, HDR, Dolby Vision, the audio track titles), "filename" (only the file name,
which under TRaSH naming spells all of them) or "both" (either counts: Kometa's rules above,
plus the resolution, HDR and Dolby Vision when only the file name spells them).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

File = Dict[str, Any]

DEFAULT_PERCENT = 60
# Where a badge's facts come from: value, label.
SOURCES = [("both", "Server and file name"), ("server", "Media server only"), ("filename", "File name only")]
SOURCE_KEYS = [s[0] for s in SOURCES]

# key, label, the server's resolution names it covers
RESOLUTIONS: List[Tuple[str, str, "re.Pattern[str]"]] = [
    ("4k", "4K", re.compile(r"(?i)2160|4k")),
    ("1080p", "1080p", re.compile(r"(?i)1080|2k")),
    ("720p", "720p", re.compile(r"(?i)720|hd")),
    ("576p", "576p", re.compile(r"(?i)576")),
    ("480p", "480p", re.compile(r"(?i)480|sd")),
]
RESOLUTION_KEYS = [r[0] for r in RESOLUTIONS]
_RES_LABELS = {r[0]: r[1] for r in RESOLUTIONS}
_RES_PATTERNS = {r[0]: r[2] for r in RESOLUTIONS}

# key, label, file-name pattern (None = decided by the server: the HDR flag, the Dolby Vision stream flag)
FLAVORS: List[Tuple[str, str, Optional["re.Pattern[str]"]]] = [
    ("dvhdrplus", "DV HDR10+", re.compile(r"(?i)\bdv.HDR10(\+|P(lus)?\b)")),
    ("dvhdr", "DV HDR", re.compile(r"(?i)\bdv(.hdr10?\b)")),
    ("plus", "HDR10+", re.compile(r"(?i)\bhdr10(\+|p(lus)?\b)")),
    ("dv", "DV", None),
    ("hlg", "HLG", re.compile(r"(?i)\bhlg\b")),
    ("hdr", "HDR", None),
]
# The resolution and the HDR formats as a file name spells them (TRaSH naming).
_RES_NAME_PATTERNS: Dict[str, "re.Pattern[str]"] = {
    "4k": re.compile(r"(?i)\b(2160p|4k|uhd)\b"),
    "1080p": re.compile(r"(?i)\b(1080[pi]|1440p|2k)\b"),
    "720p": re.compile(r"(?i)\b720p\b"),
    "576p": re.compile(r"(?i)\b576[pi]\b"),
    "480p": re.compile(r"(?i)\b480[pi]\b"),
}
_DV_NAME = re.compile(r"(?i)\b(dv|dovi|dolby.?vision)(?=[^a-z0-9]|$)")
_HDR_NAME = re.compile(r"(?i)\b(hdr\d*(\+|p(lus)?)?|hlg|pq|dv|dovi|dolby.?vision)(?=[^a-z0-9]|$)")  # any HDR format named: what the server's HDR flag covers
_FLAVOR_LABELS = {f[0]: f[1] for f in FLAVORS}
_FLAVOR_PATTERNS = {f[0]: f[2] for f in FLAVORS}

# (resolution key or "", flavor key or "") -> Kometa's weight. 576p and 480p have no HLG badge.
RESOLUTION_WEIGHTS: Dict[Tuple[str, str], int] = {}
for _res, _weights in (
    ("4k", {"dvhdrplus": 159, "dvhdr": 158, "plus": 155, "dv": 150, "hlg": 141, "hdr": 140, "": 130}),
    ("1080p", {"dvhdrplus": 129, "dvhdr": 128, "plus": 125, "dv": 120, "hlg": 111, "hdr": 110, "": 100}),
    ("720p", {"dvhdrplus": 99, "dvhdr": 98, "plus": 95, "dv": 90, "hlg": 81, "hdr": 80, "": 70}),
    ("576p", {"dvhdrplus": 69, "dvhdr": 68, "plus": 65, "dv": 60, "hdr": 50, "": 40}),
    ("480p", {"dvhdrplus": 39, "dvhdr": 38, "plus": 35, "dv": 30, "hdr": 20, "": 10}),
    ("", {"dvhdrplus": 9, "dvhdr": 8, "plus": 7, "dv": 5, "hlg": 2, "hdr": 1}),
):
    for _alt, _weight in _weights.items():
        RESOLUTION_WEIGHTS[(_res, _alt)] = _weight
# Heaviest first: the order they are tried in, and the order the page lists them.
RESOLUTION_ORDER: List[Tuple[str, str]] = sorted(RESOLUTION_WEIGHTS, key=lambda k: -RESOLUTION_WEIGHTS[k])


def resolution_image(res: str, flavor: str) -> str:
    """The badge's image key: Kometa's file name, resolution then flavor ('4kdvhdrplus', '1080p', 'hdr')."""
    return f"{res}{flavor}"


def resolution_label(res: str, flavor: str) -> str:
    return " ".join(part for part in (_RES_LABELS.get(res, ""), _FLAVOR_LABELS.get(flavor, "")) if part)


# key, label, Kometa's weight, pattern (matched against an audio track's title or the file name)
AUDIO_CODECS: List[Tuple[str, str, int, "re.Pattern[str]"]] = [
    ("truehd_atmos", "Dolby TrueHD Atmos", 160, re.compile(r"(?i)^(?=.*\btrue[ ._-]?hd(\b|\d))(?=.*\batmos(\b|\d))")),
    ("dtsx", "DTS:X", 150, re.compile(r"(?i)\b(dts[-_. ]?x7?)\b(?![-_. ]?(26[456]))")),
    ("plus_atmos", "Dolby Digital+ Atmos", 140, re.compile(r"(?i)^(?=.*\b((dd[p+])|(dolby[ ._-]digital[ ._-]plus)|(e[ ._-]?ac3)\b))(?=.*\batmos(\b|\d))")),
    ("dolby_atmos", "Dolby Atmos", 130, re.compile(r"(?i)\batmos(\b|\d)")),
    ("truehd", "Dolby TrueHD", 120, re.compile(r"(?i)\btrue[ ._-]?hd(\b|\d)")),
    ("ma", "DTS-HD MA", 110, re.compile(r"(?i)\bdts[ ._-]?(hd[ ._-])?(ma|xll|hd)(\b|\d)(?![ ._-]hra)")),
    ("flac", "FLAC", 100, re.compile(r"(?i)\bflac(\b|\d)")),
    ("pcm", "PCM", 90, re.compile(r"(?i)\bl?pcm(\b|\d)")),
    ("hra", "DTS-HD HRA", 80, re.compile(r"(?i)\bdts[ ._-]?(hd[ ._-])?(hr|hra|hi|ra)(\b|\d)")),
    ("plus", "Dolby Digital+", 70, re.compile(r"(?i)\b(dd[p+])|(dolby[ ._-]digital[ ._-]plus)|(e[ ._-]?ac3)\b")),
    ("dtses", "DTS-ES", 60, re.compile(r"(?i)\bdts[ ._-]?es(\b|\d)")),
    ("dts", "DTS", 50, re.compile(r"(?i)\bdts(\b|\d)")),
    ("digital", "Dolby Digital", 40, re.compile(r"(?i)\b(dd)|(ac3)|(dolby)(\b|\d)")),
    ("aac", "AAC", 30, re.compile(r"(?i)\b(aac|stereo|2\.0)\b")),
    ("mp3", "MP3", 20, re.compile(r"(?i)\bmp3(\b|\d)")),
    ("opus", "Opus", 10, re.compile(r"(?i)\b(?<!-)OPUS(\b|\d)")),
]
AUDIO_KEYS = [c[0] for c in AUDIO_CODECS]
AUDIO_LABELS = {c[0]: c[1] for c in AUDIO_CODECS}
AUDIO_WEIGHTS = {c[0]: c[2] for c in AUDIO_CODECS}
_AUDIO_PATTERNS = {c[0]: c[3] for c in AUDIO_CODECS}


def frame_resolution(width: Any, height: Any) -> str:
    """The resolution name Plex would give a frame size (servers that report only the size)."""
    try:
        w, h = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        return ""
    if w <= 0 and h <= 0:
        return ""
    if w >= 3800 or h >= 2000:
        return "4k"
    if w >= 2500 or h >= 1400:
        return "2k"
    if w >= 1900 or h >= 1000:
        return "1080"
    if w >= 1260 or h >= 700:
        return "720"
    if h >= 560:
        return "576"
    if h >= 400:
        return "480"
    return "sd"


def _percent(value: Any) -> int:
    try:
        return max(1, min(100, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_PERCENT


def _enough(files: List[File], test: Callable[[File], bool], episodes: bool, percent: int) -> bool:
    """A movie matches when any of its files does; a show or season when enough of its episodes do."""
    if not files:
        return False
    hits = sum(1 for f in files if test(f))
    return hits * 100 >= percent * len(files) if episodes else hits > 0


def _path(f: File) -> str:
    return str(f.get("path") or "")


def _source(value: Any) -> str:
    return value if value in SOURCE_KEYS else "both"


def resolution_badge(files: Optional[Iterable[File]], *, episodes: bool = False, percent: Any = DEFAULT_PERCENT,
                     allowed: Optional[Callable[[str, str], bool]] = None, source: Any = "both") -> Optional[Tuple[str, str]]:
    """The (resolution key, flavor key) of the heaviest resolution badge the media earns, or
    None. `episodes` = the files are a show's or a season's episodes. `allowed(res, flavor)`
    leaves switched-off badges out, so the next heaviest match is taken instead. `source`
    (see SOURCES) says whether the server's facts, the file name's, or both are read."""
    files = [f for f in (files or []) if isinstance(f, dict)]
    if not files:
        return None
    pct = _percent(percent)
    src = _source(source)
    server, name = src != "filename", src != "server"
    cache: Dict[str, bool] = {}

    def has_res(res: str) -> bool:
        if res not in cache:  # any file (any episode) with it counts
            cache[res] = (server and any(_RES_PATTERNS[res].search(str(f.get("resolution") or "")) for f in files)) \
                or (name and any(_RES_NAME_PATTERNS[res].search(_path(f)) for f in files))
        return cache[res]

    def has_flavor(flavor: str) -> bool:
        key = "~" + flavor
        if key not in cache:
            pattern = _FLAVOR_PATTERNS[flavor]
            if flavor == "hdr":  # any file (any episode) counts, like the resolution
                cache[key] = (server and any(bool(f.get("hdr")) for f in files)) or (name and any(_HDR_NAME.search(_path(f)) for f in files))
            elif flavor == "dv":
                cache[key] = _enough(files, lambda f: (server and bool(f.get("dovi"))) or (name and bool(_DV_NAME.search(_path(f)))), episodes, pct)
            else:  # only a file name tells these apart
                cache[key] = name and _enough(files, lambda f: bool(pattern and pattern.search(_path(f))), episodes, pct)
        return cache[key]

    for res, flavor in RESOLUTION_ORDER:
        if allowed is not None and not allowed(res, flavor):
            continue
        if res and not has_res(res):
            continue
        if flavor and not has_flavor(flavor):
            continue
        return res, flavor
    return None


def audio_badge(files: Optional[Iterable[File]], *, episodes: bool = False, percent: Any = DEFAULT_PERCENT,
                priorities: Optional[Dict[str, int]] = None, source: Any = "both") -> Optional[str]:
    """The key of the audio codec badge the media earns, or None: the highest priority among
    the codecs whose pattern is found in an audio track's title or in the file name (`source`,
    see SOURCES, keeps one of the two). `priorities` = {codec key: priority} of the codecs
    switched on (Kometa's weights when None)."""
    files = [f for f in (files or []) if isinstance(f, dict)]
    if not files:
        return None
    pct = _percent(percent)
    src = _source(source)
    server, name = src != "filename", src != "server"
    ranked = sorted((priorities if priorities is not None else AUDIO_WEIGHTS).items(), key=lambda kv: (-int(kv[1] or 0), AUDIO_KEYS.index(kv[0]) if kv[0] in AUDIO_KEYS else 99))
    for key, _priority in ranked:
        pattern = _AUDIO_PATTERNS.get(key)
        if pattern is None:
            continue
        hit = lambda f: (server and any(pattern.search(str(t)) for t in (f.get("audio_titles") or []))) or (name and bool(pattern.search(_path(f))))
        if _enough(files, hit, episodes, pct):
            return key
    return None
