"""Tests for the fanart.tv image-browser endpoints in api/maker_tools.py."""
import pytest

import services.fanart as fanart
from api.maker_tools import _is_export_ref_valid
from models.setting import Setting

FANART_URL = "https://assets.fanart.tv/fanart/breaking-bad-503d6f03d4bfe.png"


@pytest.fixture(autouse=True)
def _clear_record_cache():
    fanart._record_cache.clear()
    yield
    fanart._record_cache.clear()


def _set_key(test_db, value="fanart-key"):
    test_db.add(Setting(key="fanart_api_key", value=value))
    test_db.commit()


def _record():
    return {
        "hdtvlogo": [{"url": FANART_URL, "lang": "en", "likes": "3"}],
        "showbackground": [{"url": "https://assets.fanart.tv/fanart/bb-bg.jpg", "lang": "", "likes": "1"}],
        "show4kbackground": [{"url": "https://assets.fanart.tv/fanart/bb-4k.jpg", "lang": "", "likes": "0"}],
        "tvposter": [{"url": "https://assets.fanart.tv/fanart/bb-p.jpg", "lang": "de", "likes": "9"}],
        "tvsquare": [{"url": "https://assets.fanart.tv/fanart/bb-sq.jpg", "lang": "en", "likes": "2"}],
        "seasonposter": [
            {"url": "https://assets.fanart.tv/fanart/bb-s1.jpg", "lang": "en", "likes": "4", "season": "1"},
            {"url": "https://assets.fanart.tv/fanart/bb-s2.jpg", "lang": "en", "likes": "4", "season": "2"},
        ],
    }


# ---------------------------------------------------------------- /fanart/images

def test_fanart_images_requires_a_configured_key(client):
    response = client.get("/api/maker-tools/fanart/images", params={"media_type": "tv", "tvdb_id": 1})
    assert response.status_code == 400
    assert "not configured" in response.json()["detail"]


def test_fanart_images_rejects_an_unknown_media_type(client, test_db):
    _set_key(test_db)
    response = client.get("/api/maker-tools/fanart/images", params={"media_type": "person", "tvdb_id": 1})
    assert response.status_code == 400


def test_fanart_images_returns_empty_for_collections(client, test_db):
    """fanart.tv has no collection entity, so this is 'nothing here', not an error."""
    _set_key(test_db)
    response = client.get("/api/maker-tools/fanart/images",
                          params={"media_type": "collection", "tmdb_id": 5})
    assert response.status_code == 200
    assert response.json() == {"posters": [], "backdrops": [], "logos": []}


def test_fanart_images_returns_empty_without_a_usable_id(client, test_db, monkeypatch):
    """A series with no TheTVDB id can't be looked up — an empty gallery beats an error toast."""
    _set_key(test_db)
    monkeypatch.setattr(fanart, "_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no lookup expected")))
    response = client.get("/api/maker-tools/fanart/images", params={"media_type": "tv", "tmdb_id": 1396})
    assert response.status_code == 200
    assert response.json()["posters"] == []


def test_fanart_images_maps_artwork_into_the_gallery_shape(client, test_db, monkeypatch):
    _set_key(test_db)
    seen = {}

    def fake_fetch(**kwargs):
        seen.update(kwargs)
        return _record()

    monkeypatch.setattr(fanart, "fetch_artwork", fake_fetch)

    response = client.get("/api/maker-tools/fanart/images",
                          params={"media_type": "tv", "tvdb_id": 81189, "language": "all"})
    assert response.status_code == 200
    assert seen["tvdb_id"] == 81189 and seen["api_key"] == "fanart-key"
    data = response.json()
    assert data["posters"][0]["file_path"] == "https://assets.fanart.tv/fanart/bb-p.jpg"
    assert data["posters"][0]["language"] == "de"
    # 4K backgrounds first; both are textless.
    assert [(b["width"], b["language"]) for b in data["backdrops"]] == [(3840, None), (1920, None)]
    assert data["logos"][0]["url_full"] == FANART_URL
    assert data["logos"][0]["url_thumb"] == FANART_URL.replace("/fanart/", "/preview/")
    assert data["logos"][0]["vote_average"] == 3.0
    # Square art and season posters never show up in the gallery lists.
    assert not any("bb-sq" in p["file_path"] or "bb-s1" in p["file_path"] for p in data["posters"])

    # Default language preference drops the German poster but keeps textless backgrounds.
    response = client.get("/api/maker-tools/fanart/images", params={"media_type": "tv", "tvdb_id": 81189})
    data = response.json()
    assert data["posters"] == []
    assert len(data["backdrops"]) == 2
    assert len(data["logos"]) == 1


def test_fanart_images_surfaces_a_fanart_failure_with_its_status(client, test_db, monkeypatch):
    _set_key(test_db)

    def _boom(**kwargs):
        raise fanart.FanartError("fanart.tv rejected the API key.", status=401)

    monkeypatch.setattr(fanart, "fetch_artwork", _boom)
    response = client.get("/api/maker-tools/fanart/images", params={"media_type": "tv", "tvdb_id": 1})
    assert response.status_code == 401
    assert "rejected" in response.json()["detail"]


# ---------------------------------------------------------------- /fanart/season-images

def test_fanart_season_images_returns_empty_without_a_series_id(client, test_db):
    _set_key(test_db)
    response = client.get("/api/maker-tools/fanart/season-images",
                          params={"tvdb_id": 0, "season_number": 1})
    assert response.status_code == 200
    assert response.json()["posters"] == []


def test_fanart_season_images_lists_one_season(client, test_db, monkeypatch):
    _set_key(test_db)
    monkeypatch.setattr(fanart, "fetch_artwork", lambda **kwargs: _record())
    response = client.get("/api/maker-tools/fanart/season-images",
                          params={"tvdb_id": 81189, "season_number": 2})
    assert response.status_code == 200
    data = response.json()
    assert [p["file_path"] for p in data["posters"]] == ["https://assets.fanart.tv/fanart/bb-s2.jpg"]
    assert data["backdrops"] == [] and data["logos"] == []


# ---------------------------------------------------------------- export refs

@pytest.mark.parametrize("ref, ok", [
    (FANART_URL, True),                                    # fanart.tv absolute URL
    ("http://assets.fanart.tv/fanart/x.png", False),       # plain http never
    ("https://fanart.tv/x.png", False),                    # the site, not the asset host
])
def test_export_ref_validation_accepts_fanart_assets(ref, ok):
    assert _is_export_ref_valid(ref) is ok
