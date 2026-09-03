"""Tests for api/artwork_finder.py endpoints."""
import io
import json

from PIL import Image

import services.artwork_finder as af
from api.idarr import SETTING_MAKER_IDARR_CONFIG
from models.setting import upsert_setting


def _jpg_bytes(size=(1920, 1080)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buf, "JPEG")
    return buf.getvalue()


def _set_tmdb_key(db):
    upsert_setting(db, "tmdb_api_key", "test-key")
    db.commit()


def _set_asset_scope(db, source_dir: str, is_asset_drive=True):
    upsert_setting(db, SETTING_MAKER_IDARR_CONFIG, json.dumps({
        "sync_targets": [{
            "personal_drive_id": "folder-1",
            "source_dir": source_dir,
            "label": "My Artwork",
            "is_asset_drive": is_asset_drive,
        }],
    }))
    db.commit()


def test_candidates_requires_tmdb_key(client):
    resp = client.get("/api/artwork-finder/candidates", params={
        "tmdb_id": 105, "media_type": "movie", "title": "Back to the Future"})
    assert resp.status_code == 400


def test_candidates_returns_grouped_shape(client, test_db, monkeypatch):
    _set_tmdb_key(test_db)
    monkeypatch.setattr("services.artwork_finder.list_candidates", lambda *a, **k: {
        "logos": [{"source": "tmdb", "ref": "/l.png", "width": 800, "height": 300,
                   "off_white_pct": 0.0, "is_white": True}],
        "backgrounds": [{"source": "tmdb", "ref": "/b.jpg", "width": 3840, "height": 2160}],
        "squareart": [],
        "posters": [{"source": "tmdb", "ref": "/p.jpg", "width": 2000, "height": 3000}],
        "plex_available": False,
    })
    resp = client.get("/api/artwork-finder/candidates", params={
        "tmdb_id": 105, "media_type": "movie", "title": "Back to the Future", "year": 1985})
    assert resp.status_code == 200
    data = resp.json()
    assert data["plex_available"] is False
    assert data["logos"][0]["is_white"] is True
    assert data["backgrounds"][0]["ref"] == "/b.jpg"
    assert data["squareart"] == []
    assert data["posters"][0]["ref"] == "/p.jpg"


def test_candidates_browse_backgrounds_are_unfiltered(client, test_db, monkeypatch):
    # Browsing must not apply the batch pull's textless/min-width rule: obscure titles often have
    # nothing on TMDB but narrow or text-tagged backdrops, and filtering left the card empty.
    _set_tmdb_key(test_db)
    seen: dict = {}

    def fake_list(*args, **kwargs):
        seen.update(kwargs)
        return {
            "logos": [], "squareart": [], "posters": [], "plex_available": False,
            "backgrounds": [{"source": "tmdb", "ref": "/b.jpg", "width": 1280, "height": 720,
                             "language": "en"}],
        }

    monkeypatch.setattr("services.artwork_finder.list_candidates", fake_list)
    resp = client.get("/api/artwork-finder/candidates", params={
        "tmdb_id": 105, "media_type": "movie", "title": "Back to the Future", "year": 1985})
    assert resp.status_code == 200
    assert seen["textless_backgrounds"] is False
    # the language rides along so the card can badge it
    assert resp.json()["backgrounds"][0]["language"] == "en"


def test_add_and_crop_accept_string_or_empty_year(client, test_db, tmp_path, monkeypatch):
    # The UI passes the TMDB result's year verbatim: a string, and '' when unknown
    # (collections especially). Both must parse, not 422.
    _set_asset_scope(test_db, str(tmp_path))
    monkeypatch.setattr("services.artwork_finder._download_bytes", lambda *a, **k: _jpg_bytes(size=(800, 800)))
    r1 = client.post("/api/artwork-finder/add", json={
        "sync_target_index": 0, "title": "Dune", "media_type": "movie", "year": "2021",
        "tmdb_id": 438631, "subtype": "background", "source": "tmdb", "ref": "/x.jpg"})
    assert r1.status_code == 200 and "(2021)" in r1.json()["written"]
    r2 = client.post("/api/artwork-finder/crop-square", json={
        "sync_target_index": 0, "title": "Harry Potter Collection", "media_type": "collection",
        "year": "", "tmdb_id": 1241, "source": "tmdb", "ref": "/p.jpg", "x": 0, "y": 0, "size": 600})
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "added"


def test_crop_square_saves_square_art(client, test_db, tmp_path, monkeypatch):
    _set_asset_scope(test_db, str(tmp_path))

    def _wide(*a, **k):
        buf = io.BytesIO()
        Image.new("RGB", (400, 200), (1, 2, 3)).save(buf, "JPEG")
        return buf.getvalue()

    monkeypatch.setattr("services.artwork_finder._download_bytes", _wide)
    resp = client.post("/api/artwork-finder/crop-square", json={
        "sync_target_index": 0, "title": "Dune", "media_type": "movie", "year": 2021,
        "tmdb_id": 438631, "source": "tmdb", "ref": "/p.jpg", "x": 40, "y": 20, "size": 150})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "added"
    assert data["written"] == "Dune (2021) {tmdb-438631} - squareart.jpg"
    with Image.open(tmp_path / "squareart" / data["written"]) as im:
        assert im.size == (150, 150)


def test_add_writes_into_scope_subfolder(client, test_db, tmp_path, monkeypatch):
    _set_asset_scope(test_db, str(tmp_path))
    monkeypatch.setattr("services.artwork_finder._download_bytes", lambda *a, **k: _jpg_bytes())
    resp = client.post("/api/artwork-finder/add", json={
        "sync_target_index": 0, "title": "Dune", "media_type": "movie", "year": 2021,
        "tmdb_id": 438631, "subtype": "background", "source": "tmdb", "ref": "/x.jpg",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "added"
    assert data["written"] == "Dune (2021) {tmdb-438631} - background.jpg"
    assert (tmp_path / "backgrounds" / data["written"]).is_file()


def test_add_reports_existing_then_overwrites(client, test_db, tmp_path, monkeypatch):
    _set_asset_scope(test_db, str(tmp_path))
    monkeypatch.setattr("services.artwork_finder._download_bytes", lambda *a, **k: _jpg_bytes())
    body = {"sync_target_index": 0, "title": "Dune", "media_type": "movie", "year": 2021,
            "tmdb_id": 438631, "subtype": "background", "source": "tmdb", "ref": "/x.jpg"}
    assert client.post("/api/artwork-finder/add", json=body).json()["status"] == "added"
    assert client.post("/api/artwork-finder/add", json=body).json()["status"] == "exists"
    overwrite = client.post("/api/artwork-finder/add", json={**body, "confirm_overwrite": True})
    assert overwrite.json()["status"] == "added"
    assert overwrite.json()["archived"] is True


def test_add_rejects_non_asset_scope(client, test_db, tmp_path):
    _set_asset_scope(test_db, str(tmp_path), is_asset_drive=False)
    resp = client.post("/api/artwork-finder/add", json={
        "sync_target_index": 0, "title": "Dune", "media_type": "movie",
        "tmdb_id": 438631, "subtype": "background", "source": "tmdb", "ref": "/x.jpg",
    })
    assert resp.status_code == 400
    assert "artwork scope" in resp.json()["detail"].lower()


def test_scope_items_reports_missing_types(client, test_db, tmp_path):
    _set_asset_scope(test_db, str(tmp_path))
    logos = tmp_path / "logos"
    logos.mkdir()
    (logos / "Dune (2021) {tmdb-438631} - logo.png").write_bytes(_jpg_bytes(size=(20, 20)))
    bgs = tmp_path / "backgrounds"
    bgs.mkdir()
    (bgs / "Harry Potter Collection {tmdb-1241} - background.jpg").write_bytes(_jpg_bytes(size=(20, 20)))

    resp = client.get("/api/artwork-finder/scope-items", params={"sync_target_index": 0})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 2
    by_title = {i["title"]: i for i in data["items"]}
    # Dune has a logo -> missing background + squareart
    assert sorted(by_title["Dune"]["missing"]) == ["background", "squareart"]
    # Collections are checked for all three types too — the browse view reports the gaps even
    # though no source auto-fills collection logos / square art.
    assert sorted(by_title["Harry Potter Collection"]["missing"]) == ["logo", "squareart"]
    assert by_title["Harry Potter Collection"]["media_type"] == "collection"


def test_scope_items_other_sources_surface_items_with_no_artwork(client, test_db, tmp_path, monkeypatch):
    """source=scope walks the scope's own files, so an item with NO artwork there has nothing to
    enumerate and can never appear — which is why collections the user owns but has never pulled for
    looked "missing from the list". The other sources enumerate elsewhere and measure gaps against
    the scope, so those items finally show up with everything marked missing."""
    import modules.artwork_pull as ap
    _set_asset_scope(test_db, str(tmp_path))
    (tmp_path / "backgrounds").mkdir()
    (tmp_path / "backgrounds" / "Alien Collection {tmdb-8091} - background.jpg").write_bytes(_jpg_bytes(size=(20, 20)))

    assert [i["title"] for i in client.get(
        "/api/artwork-finder/scope-items", params={"sync_target_index": 0}).json()["items"]] == ["Alien Collection"]

    monkeypatch.setattr(ap, "_items_from_poster_drives", lambda db, ids: [
        af.FinderItem(title="Alien Collection", year=None, tmdb_id=8091, media_type="collection"),
        af.FinderItem(title="1980s Collection", year=None, tmdb_id=None, media_type="collection"),
    ])
    data = client.get("/api/artwork-finder/scope-items",
                      params={"sync_target_index": 0, "source": "poster_drives"}).json()
    assert data["source"] == "poster_drives"
    by_title = {i["title"]: i for i in data["items"]}
    assert sorted(by_title["1980s Collection"]["missing"]) == ["background", "logo", "squareart"]
    assert sorted(by_title["Alien Collection"]["missing"]) == ["logo", "squareart"]   # bg already there

    assert client.get("/api/artwork-finder/scope-items",
                      params={"sync_target_index": 0, "source": "nope"}).status_code == 400


def test_scope_items_poster_scope_lists_one_sync_target(client, test_db, tmp_path):
    """Poster scopes get a button each: a gap list is only actionable against the library you
    actually keep, and the artwork/PSD scopes aren't poster libraries."""
    art, posters, psds = tmp_path / "art", tmp_path / "posters", tmp_path / "psds"
    for p in (art, posters, psds):
        p.mkdir()
    upsert_setting(test_db, SETTING_MAKER_IDARR_CONFIG, json.dumps({"sync_targets": [
        {"personal_drive_id": "a", "source_dir": str(art), "label": "PlexArt", "is_asset_drive": True},
        {"personal_drive_id": "b", "source_dir": str(posters), "label": "CL2K Drive"},
        {"personal_drive_id": "c", "source_dir": str(psds), "label": "PSDs", "is_psd_drive": True},
    ]}))
    test_db.commit()
    (posters / "Alien Collection {tmdb-8091}.jpg").write_bytes(_jpg_bytes(size=(20, 20)))
    (art / "backgrounds").mkdir()
    (art / "backgrounds" / "Alien Collection {tmdb-8091} - background.jpg").write_bytes(_jpg_bytes(size=(20, 20)))

    resp = client.get("/api/artwork-finder/scope-items",
                      params={"sync_target_index": 0, "source": "poster_scope", "item_scope_index": 1})
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["title"] == "Alien Collection"
    assert sorted(item["missing"]) == ["logo", "squareart"]   # measured against the ARTWORK scope

    # A PSD or artwork scope isn't a poster library, and the index is required.
    assert client.get("/api/artwork-finder/scope-items", params={
        "sync_target_index": 0, "source": "poster_scope", "item_scope_index": 2}).status_code == 400
    assert client.get("/api/artwork-finder/scope-items", params={
        "sync_target_index": 0, "source": "poster_scope", "item_scope_index": 0}).status_code == 400
    assert client.get("/api/artwork-finder/scope-items", params={
        "sync_target_index": 0, "source": "poster_scope"}).status_code == 400


def test_scope_items_year_less_movie_with_imdb_id_is_not_a_collection(client, test_db, tmp_path):
    """A missing (YYYY) is the only collection hint for asset-drive files, so an {imdb-tt…} tag
    has to veto it — collections carry no IMDb id, and these were landing on the collection tab."""
    _set_asset_scope(test_db, str(tmp_path))
    logos = tmp_path / "logos"
    logos.mkdir()
    (logos / "Leo 2 {tmdb-1235976} {imdb-tt31066554} - logo.png").write_bytes(_jpg_bytes(size=(20, 20)))

    resp = client.get("/api/artwork-finder/scope-items", params={"sync_target_index": 0})
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["title"] == "Leo 2"
    assert item["media_type"] == "movie"
    assert item["imdb_id"] == "tt31066554"


def test_gracenote_proxy_rejects_non_plex_host(client):
    resp = client.get("/api/artwork-finder/gracenote-image-proxy",
                      params={"url": "https://evil.example.com/x.jpg"})
    assert resp.status_code == 400


def test_tmdb_download_sets_canonical_filename(client, monkeypatch):
    class FakeResp:
        status_code = 200
        headers = {"content-type": "image/png"}

        def iter_content(self, chunk_size=8192):
            yield b"PNGDATA"

    monkeypatch.setattr("api.artwork_finder.requests.get", lambda *a, **k: FakeResp())
    resp = client.get("/api/artwork-finder/tmdb-download", params={
        "path": "/abc.png", "role": "logo", "title": "Dune", "media_type": "movie",
        "year": 2021, "tmdb_id": 438631})
    assert resp.status_code == 200
    assert "Dune (2021) {tmdb-438631} - logo.png" in resp.headers["content-disposition"]


# ------------------------------------------------------------------ local picker folder

def _png_bytes(size=(800, 800)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (5, 6, 7)).save(buf, "PNG")
    return buf.getvalue()


def _set_picker_folder(client, folder) -> dict:
    resp = client.put("/api/artwork-finder/local-folder", json={"folder": str(folder)})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_set_picker_folder_rejects_bad_paths(client, tmp_path):
    assert client.put("/api/artwork-finder/local-folder", json={"folder": "relative/dir"}).status_code == 400
    assert client.put("/api/artwork-finder/local-folder", json={"folder": str(tmp_path) + "/../x"}).status_code == 400
    assert client.put("/api/artwork-finder/local-folder", json={"folder": str(tmp_path / "missing")}).status_code == 400


def test_picker_folder_lists_and_classifies(client, tmp_path):
    (tmp_path / "backgrounds").mkdir()
    (tmp_path / "backgrounds" / "bg.jpg").write_bytes(_jpg_bytes(size=(1920, 1080)))
    (tmp_path / "squareart").mkdir()
    (tmp_path / "squareart" / "sq.jpg").write_bytes(_jpg_bytes(size=(1000, 1000)))
    (tmp_path / "flat.png").write_bytes(_png_bytes(size=(800, 800)))     # square by aspect ratio
    (tmp_path / "wide.png").write_bytes(_png_bytes(size=(1600, 900)))    # background by aspect ratio
    (tmp_path / "notes.txt").write_text("skip me")

    data = _set_picker_folder(client, tmp_path)
    assert data["folder"] == str(tmp_path)
    chosen = lambda key: sorted(f["path"] for f in data[key] if f["source"] == "folder")  # noqa: E731
    assert chosen("backgrounds") == ["backgrounds/bg.jpg", "wide.png"]
    assert chosen("squareart") == ["flat.png", "squareart/sq.jpg"]

    # The app's own artwork rides along in every listing, ahead of the chosen folder's files.
    assert [f["source"] for f in data["backgrounds"]][0] == "bundled"
    assert {f["source"] for f in data["squareart"]} >= {"bundled", "folder"}

    # GET lists the stored folder without re-setting it
    again = client.get("/api/artwork-finder/local-folder").json()
    assert again["folder"] == str(tmp_path)
    assert len([f for f in again["squareart"] if f["source"] == "folder"]) == 2


def test_picker_folder_unset_and_missing(client, tmp_path):
    # No folder configured still lists the app's bundled artwork, and never errors.
    unset = client.get("/api/artwork-finder/local-folder").json()
    assert unset["folder"] == "" and unset["error"] is None
    assert {f["source"] for f in unset["backgrounds"] + unset["squareart"]} == {"bundled"}
    gone = tmp_path / "gone"
    gone.mkdir()
    _set_picker_folder(client, gone)
    gone.rmdir()
    data = client.get("/api/artwork-finder/local-folder").json()
    assert data["folder"] == str(gone) and data["error"]


def test_picker_image_serves_and_guards(client, tmp_path):
    inside = tmp_path / "art"
    inside.mkdir()
    (inside / "bg.jpg").write_bytes(_jpg_bytes())
    (tmp_path / "secret.jpg").write_bytes(_jpg_bytes())
    _set_picker_folder(client, inside)

    assert client.get("/api/artwork-finder/local-image", params={"path": "bg.jpg"}).status_code == 200
    assert client.get("/api/artwork-finder/local-image", params={"path": "../secret.jpg"}).status_code == 403
    assert client.get("/api/artwork-finder/local-image", params={"path": "missing.jpg"}).status_code == 404


def test_add_local_copies_and_converts(client, test_db, tmp_path):
    scope = tmp_path / "scope"
    scope.mkdir()
    art = tmp_path / "art"
    art.mkdir()
    (art / "bg.png").write_bytes(_png_bytes(size=(1920, 1080)))
    _set_asset_scope(test_db, str(scope))
    _set_picker_folder(client, art)

    body = {"sync_target_index": 0, "title": "Dune", "media_type": "movie", "year": 2021,
            "tmdb_id": 438631, "subtype": "background", "path": "bg.png"}
    resp = client.post("/api/artwork-finder/add-local", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "added"
    assert data["written"] == "Dune (2021) {tmdb-438631} - background.jpg"
    with Image.open(scope / "backgrounds" / data["written"]) as im:   # png source -> jpeg
        assert im.format == "JPEG"
        assert im.size == (1920, 1080)
    assert (art / "bg.png").is_file()   # copied, not moved

    assert client.post("/api/artwork-finder/add-local", json=body).json()["status"] == "exists"
    over = client.post("/api/artwork-finder/add-local", json={**body, "confirm_overwrite": True}).json()
    assert over["status"] == "added" and over["archived"] is True


def test_add_local_requires_folder_and_savable_subtype(client, test_db, tmp_path):
    scope = tmp_path / "scope"
    scope.mkdir()
    _set_asset_scope(test_db, str(scope))
    body = {"sync_target_index": 0, "title": "Dune", "media_type": "movie",
            "tmdb_id": 438631, "subtype": "background", "path": "bg.png"}
    assert client.post("/api/artwork-finder/add-local", json=body).status_code == 400  # no folder configured

    art = tmp_path / "art"
    art.mkdir()
    (art / "l.png").write_bytes(_png_bytes(size=(400, 100)))
    _set_picker_folder(client, art)
    logo = client.post("/api/artwork-finder/add-local", json={**body, "subtype": "logo", "path": "l.png"})
    assert logo.status_code == 400


def test_add_local_collection_without_ids(client, test_db, tmp_path):
    # Custom collections (decades, holidays, …) have no TMDB entry — the canonical name simply
    # carries no id tags.
    scope = tmp_path / "scope"
    scope.mkdir()
    art = tmp_path / "art"
    art.mkdir()
    (art / "sq.jpg").write_bytes(_jpg_bytes(size=(1000, 1000)))
    _set_asset_scope(test_db, str(scope))
    _set_picker_folder(client, art)

    resp = client.post("/api/artwork-finder/add-local", json={
        "sync_target_index": 0, "title": "1980s Collection", "media_type": "collection",
        "subtype": "squareart", "path": "sq.jpg"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["written"] == "1980s Collection - squareart.jpg"
    assert (scope / "squareart" / data["written"]).is_file()


def test_picker_paths_with_leading_spaces_survive(client, test_db, tmp_path):
    # Real folders contain names like " background.jpg" — the relative path must round-trip
    # verbatim through listing, image serving, and add-local (stripping it 404'd in the field).
    scope = tmp_path / "scope"
    scope.mkdir()
    art = tmp_path / "art"
    art.mkdir()
    (art / " background.jpg").write_bytes(_jpg_bytes(size=(1920, 1080)))
    _set_asset_scope(test_db, str(scope))
    data = _set_picker_folder(client, art)
    assert [f["path"] for f in data["backgrounds"] if f["source"] == "folder"] == [" background.jpg"]

    img = client.get("/api/artwork-finder/local-image", params={"path": " background.jpg"})
    assert img.status_code == 200, img.text

    add = client.post("/api/artwork-finder/add-local", json={
        "sync_target_index": 0, "title": "Dune", "media_type": "movie", "year": 2021,
        "tmdb_id": 438631, "subtype": "background", "path": " background.jpg"})
    assert add.status_code == 200, add.text
    assert add.json()["status"] == "added"


# ------------------------------------------------------------------ text logo

def test_text_logo_preview_renders_png(client):
    import base64
    resp = client.post("/api/artwork-finder/text-logo/preview",
                       json={"top": "THE", "main": "JAMES BOND", "suffix": "COLLECTION"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    with Image.open(io.BytesIO(base64.b64decode(data["png_base64"]))) as im:
        assert im.format == "PNG" and im.mode == "RGBA"
        assert im.size == (data["width"], data["height"])
        assert im.width > im.height


def test_text_logo_preview_requires_text(client):
    assert client.post("/api/artwork-finder/text-logo/preview", json={"main": "  "}).status_code == 400


def test_add_text_logo_saves_and_overwrites(client, test_db, tmp_path):
    _set_asset_scope(test_db, str(tmp_path))
    body = {"sync_target_index": 0, "title": "James Bond Collection", "media_type": "collection",
            "tmdb_id": 645, "top": "THE", "main": "JAMES BOND", "suffix": "COLLECTION"}
    resp = client.post("/api/artwork-finder/text-logo", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "added"
    assert data["written"] == "James Bond Collection {tmdb-645} - logo.png"
    with Image.open(tmp_path / "logos" / data["written"]) as im:   # transparent PNG survives save
        assert im.format == "PNG" and im.mode == "RGBA"
    assert client.post("/api/artwork-finder/text-logo", json=body).json()["status"] == "exists"
    over = client.post("/api/artwork-finder/text-logo", json={**body, "confirm_overwrite": True}).json()
    assert over["status"] == "added" and over["archived"] is True


def test_text_logo_reports_missing_font(client, monkeypatch):
    monkeypatch.setattr("services.text_logo._resolve_font", lambda candidates: None)
    resp = client.post("/api/artwork-finder/text-logo/preview", json={"main": "DUNE"})
    assert resp.status_code == 400
    assert "font" in resp.json()["detail"].lower()


def test_text_logo_tuning_overrides_change_width(client):
    # Squeezing the width or zeroing the tracking must both narrow the main line; the suffix
    # line has no overrides.
    def width(body):
        resp = client.post("/api/artwork-finder/text-logo/preview", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()["width"]

    base = width({"main": "JAMES BOND"})
    assert width({"main": "JAMES BOND", "main_scale": 50}) < base
    assert width({"main": "JAMES BOND", "main_tracking": 0}) < base


def test_text_logo_font_override_matches_otf_by_stem(tmp_path, monkeypatch):
    # A user-dropped .otf in config/artwork/fonts must satisfy a .ttf candidate name — matching
    # is by stem, and the override dir wins over the bundled fonts.
    from core.config import settings as app_settings
    from services.text_logo import _resolve_font
    fonts = tmp_path / "artwork" / "fonts"
    fonts.mkdir(parents=True)
    (fonts / "bebasneuebold.otf").write_bytes(b"stub")
    monkeypatch.setattr(app_settings, "config_dir", tmp_path)
    hit = _resolve_font(["BebasNeueBold.ttf", "BebasNeue-Bold.ttf", "BebasNeue-Regular.ttf"])
    assert hit == fonts / "bebasneuebold.otf"


def test_text_logo_fonts_lists_config_and_bundled(client, tmp_path, monkeypatch):
    import shutil
    from core.config import settings as app_settings
    from services import text_logo as tl
    # Any bundled font will do — which ones ship is a packaging choice, not this test's subject.
    a_bundled = next(p for p in sorted(tl.BUNDLED_FONT_DIR.iterdir()) if p.suffix.lower() in (".ttf", ".otf"))
    fonts_dir = tmp_path / "artwork" / "fonts"
    fonts_dir.mkdir(parents=True)
    shutil.copy(a_bundled, fonts_dir / "MyCustom.ttf")
    (fonts_dir / "broken.otf").write_bytes(b"junk")   # unreadable -> not offered
    monkeypatch.setattr(app_settings, "config_dir", tmp_path)

    fonts = client.get("/api/artwork-finder/text-logo/fonts").json()["fonts"]
    by_id = {f["id"]: f for f in fonts}
    assert by_id["MyCustom"]["source"] == "config"
    assert by_id["MyCustom"]["label"] == "MyCustom"   # config fonts label by FILENAME (renameable)
    assert "broken" not in by_id
    assert by_id[a_bundled.stem]["source"] == "bundled"
    # bundled fonts label by their embedded family name, not the filename
    assert by_id[a_bundled.stem]["label"] != a_bundled.stem or " " in a_bundled.stem


def test_text_logo_render_honors_font_override(client):
    def preview(body):
        resp = client.post("/api/artwork-finder/text-logo/preview", json=body)
        return resp

    base = preview({"main": "DUNE"}).json()
    other = preview({"main": "DUNE", "main_font": "LiberationSans-Regular"}).json()
    assert other["width"] != base["width"]   # a different face measures differently
    missing = preview({"main": "DUNE", "main_font": "NopeFont"})
    assert missing.status_code == 400
    assert "NopeFont" in missing.json()["detail"]


def test_candidates_collection_borrows_first_movie_logos(client, test_db, monkeypatch):
    # TMDB serves no collection logos — the card borrows logos from the collection's FIRST
    # movie by release date (its logo is usually the franchise mark), tagged with that movie.
    _set_tmdb_key(test_db)
    fetched_movies = []

    def fake_get(path, api_key, **params):
        if path == "/collection/1241":
            return {"parts": [
                {"id": 672, "title": "Chamber of Secrets", "release_date": "2002-11-13"},
                {"id": 671, "title": "Philosopher's Stone", "release_date": "2001-11-16"},
            ]}
        if path.startswith("/movie/"):
            fetched_movies.append(path)
            return {"logos": [{"file_path": "/l1.png", "width": 800, "height": 300,
                               "vote_average": 5, "iso_639_1": "en"},
                              {"file_path": "/skip.svg", "width": 1, "height": 1}]}
        return {}

    monkeypatch.setattr("services.artwork_finder._tmdb_get", fake_get)
    resp = client.get("/api/artwork-finder/candidates", params={
        "tmdb_id": 1241, "media_type": "collection", "title": "Harry Potter Collection",
        "evaluate_white": False})
    assert resp.status_code == 200, resp.text
    logos = resp.json()["logos"]
    assert fetched_movies == ["/movie/671/images"]   # only the earliest release is consulted
    assert [l["ref"] for l in logos] == ["/l1.png"]  # svg dropped
    assert logos[0]["origin"] == "Philosopher's Stone"


def test_parse_with_identity_pending_type_never_overrides(tmp_path):
    # IDarr caches unresolvable files (custom collections) as type "pending"; that must not
    # stomp the filename's own typing — it fell through to movie and the artwork stopped
    # matching the collection it was made for (field bug, Aug 2026).
    f = tmp_path / "1940s Collection - background.jpg"
    pending = {"1940s collection - background.jpg":
               {"asset_type": "pending", "tmdb_id": None, "tvdb_id": None, "imdb_id": None}}
    parsed = af._parse_with_identity(f, pending)
    assert parsed["type"] == "collection"
    assert af.finder_from_parsed_asset(parsed).media_type == "collection"

    # A genuinely resolved type still wins over the filename's guess.
    resolved = {"1940s collection - background.jpg":
                {"asset_type": "tv_series", "tmdb_id": 225948, "tvdb_id": None, "imdb_id": None}}
    assert af._parse_with_identity(f, resolved)["type"] == "tv_series"


def test_candidates_language_param_maps_to_tmdb_value(client, test_db, monkeypatch):
    # UI choice -> include_image_language: default en+textless -> 'en,null', all -> omit (None),
    # a bare ISO code passes through.
    _set_tmdb_key(test_db)
    seen = {}

    def fake_list(*args, **kwargs):
        seen.update(kwargs)
        return {"logos": [], "backgrounds": [], "squareart": [], "posters": [], "plex_available": False}

    monkeypatch.setattr("services.artwork_finder.list_candidates", fake_list)
    base = {"tmdb_id": 105, "media_type": "movie", "title": "Back to the Future"}
    for lang, expected in (("en+textless", "en,null"), ("all", None), ("de", "de")):
        assert client.get("/api/artwork-finder/candidates", params={**base, "language": lang}).status_code == 200
        assert seen["image_language"] == expected
    # omitted -> the en+textless default
    assert client.get("/api/artwork-finder/candidates", params=base).status_code == 200
    assert seen["image_language"] == "en,null"


def test_downloaded_jpegs_recompress_only_when_smaller():
    # Untouched JPEG downloads recompress to q92 when that saves >=10%; already-lean sources
    # keep their original bytes verbatim (a re-encode would grow them AND cost a generation).
    noise = Image.effect_noise((800, 600), 60).convert("RGB")
    fat, lean = io.BytesIO(), io.BytesIO()
    noise.save(fat, "JPEG", quality=100)
    noise.save(lean, "JPEG", quality=70)

    recompressed = af.prepare_artwork_payload(fat.getvalue(), "background")
    assert len(recompressed) <= len(fat.getvalue()) * 0.9
    with Image.open(io.BytesIO(recompressed)) as im:
        assert im.format == "JPEG" and im.size == (800, 600)

    assert af.prepare_artwork_payload(lean.getvalue(), "background") == lean.getvalue()


def test_picker_bundled_and_art_roots(client, test_db, tmp_path, monkeypatch):
    # The app's own artwork and the user's config/artwork/art stash are always offered (bundled
    # first), each resolvable by its own source root.
    from core.config import settings as app_settings
    art = tmp_path / "artwork" / "art"
    art.mkdir(parents=True)
    (art / "mine-background.jpg").write_bytes(_jpg_bytes(size=(1920, 1080)))
    monkeypatch.setattr(app_settings, "config_dir", tmp_path)

    data = client.get("/api/artwork-finder/local-folder").json()
    assert data["art_dir"] == str(art)
    sources = [f["source"] for f in data["backgrounds"]]
    assert sources[0] == "bundled" and "art" in sources          # bundled listed ahead of the stash
    assert any(f["name"] == "mine-background.jpg" for f in data["backgrounds"])
    bundled = next(f for f in data["backgrounds"] if f["source"] == "bundled")

    # Each root serves and saves through its own source, and roots can't reach each other.
    assert client.get("/api/artwork-finder/local-image",
                      params={"path": "mine-background.jpg", "source": "art"}).status_code == 200
    assert client.get("/api/artwork-finder/local-image",
                      params={"path": bundled["path"], "source": "bundled"}).status_code == 200
    assert client.get("/api/artwork-finder/local-image",
                      params={"path": "mine-background.jpg", "source": "bundled"}).status_code == 404

    scope = tmp_path / "scope"
    scope.mkdir()
    _set_asset_scope(test_db, str(scope))
    add = client.post("/api/artwork-finder/add-local", json={
        "sync_target_index": 0, "title": "Dune", "media_type": "movie", "year": 2021,
        "tmdb_id": 438631, "subtype": "background", "source": "bundled", "path": bundled["path"]})
    assert add.status_code == 200, add.text
    assert (scope / "backgrounds" / add.json()["written"]).is_file()


# ---------------------------------------------------------------- fanart.tv source

FANART_LOGO = "https://assets.fanart.tv/fanart/movies/550/hdmovielogo/fight-club-1.png"


def test_candidates_fanart_requires_a_fanart_key(client, test_db):
    _set_tmdb_key(test_db)
    resp = client.get("/api/artwork-finder/candidates", params={
        "tmdb_id": 550, "media_type": "movie", "title": "Fight Club", "source": "fanart"})
    assert resp.status_code == 400
    assert "fanart.tv" in resp.json()["detail"]


def test_candidates_fanart_lists_without_a_tmdb_key(client, test_db, monkeypatch):
    upsert_setting(test_db, "fanart_api_key", "personal-key")
    test_db.commit()
    seen = {}

    def fake_list(item, wanted, **kwargs):
        seen.update(kwargs)
        return {"logos": [{"source": "fanart", "ref": FANART_LOGO, "width": 800, "height": 310, "language": "en"}],
                "backgrounds": [], "squareart": [], "posters": [], "plex_available": False}

    monkeypatch.setattr("services.artwork_finder.list_candidates", fake_list)
    resp = client.get("/api/artwork-finder/candidates", params={
        "tmdb_id": 550, "media_type": "movie", "title": "Fight Club", "source": "fanart"})
    assert resp.status_code == 200, resp.text
    assert seen["source"] == "fanart"
    assert seen["fanart_api_key"] == "personal-key"
    assert resp.json()["logos"][0]["source"] == "fanart"


def test_candidates_rejects_an_unknown_source(client, test_db):
    _set_tmdb_key(test_db)
    resp = client.get("/api/artwork-finder/candidates", params={
        "tmdb_id": 550, "media_type": "movie", "title": "Fight Club", "source": "imgur"})
    assert resp.status_code == 400


def test_add_accepts_a_fanart_candidate(client, test_db, tmp_path, monkeypatch):
    _set_asset_scope(test_db, str(tmp_path))
    seen = {}

    def fake_save(**kwargs):
        seen.update(kwargs)
        return {"status": "added", "written": "Fight Club (1999) {tmdb-550}_logo.png",
                "subfolder": "logos", "archived": False}

    monkeypatch.setattr(af, "save_candidate", fake_save)
    resp = client.post("/api/artwork-finder/add", json={
        "sync_target_index": 0, "title": "Fight Club", "media_type": "movie", "subtype": "logo",
        "source": "fanart", "ref": FANART_LOGO, "year": 1999, "tmdb_id": 550})
    assert resp.status_code == 200, resp.text
    assert seen["source"] == "fanart" and seen["ref"] == FANART_LOGO


def test_fanart_image_proxy_only_allows_the_asset_host(client):
    for url in ("https://evil.example.com/x.png", "http://assets.fanart.tv/fanart/x.png"):
        resp = client.get("/api/artwork-finder/fanart-image-proxy", params={"url": url})
        assert resp.status_code == 400


def test_fanart_image_proxy_streams_with_a_download_filename(client, monkeypatch):
    class _Resp:
        status_code = 200
        headers = {"content-type": "image/png"}

        def iter_content(self, chunk_size):
            yield b"png"

    monkeypatch.setattr("api.artwork_finder.requests.get", lambda *a, **k: _Resp())
    resp = client.get("/api/artwork-finder/fanart-image-proxy", params={"url": FANART_LOGO})
    assert resp.status_code == 200
    assert 'filename="fight-club-1.png"' in resp.headers["content-disposition"]
    assert resp.content == b"png"


def test_tagged_download_accepts_a_fanart_ref(client, monkeypatch):
    class _Resp:
        status_code = 200
        headers = {"content-type": "image/png"}

        def iter_content(self, chunk_size):
            yield b"png"

    monkeypatch.setattr("api.artwork_finder.requests.get", lambda *a, **k: _Resp())
    resp = client.get("/api/artwork-finder/tmdb-download", params={
        "path": FANART_LOGO, "role": "logo", "title": "Fight Club", "media_type": "movie",
        "year": 1999, "tmdb_id": 550})
    assert resp.status_code == 200, resp.text
    assert resp.content == b"png"
    disposition = resp.headers["content-disposition"]
    assert "Fight" in disposition and ".png" in disposition

    resp = client.get("/api/artwork-finder/tmdb-download", params={
        "path": "https://evil.example.com/x.png", "role": "logo", "title": "X", "media_type": "movie"})
    assert resp.status_code == 400
