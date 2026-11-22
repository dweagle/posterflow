"""Custom overlay: your own text or images on the posters that match your conditions.

One custom overlay is a spot on the poster (position, text style, backdrop) holding a list of
badges. Each badge has its own wording or image, its own conditions (see rules.py), a size
of its own if it wants one (an image's width, a wording's text size) and a weight: of the badges that match a poster, the heaviest one is drawn there. So one overlay
covers a whole family of badges (a ribbon per award, a logo per list) the way a template
does, and several custom overlays put several badges on one poster.

An uploaded image is kept as config/overlays/custom/<id>.png, cropped to its artwork, and
the badge's `image` holds the id. Each upload gets a new id, so discarding unsaved edits
still finds the file the saved settings name.
"""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .. import images, text_vars
from ..base import DEFAULT_PLACEMENT, ItemContext, MEDIA_MOVIE, OverlayBadge, OverlayType, TARGET_MOVIE, TARGET_SEASON, TARGET_SHOW
from ..rules import MODE_KEYS, MODES, describe_fields, matches, needs, normalize_rules

KEY = "custom"
TEXT, IMAGE = "text", "image"
KINDS = [(TEXT, "Text"), (IMAGE, "Image")]
MAX_BADGES = 300
TEXT_LIMIT = 120  # room for a few variables
_IMAGE_ID = re.compile(r"[0-9a-f]{12}")
_ROW_ID = re.compile(r"[0-9a-f]{8}")
_FIRST_ROW = "00000001"  # the one badge a new overlay starts with
_UNSAVED_GRACE_SECONDS = 3600  # an upload not saved yet is kept this long


def badge_dir() -> Path:
    return images.config_overlays_dir() / KEY


def badge_file(image_id: Any) -> Optional[Path]:
    """The uploaded image an id names, or None."""
    image_id = str(image_id or "")
    if not _IMAGE_ID.fullmatch(image_id):
        return None
    path = badge_dir() / f"{image_id}.png"
    return path if path.is_file() else None


def store_badge(img: Any) -> Tuple[str, int, int]:
    """Keep an uploaded image, cropped to its artwork: (its id, width, height)."""
    img = img.convert("RGBA")
    img = img.crop(img.getchannel("A").getbbox() or (0, 0, img.width, img.height))
    image_id = uuid.uuid4().hex[:12]
    folder = badge_dir()
    folder.mkdir(parents=True, exist_ok=True)
    img.save(folder / f"{image_id}.png", format="PNG")
    return image_id, img.width, img.height


def prune_badges(overlays: Iterable[Dict[str, Any]]) -> int:
    """Delete the uploaded images no saved badge names any more."""
    keep = {str(row.get("image") or "") for o in overlays for row in (o.get("options") or {}).get("badges") or []}
    removed = 0
    try:
        files = list(badge_dir().glob("*.png"))
    except OSError:
        return 0
    for path in files:
        try:
            if path.stem not in keep and time.time() - path.stat().st_mtime > _UNSAVED_GRACE_SECONDS:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _row(n: int = 1, row_id: str = _FIRST_ROW) -> Dict[str, Any]:
    return {"id": row_id, "name": f"Badge {n}", "enabled": True, "text": "CUSTOM", "image": "", "size": 0, "weight": 0, "match": MODE_KEYS[0], "rules": []}


def _whole(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def normalize_rows(raw: Any) -> List[Dict[str, Any]]:
    """An overlay's badges, each with an id of its own. There is always at least one."""
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or len(rows) >= MAX_BADGES:
            continue
        base = _row(len(rows) + 1)
        row_id = str(entry.get("id") or "")
        if not _ROW_ID.fullmatch(row_id) or row_id in seen:
            row_id = uuid.uuid4().hex[:8]
        seen.add(row_id)
        enabled = entry.get("enabled", True)
        image = str(entry.get("image") or "")
        rows.append({
            "id": row_id,
            "name": str(entry.get("name") or "").strip()[:40] or base["name"],
            "enabled": enabled if isinstance(enabled, bool) else str(enabled).lower() != "false",
            "text": str(entry.get("text", base["text"]) or "").strip()[:TEXT_LIMIT],
            "image": image if _IMAGE_ID.fullmatch(image) else "",
            "size": _whole(entry.get("size"), 0, 0, 1000),  # its own image width or text size; 0 = the overlay's
            "weight": _whole(entry.get("weight"), 0, 0, 9999),
            "match": str(entry.get("match")) if entry.get("match") in MODE_KEYS else base["match"],
            "rules": normalize_rules(entry.get("rules")),
        })
    return rows or [_row()]


class CustomOverlay(OverlayType):
    key = KEY
    label = "Custom"
    description = "Your own text or images on the posters that match your conditions."
    order = 90
    targets = (TARGET_MOVIE, TARGET_SHOW, TARGET_SEASON)
    multi = True
    group_options = {"group": "group", "weight": "weight"}
    hidden_placement = ("stroke_width", "stroke_color")  # no letter outline on a custom badge
    hidden_placement_when = {"text": {"badge": IMAGE}}  # an image badge has no text to style

    def default_placement(self) -> Dict[str, Any]:
        # No box behind the badge to start with: a new overlay is a picture, which brings its own look.
        return {**DEFAULT_PLACEMENT, "horizontal_align": "center", "horizontal_offset": 0, "vertical_align": "bottom", "vertical_offset": 35, "back_enabled": False}

    def default_options(self) -> Dict[str, Any]:
        return {
            "badge": IMAGE,
            "image_width": 200,
            "movies": True,
            "shows": True,
            "apply_to_seasons": False,
            "group": "",
            "weight": 0,
            "badges": [_row()],
        }

    def schema(self) -> List[Dict[str, Any]]:
        return [
            {"kind": "select", "key": "badge", "label": "Badge", "section": "Badge", "in_text_card": True,
             "options": [{"value": v, "label": l} for v, l in KINDS],
             "help": "Text draws each badge's wording, styled in the Text card. Image draws the picture you upload for each badge."},
            {"kind": "number", "key": "image_width", "label": "Image width", "section": "Badge", "min": 10, "max": 1000, "show_when": {"badge": IMAGE},
             "help": "Width in px of a 1000x1500 poster. The height follows."},
            {"kind": "toggles", "key": "posters", "label": "Posters", "section": "Applies to",
             "help": "Which posters can get a badge of this overlay.",
             "toggles": [{"key": "movies", "label": "Movies"}, {"key": "shows", "label": "Shows"}]},
            {"kind": "pair", "key": "group_weight", "section": "Applies to", "label": "Group and weight", "pair": [
                {"kind": "text", "key": "group", "label": "Group",
                 "help": "Custom overlays with the same group name share one spot. Of the overlays that match a poster, only the one with the highest overlay weight is drawn. "
                         "Leave it blank to always draw this overlay."},
                {"kind": "number", "key": "weight", "label": "Weight", "min": 0, "max": 9999,
                 "disabled_when": {"group": ""}, "disabled_help": "Set a group first",
                 "help": "The weight of this whole overlay among the overlays that share its group. The overlay with the highest weight is the one drawn. "
                         "With equal weights the first tab wins. This is not a badge's weight: each badge in the table below has its own."},
            ]},
            {"kind": "toggle", "key": "apply_to_seasons", "label": "Season posters too", "section": "Seasons",
             "help": "Also badge season posters. A season is judged by its show, and by its own episodes for file, video and audio conditions."},
            {"kind": "badges", "key": "badges", "label": "Badges", "kind_option": "badge", "max": MAX_BADGES,
             "modes": [{"value": v, "label": l} for v, l in MODES], "fields": describe_fields(), "text_limit": TEXT_LIMIT,
             "text_help": "What the badge says. It can name values that are filled in for each poster: <<title>>, <<year>>, <<runtime>> (or <<runtimeH>> and "
                          "<<runtimeM>> for hours and minutes), <<audience_rating>>, <<critic_rating>>, <<user_rating>>, <<tmdb_rating>> (add % for a percentage, "
                          "# to drop a trailing .0), <<content_rating>>, <<edition>>, <<originally_available[%b %d, %Y]>>, <<season_number>>, <<episode_count>>, "
                          "<<versions>>, <<total_runtime>>, <<bitrate>>. A poster with no value for one of them gets no badge from this row.",
             "help": "Each badge has its own wording or image, its own weight and its own conditions. Click a badge to see and change its conditions below. "
                     "A poster gets the badge whose conditions it matches. When it matches several of this overlay's badges, the badge with the highest weight is drawn. "
                     "A badge with no condition matches every poster."},
        ]

    @staticmethod
    def _drawable(options: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The badges that are on and have something to draw, heaviest first (the list's order on a tie)."""
        image = options.get("badge") == IMAGE
        rows = [row for row in options.get("badges") or []
                if row.get("enabled", True) and (badge_file(row.get("image")) is not None if image else str(row.get("text") or "").strip())]
        return sorted(rows, key=lambda row: -int(row.get("weight") or 0))

    def needs_for(self, options: Dict[str, Any]) -> tuple:
        rows = [row for row in options.get("badges") or [] if row.get("enabled", True)]
        found = list(needs(rule for row in rows for rule in row.get("rules") or []))
        if options.get("badge") != IMAGE:  # what the wordings' variables read
            found += [need for row in rows for need in text_vars.needs(str(row.get("text") or "")) if need not in found]
        return tuple(dict.fromkeys(found))

    def normalize_options(self, raw: Any) -> Dict[str, Any]:
        defaults = self.default_options()
        if not isinstance(raw, dict):
            return defaults
        out = dict(defaults)
        out["badge"] = raw.get("badge") if raw.get("badge") in (TEXT, IMAGE) else defaults["badge"]
        out["image_width"] = _whole(raw.get("image_width", defaults["image_width"]), defaults["image_width"], 10, 1000)
        for key in ("movies", "shows", "apply_to_seasons"):
            value = raw.get(key, defaults[key])
            out[key] = value if isinstance(value, bool) else str(value).lower() == "true"
        out["group"] = str(raw.get("group") or "").strip()[:40]
        out["weight"] = _whole(raw.get("weight", 0), 0, 0, 9999)
        out["badges"] = normalize_rows(raw.get("badges"))
        return out

    def variants(self, options: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The overlay's badges, the one a preview draws when none is named first: the heaviest
        with something to draw. (The page lists the badges of the overlay being edited itself.)"""
        drawable = self._drawable(options)
        rest = [row for row in options.get("badges") or [] if row not in drawable]
        return [{"key": row["id"], "label": row["name"]} for row in [*drawable, *rest]]

    def resolve_variant(self, variant: str, options: Dict[str, Any]) -> Optional[OverlayBadge]:
        rows = options.get("badges") or []
        row = next((r for r in rows if r.get("id") == variant), None) or next(iter(self._drawable(options)), None)
        return self._badge(row, options, text_vars.sample(str(row.get("text") or ""))) if row else None

    def resolve(self, ctx: ItemContext, options: Dict[str, Any], meta: Dict[str, Any]) -> Optional[OverlayBadge]:
        wanted = "apply_to_seasons" if ctx.is_season else "movies" if ctx.media_type == MEDIA_MOVIE else "shows"
        if not options.get(wanted):
            return None
        for row in self._drawable(options):  # heaviest first: the first match that has something to say is the badge
            if matches(row.get("rules") or [], str(row.get("match") or ""), ctx, meta):
                badge = self._badge(row, options, text_vars.render(str(row.get("text") or ""), ctx, meta))
                if badge is not None:
                    return badge
        return None

    @staticmethod
    def _badge(row: Dict[str, Any], options: Dict[str, Any], wording: Optional[str]) -> Optional[OverlayBadge]:
        """`wording` is the row's text with its variables filled in (None = the poster has no value
        for one of them). `key` carries the badge's name when the overlay holds several, for the logs."""
        shared = {"key": str(row.get("name") or "") if len(options.get("badges") or []) > 1 else "",
                  "group": str(options.get("group") or ""), "weight": int(options.get("weight") or 0)}
        size = int(row.get("size") or 0)  # this badge's own size; 0 = the overlay's
        if options.get("badge") == IMAGE:
            path = badge_file(row.get("image"))
            if path is None:
                return None
            return OverlayBadge(text="image", image=str(path), image_width=size or int(options.get("image_width") or 0),
                                image_stamp=images.ImageLibrary.stamp(path), **shared)
        text = str(wording or "").strip()
        # An outline saved before the setting went away is not drawn either. A size of its own is the text's size.
        own = {"stroke_width": 0, **({"font_size": max(8, min(400, size))} if size else {})}
        return OverlayBadge(text=text, placement_overrides=own, **shared) if text else None
