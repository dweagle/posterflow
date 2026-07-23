"""Tests for api/artwork_finder.py endpoints."""
import io
import json

from PIL import Image

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
    # the collection has its background -> nothing missing (collections never need logo/square)
    assert by_title["Harry Potter Collection"]["missing"] == []
    assert by_title["Harry Potter Collection"]["media_type"] == "collection"


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
