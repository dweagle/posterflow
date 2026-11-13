"""Edition badge: the cut of a movie (Director's Cut, IMAX, Extended Edition …), read from
the {edition-...} tag in its file name (see editions.py). Movies only.

One style, lettering: white lettering with no box, cropped to the art and sized by height,
bundled under backend/assets/overlays/edition/lettering/. A file with the same path under
config/overlays/edition replaces the bundled one.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..base import DEFAULT_PLACEMENT, ItemContext, MEDIA_MOVIE, OverlayBadge, OverlayType, TARGET_MOVIE
from ..editions import EDITION_KEYS, EDITION_LABELS, EDITION_WEIGHTS, edition_badge
from ..images import ImageLibrary

IMAGES = ImageLibrary("edition", sets=(("lettering", "Lettering"),), noun="badge", cropped=True, height=84)
_STYLE = IMAGES.default_set
BADGE_HEIGHT = 42  # the art's own size: 84 px of art drawn at half size
DEFAULT_HEIGHT = 35


class EditionOverlay(OverlayType):
    key = "edition"
    label = "Edition"
    description = "The cut of a movie (Director's Cut, IMAX, Extended Edition …), from the {edition-...} tag in its file name."
    order = 65
    targets = (TARGET_MOVIE,)
    needs = ("media",)
    hidden_placement = ("text",)  # the badge is an image
    images = IMAGES

    def image_items(self) -> List[tuple]:
        return [(key, EDITION_LABELS[key]) for key in EDITION_KEYS]

    def default_placement(self) -> Dict[str, Any]:
        return {
            **DEFAULT_PLACEMENT,
            # Under the Resolution and Audio badges, top left, on its own box.
            "horizontal_align": "left",
            "horizontal_offset": 35,
            "vertical_align": "top",
            "vertical_offset": 280,
            "back_enabled": True,
            "gradient_band_height": 200,
        }

    def default_options(self) -> Dict[str, Any]:
        return {
            "badge_height": DEFAULT_HEIGHT,
            "editions": {key: {"enabled": True, "priority": EDITION_WEIGHTS[key]} for key in EDITION_KEYS},
        }

    def schema(self) -> List[Dict[str, Any]]:
        return [
            {"kind": "number", "key": "badge_height", "label": "Badge height", "section": "Badge", "min": 20, "max": 300,
             "help": f"Height of the lettering in px of a 1000x1500 poster; the width follows. {BADGE_HEIGHT} is the badge's own size."},
            {"kind": "images", "key": "custom_badges", "label": "Custom badges", "section": "Custom badges", "noun": "badge",
             "help": "Replace a bundled badge with your own image. It is cropped to its artwork, drawn at the size of the badge it replaces and kept in config/overlays/edition."},
            {
                "kind": "states",
                "key": "editions",
                "label": "Editions",
                "item_label": "Edition",
                "split": 2,
                "help": "Each edition is looked for in the {edition-...} tag of the file name. When several are named, the highest priority is the badge.",
                "items": [
                    {"key": key, "label": EDITION_LABELS[key], "fields": [
                        {"kind": "number", "key": "priority", "label": "Priority", "min": 0, "max": 1000, "help": "Higher wins when several editions are named."},
                    ]}
                    for key in EDITION_KEYS
                ],
            },
        ]

    def normalize_options(self, raw: Any) -> Dict[str, Any]:
        defaults = self.default_options()
        if not isinstance(raw, dict):
            return defaults
        out = dict(defaults)
        try:
            out["badge_height"] = max(20, min(300, int(raw.get("badge_height", defaults["badge_height"]))))
        except (TypeError, ValueError):
            out["badge_height"] = defaults["badge_height"]
        rows: Dict[str, Any] = {}
        raw_rows = raw.get("editions") if isinstance(raw.get("editions"), dict) else {}
        for key, default_row in defaults["editions"].items():
            rr = raw_rows.get(key) if isinstance(raw_rows.get(key), dict) else {}
            enabled = rr.get("enabled", default_row["enabled"])
            try:
                priority = max(0, min(1000, int(rr.get("priority", default_row["priority"]))))
            except (TypeError, ValueError):
                priority = default_row["priority"]
            rows[key] = {"enabled": enabled if isinstance(enabled, bool) else str(enabled).lower() == "true", "priority": priority}
        out["editions"] = rows
        return out

    @staticmethod
    def _priorities(options: Dict[str, Any], *, enabled_only: bool = True) -> Dict[str, int]:
        rows = options.get("editions") or {}
        return {key: int((rows.get(key) or {}).get("priority") or 0) for key in EDITION_KEYS
                if not enabled_only or (rows.get(key) or {}).get("enabled", True)}

    def variants(self, options: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Every edition, highest priority first; the page keeps the ones switched on in the options it edits.
        ranked = sorted(self._priorities(options, enabled_only=False).items(), key=lambda kv: (-kv[1], EDITION_KEYS.index(kv[0])))
        return [{"key": key, "label": EDITION_LABELS[key], "group": "editions", "item": key} for key, _p in ranked]

    def resolve_variant(self, variant: str, options: Dict[str, Any]) -> Optional[OverlayBadge]:
        if variant not in EDITION_KEYS:
            ranked = sorted(self._priorities(options).items(), key=lambda kv: (-kv[1], EDITION_KEYS.index(kv[0])))
            variant = ranked[0][0] if ranked else EDITION_KEYS[0]
        return self._badge(variant, options)

    def resolve(self, ctx: ItemContext, options: Dict[str, Any], meta: Dict[str, Any]) -> Optional[OverlayBadge]:
        if ctx.media_type != MEDIA_MOVIE:
            return None
        found = edition_badge(ctx.media, self._priorities(options))
        return self._badge(found, options) if found else None

    @staticmethod
    def _badge(key: str, options: Dict[str, Any]) -> Optional[OverlayBadge]:
        path = IMAGES.path(key, _STYLE)
        if path is None:
            return None
        return OverlayBadge(key=key, text=EDITION_LABELS.get(key, key), image=str(path),
                            image_height=IMAGES.drawn_height(key, _STYLE, int(options.get("badge_height") or 0)), image_stamp=IMAGES.stamp(path))
