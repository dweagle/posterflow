"""Tests for the TheTVDB image-browser endpoints in api/maker_tools.py."""
import pytest

import services.tvdb as tvdb
from api.maker_tools import _is_export_ref_valid
from models.setting import Setting

TVDB_URL = "https://artworks.thetvdb.com/banners/posters/1-2.jpg"


@pytest.fixture(autouse=True)
def _clear_tvdb_caches():
    """The token and artwork-type caches are process-wide; keep them out of each other's way."""
    tvdb._token_cache.clear()
    tvdb._types_cache = None
    yield
    tvdb._token_cache.clear()
    tvdb._types_cache = None


def _set_key(test_db, value="tvdb-key"):
    test_db.add(Setting(key="tvdb_api_key", value=value))
    test_db.commit()


# ---------------------------------------------------------------- /tvdb/images

def test_tvdb_images_requires_a_configured_key(client):
    response = client.get("/api/maker-tools/tvdb/images", params={"media_type": "tv", "tvdb_id": 1})
    assert response.status_code == 400
    assert "not configured" in response.json()["detail"]


def test_tvdb_images_rejects_an_unknown_media_type(client, test_db):
    _set_key(test_db)
    response = client.get("/api/maker-tools/tvdb/images", params={"media_type": "person", "tvdb_id": 1})
    assert response.status_code == 400


def test_tvdb_images_returns_empty_for_collections(client, test_db):
    """TVDB has no collection entity, so this is 'nothing here', not an error."""
    _set_key(test_db)
    response = client.get("/api/maker-tools/tvdb/images",
                          params={"media_type": "collection", "tmdb_id": 5})
    assert response.status_code == 200
    assert response.json() == {"posters": [], "backdrops": [], "logos": []}


def test_tvdb_images_returns_empty_when_the_title_has_no_tvdb_entry(client, test_db, monkeypatch):
    """A movie with no IMDb id can't be resolved — an empty gallery beats an error toast."""
    _set_key(test_db)
    monkeypatch.setattr(tvdb, "resolve_tvdb_id", lambda **kwargs: None)
    response = client.get("/api/maker-tools/tvdb/images", params={"media_type": "movie"})
    assert response.status_code == 200
    assert response.json()["posters"] == []


def test_tvdb_images_maps_artwork_into_the_gallery_shape(client, test_db, monkeypatch):
    _set_key(test_db)
    monkeypatch.setattr(tvdb, "resolve_tvdb_id", lambda **kwargs: 99)
    monkeypatch.setattr(tvdb, "artwork_types", lambda k, p: {2: ("poster", "series"),
                                                            23: ("logo", "series")})
    monkeypatch.setattr(tvdb, "fetch_artwork", lambda **kwargs: [
        {"type": 2, "image": TVDB_URL, "language": "eng", "includesText": True,
         "score": 5, "width": 680, "height": 1000},
        {"type": 23, "image": "https://artworks.thetvdb.com/l.png", "language": None,
         "includesText": False, "score": 1, "width": 800, "height": 310},
    ])

    response = client.get("/api/maker-tools/tvdb/images",
                          params={"media_type": "tv", "tvdb_id": 99, "language": "all"})
    assert response.status_code == 200
    data = response.json()
    assert data["posters"][0]["file_path"] == TVDB_URL
    assert data["posters"][0]["language"] == "en"
    assert data["logos"][0]["language"] is None      # textless
    assert data["backdrops"] == []


def test_tvdb_images_surfaces_a_tvdb_failure_with_its_status(client, test_db, monkeypatch):
    _set_key(test_db)

    def _boom(**kwargs):
        raise tvdb.TvdbError("TheTVDB rejected the API key", status=401)

    monkeypatch.setattr(tvdb, "resolve_tvdb_id", _boom)
    response = client.get("/api/maker-tools/tvdb/images", params={"media_type": "tv", "tvdb_id": 1})
    assert response.status_code == 401
    assert "rejected" in response.json()["detail"]


# ---------------------------------------------------------------- season images

def test_tvdb_season_images_returns_empty_without_a_series_id(client, test_db):
    _set_key(test_db)
    response = client.get("/api/maker-tools/tvdb/season-images",
                          params={"tvdb_id": 0, "season_number": 1})
    assert response.status_code == 200
    assert response.json()["posters"] == []


def test_tvdb_season_images_keeps_season_scoped_artwork(client, test_db, monkeypatch):
    """Season records use season-scoped type ids, which the title filter would otherwise drop."""
    _set_key(test_db)
    monkeypatch.setattr(tvdb, "artwork_types", lambda k, p: {7: ("poster", "season")})
    monkeypatch.setattr(tvdb, "fetch_season_artwork", lambda **kwargs: [
        {"type": 7, "image": TVDB_URL, "language": None, "includesText": False,
         "score": 0, "width": 680, "height": 1000},
    ])

    response = client.get("/api/maker-tools/tvdb/season-images",
                          params={"tvdb_id": 99, "season_number": 2})
    assert response.status_code == 200
    assert len(response.json()["posters"]) == 1


# ---------------------------------------------------------------- image proxy

def test_tvdb_image_proxy_rejects_a_foreign_host(client):
    response = client.get("/api/maker-tools/tvdb/image-proxy",
                          params={"url": "https://evil.com/x.jpg"})
    assert response.status_code == 400


# ---------------------------------------------------------------- export refs

@pytest.mark.parametrize("ref, ok", [
    ("/abc123.jpg", True),                  # TMDB file_path — the pre-existing form
    (TVDB_URL, True),                       # TVDB absolute URL
    ("/../etc/passwd", False),              # traversal
    ("https://evil.com/x.jpg", False),      # not an allowed host
    ("abc123.jpg", False),                  # neither form
])
def test_export_ref_validation_accepts_both_sources(ref, ok):
    assert _is_export_ref_valid(ref) is ok
