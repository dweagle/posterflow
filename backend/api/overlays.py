"""Overlays page API: overlay types, config, live preview, fonts, and the apply job."""

import io
import json
import re
import traceback
from urllib.parse import quote
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from core.job_queue import job_queue
from core.logging import LogTags, log_error, log_user_action
from models.job import JOB_TYPE_OVERLAYS, create_job, format_start_message
from models.setting import get_setting_value
from modules.overlays import run_overlays_background_job
from services.overlays import images as overlay_images, sharing
from services.overlays.base import normalize_placement
from services.overlays.config import SETTING_TMDB_API_KEY, load_config, save_config
from services.overlays.lookups import rule_values
from services.overlays.registry import describe_types, get_type
from services.overlays.server import ServerOverlayEngine
from services.overlays.render import join_lines, render_badges
from services.overlays.types.custom import KEY as CUSTOM_KEY, badge_file, prune_badges, store_badge
from services.poster_borders import HOLIDAY, RULE, border_choices, match_key
from services.text_logo import list_fonts

router = APIRouter(prefix="/api/overlays", tags=["overlays"])


class OverlayPreviewLayer(BaseModel):
    """Another overlay type drawn under the one being edited, so the preview shows the whole
    composition (where the other badges sit) rather than one badge alone."""
    type: str
    variant: str = ""
    placement: Dict[str, Any] = {}
    options: Dict[str, Any] = {}
    id: str = ""  # tells the layers of one type apart (several custom overlays); comes back with the layer's rectangle


class OverlayPreviewRequest(BaseModel):
    type: str
    variant: str = ""
    enabled: bool = True  # False: the overlay being edited is switched off, so only the others are drawn
    placement: Dict[str, Any] = {}
    options: Dict[str, Any] = {}
    base: str = "plain"  # "plain" | "bordered" (sample poster through the saved border settings)
    border: str = ""  # with "bordered": the holiday or border rule whose border the sample gets ("holiday:<name>" / "rule:<name>")
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
        # The holidays and border rules of the Border Replacer an overlay can be linked to.
        "borders": border_choices(db),
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
    prune_badges(config["types"].get(CUSTOM_KEY, {}).get("overlays") or [])
    enabled = [k for k, v in config["types"].items() if v.get("enabled")]
    log_user_action(f"Saved overlay settings (enabled: {'on' if config['enabled'] else 'off'}; types: {', '.join(enabled) or 'none'})")
    return {"success": True, "config": config, "badged_count": ServerOverlayEngine(db, config).badged_count()}


@router.get("/rule-values/{field}")
def get_rule_values(field: str, libraries: Optional[str] = None, q: Optional[str] = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The values there are to pick from for a condition's field: the Arrs' tags, what the
    libraries named ("instance:key", comma separated) hold, TMDB's lists, or TMDB's keywords
    that fit `q`. A plain def: the sources are asked in the threadpool."""
    found = rule_values(db, field, [lib for lib in (libraries or "").split(",") if lib], q or "")
    if found is None:
        raise HTTPException(status_code=404, detail="This condition takes a typed value")
    return {"field": field, **found}


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


_IMAGE_MAX_BYTES = overlay_images.MAX_UPLOAD_BYTES
_IMAGE_MAX_SIDE = overlay_images.MAX_UPLOAD_SIDE


def _read_upload(file: UploadFile):
    """An uploaded image, decoded: (image, width, height). Its size is checked from the header,
    before anything is decoded."""
    from PIL import Image, UnidentifiedImageError

    raw = file.file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="The file is empty")
    if len(raw) > _IMAGE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Image files must be 5 MB or smaller")
    try:
        img = Image.open(io.BytesIO(raw))
        width, height = img.size
        if width > _IMAGE_MAX_SIDE or height > _IMAGE_MAX_SIDE:
            raise HTTPException(status_code=400, detail=f"Images must be at most {_IMAGE_MAX_SIDE} px on a side")
        img.load()
    except UnidentifiedImageError:
        raise HTTPException(status_code=400, detail="That file is not an image (use a PNG with a transparent background)")
    return img, width, height


@router.post("/badges")
def upload_custom_badge(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Keep an image for a custom overlay and answer with its id, which the overlay's `image`
    option then holds. A plain def: the decode runs in the threadpool."""
    img, width, height = _read_upload(file)
    image_id, kept_width, kept_height = store_badge(img)
    log_user_action(f"Uploaded a custom overlay image ({width}x{height}, {file.filename or 'file'})")
    return {"success": True, "id": image_id, "width": kept_width, "height": kept_height}


@router.get("/badges/{image_id}")
def get_custom_badge(image_id: str) -> Response:
    path = badge_file(image_id)
    if path is None:
        raise HTTPException(status_code=404, detail="No image with this id")
    return FileResponse(str(path), media_type="image/png", headers={"Cache-Control": "no-store"})


def _app_version() -> str:
    try:
        from main import get_app_version

        return str(get_app_version() or "")
    except Exception:
        return ""


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


@router.get("/export/options")
def overlay_export_options() -> Dict[str, Any]:
    """What an export can carry beside the overlays: per type, how many images the user replaced."""
    return {"images": sharing.replaced_counts()}


@router.get("/export")
def export_overlays(overlays: str = "", images: str = "", db: Session = Depends(get_db)) -> Response:
    """The saved overlays named ("type:index", comma separated) as one zip, with their images.
    `images` names the types whose replaced images go along."""
    picks = []
    for part in overlays.split(","):
        key, _, index = part.strip().rpartition(":")
        if key and index.isdigit():
            picks.append((key, int(index)))
    try:
        data, names = sharing.build_export(load_config(db), picks, [t.strip() for t in images.split(",") if t.strip()], app=_app_version())
    except sharing.ExportError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    filename = f"posterflow-overlay-{_slug(names[0]) or 'export'}.zip" if len(names) == 1 else f"posterflow-overlays-{len(names)}.zip"
    log_user_action(f"Exported {len(names)} overlay{'s' if len(names) != 1 else ''} ({', '.join(names[:5])}{', ...' if len(names) > 5 else ''})")
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


def _open_import(file: UploadFile) -> sharing.OverlayImport:
    try:
        return sharing.open_import(file.file)
    except sharing.ExportError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/import/read")
def read_overlay_import(file: UploadFile = File(...)) -> Dict[str, Any]:
    """What an export holds, for the page to offer. Nothing is stored. A plain def: the file is
    read in the threadpool."""
    with _open_import(file) as found:
        return {"file": file.filename or "", **found.describe()}


@router.post("/import")
def import_overlays(file: UploadFile = File(...), overlays: str = Form(""), images: str = Form("")) -> Dict[str, Any]:
    """Bring in the overlays picked from an export (their index in the file, comma separated) and
    the images of the types named. The overlays come back switched off for the page to add: they
    are saved with the page. The images are stored now."""
    picks = [int(part) for part in overlays.split(",") if part.strip().isdigit()]
    with _open_import(file) as found:
        result = found.take(picks, [t.strip() for t in images.split(",") if t.strip()])
    count, stored = len(result["overlays"]), sum(result["images"].values())
    log_user_action(f"Imported {count} overlay{'s' if count != 1 else ''} from {file.filename or 'a file'}"
                    + (f", with {stored} image{'s' if stored != 1 else ''} for {', '.join(result['images'])}" if stored else ""))
    return {"success": True, **result}


def _image_type(type_key: str):
    """An overlay type that draws replaceable PNG badges, with its image library."""
    overlay_type = get_type(type_key)
    if overlay_type is None or overlay_type.images is None:
        raise HTTPException(status_code=404, detail="This overlay type has no image files")
    return overlay_type


def _image_label(overlay_type, key: str, image_set: str) -> str:
    labels = dict(overlay_type.image_items_in(image_set))
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
            for key, label in overlay_type.image_items_in(image_set)
            for source in [overlay_type.image_source(key, image_set)]
        ],
    }


@router.get("/images/{type_key}/{key}")
def get_overlay_image(type_key: str, key: str, set: Optional[str] = None) -> Response:
    """The badge as it draws right now in a set: the custom file when uploaded, else the bundled
    one, else one the type joins from parts, else the default set's."""
    overlay_type = _image_type(type_key)
    image_set = _image_set(overlay_type, set)
    _image_label(overlay_type, key, image_set)
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
    overlay_type = _image_type(type_key)
    library, image_set = overlay_type.images, _image_set(overlay_type, set)
    label = _image_label(overlay_type, key, image_set)
    img, width, height = _read_upload(file)
    fitted = library.fit(img, key, image_set)
    target = library.custom_file(key, image_set)
    target.parent.mkdir(parents=True, exist_ok=True)
    fitted.save(target, format="PNG")
    log_user_action(f"Uploaded a custom {label} {overlay_type.label.lower()} {library.noun}, {library.set_label(image_set).lower()} set ({width}x{height}, {file.filename or 'file'})")
    return {"success": True, "type": overlay_type.key, "key": key, "set": image_set, "custom": True, "width": width, "height": height}


@router.delete("/images/{type_key}/{key}")
def delete_overlay_image(type_key: str, key: str, set: Optional[str] = None) -> Dict[str, Any]:
    """Drop a badge's custom file in a set so the bundled one is drawn again."""
    overlay_type = _image_type(type_key)
    library, image_set = overlay_type.images, _image_set(overlay_type, set)
    label = _image_label(overlay_type, key, image_set)
    target = library.custom_file(key, image_set)
    if target.is_file():
        target.unlink()
        log_user_action(f"Removed the custom {label} {overlay_type.label.lower()} {library.noun}, {library.set_label(image_set).lower()} set")
    return {"success": True, "type": overlay_type.key, "key": key, "set": image_set, "custom": False}


def _apply_saved_borders(db: Session, base, border: str = ""):
    """The sample poster after the user's saved main-poster border settings (holiday
    schedule included), following the same decisions as the border pass: remove, replace,
    or leave untouched when nothing renderable is configured. `border` names a holiday or a
    border rule: the sample then gets that one's border, whatever the date."""
    from services.border_replacer import (
        BorderReplacerService,
        _hex_to_rgb,
        _render_bordered_image,
        _strip_existing_border,
        build_border_run_settings,
        style_band_width,
    )
    from services.plex_border_rules import load_plex_rules

    rs = build_border_run_settings(db, run_type="manual", plex_rules=False)  # the sample poster matches no rule
    width = int(rs["border_width"] or 26)
    kind, _, name = (part.strip() for part in match_key(border).partition(":"))
    rule = next((r for r in load_plex_rules(db) if match_key(r.name) == name), None) if kind == RULE else None
    if rule is not None:
        if rule.mode == "skip":
            return base
        return _render_bordered_image(base, style_band_width(rule.style_opts, width), _hex_to_rgb(rule.colors[0] if rule.colors else "#000000"), rule.style_opts)
    if rs["remove_borders"]:
        return _strip_existing_border(base, width, exclude=False).resize((1000, 1500)).convert("RGB")
    service = BorderReplacerService(db)
    named = next((h for h in service._load_holiday_schedules() if match_key(h.get("name")) == name), None) if kind == HOLIDAY else None
    if named is not None:
        is_holiday, colors, holiday_style = True, named.get("colors") or rs["border_colors"] or [], named.get("style_opts")
    else:
        is_holiday, _name, colors, holiday_style = service._resolve_effective_border_colors(rs["border_colors"] or [])
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
            base = _apply_saved_borders(db, base, payload.border)
        # The other overlays under the edited one, then the edited one on top; the edge band once for all.
        layers = []
        layer_ids: Dict[int, str] = {}  # id(badge) → the id its layer was sent with
        for layer in payload.others:
            other = get_type(layer.type)
            if other is None or (other.key == otype.key and not other.multi):
                continue
            o_placement = normalize_placement(layer.placement, other.default_placement())
            o_options = other.normalize_options(layer.options)
            o_variants = other.variants(o_options)
            o_badge = other.resolve_variant(layer.variant or (o_variants[0]["key"] if o_variants else ""), o_options)
            if o_badge is not None:
                layers.append((other.key, o_badge, o_placement))
                layer_ids[id(o_badge)] = layer.id or other.key
        if badge is not None:
            layers.append((otype.key, badge, placement))
        # A badge that shares another type's line has no rectangle of its own: that type's covers the line.
        layers = join_lines(layers)
        base, rects = render_badges(base, [(b, p) for _key, b, p in layers])
        # In the order the badges are drawn. A layer joined onto a line is a new badge: its type names it.
        drawn = [(layer_ids.get(id(b), "" if b is badge or key == otype.key else key), key, r) for (key, b, _p), r in zip(layers, rects)]
        rect = next((r for layer_id, _key, r in drawn if not layer_id), rect)
        others_drawn = [{"type": key, "id": layer_id, "rect": [int(v) for v in r]} for layer_id, key, r in drawn if layer_id]
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
