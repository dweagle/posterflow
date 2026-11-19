"""Credits-scene badge: marks a movie that has a scene after its credits (post credits),
during them (mid credits) or both, from TMDB's keywords. Movies only.

Five styles ship under backend/assets/overlays/mediastinger/. icon is one file,
icon/stinger.png (the yellow S of Kometa's mediastinger overlay), drawn for every scene. The
other four are lettered in the shape of the content-rating badges, one file per scene,
<style>/<scene>.png (PCS / MCS / M+P): color (the icon's yellow with black lettering) and
white (white lettering, no fill), each inside a white line, and color_plain and white_plain
(the same without the line). A file with the same path under config/overlays/mediastinger
replaces the bundled one.

A sixth style, text, draws no file: each scene has its own wording and, if wanted, its own
text color, set in the Scenes table and styled like any text badge.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..base import DEFAULT_PLACEMENT, ItemContext, MEDIA_MOVIE, OverlayBadge, OverlayType, TARGET_MOVIE
from ..images import ImageLibrary

IMAGES = ImageLibrary(
    "mediastinger",
    sets=(("icon", "S icon"), ("color", "Color"), ("color_plain", "Color, no border"),
          ("white", "Transparent"), ("white_plain", "Transparent, no border")),
    noun="badge", cropped=True, height=88,
)
TEXT = "text"  # the style that draws each scene's wording instead of a badge file
STYLES = [*IMAGES.sets, (TEXT, "Text")]
_STYLES = [s for s, _l in STYLES]
_IMAGE_STYLES = [s for s, _l in IMAGES.sets]
_ICON = [IMAGES.default_set]                              # sized in percent of its own size
_LETTERED = [s for s in _IMAGE_STYLES if s not in _ICON]  # sized by width, like the content-rating badges
LETTERED_WIDTH = 119  # the lettered badges' own width

# key (= a lettered style's badge file name), label
SCENES = [("post", "Post credits"), ("mid", "Mid credits"), ("both", "Mid and post credits")]
_LABELS = dict(SCENES)
TEXTS = {"post": "PCS", "mid": "MCS", "both": "M+P"}  # the text style's wording until the user sets their own
ICON = ("stinger", "S icon")  # the icon style's one file
KEYWORDS = {"post": "aftercreditsstinger", "mid": "duringcreditsstinger"}


def _enabled(options: Dict[str, Any], key: str) -> bool:
    return bool(((options.get("scenes") or {}).get(key) or {}).get("enabled", True))


def scene_for(keywords: Any, options: Dict[str, Any]) -> Optional[str]:
    """The scene badge a movie's TMDB keywords call for. A movie with both scenes gets the
    'both' badge, else its post credits one, else its mid credits one, whichever is on first."""
    keywords = keywords or []
    post, mid = KEYWORDS["post"] in keywords, KEYWORDS["mid"] in keywords
    wanted = ("both", "post", "mid") if post and mid else ("post",) if post else ("mid",) if mid else ()
    return next((key for key in wanted if _enabled(options, key)), None)


class MediastingerOverlay(OverlayType):
    key = "mediastinger"
    label = "Credits Scenes"
    description = "Marks a movie that has a scene during or after its credits, from TMDB's keywords."
    order = 70
    targets = (TARGET_MOVIE,)
    needs = ("tmdb", "keywords")
    hidden_placement = ("stroke_width", "stroke_color")  # no letter outline here: the Style picker has its place in the Text card
    hidden_placement_when = {"text": {"badge_style": _IMAGE_STYLES}}  # an image badge has no text to style
    images = IMAGES

    def image_items(self) -> List[tuple]:
        return [ICON, *SCENES]

    def image_items_in(self, image_set: str) -> List[tuple]:
        return [ICON] if image_set in _ICON else list(SCENES)

    def default_placement(self) -> Dict[str, Any]:
        return {
            **DEFAULT_PLACEMENT,
            "horizontal_align": "left",
            "horizontal_offset": 35,
            "vertical_align": "top",
            "vertical_offset": 35,
            # The icon's dark box: 105 px wide around the 85 x 88 icon.
            "back_enabled": True,
            "back_style": "solid",
            "back_color": "#00000099",
            "back_padding": 9,
            "back_radius": 30,
            "back_min_width": 105,
        }

    def default_options(self) -> Dict[str, Any]:
        return {
            "badge_style": IMAGES.default_set,
            "badge_scale": 100,
            "badge_width": 130,
            "scenes": {key: {"enabled": True, "text": TEXTS[key], "font_color": ""} for key, _label in SCENES},
        }

    def schema(self) -> List[Dict[str, Any]]:
        return [
            {"kind": "select", "key": "badge_style", "label": "Style", "section": "Badge", "options": [{"value": v, "label": l} for v, l in STYLES],
             "in_text_card": True,  # it leads the Text card while that card shows, so it never moves
             "help": "The yellow S icon for every scene, or lettered badges shaped like the content rating badges: PCS for post credits, MCS for mid credits, M+P for both. "
                     "Yellow box or transparent, each with or without its white border. Turn the Backdrop off with a lettered badge. "
                     "Text draws your own wording for each scene, set in the Scenes table and styled in the Text card."},
            {"kind": "number", "key": "badge_scale", "label": "Badge size (%)", "section": "Badge", "min": 10, "max": 400, "show_when": {"badge_style": _ICON},
             "help": "100 is the icon's own size."},
            {"kind": "number", "key": "badge_width", "label": "Badge width", "section": "Badge", "min": 20, "max": 1000, "show_when": {"badge_style": _LETTERED},
             "help": f"Width in px of a 1000x1500 poster; the height follows. {LETTERED_WIDTH} is the badge's own size."},
            {"kind": "images", "key": "custom_badges", "label": "Custom badges", "section": "Custom badges", "set_option": "badge_style", "noun": "badge",
             "show_when": {"badge_style": _IMAGE_STYLES},
             "help": "Replace a bundled badge of the chosen style with your own image. The S icon style has one icon for every scene. It is cropped to its artwork, sized like the badge it replaces and kept in config/overlays/mediastinger."},
            {
                "kind": "states",
                "key": "scenes",
                "label": "Scenes",
                "item_label": "Scene",
                "help": "Post credits is a scene after the credits. Mid credits is a scene during them. A movie with both gets the Mid and post credits badge. "
                        "With that one off it gets its post credits badge, or its mid credits badge when that is off too.",
                "items": [{"key": key, "label": label, "fields": [
                    {"kind": "text", "key": "text", "label": "Text", "show_when": {"badge_style": TEXT}, "help": "What the Text style draws for this scene. A scene with no text gets no badge."},
                    {"kind": "color", "key": "font_color", "label": "Text color", "alpha": True, "optional": True, "show_when": {"badge_style": TEXT},
                     "help": "Blank = the default text color from the Text card."},
                ]} for key, label in SCENES],
            },
        ]

    def normalize_options(self, raw: Any) -> Dict[str, Any]:
        defaults = self.default_options()
        if not isinstance(raw, dict):
            return defaults
        out = dict(defaults)
        style = str(raw.get("badge_style") or defaults["badge_style"])
        out["badge_style"] = style if style in _STYLES else defaults["badge_style"]
        for key, lo, hi in (("badge_scale", 10, 400), ("badge_width", 20, 1000)):
            try:
                out[key] = max(lo, min(hi, int(raw.get(key, defaults[key]))))
            except (TypeError, ValueError):
                out[key] = defaults[key]
        rows: Dict[str, Any] = {}
        raw_rows = raw.get("scenes") if isinstance(raw.get("scenes"), dict) else {}
        for key, default_row in defaults["scenes"].items():
            rr = raw_rows.get(key) if isinstance(raw_rows.get(key), dict) else {}
            enabled = rr.get("enabled", default_row["enabled"])
            rows[key] = {
                "enabled": enabled if isinstance(enabled, bool) else str(enabled).lower() == "true",
                "text": str(rr.get("text", default_row["text"]) or "").strip()[:60],
                "font_color": str(rr.get("font_color", default_row["font_color"]) or "").strip(),
            }
        out["scenes"] = rows
        return out

    def variants(self, options: Dict[str, Any]) -> List[Dict[str, Any]]:
        # The page lists the one icon while the icon style is picked, else the scenes that are on.
        icon = {"key": ICON[0], "label": ICON[1], "option": "badge_style", "labels": {s: ICON[1] for s in _ICON}}
        return [icon] + [{"key": key, "label": label, "group": "scenes", "item": key, "option": "badge_style", "labels": {s: label for s in (*_LETTERED, TEXT)}}
                         for key, label in SCENES]

    def resolve_variant(self, variant: str, options: Dict[str, Any]) -> Optional[OverlayBadge]:
        return self._badge(variant if variant in _LABELS else SCENES[0][0], options)

    def resolve(self, ctx: ItemContext, options: Dict[str, Any], meta: Dict[str, Any]) -> Optional[OverlayBadge]:
        if ctx.media_type != MEDIA_MOVIE:
            return None
        scene = scene_for((meta.get("tmdb") or {}).get("keywords"), options)
        return self._badge(scene, options) if scene else None

    @staticmethod
    def _badge(scene: str, options: Dict[str, Any]) -> Optional[OverlayBadge]:
        style = str(options.get("badge_style") or IMAGES.default_set)
        if style == TEXT:
            row = (options.get("scenes") or {}).get(scene) or {}
            text = str(row.get("text") or "").strip()
            return OverlayBadge(key=scene, text=text, font_color=row.get("font_color") or None) if text else None
        file_key = scene if style in _LETTERED else ICON[0]
        path = IMAGES.path(file_key, style)
        if path is None:
            return None
        if style in _LETTERED:
            size = {"image_width": int(options.get("badge_width") or 0)}
        else:
            size = {"image_height": IMAGES.drawn_height(file_key, style, int(options.get("badge_scale") or 0), reference=100)}
        return OverlayBadge(key=scene, text=_LABELS[scene], image=str(path), image_stamp=IMAGES.stamp(path), **size)
