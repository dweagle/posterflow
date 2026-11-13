"""Resolution badge: the resolution and HDR format of the media (4K, 1080p, Dolby Vision,
HDR10+ …), the way Kometa's default resolution overlay decides it.

The media server says what resolution a file is and whether it is HDR or Dolby Vision; the
finer HDR formats (HDR10+, HLG, DV with an HDR fallback) are read from the file name. Every
combination has Kometa's weight and the heaviest one that matches is the badge; a show or
a season is judged by its episodes (see media.py). Each badge can be switched off in the
Badges table: an item that would get it then gets the next one that fits.

Three styles, bundled under backend/assets/overlays/resolution/<set>/, every file cropped to
its art. Color (Kometa's images, the format logos in color) and white (the same images with
every pixel made white) are sized in percent of their own size, so an offset reaches the art
and the backdrop box hugs it (26 px of padding and a 305 px minimum width give Kometa's box
back). Lettering is white lettering with no box, sized by height.
Its set ships parts (_res-<resolution>, _fmt-<format>, _dot) and a badge is joined at draw
time, resolution · format on one line, the dots and the air around them being settings (DV HDR
and DV HDR10+ are two marks with a dot); every mark is 80 px tall, like the Audio lettering,
so a badge is one height. A file
with the same path under config/overlays/resolution replaces the bundled one; in the
lettering set a file named by a badge's key (4kdv.png) is drawn whole instead of joined.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..base import (
    DEFAULT_PLACEMENT,
    ItemContext,
    MEDIA_MOVIE,
    OverlayBadge,
    OverlayType,
    TARGET_MOVIE,
    TARGET_SEASON,
    TARGET_SHOW,
)
from ..images import ImageLibrary
from ..render import image_line
from ..media import (
    DEFAULT_PERCENT,
    SOURCE_KEYS,
    SOURCES,
    RESOLUTION_KEYS,
    RESOLUTION_ORDER,
    resolution_badge,
    resolution_image,
    resolution_label,
)

IMAGES = ImageLibrary("resolution", sets=(("color", "Color"), ("white", "White"), ("lettering", "Lettering")), noun="badge", cropped=True, height=80)
_SET_KEYS = [s for s, _l in IMAGES.sets]
_LETTERING = ("lettering",)  # sized by height in px; the other sets in percent of their own size
_SCALED = [s for s in _SET_KEYS if s not in _LETTERING]

# image key → label, heaviest first: "4kdvhdrplus" → "4K DV HDR10+"
BADGES = [(resolution_image(res, flavor), resolution_label(res, flavor)) for res, flavor in RESOLUTION_ORDER]
_LABELS = dict(BADGES)
_PARTS = {resolution_image(res, flavor): (res, flavor) for res, flavor in RESOLUTION_ORDER}
_FORMAT_MARKS = {"dvhdr": ("dv", "hdr"), "dvhdrplus": ("dv", "plus")}  # formats written as two marks with a dot
DOT_GAP = 60  # default air on each side of the dot, in percent of the badge height
# The Badges table as three tables side by side, each ending where a resolution's badges end:
# 4K and 1080p, 720p and 576p, 480p and the formats on their own.
_PER_RES = [sum(1 for res, _f in RESOLUTION_ORDER if res == key) for key in (*RESOLUTION_KEYS, "")]
_COLUMNS = [_PER_RES[0] + _PER_RES[1], _PER_RES[2] + _PER_RES[3], _PER_RES[4] + _PER_RES[5]]


class ResolutionOverlay(OverlayType):
    key = "resolution"
    label = "Resolution"
    description = "The resolution and HDR format of the media (4K, 1080p, Dolby Vision, HDR10+ …), from your media server and the file names."
    order = 50
    targets = (TARGET_MOVIE, TARGET_SHOW, TARGET_SEASON)
    needs = ("media",)
    hidden_placement = ("text",)  # the badge is an image; the backdrop is its box
    images = IMAGES

    def image_items(self) -> List[tuple]:
        return list(BADGES)

    def default_placement(self) -> Dict[str, Any]:
        return {
            **DEFAULT_PLACEMENT,
            "horizontal_align": "left",
            "horizontal_offset": 35,
            "vertical_align": "top",
            "vertical_offset": 35,
            # Kometa's box: translucent black, rounded, 26 px around the art and at least 305 wide
            # (its 305 x 105 frame; the files are cropped to the art).
            "back_enabled": True,
            "back_style": "solid",
            "back_color": "#00000099",
            "back_padding": 26,
            "back_radius": 30,
            "back_min_width": 305,
            "back_max_width": 0,
        }

    def default_options(self) -> Dict[str, Any]:
        return {
            "badge_style": IMAGES.default_set,
            "badge_scale": 100,
            "badge_height": 40,  # the Audio lettering's, so the two line up
            "dot_gap": DOT_GAP,
            "show_dot": True,
            "episode_percent": DEFAULT_PERCENT,
            "source": "both",
            "apply_to_seasons": False,
            "badges": {key: {"enabled": True} for key, _label in BADGES},
        }

    def schema(self) -> List[Dict[str, Any]]:
        return [
            {"kind": "select", "key": "badge_style", "label": "Style", "section": "Badge", "options": [{"value": v, "label": l} for v, l in IMAGES.sets],
             "help": "Color keeps the HDR format logos in color. White draws the same badges all in white. Lettering is plain white lettering in one line, with no box. Turn the Backdrop off or use an edge band with it."},
            {"kind": "number", "key": "badge_scale", "label": "Badge size (%)", "section": "Badge", "min": 10, "max": 400, "show_when": {"badge_style": _SCALED},
             "help": "100 is the badges' own size. The badges keep their sizes relative to each other."},
            {"kind": "number", "key": "badge_height", "label": "Badge height", "section": "Badge", "min": 20, "max": 300, "show_when": {"badge_style": list(_LETTERING)},
             "help": "Height of the lettering in px of a 1000x1500 poster; the width follows. The resolution and the format are the same height."},
            {"kind": "number", "key": "dot_gap", "label": "Dot spacing (%)", "section": "Badge", "min": 0, "max": 200, "show_when": {"badge_style": list(_LETTERING)},
             "help": f"Space on each side of the dot between the resolution and the format, as a share of the badge height. The default is {DOT_GAP}."},
            {"kind": "toggle", "key": "show_dot", "label": "Dot between them", "section": "Badge", "show_when": {"badge_style": list(_LETTERING)},
             "help": "Draw the dot between the resolution and the format. Off leaves just the space."},
            {"kind": "number", "key": "episode_percent", "label": "Episode share (%)", "section": "Badge", "min": 1, "max": 100,
             "help": "A show or season gets a Dolby Vision or file-name format when at least this share of its episodes has it. Its resolution and plain HDR count when any episode has them."},
            {"kind": "select", "key": "source", "label": "Read from", "section": "Badge", "options": [{"value": v, "label": l} for v, l in SOURCES],
             "help": "The server knows the resolution, HDR and Dolby Vision. The file name can also name HDR10+, HLG and the DV mixes, when it follows TRaSH naming. Server only never shows those four."},
            {"kind": "images", "key": "custom_badges", "label": "Custom badges", "section": "Custom badges", "set_option": "badge_style", "noun": "badge",
             "help": "Replace a bundled badge of the chosen style with your own image. It is cropped to its artwork, drawn at the size of the badge it replaces and kept in config/overlays/resolution."},
            {"kind": "toggle", "key": "apply_to_seasons", "label": "Season posters too", "section": "Seasons",
             "help": "Also badge each season poster, judged by that season's episodes."},
            {
                "kind": "states",
                "key": "badges",
                "label": "Badges",
                "item_label": "Badge",
                "split": _COLUMNS,
                "help": "The resolution, Dolby Vision and HDR come from your media server. HDR10+, HLG and the DV HDR mixes are read from the file name. "
                        "Switch a badge off to leave it out. An item that would get it then gets the next badge that fits.",
                "items": [{"key": key, "label": label, "fields": []} for key, label in BADGES],
            },
        ]

    def normalize_options(self, raw: Any) -> Dict[str, Any]:
        defaults = self.default_options()
        if not isinstance(raw, dict):
            return defaults
        out = dict(defaults)
        style = str(raw.get("badge_style") or defaults["badge_style"])
        out["badge_style"] = style if style in _SET_KEYS else defaults["badge_style"]
        for key, lo, hi in (("badge_scale", 10, 400), ("badge_height", 20, 300), ("dot_gap", 0, 200), ("episode_percent", 1, 100)):
            try:
                out[key] = max(lo, min(hi, int(raw.get(key, defaults[key]))))
            except (TypeError, ValueError):
                out[key] = defaults[key]
        source = str(raw.get("source") or defaults["source"])
        out["source"] = source if source in SOURCE_KEYS else defaults["source"]
        for key in ("apply_to_seasons", "show_dot"):
            val = raw.get(key, defaults[key])
            out[key] = val if isinstance(val, bool) else str(val).lower() == "true"
        rows: Dict[str, Any] = {}
        raw_rows = raw.get("badges") if isinstance(raw.get("badges"), dict) else {}
        for key, default_row in defaults["badges"].items():
            rr = raw_rows.get(key) if isinstance(raw_rows.get(key), dict) else {}
            enabled = rr.get("enabled", default_row["enabled"])
            rows[key] = {"enabled": enabled if isinstance(enabled, bool) else str(enabled).lower() == "true"}
        out["badges"] = rows
        return out

    @staticmethod
    def _on(options: Dict[str, Any], key: str) -> bool:
        return bool(((options.get("badges") or {}).get(key) or {}).get("enabled", True))

    def variants(self, options: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Every badge, heaviest first; the page keeps the ones switched on in the options it edits.
        return [{"key": key, "label": label, "group": "badges", "item": key} for key, label in BADGES]

    def resolve_variant(self, variant: str, options: Dict[str, Any]) -> Optional[OverlayBadge]:
        if variant not in _LABELS:
            variant = next((key for key, _l in BADGES if self._on(options, key)), BADGES[0][0])
        return self._badge(variant, options)

    def resolve(self, ctx: ItemContext, options: Dict[str, Any], meta: Dict[str, Any]) -> Optional[OverlayBadge]:
        if ctx.is_season and not options.get("apply_to_seasons"):
            return None
        found = resolution_badge(
            ctx.media, episodes=ctx.media_type != MEDIA_MOVIE, percent=options.get("episode_percent"), source=options.get("source"),
            allowed=lambda res, flavor: self._on(options, resolution_image(res, flavor)),
        )
        return self._badge(resolution_image(*found), options) if found else None

    def image_source(self, key: str, image_set: str) -> str:
        if image_set not in _LETTERING:
            return IMAGES.source(key, image_set)
        if IMAGES.file(key, image_set) is not None:
            return "custom"  # a whole badge of the user's own (the set ships no whole files)
        return "bundled" if _parts(key, image_set, IMAGES.height, True) else "none"

    def image_png(self, key: str, image_set: str) -> Optional[bytes]:
        """A lettering badge joined from its parts at the art's own size, with the default spacing."""
        if image_set not in _LETTERING or IMAGES.file(key, image_set) is not None:
            return None
        parts = _parts(key, image_set, IMAGES.height, True)
        if not parts:
            return None
        buf = io.BytesIO()
        image_line(parts, IMAGES.height * DOT_GAP // 100).save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _badge(key: str, options: Dict[str, Any]) -> Optional[OverlayBadge]:
        style = str(options.get("badge_style") or "")
        text = _LABELS.get(key, key)
        if style not in _LETTERING:  # Kometa's art, in percent of its own size
            path = IMAGES.path(key, style)
            if path is None:
                return None
            return OverlayBadge(key=key, text=text, image=str(path), image_stamp=IMAGES.stamp(path),
                                image_height=IMAGES.drawn_height(key, style, int(options.get("badge_scale") or 0), reference=100))
        setting = int(options.get("badge_height") or 0)
        own = IMAGES.file(key, style)
        if own is not None:  # a whole badge of the user's own: as tall as the joined one would be
            return OverlayBadge(key=key, text=text, image=str(own), image_height=IMAGES.drawn_height(_lead(key), style, setting), image_stamp=IMAGES.stamp(own))
        parts = _parts(key, style, setting, bool(options.get("show_dot", True)))
        if not parts:
            return None
        return OverlayBadge(
            key=key, text=text, image=parts[0][0], image_height=parts[0][1], image_tail=tuple(parts[1:]),
            image_gap=round(setting * int(options.get("dot_gap") or 0) / 100) if len(parts) > 1 else 0,
            image_stamp="+".join(IMAGES.stamp(Path(path)) for path, _h in parts),
        )


def _lead(key: str) -> str:
    """The part whose height a badge takes: its resolution lettering, else its format lettering."""
    res, flavor = _PARTS.get(key, ("", ""))
    return f"_res-{res}" if res else f"_fmt-{flavor}"


def _parts(key: str, image_set: str, setting: int, dot: bool) -> List[Tuple[str, int]]:
    """The images a lettering badge is joined from, (path, drawn height): the resolution and the
    format's mark or marks, a dot between every two when it is wanted."""
    res, flavor = _PARTS.get(key, ("", ""))
    names = ([f"_res-{res}"] if res else []) + [f"_fmt-{mark}" for mark in _FORMAT_MARKS.get(flavor, (flavor,) if flavor else ())]
    if dot:
        names = [name for i, name in enumerate(names) for name in ((name,) if i == 0 else ("_dot", name))]
    out: List[Tuple[str, int]] = []
    for name in names:
        path = IMAGES.file(name, image_set)
        if path is None:
            return []
        out.append((str(path), IMAGES.drawn_height(name, image_set, setting)))
    return out
