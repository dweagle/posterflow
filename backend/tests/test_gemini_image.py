"""Tests for services/gemini_image.py and the maker-tools Gemini image route."""
import base64
import io
from unittest.mock import patch

import pytest
import requests
from PIL import Image

import services.gemini_image as gi
import services.gemini_kept as gk
from models.setting import upsert_setting


@pytest.fixture(autouse=True)
def _kept_folder(tmp_path, monkeypatch):
    """Every route test parks results in its own folder."""
    monkeypatch.setattr(gk, "folder", lambda: tmp_path / "gemini")


def _png(size=(200, 300), color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _jpg(size=(200, 300)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (90, 10, 10)).save(buf, "JPEG")
    return buf.getvalue()


def _logo_frame(size=(1920, 1080), box=(400, 300, 1500, 700), fg=255, bg=0) -> bytes:
    """A flat 'logo' rectangle on a flat background, the shape Make Logo asks Gemini for."""
    im = Image.new("L", size, bg)
    im.paste(fg, box)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def _interaction(image, *, status="completed", text=None, errors=None) -> dict:
    content = []
    if text:
        content.append({"type": "text", "text": text})
    if image is not None:
        content.append({"type": "image", "mime_type": "image/png", "data": base64.b64encode(image).decode()})
    body = {"status": status, "steps": [{"type": "model_output", "content": content}], "usage": {"total_tokens": 1234}}
    if errors:
        body["errors"] = errors
    return body


class _Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def _spy(monkeypatch, body, calls: dict):
    def fake_post(url, json=None, timeout=None, headers=None):
        calls.update(url=url, json=json, headers=headers)
        return _Resp(200, body)
    monkeypatch.setattr(gi.requests, "post", fake_post)


def test_aspect_ratio_snaps_to_nearest_supported():
    assert gi.aspect_ratio_for(2000, 3000) == "2:3"
    assert gi.aspect_ratio_for(1920, 1080) == "16:9"
    assert gi.aspect_ratio_for(1000, 1426) == "2:3"
    assert gi.aspect_ratio_for(1000, 1000) == "1:1"
    assert gi.aspect_ratio_for(3840, 2160) == "16:9"


# ---------------------------------------------------------------- remove_text


def test_remove_text_sends_interaction_and_fits_result_back(monkeypatch):
    calls = {}
    _spy(monkeypatch, _interaction(_png((1365, 2048), (0, 200, 0))), calls)
    result = gi.remove_text(_jpg((2000, 3000)), api_key="k", model="gemini-nano-banana-2.1", image_size="2K")

    assert calls["url"] == gi.GEMINI_API
    assert calls["headers"]["x-goog-api-key"] == "k"
    body = calls["json"]
    assert body["model"] == "gemini-nano-banana-2.1"
    assert body["input"][0] == {"type": "text", "text": gi.REMOVE_TEXT_PROMPT}
    assert body["input"][1]["type"] == "image" and body["input"][1]["mime_type"] == "image/jpeg"
    assert body["response_format"] == {"type": "image", "aspect_ratio": "2:3", "image_size": "2K"}

    out = Image.open(io.BytesIO(result["image"]))
    assert out.size == (2000, 3000) and out.format == "JPEG"
    assert result["mime_type"] == "image/jpeg"
    assert (result["width"], result["height"]) == (2000, 3000)
    assert (result["output_width"], result["output_height"]) == (1365, 2048)
    assert result["total_tokens"] == 1234


def test_remove_text_scales_small_posters_up_to_the_floor(monkeypatch):
    # 680x1000 is under 1000x1500; Gemini rendered 1365x2048, so the result is 1020x1500 (same aspect).
    monkeypatch.setattr(gi.requests, "post", lambda *a, **k: _Resp(200, _interaction(_png((1365, 2048)))))
    result = gi.remove_text(_jpg((680, 1000)), api_key="k", min_size=gi.POSTER_MIN_SIZE)
    assert (result["width"], result["height"]) == (1020, 1500)
    assert Image.open(io.BytesIO(result["image"])).size == (1020, 1500)
    # Already above the floor: untouched. No floor: the source size as before.
    assert gi.remove_text(_jpg((2000, 3000)), api_key="k", min_size=gi.POSTER_MIN_SIZE)["width"] == 2000
    assert gi.remove_text(_jpg((680, 1000)), api_key="k")["width"] == 680


def test_remove_text_uses_a_custom_prompt_when_given(monkeypatch):
    calls = {}
    _spy(monkeypatch, _interaction(_png()), calls)
    gi.remove_text(_png(), api_key="k", prompt="Take the title off.")
    assert calls["json"]["input"][0]["text"] == "Take the title off."


def test_remove_text_center_crops_when_the_ratio_differs(monkeypatch):
    # 1000x1426 snaps to 2:3 and Gemini answers 1024x1536: covered then cropped, never squashed.
    monkeypatch.setattr(gi.requests, "post", lambda *a, **k: _Resp(200, _interaction(_png((1024, 1536)))))
    result = gi.remove_text(_png((1000, 1426)), api_key="k")
    assert Image.open(io.BytesIO(result["image"])).size == (1000, 1426)


def test_remove_text_reencodes_unsupported_formats_as_png(monkeypatch):
    calls = {}
    _spy(monkeypatch, _interaction(_png()), calls)
    buf = io.BytesIO()
    Image.new("RGB", (40, 60), (1, 2, 3)).save(buf, "BMP")
    gi.remove_text(buf.getvalue(), api_key="k")
    assert calls["json"]["input"][1]["mime_type"] == "image/png"


# ---------------------------------------------------------------- extract_logo


def test_extract_logo_asks_for_a_wide_canvas_with_the_logo_prompt(monkeypatch):
    calls = {}
    _spy(monkeypatch, _interaction(_logo_frame()), calls)
    gi.extract_logo(_jpg((2000, 3000)), api_key="k", image_size="4K")
    body = calls["json"]
    assert body["input"][0]["text"] == gi.EXTRACT_LOGO_PROMPT
    assert body["response_format"] == {"type": "image", "aspect_ratio": "16:9", "image_size": "4K"}


def test_extract_logo_turns_black_transparent_and_trims(monkeypatch):
    monkeypatch.setattr(gi.requests, "post", lambda *a, **k: _Resp(200, _interaction(_logo_frame())))
    result = gi.extract_logo(_png(), api_key="k")
    assert result["mime_type"] == "image/png"
    logo = Image.open(io.BytesIO(result["image"]))
    assert logo.mode == "RGBA"
    # 1100x400 mark + a 2px fringe each side, nothing more
    assert (logo.width, logo.height) == (1104, 404) == (result["width"], result["height"])
    assert logo.getpixel((logo.width // 2, logo.height // 2)) == (255, 255, 255, 255)
    assert logo.getpixel((0, 0))[3] == 0
    assert (result["output_width"], result["output_height"]) == (1920, 1080)


def test_extract_logo_flips_an_inverted_render(monkeypatch):
    # Black lettering on white is the same mark; the mostly-bright frame triggers the flip.
    monkeypatch.setattr(gi.requests, "post", lambda *a, **k: _Resp(200, _interaction(_logo_frame(fg=0, bg=255))))
    logo = Image.open(io.BytesIO(gi.extract_logo(_png(), api_key="k")["image"]))
    assert (logo.width, logo.height) == (1104, 404)
    assert logo.getpixel((logo.width // 2, logo.height // 2)) == (255, 255, 255, 255)
    assert logo.getpixel((0, 0))[3] == 0


def test_extract_logo_ignores_dark_noise_and_rejects_blank_frames(monkeypatch):
    noisy = Image.new("L", (1920, 1080), 0)
    noisy.paste(255, (400, 300, 1500, 700))
    noisy.putpixel((10, 10), 60)          # a dim speck far from the mark must not widen the trim
    buf = io.BytesIO()
    noisy.convert("RGB").save(buf, "PNG")
    monkeypatch.setattr(gi.requests, "post", lambda *a, **k: _Resp(200, _interaction(buf.getvalue())))
    logo = Image.open(io.BytesIO(gi.extract_logo(_png(), api_key="k")["image"]))
    assert (logo.width, logo.height) == (1104, 404)

    monkeypatch.setattr(gi.requests, "post", lambda *a, **k: _Resp(200, _interaction(_png((1920, 1080), (0, 0, 0)))))
    with pytest.raises(gi.GeminiError, match="blank logo"):
        gi.extract_logo(_png(), api_key="k")


def test_white_on_black_to_logo_keeps_soft_edges():
    im = Image.new("L", (100, 50), 0)
    im.paste(255, (20, 10, 80, 40))
    im.paste(128, (20, 40, 80, 42))        # a half-lit edge row becomes half alpha
    logo = gi.white_on_black_to_logo(im.convert("RGB"))
    assert logo is not None
    edge_alpha = logo.getpixel((logo.width // 2, logo.height - 1 - gi.LOGO_FRINGE))[3]   # last row inside the fringe
    assert 100 < edge_alpha < 160


# ---------------------------------------------------------------- failures


@pytest.mark.parametrize("status_code,expected", [(401, 400), (403, 400), (429, 429), (500, 502)])
def test_maps_http_failures(monkeypatch, status_code, expected):
    monkeypatch.setattr(gi.requests, "post",
                        lambda *a, **k: _Resp(status_code, {"error": {"message": "nope", "status": "X"}}))
    with pytest.raises(gi.GeminiError) as exc:
        gi.remove_text(_png(), api_key="k")
    assert exc.value.status == expected
    assert "nope" in str(exc.value)


def test_reads_list_wrapped_errors(monkeypatch):
    # Google wraps some errors in a one-element list; the 402 "credits depleted" reply is one.
    body = [{"error": {"code": 402, "message": "Your prepayment credits are depleted.", "status": "RESOURCE_EXHAUSTED"}}]
    monkeypatch.setattr(gi.requests, "post", lambda *a, **k: _Resp(402, body))
    with pytest.raises(gi.GeminiError) as exc:
        gi.extract_logo(_png(), api_key="k")
    assert exc.value.status == 402
    assert str(exc.value) == "Gemini billing problem (HTTP 402): Your prepayment credits are depleted."


def test_rejects_non_object_success_bodies(monkeypatch):
    monkeypatch.setattr(gi.requests, "post", lambda *a, **k: _Resp(200, [{"status": "completed"}]))
    with pytest.raises(gi.GeminiError, match="unexpected response shape"):
        gi.remove_text(_png(), api_key="k")


def test_surfaces_refusals_and_failed_status(monkeypatch):
    monkeypatch.setattr(gi.requests, "post",
                        lambda *a, **k: _Resp(200, _interaction(None, text="I can't edit this image.")))
    with pytest.raises(gi.GeminiError, match="returned no image: I can't edit this image."):
        gi.remove_text(_png(), api_key="k")

    monkeypatch.setattr(gi.requests, "post", lambda *a, **k: _Resp(
        200, _interaction(None, status="failed", errors=[{"code": "x", "message": "boom"}])))
    with pytest.raises(gi.GeminiError, match="status failed: boom"):
        gi.remove_text(_png(), api_key="k")


def test_wraps_transport_errors(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(gi.requests, "post", boom)
    with pytest.raises(gi.GeminiError, match="down"):
        gi.remove_text(_png(), api_key="k")


def test_rejects_unreadable_source():
    with pytest.raises(gi.GeminiError) as exc:
        gi.remove_text(b"not an image", api_key="k")
    assert exc.value.status == 400


# ---------------------------------------------------------------- route


def _result(image: bytes, mime: str, width: int, height: int) -> dict:
    return {"image": image, "mime_type": mime, "width": width, "height": height,
            "output_width": 1365, "output_height": 2048, "model": "gemini-nano-banana-2.1",
            "image_size": "2K", "elapsed_seconds": 12.3, "total_tokens": 5, "text": "",
            "source_width": 20, "source_height": 30}


def test_route_prompts_endpoint_lists_both_operations(client):
    resp = client.get("/api/maker-tools/gemini-image/prompts")
    assert resp.status_code == 200
    assert resp.json() == {
        "remove_text": {"detailed": gi.REMOVE_TEXT_PROMPT, "simple": gi.SIMPLE_REMOVE_TEXT_PROMPT},
        "extract_logo": {"detailed": gi.EXTRACT_LOGO_PROMPT, "simple": gi.SIMPLE_EXTRACT_LOGO_PROMPT},
    }


def test_route_requires_key_and_valid_inputs(client, test_db):
    resp = client.post("/api/maker-tools/gemini-image", json={"path": "/p.jpg", "title": "X"})
    assert resp.status_code == 400 and "Gemini API key" in resp.json()["detail"]

    upsert_setting(test_db, "gemini_api_key", "k")
    test_db.commit()
    post = lambda **extra: client.post("/api/maker-tools/gemini-image", json={"path": "/p.jpg", **extra}).status_code
    assert post(path="/../p.jpg") == 400
    assert post(image_size="8K") == 400
    assert post(model="gemini-ultra") == 400
    assert post(operation="inpaint") == 400
    assert post(prompt="x" * (gi.MAX_PROMPT_CHARS + 1)) == 400


def test_route_returns_inline_jpeg_with_textless_name(client, test_db):
    upsert_setting(test_db, "gemini_api_key", "k")
    test_db.commit()
    jpeg = _jpg((20, 30))
    with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=_jpg((20, 30))), \
         patch("services.gemini_image.remove_text", return_value=_result(jpeg, "image/jpeg", 20, 30)) as rt:
        resp = client.post("/api/maker-tools/gemini-image", json={
            "path": "/p.jpg", "role": "poster", "title": "Fight Club", "year": 1999, "tmdb_id": 550,
            "media_type": "movie", "image_size": "2k", "prompt": "  "})
    assert resp.status_code == 200
    data = resp.json()
    assert data["operation"] == "remove_text"
    assert base64.b64decode(data["image_base64"]) == jpeg
    assert data["mime_type"] == "image/jpeg"
    assert data["filename"] == "Fight Club (1999) {tmdb-550} - textless.jpg"
    assert data["output_width"] == 1365 and data["elapsed_seconds"] == 12.3
    assert rt.call_args.kwargs == {"api_key": "k", "model": "gemini-nano-banana-2.1", "image_size": "2K",
                                   "prompt": None, "min_size": gi.POSTER_MIN_SIZE}


def test_route_makes_logos_with_the_canonical_logo_name(client, test_db):
    upsert_setting(test_db, "gemini_api_key", "k")
    test_db.commit()
    png = _png((10, 4))
    with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=_jpg()), \
         patch("services.gemini_image.extract_logo", return_value=_result(png, "image/png", 1166, 424)) as el:
        resp = client.post("/api/maker-tools/gemini-image", json={
            "path": "/p.jpg", "operation": "extract_logo", "role": "poster", "season": 2, "prompt": "White on black.",
            "title": "Dune", "year": 2021, "tmdb_id": 438631, "media_type": "movie"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["operation"] == "extract_logo"
    assert data["mime_type"] == "image/png"
    assert base64.b64decode(data["image_base64"]) == png
    assert (data["width"], data["height"]) == (1166, 424)
    # logos are per title: no season part, canonical " - logo.png"
    assert data["filename"] == "Dune (2021) {tmdb-438631} - logo.png"
    assert el.call_args.kwargs["prompt"] == "White on black."


def test_route_names_backdrops_like_gallery_downloads(client, test_db):
    upsert_setting(test_db, "gemini_api_key", "k")
    test_db.commit()
    with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=_jpg()), \
         patch("services.gemini_image.remove_text", return_value=_result(_jpg(), "image/jpeg", 200, 300)) as rt:
        resp = client.post("/api/maker-tools/gemini-image", json={
            "path": "/b.jpg", "role": "backdrop", "title": "Dune", "year": 2021, "tmdb_id": 438631, "media_type": "movie"})
    assert resp.status_code == 200
    assert resp.json()["filename"] == "Dune (2021) {tmdb-438631} - background - textless.jpg"
    assert rt.call_args.kwargs["min_size"] is None   # the poster floor is for posters only


def test_route_turns_unexpected_crashes_into_502(client, test_db):
    upsert_setting(test_db, "gemini_api_key", "k")
    test_db.commit()
    with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=_jpg()), \
         patch("services.gemini_image.remove_text", side_effect=AttributeError("'list' object has no attribute 'get'")):
        resp = client.post("/api/maker-tools/gemini-image", json={"path": "/p.jpg", "title": "X"})
    assert resp.status_code == 502
    assert "Gemini call failed" in resp.json()["detail"]


def test_route_maps_gemini_errors(client, test_db):
    upsert_setting(test_db, "gemini_api_key", "k")
    test_db.commit()
    with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=_jpg()), \
         patch("services.gemini_image.extract_logo", side_effect=gi.GeminiError("quota", status=429)):
        resp = client.post("/api/maker-tools/gemini-image", json={"path": "/p.jpg", "operation": "extract_logo", "title": "X"})
    assert resp.status_code == 429 and resp.json()["detail"] == "quota"


def test_warns_once_about_an_invalid_key(monkeypatch):
    import services.rejected_keys as rk
    rk._logged_at.clear()
    lines = []
    monkeypatch.setattr(rk, "log_warning", lambda tag, msg, **kw: lines.append(msg))
    body = {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                      "status": "INVALID_ARGUMENT",
                      "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}]}}
    monkeypatch.setattr(gi.requests, "post", lambda *a, **k: _Resp(400, body))

    for _ in range(2):
        with pytest.raises(gi.GeminiError) as exc:
            gi.remove_text(_png(), api_key="k")

    assert exc.value.status == 400
    assert str(exc.value).startswith("Gemini rejected the API key (HTTP 400)")
    assert len(lines) == 1
    assert lines[0].startswith("Gemini rejected the API key.")


# ---------------------------------------------------------------- kept results


def _kept_item_json(**extra):
    return {"path": "/p.jpg", "role": "poster", "title": "Fight Club", "year": 1999, "tmdb_id": 550,
            "media_type": "movie", "source": "tvdb", "season": 2, **extra}


def test_route_parks_the_result_and_keep_makes_it_a_gallery_tile(client, test_db):
    upsert_setting(test_db, "gemini_api_key", "k")
    test_db.commit()
    jpeg = _jpg((20, 30))
    with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=_jpg()), \
         patch("services.gemini_image.remove_text", return_value=_result(jpeg, "image/jpeg", 20, 30)):
        data = client.post("/api/maker-tools/gemini-image", json=_kept_item_json()).json()
    ref = data["ref"]
    assert ref.startswith("gemini:tmdb-550/poster-") and ref.endswith(".jpg")
    assert data["file_url"] == gk.file_url(ref)
    assert gk.read_ref(ref) == jpeg
    # parked only: nothing listed until Keep
    assert client.get("/api/maker-tools/gemini-image/kept", params={"tmdb_id": 550, "title": "Fight Club"}).json() == []

    tile = client.post("/api/maker-tools/gemini-image/keep", json={"ref": ref}).json()
    assert tile["ref"] == ref and tile["role"] == "poster" and tile["source"] == "tvdb" and tile["season"] == 2
    # a season-tab source names the download like a season poster download would
    assert tile["width"] == 20 and tile["download_name"] == "Fight Club (1999) {tmdb-550} - Season 2 - textless.jpg"
    listed = client.get("/api/maker-tools/gemini-image/kept", params={"tmdb_id": 550, "title": "Fight Club"}).json()
    assert [t["ref"] for t in listed] == [ref]

    served = client.get("/api/maker-tools/gemini-image/file", params={"ref": ref})
    assert served.status_code == 200 and served.headers["content-type"] == "image/jpeg" and served.content == jpeg
    named = client.get("/api/maker-tools/gemini-image/file", params={"ref": ref, "download": 1})
    assert "textless.jpg" in named.headers["content-disposition"]

    assert client.delete("/api/maker-tools/gemini-image/kept", params={"ref": ref}).json() == {"deleted": True}
    assert client.get("/api/maker-tools/gemini-image/kept", params={"tmdb_id": 550, "title": "Fight Club"}).json() == []
    assert client.get("/api/maker-tools/gemini-image/file", params={"ref": ref}).status_code == 404


def test_route_logo_results_park_as_png_without_a_season(client, test_db):
    upsert_setting(test_db, "gemini_api_key", "k")
    test_db.commit()
    with patch("api.maker_tools._fetch_tmdb_image_bytes", return_value=_jpg()), \
         patch("services.gemini_image.extract_logo", return_value=_result(_png((10, 4)), "image/png", 10, 4)):
        data = client.post("/api/maker-tools/gemini-image", json=_kept_item_json(operation="extract_logo")).json()
    assert data["ref"].startswith("gemini:tmdb-550/logo-") and data["ref"].endswith(".png")
    tile = client.post("/api/maker-tools/gemini-image/keep", json={"ref": data["ref"]}).json()
    assert tile["role"] == "logo" and tile["season"] is None


def test_route_keep_and_delete_of_unknown_refs(client):
    assert client.post("/api/maker-tools/gemini-image/keep", json={"ref": "gemini:tmdb-1/poster-aaaaaaaaaaaa.jpg"}).status_code == 404
    assert client.post("/api/maker-tools/gemini-image/keep", json={"ref": "/p.jpg"}).status_code == 404
    assert client.delete("/api/maker-tools/gemini-image/kept", params={"ref": "gemini:tmdb-1/poster-aaaaaaaaaaaa.jpg"}).json() == {"deleted": False}
    assert client.get("/api/maker-tools/gemini-image/file", params={"ref": "gemini:../x/poster-aaaaaaaaaaaa.jpg"}).status_code == 404


def _keep_poster(data=b"\xff\xd8kept"):
    ref = gk.save_pending(data, ext="jpg", key="tmdb-550", meta={"role": "poster", "source": "tmdb", "season": None,
                                                                 "operation": "remove_text", "width": 1, "height": 1,
                                                                 "download_name": "x.jpg"})
    gk.keep(ref)
    return ref


def test_psd_export_uses_kept_gemini_refs_and_clears_them(client, test_db):
    upsert_setting(test_db, "tmdb_api_key", "k")
    test_db.commit()
    ref = _keep_poster()
    other = _keep_poster(b"\xff\xd8other")
    with patch("api.maker_tools._build_psd", return_value=b"PSD") as build:
        resp = client.post("/api/maker-tools/tmdb/psd-export", json={
            "title": "Fight Club", "year": "1999", "poster_paths": [ref], "poster_layer_names": ["main"]})
    assert resp.status_code == 200 and resp.content == b"PSD"
    assert build.call_args.args[0] == [b"\xff\xd8kept"]           # the kept file fed the poster layer
    assert resp.headers["x-gemini-cleared"] == "gemini%3Atmdb-550%2F" + ref.split("/")[1]
    assert not gk.is_valid_ref(ref) and gk.is_valid_ref(other)   # only the exported one is gone


def test_psd_export_to_a_folder_reports_cleared_refs(client, test_db, tmp_path):
    upsert_setting(test_db, "tmdb_api_key", "k")
    upsert_setting(test_db, "psd_export_folder", str(tmp_path / "psd"))
    test_db.commit()
    ref = _keep_poster()
    with patch("api.maker_tools._build_psd", return_value=b"PSD"):
        resp = client.post("/api/maker-tools/tmdb/psd-export", json={"title": "Fight Club", "year": "1999", "poster_paths": [ref]})
    assert resp.status_code == 200
    assert resp.json()["cleared_refs"] == [ref]
    assert not gk.is_valid_ref(ref)


def test_psd_export_rejects_a_gemini_ref_that_no_longer_exists(client, test_db):
    upsert_setting(test_db, "tmdb_api_key", "k")
    test_db.commit()
    resp = client.post("/api/maker-tools/tmdb/psd-export", json={
        "title": "Fight Club", "year": "1999", "poster_paths": ["gemini:tmdb-550/poster-aaaaaaaaaaaa.jpg"]})
    assert resp.status_code == 400 and "invalid image path" in resp.json()["detail"].lower()
