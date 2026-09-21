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
    tvdb._seasons_cache.clear()
    tvdb._types_cache = None
    tvdb._consecutive_failures = 0
    tvdb._circuit_open_until = 0.0
    yield
    tvdb._token_cache.clear()
    tvdb._seasons_cache.clear()
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
    assert response.json() == {"posters": [], "backdrops": [], "logos": [], "season_posters": None}


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


def test_tvdb_images_reports_the_seasons_with_posters(client, test_db, monkeypatch):
    """Season posters ride along inline on /artworks, keyed by seasonId; the gallery's Seasons
    tab and chips are gated on them."""
    _set_key(test_db)
    monkeypatch.setattr(tvdb, "resolve_tvdb_id", lambda **kwargs: 99)
    monkeypatch.setattr(tvdb, "artwork_types", lambda k, p: {2: ("poster", "series"), 7: ("poster", "season"),
                                                            8: ("background", "season")})
    monkeypatch.setattr(tvdb, "fetch_season_numbers", lambda **kwargs: {30272: 1, 40719: 2})
    art = {"image": TVDB_URL, "score": 5, "width": 680, "height": 1000}
    monkeypatch.setattr(tvdb, "fetch_artwork", lambda **kwargs: [
        {"type": 2, "language": "eng", "includesText": True, **art},
        {"type": 7, "language": "eng", "includesText": True, "seasonId": 30272, **art},
        {"type": 7, "language": "deu", "includesText": True, "seasonId": 40719, **art},
        {"type": 8, "language": None, "includesText": False, "seasonId": 40719, **art},     # a background, not a poster
        {"type": 7, "language": None, "includesText": False, "seasonId": 1726855, **art},   # DVD-order season: maps to nothing
    ])

    response = client.get("/api/maker-tools/tvdb/images", params={"media_type": "tv", "tvdb_id": 99})
    assert response.status_code == 200
    data = response.json()
    assert len(data["posters"]) == 1            # the series' own poster only
    assert data["season_posters"] == [1]        # season 2's poster is German, outside the default preference

    response = client.get("/api/maker-tools/tvdb/images", params={"media_type": "tv", "tvdb_id": 99, "language": "all"})
    assert response.json()["season_posters"] == [1, 2]


def test_tvdb_images_leaves_the_season_hint_unknown_when_the_season_list_fails(client, test_db, monkeypatch):
    _set_key(test_db)
    monkeypatch.setattr(tvdb, "resolve_tvdb_id", lambda **kwargs: 99)
    monkeypatch.setattr(tvdb, "artwork_types", lambda k, p: {2: ("poster", "series")})
    monkeypatch.setattr(tvdb, "fetch_artwork", lambda **kwargs: [])

    def _boom(**kwargs):
        raise tvdb.TvdbError("TheTVDB is down", status=502)

    monkeypatch.setattr(tvdb, "fetch_season_numbers", _boom)
    response = client.get("/api/maker-tools/tvdb/images", params={"media_type": "tv", "tvdb_id": 99})
    assert response.status_code == 200
    assert response.json()["season_posters"] is None


def test_tvdb_images_movies_carry_no_season_hint(client, test_db, monkeypatch):
    _set_key(test_db)
    monkeypatch.setattr(tvdb, "resolve_tvdb_id", lambda **kwargs: 5)
    monkeypatch.setattr(tvdb, "artwork_types", lambda k, p: {14: ("poster", "movie")})
    monkeypatch.setattr(tvdb, "fetch_artwork", lambda **kwargs: [])
    monkeypatch.setattr(tvdb, "fetch_season_numbers",
                        lambda **kwargs: (_ for _ in ()).throw(AssertionError("no season lookup for a movie")))
    response = client.get("/api/maker-tools/tvdb/images", params={"media_type": "movie", "tvdb_id": 5})
    assert response.status_code == 200
    assert response.json()["season_posters"] is None


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
