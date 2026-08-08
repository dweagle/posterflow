"""Render a text logo in the style of the user's collection-poster PSD LOGO group: up to three
centered white lines on a transparent canvas.

Metrics were extracted from the PSD (1000×1500 poster) and are reproduced at 2× for crispness:
  TOP     Roboto Condensed Regular   62.5px   tracking  50    gap below 13px
  MAIN    Bebas Neue (classic Bold)  150px    tracking 100    gap below 19px
  SUFFIX  Arial                      32px     tracking 800    (the spread "C O L L E C T I O N")

Tracking is Photoshop's 1/1000-em unit, reproduced by drawing per character (PIL has no
letter-spacing). Fonts resolve from ``config/artwork/fonts`` first — drop in the exact fonts
(.ttf or .otf, named per LINE_SPECS below, extension ignored) for pixel-perfect fidelity — then
the bundled ``backend/assets/fonts`` (Liberation Sans is metric-compatible with Arial; see that
folder for what ships).
"""
import io
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from core.config import settings as app_settings

BUNDLED_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


def config_font_dir() -> Path:
    """User font overrides, alongside the rest of the artwork config."""
    return app_settings.config_dir / "artwork" / "fonts"


SCALE = 2
PAD = 0   # ink-tight canvas — consumers (Plex/Kometa) letterbox logos themselves
MAX_LINE_CHARS = 80

# Candidate filenames are tried in order per directory, so a user-supplied exact font always
# beats a bundled substitute.
LINE_SPECS = {
    "top": {"fonts": ["RobotoCondensed-Regular.ttf", "RobotoCondensed-Variable.ttf"],
            "size": 62.5, "tracking": 50, "gap_after": 13},
    # The PSD's main-line tracking is 150; 100 is the user's preferred default. Bebas Neue Pro
    # leads the font list — it's the PSD's actual face and the only one here with real lowercase;
    # the plain Bebas Neue names stay as fallbacks for instances that only have that.
    "main": {"fonts": ["Bebas Neue Pro Bold.ttf", "BebasNeuePro-Bold.ttf", "BebasNeuePro.ttf",
                       "BebasNeueBold.ttf", "BebasNeue-Bold.ttf", "BebasNeue-Regular.ttf"],
             "size": 150, "tracking": 100, "gap_after": 19},
    "suffix": {"fonts": ["ArialMT.ttf", "Arial.ttf", "LiberationSans-Regular.ttf"],
               "size": 32, "tracking": 800, "gap_after": 0},
}


def _resolve_font(candidates: list[str]) -> Optional[Path]:
    """First matching font file from config/artwork/fonts, then the bundled set. Matching is
    case-insensitive and extension-agnostic (a dropped-in .otf matches a .ttf candidate name)."""
    for directory in (config_font_dir(), BUNDLED_FONT_DIR):
        if not directory.is_dir():
            continue
        files: dict[str, Path] = {}
        for p in sorted(directory.iterdir()):
            if p.suffix.lower() in (".ttf", ".otf"):
                files.setdefault(p.stem.lower(), p)
        for name in candidates:
            hit = files.get(Path(name).stem.lower())
            if hit:
                return hit
    return None


def list_fonts() -> list[dict]:
    """Every usable font for the dialog's pickers: config/artwork/fonts first (they win name
    collisions with the bundled set). Unreadable files are skipped — offering them would only
    error later."""
    out: list[dict] = []
    seen: set[str] = set()
    for directory, source in ((config_font_dir(), "config"), (BUNDLED_FONT_DIR, "bundled")):
        if not directory.is_dir():
            continue
        for p in sorted(directory.iterdir()):
            if p.suffix.lower() not in (".ttf", ".otf") or p.stem.lower() in seen:
                continue
            try:
                family, style = ImageFont.truetype(str(p), 24).getname()
            except Exception:
                continue
            seen.add(p.stem.lower())
            # config fonts list under their FILENAME so the user can edit the label by renaming
            # the file; bundled ones show their embedded family name.
            if source == "config":
                label = p.stem
            else:
                label = family if not style or style.lower() == "regular" else f"{family} {style}"
            out.append({"id": p.stem, "label": label, "source": source})
    return out


def _render_line(text: str, font: ImageFont.FreeTypeFont, tracking: float, size_px: float) -> Image.Image:
    """One line of white text with Photoshop-style tracking, cropped to its ink box."""
    spacing = tracking / 1000.0 * size_px
    advances = [font.getlength(ch) for ch in text]
    total = sum(advances) + spacing * max(0, len(text) - 1)
    ascent, descent = font.getmetrics()
    margin = 10  # headroom so glyph overshoot never clips before the ink-box crop
    canvas = Image.new("RGBA", (int(total) + 2 * margin, ascent + descent + 2 * margin), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    x = float(margin)
    for ch, adv in zip(text, advances):
        draw.text((x, margin), ch, font=font, fill=(255, 255, 255, 255))
        x += adv + spacing
    box = canvas.getbbox()
    return canvas.crop(box) if box else canvas


def render_text_logo(top: str = "", main: str = "", suffix: str = "", *,
                     top_tracking: Optional[int] = None, top_scale: Optional[int] = None,
                     main_tracking: Optional[int] = None, main_scale: Optional[int] = None,
                     top_font: Optional[str] = None, main_font: Optional[str] = None) -> Image.Image:
    """The composed transparent-PNG logo. Empty lines are omitted; raises ValueError when all
    lines are empty, a line is absurdly long, or a needed font can't be found.

    The title lines take optional overrides — tracking (Photoshop 1/1000-em units), a
    horizontal-scale percent (Photoshop's HorizontalScale, for squeezing long titles), and a
    font id (a list_fonts() stem). The suffix line's styling is fixed."""
    overrides = {"top": (top_tracking, top_scale), "main": (main_tracking, main_scale)}
    font_overrides = {"top": top_font, "main": main_font}
    lines: list[tuple[Image.Image, int]] = []
    for key, raw in (("top", top), ("main", main), ("suffix", suffix)):
        text = " ".join(str(raw or "").split())
        if not text:
            continue
        if len(text) > MAX_LINE_CHARS:
            raise ValueError(f"{key} line is too long (max {MAX_LINE_CHARS} characters)")
        spec = LINE_SPECS[key]
        wanted_font = str(font_overrides.get(key) or "").strip()
        font_path = _resolve_font([wanted_font] if wanted_font else spec["fonts"])
        if font_path is None:
            raise ValueError(f"Font '{wanted_font}' not found in config/artwork/fonts." if wanted_font
                             else f"No font found for the {key} line — put {spec['fonts'][0]} in config/artwork/fonts.")
        tracking, scale = overrides.get(key, (None, None))
        tracking = spec["tracking"] if tracking is None else max(-200, min(2000, int(tracking)))
        scale = 100 if scale is None else max(25, min(200, int(scale)))
        size_px = spec["size"] * SCALE
        font = ImageFont.truetype(str(font_path), int(round(size_px)))
        image = _render_line(text, font, tracking, size_px)
        if scale != 100:
            image = image.resize((max(1, round(image.width * scale / 100)), image.height),
                                 Image.Resampling.LANCZOS)
        lines.append((image, spec["gap_after"] * SCALE))
    if not lines:
        raise ValueError("logo text is empty")

    width = max(im.width for im, _ in lines) + 2 * PAD
    height = sum(im.height for im, _ in lines) + sum(int(g) for _, g in lines[:-1]) + 2 * PAD
    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    y = PAD
    for i, (im, gap) in enumerate(lines):
        out.paste(im, ((width - im.width) // 2, y), im)
        y += im.height + (int(gap) if i < len(lines) - 1 else 0)
    return out


def render_text_logo_png(top: str = "", main: str = "", suffix: str = "", **overrides) -> bytes:
    buf = io.BytesIO()
    render_text_logo(top, main, suffix, **overrides).save(buf, "PNG")
    return buf.getvalue()
