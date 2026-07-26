"""Tests for services/tvdb.py — the TheTVDB image source.

Covers the pure normalisation/grouping logic that turns TVDB's artwork records into the same
shape the TMDB gallery already renders. The HTTP layer is exercised through monkeypatched
transports rather than live calls.
"""
import pytest

import services.tvdb as tvdb


# ---------------------------------------------------------------- categorisation

@pytest.mark.parametrize("slug, expected", [
    ("posters", "poster"),
    ("poster", "poster"),
    ("backgrounds", "background"),
    ("fanart", "background"),
    ("clearlogo", "logo"),
    ("Clear Logo", "logo"),
    ("logo", "logo"),
    # Types that exist on TVDB but don't map onto a poster/backdrop/logo role.
    ("banners", ""),
    ("icons", ""),
    ("clearart", ""),
    ("", ""),
])
def test_categorize_maps_only_the_three_roles(slug, expected):
    assert tvdb._categorize(slug, "") == expected


def test_categorize_falls_back_to_the_type_name():
    assert tvdb._categorize("", "Background") == "background"


# ---------------------------------------------------------------- language + urls

def test_normalize_language_converts_three_letter_codes():
    assert tvdb.normalize_language("eng") == "en"
    assert tvdb.normalize_language("spa") == "es"
    # Both the terminological and bibliographic spellings resolve.
    assert tvdb.normalize_language("deu") == "de"
    assert tvdb.normalize_language("ger") == "de"


def test_normalize_language_treats_blank_as_textless():
    assert tvdb.normalize_language(None) is None
    assert tvdb.normalize_language("") is None


def test_normalize_language_passes_through_unknown_codes():
    assert tvdb.normalize_language("zzz") == "zzz"


def test_absolute_image_url_leaves_absolute_refs_alone():
    url = "https://artworks.thetvdb.com/banners/posters/1-2.jpg"
    assert tvdb.absolute_image_url(url) == url


def test_absolute_image_url_expands_relative_refs():
    assert tvdb.absolute_image_url("banners/posters/1-2.jpg") == \
        "https://artworks.thetvdb.com/banners/posters/1-2.jpg"


@pytest.mark.parametrize("url, ok", [
    ("https://artworks.thetvdb.com/banners/x.jpg", True),
    ("https://cdn.thetvdb.com/x.jpg", True),                 # any artwork subdomain
    ("https://thetvdb.com/x.jpg", False),                    # the site itself serves no artwork
    ("http://artworks.thetvdb.com/banners/x.jpg", False),    # must be https
    ("https://evil.com/x.jpg", False),
    ("https://artworks.thetvdb.com.evil.com/x.jpg", False),  # suffix must be a real label
    ("", False),
])
def test_is_tvdb_image_url_allowlists_the_artwork_host(url, ok):
    assert tvdb.is_tvdb_image_url(url) is ok


# ---------------------------------------------------------------- grouping

# id -> (category, record_type), the shape artwork_types() returns. These are the real ids from
# a live /artwork/types call (Jul 2026) — the same type name has a different id per record type.
TYPES = {
    2: ("poster", "series"),
    3: ("background", "series"),
    23: ("logo", "series"),
    7: ("poster", "season"),
    8: ("background", "season"),
    14: ("poster", "movie"),
    15: ("background", "movie"),
    25: ("logo", "movie"),
    27: ("poster", "list"),
}


def _record(type_id, *, image="https://artworks.thetvdb.com/a.jpg", language="eng",
            includes_text=True, score=0, width=1000, height=1500):
    return {"type": type_id, "image": image, "language": language,
            "includesText": includes_text, "score": score, "width": width, "height": height}


def test_group_artwork_buckets_by_category():
    records = [_record(2), _record(3), _record(23)]
    out = tvdb.group_artwork(records, TYPES, "all")
    assert len(out["posters"]) == 1
    assert len(out["backdrops"]) == 1
    assert len(out["logos"]) == 1


def test_group_artwork_drops_unmapped_and_imageless_records():
    records = [_record(999), _record(2, image=""), _record(2)]
    out = tvdb.group_artwork(records, TYPES, "all")
    assert len(out["posters"]) == 1


def test_group_artwork_excludes_season_artwork_from_the_series_tabs():
    """/series/{id}/artworks really does return season artwork inline — a live Breaking Bad call
    came back with 154 season posters (type 7) alongside its 45 series posters (type 2), so
    without this filter the series poster tab shows every season's poster."""
    records = [_record(2)] + [_record(7) for _ in range(10)] + [_record(8)]
    out = tvdb.group_artwork(records, TYPES, "all")
    assert len(out["posters"]) == 1
    assert len(out["backdrops"]) == 0


def test_group_artwork_excludes_list_artwork():
    """TVDB's 'list' record has its own poster type (27), which isn't the title's artwork."""
    out = tvdb.group_artwork([_record(14), _record(27)], TYPES, "all")
    assert len(out["posters"]) == 1


def test_group_artwork_handles_movie_type_ids():
    """Movies use a different id per category than series do."""
    out = tvdb.group_artwork([_record(14), _record(15), _record(25)], TYPES, "all")
    assert (len(out["posters"]), len(out["backdrops"]), len(out["logos"])) == (1, 1, 1)


def test_group_artwork_keeps_season_artwork_when_title_only_is_off():
    out = tvdb.group_artwork([_record(7)], TYPES, "all", title_only=False)
    assert len(out["posters"]) == 1


def test_group_artwork_marks_includes_text_false_as_textless():
    out = tvdb.group_artwork([_record(2, language="eng", includes_text=False)], TYPES, "all")
    assert out["posters"][0]["language"] is None


def test_group_artwork_treats_missing_language_as_textless():
    out = tvdb.group_artwork([_record(2, language=None)], TYPES, "all")
    assert out["posters"][0]["language"] is None


def test_group_artwork_en_textless_filter_keeps_english_and_textless_only():
    records = [
        _record(2, language="eng"),
        _record(2, language="spa"),
        _record(2, language="eng", includes_text=False),
    ]
    out = tvdb.group_artwork(records, TYPES, "en+textless")
    assert [i["language"] for i in out["posters"]] == [None, "en"]  # textless sorts first


def test_group_artwork_specific_language_excludes_textless():
    records = [_record(2, language="spa"), _record(2, language=None)]
    out = tvdb.group_artwork(records, TYPES, "es")
    assert [i["language"] for i in out["posters"]] == ["es"]


def test_group_artwork_sorts_textless_first_then_by_score():
    records = [
        _record(2, language="eng", score=10),
        _record(2, language="eng", score=99),
        _record(2, language=None, score=1),
    ]
    out = tvdb.group_artwork(records, TYPES, "all")
    assert [i["vote_average"] for i in out["posters"]] == [1, 99, 10]


def test_group_artwork_emits_the_tmdb_gallery_shape():
    out = tvdb.group_artwork([_record(2, image="https://artworks.thetvdb.com/p.jpg")], TYPES, "all")
    entry = out["posters"][0]
    assert set(entry) == {"file_path", "width", "height", "language", "vote_average",
                          "url_thumb", "url_full"}
    # TVDB refs are absolute URLs, unlike TMDB's leading-slash file paths.
    assert entry["file_path"] == "https://artworks.thetvdb.com/p.jpg"
    assert entry["url_full"] == "https://artworks.thetvdb.com/p.jpg"


def test_group_artwork_falls_back_to_the_full_image_when_no_thumbnail():
    out = tvdb.group_artwork([_record(2)], TYPES, "all")
    assert out["posters"][0]["url_thumb"] == out["posters"][0]["url_full"]


# ---------------------------------------------------------------- season list

def _season(number, order="official", season_id=None, name="", image="", year=None):
    return {"id": season_id if season_id is not None else 100 + number,
            "number": number, "name": name, "image": image, "year": year,
            "type": {"type": order}}


def test_fetch_series_seasons_keeps_only_the_official_order(monkeypatch):
    """TVDB stacks several season orders in one array — a live Simpsons record carried 72 seasons
    (38 official + 33 dvd + 1 absolute). Without this filter the card would show all 72."""
    monkeypatch.setattr(tvdb, "_get", lambda *a, **k: {"seasons": [
        _season(0), _season(1), _season(2),
        _season(1, "dvd", season_id=901), _season(2, "dvd", season_id=902),
        _season(1, "absolute", season_id=903),
    ]})
    seasons = tvdb.fetch_series_seasons(tvdb_id=1, api_key="k", pin="")
    assert [s["number"] for s in seasons] == [0, 1, 2]
    assert [s["id"] for s in seasons] == [100, 101, 102]


def test_fetch_series_seasons_sorts_unordered_input(monkeypatch):
    monkeypatch.setattr(tvdb, "_get", lambda *a, **k: {"seasons": [
        _season(3), _season(1), _season(0), _season(2),
    ]})
    seasons = tvdb.fetch_series_seasons(tvdb_id=1, api_key="k", pin="")
    assert [s["number"] for s in seasons] == [0, 1, 2, 3]


def test_fetch_series_seasons_keeps_seasons_with_no_order_type(monkeypatch):
    """An untyped season is kept rather than dropped — same fail-safe stance as record types."""
    monkeypatch.setattr(tvdb, "_get", lambda *a, **k: {"seasons": [{"id": 5, "number": 1}]})
    assert len(tvdb.fetch_series_seasons(tvdb_id=1, api_key="k", pin="")) == 1


def test_fetch_series_seasons_normalizes_fields(monkeypatch):
    monkeypatch.setattr(tvdb, "_get", lambda *a, **k: {"seasons": [
        _season(1, name="  Book One  ", image="banners/s1.jpg", year="2020"),
    ]})
    season = tvdb.fetch_series_seasons(tvdb_id=1, api_key="k", pin="")[0]
    assert season["name"] == "Book One"
    assert season["image"] == "https://artworks.thetvdb.com/banners/s1.jpg"
    assert season["year"] == "2020"


def test_fetch_series_seasons_skips_unparseable_rows(monkeypatch):
    monkeypatch.setattr(tvdb, "_get", lambda *a, **k: {"seasons": [
        _season(1), {"id": "x", "number": "y"}, "not-a-dict", {"number": 2},
    ]})
    assert [s["number"] for s in tvdb.fetch_series_seasons(tvdb_id=1, api_key="k", pin="")] == [1]


def test_fetch_series_seasons_handles_a_missing_series(monkeypatch):
    monkeypatch.setattr(tvdb, "_get", lambda *a, **k: None)   # 404 → _get returns None
    assert tvdb.fetch_series_seasons(tvdb_id=1, api_key="k", pin="") == []


def test_fetch_season_artwork_resolves_the_number_through_the_season_list(monkeypatch):
    monkeypatch.setattr(tvdb, "fetch_series_seasons",
                        lambda **k: [{"id": 55, "number": 2, "name": "", "image": "", "year": None}])
    calls = []

    def fake_get(path, *a, **k):
        calls.append(path)
        return {"artwork": [{"type": 7}]}

    monkeypatch.setattr(tvdb, "_get", fake_get)
    out = tvdb.fetch_season_artwork(tvdb_id=1, season_number=2, api_key="k", pin="")
    assert calls == ["/seasons/55/extended"]
    assert len(out) == 1


def test_fetch_season_artwork_returns_empty_for_an_unknown_season(monkeypatch):
    monkeypatch.setattr(tvdb, "fetch_series_seasons", lambda **k: [{"id": 55, "number": 2}])
    assert tvdb.fetch_season_artwork(tvdb_id=1, season_number=9, api_key="k", pin="") == []


# ---------------------------------------------------------------- id resolution

def test_resolve_tvdb_id_prefers_a_carried_id():
    assert tvdb.resolve_tvdb_id(media_type="tv", tvdb_id=1234, imdb_id="tt1",
                                api_key="k", pin="") == 1234


def test_resolve_tvdb_id_returns_none_for_tv_without_an_id():
    """TV never falls back to the IMDb search — TMDB already supplies its tvdb_id."""
    assert tvdb.resolve_tvdb_id(media_type="tv", tvdb_id=None, imdb_id="tt1",
                                api_key="k", pin="") is None


def test_resolve_tvdb_id_uses_the_remote_search_for_movies(monkeypatch):
    monkeypatch.setattr(tvdb, "resolve_movie_tvdb_id", lambda imdb, k, p: 555)
    assert tvdb.resolve_tvdb_id(media_type="movie", tvdb_id=0, imdb_id="tt1",
                                api_key="k", pin="") == 555


def test_resolve_movie_tvdb_id_picks_the_movie_entry(monkeypatch):
    monkeypatch.setattr(tvdb, "_get", lambda *a, **k: [
        {"series": {"id": 11}},
        {"movie": {"id": 22}},
    ])
    assert tvdb.resolve_movie_tvdb_id("tt123", "k", "") == 22


def test_resolve_movie_tvdb_id_returns_none_without_an_imdb_id():
    assert tvdb.resolve_movie_tvdb_id("", "k", "") is None
