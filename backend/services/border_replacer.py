"""
Border Replacer Service

Applies or removes borders from poster images.
Adapted from DAPS border_replacerr.py - image processing logic preserved exactly.
"""

import filecmp
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from core.config import settings as app_settings

from core.logging import (
    logger,
    LogTags,
    log_success,
    log_error,
    log_warning,
    log_info,
    log_debug,
    log_section_start,
    log_section_end,
)
from models.setting import get_setting, upsert_setting

try:
    from PIL import Image, ImageDraw, UnidentifiedImageError
except ImportError as e:
    raise ImportError(
        f"PIL/Pillow not installed: {e}\n"
        "Install with: pip install Pillow"
    )


def _drop_file_cache(path: str) -> None:
    """Advise the kernel to evict a file's pages from the page cache."""
    try:
        with open(path, "rb") as f:
            os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
    except (AttributeError, OSError, Exception):
        pass


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """Convert a hex color string to an RGB tuple, defaulting to white on error."""
    hex_color = (hex_color or "").strip().strip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    try:
        return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (255, 255, 255)


def _build_gradient_band(
    size: Tuple[int, int],
    colors: Optional[List[str]],
    direction: Optional[str],
) -> "Image.Image":
    """Build an RGB gradient image of `size` blending evenly-spaced color stops."""
    width, height = size
    stops = [_hex_to_rgb(c) for c in (colors or []) if str(c).strip()]
    if not stops:
        stops = [(255, 255, 255)]
    if len(stops) == 1:
        stops = stops * 2

    def _strip(length: int) -> "Image.Image":
        length = max(2, length)
        strip = Image.new("RGB", (length, 1))
        pixels = strip.load()
        segments = len(stops) - 1
        seg_len = length / segments
        for x in range(length):
            idx = min(int(x / seg_len), segments - 1)
            t = (x - idx * seg_len) / seg_len
            start, end = stops[idx], stops[idx + 1]
            pixels[x, 0] = tuple(
                int(round(start[c] + (end[c] - start[c]) * t)) for c in range(3)
            )
        return strip

    # rotate(-90) (clockwise) puts stops[0] at the TOP, matching horizontal's stops[0]-at-
    # left convention and the top-to-bottom order of the color list in the UI. rotate(90)
    # would invert it (first color at the bottom).
    direction = (direction or "vertical").lower()
    if direction == "horizontal":
        return _strip(width).resize((width, height))
    if direction == "diagonal":
        horizontal = _strip(width).resize((width, height))
        vertical = _strip(height).rotate(-90, expand=True).resize((width, height))
        return Image.blend(horizontal, vertical, 0.5)
    # vertical (default)
    return _strip(height).rotate(-90, expand=True).resize((width, height))


def _apply_inner_effect(
    canvas: "Image.Image",
    border_width: int,
    style_opts: Dict[str, Any],
) -> "Image.Image":
    """Composite a dark inner glow or a border-color fade just inside the border edge."""
    effect = (style_opts.get("inner_effect") or "none").lower()
    if effect not in ("glow", "fade"):
        return canvas

    width_px, height_px = canvas.size

    if effect == "glow":
        color = _hex_to_rgb(style_opts.get("inner_color") or "#000000")
        try:
            opacity = float(style_opts.get("inner_opacity", 70))
        except (TypeError, ValueError):
            opacity = 70.0
        max_alpha = int(max(0.0, min(100.0, opacity)) / 100.0 * 255)
        try:
            span = int(style_opts.get("inner_width", 8))
        except (TypeError, ValueError):
            span = 8
    else:  # fade — bleed the border's actual edge color inward. Sampling the border
           # (instead of using border_color) keeps this correct for gradient/image
           # borders, whose band is not a single color and whose border_color is a
           # placeholder; for a solid border the sampled pixel equals border_color.
        max_alpha = 255
        try:
            span = int(style_opts.get("fade_width", 8))
        except (TypeError, ValueError):
            span = 8
        sample_y = max(0, min(border_width - 1, height_px - 1))
        sample_x = min(max(0, width_px // 2), width_px - 1)
        color = canvas.convert("RGB").getpixel((sample_x, sample_y))

    # Clamp the span so the gradient never overruns the art area.
    max_span = max(0, (min(width_px, height_px) - 2 * border_width) // 2)
    span = max(0, min(span, max_span))
    if span == 0 or max_alpha <= 0:
        return canvas

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for i in range(span):
        alpha = int(round(max_alpha * (1 - i / span)))
        if alpha <= 0:
            continue
        left = border_width + i
        top = border_width + i
        right = width_px - border_width - i - 1
        bottom = height_px - border_width - i - 1
        if right <= left or bottom <= top:
            break
        draw.rectangle(
            (left, top, right, bottom),
            outline=(color[0], color[1], color[2], alpha),
            width=1,
        )

    return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")


def _strip_existing_border(image: "Image.Image", border_width: int, exclude: bool = False) -> "Image.Image":
    """Strip a poster's existing border exactly like the dedicated Remove Borders mode.

    - exclude=False: remove the top/left/right borders and replace the bottom border
      with a fresh black bar (the title-area convention).
    - exclude=True: remove all four borders.

    Returns a new (un-resized) image. This is the single source of truth for border
    removal — `remove_borders` and the image-overlay "remove existing border first"
    option both call it so they behave identically.
    """
    width, height = image.size
    if not exclude:
        stripped = image.crop((border_width, border_width, width - border_width, height))
        bottom_border = Image.new("RGB", (width - 2 * border_width, border_width), color="black")
        stripped.paste(bottom_border, (0, height - border_width - border_width))
        return stripped
    return image.crop((border_width, border_width, width - border_width, height - border_width))


# For "remove existing border first": how many pixels PAST the new border to trim, so
# the preset poster's baked-in edge glow (which survives the normal border-width crop) is
# tucked behind the new border/gradient/frame instead of stripping the whole poster like
# Remove Borders mode. Tunable per-collection via style_opts["glow_trim"].
_GLOW_TRIM_DEFAULT_PX = 16

# The poster's baked-in border thickness (DAPS default). "Remove existing border first" has to
# cover this whole border: the new band covers `border_width` of it, so when the band is
# narrower the trim/black-bar must make up the rest, up to this width. See _remove_existing_base.
_REMOVE_EXISTING_REF_WIDTH = 26


def _remove_existing_base(source_image: "Image.Image", style_opts: Dict[str, Any], border_width: int) -> "Image.Image":
    """Prepare the poster for 'remove existing border first' using the DAPS Remove Borders
    geometry — crop the edge (the baked-in glow that survives the normal crop) off
    left/top/right, keep the black bottom bar, and resize UP to 1000x1500 (never scaled
    down). The caller then lays its border (solid/gradient band or image frame) over the
    outer edge, so the poster stays full size and only the trimmed edge tucks behind it.

    Trim vs border width: the new band covers `border_width` of the poster's ~reference-px
    baked-in border. At/above the reference the band covers the whole border, so we trim only
    the glow. Below it the band leaves part of the border uncovered, so the trim (black bottom
    bar + side crop) is extended to the FULL reference width to cover the rest — otherwise a
    thin line of the old border leaks through just inside the new border. That extra trim may
    exceed the border width (growing the black bottom bar to cover the leftover edge)."""
    try:
        base_trim = max(0, int(style_opts.get("glow_trim", _GLOW_TRIM_DEFAULT_PX)))
    except (TypeError, ValueError):
        base_trim = _GLOW_TRIM_DEFAULT_PX
    width, height = source_image.size
    base_trim = min(base_trim, max(0, border_width - 1))  # glow stays tucked behind the band
    # A band narrower than the baked-in border can't hide all of it, so extend the trim to the
    # full reference width; the band covers the rest.
    if border_width < _REMOVE_EXISTING_REF_WIDTH:
        side_trim = max(base_trim, _REMOVE_EXISTING_REF_WIDTH)
    else:
        side_trim = base_trim
    side_trim = min(side_trim, min(width, height) // 2 - 1)  # geometric safety
    if side_trim <= 0:
        return source_image.resize((1000, 1500))
    return _strip_existing_border(source_image, side_trim, exclude=False).resize((1000, 1500))


def _render_bordered_image(
    source_image: "Image.Image",
    border_width: int,
    border_color: Tuple[int, int, int],
    style_opts: Optional[Dict[str, Any]],
) -> "Image.Image":
    """
    Build the final 1000x1500 bordered poster from a source poster image.

    style_opts keys (all optional, defaults preserve flat solid-color behavior):
        style: "solid" | "gradient" | "image"
        gradient_colors: list of hex colors, gradient_direction: vertical|horizontal|diagonal
        overlay_path: absolute path to a 1000x1500 transparent-center PNG frame
        inner_effect: "none" | "glow" | "fade"
        inner_color, inner_opacity (0-100), inner_width (px), fade_width (px)

    Solid/gradient styles follow DAPS: crop `border_width` off all sides then add a
    new band (the existing border is replaced). The image-overlay style does NOT crop
    by default — the premade frame simply sits on top of the full poster.

    If `remove_existing` is set (all styles), the poster is trimmed by border_width +
    `glow_trim` px and stretched to fill, so the preset poster's baked-in edge glow is
    tucked behind the new border/gradient/frame (see `_trim_for_remove_existing`). An
    inner-edge effect can optionally be applied on top.
    """
    style_opts = style_opts or {}
    style = (style_opts.get("style") or "solid").lower()

    # "Remove borders" style (used by Plex rules): strip the existing border exactly
    # like Remove Borders mode (top/left/right crop + black title bar) and resize. No
    # new band or inner effect is applied.
    if style == "remove":
        return _strip_existing_border(source_image, border_width, exclude=False).resize((1000, 1500)).convert("RGB")

    # Image-overlay style: composite a frame PNG over the poster. The frame's opaque
    # edge covers the existing border; optionally strip the existing border first.
    if style == "image":
        overlay_path = style_opts.get("overlay_path")
        if overlay_path and os.path.isfile(overlay_path):
            try:
                base_src = source_image
                if style_opts.get("remove_existing"):
                    # DAPS-strip a few px off the edge (glow_trim) so the baked-in glow
                    # tucks behind the frame; keeps the full-size poster (resized up).
                    base_src = _remove_existing_base(source_image, style_opts, border_width)
                base = base_src.convert("RGBA").resize((1000, 1500))
                with Image.open(overlay_path) as frame:
                    frame_rgba = frame.convert("RGBA").resize((1000, 1500))
                composited = Image.alpha_composite(base, frame_rgba).convert("RGB")
                # Optional inner glow / border-color fade on top of the frame.
                return _apply_inner_effect(composited, border_width, style_opts)
            except Exception:
                # Fall through to a solid border on any overlay failure.
                pass
        style = "solid"

    gradient_colors = [c for c in (style_opts.get("gradient_colors") or []) if str(c).strip()]

    if style_opts.get("remove_existing"):
        # "Remove existing border first": DAPS-strip a few px off the edge (glow_trim),
        # resized UP to a full 1000x1500 poster (never scaled down), then lay the new band
        # OVER the outer border_width — so the poster stays full-size and only the glow
        # tucks behind the band (same idea as the image-overlay frame covering the edge).
        base = _remove_existing_base(source_image, style_opts, border_width).convert("RGB")
        if style == "gradient" and gradient_colors:
            band = _build_gradient_band((1000, 1500), gradient_colors, style_opts.get("gradient_direction")).convert("RGB")
        else:
            band = Image.new("RGB", (1000, 1500), border_color)
        # Opaque border ring, transparent center → band on the edge, poster in the middle.
        mask = Image.new("L", (1000, 1500), 255)
        mask.paste(Image.new("L", (1000 - 2 * border_width, 1500 - 2 * border_width), 0), (border_width, border_width))
        canvas = Image.composite(band, base, mask)
        return _apply_inner_effect(canvas, border_width, style_opts).convert("RGB")

    # Solid/gradient (default): crop the border width off all sides then add a new band.
    src_width, src_height = source_image.size
    cropped_image = source_image.crop(
        (border_width, border_width, src_width - border_width, src_height - border_width)
    )

    new_width = cropped_image.width + 2 * border_width
    new_height = cropped_image.height + 2 * border_width

    if style == "gradient" and gradient_colors:
        canvas = _build_gradient_band(
            (new_width, new_height),
            gradient_colors,
            style_opts.get("gradient_direction"),
        ).convert("RGB")
    else:
        canvas = Image.new("RGB", (new_width, new_height), border_color)

    canvas.paste(cropped_image, (border_width, border_width))
    canvas = _apply_inner_effect(canvas, border_width, style_opts)

    return canvas.resize((1000, 1500)).convert("RGB")


# Border-overlay frame images: bundled presets ship with the app (read-only);
# user uploads live in the writable config dir. User overlays win on name clash.
BUNDLED_OVERLAY_DIR = Path(__file__).resolve().parent.parent / "assets" / "border_overlays"
USER_OVERLAY_DIR = app_settings.config_dir / "border_overlays"


def resolve_overlay_path(name: Optional[str]) -> Optional[str]:
    """Resolve a border-overlay filename to an absolute path (user dir wins over bundled)."""
    if not name:
        return None
    safe = os.path.basename(str(name))  # guard against path traversal
    for base in (USER_OVERLAY_DIR, BUNDLED_OVERLAY_DIR):
        candidate = os.path.join(str(base), safe)
        if os.path.isfile(candidate):
            return candidate
    return None


def build_style_opts(db: Session, prefix: str = "") -> Dict[str, Any]:
    """Read border style + inner-edge effect options from settings into a style_opts dict.

    `prefix` selects the setting namespace: "" for main posters
    (border_replacer_*), "season_" for season posters (border_replacer_season_*).
    """
    from models.setting import get_setting_value

    def k(name: str) -> str:
        return f"border_replacer_{prefix}{name}"

    style = (get_setting_value(db, k("style"), "solid") or "solid").strip().lower()
    # "remove" strips the border like Remove Borders mode, but as a STYLE — so an active
    # holiday still overrides it (unlike the global remove_borders toggle).
    if style not in ("solid", "gradient", "image", "remove"):
        style = "solid"

    gradient_colors: List[str] = []
    raw_colors = get_setting_value(db, k("gradient_colors"))
    if raw_colors:
        try:
            parsed = json.loads(raw_colors)
            if isinstance(parsed, list):
                gradient_colors = [str(c) for c in parsed if str(c).strip()]
        except json.JSONDecodeError:
            pass

    direction = (get_setting_value(db, k("gradient_direction"), "vertical") or "vertical").strip().lower()
    if direction not in ("vertical", "horizontal", "diagonal"):
        direction = "vertical"

    inner_effect = (get_setting_value(db, k("inner_effect"), "none") or "none").strip().lower()
    if inner_effect not in ("none", "glow", "fade"):
        inner_effect = "none"

    def _as_int(name: str, default: int) -> int:
        val = get_setting_value(db, k(name))
        try:
            return int(val) if val not in (None, "") else default
        except (TypeError, ValueError):
            return default

    overlay_path = None
    if style == "image":
        overlay_path = resolve_overlay_path(get_setting_value(db, k("overlay_image")))

    remove_existing = str(get_setting_value(db, k("overlay_remove_existing"), "false")).lower() == "true"

    return {
        "style": style,
        "gradient_colors": gradient_colors,
        "gradient_direction": direction,
        "overlay_path": overlay_path,
        "remove_existing": remove_existing,
        "inner_effect": inner_effect,
        "inner_color": get_setting_value(db, k("inner_color"), "#000000") or "#000000",
        "inner_opacity": _as_int("inner_opacity", 70),
        "inner_width": _as_int("inner_width", 8),
        "fade_width": _as_int("fade_width", 8),
    }


def build_holiday_style_opts(raw_style: Optional[Dict[str, Any]], border_image: Optional[str]) -> Dict[str, Any]:
    """Build a style_opts dict for a holiday from its stored `style` object, falling
    back to the legacy `border_image` field for backward compatibility."""
    raw = raw_style if isinstance(raw_style, dict) else {}

    style = str(raw.get("style") or ("image" if border_image else "solid")).strip().lower()
    # "remove" is valid for Plex rules (strip the border on matched items); holidays
    # never emit it, so parsing it here is harmless for them.
    if style not in ("solid", "gradient", "image", "remove"):
        style = "solid"

    gradient_colors = [str(c) for c in (raw.get("gradient_colors") or []) if str(c).strip()]
    direction = str(raw.get("gradient_direction") or "vertical").strip().lower()
    if direction not in ("vertical", "horizontal", "diagonal"):
        direction = "vertical"
    inner_effect = str(raw.get("inner_effect") or "none").strip().lower()
    if inner_effect not in ("none", "glow", "fade"):
        inner_effect = "none"

    def _as_int(key: str, default: int) -> int:
        val = raw.get(key)
        try:
            return int(val) if val not in (None, "") else default
        except (TypeError, ValueError):
            return default

    overlay_path = resolve_overlay_path(raw.get("overlay_image") or border_image) if style == "image" else None

    return {
        "style": style,
        "gradient_colors": gradient_colors,
        "gradient_direction": direction,
        "overlay_path": overlay_path,
        "remove_existing": str(raw.get("remove_existing", "false")).strip().lower() in ("true", "1"),
        "inner_effect": inner_effect,
        "inner_color": str(raw.get("inner_color") or "#000000"),
        "inner_opacity": _as_int("inner_opacity", 70),
        "inner_width": _as_int("inner_width", 8),
        "fade_width": _as_int("fade_width", 8),
    }


def build_border_run_settings(db: Session, run_type: str = "manual") -> Dict[str, Any]:
    """Read every border-replacer setting into process_posters() keyword arguments.

    This is the SINGLE source of truth so every caller — the standalone/workflow job,
    the Plex-webhook pre-upload pass, and the post-rename auto-run — behaves identically.
    Returns everything except source_dir/destination_dir/dry_run/mode/progress_callback
    (those are caller-specific). `run_type` gates the Plex label/genre/collection rules
    (one of "workflow" | "webhook" | "manual" | "autorun" | "scheduled").
    """
    from models.setting import get_setting_value
    from services.plex_border_rules import build_plex_matcher

    def _json_list(key: str) -> List[str]:
        raw = get_setting_value(db, key)
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []

    border_colors = _json_list("border_replacer_colors")
    exclusions = _json_list("border_replacer_exclusions")

    try:
        border_width = int(get_setting_value(db, "border_replacer_width") or 26)
    except (TypeError, ValueError):
        border_width = 26
    # Safety net (matches the UI's 1..200 cap): a border wider than half a 1000x1500 poster
    # would invert the PIL crop box and fail the whole run. The frontend clamps typed input,
    # but stale settings or direct API writes could still slip a bad value through.
    border_width = max(1, min(border_width, 400))

    remove_borders = str(get_setting_value(db, "border_replacer_remove_borders", "false")).strip().lower() == "true"

    # The top "Border Width" is the REMOVAL width (how much to strip). When adding a border
    # instead, the Border Style box has its own width. It falls back to the removal width for
    # configs saved before this split, so existing setups are unchanged. Which one applies is
    # chosen here by the Remove Borders toggle, so process_posters still takes a single width.
    band_width_raw = get_setting_value(db, "border_replacer_band_width")
    try:
        band_width = int(band_width_raw) if band_width_raw not in (None, "") else border_width
    except (TypeError, ValueError):
        band_width = border_width
    band_width = max(1, min(band_width, 400))
    effective_border_width = border_width if remove_borders else band_width

    season_mode = get_setting_value(db, "border_replacer_season_mode", "inherit")
    if season_mode not in ("inherit", "remove", "colors", "custom"):
        season_mode = "inherit"

    season_colors = _json_list("border_replacer_season_colors")

    season_width_raw = get_setting_value(db, "border_replacer_season_width")
    try:
        season_width = int(season_width_raw) if season_width_raw else None
    except (TypeError, ValueError):
        season_width = None
    if season_width is not None:
        season_width = max(1, min(season_width, 400))

    return {
        "border_colors": border_colors if border_colors else None,
        "remove_borders": remove_borders,
        "border_width": effective_border_width,
        "exclusion_list": exclusions,
        "season_mode": season_mode,
        "season_border_colors": season_colors if season_colors else None,
        "season_border_width": season_width,
        "style_opts": build_style_opts(db),
        "season_style_opts": build_style_opts(db, prefix="season_") if season_mode == "custom" else None,
        "plex_matcher": build_plex_matcher(db, run_type),
    }


class BorderReplacerService:
    """Service for applying or removing borders from poster images."""

    ProgressCallback = Callable[[str, int, int, str], None]

    def __init__(self, db: Session) -> None:
        self.db = db
        self.logger = logger

    @staticmethod
    def _is_season_file(filename: str) -> bool:
        """Return True if the filename represents a season poster (e.g. Season01.jpg)."""
        import re as _re
        name = os.path.splitext(filename)[0]
        return bool(_re.match(r"^Season\d+$", name, _re.IGNORECASE))

    @staticmethod
    def _style_opts_for_hash(style_opts: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Normalize style options into a stable, hashable subset (overlay by name)."""
        style = style_opts or {}
        return {
            "style": style.get("style") or "solid",
            "gradient_colors": list(style.get("gradient_colors") or []),  # order matters
            "gradient_direction": style.get("gradient_direction"),
            "inner_effect": style.get("inner_effect") or "none",
            "inner_color": style.get("inner_color"),
            "inner_opacity": style.get("inner_opacity"),
            "inner_width": style.get("inner_width"),
            "fade_width": style.get("fade_width"),
            "overlay": os.path.basename(style.get("overlay_path") or "") or None,
            "remove_existing": bool(style.get("remove_existing")),
        }

    def _rule_signature_from_match(self, match: Optional[Any]) -> Optional[str]:
        """Per-poster fingerprint of the Plex rule applied to it (from a resolved RuleMatch).

        None when no rule applies — the item gets its normal border, which the global
        settings hash already covers. Incremental mode stores this per poster and reprocesses
        ONLY the items whose fingerprint changed (e.g. Kometa added/removed a label so the
        item now matches a different rule, or the rule's colors/style were edited), instead of
        resetting and reprocessing every poster whenever Plex metadata shifts."""
        if match is None:
            return None
        if match.skip:
            return "skip"
        payload = {
            "colors": list(match.colors),
            "style": self._style_opts_for_hash(match.style_opts),
        }
        digest = hashlib.md5(json.dumps(payload, sort_keys=True).encode(), usedforsecurity=False).hexdigest()
        return f"r:{digest}"

    def _rule_signature(self, plex_matcher: Optional[Any], folder: Optional[str]) -> Optional[str]:
        """Resolve `folder` against the matcher and return its per-poster rule fingerprint."""
        if plex_matcher is None:
            return None
        return self._rule_signature_from_match(plex_matcher.resolve(folder))

    def calculate_settings_hash(
        self,
        border_colors: Optional[List[str]],
        border_width: int,
        exclusion_list: Optional[List[str]],
        processing_profile: Optional[str] = None,
        season_mode: str = "inherit",
        season_border_colors: Optional[List[str]] = None,
        season_border_width: Optional[int] = None,
        style_opts: Optional[Dict[str, Any]] = None,
        season_style_opts: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Calculate a hash of border replacer settings to detect changes.
        When settings change, we need to reprocess all files even in incremental mode.

        Args:
            border_colors: List of hex colors or None
            border_width: Border width in pixels
            exclusion_list: List of titles to exclude
            season_mode: Season border mode ("inherit", "remove", "colors")
            season_border_colors: Colors to use for season posters when season_mode is "colors"
            season_border_width: Border width for season posters when season_mode is not "inherit"

        Returns:
            MD5 hash of the settings as a hex string
        """
        settings_dict = {
            "colors": sorted(border_colors or []),  # Sort for consistent hashing
            "width": border_width,
            "exclusions": sorted(exclusion_list or []),  # Sort for consistent hashing
            "profile": processing_profile or "default",
            "season_mode": season_mode,
            "season_colors": sorted(season_border_colors or []),
            "season_width": season_border_width,
            "style": self._style_opts_for_hash(style_opts),
            "season_style": self._style_opts_for_hash(season_style_opts) if season_style_opts else None,
        }
        settings_json = json.dumps(settings_dict, sort_keys=True)
        return hashlib.md5(settings_json.encode(), usedforsecurity=False).hexdigest()

    def detect_settings_change(
        self,
        border_colors: Optional[List[str]],
        border_width: int,
        exclusion_list: Optional[List[str]],
        destination_dir: str,
        dry_run: bool = False,
        processing_profile: Optional[str] = None,
        season_mode: str = "inherit",
        season_border_colors: Optional[List[str]] = None,
        season_border_width: Optional[int] = None,
        style_opts: Optional[Dict[str, Any]] = None,
        season_style_opts: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Check if border replacer settings have changed since last run.
        If changed, reset incremental tracking so all files are reprocessed.
        
        Args:
            border_colors: Current border colors
            border_width: Current border width
            exclusion_list: Current exclusion list
            destination_dir: Destination directory path to scope the reset
            dry_run: If True, only log what would happen without modifying database
            season_mode: Season border mode ("inherit", "remove", "colors")
            season_border_colors: Colors for season posters when season_mode is "colors"
            
        Returns:
            True if settings changed (or would change in dry run), False otherwise
        """
        from models.poster import Poster
        
        # Calculate current settings hash
        current_hash = self.calculate_settings_hash(
            border_colors,
            border_width,
            exclusion_list,
            processing_profile=processing_profile,
            season_mode=season_mode,
            season_border_colors=season_border_colors,
            season_border_width=season_border_width,
            style_opts=style_opts,
            season_style_opts=season_style_opts,
        )

        # Get stored hash from database
        stored_hash_setting = get_setting(self.db, "border_replacer_settings_hash")
        
        stored_hash = stored_hash_setting.value if stored_hash_setting else None
        
        # If hash changed, reset tracking for posters in destination directory
        if stored_hash != current_hash:
            if dry_run:
                # During dry run, only log what would happen - don't modify database
                if stored_hash:
                    log_info(
                        LogTags.BORDER_REPLACER,
                        "[DRY RUN] Border settings changed - would reset incremental tracking",
                        old_hash=stored_hash[:8],
                        new_hash=current_hash[:8]
                    )
                else:
                    log_debug(
                        LogTags.BORDER_REPLACER,
                        "[DRY RUN] Would initialize settings tracking",
                        hash=current_hash[:8]
                    )
                
                # Count how many would be reset
                try:
                    dest_pattern = destination_dir.rstrip('/') + '/%'
                    count = self.db.query(Poster).filter(
                        Poster.file_path.like(dest_pattern)
                    ).count()
                    
                    if count > 0:
                        log_info(
                            LogTags.BORDER_REPLACER,
                            f"[DRY RUN] Would reset tracking for {count} poster(s) in destination directory",
                            count=count,
                            destination=destination_dir
                        )
                except Exception as e:
                    log_error(LogTags.BORDER_REPLACER, f"Failed to count posters: {e}")
                
                return True
            else:
                # Real run - actually modify the database
                if stored_hash:
                    log_info(
                        LogTags.BORDER_REPLACER,
                        "Border settings changed - resetting incremental tracking",
                        old_hash=stored_hash[:8],
                        new_hash=current_hash[:8]
                    )
                else:
                    log_debug(
                        LogTags.BORDER_REPLACER,
                        "Initializing settings tracking",
                        hash=current_hash[:8]
                    )
                
                # Reset file_mtime values only for posters in the destination directory
                # This avoids resetting tracking for all 100k+ posters from synced drives
                try:
                    # Normalize destination_dir path for comparison
                    dest_pattern = destination_dir.rstrip('/') + '/%'
                    
                    updated_count = self.db.query(Poster).filter(
                        Poster.file_path.like(dest_pattern)
                    ).update({"file_mtime": 0}, synchronize_session=False)
                    self.db.commit()
                    
                    if updated_count > 0:
                        log_info(
                            LogTags.BORDER_REPLACER,
                            f"Reset tracking for {updated_count} poster(s) in destination directory",
                            count=updated_count,
                            destination=destination_dir
                        )
                except Exception as e:
                    log_error(LogTags.BORDER_REPLACER, f"Failed to reset tracking: {e}")
                    self.db.rollback()
                
                # Store new hash (only during real runs)
                upsert_setting(self.db, "border_replacer_settings_hash", current_hash)
                
                try:
                    self.db.commit()
                except Exception as e:
                    log_error(LogTags.BORDER_REPLACER, f"Failed to save settings hash: {e}")
                    self.db.rollback()
                
                return True
        
        return False

    def convert_to_rgb(self, hex_color: str) -> Tuple[int, int, int]:
        """Convert a hex color string (e.g. "#FF0000" or "F00") to an RGB tuple.

        Defaults to white on malformed input. Delegates to the module-level
        `_hex_to_rgb` so there is a single, correct implementation (including
        3-digit shorthand expansion).
        """
        return _hex_to_rgb(hex_color)

    def _is_within_holiday_range(self, schedule: str, now: datetime) -> bool:
        """Check whether current date is inside a DAPS-style holiday range schedule."""
        if not schedule or not schedule.startswith("range(") or not schedule.endswith(")"):
            return False

        try:
            inside = schedule[len("range(") : -1]
            start_str, end_str = inside.split("-", 1)
            start_month, start_day = map(int, start_str.split("/"))
            end_month, end_day = map(int, end_str.split("/"))
        except Exception:
            log_warning(
                LogTags.BORDER_REPLACER,
                f"Invalid holiday schedule format '{schedule}' - expected range(MM/DD-MM/DD)",
            )
            return False

        year = now.year
        start_date = datetime(year, start_month, start_day)
        end_date = datetime(year, end_month, end_day)

        if end_date < start_date:
            if now.month < start_month:
                start_date = start_date.replace(year=year - 1)
            else:
                end_date = end_date.replace(year=year + 1)

        return start_date <= now <= end_date

    def _load_holiday_schedules(self) -> List[Dict[str, Any]]:
        """Load holiday schedules from DB setting `border_replacer_holidays`."""
        holidays_setting = get_setting(self.db, "border_replacer_holidays")
        if not holidays_setting or not holidays_setting.value:
            return []

        try:
            parsed = json.loads(holidays_setting.value)
        except json.JSONDecodeError:
            log_warning(
                LogTags.BORDER_REPLACER,
                "Invalid border_replacer_holidays JSON - ignoring holiday schedules",
            )
            return []

        normalized: List[Dict[str, Any]] = []
        if isinstance(parsed, dict):
            for holiday_name, data in parsed.items():
                if not isinstance(data, dict):
                    continue
                colors = data.get("colors", data.get("color", []))
                if isinstance(colors, str):
                    colors = [colors]
                border_image = str(data.get("border_image", "")).strip() or None
                normalized.append(
                    {
                        "name": str(holiday_name),
                        "schedule": str(data.get("schedule", "")).strip(),
                        "colors": [str(c) for c in (colors or []) if str(c).strip()],
                        "border_image": border_image,
                        "style_opts": build_holiday_style_opts(data.get("style"), border_image),
                    }
                )
        elif isinstance(parsed, list):
            for entry in parsed:
                if not isinstance(entry, dict):
                    continue
                colors = entry.get("colors", entry.get("color", []))
                if isinstance(colors, str):
                    colors = [colors]
                name = str(entry.get("name", "")).strip()
                schedule = str(entry.get("schedule", "")).strip()
                if not name or not schedule:
                    continue
                normalized.append(
                    {
                        "name": name,
                        "schedule": schedule,
                        "colors": [str(c) for c in (colors or []) if str(c).strip()],
                        "border_image": str(entry.get("border_image", "")).strip() or None,
                        "style_opts": build_holiday_style_opts(
                            entry.get("style"), str(entry.get("border_image", "")).strip() or None
                        ),
                    }
                )

        return normalized

    def _resolve_effective_border_colors(
        self,
        default_border_colors: Optional[List[str]],
    ) -> Tuple[bool, Optional[str], List[str], Optional[Dict[str, Any]]]:
        """
        Resolve the active holiday's colors and full style for today if a holiday
        schedule matches.

        Returns:
            (is_holiday_active, holiday_name, effective_border_colors, holiday_style_opts)
        """
        now = datetime.now()
        default_colors = list(default_border_colors or [])
        holiday_schedules = self._load_holiday_schedules()

        for holiday in holiday_schedules:
            schedule = holiday.get("schedule", "")
            if not self._is_within_holiday_range(schedule, now):
                continue

            holiday_name = str(holiday.get("name", "Holiday")).strip() or "Holiday"
            holiday_colors = holiday.get("colors") or default_colors
            if isinstance(holiday_colors, str):
                holiday_colors = [holiday_colors]

            effective_colors = [str(c) for c in (holiday_colors or []) if str(c).strip()]
            return True, holiday_name, effective_colors, holiday.get("style_opts") or None

        return False, None, default_colors, None

    @staticmethod
    def _copy_file_if_changed(src_file: str, dest_file: str) -> bool:
        """Copy src→dest verbatim. Returns True if written, False if already identical."""
        os.makedirs(os.path.dirname(dest_file), exist_ok=True)
        if os.path.exists(dest_file) and filecmp.cmp(src_file, dest_file, shallow=False):
            return False
        shutil.copy2(src_file, dest_file)
        return True

    def _copy_single_image_unchanged(
        self,
        input_file: str,
        destination_dir: str,
        folder: Optional[str],
    ) -> bool:
        """Copy one poster to the destination without border changes.

        Returns True if the destination was written, False if it was already identical.
        """
        filename = os.path.basename(input_file)
        dest_file = (
            os.path.join(destination_dir, folder, filename)
            if folder
            else os.path.join(destination_dir, filename)
        )
        return self._copy_file_if_changed(input_file, dest_file)

    def replace_borders(
        self,
        input_file: str,
        output_path: str,
        border_color: Tuple[int, int, int],
        border_width: int,
        folder: Optional[str],
        style_opts: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Removes existing border and applies a new one.

        CRITICAL: Base image processing matches DAPS exactly:
        1. Crop image by border_width on all sides
        2. Add new border (solid color, gradient band, or image-overlay frame)
           plus an optional inner-edge effect (dark glow / border-color fade)
        3. Resize to exactly (1000, 1500)
        4. Convert to RGB

        Args:
            input_file: Path to input image
            output_path: Base directory for output
            border_color: RGB color tuple for new border
            border_width: Width of border in pixels
            folder: Optional subfolder for organization
            style_opts: Optional border style/effect options (see
                        _render_bordered_image). Defaults preserve solid color.

        Returns:
            True if file was saved/updated, False if unchanged
        """
        try:
            with Image.open(input_file) as image:
                # PIL has read the file — evict input pages from page cache.
                _drop_file_cache(input_file)

                # Build the new border (style + inner effect) and resize to DAPS standard.
                # Cropping (for solid/gradient) happens inside the renderer; image-overlay
                # frames are applied without cropping.
                final_image = _render_bordered_image(
                    image, border_width, border_color, style_opts
                )

                return self._save_image_if_changed(final_image, input_file, output_path, folder)

        except UnidentifiedImageError as e:
            log_error(
                LogTags.BORDER_REPLACER,
                f"Unidentified image format: {input_file}",
                error=str(e),
            )
            return False
        except Exception as e:
            log_error(
                LogTags.BORDER_REPLACER, f"Error processing {input_file}: {str(e)}"
            )
            return False

    def _save_image_if_changed(
        self,
        final_image: "Image.Image",
        input_file: str,
        output_path: str,
        folder: Optional[str],
    ) -> bool:
        """Write final_image to the computed output path only if it differs from what's
        already there (compared via a temp file). Returns True if written, False if
        identical. Shared by replace_borders and remove_borders."""
        file_name = os.path.basename(input_file)
        if folder:
            final_path = os.path.join(output_path, folder, file_name)
        else:
            final_path = os.path.join(output_path, file_name)

        if os.path.isfile(final_path):
            # Save to a temp file and byte-compare before overwriting.
            with tempfile.NamedTemporaryFile(
                suffix=os.path.splitext(file_name)[1], delete=False
            ) as tmp:
                tmp_path = tmp.name
                final_image.save(tmp_path)

            try:
                if not filecmp.cmp(final_path, tmp_path):
                    _drop_file_cache(tmp_path)
                    _drop_file_cache(final_path)
                    final_image.save(final_path)
                    _drop_file_cache(final_path)
                    os.remove(tmp_path)
                    return True
                _drop_file_cache(tmp_path)
                _drop_file_cache(final_path)
                os.remove(tmp_path)
                return False
            except Exception:
                _drop_file_cache(tmp_path)
                os.remove(tmp_path)
                raise

        os.makedirs(os.path.dirname(final_path), exist_ok=True)
        final_image.save(final_path)
        _drop_file_cache(final_path)
        return True

    def remove_borders(
        self,
        input_file: str,
        output_path: str,
        border_width: int,
        exclude: bool,
        folder: Optional[str],
    ) -> bool:
        """
        Crops an image to remove its borders.
        
        CRITICAL: Image processing logic must match DAPS exactly:
        - If exclude=False: Remove top/left/right, add black bottom border
        - If exclude=True: Remove all borders
        - Resize to exactly (1000, 1500)
        - Convert to RGB

        Args:
            input_file: Path to input image
            output_path: Base directory for output
            border_width: Width of border to remove
            exclude: If True, remove all borders; if False, keep black bottom
            folder: Optional subfolder for organization

        Returns:
            True if file was saved/updated, False if unchanged
        """
        try:
            with Image.open(input_file) as image:
                # PIL has read the file — evict input pages from page cache.
                _drop_file_cache(input_file)

                # Strip the border (shared with the image-overlay "remove existing
                # border first" option so removal behaves identically everywhere).
                final_image = _strip_existing_border(image, border_width, exclude)

                # CRITICAL: Resize to DAPS standard dimensions and convert to RGB
                final_image = final_image.resize((1000, 1500)).convert("RGB")

                return self._save_image_if_changed(final_image, input_file, output_path, folder)

        except UnidentifiedImageError as e:
            log_error(
                LogTags.BORDER_REPLACER,
                f"Unidentified image format: {input_file}",
                error=str(e),
            )
            return False
        except Exception as e:
            log_error(
                LogTags.BORDER_REPLACER, f"Error processing {input_file}: {str(e)}"
            )
            return False

    def process_posters(
        self,
        source_dir: str,
        destination_dir: str,
        border_colors: Optional[List[str]] = None,
        remove_borders: bool = False,
        border_width: int = 26,
        exclusion_list: Optional[List[str]] = None,
        dry_run: bool = False,
        progress_callback: Optional[ProgressCallback] = None,
        mode: str = "full",
        season_mode: str = "inherit",
        season_border_colors: Optional[List[str]] = None,
        season_border_width: Optional[int] = None,
        style_opts: Optional[Dict[str, Any]] = None,
        season_style_opts: Optional[Dict[str, Any]] = None,
        plex_matcher: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Process posters by applying or removing borders.

        Args:
            source_dir: Source directory containing posters (typically /posters/assets/tmp/)
            destination_dir: Final destination directory (/posters/assets/)
            border_colors: List of hex colors to cycle through for replacement mode
            remove_borders: If True, run explicit border-removal mode for main/movie posters
            border_width: Border width in pixels (default: 26)
            exclusion_list: List of titles to exclude from processing
            dry_run: If True, simulate without making changes
            progress_callback: Optional callback(phase, current, total, message)
            mode: "full" or "incremental" - full processes all, incremental only changed items
            season_mode: How to handle season posters — "inherit" (same as main), "remove"
                         (strip borders), or "colors" (use season_border_colors)
            season_border_colors: Hex colors used when season_mode is "colors"
            season_border_width: Border width (px) for season posters when season_mode is
                                 not "inherit". Falls back to border_width when not set.

        Returns:
            Dictionary with results including processed count and messages
        """
        try:
            # Log section start
            action_title = "Border Removal Starting" if remove_borders else "Border Replacement Starting"
            log_section_start(LogTags.BORDER_REPLACER, action_title)

            # Border style/effect options for this run (mutated below if a holiday
            # supplies its own border image).
            run_style_opts: Dict[str, Any] = dict(style_opts or {})

            if not os.path.exists(source_dir):
                return {
                    "success": False,
                    "error": f"Source directory does not exist: {source_dir}",
                }

            if not os.path.exists(destination_dir):
                log_info(
                    LogTags.BORDER_REPLACER,
                    f"Creating destination directory: {destination_dir}",
                )
                if not dry_run:
                    os.makedirs(destination_dir, exist_ok=True)

            is_holiday_active, active_holiday_name, effective_border_colors, holiday_style_opts = self._resolve_effective_border_colors(border_colors)

            if remove_borders:
                effective_border_colors = []

            # An active holiday brings its own complete border style (solid/gradient/
            # image + inner effect). Its colors feed the solid band.
            if is_holiday_active and not remove_borders and holiday_style_opts:
                run_style_opts = dict(holiday_style_opts)
                log_info(
                    LogTags.BORDER_REPLACER,
                    f"Holiday style active: {run_style_opts.get('style', 'solid')}",
                    holiday=active_holiday_name,
                )

            if is_holiday_active:
                log_info(
                    LogTags.BORDER_REPLACER,
                    f"Holiday schedule active: {active_holiday_name}",
                    holiday=active_holiday_name,
                    colors=len(effective_border_colors),
                )

            # An image-overlay frame, a gradient band, or the "remove" style is a complete
            # configuration on its own — none needs the solid border colors. (The "remove"
            # style strips the border via the replace path, so an active holiday can still
            # override it, unlike the global remove_borders toggle.)
            has_image_style = run_style_opts.get("style") == "image" and bool(run_style_opts.get("overlay_path"))
            has_gradient_style = run_style_opts.get("style") == "gradient" and bool(
                [c for c in (run_style_opts.get("gradient_colors") or []) if str(c).strip()]
            )
            has_remove_style = run_style_opts.get("style") == "remove"
            has_renderable_style = has_image_style or has_gradient_style or has_remove_style

            # Nothing configured for main posters: leave them unchanged (per-file
            # passthrough in the loop below) instead of stripping borders. Seasons and
            # holidays that carry their own config still process normally.
            main_passthrough = not remove_borders and len(effective_border_colors) == 0 and not has_renderable_style
            if main_passthrough:
                log_info(LogTags.BORDER_REPLACER, "No border color/style configured for main posters — leaving them unchanged")

            # Season custom style (its own complete border style), resolved upstream.
            season_render_opts: Dict[str, Any] = dict(season_style_opts or {})
            season_has_image = season_render_opts.get("style") == "image" and bool(season_render_opts.get("overlay_path"))
            season_has_gradient = season_render_opts.get("style") == "gradient" and bool(
                [c for c in (season_render_opts.get("gradient_colors") or []) if str(c).strip()]
            )
            season_has_renderable = season_has_image or season_has_gradient

            # Resolve season border colors
            effective_season_mode = season_mode if season_mode in ("inherit", "remove", "colors", "custom") else "inherit"
            effective_season_colors: List[str] = []
            if effective_season_mode in ("colors", "custom"):
                effective_season_colors = list(season_border_colors or [])
                # "colors" needs colors; "custom" is also valid with a gradient/image style.
                if not effective_season_colors and not (effective_season_mode == "custom" and season_has_renderable):
                    effective_season_mode = "inherit"

            # Resolve season border width — only used when seasons are handled separately
            # (remove/colors mode). Falls back to the main border width when not set or <= 0.
            if effective_season_mode != "inherit" and season_border_width and season_border_width > 0:
                effective_season_width = int(season_border_width)
            else:
                effective_season_width = border_width

            # Convert hex colors to RGB
            rgb_border_colors: List[Tuple[int, int, int]] = []
            if not remove_borders and effective_border_colors:
                for color in effective_border_colors:
                    rgb_color = self.convert_to_rgb(color)
                    rgb_border_colors.append(rgb_color)

            # Image-overlay or gradient style with no solid colors configured: the band
            # color is hidden by the overlay/gradient, so use a placeholder so the
            # replace path still runs (instead of falling through to border removal).
            if not remove_borders and not rgb_border_colors and has_renderable_style:
                rgb_border_colors = [(0, 0, 0)]

            # Legacy "colors" mode renders seasons as a solid band (dropping the main
            # gradient/image) while still honoring the main inner effect.
            legacy_season_style_opts = {**run_style_opts, "style": "solid", "overlay_path": None}

            rgb_season_colors: List[Tuple[int, int, int]] = []
            if effective_season_mode in ("colors", "custom") and effective_season_colors:
                for color in effective_season_colors:
                    rgb_season_colors.append(self.convert_to_rgb(color))
            # Custom season style with a gradient/image and no solid colors: placeholder
            # band color so the replace path still runs.
            if effective_season_mode == "custom" and not rgb_season_colors and season_has_renderable:
                rgb_season_colors = [(0, 0, 0)]

            action = "Removing borders" if remove_borders else "Replacing borders"
            mode_str = "(incremental)" if mode == "incremental" else "(full)"
            log_info(LogTags.BORDER_REPLACER, f"{action} {mode_str}")
            if effective_season_mode != "inherit":
                if effective_season_mode == "remove":
                    season_action = "remove"
                elif effective_season_mode == "custom":
                    season_action = f"custom style ({season_render_opts.get('style', 'solid')})"
                else:
                    season_action = f"custom colors ({len(rgb_season_colors)})"
                width_note = f", width {effective_season_width}px" if effective_season_width != border_width else ""
                log_info(LogTags.BORDER_REPLACER, f"Season poster override: {season_action}{width_note}")

            # Import Poster model for incremental tracking
            from models.poster import Poster

            # Detect if settings have changed (only relevant for incremental mode)
            # During dry runs, only logs what would happen without modifying database
            # During real runs, resets tracking if colors/width/exclusions changed
            if mode == "incremental":
                if remove_borders:
                    profile = "remove_borders"
                elif main_passthrough:
                    profile = "no_border_passthrough"
                else:
                    profile = f"holiday:{active_holiday_name}" if is_holiday_active and active_holiday_name else "default"
                self.detect_settings_change(
                    effective_border_colors,
                    border_width,
                    exclusion_list,
                    destination_dir,
                    dry_run,
                    processing_profile=profile,
                    season_mode=effective_season_mode,
                    season_border_colors=effective_season_colors,
                    season_border_width=effective_season_width if effective_season_mode != "inherit" else None,
                    # In passthrough the main style is never applied, so keep it out of the
                    # hash — otherwise editing a leftover style field needlessly resets the
                    # whole destination's incremental tracking.
                    style_opts=None if main_passthrough else run_style_opts,
                    season_style_opts=season_render_opts if effective_season_mode == "custom" else None,
                )

            incremental_tracking_records: Dict[str, Poster] = {}
            pending_tracking_updates = 0
            tracking_updates_enabled = not dry_run  # Both full and incremental maintain tracking

            def _flush_tracking_updates(force: bool = False) -> None:
                nonlocal pending_tracking_updates, tracking_updates_enabled
                if not tracking_updates_enabled:
                    return
                if pending_tracking_updates == 0:
                    return
                if not force and pending_tracking_updates < 250:
                    return

                try:
                    self.db.commit()
                    pending_tracking_updates = 0
                except Exception as db_error:
                    log_warning(
                        LogTags.BORDER_REPLACER,
                        f"Failed to flush incremental tracking updates: {db_error}",
                        error=str(db_error),
                    )
                    self.db.rollback()
                    pending_tracking_updates = 0
                    tracking_updates_enabled = False

            # For incremental mode, build a set of files that need processing
            # by querying database once instead of per-file
            files_needing_processing = None
            if mode == "incremental":
                log_info(LogTags.BORDER_REPLACER, "Building list of files needing processing...")
                files_needing_processing = set()
                
                # Walk source directory to get all source files with their mtimes
                source_files_with_mtime = {}
                for root, dirs, files in os.walk(source_dir):
                    rel_path = os.path.relpath(root, source_dir)
                    folder = None if rel_path == "." else rel_path
                    
                    for file in files:
                        if not file.lower().endswith((".jpg", ".jpeg", ".png")):
                            continue
                        
                        input_file = os.path.join(root, file)
                        dest_file = os.path.join(destination_dir, folder, file) if folder else os.path.join(destination_dir, file)
                        
                        try:
                            source_mtime = os.path.getmtime(input_file)
                            source_size = os.path.getsize(input_file)
                            source_files_with_mtime[dest_file] = (input_file, source_mtime, source_size, folder)
                        except OSError:
                            # If can't get mtime, add to processing list
                            files_needing_processing.add(dest_file)
                
                # Query database for all tracked files in one go
                dest_paths = list(source_files_with_mtime.keys())
                if dest_paths:
                    tracked_files = self.db.query(Poster).filter(
                        Poster.file_path.in_(dest_paths)
                    ).all()
                    
                    # Build dict of tracked files with their mtimes
                    tracked_metadata = {
                        p.file_path: (p.file_mtime, p.file_size, p.dest_file_mtime, p.border_rule_sig)
                        for p in tracked_files
                    }

                    def _mark_for_processing(
                        dest_file_path: str,
                        input_file_path: str,
                        reason: str,
                        title_folder: Optional[str],
                        source_mtime_value: Optional[float],
                        tracked_mtime_value: Optional[float],
                        source_size_value: Optional[int],
                        tracked_size_value: Optional[int],
                    ) -> None:
                        if title_folder:
                            display_title = title_folder
                        else:
                            display_title = os.path.splitext(os.path.basename(input_file_path))[0]

                        files_needing_processing.add(dest_file_path)
                        log_debug(
                            LogTags.BORDER_REPLACER,
                            f"Incremental reprocess queued: {display_title} ({reason})",
                            reason=reason,
                            source=input_file_path,
                            destination=dest_file_path,
                            source_mtime=source_mtime_value,
                            tracked_mtime=tracked_mtime_value,
                            source_size=source_size_value,
                            tracked_size=tracked_size_value,
                        )
                    
                    # Compare mtimes to determine which files need processing
                    for dest_file, (input_file, source_mtime, source_size, folder) in source_files_with_mtime.items():
                        tracked_entry = tracked_metadata.get(dest_file)

                        if tracked_entry is None:
                            _mark_for_processing(
                                dest_file,
                                input_file,
                                "missing_tracking",
                                folder,
                                source_mtime,
                                None,
                                source_size,
                                None,
                            )
                            continue

                        tracked_mtime, tracked_source_size, tracked_dest_mtime, tracked_rule_sig = tracked_entry

                        # Reprocess when the Plex rule applied to THIS item changed since
                        # last run — e.g. Kometa added/removed a label so it now matches a
                        # different rule (or none), or the rule's colors/style were edited.
                        # This targets only the affected items instead of resetting all.
                        # Only compare when rules were actually evaluated this run (matcher
                        # present); when they weren't (disabled for this run type, no
                        # libraries, or Plex unreachable) we must NOT treat "no rule" as a
                        # change, or every rule-styled item would revert to a default border.
                        if plex_matcher is not None and self._rule_signature(plex_matcher, folder) != tracked_rule_sig:
                            _mark_for_processing(
                                dest_file,
                                input_file,
                                "plex_rule_changed",
                                folder,
                                source_mtime,
                                tracked_mtime,
                                source_size,
                                tracked_source_size,
                            )
                            continue

                        # Reprocess if output file disappeared.
                        if not os.path.exists(dest_file):
                            _mark_for_processing(
                                dest_file,
                                input_file,
                                "missing_destination",
                                folder,
                                source_mtime,
                                tracked_mtime,
                                source_size,
                                tracked_source_size,
                            )
                            continue

                        # Reprocess when source mtime changed materially.
                        if tracked_mtime is None or abs(float(source_mtime) - float(tracked_mtime)) > 0.0001:
                            _mark_for_processing(
                                dest_file,
                                input_file,
                                "mtime_changed",
                                folder,
                                source_mtime,
                                tracked_mtime,
                                source_size,
                                tracked_source_size,
                            )
                            continue

                        # Reprocess when source size changed even if mtime is preserved.
                        if tracked_source_size is not None and int(source_size) != int(tracked_source_size):
                            _mark_for_processing(
                                dest_file,
                                input_file,
                                "size_changed",
                                folder,
                                source_mtime,
                                tracked_mtime,
                                source_size,
                                tracked_source_size,
                            )
                            continue

                        # First-time population: dest_file_mtime not yet recorded for this
                        # file (e.g. existing records from before the column was added).
                        # Process once so the value gets written and future runs can detect
                        # external modifications.
                        if tracked_dest_mtime is None:
                            _mark_for_processing(
                                dest_file,
                                input_file,
                                "dest_mtime_not_recorded",
                                folder,
                                source_mtime,
                                tracked_mtime,
                                source_size,
                                tracked_source_size,
                            )
                            continue

                        # Reprocess if destination was externally modified (e.g. replaced by another app).
                        if tracked_dest_mtime is not None:
                            try:
                                current_dest_mtime = os.path.getmtime(dest_file)
                                if abs(float(current_dest_mtime) - float(tracked_dest_mtime)) > 0.0001:
                                    _mark_for_processing(
                                        dest_file,
                                        input_file,
                                        "dest_modified_externally",
                                        folder,
                                        source_mtime,
                                        tracked_mtime,
                                        source_size,
                                        tracked_source_size,
                                    )
                            except OSError:
                                pass
                
                # Clean up stale tracking records before (potentially) short-circuiting
                if tracking_updates_enabled and source_files_with_mtime:
                    current_dest_paths = set(source_files_with_mtime.keys())
                    try:
                        all_tracked = self.db.query(Poster).filter(
                            Poster.drive_id == "border_processed"
                        ).all()
                        stale = [r for r in all_tracked if r.file_path not in current_dest_paths]
                        if stale:
                            for record in stale:
                                self.db.delete(record)
                            self.db.commit()
                            log_info(
                                LogTags.BORDER_REPLACER,
                                f"Removed {len(stale)} stale tracking record(s) for deleted/moved files",
                                count=len(stale),
                            )
                    except Exception as stale_err:
                        log_warning(LogTags.BORDER_REPLACER, f"Failed to clean up stale tracking records: {stale_err}")
                        self.db.rollback()

                log_info(
                    LogTags.BORDER_REPLACER, 
                    f"Incremental mode: {len(files_needing_processing)} of {len(source_files_with_mtime)} files need processing",
                    needing_processing=len(files_needing_processing),
                    total=len(source_files_with_mtime)
                )
                
                # Short-circuit if no files need processing in incremental mode
                if len(files_needing_processing) == 0:
                    log_info(
                        LogTags.BORDER_REPLACER,
                        "No files need processing - all up to date",
                    )
                    log_success(
                        LogTags.BORDER_REPLACER,
                        f"Border processing complete: 0 changed, {len(source_files_with_mtime)} skipped",
                        changed=0,
                        skipped=len(source_files_with_mtime),
                        total=len(source_files_with_mtime),
                    )
                    log_section_end(LogTags.BORDER_REPLACER, "Border Processing Complete")
                    return {
                        "success": True,
                        "processed": 0,
                        "changed": 0,
                        "skipped": len(source_files_with_mtime),
                        "changed_count": 0,
                        "skipped_count": len(source_files_with_mtime),
                    }

                tracked_records = self.db.query(Poster).filter(
                    Poster.file_path.in_(list(files_needing_processing))
                ).all()
                incremental_tracking_records = {
                    record.file_path: record
                    for record in tracked_records
                }

            elif mode == "full" and tracking_updates_enabled:
                # Full mode: pre-load all existing tracking records so we can update
                # vs insert correctly, and clean up stale records in one pass.
                log_info(LogTags.BORDER_REPLACER, "Full mode: loading tracking records for update...")

                # Walk source dir to build the complete set of dest paths this run will produce
                full_mode_dest_paths: set[str] = set()
                for root, dirs, files in os.walk(source_dir):
                    rel_path = os.path.relpath(root, source_dir)
                    folder = None if rel_path == "." else rel_path
                    for file in files:
                        if not file.lower().endswith((".jpg", ".jpeg", ".png")):
                            continue
                        dest_file = os.path.join(destination_dir, folder, file) if folder else os.path.join(destination_dir, file)
                        full_mode_dest_paths.add(dest_file)

                # Load existing tracking records (filter in Python to avoid SQLite 999-var limit)
                if full_mode_dest_paths:
                    all_existing = self.db.query(Poster).filter(
                        Poster.drive_id == "border_processed"
                    ).all()
                    incremental_tracking_records = {
                        record.file_path: record
                        for record in all_existing
                        if record.file_path in full_mode_dest_paths
                    }

                # Remove stale records (dest paths no longer produced by source tree)
                # Filter in Python to avoid SQLite's 999-variable IN clause limit
                try:
                    all_tracked = self.db.query(Poster).filter(
                        Poster.drive_id == "border_processed"
                    ).all()
                    stale = [r for r in all_tracked if r.file_path not in full_mode_dest_paths]
                    if stale:
                        for record in stale:
                            self.db.delete(record)
                        self.db.commit()
                        log_info(
                            LogTags.BORDER_REPLACER,
                            f"Removed {len(stale)} stale tracking record(s) for deleted/moved files",
                            count=len(stale),
                        )
                except Exception as stale_err:
                    log_warning(LogTags.BORDER_REPLACER, f"Failed to clean up stale tracking records: {stale_err}")
                    self.db.rollback()

            # Scan source directory for folders and posters
            processed_count = 0
            changed_count = 0
            skipped_count = 0
            total_items = 0

            # Count total items for progress
            # If incremental mode already walked the tree, use that count
            if mode == "incremental" and files_needing_processing is not None:
                total_items = len(files_needing_processing)
            else:
                # Otherwise, count by walking
                for root, dirs, files in os.walk(source_dir):
                    for file in files:
                        if file.lower().endswith((".jpg", ".jpeg", ".png")):
                            total_items += 1

            if total_items == 0:
                return {
                    "success": False,
                    "error": "No poster files found in source directory",
                }

            log_info(
                LogTags.BORDER_REPLACER,
                f"Found {total_items} poster files to process",
                count=total_items,
            )

            current_color_index = 0
            current_season_color_index = 0
            # Each Plex rule cycles its OWN color list independently (keyed by rule name), so
            # one rule's matched posters don't consume another rule's place in the rotation.
            plex_color_indices: Dict[str, int] = {}

            # Process each poster file
            for root, dirs, files in os.walk(source_dir):
                # Get relative path from source_dir for folder structure
                rel_path = os.path.relpath(root, source_dir)
                folder = None if rel_path == "." else rel_path

                for file in files:
                    if not file.lower().endswith((".jpg", ".jpeg", ".png")):
                        continue

                    input_file = os.path.join(root, file)

                    # Determine whether this is a season poster
                    is_season = self._is_season_file(file)

                    # Exclusion only affects border REMOVAL — excluded titles have all four
                    # borders stripped instead of the top/left/right + black-bar convention.
                    # It has no effect when a border is added or the poster passes through,
                    # so only evaluate (and log) it for files that will actually be removed.
                    # A poster is removed when it's a season poster in explicit "remove" mode,
                    # OR it follows the main path with global remove-borders on — the latter
                    # INCLUDES season posters in "inherit" mode (they inherit main removal).
                    file_will_be_removed = (
                        (is_season and effective_season_mode == "remove")
                        or (remove_borders and (not is_season or effective_season_mode == "inherit"))
                    )
                    excluded = False
                    if file_will_be_removed and exclusion_list and folder:
                        for exclusion in exclusion_list:
                            if exclusion.lower() in folder.lower():
                                excluded = True
                                log_debug(
                                    LogTags.BORDER_REPLACER,
                                    f"Full border removal (no title bar) for {folder}/{file}",
                                    file=file,
                                )
                                break

                    # Incremental mode: Skip files not in processing list (applies to both dry run and real run)
                    if mode == "incremental" and files_needing_processing is not None:
                        dest_file = os.path.join(destination_dir, folder, file) if folder else os.path.join(destination_dir, file)

                        if dest_file not in files_needing_processing:
                            continue

                    processed_count += 1

                    progress_item = f"{folder}/{file}" if folder else file
                    if progress_callback:
                        progress_callback(
                            "processing",
                            processed_count,
                            total_items,
                            f"Processing: {progress_item}",
                        )

                    if not dry_run:
                        dest_file = os.path.join(destination_dir, folder, file) if folder else os.path.join(destination_dir, file)

                        # Plex label/genre/collection rule (highest priority): applies its
                        # own border to matching items, skips items marked skip, and leaves
                        # everything else to the normal season/main handling below.
                        plex_match = plex_matcher.resolve(folder) if plex_matcher else None

                        # Determine the action for this specific file based on whether it's a season poster
                        if plex_match is not None and plex_match.skip:
                            # Plex rule: leave this item completely untouched.
                            result = self._copy_single_image_unchanged(input_file, destination_dir, folder)
                        elif plex_match is not None:
                            # Plex rule: apply this rule's border style (cycle its colors).
                            rgb_rule_colors = [self.convert_to_rgb(c) for c in (plex_match.colors or [])]
                            if not rgb_rule_colors:
                                rgb_rule_colors = [(0, 0, 0)]  # gradient/image styles hide the band color
                            rule_idx = plex_color_indices.get(plex_match.name, 0)
                            rule_color = rgb_rule_colors[rule_idx % len(rgb_rule_colors)]
                            plex_color_indices[plex_match.name] = rule_idx + 1
                            result = self.replace_borders(
                                input_file,
                                destination_dir,
                                rule_color,
                                border_width,
                                folder,
                                style_opts=plex_match.style_opts,
                            )
                        elif is_season and effective_season_mode == "remove":
                            # Season-specific: remove borders
                            result = self.remove_borders(
                                input_file,
                                destination_dir,
                                effective_season_width,
                                excluded,
                                folder,
                            )
                        elif is_season and effective_season_mode == "custom" and rgb_season_colors:
                            # Season-specific: full independent style (solid/gradient/image + inner effect)
                            rgb_season_color = rgb_season_colors[current_season_color_index]
                            result = self.replace_borders(
                                input_file,
                                destination_dir,
                                rgb_season_color,
                                effective_season_width,
                                folder,
                                style_opts=season_render_opts,
                            )
                            current_season_color_index = (current_season_color_index + 1) % len(rgb_season_colors)
                        elif is_season and effective_season_mode == "colors" and rgb_season_colors:
                            # Season-specific (legacy): apply season colors as a solid band
                            rgb_season_color = rgb_season_colors[current_season_color_index]
                            result = self.replace_borders(
                                input_file,
                                destination_dir,
                                rgb_season_color,
                                effective_season_width,
                                folder,
                                style_opts=legacy_season_style_opts,
                            )
                            current_season_color_index = (current_season_color_index + 1) % len(rgb_season_colors)
                        elif not remove_borders and rgb_border_colors:
                            # Main behavior: replace border with color (style + inner effect)
                            rgb_border_color = rgb_border_colors[current_color_index]
                            result = self.replace_borders(
                                input_file,
                                destination_dir,
                                rgb_border_color,
                                border_width,
                                folder,
                                style_opts=run_style_opts,
                            )
                            current_color_index = (current_color_index + 1) % len(rgb_border_colors)
                        elif main_passthrough:
                            # Nothing configured for this main/inherit poster: leave it
                            # unchanged (copy through) instead of stripping its border.
                            result = self._copy_single_image_unchanged(
                                input_file,
                                destination_dir,
                                folder,
                            )
                        else:
                            # Fallback: remove borders (explicit remove_borders mode)
                            result = self.remove_borders(
                                input_file,
                                destination_dir,
                                border_width,
                                excluded,
                                folder,
                            )

                        if mode == "incremental" and tracking_updates_enabled:
                            # Update tracking for both changed and unchanged files so
                            # incremental mode does not repeatedly re-check identical items.
                            try:
                                current_mtime = os.path.getmtime(input_file)
                                current_size = os.path.getsize(input_file)
                                current_time = datetime.now(timezone.utc)

                                # Record the destination file's mtime so we can detect
                                # external modifications on subsequent runs.
                                try:
                                    written_dest_mtime = os.path.getmtime(dest_file)
                                except OSError:
                                    written_dest_mtime = None

                                # Fingerprint of the rule applied to this item, so the next
                                # incremental run reprocesses it only if its rule changes. We
                                # only reach here for items actually reprocessed this run, so
                                # this always reflects what we just baked: sig(rule) when a
                                # rule applied, or None when it didn't (including runs where
                                # rules weren't evaluated — those bake a non-rule border, and
                                # the next rules-run's mismatch restores the rule border).
                                current_rule_sig = self._rule_signature_from_match(plex_match)

                                poster_record = incremental_tracking_records.get(dest_file)

                                if poster_record:
                                    poster_record.file_mtime = current_mtime
                                    poster_record.last_processed = current_time
                                    poster_record.file_size = current_size
                                    poster_record.dest_file_mtime = written_dest_mtime
                                    poster_record.border_rule_sig = current_rule_sig
                                else:
                                    poster_record = Poster(
                                        drive_id="border_processed",
                                        file_name=file,
                                        file_path=dest_file,
                                        file_size=current_size,
                                        file_mtime=current_mtime,
                                        last_processed=current_time,
                                        dest_file_mtime=written_dest_mtime,
                                        border_rule_sig=current_rule_sig,
                                    )
                                    self.db.add(poster_record)
                                    incremental_tracking_records[dest_file] = poster_record

                                pending_tracking_updates += 1
                                _flush_tracking_updates()
                            except Exception as db_error:
                                log_warning(
                                    LogTags.BORDER_REPLACER,
                                    f"Failed to update database tracking for {file}: {db_error}",
                                    file=file,
                                    error=str(db_error),
                                )
                                self.db.rollback()
                                tracking_updates_enabled = False

                        if result:
                            changed_count += 1
                            
                            # Log the actual change
                            title_info = f" - {folder}" if folder else ""
                            log_debug(
                                LogTags.BORDER_REPLACER,
                                f"Applied changes: {file}{title_info}",
                                file=file,
                                folder=folder or "root"
                            )
                            
                        else:
                            skipped_count += 1
                            # Log when file was checked but no changes needed (already identical)
                            title_info = f" - {folder}" if folder else ""
                            log_debug(
                                LogTags.BORDER_REPLACER,
                                f"No change needed: {file}{title_info}",
                                file=file,
                                folder=folder or "root"
                            )
                    else:
                        # Dry run - just count
                        changed_count += 1

            _flush_tracking_updates(force=True)

            log_success(
                LogTags.BORDER_REPLACER,
                f"Border processing complete: {changed_count} changed, {skipped_count} skipped",
                changed=changed_count,
                skipped=skipped_count,
                total=processed_count,
            )
            
            log_section_end(LogTags.BORDER_REPLACER, "Border Processing Complete")

            return {
                "success": True,
                "processed": processed_count,
                "changed": changed_count,
                "skipped": skipped_count,
                "dry_run": dry_run,
            }

        except Exception as e:
            log_error(
                LogTags.BORDER_REPLACER, f"Border processing failed: {str(e)}", error=str(e)
            )
            log_section_end(LogTags.BORDER_REPLACER, "Border Processing Failed")
            return {"success": False, "error": str(e)}
