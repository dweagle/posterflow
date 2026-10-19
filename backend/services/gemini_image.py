"""Gemini image editing (Nano Banana) behind the maker card's experimental Gemini buttons:
Remove Text (a textless redraw of a poster/backdrop) and Make Logo (the title lettering lifted
out as a white-on-transparent PNG).

Gemini is a regenerating model, not an eraser: it redraws the whole image from a prompt, so
pixels outside the text can drift too. Its output comes back on Gemini's own 1K/2K/4K grid and
is fitted back to the source size here. Every call is billed to the user's own key.
"""
import base64
import io
import math
import time
from typing import Any

import requests
from PIL import Image, ImageOps

from core.logging import LogTags, log_info
from models.setting import get_setting
from services.rejected_keys import warn_rejected_key

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_MODEL = "gemini-nano-banana-2.1"
MODELS = ("gemini-nano-banana-2.1", "gemini-3.1-flash-image", "gemini-3-pro-image")
IMAGE_SIZES = ("1K", "2K", "4K")
# Gemini's output ratios (w:h); the source is snapped to the nearest one.
ASPECT_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9",
                 "1:4", "4:1", "1:8", "8:1")
JPEG_QUALITY = 92
MAX_PROMPT_CHARS = 2000
LOGO_ASPECT_RATIO = "16:9"   # a wide canvas for lettering; the mark is trimmed out of it anyway
LOGO_FRINGE = 2              # px kept beyond the trim box so anti-aliased edges survive; otherwise tight
POSTER_MIN_SIZE = (1000, 1500)   # a textless poster never comes back smaller than this
_TIMEOUT = (15, 240)         # a render takes 10-60s; only the connect timeout stays short

REMOVE_TEXT_PROMPT = (
    "Remove every piece of text from this image: the title, tagline, credits block, billing, "
    "logos, rating badges, release dates, and any other lettering or wordmarks. Keep everything "
    "else exactly as it is: the same artwork, composition, framing, colors, lighting, grain and "
    "level of detail. Fill the areas the text covered with background that matches the "
    "surrounding artwork. Do not add, move, restyle or crop anything. Return only the edited image."
)
EXTRACT_LOGO_PROMPT = (
    "Isolate the title logo in this image: the stylized title lettering or wordmark, including "
    "any emblem that is part of it. Reproduce it as a faithful copy, not a reinterpretation: the "
    "same typeface, letter shapes, letter heights and widths, stroke weights, letter spacing, "
    "line breaks and overall width-to-height ratio as the original. Do not stretch, condense, "
    "enlarge individual letters, or change the font. Render it as a solid pure white (#FFFFFF) "
    "shape on a solid pure black (#000000) background, flat colors only: no gradients, shadows, "
    "glows, outlines, textures or artwork. Leave out taglines, credits, names and any other "
    "text. Center the logo and scale the whole logo uniformly so it fits comfortably inside the "
    "frame with black margin around it. Return only the image."
)
# The detailed prompts sometimes make Gemini balk; these plain ones are offered as a one-click swap.
SIMPLE_REMOVE_TEXT_PROMPT = "Remove text from this image"
SIMPLE_EXTRACT_LOGO_PROMPT = ("Pull the logo from this image. Make it a solid white logo with no 3d effects "
                              "and with a solid black background.")
OPERATIONS = ("remove_text", "extract_logo")
PROMPTS = {"remove_text": REMOVE_TEXT_PROMPT, "extract_logo": EXTRACT_LOGO_PROMPT}
SIMPLE_PROMPTS = {"remove_text": SIMPLE_REMOVE_TEXT_PROMPT, "extract_logo": SIMPLE_EXTRACT_LOGO_PROMPT}

# Make Logo post-processing: brightness becomes alpha. Below the floor is clear (JPEG noise in
# the black), above the ceiling is solid white; only clearly lit pixels decide the trim box so
# stray specks can't widen it.
_ALPHA_FLOOR, _ALPHA_CEIL, _TRIM_LEVEL = 24, 232, 96
_ALPHA_LUT = [0 if v < _ALPHA_FLOOR else 255 if v > _ALPHA_CEIL
              else round((v - _ALPHA_FLOOR) * 255 / (_ALPHA_CEIL - _ALPHA_FLOOR)) for v in range(256)]


class GeminiError(Exception):
    """Any Gemini failure worth surfacing to the user. The API layer maps this to an HTTP error."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def get_gemini_key(db) -> str:
    """The user's Gemini API key from Settings → General → API Keys ('' when unset)."""
    setting = get_setting(db, "gemini_api_key")
    return str(setting.value or "").strip() if setting else ""


def _ratio(spec: str) -> float:
    w, h = spec.split(":")
    return int(w) / int(h)


def aspect_ratio_for(width: int, height: int) -> str:
    """The supported ratio nearest the source's, compared on a log scale so 2:3 and 3:2 mirror."""
    target = math.log(width / height)
    return min(ASPECT_RATIOS, key=lambda spec: abs(math.log(_ratio(spec)) - target))


def _open_source(data: bytes) -> Image.Image:
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        return im
    except Exception as exc:
        raise GeminiError(f"Could not read the source image: {exc}", status=400)


def _upload_payload(data: bytes, im: Image.Image) -> tuple[str, str]:
    """(base64, mime) for the request; formats Gemini can't take go up as PNG."""
    mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(im.format or "")
    if mime:
        return base64.b64encode(data).decode("ascii"), mime
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii"), "image/png"


def _image_from_interaction(body: dict[str, Any]) -> tuple[bytes | None, str]:
    """(last image's bytes or None, any text the model wrote) — what the SDK's output_image does."""
    image: bytes | None = None
    texts: list[str] = []
    for step in body.get("steps") or []:
        if step.get("type") != "model_output":
            continue
        for block in step.get("content") or []:
            if block.get("type") == "image" and block.get("data"):
                image = base64.b64decode(block["data"])
            elif block.get("type") == "text" and block.get("text"):
                texts.append(str(block["text"]).strip())
    return image, " ".join(t for t in texts if t)


def _cover_fit(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale to cover the source size, then center-crop: the snapped ratio can differ from the
    source's by a few percent, and a plain resize would squash the art."""
    sw, sh = size
    if im.size == size:
        return im
    scale = max(sw / im.width, sh / im.height)
    im = im.resize((max(sw, round(im.width * scale)), max(sh, round(im.height * scale))),
                   Image.Resampling.LANCZOS)
    left = (im.width - sw) // 2
    top = (im.height - sh) // 2
    return im.crop((left, top, left + sw, top + sh))


def white_on_black_to_logo(im: Image.Image) -> Image.Image | None:
    """White lettering on black -> white-on-transparent RGBA, trimmed to the mark. A mostly bright
    frame means Gemini inverted the colors, so it is flipped first. None when nothing is lit."""
    lum = im.convert("L")
    if sum(lum.histogram()[128:]) > lum.width * lum.height / 2:
        lum = ImageOps.invert(lum)
    alpha = lum.point(_ALPHA_LUT)
    bbox = alpha.point(lambda v: 255 if v >= _TRIM_LEVEL else 0).getbbox()
    if not bbox:
        return None
    alpha = alpha.crop((max(0, bbox[0] - LOGO_FRINGE), max(0, bbox[1] - LOGO_FRINGE),
                        min(alpha.width, bbox[2] + LOGO_FRINGE), min(alpha.height, bbox[3] + LOGO_FRINGE)))
    white = Image.new("L", alpha.size, 255)
    return Image.merge("RGBA", (white, white, white, alpha))


def _status_for(code: int) -> int:
    if code in (400, 401, 403):
        return 400
    if code in (402, 429):
        return code
    return 502


def _error_message(body: Any) -> str:
    """Google's error message; some replies wrap the object in a one-element list."""
    if isinstance(body, list):
        body = body[0] if body else {}
    err = body.get("error") if isinstance(body, dict) else None
    return str(err.get("message") or "") if isinstance(err, dict) else ""


def _key_rejected(resp: requests.Response) -> bool:
    """Google answers a bad key with 400 API_KEY_INVALID; 401/403 cover other credential failures."""
    if resp.status_code in (401, 403):
        return True
    if resp.status_code != 400:
        return False
    try:
        body = resp.json()
    except ValueError:
        return False
    if isinstance(body, list):
        body = body[0] if body else {}
    err = body.get("error") if isinstance(body, dict) else None
    details = err.get("details") if isinstance(err, dict) else None
    return any(isinstance(d, dict) and d.get("reason") == "API_KEY_INVALID" for d in (details or []))


def _http_failure(resp: requests.Response) -> str:
    message = ""
    try:
        message = _error_message(resp.json())
    except ValueError:
        pass
    tail = f": {message}" if message else ""
    if _key_rejected(resp):
        return f"Gemini rejected the API key (HTTP {resp.status_code}){tail}"
    if resp.status_code == 402:
        return f"Gemini billing problem (HTTP 402){tail}"
    if resp.status_code == 429:
        return f"Gemini rate limit or quota hit (HTTP 429){tail}"
    return f"Gemini returned HTTP {resp.status_code}{tail}"


def _generate(data: bytes, *, api_key: str, prompt: str, model: str, image_size: str,
              aspect_ratio: str | None = None) -> tuple[Image.Image, dict[str, Any]]:
    """One Interactions call: source + prompt in, the decoded output image and its metadata out.
    aspect_ratio None snaps to the source's."""
    src = _open_source(data)
    b64, mime = _upload_payload(data, src)
    payload = {
        "model": model,
        "input": [
            {"type": "text", "text": prompt},
            {"type": "image", "mime_type": mime, "data": b64},
        ],
        "response_format": {"type": "image", "aspect_ratio": aspect_ratio or aspect_ratio_for(*src.size),
                            "image_size": image_size},
    }
    started = time.monotonic()
    try:
        resp = requests.post(GEMINI_API, json=payload, timeout=_TIMEOUT,
                             headers={"x-goog-api-key": api_key, "Content-Type": "application/json"})
    except requests.RequestException as exc:
        raise GeminiError(f"Gemini request failed: {exc}")
    elapsed = time.monotonic() - started
    if resp.status_code != 200:
        if _key_rejected(resp):
            warn_rejected_key("gemini", api_key)
        raise GeminiError(_http_failure(resp), status=_status_for(resp.status_code))
    try:
        body = resp.json()
    except ValueError:
        raise GeminiError("Gemini returned a non-JSON response")
    if not isinstance(body, dict):
        raise GeminiError("Gemini returned an unexpected response shape")
    if body.get("status") != "completed":
        detail = "; ".join(str(e.get("message")) for e in body.get("errors") or [] if e.get("message"))
        raise GeminiError(f"Gemini did not finish the image (status {body.get('status') or 'unknown'}"
                          f"{': ' + detail if detail else ''})")
    image, text = _image_from_interaction(body)
    if image is None:
        raise GeminiError("Gemini returned no image" + (f": {text[:300]}" if text else ""))
    try:
        out = Image.open(io.BytesIO(image))
        out.load()
    except Exception as exc:
        raise GeminiError(f"Gemini returned an unreadable image: {exc}")
    usage = body.get("usage") or {}
    meta = {
        "source_width": src.width, "source_height": src.height,
        "output_width": out.width, "output_height": out.height,
        "model": model, "image_size": image_size, "elapsed_seconds": round(elapsed, 1),
        "total_tokens": usage.get("total_tokens"), "text": text,
    }
    return out, meta


def _log(op: str, meta: dict[str, Any], size: tuple[int, int]) -> None:
    tokens = meta["total_tokens"] if meta["total_tokens"] is not None else "?"
    log_info(LogTags.API, f"Gemini {op}: {meta['model']} {meta['image_size']} "
                          f"{meta['source_width']}x{meta['source_height']} -> "
                          f"{meta['output_width']}x{meta['output_height']} native -> {size[0]}x{size[1]}, "
                          f"{meta['elapsed_seconds']}s, {tokens} tokens")


def remove_text(data: bytes, *, api_key: str, model: str = DEFAULT_MODEL, image_size: str = "2K",
                prompt: str | None = None, min_size: tuple[int, int] | None = None) -> dict[str, Any]:
    """The image without its text, as JPEG bytes at the source's size. A small source is scaled
    up (same aspect) so it is at least min_size: Gemini renders more pixels than a small source
    has, so there is no point handing them back."""
    out, meta = _generate(data, api_key=api_key, prompt=prompt or REMOVE_TEXT_PROMPT, model=model,
                          image_size=image_size)
    size = (meta["source_width"], meta["source_height"])
    if min_size:
        scale = max(1.0, min_size[0] / size[0], min_size[1] / size[1])
        size = (round(size[0] * scale), round(size[1] * scale))
    buf = io.BytesIO()
    _cover_fit(out.convert("RGB"), size).save(buf, "JPEG", quality=JPEG_QUALITY)
    _log("remove-text", meta, size)
    return {**meta, "image": buf.getvalue(), "mime_type": "image/jpeg", "width": size[0], "height": size[1]}


def extract_logo(data: bytes, *, api_key: str, model: str = DEFAULT_MODEL, image_size: str = "2K",
                 prompt: str | None = None) -> dict[str, Any]:
    """The title lettering as a trimmed white-on-transparent PNG."""
    out, meta = _generate(data, api_key=api_key, prompt=prompt or EXTRACT_LOGO_PROMPT, model=model,
                          image_size=image_size, aspect_ratio=LOGO_ASPECT_RATIO)
    logo = white_on_black_to_logo(out)
    if logo is None:
        raise GeminiError("Gemini returned a blank logo" + (f": {meta['text'][:300]}" if meta["text"] else ""))
    buf = io.BytesIO()
    logo.save(buf, "PNG")
    _log("make-logo", meta, logo.size)
    return {**meta, "image": buf.getvalue(), "mime_type": "image/png", "width": logo.width, "height": logo.height}
