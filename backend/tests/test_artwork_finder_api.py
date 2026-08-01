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
