"""Overlay building blocks shared by every overlay type.

An overlay type is a small module under services/overlays/types/ that decides WHAT to draw
for an item (a badge) — the engine decides WHEN (pipeline placement, tracking) and the
renderer decides HOW (fonts, backdrop, position). Placement follows Kometa's overlay
vocabulary (horizontal/vertical align + offset, back_*, font_*) in 1000x1500 poster space.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from services.text_logo import _resolve_font


MEDIA_MOVIE = "movie"
MEDIA_SHOW = "show"
MEDIA_COLLECTION = "collection"  # never badged: no overlay type targets a collection

# Where a badge can apply. "season" = a show's season poster (its own slot).
TARGET_MOVIE = "movie"
TARGET_SHOW = "show"
TARGET_COLLECTION = "collection"  # a collection poster; nothing targets it
TARGET_SEASON = "season"

H_ALIGNS = ("left", "center", "right")
V_ALIGNS = ("top", "center", "bottom")

# Fonts that no longer ship: a placement naming one takes the bundled font beside it, unless
# the user's own copy is in config/artwork/fonts.
RETIRED_FONTS = {"AvenirNextLTPro-Bold": "AlbertSans-Black"}

# Kometa-style placement defaults, in 1000x1500 poster space.
DEFAULT_PLACEMENT: Dict[str, Any] = {
    "horizontal_align": "center",
    "horizontal_offset": 0,
    "vertical_align": "top",
    "vertical_offset": 35,
    "font": "RobotoCondensed-Variable",
    "font_weight": "SemiBold",
    "font_size": 48,
    "font_color": "#FFFFFF",
    "letter_spacing": 6,
    "stroke_width": 0,
    "stroke_color": "#000000",
    "back_enabled": True,     # False = text only (nothing behind the text)
    "back_style": "solid",    # solid = flat box; gradient = the box takes a gradient; band = full-width fade from the top of the poster, no box
    "back_color": "#000000B3",
    "back_padding": 15,
    "back_min_width": 0,      # box at least this wide (reference px) so short and long texts match; 0 = fit the text
    "back_max_width": 0,      # box at most this wide: text that would overflow is shrunk for that badge only; 0 = no limit
    "back_radius": 15,
    "back_line_color": "#FFFFFFFF",
    "back_line_width": 0,
    "gradient_start": "#0000008C",  # gradient/band styles fade this (black, 55%) to transparent; "" = the badge's backdrop color
    "gradient_mode": "center",      # gradient box: linear (along gradient_angle) or center (solid box glowing out past its edges)
    "gradient_angle": 90,           # linear: CSS-style degrees, 0 = to top, 90 = to right, 180 = to bottom
    "gradient_spread": 20,          # center: how far the glow reaches past the box, reference px
    # band: solid side `gradient_band_offset` px down from the top poster edge, fading to clear
    # over `gradient_band_height` px toward the middle; side insets narrow it (reference px)
    "gradient_band_offset": 0,
    "gradient_band_height": 150,
    "gradient_band_left": 0,
    "gradient_band_right": 0,
    "gradient_band_curve": "linear",  # how the band fades out: linear, or soft (eases out toward its end)
}

BACK_STYLES = ("solid", "gradient", "band")
GRADIENT_MODES = ("linear", "center")
BAND_CURVES = ("linear", "soft")



@dataclass
class ItemContext:
    """What the engine knows about one media-server item before asking TMDB."""

    media_type: str                 # movie | show | collection
    title: str
    year: Optional[int]
    tmdb_id: Optional[int]
    tvdb_id: Optional[int]
    imdb_id: Optional[str]
    season_number: Optional[int]    # set for season posters (0 = Specials)
    rel_path: str                   # tracking key: "<instance>|<server item id>"
    ratings: Dict[str, float] = field(default_factory=dict)  # the server's audience / critic / user ratings, 0-10
    content_rating: Optional[str] = None  # the age rating as the server shows it (R, TV-14, gb/15); seasons take the show's
    library_id: str = ""  # "<instance>:<library key>" the item was listed in; picks a per-library overlay configuration
    # The media files behind the item (see media.py): a movie's versions, a show's or a season's
    # episodes. None = not looked up (no active overlay needs it).
    media: Optional[List[Dict[str, Any]]] = None

    @property
    def is_season(self) -> bool:
        return self.season_number is not None

    @property
    def target(self) -> str:
        if self.media_type == MEDIA_MOVIE:
            return TARGET_MOVIE
        if self.media_type == MEDIA_COLLECTION:
            return TARGET_COLLECTION
        return TARGET_SEASON if self.is_season else TARGET_SHOW

    @classmethod
    def from_server_item(cls, item: Any, instance_name: str, parent: Any = None) -> "ItemContext":
        """Context for a media-server item (MediaServerItem). Seasons take their ids from the
        parent show. The kind comes from the server, so it is never guessed."""
        ids_source = parent if (parent is not None and item.item_type == "season") else item
        provider = {str(k).lower(): str(v) for k, v in (ids_source.provider_ids or {}).items() if v}

        def as_int(value: Optional[str]) -> Optional[int]:
            try:
                return int(str(value).strip()) if value not in (None, "") else None
            except ValueError:
                return None

        media_type = MEDIA_MOVIE if item.item_type == "movie" else MEDIA_COLLECTION if item.item_type == "collection" else MEDIA_SHOW
        title = ids_source.title if item.item_type == "season" else item.title
        # Items carry "<server>:/library/sections/N" (Plex) or "<server>:<guid>" (Jellyfin); the
        # config keeps "<instance>:N" / "<instance>:<guid>", so only the last segment is kept.
        lib_raw = str(getattr(item, "library_key", "") or "") or (str(getattr(parent, "library_key", "") or "") if parent is not None else "")
        lib_key = lib_raw.split(":", 1)[-1].rsplit("/", 1)[-1] if lib_raw else ""
        year = ids_source.year if item.item_type == "season" else item.year
        return cls(
            media_type=media_type,
            title=str(title or ""),
            year=int(year) if isinstance(year, int) or (isinstance(year, str) and year.isdigit()) else None,
            tmdb_id=as_int(provider.get("tmdb")),
            tvdb_id=as_int(provider.get("tvdb")),
            imdb_id=provider.get("imdb") or None,
            season_number=int(item.index) if item.item_type == "season" and item.index is not None else None,
            rel_path=f"{instance_name}|{item.item_id}",
            ratings=dict(getattr(ids_source, "ratings", None) or {}),
            content_rating=str(getattr(ids_source, "content_rating", None) or "").strip() or None,
            library_id=f"{instance_name}:{lib_key}" if lib_key else "",
        )


@dataclass
class OverlayBadge:
    """One resolved overlay: the text to draw plus optional per-badge color overrides.
    `key` names the state (e.g. "airing") and `signature` must change whenever the
    rendered output would — it decides when a badge is re-composed and re-uploaded."""

    key: str
    text: str
    back_color: Optional[str] = None
    font_color: Optional[str] = None
    placement_overrides: Dict[str, Any] = field(default_factory=dict)
    image: Optional[str] = None  # a PNG drawn instead of text (absolute path); `text` then only labels it in logs
    image_width: int = 0         # width the image is scaled to, reference px; 0 = its own size
    image_height: int = 0        # height it is scaled to instead (the width follows): lettering of one height, any width
    # More PNGs drawn to the right of `image` on the same line, as (path, height) pairs: each
    # at its own height, centered on the line, `image_gap` reference px apart.
    image_tail: tuple = ()
    image_gap: int = 0
    # Sharing a line (see render.join_lines): `line` on a badge lets another type's badge be
    # drawn at its end, {height, gap, dot: (path, height) or None}; `joins` names the type whose
    # line this badge joins when that type draws one on the same poster.
    line: Optional[Dict[str, Any]] = None
    joins: str = ""
    fit: bool = False  # scaled down, for this poster only, when wider than the room its offset leaves
    # Outline drawn around the image's shape before it is placed: {width (image px), color
    # '#RRGGBB', opacity 0-100, soft (edge blur, image px)}; None = the image as it is.
    image_effect: Optional[Dict[str, Any]] = None
    image_stamp: str = ""  # identifies the image's content (a short hash), so a replaced file re-badges
    # The badge's own box, drawn around the text INSIDE the placement's backdrop (which then wraps
    # it as a separate layer): {fill '#RRGGBBAA', line_width, line_color, padding, radius,
    # min_width, max_width} in reference px. None = the backdrop is the only box.
    box: Optional[Dict[str, Any]] = None

    @property
    def signature(self) -> str:
        sig = f"{self.key}|{self.text}|{self.back_color or ''}|{self.font_color or ''}"
        if self.box:
            sig += "|box=" + ",".join(f"{k}={self.box[k]}" for k in sorted(self.box))
        if self.image:
            names = "+".join(os.path.basename(path) for path in (self.image, *(path for path, _h in self.image_tail)))
            sig += f"|{names}{'@' + self.image_stamp if self.image_stamp else ''}|{self.image_width}"
            if self.image_height:
                sig += "|h=" + "+".join(str(h) for h in (self.image_height, *(h for _p, h in self.image_tail)))
            if self.image_gap:
                sig += f"|g={self.image_gap}"
            if self.image_effect:
                sig += "|" + ",".join(f"{k}={self.image_effect[k]}" for k in sorted(self.image_effect))
        if self.joins:
            sig += f"|join={self.joins}"
        return sig


class OverlayType:
    """Base class for an overlay sub-module. Subclass it in services/overlays/types/<key>.py
    and set the class attributes; the registry discovers it automatically."""

    key: str = ""
    label: str = ""
    description: str = ""
    order: int = 100
    # Which poster kinds this overlay can decorate (TARGET_MOVIE / TARGET_SHOW / TARGET_SEASON).
    targets: tuple = (TARGET_MOVIE, TARGET_SHOW)
    # What this overlay needs the engine to fetch: "tmdb" (and "providers" for the watch
    # providers), "media" (the item's media files: resolution, HDR, audio). Override
    # needs_for() when that depends on the options (e.g. a rating source the user picks).
    needs: tuple = ()
    # Shared placement fields the page hides because the type sets them per state
    # (e.g. a status overlay colors every badge itself, so a shared back_color is noise).
    # "text" / "backdrop" hide those whole panels (an image badge has no text to style).
    hidden_placement: tuple = ()
    # {when: {option: value or values}, note}: while the options hold those values another overlay
    # places the badge, so the page greys out the Position and Backdrop cards and shows the note.
    placement_lock: Dict[str, Any] = {}
    # Types that draw PNG badges: their files (an images.ImageLibrary) and, from image_items(),
    # the keys a user may replace with an upload, each with its label.
    images: Any = None

    def default_placement(self) -> Dict[str, Any]:
        return dict(DEFAULT_PLACEMENT)

    def default_options(self) -> Dict[str, Any]:
        return {}

    def schema(self) -> List[Dict[str, Any]]:
        """Field descriptors the Overlays page renders for `options` (see OptionFields.tsx)."""
        return []

    def needs_for(self, options: Dict[str, Any]) -> tuple:
        return self.needs

    def image_items(self) -> List[tuple]:
        """(key, label) of every image file of this type (see `images`)."""
        return []

    def image_source(self, key: str, image_set: str) -> str:
        """Where a badge's file in a set comes from (see ImageLibrary.source). A type that joins
        some badges from parts answers for those itself."""
        library = getattr(self, "images", None)
        return library.source(key, image_set) if library is not None else "none"

    def image_png(self, key: str, image_set: str) -> Optional[bytes]:
        """A badge the set has no file for, drawn by the type itself as PNG bytes, or None."""
        return None

    def normalize_options(self, raw: Any) -> Dict[str, Any]:
        """Coerce a stored/user options dict into a valid one (defaults fill gaps)."""
        defaults = self.default_options()
        if not isinstance(raw, dict):
            return defaults
        merged = dict(defaults)
        merged.update({k: v for k, v in raw.items() if k in defaults})
        return merged

    def variants(self, options: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Sample states for the preview page: [{key, label}]. A variant that stands for a row of
        a states table carries `group` (the table's option key) and `item` (the row's key): the
        page then shows it only while that row is on, in the row's priority order where rows
        have one, from the options being edited rather than these defaults."""
        return []

    def resolve_variant(self, variant: str, options: Dict[str, Any]) -> Optional[OverlayBadge]:
        """The badge a preview variant draws."""
        return None

    def resolve(self, ctx: ItemContext, options: Dict[str, Any], meta: Dict[str, Any]) -> Optional[OverlayBadge]:
        """The badge for a real item, or None to draw nothing. `meta` is the engine-fetched
        metadata for the item (may be empty when a source is unavailable)."""
        return None

    def describe(self) -> Dict[str, Any]:
        opts = self.default_options()
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "order": self.order,
            "targets": list(self.targets),
            "needs": list(self.needs),
            "hidden_placement": list(self.hidden_placement),
            "placement_lock": self.placement_lock or None,
            "schema": self.schema(),
            "variants": self.variants(opts),
            "default_placement": self.default_placement(),
            "default_options": opts,
        }


def normalize_placement(raw: Any, defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Coerce a placement dict into valid values, filling gaps from `defaults`."""
    base = dict(defaults or DEFAULT_PLACEMENT)
    if not isinstance(raw, dict):
        return base
    out = dict(base)

    def _int(key: str, lo: int, hi: int) -> None:
        try:
            out[key] = max(lo, min(hi, int(raw.get(key, base[key]))))
        except (TypeError, ValueError):
            out[key] = base[key]

    def _color(key: str) -> None:
        val = str(raw.get(key, base[key]) or "").strip()
        out[key] = val if re.fullmatch(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", val) else base[key]

    ha = str(raw.get("horizontal_align", base["horizontal_align"])).lower()
    out["horizontal_align"] = ha if ha in H_ALIGNS else base["horizontal_align"]
    va = str(raw.get("vertical_align", base["vertical_align"])).lower()
    out["vertical_align"] = va if va in V_ALIGNS else base["vertical_align"]
    _int("horizontal_offset", -1000, 1000)
    _int("vertical_offset", -1500, 1500)
    _int("font_size", 8, 400)
    _int("letter_spacing", 0, 100)
    _int("stroke_width", 0, 20)
    _int("back_padding", 0, 200)
    _int("back_min_width", 0, 1000)
    _int("back_max_width", 0, 1000)
    if out["back_max_width"] and out["back_max_width"] < out["back_min_width"]:
        out["back_max_width"] = out["back_min_width"]  # a ceiling below the floor is the floor
    _int("back_radius", 0, 500)
    _int("back_line_width", 0, 50)
    _int("gradient_angle", 0, 359)
    _int("gradient_spread", 0, 500)
    _int("gradient_band_offset", 0, 1400)
    _int("gradient_band_height", 20, 1500)
    _int("gradient_band_left", 0, 1000)
    _int("gradient_band_right", 0, 1000)
    for key in ("font_color", "stroke_color", "back_color", "back_line_color"):
        _color(key)
    start = str(raw.get("gradient_start", base["gradient_start"]) or "").strip()
    out["gradient_start"] = start if start == "" or re.fullmatch(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", start) else base["gradient_start"]
    style = str(raw.get("back_style", base["back_style"])).lower()
    out["back_style"] = style if style in BACK_STYLES else base["back_style"]
    mode = str(raw.get("gradient_mode", base["gradient_mode"])).lower()
    out["gradient_mode"] = mode if mode in GRADIENT_MODES else base["gradient_mode"]
    curve = str(raw.get("gradient_band_curve", base["gradient_band_curve"])).lower()
    out["gradient_band_curve"] = curve if curve in BAND_CURVES else base["gradient_band_curve"]
    back_enabled = raw.get("back_enabled", base["back_enabled"])
    out["back_enabled"] = back_enabled if isinstance(back_enabled, bool) else str(back_enabled).lower() != "false"
    out["font"] = os.path.basename(str(raw.get("font", base["font"]) or base["font"]))
    if out["font"] in RETIRED_FONTS and _resolve_font([out["font"]]) is None:
        out["font"] = RETIRED_FONTS[out["font"]]
    out["font_weight"] = str(raw.get("font_weight", base["font_weight"]) or "").strip()[:40]
    return out
