import io
import json
import zipfile

import pytest
from PIL import Image

from services.overlays import images, sharing
from services.overlays.config import normalize_config
from services.overlays.registry import get_type
from services.overlays.types import custom as custom_mod


def _png(size=(120, 60), box=(20, 10, 100, 50), color=(255, 200, 0, 255)):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    img.paste(color, box)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _stored(**kw):
    return custom_mod.store_badge(Image.open(io.BytesIO(_png(**kw))))[0]


def _row(n, name, **kw):
    return {"id": f"{n:08x}", "name": name, **kw}


def _config(**types):
    return normalize_config({"enabled": True, "types": {key: {"enabled": True, "overlays": overlays} for key, overlays in types.items()}})


def _zip(manifest, files=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if manifest is not None:
            zf.writestr(sharing.MANIFEST, manifest if isinstance(manifest, (str, bytes)) else json.dumps(manifest))
        for name, data in (files or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def _manifest(*overlays, **extra):
    return {"format": sharing.FORMAT, "version": sharing.VERSION, "overlays": list(overlays), **extra}


def _open(data):
    return sharing.open_import(io.BytesIO(data))


@pytest.fixture
def overlays_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "config_overlays_dir", lambda: tmp_path / "overlays")
    return tmp_path / "overlays"


def test_a_custom_overlay_travels_with_its_pictures_and_leaves_the_install_behind(overlays_dir, tmp_path, monkeypatch):
    ribbon, star = _stored(), _stored(color=(0, 90, 200, 255))
    config = _config(custom=[{
        "name": "Awards", "enabled": True, "libraries": ["plex:1"],
        "placement": {"horizontal_align": "left", "vertical_align": "top", "horizontal_offset": 0, "vertical_offset": 0, "back_enabled": False},
        "options": {"badge": "image", "image_width": 251, "movies": False, "group": "corner", "weight": 7, "badges": [
            _row(1, "Oscar", image=ribbon, weight=20, rules=[{"field": "label", "op": "is", "value": "Oscar", "instance": "My Plex", "also": [{"value": "Academy", "instance": "Den"}]}]),
            _row(2, "Globe", image=star, size=233, rules=[{"field": "sonarr_tag", "op": "is", "value": "globe", "instance": "sonarr-4k"}]),
            _row(3, "Oscar again", image=ribbon),
        ]},
    }])
    data, names = sharing.build_export(config, [("custom", 0), ("custom", 0), ("custom", 9), ("nope", 0)], app="1.2.3")
    assert names == ["Awards"]

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        manifest = json.loads(zf.read(sharing.MANIFEST))
        assert sorted(zf.namelist()) == sorted([sharing.MANIFEST, f"images/custom/{ribbon}.png", f"images/custom/{star}.png"])
    assert (manifest["format"], manifest["version"], manifest["app"]) == (sharing.FORMAT, 1, "1.2.3")
    (entry,) = manifest["overlays"]
    assert sorted(entry) == ["name", "options", "placement", "type"]  # no libraries, no switch
    text = json.dumps(manifest)
    assert "My Plex" not in text and "sonarr-4k" not in text and "Den" not in text and "plex:1" not in text

    # Another install: nothing of the first one's pictures is there.
    monkeypatch.setattr(images, "config_overlays_dir", lambda: tmp_path / "elsewhere")
    with _open(data) as found:
        seen = found.describe()
        assert seen["unknown"] == 0 and seen["images"] == []
        assert seen["overlays"] == [{"index": 0, "type": "custom", "type_label": "Custom", "name": "Awards", "badges": 3, "images": 2, "missing_images": 0, "notes": []}]
        assert not (tmp_path / "elsewhere").exists()  # looking stores nothing
        result = found.take([0])
    (got,) = result["overlays"]
    overlay = got["overlay"]
    assert got["type"] == "custom" and (overlay["name"], overlay["enabled"], overlay["all_libraries"], overlay["libraries"]) == ("Awards", False, True, [])
    assert (overlay["placement"]["horizontal_align"], overlay["placement"]["vertical_offset"], overlay["placement"]["back_enabled"]) == ("left", 0, False)
    options = overlay["options"]
    assert (options["badge"], options["image_width"], options["movies"], options["group"], options["weight"]) == ("image", 251, False, "corner", 7)
    oscar, globe, again = options["badges"]
    assert oscar["rules"] == [{"field": "label", "op": "is", "value": "Oscar", "also": [{"value": "Academy"}]}] and (oscar["weight"], globe["size"]) == (20, 233)
    assert globe["rules"] == [{"field": "sonarr_tag", "op": "is", "value": "globe"}]
    # New ids here, the same picture stored once, and the files are there.
    assert oscar["image"] == again["image"] != globe["image"] and {oscar["image"], globe["image"]}.isdisjoint({ribbon, star})
    assert sorted(p.stem for p in (tmp_path / "elsewhere" / "custom").glob("*.png")) == sorted([oscar["image"], globe["image"]])
    assert Image.open(custom_mod.badge_file(globe["image"])).size == (80, 40)
    # What came back is what the settings keep.
    assert normalize_config({"types": {"custom": {"overlays": [overlay]}}})["types"]["custom"]["overlays"][0] == overlay


def test_a_built_in_overlay_takes_the_replaced_images_only_when_asked(overlays_dir, tmp_path, monkeypatch):
    streaming = get_type("streaming")
    mine = streaming.images.custom_file("netflix", "white")
    mine.parent.mkdir(parents=True)
    mine.write_bytes(_png(size=(300, 100), box=(10, 10, 290, 90)))
    config = _config(streaming=[{"name": "Where to watch", "placement": {"font_size": 60}}], status=[{"name": "Status"}])
    assert sharing.replaced_counts() == {"streaming": 1}

    plain, _names = sharing.build_export(config, [("streaming", 0)])
    assert zipfile.ZipFile(io.BytesIO(plain)).namelist() == [sharing.MANIFEST]
    other, _names = sharing.build_export(config, [("status", 0)], ["streaming"])  # its type is not in the file
    assert zipfile.ZipFile(io.BytesIO(other)).namelist() == [sharing.MANIFEST]
    data, names = sharing.build_export(config, [("streaming", 0), ("status", 0)], ["streaming", "status", "nope"])
    assert names == ["Where to watch", "Status"]
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert "images/streaming/white/netflix.png" in zf.namelist() and json.loads(zf.read(sharing.MANIFEST))["images"] == {"streaming": [{"set": "white", "key": "netflix"}]}

    monkeypatch.setattr(images, "config_overlays_dir", lambda: tmp_path / "elsewhere")
    theirs = streaming.images.custom_file("netflix", "white")
    with _open(data) as found:
        seen = found.describe()
        assert [(o["type"], o["name"], o["badges"]) for o in seen["overlays"]] == [("streaming", "Where to watch", None), ("status", "Status", None)]
        assert seen["images"] == [{"type": "streaming", "label": "Streaming", "noun": "logo", "count": 1, "replaces": 0}]
        result = found.take([0, 1])  # the overlays without the images
        assert result["images"] == {} and not theirs.exists()
        assert [(o["type"], o["overlay"]["enabled"], o["overlay"]["placement"]["font_size"]) for o in result["overlays"]] == [("streaming", False, 60), ("status", False, get_type("status").default_placement()["font_size"])]
        theirs.parent.mkdir(parents=True)
        theirs.write_bytes(_png())
        assert found.describe()["images"][0]["replaces"] == 1
        result = found.take([], ["streaming", "status"])
    assert result == {"overlays": [], "images": {"streaming": 1}} and Image.open(theirs).size == (300, 100)  # fitted onto the type's frame


@pytest.mark.parametrize("data, says", [
    (b"not a zip at all", "not an overlay export"),
    (_zip(None, {"readme.txt": b"hello"}), "not an overlay export"),
    (_zip("{not json"), "not an overlay export"),
    (_zip({"format": "something-else", "version": 1, "overlays": []}), "not an overlay export"),
    (_zip({"format": sharing.FORMAT, "version": 1, "overlays": {}}), "not an overlay export"),
    (_zip({"format": sharing.FORMAT, "version": True, "overlays": []}), "not an overlay export"),
    (_zip({"format": sharing.FORMAT, "version": sharing.VERSION + 1, "overlays": []}), "newer Posterflow"),
    (_zip(_manifest(*[{"type": "status"}] * (sharing.MAX_OVERLAYS + 1))), f"more than {sharing.MAX_OVERLAYS} overlays"),
])
def test_what_is_not_an_export_is_refused_in_words(data, says):
    with pytest.raises(sharing.ExportError, match=says):
        _open(data)


def test_a_file_too_large_or_too_full_is_refused(monkeypatch):
    data = _zip(_manifest({"type": "status"}), {f"images/custom/{n:012x}.png": b"x" for n in range(6)})
    monkeypatch.setattr(sharing, "MAX_ENTRIES", 5)
    with pytest.raises(sharing.ExportError, match="too many entries"):
        _open(data)
    monkeypatch.setattr(sharing, "MAX_ENTRIES", 5000)
    monkeypatch.setattr(sharing, "MAX_FILE_BYTES", len(data) - 1)
    with pytest.raises(sharing.ExportError, match="larger than"):
        _open(data)
    monkeypatch.setattr(sharing, "MAX_FILE_BYTES", 64 * 1024 * 1024)
    monkeypatch.setattr(sharing, "MAX_MANIFEST_BYTES", 10)  # an overlay list past its limit is not read
    with pytest.raises(sharing.ExportError, match="not an overlay export"):
        _open(data)


def test_a_file_is_read_as_a_strangers(overlays_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(sharing, "MAX_BADGES", 3)
    monkeypatch.setattr(custom_mod, "MAX_BADGES", 3)
    good, huge, junk = "a" * 12, "b" * 12, "c" * 12
    rows = [
        _row(1, "Good", image=good, rules=[{"field": "title", "op": "regex", "value": "^(a+)+$"}, {"field": "label", "op": "is", "value": "x", "instance": "Their Plex"}]),
        _row(2, "Too big", image=huge, rules=[{"field": "id_list", "op": "is", "value": "http://192.168.1.1/ids.txt"}, {"field": "list", "op": "is", "value": "https://mdblist.com/lists/someone/a-list"}]),
        _row(3, "Not an image", image=junk),
        _row(4, "Past the limit", image="../../../etc/passwd"),
    ]
    manifest = _manifest(
        {"type": "custom", "name": "  Theirs  ", "enabled": True, "all_libraries": False, "libraries": ["their-plex:9"],
         "placement": {"font": "TheirOwnFont", "horizontal_offset": 99999}, "options": {"badge": "text", "badges": rows, "made_up": 1}},
        {"type": "from-the-future", "name": "Unknown"},
        "not even an object",
        {"type": "custom", "name": "", "options": "nonsense", "placement": []},
        images={"streaming": [{"set": "white", "key": "netflix"}, {"set": "white", "key": "../../evil"}, {"set": "nope", "key": "netflix"}, "junk"],
                "status": [{"set": "x", "key": "y"}], "nope": [{"set": "white", "key": "netflix"}]},
    )
    data = _zip(manifest, {
        f"images/custom/{good}.png": _png(),
        f"images/custom/{huge}.png": _png(size=(images.MAX_UPLOAD_SIDE + 1, 10), box=(0, 0, 5, 5)),
        f"images/custom/{junk}.png": b"this is not a picture",
        "images/streaming/white/netflix.png": _png(),
        "images/streaming/white/../../evil.png": _png(),
        "../outside.png": _png(),
        "/abs.png": _png(),
    })
    with _open(data) as found:
        seen = found.describe()
        result = found.take([0, 1, 2, 3, 99], ["streaming", "status", "nope"])
    assert seen["unknown"] == 2 and [o["index"] for o in seen["overlays"]] == [0, 3]
    first = seen["overlays"][0]
    assert (first["name"], first["badges"], first["images"], first["missing_images"]) == ("Theirs", 3, 3, 0)
    assert first["notes"] == [
        "Only the first 3 of its 4 badges are kept.",
        "1 condition uses a regex pattern. Check it before switching the overlay on.",
        "Its conditions read lists from 192.168.1.1, mdblist.com.",
        "The font TheirOwnFont is not installed here. The default font is drawn.",
    ]
    assert seen["images"] == [{"type": "streaming", "label": "Streaming", "noun": "logo", "count": 1, "replaces": 0}]

    assert [o["type"] for o in result["overlays"]] == ["custom", "custom"] and result["images"] == {"streaming": 1}
    theirs, empty = (o["overlay"] for o in result["overlays"])
    assert (theirs["name"], theirs["enabled"], theirs["all_libraries"], theirs["libraries"]) == ("Theirs", False, True, [])
    assert theirs["placement"]["horizontal_offset"] == 1000 and "made_up" not in theirs["options"]
    kept = theirs["options"]["badges"]
    assert [r["name"] for r in kept] == ["Good", "Too big", "Not an image"]
    assert custom_mod.badge_file(kept[0]["image"]) is not None and kept[0]["image"] != good and (kept[1]["image"], kept[2]["image"]) == ("", "")
    assert kept[0]["rules"][1] == {"field": "label", "op": "is", "value": "x"}
    assert (empty["name"], empty["enabled"]) == ("Custom", False) and len(empty["options"]["badges"]) == 1
    # Only what the overlay list implied was written, and only under the overlays folder.
    written = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())
    assert written == sorted([f"overlays/custom/{kept[0]['image']}.png", "overlays/streaming/white/netflix.png"])


def test_a_file_hands_over_no_more_image_bytes_than_an_export_holds(overlays_dir, monkeypatch):
    ids = [f"{n:012x}" for n in range(1, 4)]
    picture = _png()
    data = _zip(_manifest({"type": "custom", "name": "Three", "options": {"badge": "image", "badges": [_row(n, f"B{n}", image=i) for n, i in enumerate(ids, 1)]}}),
                {f"images/custom/{i}.png": picture for i in ids})
    monkeypatch.setattr(sharing, "MAX_FILE_BYTES", len(data) + 1)  # the file itself fits
    with _open(data) as found:
        found._room = 2 * len(picture)  # room for two of its three pictures
        rows = found.take([0])["overlays"][0]["overlay"]["options"]["badges"]
    assert [bool(r["image"]) for r in rows] == [True, True, False]
    assert len(list((overlays_dir / "custom").glob("*.png"))) == 2


def test_an_overlay_its_type_cannot_read_is_left_out_and_stores_nothing(overlays_dir, monkeypatch):
    data = _zip(_manifest({"type": "custom", "name": "Broken", "options": {"badge": "image", "badges": [_row(1, "B", image="a" * 12)]}}, {"type": "status", "name": "Fine"}),
                {f"images/custom/{'a' * 12}.png": _png()})
    monkeypatch.setattr(custom_mod.CustomOverlay, "normalize_options", lambda self, raw: 1 / 0)
    with _open(data) as found:
        seen = found.describe()
        result = found.take([0, 1])
    assert [o["name"] for o in seen["overlays"]] == ["Fine"] and seen["unknown"] == 1
    assert [o["overlay"]["name"] for o in result["overlays"]] == ["Fine"] and not (overlays_dir / "custom").exists()


def test_a_list_address_that_is_no_address_does_not_stop_the_file_being_read(overlays_dir):
    rules = [{"field": "id_list", "op": "is", "value": "https://[bad/x.txt", "also": [{"value": "https://example.com/ids.txt"}]}]
    data = _zip(_manifest({"type": "custom", "name": "Lists", "options": {"badge": "text", "badges": [_row(1, "A", text="A", rules=rules)]}}))
    with _open(data) as found:
        seen = found.describe()
    assert [o["name"] for o in seen["overlays"]] == ["Lists"] and seen["unknown"] == 0
    assert seen["overlays"][0]["notes"] == ["Its conditions read lists from example.com."]


def test_an_overlay_list_nested_past_reading_is_refused():
    deep = '{"format": "%s", "version": 1, "overlays": %s%s}' % (sharing.FORMAT, "[" * 100000, "]" * 100000)
    with pytest.raises(sharing.ExportError, match="not an overlay export"):
        _open(_zip(deep))


def test_export_refuses_nothing_to_export_and_too_much(overlays_dir, monkeypatch):
    config = _config(status=[{"name": "One"}, {"name": "Two"}])
    with pytest.raises(sharing.ExportError, match="at least one overlay"):
        sharing.build_export(config, [("status", 5), ("nope", 0)])
    monkeypatch.setattr(sharing, "MAX_OVERLAYS", 1)
    with pytest.raises(sharing.ExportError, match="at most 1 overlays"):
        sharing.build_export(config, [("status", 0), ("status", 1)])
    monkeypatch.setattr(sharing, "MAX_OVERLAYS", 200)
    monkeypatch.setattr(sharing, "MAX_MANIFEST_BYTES", 50)
    with pytest.raises(sharing.ExportError, match="too much for one file"):
        sharing.build_export(config, [("status", 0)])


def test_api_export_and_import(client, test_db, overlays_dir):
    image_id = client.post("/api/overlays/badges", files={"file": ("ribbon.png", _png(), "image/png")}).json()["id"]
    row = _row(1, "Ribbon", image=image_id, rules=[{"field": "label", "op": "is", "value": "Award", "instance": "My Plex"}])
    saved = client.post("/api/overlays/config", json={"enabled": True, "types": {
        "custom": {"enabled": True, "overlays": [{"name": "Awards / Night!", "libraries": ["plex:1"], "options": {"badge": "image", "badges": [row]}}, {"name": "Second"}]},
        "status": {"enabled": True, "overlays": [{"name": "Status"}]}}}).json()["config"]
    assert len(saved["types"]["custom"]["overlays"]) == 2
    assert client.get("/api/overlays/export/options").json() == {"images": {}}

    assert client.get("/api/overlays/export").status_code == 400
    assert client.get("/api/overlays/export", params={"overlays": "custom:7,nope:0,junk"}).status_code == 400
    one = client.get("/api/overlays/export", params={"overlays": "custom:0"})
    assert one.status_code == 200 and one.headers["content-type"] == "application/zip"
    assert one.headers["content-disposition"] == 'attachment; filename="posterflow-overlay-awards-night.zip"'
    three = client.get("/api/overlays/export", params={"overlays": "custom:0,custom:1,status:0", "images": "streaming"})
    assert three.headers["content-disposition"] == 'attachment; filename="posterflow-overlays-3.zip"'
    assert [e["name"] for e in json.loads(zipfile.ZipFile(io.BytesIO(three.content)).read(sharing.MANIFEST))["overlays"]] == ["Awards / Night!", "Second", "Status"]

    assert client.post("/api/overlays/import/read", files={"file": ("x.zip", b"nope", "application/zip")}).status_code == 400
    assert client.post("/api/overlays/import", files={"file": ("x.zip", b"nope", "application/zip")}, data={"overlays": "0"}).status_code == 400
    upload = {"file": ("shared.zip", three.content, "application/zip")}
    seen = client.post("/api/overlays/import/read", files=upload).json()
    assert seen["file"] == "shared.zip" and [(o["index"], o["type"], o["name"]) for o in seen["overlays"]] == [(0, "custom", "Awards / Night!"), (1, "custom", "Second"), (2, "status", "Status")]
    assert (seen["overlays"][0]["badges"], seen["overlays"][0]["images"], seen["unknown"], seen["images"]) == (1, 1, 0, [])

    got = client.post("/api/overlays/import", files=upload, data={"overlays": "0, 2,x", "images": ""}).json()
    assert got["success"] and got["images"] == {} and [o["type"] for o in got["overlays"]] == ["custom", "status"]
    ribbon = got["overlays"][0]["overlay"]
    new_id = ribbon["options"]["badges"][0]["image"]
    assert new_id != image_id and client.get(f"/api/overlays/badges/{new_id}").status_code == 200
    assert ribbon["options"]["badges"][0]["rules"] == [{"field": "label", "op": "is", "value": "Award"}] and ribbon["enabled"] is False
    # The page adds what came back and saves it like any other overlay.
    saved["types"]["custom"]["overlays"].append(ribbon)
    again = client.post("/api/overlays/config", json=saved).json()["config"]
    assert again["types"]["custom"]["overlays"][2] == ribbon
