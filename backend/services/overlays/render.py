"""Draws a text badge (backdrop + text) onto a poster.

Geometry is expressed in Kometa's 1000x1500 poster space and scaled to the actual image,
so the same placement lands in the same spot on a 2000x3000 poster. The badge is drawn
at 2x and downsampled so rounded corners stay smooth.
"""

from __future__ import annotations

import math
import os
import re
import unicodedata
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from services.text_logo import BUNDLED_FONT_DIR, _resolve_font

from .base import OverlayBadge

REF_WIDTH = 1000
REF_HEIGHT = 1500
_SUPERSAMPLE = 2
_FALLBACK_FONTS = ["RobotoCondensed-Variable", "LiberationSans-Regular", "BebasNeue-Regular"]
# Bundled fonts that stand in, in this order, for a character the badge's own font has no glyph for
_GLYPH_FALLBACKS = ("fallback/DejaVuSans.ttf", "LiberationSans-Regular.ttf")

Rect = Tuple[int, int, int, int]  # x, y, w, h in reference space


def parse_color(value: Optional[str], default: Tuple[int, int, int, int] = (0, 0, 0, 0)) -> Tuple[int, int, int, int]:
    """'#RRGGBB' / '#RRGGBBAA' → RGBA tuple. Anything else → default."""
    raw = str(value or "").strip()
    if not re.fullmatch(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", raw):
        return default
    raw = raw[1:]
    r, g, b = (int(raw[i:i + 2], 16) for i in (0, 2, 4))
    a = int(raw[6:8], 16) if len(raw) == 8 else 255
    return (r, g, b, a)


def resolve_font_path(font_id: Optional[str]) -> str:
    candidates = ([font_id] if font_id else []) + _FALLBACK_FONTS
    path = _resolve_font(candidates)
    if path is None:
        raise RuntimeError("No overlay font found (bundled fonts missing)")
    return str(path)


@lru_cache(maxsize=64)
def _load_font(path: str, size: int, weight: str) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(path, size)
    if weight:
        try:
            names = [n.decode() if isinstance(n, bytes) else str(n) for n in font.get_variation_names()]
            match = next((n for n in names if n.lower() == weight.lower()), None)
            if match:
                font.set_variation_by_name(match)
        except OSError:
            pass  # not a variable font
    return font


def _glyph_print(font: ImageFont.FreeTypeFont, ch: str) -> Tuple[Tuple[int, int], bytes, float]:
    mask = font.getmask(ch)
    return mask.size, bytes(mask), font.getlength(ch)


@lru_cache(maxsize=32)
def _glyph_probe(path: str) -> Tuple[ImageFont.FreeTypeFont, Tuple[Tuple[int, int], bytes, float]]:
    """A small instance of the font, and what it draws for a character it does not have."""
    font = ImageFont.truetype(path, 48)
    return font, _glyph_print(font, "\uFFFE")  # U+FFFE is no character, so no font carries it


@lru_cache(maxsize=4096)
def _has_glyph(path: str, ch: str) -> bool:
    font, missing = _glyph_probe(path)
    return _glyph_print(font, ch) != missing


@lru_cache(maxsize=1)
def _glyph_fallbacks() -> Tuple[str, ...]:
    return tuple(str(BUNDLED_FONT_DIR / name) for name in _GLYPH_FALLBACKS if (BUNDLED_FONT_DIR / name).is_file())


def _font_for(ch: str, font: ImageFont.FreeTypeFont) -> ImageFont.FreeTypeFont:
    """The font that draws `ch`: the badge's own, or a bundled stand-in at the same size when
    that font has no glyph for it (a symbol such as ● would come out as an empty box)."""
    stand_in = _stand_in_path(str(font.path), ch)
    return _load_font(stand_in, int(font.size), "") if stand_in else font


def _stand_in_path(path: str, ch: str) -> Optional[str]:
    """The bundled font that draws `ch` in place of the font at `path`; None when that font
    draws it itself, or when no bundled font has it either."""
    if ch.isspace() or _has_glyph(path, ch):
        return None
    return next((fallback for fallback in _glyph_fallbacks() if fallback != path and _has_glyph(fallback, ch)), None)


def stand_in_mark(badge: OverlayBadge, placement: Dict[str, Any]) -> str:
    """'|glyphs=1' when a stand-in font draws part of the badge's text, '' otherwise. It is part
    of an item's signature, so a poster badged while such a character came out as an empty box
    is badged again."""
    if badge.image or not badge.text:
        return ""
    path = resolve_font_path(_merged_placement(badge, placement).get("font"))
    return "|glyphs=1" if any(_stand_in_path(path, ch) for ch in badge.text) else ""


@lru_cache(maxsize=4096)
def _ink_rows(font: ImageFont.FreeTypeFont, ch: str, stroke: int) -> Optional[Tuple[int, int]]:
    """Top and bottom row of the character's ink, measured from where it is drawn; None = no ink."""
    mask, (_ox, oy) = font.getmask2(ch, stroke_width=stroke)
    box = mask.getbbox()
    return (int(oy + box[1]), int(oy + box[3])) if box else None


def _glyph_shift(ch: str, font: ImageFont.FreeTypeFont, used: ImageFont.FreeTypeFont) -> float:
    """How far down a stand-in font's glyph is drawn so it lines up with the badge's own font:
    a symbol (●, ★, →) is centered on that font's capitals, anything else shares its baseline."""
    if used is font:
        return 0.0
    if unicodedata.category(ch) in ("So", "Sm"):
        caps, ink = _ink_rows(font, "H", 0), _ink_rows(used, ch, 0)
        if caps and ink:
            return (caps[0] + caps[1]) / 2 - (ink[0] + ink[1]) / 2
    return float(font.getmetrics()[0] - used.getmetrics()[0])


def _ink_span(text: str, font: ImageFont.FreeTypeFont, stroke: int) -> Optional[Tuple[float, float]]:
    """Top and bottom of the text's real ink, from where it is drawn. Descenders and stand-in
    symbols reach past the cap-height band the badge is sized by."""
    rows = []
    for ch in text:
        used = _font_for(ch, font)
        ink = _ink_rows(used, ch, stroke)
        if ink:
            shift = _glyph_shift(ch, font, used)
            rows.append((ink[0] + shift, ink[1] + shift))
    return (min(top for top, _ in rows), max(bottom for _, bottom in rows)) if rows else None


def _text_width(font: ImageFont.FreeTypeFont, text: str, spacing: float) -> float:
    if not text:
        return 0.0
    return sum(_font_for(ch, font).getlength(ch) for ch in text) + spacing * (len(text) - 1)


def _draw_spaced(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, font: ImageFont.FreeTypeFont,
                 spacing: float, fill: Tuple[int, int, int, int], stroke_width: int,
                 stroke_fill: Tuple[int, int, int, int]) -> None:
    for ch in text:
        used = _font_for(ch, font)
        draw.text((x, y + _glyph_shift(ch, font, used)), ch, font=used, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)
        x += used.getlength(ch) + spacing


def _has_box(placement: Dict[str, Any]) -> bool:
    """A solid or gradient box behind the badge (the band style draws no box, only the band)."""
    return bool(placement.get("back_enabled", True)) and placement.get("back_style", "solid") != "band"


def _gradient_mask(w: int, h: int, angle_deg: float) -> Image.Image:
    """L mask running 0 → 255 across the box in a CSS-style direction (0° = to top, 90° = to right)."""
    theta = math.radians(angle_deg)
    # Like CSS, the ramp spans the box's extent along the direction (corner to corner at 45°).
    run = max(1, int(round(abs(w * math.sin(theta)) + abs(h * math.cos(theta)))))
    d = max(run, int(math.ceil(math.hypot(w, h))))
    # Build the ramp vertically (black at top → white at bottom = "to bottom", 180°), clamped
    # beyond its ends, then rotate counter-clockwise the rest of the way and crop the box out
    # of the middle. d ≥ the diagonal, so the crop never touches the rotation's empty corners.
    square = Image.new("L", (d, d), 0)
    start_row = (d - run) // 2
    square.paste(255, (0, start_row + run, d, d))
    square.paste(Image.linear_gradient("L").resize((d, run), Image.BILINEAR), (0, start_row))
    square = square.rotate(180 - angle_deg, resample=Image.BILINEAR)
    left, top = (d - w) // 2, (d - h) // 2
    return square.crop((left, top, left + w, top + h))


def _fade_layer(w: int, h: int, color: Tuple[int, int, int, int], angle_deg: float) -> Image.Image:
    """RGBA layer fading `color` to fully transparent across the box along `angle_deg`."""
    mask = _gradient_mask(w, h, angle_deg)
    clear = (color[0], color[1], color[2], 0)
    return Image.composite(Image.new("RGBA", (w, h), clear), Image.new("RGBA", (w, h), color), mask)


def _glow_mask(size: Tuple[int, int], box: Tuple[int, int, int, int], radius: int, reach: int) -> Image.Image:
    """L mask for a solid rounded box that glows out past its edges: a Gaussian falloff that
    is (nearly) gone `reach` px out. Blurring keeps the falloff soft and crease-free."""
    core = Image.new("L", size, 0)
    ImageDraw.Draw(core).rounded_rectangle(box, radius=radius, fill=255)
    if reach <= 0:
        return core
    # Blurring the box itself would leave the glow at 50% right at the box edge (a visible
    # step). Instead blur a box grown by half the reach, with a sigma of a quarter of it: the
    # S-curve then sits ~98% at the box edge and ~2% at the reach, and a small gain lifts the
    # edge to full so the solid box and its glow join without a seam.
    grow, sigma = reach / 2.0, max(0.5, reach / 4.0)
    wide = Image.new("L", size, 0)
    ImageDraw.Draw(wide).rounded_rectangle(
        (box[0] - grow, box[1] - grow, box[2] + grow, box[3] + grow), radius=radius + grow, fill=255)
    at_edge = 0.5 * (1.0 + math.erf((grow / sigma) / math.sqrt(2.0)))  # blurred value at the box edge, 0..1
    lut = [min(255, int(round(v / at_edge))) for v in range(256)]
    return ImageChops.lighter(core, wide.filter(ImageFilter.GaussianBlur(radius=sigma)).point(lut))


# The "soft" band: how much of the fade color is left at 17 evenly spaced steps from the band's
# solid edge to its end. Close to a straight line at first, easing out toward the end.
_SOFT_FADE = (1.0, 0.941, 0.881, 0.817, 0.748, 0.673, 0.599, 0.525, 0.45, 0.376, 0.307, 0.243, 0.178, 0.129, 0.079, 0.04, 0.0)


def _soft_fade(coverage: float) -> float:
    """`coverage` is the straight-line fade (1 at the solid edge, 0 at the end); the soft value for it."""
    pos = (1.0 - max(0.0, min(1.0, coverage))) * (len(_SOFT_FADE) - 1)
    i = min(len(_SOFT_FADE) - 2, int(pos))
    return _SOFT_FADE[i] + (_SOFT_FADE[i + 1] - _SOFT_FADE[i]) * (pos - i)


def _fade_color(merged: Dict[str, Any]) -> Tuple[int, int, int, int]:
    return parse_color(merged.get("gradient_start") or merged["back_color"])


def _paste_band(canvas: Image.Image, merged: Dict[str, Any], scale: float) -> None:
    """The edge band behind the badge: solid `gradient_band_offset` px down from the top poster
    edge, fading to clear over `gradient_band_height` px toward the middle, narrowed by the
    left/right insets."""
    x0 = int(round(merged["gradient_band_left"] * scale))
    x1 = canvas.width - int(round(merged["gradient_band_right"] * scale))
    y0 = int(round(merged["gradient_band_offset"] * scale))
    y1 = min(canvas.height, y0 + max(1, int(round(merged["gradient_band_height"] * scale))))
    if x1 <= x0 or y1 <= y0:
        return
    color = _fade_color(merged)
    band = _fade_layer(x1 - x0, y1 - y0, color, 180)  # solid at the top, clear at the bottom
    if merged.get("gradient_band_curve") == "soft" and color[3] > 0:
        band.putalpha(band.getchannel("A").point([int(round(color[3] * _soft_fade(v / color[3]))) for v in range(256)]))
    canvas.paste(band, (x0, y0), band)


def _badge_geometry(text: str, placement: Dict[str, Any], scale: float, box: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Everything needed to draw: the badge rect (pixels, in the target image), the font and
    the text's ink box so it centers on the backdrop. With `box` (the badge's own box) the
    text sits in that box and the backdrop wraps it as a separate layer ("inner" = its size)."""
    ss = _SUPERSAMPLE
    font_px = max(4, int(round(placement["font_size"] * scale * ss)))
    font_path = resolve_font_path(placement.get("font"))
    weight = str(placement.get("font_weight") or "")
    font = _load_font(font_path, font_px, weight)
    spacing = placement["letter_spacing"] * scale * ss
    stroke = int(round(placement["stroke_width"] * scale * ss))
    text_w = _text_width(font, text, spacing) + 2 * stroke
    # Vertical extent from a fixed cap-height reference, NOT the string's own ink box: a
    # descender (the "/" in a date, a comma, a lowercase g) would otherwise grow the box and
    # push the baseline up, so "AIRING" and "AIRING 09/05" would sit at different heights.
    # Descenders hang below a baseline that is the same for every badge.
    left, top, right, bottom = font.getbbox("H", stroke_width=stroke)
    ink_h = bottom - top
    has_box = _has_box(placement)
    if box:
        pad = box.get("padding", 0) * scale * ss
        max_w = box.get("max_width", 0) * scale * ss
        min_w = box.get("min_width", 0) * scale * ss
    else:
        pad = placement["back_padding"] * scale * ss if has_box else ss  # text only keeps a little air around the ink
        # The backdrop's widths belong to its box: with the backdrop off (or a band) they do nothing.
        max_w = placement.get("back_max_width", 0) * scale * ss if has_box else 0
        min_w = placement.get("back_min_width", 0) * scale * ss if has_box else 0
    room = max_w - 2 * pad - 2 * stroke  # what the text may take inside a box capped at max_w
    if max_w and text_w - 2 * stroke > room > 0:
        # Too wide for the cap: shrink this badge's text (spacing along with it) until it fits.
        # The box keeps the nominal font's height, so a shrunken badge still lines up with the rest.
        factor = room / (text_w - 2 * stroke)
        small_px = max(4, int(font_px * factor))
        spacing *= factor
        while True:
            font = _load_font(font_path, small_px, weight)
            text_w = _text_width(font, text, spacing) + 2 * stroke
            if text_w - 2 * stroke <= room or small_px <= 4:
                break
            small_px -= 1
        _l, small_top, _r, small_bottom = font.getbbox("H", stroke_width=stroke)
        top = small_top - (ink_h - (small_bottom - small_top)) / 2  # center the smaller caps in the nominal cap band
    if box or has_box:
        w = max(text_w + 2 * pad, min_w)
        h = ink_h + 2 * pad
    else:
        # Text only: the badge box hugs the ink (a little air so strokes aren't clipped).
        w, h = text_w + 2 * ss, ink_h + 2 * ss
    inner = None
    if box:
        inner = (int(round(w)), int(round(h)))
        if has_box:  # the backdrop wraps the badge's box with its own padding and width floor
            opad = placement["back_padding"] * scale * ss
            w = max(w + 2 * opad, placement.get("back_min_width", 0) * scale * ss)
            h = h + 2 * opad
    return {
        "font": font, "spacing": spacing, "stroke": stroke, "text_w": text_w,
        "ink_top": top, "ink_h": ink_h, "w": int(round(w)), "h": int(round(h)), "ss": ss, "inner": inner,
    }


@lru_cache(maxsize=128)
def _load_image(path: str, _mtime: float) -> Image.Image:
    with Image.open(path) as img:
        return img.convert("RGBA")


_EFFECT_SS = 4  # outlines are built at 4x the image so their edges stay smooth after scaling


@lru_cache(maxsize=128)
def _outlined_image(path: str, _mtime: float, width: float, color: str, opacity: int, soft: float) -> Image.Image:
    """The image with an outline around its shape, at _EFFECT_SS x its size: the alpha is grown
    by `width` px (a blur crosses 7% about 1.48 sigma out, so that sigma grows it by `width`),
    the edge blurred by `soft` px, filled with `color` at `opacity` %, and the image laid on top."""
    img = _load_image(path, _mtime)
    ss = _EFFECT_SS
    big = img.resize((img.width * ss, img.height * ss), Image.LANCZOS)
    grown = big.getchannel("A").filter(ImageFilter.GaussianBlur(width * ss / 1.48)).point(lambda v: 255 if v > 18 else 0)
    if soft > 0:
        grown = grown.filter(ImageFilter.GaussianBlur(soft * ss))
    rgb = parse_color(color, (0, 0, 0, 255))[:3]
    halo = Image.new("RGBA", big.size, rgb + (0,))
    halo.putalpha(grown.point(lambda v: int(v * max(0, min(100, opacity)) / 100)))
    halo.alpha_composite(big)
    return halo


@lru_cache(maxsize=128)
def _joined_images(parts: Tuple[Tuple[str, float, int], ...], gap: int) -> Image.Image:
    """Several images as one line: side by side, `gap` apart, each centered on the line. A part
    is (path, mtime, the height it is drawn at); the line is built at the first image's own
    resolution and the others are scaled to their height next to it."""
    images = [_load_image(path, mtime) for path, mtime, _h in parts]
    unit = images[0].height / parts[0][2] if parts[0][2] else 1.0  # image px per drawn px
    sized = []
    for im, (_path, _mtime, drawn) in zip(images, parts):
        h = max(1, round(drawn * unit)) if drawn else im.height
        if abs(h - im.height) > 1:  # a pixel off is rounding: the image stays as it is
            im = im.convert("RGBa").resize((max(1, round(im.width * h / im.height)), h), Image.LANCZOS).convert("RGBA")
        sized.append(im)
    space = int(round(gap * unit))
    height = max(im.height for im in sized)
    line = Image.new("RGBA", (sum(im.width for im in sized) + space * (len(sized) - 1), height), (0, 0, 0, 0))
    x = 0
    for im in sized:
        line.paste(im, (x, (height - im.height) // 2))  # a plain paste keeps the edges as they are
        x += im.width + space
    return line


def image_line(parts: List[Tuple[str, int]], gap: int) -> Image.Image:
    """The line `parts` [(path, drawn height)] make, `gap` apart, at the first image's own resolution."""
    return _joined_images(tuple((path, os.stat(path).st_mtime, int(height)) for path, height in parts), int(gap))


def _image_geometry(badge: OverlayBadge, placement: Dict[str, Any], scale: float) -> Dict[str, Any]:
    """Like _badge_geometry, for an image badge: the logo (outlined when asked) at the layer's
    supersampled size, and the box around it (padding, minimum width; a maximum width scales
    the logo down for that badge only)."""
    ss = _SUPERSAMPLE
    mtime = os.stat(badge.image).st_mtime
    line_h = badge.image_height
    if badge.image_tail:  # a line of several images (no outline effect)
        parts = ((badge.image, badge.image_height), *badge.image_tail)
        native = source = image_line(list(parts), badge.image_gap)
        line_h = max(int(height) for _path, height in parts)
    else:
        native = _load_image(badge.image, mtime)
        effect = badge.image_effect or {}
        width, opacity = float(effect.get("width") or 0), int(effect.get("opacity") or 0)
        source = (_outlined_image(badge.image, mtime, width, str(effect.get("color") or "#000000"), opacity, float(effect.get("soft") or 0))
                  if width > 0 and opacity > 0 else native)
    # Sized by height (the width follows the image's shape), else by width, else its own size.
    ref_w = native.width * line_h / native.height if line_h else (badge.image_width or native.width)
    logo_w = max(1, int(round(ref_w * scale * ss)))
    has_box = _has_box(placement)
    pad = placement["back_padding"] * scale * ss if has_box else 0
    max_w = placement.get("back_max_width", 0) * scale * ss if has_box else 0  # the backdrop's cap: only with its box
    if max_w and logo_w + 2 * pad > max_w and max_w - 2 * pad > 0:
        logo_w = max(1, int(round(max_w - 2 * pad)))  # too wide for the cap: this logo shrinks
    logo_h = max(1, int(round(native.height * (logo_w / native.width))))
    logo = source if source.size == (logo_w, logo_h) else source.resize((logo_w, logo_h), Image.LANCZOS)
    w = max(logo_w + 2 * pad, placement.get("back_min_width", 0) * scale * ss) if has_box else logo_w
    h = logo_h + 2 * pad
    return {"logo": logo, "logo_w": logo_w, "logo_h": logo_h, "w": int(round(w)), "h": int(round(h)), "ss": ss}


def _paste_clipped(canvas: Image.Image, layer: Image.Image, lx: int, ly: int) -> None:
    """Alpha-paste `layer` with its top-left at (lx, ly), clipped to the canvas (a badge
    dragged past an edge is partially visible, never an error)."""
    src_left, src_top = max(0, -lx), max(0, -ly)
    dst_left, dst_top = max(0, lx), max(0, ly)
    vis_w = min(layer.width - src_left, canvas.width - dst_left)
    vis_h = min(layer.height - src_top, canvas.height - dst_top)
    if vis_w > 0 and vis_h > 0:
        visible = layer.crop((src_left, src_top, src_left + vis_w, src_top + vis_h))
        canvas.paste(visible, (dst_left, dst_top), visible)


def _anchor(align: str, offset: float, size: float, extent: float) -> float:
    if align in ("left", "top"):
        return offset
    if align in ("right", "bottom"):
        return extent - size - offset
    return (extent - size) / 2 + offset


def measure_badge(badge: OverlayBadge, placement: Dict[str, Any]) -> Rect:
    """The badge rect in 1000x1500 reference space (what the page's drag handle covers)."""
    merged = {**placement, **(badge.placement_overrides or {})}
    geo = _image_geometry(badge, merged, 1.0) if badge.image else _badge_geometry(badge.text, merged, 1.0, badge.box)
    w = geo["w"] / geo["ss"]
    h = geo["h"] / geo["ss"]
    x = _anchor(merged["horizontal_align"], merged["horizontal_offset"], w, REF_WIDTH)
    y = _anchor(merged["vertical_align"], merged["vertical_offset"], h, REF_HEIGHT)
    return (int(round(x)), int(round(y)), int(round(w)), int(round(h)))


def _merged_placement(badge: OverlayBadge, placement: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(placement)
    merged.update(badge.placement_overrides or {})
    return merged


def _wants_band(merged: Dict[str, Any]) -> bool:
    return bool(merged.get("back_enabled", True)) and merged.get("back_style") == "band"


def render_badges(image: Image.Image, layers: List[Tuple[OverlayBadge, Dict[str, Any]]]) -> Tuple[Image.Image, List[Rect]]:
    """Composite several badges (one per overlay type, in order) onto a copy of `image`.
    The edge band is painted once for the whole poster, even when more than one overlay asks
    for it: the most opaque one wins (the taller one on a tie, then the first), so two banded
    overlays do not stack into a darker top. Returns (image, one rect per badge)."""
    merged = [_merged_placement(badge, placement) for badge, placement in layers]
    bands = [m for m in merged if _wants_band(m)]
    canvas = image.convert("RGB")
    if bands:
        scale = canvas.width / REF_WIDTH
        strongest = max(bands, key=lambda m: (_fade_color(m)[3], m.get("gradient_band_height", 0), -bands.index(m)))
        _paste_band(canvas, strongest, scale)
    rects: List[Rect] = []
    for (badge, _placement), m in zip(layers, merged):
        canvas, rect = render_badge(canvas, badge, m, band=False)
        rects.append(rect)
    return canvas, rects


def _ink_overflow(badge: OverlayBadge, geo: Dict[str, Any]) -> Tuple[int, int]:
    """Rows the text's ink reaches above and below the badge's own height (a "g" or a symbol in
    a badge without a backdrop), in whole output pixels: the layer grows by that much on that
    side so none is cut off."""
    if badge.image or not badge.text:
        return 0, 0
    span = _ink_span(badge.text, geo["font"], geo["stroke"])
    if span is None:
        return 0, 0
    ss = geo["ss"]
    room = (geo["h"] - geo["ink_h"]) / 2  # what the badge leaves above and below its capitals
    above = geo["ink_top"] - span[0] - room
    below = span[1] - (geo["ink_top"] + geo["ink_h"]) - room
    return (ss * math.ceil(above / ss) if above > 0 else 0, ss * math.ceil(below / ss) if below > 0 else 0)


def render_badge(image: Image.Image, badge: OverlayBadge, placement: Dict[str, Any], band: bool = True) -> Tuple[Image.Image, Rect]:
    """Composite one badge onto a copy of `image`. Returns (image, rect in reference space).
    `band=False` leaves the edge band to the caller (render_badges paints it once per poster)."""
    merged = _merged_placement(badge, placement)
    if badge.back_color:
        merged["back_color"] = badge.back_color
    if badge.font_color:
        merged["font_color"] = badge.font_color

    scale = image.width / REF_WIDTH
    # An image badge (a logo) takes the same backdrop as text: a box, a fading box or the band.
    geo = _image_geometry(badge, merged, scale) if badge.image else _badge_geometry(badge.text, merged, scale, badge.box)
    ss = geo["ss"]
    w, h = geo["w"], geo["h"]
    if w <= 0 or h <= 0:
        return image, (0, 0, 0, 0)

    has_box = _has_box(merged)
    box_gradient = has_box and merged.get("back_style") == "gradient"
    glow = box_gradient and merged.get("gradient_mode") == "center"
    # A glowing box needs room around it: the layer grows by the glow's reach on every side,
    # while the reported rect (and the anchor) stay the box itself.
    m = int(round(merged.get("gradient_spread", 0) * scale * ss)) if glow else 0
    # Ink past the badge's height gets rows of its own on that side, for the same reason
    over_top, over_bottom = _ink_overflow(badge, geo)
    mt, mb = max(m, over_top), max(m, over_bottom)
    layer = Image.new("RGBA", (w + 2 * m, h + mt + mb), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    box = (m, mt, m + w - 1, mt + h - 1)
    radius = min(int(round(merged["back_radius"] * scale * ss)), min(w, h) // 2)
    line_w = int(round(merged["back_line_width"] * scale * ss))
    back = parse_color(merged["back_color"])
    line = parse_color(merged["back_line_color"])
    if box_gradient:
        # The backdrop box takes the fade instead of its flat color (outline drawn after).
        color = _fade_color(merged)
        if glow:
            mask = _glow_mask(layer.size, box, radius, m)
            fill = Image.new("RGBA", layer.size, color)
        else:
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
            fill = _fade_layer(w, h, color, merged["gradient_angle"])
        fill.putalpha(ImageChops.multiply(fill.getchannel("A"), mask))
        layer.alpha_composite(fill, (0, 0) if glow else (m, mt))
    if has_box and ((back[3] > 0 and not box_gradient) or (line_w > 0 and line[3] > 0)):
        draw.rounded_rectangle(
            box, radius=radius,
            fill=back if back[3] > 0 and not box_gradient else None,
            outline=line if line_w > 0 and line[3] > 0 else None,
            width=line_w if line_w > 0 else 0,
        )
    if badge.box and geo.get("inner"):
        # The badge's own box, centered inside the backdrop (or alone when the backdrop is off).
        iw, ih = geo["inner"]
        ix, iy = m + (w - iw) // 2, mt + (h - ih) // 2
        ifill = parse_color(badge.box.get("fill"))
        iline = parse_color(badge.box.get("line_color"))
        iline_w = int(round(float(badge.box.get("line_width") or 0) * scale * ss))
        iradius = min(int(round(float(badge.box.get("radius") or 0) * scale * ss)), min(iw, ih) // 2)
        if ifill[3] > 0 or (iline_w > 0 and iline[3] > 0):
            draw.rounded_rectangle(
                (ix, iy, ix + iw - 1, iy + ih - 1), radius=iradius,
                fill=ifill if ifill[3] > 0 else None,
                outline=iline if iline_w > 0 and iline[3] > 0 else None,
                width=iline_w if iline_w > 0 else 0,
            )
    if badge.image:
        layer.alpha_composite(geo["logo"], (m + (w - geo["logo_w"]) // 2, mt + (h - geo["logo_h"]) // 2))
    elif badge.text:
        tx = m + (w - geo["text_w"]) / 2 + geo["stroke"]
        ty = mt + (h - geo["ink_h"]) / 2 - geo["ink_top"]
        _draw_spaced(
            draw, tx, ty, badge.text, geo["font"], geo["spacing"],
            parse_color(merged["font_color"], (255, 255, 255, 255)),
            geo["stroke"], parse_color(merged["stroke_color"], (0, 0, 0, 255)),
        )
    out_w, out_h, out_m, out_mt, out_mb = max(1, w // ss), max(1, h // ss), m // ss, mt // ss, mb // ss
    layer = layer.resize((out_w + 2 * out_m, out_h + out_mt + out_mb), Image.LANCZOS)

    x = _anchor(merged["horizontal_align"], merged["horizontal_offset"] * scale, out_w, image.width)
    y = _anchor(merged["vertical_align"], merged["vertical_offset"] * scale, out_h, image.height)
    x, y = int(round(x)), int(round(y))

    canvas = image.convert("RGB")
    if band and _wants_band(merged):
        _paste_band(canvas, merged, scale)
    _paste_clipped(canvas, layer, x - out_m, y - out_mt)  # where the (possibly glow-padded) layer lands
    rect = (int(round(x / scale)), int(round(y / scale)), int(round(out_w / scale)), int(round(out_h / scale)))
    return canvas, rect
