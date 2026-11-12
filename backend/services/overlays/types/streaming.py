"""Streaming-service badge: the logo of the service that streams the item.

Availability comes from TMDB's watch providers (JustWatch data) for the region you pick.
Every row of the Services table lists the TMDB provider ids that count as that service and
a priority; an item on several services shows the highest-priority logo. Two logo sets
ship under backend/assets/overlays/streaming/: white/<key>.png (plain white) and
color/<key>.png (the services' own colors, Kometa's set; a service without one draws its
white logo). The outline styles, and the backdrop behind the logo, are drawn at render
time. A file with the same path under config/overlays/streaming replaces the bundled one.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..base import (
    DEFAULT_PLACEMENT,
    ItemContext,
    OverlayBadge,
    OverlayType,
    TARGET_MOVIE,
    TARGET_SEASON,
    TARGET_SHOW,
)
from ..images import ImageLibrary, effect, effect_defaults, normalize_effect, outline_fields, style_field

IMAGES = ImageLibrary("streaming", frame=(300, 100), sets=(("white", "White"), ("color", "Color")), margin=6, noun="logo")
_SET_KEYS = [s for s, _l in IMAGES.sets]

# key (= logo file name), label, TMDB provider ids, priority (higher wins), on by default.
# BET+ and Freevee have no TMDB provider, so they are not listed.
PROVIDER_DEFS = [
    ("netflix", "Netflix", "8, 1796", 180, True),
    ("appletv", "Apple TV", "350, 2243", 170, True),
    ("disney", "Disney+", "337", 160, True),
    ("hbo-max", "HBO Max", "1899, 1825", 150, True),
    ("prime", "Prime Video", "9, 2100, 613", 140, True),
    ("crunchyroll", "Crunchyroll", "283, 1968", 130, True),
    ("hulu", "Hulu", "15", 100, True),
    ("paramount", "Paramount+", "531, 2303, 2616, 1853, 582, 633", 90, True),
    ("peacock", "Peacock", "386, 387", 80, True),
    ("discovery-plus", "Discovery+", "510, 520, 524, 584", 60, True),
    ("starz", "Starz", "43, 1855, 634, 1794", 27, True),
    ("amc-plus", "AMC+", "80, 526, 1854, 528, 635", 25, True),
    ("fxnow", "FXNOW", "123", 25, True),
    ("mgm-plus", "MGM+", "34, 636, 583", 25, True),
    ("tbs", "TBS", "506", 25, True),
    ("tnt", "TNT", "363", 25, True),
    ("usa", "USA Network", "322", 25, True),
    # Off until wanted: the other bundled logos.
    ("all4", "Channel 4", "103, 2311", 20, False),
    ("britbox", "BritBox", "151, 197, 1852", 20, False),
    ("crave", "Crave", "230, 2604", 20, False),
    ("fubotv", "fuboTV", "257", 20, False),
    ("hayu", "hayu", "223, 296", 20, False),
    ("hoopla", "Hoopla", "212", 20, False),
    ("itvx", "ITVX", "41, 2300, 598", 20, False),
    ("my5", "My5", "333", 20, False),
    ("now", "NOW", "39, 591", 20, False),
    ("stan", "Stan", "21", 20, False),
    ("trutv", "truTV", "507", 20, False),
    ("tubitv", "Tubi", "73", 20, False),
    ("youtube", "YouTube", "192, 188, 235", 20, False),
    ("plex", "Plex", "538, 2077", 15, False),
]
_PROVIDER_KEYS = [d[0] for d in PROVIDER_DEFS]
# Logos that are not TMDB services: drawn only as the "on no service" fallback.
EXTRA_LOGOS = [("jellyfin", "Jellyfin")]
_LABELS = {d[0]: d[1] for d in PROVIDER_DEFS} | {k: l for k, l in EXTRA_LOGOS}
_FALLBACK_VARIANT = "fallback"

# option key, label, TMDB offer kind
OFFERS = [
    ("flatrate", "Plan", "Included in the service's subscription plan."),
    ("free", "Free", "Free to stream."),
    ("ads", "Ads", "Free to stream with ads."),
    ("rent", "Rent", "Available to rent."),
    ("buy", "Buy", "Available to buy."),
]
# value, label; every value but "none" is the key of the logo drawn.
FALLBACKS = [("none", "Nothing"), ("plex", "The Plex logo"), ("jellyfin", "The Jellyfin logo")]
_FALLBACK_KEYS = [f[0] for f in FALLBACKS]


def _ids(raw: Any) -> List[int]:
    return [int(v) for v in re.split(r"[,\s|]+", str(raw or "")) if v.strip().isdigit()]


class StreamingOverlay(OverlayType):
    key = "streaming"
    label = "Streaming"
    description = "The logo of the service that streams the item, from TMDB's watch providers for your region."
    order = 40
    targets = (TARGET_MOVIE, TARGET_SHOW, TARGET_SEASON)
    needs = ("tmdb", "providers")
    hidden_placement = ("text",)  # a logo has no text to style; the backdrop (box, fade, band) is drawn behind it like text
    images = IMAGES

    def image_items(self) -> List[tuple]:
        """Every logo a user may replace: the services plus the fallback-only ones."""
        return [(key, _LABELS[key]) for key in _PROVIDER_KEYS] + list(EXTRA_LOGOS)

    def default_placement(self) -> Dict[str, Any]:
        return {
            **DEFAULT_PLACEMENT,
            "horizontal_align": "center",
            "horizontal_offset": 0,
            "vertical_align": "top",
            "vertical_offset": 30,
            "back_enabled": False,  # nothing behind the logo until asked for
            "back_style": "band",   # switching the backdrop on gives the edge band first
        }

    def default_options(self) -> Dict[str, Any]:
        return {
            "logo_set": "white",
            **effect_defaults("outline"),
            "logo_width": 300,
            "watch_region": "US",
            **{offer: True for offer, _l, _h in OFFERS},
            "fallback": "plex",
            "apply_to_seasons": False,
            "providers": {key: {"enabled": on, "ids": ids, "priority": priority} for key, _label, ids, priority, on in PROVIDER_DEFS},
        }

    def schema(self) -> List[Dict[str, Any]]:
        return [
            {"kind": "select", "key": "logo_set", "label": "Logo", "section": "Logo", "options": [{"value": v, "label": l} for v, l in IMAGES.sets],
             "help": "White logos, or the services' own colors. A service with no color logo draws its white one."},
            style_field("Logo", "logo"),
            {"kind": "number", "key": "logo_width", "label": "Logo width", "section": "Logo", "min": 20, "max": 1000,
             "help": "Width in px of a 1000x1500 poster; the height follows. 300 is the logo's own size."},
            *outline_fields("Logo"),
            {"kind": "images", "key": "logos", "label": "Custom logos", "section": "Custom logos", "set_option": "logo_set", "noun": "logo",
             "help": "Replace a bundled logo of the chosen set with your own image. It is fitted onto the 300 x 100 frame the bundled ones use and kept in config/overlays/streaming."},
            {"kind": "text", "key": "watch_region", "label": "Region", "section": "Availability",
             "help": "Two-letter country code whose catalog is checked (US, GB, CA, AU)."},
            {"kind": "select", "key": "fallback", "label": "On no service", "section": "Availability",
             "options": [{"value": v, "label": l} for v, l in FALLBACKS], "help": "What an item that none of the services carry gets."},
            {"kind": "toggles", "key": "offers", "label": "Offers", "section": "Availability",
             "help": "Which kinds of listing count as being on a service.",
             "toggles": [{"key": offer, "label": label, "help": help} for offer, label, help in OFFERS]},
            {"kind": "toggle", "key": "apply_to_seasons", "label": "Season posters too", "section": "Seasons",
             "help": "Also badge each season poster with the show's service."},
            {
                "kind": "states",
                "key": "providers",
                "label": "Services",
                "item_label": "Service",
                "split": 2,  # a long list: two tables side by side
                "help": "An item on several services shows the one with the highest priority. Ids are TMDB watch provider ids, comma-separated.",
                "items": [
                    {"key": key, "label": label, "fields": [
                        {"kind": "text", "key": "ids", "label": "TMDB ids", "help": "Provider ids that count as this service."},
                        {"kind": "number", "key": "priority", "label": "Priority", "min": 0, "max": 1000, "help": "Higher wins when an item is on several services."},
                    ]}
                    for key, label, _ids, _priority, _on in PROVIDER_DEFS
                ],
            },
        ]

    def normalize_options(self, raw: Any) -> Dict[str, Any]:
        defaults = self.default_options()
        if not isinstance(raw, dict):
            return defaults
        out = dict(defaults)
        logo_set = str(raw.get("logo_set") or defaults["logo_set"])
        out["logo_set"] = logo_set if logo_set in _SET_KEYS else defaults["logo_set"]
        normalize_effect(raw, out, defaults)
        try:
            out["logo_width"] = max(20, min(1000, int(raw.get("logo_width", defaults["logo_width"]))))
        except (TypeError, ValueError):
            out["logo_width"] = defaults["logo_width"]
        region = re.sub(r"[^A-Za-z]", "", str(raw.get("watch_region") or "")).upper()[:2]
        out["watch_region"] = region if len(region) == 2 else defaults["watch_region"]
        for key in [offer for offer, _l, _h in OFFERS] + ["apply_to_seasons"]:
            val = raw.get(key, defaults[key])
            out[key] = val if isinstance(val, bool) else str(val).lower() == "true"
        fallback = str(raw.get("fallback") or defaults["fallback"])
        out["fallback"] = fallback if fallback in _FALLBACK_KEYS else defaults["fallback"]
        rows: Dict[str, Any] = {}
        raw_rows = raw.get("providers") if isinstance(raw.get("providers"), dict) else {}
        for key, default_row in defaults["providers"].items():
            rr = raw_rows.get(key) if isinstance(raw_rows.get(key), dict) else {}
            enabled = rr.get("enabled", default_row["enabled"])
            try:
                priority = max(0, min(1000, int(rr.get("priority", default_row["priority"]))))
            except (TypeError, ValueError):
                priority = default_row["priority"]
            rows[key] = {
                "enabled": enabled if isinstance(enabled, bool) else str(enabled).lower() == "true",
                "ids": str(rr.get("ids", default_row["ids"]) or "").strip()[:200],
                "priority": priority,
            }
        out["providers"] = rows
        return out

    @staticmethod
    def _ranked(options: Dict[str, Any]) -> List[str]:
        """Enabled service keys, highest priority first (table order breaks ties)."""
        rows = options.get("providers") or {}
        keys = [k for k in _PROVIDER_KEYS if (rows.get(k) or {}).get("enabled", True)]
        return sorted(keys, key=lambda k: (-int((rows.get(k) or {}).get("priority") or 0), _PROVIDER_KEYS.index(k)))

    @staticmethod
    def _fallback_logo(options: Dict[str, Any]) -> Optional[str]:
        """The logo key an item on no service gets, or None for nothing."""
        key = str(options.get("fallback") or "none")
        return key if key in _FALLBACK_KEYS and key != "none" else None

    def variants(self, options: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Every service, highest priority first; the page keeps the ones switched on in the options it edits.
        rows = options.get("providers") or {}
        ranked = sorted(_PROVIDER_KEYS, key=lambda k: (-int((rows.get(k) or {}).get("priority") or 0), _PROVIDER_KEYS.index(k)))
        out: List[Dict[str, Any]] = [{"key": key, "label": _LABELS[key], "group": "providers", "item": key} for key in ranked]
        fallback = self._fallback_logo(options)
        if fallback:
            # The page relabels (or hides) this entry as the "On no service" option changes.
            labels = {k: f"On no service ({_LABELS[k]} logo)" for k, _l in FALLBACKS if k != "none"}
            out.append({"key": _FALLBACK_VARIANT, "label": labels[fallback], "option": "fallback", "labels": labels})
        return out

    def resolve_variant(self, variant: str, options: Dict[str, Any]) -> Optional[OverlayBadge]:
        if variant == _FALLBACK_VARIANT:
            return self._badge(self._fallback_logo(options) or "plex", options)
        if variant not in _PROVIDER_KEYS:
            ranked = self._ranked(options)
            variant = ranked[0] if ranked else _PROVIDER_KEYS[0]
        return self._badge(variant, options)

    def resolve(self, ctx: ItemContext, options: Dict[str, Any], meta: Dict[str, Any]) -> Optional[OverlayBadge]:
        if ctx.is_season and not options.get("apply_to_seasons"):
            return None
        tmdb = meta.get("tmdb") if isinstance(meta, dict) else None
        if not isinstance(tmdb, dict) or "providers" not in tmdb:
            return None  # no record (or one fetched before providers were wanted): nothing to say yet
        key = self.match(tmdb.get("providers") or {}, options) or self._fallback_logo(options)
        return self._badge(key, options) if key else None

    def match(self, providers: Dict[str, Any], options: Dict[str, Any]) -> Optional[str]:
        """The highest-priority enabled service among the item's offers in the chosen region."""
        offers = providers.get(str(options.get("watch_region") or "US").upper()) or {}
        on_item = {int(pid) for offer, _l, _h in OFFERS if options.get(offer, True) for pid in (offers.get(offer) or [])}
        if not on_item:
            return None
        rows = options.get("providers") or {}
        for key in self._ranked(options):
            if on_item.intersection(_ids((rows.get(key) or {}).get("ids"))):
                return key
        return None

    @staticmethod
    def _badge(key: str, options: Dict[str, Any]) -> Optional[OverlayBadge]:
        path = IMAGES.path(key, str(options.get("logo_set") or "white"))
        if path is None:
            return None
        return OverlayBadge(key=key, text=_LABELS.get(key, key), image=str(path), image_width=int(options.get("logo_width") or 0),
                            image_effect=effect(options), image_stamp=IMAGES.stamp(path))
