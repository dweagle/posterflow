"""Tests for services/fanart.py — the fanart.tv image source.

Covers the shaping that turns fanart.tv records into the finder's candidate groups, the id
routing of lookups, and the transport's error mapping via a monkeypatched requests.get.
"""
import pytest
import requests

import services.fanart as fanart

HD_LOGO = "https://assets.fanart.tv/fanart/movies/550/hdmovielogo/fight-club-1.png"


@pytest.fixture(autouse=True)
def _reset_record_cache():
    """The per-path record cache is module-level; keep tests from seeing each other's answers."""
    fanart._record_cache.clear()
    yield
    fanart._record_cache.clear()


def _movie_record():
    return {
        "name": "Fight Club",
        "hdmovielogo": [
            {"id": "1", "url": HD_LOGO, "lang": "en", "likes": "3"},
            # http:// is what older fanart.tv responses carry — upgraded, not dropped
            {"id": "2", "url": "http://assets.fanart.tv/fanart/movies/550/hdmovielogo/fight-club-2.png",
             "lang": "de", "likes": "9"},
        ],
        "movielogo": [{"id": "3", "url": "https://assets.fanart.tv/fanart/movies/550/movielogo/sd.png",
                       "lang": "en", "likes": "50"}],
        "moviebackground": [
            {"id": "4", "url": "https://assets.fanart.tv/fanart/movies/550/moviebackground/bg-1.jpg",
             "lang": "en", "likes": "7"},
            {"id": "5", "url": "https://assets.fanart.tv/fanart/movies/550/moviebackground/bg-2.jpg",
             "lang": "00", "likes": "2"},
        ],
        "movieposter": [{"id": "6", "url": "https://assets.fanart.tv/fanart/movies/550/movieposter/p.jpg",
                         "lang": "en", "likes": "1"}],
        # Types the finder has no role for.
        "moviethumb": [{"id": "7", "url": "https://assets.fanart.tv/fanart/movies/550/moviethumb/t.jpg",
                        "lang": "en", "likes": "99"}],
        "moviedisc": [{"id": "8", "url": "https://assets.fanart.tv/fanart/movies/550/moviedisc/d.png",
                       "lang": "en", "likes": "1"}],
        "movie4kbackground": [{"id": "9", "url": "https://assets.fanart.tv/fanart/movies/550/movie4kbackground/4k.jpg",
                               "lang": "", "likes": "0"}],
        "moviesquare": [
            {"id": "10", "url": "https://assets.fanart.tv/fanart/movies/550/moviesquare/sq-text.jpg",
             "lang": "en", "likes": "4"},
            {"id": "11", "url": "https://assets.fanart.tv/fanart/movies/550/moviesquare/sq-clean.jpg",
             "lang": "00", "likes": "1"},
        ],
    }


# ---------------------------------------------------------------- language + urls

def test_normalize_language_treats_00_and_blank_as_textless():
    assert fanart.normalize_language("00") is None
    assert fanart.normalize_language("") is None
    assert fanart.normalize_language(None) is None
    assert fanart.normalize_language("EN") == "en"


def test_wanted_languages_accepts_both_the_gallery_and_tmdb_forms():
    assert fanart.wanted_languages(None) is None
    assert fanart.wanted_languages("") is None
    assert fanart.wanted_languages("all") is None
    assert fanart.wanted_languages("en+textless") == {"en", None}
    assert fanart.wanted_languages("en,null") == {"en", None}
    assert fanart.wanted_languages("de") == {"de"}


def test_preview_url_swaps_the_asset_path_segment():
    assert fanart.preview_url(HD_LOGO) == HD_LOGO.replace("/fanart/", "/preview/")


@pytest.mark.parametrize("url, ok", [
    (HD_LOGO, True),
    ("http://assets.fanart.tv/fanart/movies/550/hdmovielogo/x.png", False),
    ("https://evil.example.com/assets.fanart.tv/x.png", False),
    ("https://fanart.tv/x.png", False),
    ("", False),
])
def test_is_fanart_image_url_allows_only_the_https_asset_host(url, ok):
    assert fanart.is_fanart_image_url(url) is ok


# ---------------------------------------------------------------- grouping

def test_group_artwork_keeps_only_the_app_roles_with_fixed_dims():
    out = fanart.group_artwork(_movie_record(), "movie")
    assert set(out) == {"logos", "backgrounds", "posters", "squareart"}
    assert len(out["logos"]) == 3
    assert len(out["backgrounds"]) == 3
    assert len(out["posters"]) == 1
    assert len(out["squareart"]) == 2
    hd, sd = out["logos"][0], out["logos"][2]
    assert (hd["width"], hd["height"]) == (800, 310)
    assert (sd["width"], sd["height"]) == (400, 155)
    # 4K backgrounds lead, then 1080p.
    assert [(b["width"], b["height"]) for b in out["backgrounds"]] == [(3840, 2160), (1920, 1080), (1920, 1080)]
    assert (out["squareart"][0]["width"], out["squareart"][0]["height"]) == (1000, 1000)
    # Gallery fields ride along so the maker card renders these like TMDB/TVDB images.
    assert hd["url_full"] == hd["file_path"]
    assert hd["url_thumb"] == hd["file_path"].replace("/fanart/", "/preview/")
    assert hd["vote_average"] == float(hd["likes"])


def test_group_artwork_orders_hd_first_then_textless_then_likes():
    out = fanart.group_artwork(_movie_record(), "movie")
    # HD logos before the standard one even though the SD logo has the most likes.
    assert [l["file_path"].rsplit("/", 1)[1] for l in out["logos"]] == \
        ["fight-club-2.png", "fight-club-1.png", "sd.png"]
    # The http:// entry came through upgraded.
    assert out["logos"][0]["file_path"].startswith("https://assets.fanart.tv/")
    # Within a type: textless first, then by likes.
    assert [b["language"] for b in out["backgrounds"]] == [None, None, "en"]
    assert [s["language"] for s in out["squareart"]] == [None, "en"]


def test_group_artwork_honours_the_language_preference():
    out = fanart.group_artwork(_movie_record(), "movie", {"en", None})
    assert [l["language"] for l in out["logos"]] == ["en", "en"]
    assert [b["language"] for b in out["backgrounds"]] == [None, None, "en"]
    only_de = fanart.group_artwork(_movie_record(), "movie", {"de"})
    assert [l["language"] for l in only_de["logos"]] == ["de"]
    assert only_de["backgrounds"] == []
    assert only_de["squareart"] == []


def test_group_artwork_drops_foreign_hosts_and_junk_entries():
    record = {"hdmovielogo": [
        {"url": "https://evil.example.com/l.png", "lang": "en", "likes": "1"},
        "not-a-dict",
        {"url": HD_LOGO, "lang": "en", "likes": "not-a-number"},
    ]}
    out = fanart.group_artwork(record, "movie")
    assert [l["file_path"] for l in out["logos"]] == [HD_LOGO]
    assert out["logos"][0]["likes"] == 0


def test_group_artwork_uses_the_tv_types_for_series():
    record = {
        "hdtvlogo": [{"url": "https://assets.fanart.tv/fanart/tv/1/hdtvlogo/l.png", "lang": "en", "likes": "1"}],
        "clearlogo": [{"url": "https://assets.fanart.tv/fanart/tv/1/clearlogo/sd.png", "lang": "en", "likes": "1"}],
        "showbackground": [{"url": "https://assets.fanart.tv/fanart/tv/1/showbackground/bg.jpg", "lang": "00",
                            "likes": "1", "season": "all"}],
        "tvposter": [{"url": "https://assets.fanart.tv/fanart/tv/1/tvposter/p.jpg", "lang": "en", "likes": "1"}],
        "seasonposter": [
            {"url": "https://assets.fanart.tv/fanart/tv/1/seasonposter/s1-a.jpg", "lang": "en", "likes": "1", "season": "1"},
            {"url": "https://assets.fanart.tv/fanart/tv/1/seasonposter/s1-b.jpg", "lang": "en", "likes": "7", "season": "1"},
            {"url": "https://assets.fanart.tv/fanart/tv/1/seasonposter/s2.jpg", "lang": "en", "likes": "9", "season": "2"},
            {"url": "https://assets.fanart.tv/fanart/tv/1/seasonposter/s0.jpg", "lang": "en", "likes": "0", "season": "0"},
            {"url": "https://assets.fanart.tv/fanart/tv/1/seasonposter/all.jpg", "lang": "en", "likes": "50", "season": "all"},
        ],
        "tvsquare": [{"url": "https://assets.fanart.tv/fanart/tv/1/tvsquare/sq.jpg", "lang": "en", "likes": "2"}],
        # Movie types on a series record are ignored.
        "hdmovielogo": [{"url": HD_LOGO, "lang": "en", "likes": "1"}],
    }
    out = fanart.group_artwork(record, "tv")
    assert [l["file_path"].rsplit("/", 1)[1] for l in out["logos"]] == ["l.png", "sd.png"]
    assert len(out["backgrounds"]) == 1 and out["backgrounds"][0]["language"] is None
    # Season posters never pollute the series' own poster list.
    assert [p["file_path"].rsplit("/", 1)[1] for p in out["posters"]] == ["p.jpg"]
    assert [s["file_path"].rsplit("/", 1)[1] for s in out["squareart"]] == ["sq.jpg"]
    # One season at a time, most liked first; "all" and other seasons stay out.
    assert [p["file_path"].rsplit("/", 1)[1] for p in fanart.season_posters(record, 1)] == ["s1-b.jpg", "s1-a.jpg"]
    assert [p["file_path"].rsplit("/", 1)[1] for p in fanart.season_posters(record, 0)] == ["s0.jpg"]
    assert fanart.season_posters(record, 3) == []
    assert fanart.season_posters(record, 1, {"de"}) == []


# ---------------------------------------------------------------- lookups

def test_fetch_artwork_routes_by_the_right_id(monkeypatch):
    calls = []
    monkeypatch.setattr(fanart, "_get", lambda path, key, what: calls.append(path) or {"name": "x"})

    assert fanart.fetch_artwork(media_type="tv", tmdb_id=1, imdb_id="tt1", tvdb_id=99, api_key="k") == {"name": "x"}
    assert fanart.fetch_artwork(media_type="movie", tmdb_id=550, imdb_id="tt0137523", tvdb_id=None, api_key="k")
    assert fanart.fetch_artwork(media_type="movie", tmdb_id=None, imdb_id="tt0137523", tvdb_id=None, api_key="k")
    assert calls == ["/tv/99", "/movies/550", "/movies/tt0137523"]


def test_fetch_artwork_is_empty_without_a_usable_id(monkeypatch):
    monkeypatch.setattr(fanart, "_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no lookup expected")))
    assert fanart.fetch_artwork(media_type="tv", tmdb_id=1, imdb_id=None, tvdb_id=None, api_key="k") == {}
    assert fanart.fetch_artwork(media_type="movie", tmdb_id=None, imdb_id=None, tvdb_id=5, api_key="k") == {}
    assert fanart.fetch_artwork(media_type="collection", tmdb_id=10, imdb_id=None, tvdb_id=None, api_key="k") == {}


def test_fetch_artwork_is_empty_when_fanart_has_nothing(monkeypatch):
    monkeypatch.setattr(fanart, "_get", lambda *a, **k: None)
    assert fanart.fetch_artwork(media_type="movie", tmdb_id=550, imdb_id=None, tvdb_id=None, api_key="k") == {}


# ---------------------------------------------------------------- transport

class _Resp:
    def __init__(self, status, payload=None, bad_json=False):
        self.status_code = status
        self._payload = payload
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._payload


def _patch_get(monkeypatch, resp):
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen.update(url=url, params=params, timeout=timeout)
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(fanart.requests, "get", fake_get)
    return seen


def test_get_sends_the_users_key_in_both_slots_without_a_project_key(monkeypatch):
    monkeypatch.setattr(fanart, "PROJECT_KEY", "")
    seen = _patch_get(monkeypatch, _Resp(200, {"name": "x"}))
    assert fanart._get("/movies/550", "personal", what="movie") == {"name": "x"}
    assert seen["url"] == "https://webservice.fanart.tv/v3/movies/550"
    assert seen["params"] == {"api_key": "personal", "client_key": "personal"}


def test_get_prefers_the_project_key_when_one_is_registered(monkeypatch):
    monkeypatch.setattr(fanart, "PROJECT_KEY", "project")
    seen = _patch_get(monkeypatch, _Resp(200, {"name": "x"}))
    fanart._get("/movies/550", "personal", what="movie")
    assert seen["params"] == {"api_key": "project", "client_key": "personal"}


def test_get_treats_not_found_as_nothing(monkeypatch):
    _patch_get(monkeypatch, _Resp(404, {"status": "error", "error message": "not found"}))
    assert fanart._get("/movies/1", "k", what="movie") is None
    _patch_get(monkeypatch, _Resp(200, {"status": "error", "error message": "not found"}))
    assert fanart._get("/movies/2", "k", what="movie") is None


def test_get_caches_each_record_for_a_while(monkeypatch):
    seen = _patch_get(monkeypatch, _Resp(200, {"name": "x"}))
    assert fanart._get("/tv/1", "k", what="series") == {"name": "x"}
    seen.clear()
    assert fanart._get("/tv/1", "k", what="series") == {"name": "x"}
    assert seen == {}                       # served from the cache, no second request
    # A "nothing here" answer is cached too, so a season picker doesn't re-ask.
    _patch_get(monkeypatch, _Resp(404))
    assert fanart._get("/tv/2", "k", what="series") is None
    seen2 = _patch_get(monkeypatch, _Resp(200, {"name": "late"}))
    assert fanart._get("/tv/2", "k", what="series") is None
    assert seen2 == {}
    # Failures are never cached — the next call asks again.
    _patch_get(monkeypatch, _Resp(503))
    with pytest.raises(fanart.FanartError):
        fanart._get("/tv/3", "k", what="series")
    seen3 = _patch_get(monkeypatch, _Resp(200, {"name": "back"}))
    assert fanart._get("/tv/3", "k", what="series") == {"name": "back"}
    assert seen3["url"].endswith("/tv/3")


def test_get_maps_failures_to_fanart_errors(monkeypatch):
    _patch_get(monkeypatch, _Resp(401, {"status": "error"}))
    with pytest.raises(fanart.FanartError) as exc:
        fanart._get("/movies/1", "bad", what="movie")
    assert exc.value.status == 401

    _patch_get(monkeypatch, _Resp(503))
    with pytest.raises(fanart.FanartError) as exc:
        fanart._get("/movies/1", "k", what="movie")
    assert exc.value.status == 502

    _patch_get(monkeypatch, _Resp(200, bad_json=True))
    with pytest.raises(fanart.FanartError):
        fanart._get("/movies/1", "k", what="movie")

    _patch_get(monkeypatch, requests.ConnectionError("down"))
    with pytest.raises(fanart.FanartError) as exc:
        fanart._get("/movies/1", "k", what="movie")
    assert "Could not reach" in str(exc.value)
