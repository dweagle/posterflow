"""Content-rating badge: the age rating as a badge (G, PG-13, R, TV-14, TV-MA …).

The rating comes from what the media server holds (Plex's contentRating, Jellyfin's
OfficialRating). Two tables map whatever the servers call it onto the US movie and TV
buckets; each bucket draws a PNG badge. Two sets ship under
backend/assets/overlays/content_rating/: color/<key>.png (the rating's colored box with
white text, the user's Kometa set) and white/<key>.png (the same badge with the fill
dropped: a white line and white text). The backdrop is drawn at render time. A file with
the same path under config/overlays/content_rating replaces the bundled one.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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

IMAGES = ImageLibrary("content_rating", frame=(119, 60), sets=(("color", "Color boxes"), ("white", "Transparent")), noun="badge")
_SET_KEYS = [s for s, _l in IMAGES.sets]

# key (= badge file name), label, values the servers may hold (first row listing a value wins)
MOVIE_DEFS = [
    ("g", "G", "1, 01, 2, 02, 3, 03, 4, 04, 5, 05, 6, 06, G, G - All Ages, U, gb/U, gb/0+, E, gb/E, A, no/A, TV-Y, TV-G"),
    ("pg", "PG", "PG, PG - Children, gb/PG, gb/9+, TV-PG, TV-Y7, TV-Y7-FV, 7, 8, 9, 07, 08, 09, 10, 11, no/5, no/05, no/6, no/06, no/7, no/07"),
    ("pg-13", "PG-13", "PG-13, gb/12A, gb/12, 12+, TV-13, gb/14+, gb/15, TV-14, 12, 13, 14, 15, 16, PG-13 - Teens 13 or older, no/9, no/09, no/10, no/11, no/12"),
    ("r", "R", "R, 17, 18, gb/18, MA-17, TVMA, TV-MA, R - 17+ (violence & profanity), R+ - Mild Nudity, no/15, no/16, no/18"),
    ("nc-17", "NC-17", "NC-17, gb/R18, gb/X, R18, X, Rx - Hentai"),
    ("nr", "NR", "None, NR, Not Rated, Unrated"),
]
TV_DEFS = [
    ("tv-y", "TV-Y", "TV-Y, TV-Y7, TV-Y7-FV, 7, 07, 8, 08, 9, 09, no/5, no/05, no/6, no/06, no/7, no/07"),
    ("tv-g", "TV-G", "TV-G, 1, 01, 2, 02, 3, 03, 4, 04, 5, 05, 6, 06, U, G, gb/U, gb/0+, G - All Ages, A, no/A"),
    ("tv-pg", "TV-PG", "gb/PG, gb/9+, 10, 11, 12, 13, TV-PG, PG - Children, no/9, no/09, no/10, no/11, no/12"),
    ("tv-14", "TV-14", "gb/12A, 12+, PG-13, TV-13, TV-14, 12, PG-13 - Teens 13 or older, gb/14+, gb/15, 14, 15, 16, 17, no/15, no/16"),
    ("tv-ma", "TV-MA", "18, gb/18, MA-17, NC-17, R, TV-MA, TVMA, R - 17+ (violence & profanity), R+ - Mild Nudity, Rx - Hentai, no/18"),
    ("tv-nr", "NR", "None, NR, Not Rated, Unrated"),
]
TABLES = {"movie": MOVIE_DEFS, "tv": TV_DEFS}
_TABLE_LABELS = {"movie": "movies", "tv": "TV"}
_UNRATED_KEYS = {"movie": "nr", "tv": "tv-nr"}  # each table's NR is lettered like its neighbours
# One badge file per rating.
RATING_KEYS = [key for defs in TABLES.values() for key, _l, _v in defs]
_LABELS = {key: label for defs in TABLES.values() for key, label, _v in defs}


def _values(raw: Any) -> List[str]:
    return [v.strip().lower() for v in str(raw or "").split(",") if v.strip()]


class ContentRatingOverlay(OverlayType):
    key = "content_rating"
    label = "Content Rating"
    description = "The age rating (G, PG-13, R, TV-14, TV-MA …) as a badge, from the rating your media server holds."
    order = 30
    targets = (TARGET_MOVIE, TARGET_SHOW, TARGET_SEASON)
    needs = ()
    hidden_placement = ("text",)  # the badge is an image; the backdrop is drawn behind it like text
    images = IMAGES

    def image_items(self) -> List[tuple]:
        return [(key, _LABELS[key]) for key in RATING_KEYS]

    def default_placement(self) -> Dict[str, Any]:
        return {
            **DEFAULT_PLACEMENT,
            "horizontal_align": "left",
            "horizontal_offset": 35,
            "vertical_align": "bottom",
            "vertical_offset": 35,
            "back_enabled": False,  # nothing behind the badge until asked for
            "back_style": "band",   # switching the backdrop on gives the edge band first
            # A box backdrop, when picked: solid black, one fixed 130 px width for every rating.
            "back_color": "#000000FF",
            "back_padding": 6,
            "back_radius": 10,
            "back_min_width": 130,
            "back_max_width": 130,
        }

    def default_options(self) -> Dict[str, Any]:
        return {
            "badge_set": "color",
            "badge_width": 130,
            "badge_unrated": True,
            "apply_to_seasons": False,
            **{
                table: {key: {"enabled": True, "matches": values} for key, _label, values in defs}
                for table, defs in TABLES.items()
            },
        }

    def schema(self) -> List[Dict[str, Any]]:
        row_fields = [
            {"kind": "text", "key": "matches", "label": "Values", "help": "Ratings your servers may hold that land here, comma-separated. Not case-sensitive."},
        ]
        return [
            {"kind": "select", "key": "badge_set", "label": "Badge", "section": "Badge", "options": [{"value": v, "label": l} for v, l in IMAGES.sets],
             "help": "A box in the rating's color with white text, or the same badge with no fill: a white line and white text."},
            {"kind": "number", "key": "badge_width", "label": "Badge width", "section": "Badge", "min": 20, "max": 1000,
             "help": f"Width in px of a 1000x1500 poster; the height follows. {IMAGES.frame[0]} is the badge's own size."},
            {"kind": "toggle", "key": "badge_unrated", "label": "Badge unrated items", "section": "Badge", "help": "An item with no rating gets the NR badge."},
            {"kind": "images", "key": "badges", "label": "Custom badges", "section": "Custom badges", "set_option": "badge_set", "noun": "badge",
             "help": f"Replace a bundled badge of the chosen set with your own image. It is fitted onto the {IMAGES.frame[0]} x {IMAGES.frame[1]} frame the bundled ones use and kept in config/overlays/content_rating."},
            {"kind": "toggle", "key": "apply_to_seasons", "label": "Season posters too", "section": "Seasons", "help": "Also badge each season poster with the show's rating."},
            {
                "kind": "states",
                "key": "movie",
                "label": "Movie ratings",
                "item_label": "Rating",
                "help": "Movies use this table. The first row whose Values list the server's rating wins.",
                "items": [{"key": key, "label": label, "fields": row_fields} for key, label, _v in MOVIE_DEFS],
            },
            {
                "kind": "states",
                "key": "tv",
                "label": "TV ratings",
                "item_label": "Rating",
                "help": "Shows and their seasons use this table.",
                "items": [{"key": key, "label": label, "fields": row_fields} for key, label, _v in TV_DEFS],
            },
        ]

    def normalize_options(self, raw: Any) -> Dict[str, Any]:
        defaults = self.default_options()
        if not isinstance(raw, dict):
            return defaults
        out = dict(defaults)
        badge_set = str(raw.get("badge_set") or defaults["badge_set"])
        out["badge_set"] = badge_set if badge_set in _SET_KEYS else defaults["badge_set"]
        try:
            out["badge_width"] = max(20, min(1000, int(raw.get("badge_width", defaults["badge_width"]))))
        except (TypeError, ValueError):
            out["badge_width"] = defaults["badge_width"]
        for key in ("badge_unrated", "apply_to_seasons"):
            val = raw.get(key, defaults[key])
            out[key] = val if isinstance(val, bool) else str(val).lower() == "true"
        for table in TABLES:
            rows: Dict[str, Any] = {}
            raw_rows = raw.get(table) if isinstance(raw.get(table), dict) else {}
            for key, default_row in defaults[table].items():
                rr = raw_rows.get(key) if isinstance(raw_rows.get(key), dict) else {}
                enabled = rr.get("enabled", default_row["enabled"])
                rows[key] = {
                    "enabled": enabled if isinstance(enabled, bool) else str(enabled).lower() == "true",
                    "matches": str(rr.get("matches", default_row["matches"]) or "").strip()[:1000],
                }
            out[table] = rows
        return out

    def variants(self, options: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {"key": f"{table}:{key}", "label": f"{label} ({_TABLE_LABELS[table]})", "group": table, "item": key}
            for table, defs in TABLES.items()
            for key, label, _v in defs
        ]

    def resolve_variant(self, variant: str, options: Dict[str, Any]) -> Optional[OverlayBadge]:
        table, _, key = (variant or "").partition(":")
        if table not in TABLES:
            table, key = "movie", "pg-13"
        return self._badge(table, key, options)

    def resolve(self, ctx: ItemContext, options: Dict[str, Any], meta: Dict[str, Any]) -> Optional[OverlayBadge]:
        if ctx.is_season and not options.get("apply_to_seasons"):
            return None
        table = "movie" if ctx.media_type == MEDIA_MOVIE else "tv"
        value = str(ctx.content_rating or "").strip().lower()
        if not value:
            return self._badge(table, _UNRATED_KEYS[table], options) if options.get("badge_unrated") else None
        key = self.match(table, value, options)
        return self._badge(table, key, options) if key else None

    @staticmethod
    def match(table: str, value: str, options: Dict[str, Any]) -> Optional[str]:
        """The row (in table order) whose Values list the server's rating; None when nothing does."""
        rows = options.get(table) or {}
        for key, _label, _v in TABLES[table]:
            row = rows.get(key) or {}
            if row.get("enabled", True) and value in _values(row.get("matches")):
                return key
        return None

    @staticmethod
    def _badge(table: str, key: str, options: Dict[str, Any]) -> Optional[OverlayBadge]:
        row = (options.get(table) or {}).get(key) or {}
        if not row.get("enabled", True) or key not in _LABELS:
            return None
        path = IMAGES.path(key, str(options.get("badge_set") or "color"))
        if path is None:
            return None
        return OverlayBadge(key=f"{table}:{key}", text=_LABELS[key], image=str(path), image_width=int(options.get("badge_width") or 0),
                            image_stamp=IMAGES.stamp(path))
