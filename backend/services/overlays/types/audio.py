"""Audio badge: the audio codec of the media (Dolby TrueHD Atmos, DTS:X, DTS-HD MA, AAC …),
the way Kometa's default audio_codec overlay decides it.

Each codec has a pattern that is looked for in the audio tracks' titles and in the file
name, and a priority (Kometa's weights); the highest-priority codec that is found is the
badge. A show or a season is judged by its episodes (see media.py). Three styles, bundled
under backend/assets/overlays/audio/<set>/, every file cropped to its art: Kometa's compact
and standard images, sized in percent of their own size (an offset reaches the art and the
backdrop box hugs it; 26 px of padding and a 305 px minimum width give Kometa's box back),
and lettering, white lettering with no box (80 px tall, sized by height). A file with the same path under config/overlays/audio replaces the bundled one.
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
from ..media import AUDIO_CODECS, AUDIO_KEYS, AUDIO_LABELS, DEFAULT_PERCENT, SOURCE_KEYS, SOURCES, audio_badge

IMAGES = ImageLibrary("audio", sets=(("compact", "Compact"), ("standard", "Standard"), ("lettering", "Lettering")), noun="badge", cropped=True, height=80)
_SET_KEYS = [s for s, _l in IMAGES.sets]
_LETTERING = ("lettering",)  # sized by height in px; the other sets in percent of their own size
_SCALED = [s for s in _SET_KEYS if s not in _LETTERING]


class AudioOverlay(OverlayType):
    key = "audio"
    label = "Audio"
    description = "The audio codec of the media (Dolby TrueHD Atmos, DTS:X, DTS-HD MA, AAC …), from the audio track titles and the file names."
    order = 60
    targets = (TARGET_MOVIE, TARGET_SHOW, TARGET_SEASON)
    needs = ("media",)
    hidden_placement = ("text",)  # the badge is an image; the backdrop is its box
    images = IMAGES

    def image_items(self) -> List[tuple]:
        return [(key, AUDIO_LABELS[key]) for key in AUDIO_KEYS]

    def default_placement(self) -> Dict[str, Any]:
        return {
            **DEFAULT_PLACEMENT,
            # Under the Resolution badge, top left.
            "horizontal_align": "left",
            "horizontal_offset": 35,
            "vertical_align": "top",
            "vertical_offset": 150,
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
            "badge_style": "compact",
            "badge_scale": 100,
            "badge_height": 40,
            "episode_percent": DEFAULT_PERCENT,
            "source": "both",
            "apply_to_seasons": False,
            "codecs": {key: {"enabled": True, "priority": weight} for key, _label, weight, _p in AUDIO_CODECS},
        }

    def schema(self) -> List[Dict[str, Any]]:
        return [
            {"kind": "select", "key": "badge_style", "label": "Style", "section": "Badge", "options": [{"value": v, "label": l} for v, l in IMAGES.sets],
             "help": "Compact keeps every badge on one line. Standard stacks the Atmos ones on two lines. Lettering is plain white lettering with no box. Turn the Backdrop off or use an edge band with it."},
            {"kind": "number", "key": "badge_scale", "label": "Badge size (%)", "section": "Badge", "min": 10, "max": 400, "show_when": {"badge_style": _SCALED},
             "help": "100 is the badges' own size. The badges keep their sizes relative to each other."},
            {"kind": "number", "key": "badge_height", "label": "Badge height", "section": "Badge", "min": 20, "max": 300, "show_when": {"badge_style": list(_LETTERING)},
             "help": "Height of the lettering in px of a 1000x1500 poster; the width follows."},
            {"kind": "number", "key": "episode_percent", "label": "Episode share (%)", "section": "Badge", "min": 1, "max": 100,
             "help": "A show or season gets a codec when at least this share of its episodes has it."},
            {"kind": "select", "key": "source", "label": "Read from", "section": "Badge", "options": [{"value": v, "label": l} for v, l in SOURCES],
             "help": "The server gives the audio track titles. The file name names the codec when it follows TRaSH naming."},
            {"kind": "images", "key": "badges", "label": "Custom badges", "section": "Custom badges", "set_option": "badge_style", "noun": "badge",
             "help": "Replace a bundled badge of the chosen style with your own image. It is cropped to its artwork, drawn at the size of the badge it replaces and kept in config/overlays/audio."},
            {"kind": "toggle", "key": "apply_to_seasons", "label": "Season posters too", "section": "Seasons",
             "help": "Also badge each season poster, judged by that season's episodes."},
            {
                "kind": "states",
                "key": "codecs",
                "label": "Codecs",
                "item_label": "Codec",
                "split": 2,
                "help": "Each codec is looked for in the audio track titles and in the file name. When several are found, the highest priority is the badge.",
                "items": [
                    {"key": key, "label": label, "fields": [
                        {"kind": "number", "key": "priority", "label": "Priority", "min": 0, "max": 1000, "help": "Higher wins when several codecs are found."},
                    ]}
                    for key, label, _weight, _p in AUDIO_CODECS
                ],
            },
        ]

    def normalize_options(self, raw: Any) -> Dict[str, Any]:
        defaults = self.default_options()
        if not isinstance(raw, dict):
            return defaults
        out = dict(defaults)
        style = str(raw.get("badge_style") or defaults["badge_style"])
        out["badge_style"] = style if style in _SET_KEYS else defaults["badge_style"]
        for key, lo, hi in (("badge_scale", 10, 400), ("badge_height", 20, 300), ("episode_percent", 1, 100)):
            try:
                out[key] = max(lo, min(hi, int(raw.get(key, defaults[key]))))
            except (TypeError, ValueError):
                out[key] = defaults[key]
        seasons = raw.get("apply_to_seasons", defaults["apply_to_seasons"])
        out["apply_to_seasons"] = seasons if isinstance(seasons, bool) else str(seasons).lower() == "true"
        source = str(raw.get("source") or defaults["source"])
        out["source"] = source if source in SOURCE_KEYS else defaults["source"]
        rows: Dict[str, Any] = {}
        raw_rows = raw.get("codecs") if isinstance(raw.get("codecs"), dict) else {}
        for key, default_row in defaults["codecs"].items():
            rr = raw_rows.get(key) if isinstance(raw_rows.get(key), dict) else {}
            enabled = rr.get("enabled", default_row["enabled"])
            try:
                priority = max(0, min(1000, int(rr.get("priority", default_row["priority"]))))
            except (TypeError, ValueError):
                priority = default_row["priority"]
            rows[key] = {"enabled": enabled if isinstance(enabled, bool) else str(enabled).lower() == "true", "priority": priority}
        out["codecs"] = rows
        return out

    @staticmethod
    def _priorities(options: Dict[str, Any], *, enabled_only: bool = True) -> Dict[str, int]:
        rows = options.get("codecs") or {}
        return {key: int((rows.get(key) or {}).get("priority") or 0) for key in AUDIO_KEYS
                if not enabled_only or (rows.get(key) or {}).get("enabled", True)}

    def variants(self, options: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Every codec, highest priority first; the page keeps the ones switched on in the options it edits.
        ranked = sorted(self._priorities(options, enabled_only=False).items(), key=lambda kv: (-kv[1], AUDIO_KEYS.index(kv[0])))
        return [{"key": key, "label": AUDIO_LABELS[key], "group": "codecs", "item": key} for key, _p in ranked]

    def resolve_variant(self, variant: str, options: Dict[str, Any]) -> Optional[OverlayBadge]:
        if variant not in AUDIO_KEYS:
            ranked = sorted(self._priorities(options).items(), key=lambda kv: (-kv[1], AUDIO_KEYS.index(kv[0])))
            variant = ranked[0][0] if ranked else AUDIO_KEYS[0]
        return self._badge(variant, options)

    def resolve(self, ctx: ItemContext, options: Dict[str, Any], meta: Dict[str, Any]) -> Optional[OverlayBadge]:
        if ctx.is_season and not options.get("apply_to_seasons"):
            return None
        found = audio_badge(ctx.media, episodes=ctx.media_type != MEDIA_MOVIE, percent=options.get("episode_percent"),
                            priorities=self._priorities(options), source=options.get("source"))
        return self._badge(found, options) if found else None

    @staticmethod
    def _badge(key: str, options: Dict[str, Any]) -> Optional[OverlayBadge]:
        style = str(options.get("badge_style") or "")
        path = IMAGES.path(key, style)
        if path is None:
            return None
        height = (IMAGES.drawn_height(key, style, int(options.get("badge_height") or 0)) if style in _LETTERING
                  else IMAGES.drawn_height(key, style, int(options.get("badge_scale") or 0), reference=100))  # Kometa's art, in percent of its own size
        return OverlayBadge(key=key, text=AUDIO_LABELS.get(key, key), image=str(path), image_stamp=IMAGES.stamp(path), image_height=height)
