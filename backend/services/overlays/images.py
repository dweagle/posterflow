"""PNG badges with user-replaceable files, shared by the types that draw images instead of
text (streaming logos, content-rating badges).

A type's files live in sets, <set>/<key>.png, under backend/assets/overlays/<name>/; a file
with the same path under config/overlays/<name>/ replaces the bundled one. Every file of a
library sits on one frame, so a width option means the same for all of them. A library may
instead be cropped (`cropped`): every file cut to its art with no frame, drawn by height at
one scale per set, so small art stays small beside big. The outline styles (a solid or soft outline around the image's shape) are drawn at render time from the
same <style>_* options in every type that offers them.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import settings as app_settings

BUNDLED_DIR = Path(__file__).resolve().parents[2] / "assets" / "overlays"

# value, label. Each outline style has its own width/color/opacity options (<style>_*); the
# edge softness is fixed per style: crisp for the outline, blurred for the soft one.
STYLES = [("plain", "Plain"), ("outline", "Outline"), ("soft", "Soft outline")]
STYLE_KEYS = [s[0] for s in STYLES]
_SOFTNESS = {"outline": 1.0, "soft": 3.0}
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


def config_overlays_dir() -> Path:
    return app_settings.config_dir / "overlays"


@lru_cache(maxsize=256)
def _stamp(path: str, _mtime: float, _size: int) -> str:
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()[:8]


@lru_cache(maxsize=256)
def _png_height(path: str, _mtime: float) -> int:
    from PIL import Image

    with Image.open(path) as img:
        return img.height


@dataclass(frozen=True)
class ImageLibrary:
    """One type's image files: where they live, what their sets are called, and the frame an
    upload is fitted onto."""

    name: str                      # folder under assets/overlays and config/overlays
    # (set key, label), in the order the page offers them. The first is the default set: what
    # an unnamed set means, and what is drawn when another set has no file for a key.
    sets: Tuple[Tuple[str, str], ...]
    frame: Tuple[int, int] = (0, 0)  # a framed library: every file sits centered on this transparent frame
    margin: int = 0                # air kept inside the frame when an upload is fitted
    noun: str = "logo"             # what one file is called (log lines, the page)
    # A cropped library: every file is cut to its art, with no frame, and drawn by height
    # (see drawn_height); an upload is cropped and scaled to the bundled file it replaces.
    cropped: bool = False
    # For a cropped library, the tallest bundled art of its lettering sets: a height setting
    # is how tall that one is drawn and the rest follow in proportion.
    height: int = 0

    @property
    def bundled_dir(self) -> Path:
        return BUNDLED_DIR / self.name

    @property
    def config_dir(self) -> Path:
        return config_overlays_dir() / self.name

    @property
    def default_set(self) -> str:
        return self.sets[0][0]

    def set_label(self, image_set: str) -> str:
        return dict(self.sets).get(image_set, image_set)

    def custom_file(self, key: str, image_set: str) -> Path:
        return self.config_dir / image_set / f"{key}.png"

    def file(self, key: str, image_set: str = "") -> Optional[Path]:
        """The file for a key in exactly this set (custom first, then bundled), or None."""
        image_set = image_set or self.default_set
        for directory in (self.config_dir, self.bundled_dir):
            candidate = directory / image_set / f"{key}.png"
            if candidate.is_file():
                return candidate
        return None

    def path(self, key: str, image_set: str = "") -> Optional[Path]:
        """The file a key draws in a set; a set without it falls back to the default set's."""
        image_set = image_set or self.default_set
        return self.file(key, image_set) or (self.file(key, self.default_set) if image_set != self.default_set else None)

    def source(self, key: str, image_set: str = "") -> str:
        """'custom' | 'bundled' | 'fallback' (the set has no file for it; the default set's is drawn) | 'none'."""
        image_set = image_set or self.default_set
        path = self.file(key, image_set)
        if path is not None:
            return "custom" if path.is_relative_to(self.config_dir) else "bundled"
        return "fallback" if image_set != self.default_set and self.file(key, self.default_set) is not None else "none"

    def bundled_height(self, key: str, image_set: str = "") -> int:
        """How tall the bundled file for a key is in a set (`height` when there is none)."""
        path = self.bundled_dir / (image_set or self.default_set) / f"{key}.png"
        try:
            return _png_height(str(path), path.stat().st_mtime)
        except OSError:
            return self.height

    def drawn_height(self, key: str, image_set: str, setting: int, reference: int = 0) -> int:
        """The height a cropped badge is drawn at: its bundled file's height times
        setting / reference. The reference is `height` (a lettering set: the setting is the
        tallest lettering's height in px) or what the caller gives (100 for a size in
        percent). It goes by the bundled file, so a custom file of any size is drawn as tall
        as the one it replaces."""
        reference = reference or self.height
        if not reference or setting <= 0:
            return max(0, setting)
        return max(1, round(setting * self.bundled_height(key, image_set) / reference))

    def fit(self, img: Any, key: str = "", image_set: str = "") -> Any:
        """An upload made ready to store: cropped to its artwork, then set on the frame
        (scaled to fit inside the margin, centered on transparency) or, in a cropped library,
        scaled to the size of the bundled file it replaces. The bundled framed files went
        through this too, so the air around every image is the same and a backdrop box pads
        evenly."""
        from PIL import Image

        img = img.convert("RGBA")
        bbox = img.getchannel("A").getbbox() or (0, 0, img.width, img.height)
        img = img.crop(bbox)
        if self.cropped:
            height = self.bundled_height(key, image_set)
            size = (max(1, round(img.width * height / img.height)), height)
            return img if size == img.size else img.convert("RGBa").resize(size, Image.LANCZOS).convert("RGBA")
        inner = (self.frame[0] - 2 * self.margin, self.frame[1] - 2 * self.margin)
        scale = min(inner[0] / img.width, inner[1] / img.height)
        w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
        if (w, h) != img.size:
            img = img.convert("RGBa").resize((w, h), Image.LANCZOS).convert("RGBA")  # premultiplied: no dark fringes at the edges
        canvas = Image.new("RGBA", self.frame, (0, 0, 0, 0))
        canvas.paste(img, ((self.frame[0] - w) // 2, (self.frame[1] - h) // 2))  # a plain paste: a masked one would darken and thin the edges
        return canvas

    @staticmethod
    def stamp(path: Path) -> str:
        """A short hash of the file's content: part of the badge signature, so a replaced
        file re-badges every poster that shows it."""
        st = os.stat(path)
        return _stamp(str(path), st.st_mtime, st.st_size)


def effect_defaults(style: str = "outline") -> Dict[str, Any]:
    """The style option and each outline style's settings. The outline measures the user's
    original outlined logos (a 6 px solid black stroke); the soft look is a 4 px stroke with
    a blurred edge at 60 %."""
    return {
        "style": style,
        "outline_width": 6,
        "outline_color": "#000000",
        "outline_opacity": 100,
        "soft_width": 4,
        "soft_color": "#000000",
        "soft_opacity": 60,
    }


def style_field(section: str, noun: str) -> Dict[str, Any]:
    labels = {"plain": f"Plain {noun}"}
    return {"kind": "select", "key": "style", "label": "Style", "section": section,
            "options": [{"value": v, "label": labels.get(v, l)} for v, l in STYLES],
            "help": f"The plain {noun}, the {noun} with a solid outline, or with a soft, see-through outline."}


def outline_fields(section: str) -> List[Dict[str, Any]]:
    """Width, color and opacity for each outline style, shown only while that style is picked."""
    out: List[Dict[str, Any]] = []
    for style, what in (("outline", "outline"), ("soft", "soft outline")):
        out += [
            {"kind": "number", "key": f"{style}_width", "label": "Outline width", "section": section, "min": 1, "max": 30, "show_when": {"style": style},
             "help": f"How far the {what} reaches out from the image, in px of the image's own size."},
            {"kind": "color", "key": f"{style}_color", "label": "Outline color", "section": section, "show_when": {"style": style},
             "help": f"Color of the {what}."},
            {"kind": "number", "key": f"{style}_opacity", "label": "Outline opacity", "section": section, "min": 1, "max": 100, "show_when": {"style": style},
             "help": f"Opacity of the {what}, in percent."},
        ]
    return out


def normalize_effect(raw: Dict[str, Any], out: Dict[str, Any], defaults: Dict[str, Any]) -> None:
    """Fill `out` with a valid style and outline settings from `raw`."""
    style = str(raw.get("style") or defaults["style"])
    out["style"] = style if style in STYLE_KEYS else defaults["style"]
    for prefix in ("outline", "soft"):
        for key, lo, hi in ((f"{prefix}_width", 1, 30), (f"{prefix}_opacity", 1, 100)):
            try:
                out[key] = max(lo, min(hi, int(raw.get(key, defaults[key]))))
            except (TypeError, ValueError):
                out[key] = defaults[key]
        color = str(raw.get(f"{prefix}_color", defaults[f"{prefix}_color"]) or "").strip()
        out[f"{prefix}_color"] = color.upper() if _COLOR.fullmatch(color) else defaults[f"{prefix}_color"]


def effect(options: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The outline the chosen style draws around the image; None for the plain image."""
    style = str(options.get("style") or "")
    if style not in _SOFTNESS:
        return None
    return {
        "width": int(options.get(f"{style}_width") or 0),
        "color": str(options.get(f"{style}_color") or "#000000"),
        "opacity": int(options.get(f"{style}_opacity") or 0),
        "soft": _SOFTNESS[style],
    }
