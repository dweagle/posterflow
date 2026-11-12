"""Overlays page API: overlay types, config, live preview, fonts, and the apply job."""

import io
import json
import traceback
from urllib.parse import quote
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from core.job_queue import job_queue
from core.logging import LogTags, log_error, log_user_action
from models.job import JOB_TYPE_OVERLAYS, create_job, format_start_message
from models.setting import get_setting_value
from modules.overlays import run_overlays_background_job
from services.overlays.base import normalize_placement
from services.overlays.config import SETTING_TMDB_API_KEY, load_config, save_config
from services.overlays.registry import describe_types, get_type
from services.overlays.server import ServerOverlayEngine
from services.overlays.render import render_badges
from services.text_logo import list_fonts

router = APIRouter(prefix="/api/overlays", tags=["overlays"])


class OverlayPreviewLayer(BaseModel):
    """Another overlay type drawn under the one being edited, so the preview shows the whole
    composition (where the other badges sit) rather than one badge alone."""
    type: str
    variant: str = ""
    placement: Dict[str, Any] = {}
    options: Dict[str, Any] = {}


class OverlayPreviewRequest(BaseModel):
    type: str
    variant: str = ""
    enabled: bool = True  # False: the overlay being edited is switched off, so only the others are drawn
    placement: Dict[str, Any] = {}
    options: Dict[str, Any] = {}
    base: str = "plain"  # "plain" | "bordered" (sample poster through the saved border settings)
    others: List[OverlayPreviewLayer] = []


class OverlayRunRequest(BaseModel):
    dry_run: bool = False
    mode: Optional[str] = None


@router.get("/types")
def list_overlay_types() -> Dict[str, Any]:
    return {"types": describe_types()}


@router.get("/config")
def get_overlays_config(db: Session = Depends(get_db)) -> Dict[str, Any]:
    config = load_config(db)
    engine = ServerOverlayEngine(db, config)
    return {
        "config": config,
        "has_tmdb_key": bool((get_setting_value(db, SETTING_TMDB_API_KEY, "") or "").strip()),
        # Every enabled movie/show library the page can offer, grouped per media server.
        "library_configs": [
            {"instance_name": cfg.get("instance_name"),
             "libraries": [lib for lib in (cfg.get("libraries") or []) if isinstance(lib, dict) and lib.get("enabled") and str(lib.get("type")) in ("movie", "show")]}
            for cfg in engine.library_configs()
            if cfg.get("instance_name") in {i["name"] for i in engine.instances()}
        ],
        "badged_count": engine.badged_count(),
    }


def _queue_run(db: Session, mode: str, dry_run: bool = False) -> int:
    job = create_job(db, job_type=JOB_TYPE_OVERLAYS, message=format_start_message("overlays", dry_run=dry_run, qualifier=mode))
    job_queue.submit(run_overlays_background_job, job.id, job.id, dry_run, mode)
    return job.id


@router.post("/config")
def save_overlays_config(payload: Dict[str, Any], db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Save the settings. Switching overlays off does not touch the servers by itself — the
    Remove overlays button (or the workflow/schedule) uploads the clean posters again."""
    config = save_config(db, payload)
    enabled = [k for k, v in config["types"].items() if v.get("enabled")]
    log_user_action(f"Saved overlay settings (enabled: {'on' if config['enabled'] else 'off'}; types: {', '.join(enabled) or 'none'})")
    return {"success": True, "config": config, "badged_count": ServerOverlayEngine(db, config).badged_count()}


def _font_weights(font_id: str) -> List[str]:
    """Named instances of a variable font (e.g. Regular, Bold); [] for a static font."""
    from PIL import ImageFont
    from services.text_logo import _resolve_font

    path = _resolve_font([font_id])
    if path is None:
        return []
    try:
        names = ImageFont.truetype(str(path), 24).get_variation_names()
    except OSError:
        return []  # not a variable font
    except Exception:
        return []
    return [n.decode() if isinstance(n, bytes) else str(n) for n in names]


@router.get("/fonts")
def list_overlay_fonts() -> Dict[str, List[dict]]:
    """Fonts for the placement picker, each with its variable-font weights (empty = static,
    so the page disables the weight control)."""
    return {"fonts": [{**f, "weights": _font_weights(f["id"])} for f in list_fonts()]}


_IMAGE_MAX_BYTES = 5 * 1024 * 1024
_IMAGE_MAX_SIDE = 2000


def _image_type(type_key: str):
    """An overlay type that draws replaceable PNG badges, with its image library."""
    overlay_type = get_type(type_key)
    if overlay_type is None or overlay_type.images is None:
        raise HTTPException(status_code=404, detail="This overlay type has no image files")
    return overlay_type


def _image_label(overlay_type, key: str) -> str:
    labels = dict(overlay_type.image_items())
    if key not in labels:
        raise HTTPException(status_code=404, detail=f"Unknown {overlay_type.images.noun}")
    return labels[key]


def _image_set(overlay_type, image_set: Optional[str]) -> str:
    keys = [s for s, _l in overlay_type.images.sets]
    if not image_set:
        return overlay_type.images.default_set
    if image_set not in keys:
        raise HTTPException(status_code=400, detail=f"Unknown image set ({' or '.join(keys)})")
    return image_set


@router.get("/images/{type_key}")
def list_overlay_images(type_key: str, set: Optional[str] = None) -> Dict[str, Any]:
    """A type's image badges and where each one's file in a set comes from: custom (uploaded),
    bundled, or fallback (the set has none for it, so the default set's file is drawn). With no
    set named, the type's default set is listed."""
    overlay_type = _image_type(type_key)
    library, image_set = overlay_type.images, _image_set(overlay_type, set)
    return {
        "type": overlay_type.key,
        "set": image_set,
        "set_label": library.set_label(image_set),
        "fallback_label": library.set_label(library.default_set),
        "noun": library.noun,
        "folder": str(library.config_dir / image_set),
        "images": [
            {"key": key, "label": label, "source": source, "custom": source == "custom"}
            for key, label in overlay_type.image_items()
            for source in [overlay_type.image_source(key, image_set)]
        ],
    }


@router.get("/images/{type_key}/{key}")
def get_overlay_image(type_key: str, key: str, set: Optional[str] = None) -> Response:
    """The badge as it draws right now in a set: the custom file when uploaded, else the bundled
    one, else one the type joins from parts, else the default set's."""
    overlay_type = _image_type(type_key)
    _image_label(overlay_type, key)
    image_set = _image_set(overlay_type, set)
    png = overlay_type.image_png(key, image_set)
    if png is not None:
        return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})
    path = overlay_type.images.path(key, image_set)
    if path is None:
        raise HTTPException(status_code=404, detail=f"No {overlay_type.images.noun} for this key")
    return FileResponse(str(path), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/images/{type_key}/{key}")
def upload_overlay_image(type_key: str, key: str, set: Optional[str] = None, file: UploadFile = File(...)) -> Dict[str, Any]:
    """Replace a badge's file in a set with an uploaded image: fitted onto the type's frame and
    stored as a PNG with transparency. A plain def: the decode runs in the threadpool, not on
    the event loop."""
    from PIL import Image, UnidentifiedImageError

    overlay_type = _image_type(type_key)
    library, label, image_set = overlay_type.images, _image_label(overlay_type, key), _image_set(overlay_type, set)
    raw = file.file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="The file is empty")
    if len(raw) > _IMAGE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Image files must be 5 MB or smaller")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            width, height = img.size  # from the header: nothing is decoded until the size has passed
            if width > _IMAGE_MAX_SIDE or height > _IMAGE_MAX_SIDE:
                raise HTTPException(status_code=400, detail=f"Images must be at most {_IMAGE_MAX_SIDE} px on a side")
            img.load()
            fitted = library.fit(img, key, image_set)
    except UnidentifiedImageError:
        raise HTTPException(status_code=400, detail="That file is not an image (use a PNG with a transparent background)")
    target = library.custom_file(key, image_set)
    target.parent.mkdir(parents=True, exist_ok=True)
    fitted.save(target, format="PNG")
    log_user_action(f"Uploaded a custom {label} {overlay_type.label.lower()} {library.noun}, {library.set_label(image_set).lower()} set ({width}x{height}, {file.filename or 'file'})")
    return {"success": True, "type": overlay_type.key, "key": key, "set": image_set, "custom": True, "width": width, "height": height}


@router.delete("/images/{type_key}/{key}")
def delete_overlay_image(type_key: str, key: str, set: Optional[str] = None) -> Dict[str, Any]:
    """Drop a badge's custom file in a set so the bundled one is drawn again."""
    overlay_type = _image_type(type_key)
    library, label, image_set = overlay_type.images, _image_label(overlay_type, key), _image_set(overlay_type, set)
    target = library.custom_file(key, image_set)
    if target.is_file():
        target.unlink()
        log_user_action(f"Removed the custom {label} {overlay_type.label.lower()} {library.noun}, {library.set_label(image_set).lower()} set")
    return {"success": True, "type": overlay_type.key, "key": key, "set": image_set, "custom": False}


def _apply_saved_borders(db: Session, base):
    """The sample poster after the user's saved main-poster border settings (holiday
    schedule included), following the same decisions as the border pass: remove, replace,
    or leave untouched when nothing renderable is configured."""
    from services.border_replacer import (
        BorderReplacerService,
        _hex_to_rgb,
        _render_bordered_image,
        _strip_existing_border,
        build_border_run_settings,
        style_band_width,
    )

    rs = build_border_run_settings(db, run_type="manual")
    width = int(rs["border_width"] or 26)
    if rs["remove_borders"]:
        return _strip_existing_border(base, width, exclude=False).resize((1000, 1500)).convert("RGB")
    is_holiday, _name, colors, holiday_style = BorderReplacerService(db)._resolve_effective_border_colors(rs["border_colors"] or [])
    style_opts = dict(holiday_style) if is_holiday and holiday_style else dict(rs["style_opts"] or {})
    style = style_opts.get("style")
    renderable = (
        (style == "image" and bool(style_opts.get("overlay_path")))
        or (style == "gradient" and any(str(c).strip() for c in style_opts.get("gradient_colors") or []))
        or style == "remove"
    )
    if not colors and not renderable:
        return base
    return _render_bordered_image(base, style_band_width(style_opts, width), _hex_to_rgb(colors[0] if colors else "#000000"), style_opts)


@router.post("/preview")
def overlay_preview(payload: OverlayPreviewRequest, db: Session = Depends(get_db)) -> Response:
    """Render the sample poster with one overlay state. The badge rectangle (1000x1500
    space) comes back in X-Overlay-Rect so the page can offer a drag handle; the other layers'
    rectangles come back in X-Overlay-Layers, so those badges can be dragged too."""
    from api.poster_manager import _load_preview_base

    otype = get_type(payload.type)
    if otype is None:
        raise HTTPException(status_code=404, detail="Unknown overlay type")
    placement = normalize_placement(payload.placement, otype.default_placement())
    options = otype.normalize_options(payload.options)
    variants = otype.variants(options)
    variant = payload.variant or (variants[0]["key"] if variants else "")
    badge = otype.resolve_variant(variant, options) if payload.enabled else None

    base = _load_preview_base(db)
    rect = (0, 0, 0, 0)
    try:
        if payload.base == "bordered":
            base = _apply_saved_borders(db, base)
        # The other overlays under the edited one, then the edited one on top; the edge band once for all.
        layers = []
        drawn: List[str] = []  # the other layers' types, in the order their badges are drawn
        for layer in payload.others:
            other = get_type(layer.type)
            if other is None or other.key == otype.key:
                continue
            o_placement = normalize_placement(layer.placement, other.default_placement())
            o_options = other.normalize_options(layer.options)
            o_variants = other.variants(o_options)
            o_badge = other.resolve_variant(layer.variant or (o_variants[0]["key"] if o_variants else ""), o_options)
            if o_badge is not None:
                layers.append((o_badge, o_placement))
                drawn.append(other.key)
        if badge is not None:
            layers.append((badge, placement))
        base, rects = render_badges(base, layers)
        if badge is not None:
            rect = rects[-1]
        others_drawn = [{"type": key, "rect": [int(v) for v in r]} for key, r in zip(drawn, rects)]
    except Exception as exc:
        log_error(LogTags.OVERLAYS, f"Overlay preview failed: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Preview failed: {exc}")

    buf = io.BytesIO()
    base.save(buf, format="PNG")
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Overlay-Rect": ",".join(str(v) for v in rect),
            "X-Overlay-Layers": json.dumps(others_drawn, separators=(",", ":")),
            # Percent-encoded: a header carries plain ASCII only, and a badge's text may be anything.
            "X-Overlay-Text": quote(badge.text if badge else "", safe=" ,/:+()'"),
        },
    )


@router.post("/run")
def run_overlays(payload: Optional[OverlayRunRequest] = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Queue an overlays run: badge the posters on the configured media servers."""
    dry_run = bool(payload.dry_run) if payload else False
    mode = (payload.mode if payload and payload.mode in ("full", "incremental", "rebuild", "restore") else None) or "incremental"
    config = load_config(db)
    active = config.get("enabled") and any(t.get("enabled") for t in config["types"].values())
    badged = ServerOverlayEngine(db, config).badged_count()
    if mode == "restore" and badged == 0:
        raise HTTPException(status_code=400, detail="No item carries a badge. Nothing to remove.")
    if mode != "restore" and not active and badged == 0:
        raise HTTPException(status_code=400, detail="Enable overlays and save before running. Nothing is badged to remove.")
    job_id = _queue_run(db, mode, dry_run)
    log_user_action(f"Queued overlays run (job {job_id}, mode: {mode}{', dry run' if dry_run else ''})")
    return {"success": True, "job_id": job_id, "message": "Overlays run queued. Monitor progress via job status."}
