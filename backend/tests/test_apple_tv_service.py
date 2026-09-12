"""Tests for services/apple_tv.py — the Apple TV image source.

Covers the storefront plan, the title matching that picks our title out of Apple's fuzzy search,
the shaping of a listing's image templates into the finder's candidate groups, and the transport's
error mapping via a monkeypatched requests.get.
"""
import pytest
import requests

import services.apple_tv as apple

TMPL = "https://is1-ssl.mzstatic.com/image/thumb/abc/pr_source.jpg/{w}x{h}.{f}"
HERO = "https://is1-ssl.mzstatic.com/image/thumb/hero/{w}x{h}{c}.{f}"
TALL = "https://is1-ssl.mzstatic.com/image/thumb/tall/{w}x{h}{c}.{f}"


@pytest.fixture(autouse=True)
def _reset_cache():
    apple._cache.clear()
    yield
    apple._cache.clear()


def _show(title="DECA-DENCE"):
    return {"id": "umc.cmc.1", "type": "Show", "title": title, "images": {
        "coverArt": {"url": TMPL, "width": 3000, "height": 3000},
        "coverArt16X9": {"url": TMPL, "width": 1920, "height": 1080},
        "previewFrame": {"url": TMPL, "width": 3840, "height": 2160},
        "centeredFullScreenBackgroundImage": {"url": HERO, "width": 4320, "height": 3240},
        "centeredFullScreenBackgroundSmallImage": {"url": TALL, "width": 1680, "height": 3636},
        "fullColorContentLogo": {"url": TMPL, "width": 4315, "height": 878},
        "singleColorContentLogo": {"url": TMPL, "width": 4304, "height": 736},
    }}


def _movie(title="Alien", release_ms=296438400000):
    return {"id": "umc.cmc.2", "type": "Movie", "title": title, "releaseDate": release_ms, "images": {
        "coverArt": {"url": TMPL, "width": 2000, "height": 3000},
        "fullColorContentLogo": {"url": TMPL, "width": 4319, "height": 251},
    }}


# ---------------------------------------------------------------- storefront plan

def test_plan_prefers_english_stores_that_list_the_title():
    plan = apple.plan_storefronts(["DE", "GB", "US", "AU"], ["JP"])
    assert (plan.iso, plan.storefront) == ("US", "143441")
    assert plan.sold_in == ["US", "GB", "AU", "DE"]
    assert plan.search_order == [("US", "143441"), ("GB", "143444"), ("AU", "143460"), ("DE", "143443")]
    assert apple.plan_storefronts(["AU", "GB"], ["JP"]).iso == "GB"


def test_plan_then_origin_then_any_other_store_then_us():
    plan = apple.plan_storefronts(["DE", "FR"], ["FR"])
    assert plan.iso == "FR" and plan.sold_in == ["FR", "DE"]
    assert [iso for iso, _ in plan.search_order] == ["FR", "DE", "US"]
    assert apple.plan_storefronts(["DE"], ["FR"]).search_order[0][0] == "DE"


def test_plan_falls_back_to_origin_then_us_without_store_data():
    assert apple.plan_storefronts([], ["FR"]).search_order == [("FR", "143442"), ("US", "143441")]
    assert apple.plan_storefronts([], []).search_order == [("US", "143441")]


def test_plan_skips_native_title_stores_and_unknown_codes():
    plan = apple.plan_storefronts(["JP"], ["JP"])
    assert plan.iso == "US" and plan.sold_in == ["JP"] and plan.search_order == [("US", "143441")]
    assert apple.plan_storefronts(["JP", "GB"], ["JP"]).search_order[0][0] == "GB"
    assert apple.plan_storefronts(["XX"], ["ZZ"]).search_order == [("US", "143441")]


def test_plan_normalizes_dedupes_and_caps_the_stores_tried():
    plan = apple.plan_storefronts(["gb", " GB "], ["gb"])
    assert plan.iso == "GB" and plan.sold_in == ["GB"]
    many = apple.plan_storefronts(["AR", "AT", "BE", "BR", "CL", "CO", "DE"], [])
    assert len(many.search_order) == apple.MAX_STOREFRONTS_TRIED


def test_storefront_language():
    assert apple.storefront_language("AU") == "en"
    assert apple.storefront_language("DE") == "de"
    assert apple.storefront_language("BR") == "pt"
    assert apple.storefront_language("XK") == "xk"


# ---------------------------------------------------------------- title matching

@pytest.mark.parametrize("raw, cleaned", [
    ("Spider-Man: No Way Home", "Spider Man No Way Home"),
    ("Mission: Impossible – Dead Reckoning", "Mission Impossible Dead Reckoning"),
    ("Schitt's Creek", "Schitts Creek"),
    ("M*A*S*H", "MASH"),
    ("Face/Off", "Face Off"),
    ("  Mr.   Robot  ", "Mr Robot"),
    ("Amélie", "Amélie"),
])
def test_clean_query(raw, cleaned):
    assert apple.clean_query(raw) == cleaned


def test_normalize_title_is_blind_to_case_accents_and_punctuation():
    assert apple.normalize_title("DECA-DENCE") == apple.normalize_title("Deca-Dence") == "deca dence"
    assert apple.normalize_title("Amélie") == "amelie"
    assert apple.normalize_title("Fast & Furious") == apple.normalize_title("Fast and Furious")
    assert apple.normalize_title("Schitt’s Creek") == "schitts creek"
    assert apple.normalize_title("") == ""


def test_find_title_matches_the_right_kind_by_normalized_name():
    items = [_movie("Deca-Dence"), _show("DECA-DENCE"), _show("Decadence")]
    assert apple.find_title(items, "Deca-Dence", 2020, "tv") is items[1]
    assert apple.find_title(items, "Deca-Dence", None, "movie") is items[0]
    assert apple.find_title(items, "Deca-Dence", 2020, "collection") is None
    assert apple.find_title(items, "", 2020, "tv") is None


def test_find_title_ignores_a_trailing_parenthetical_but_prefers_exact():
    dubbed = _show("Deca-Dence (Original Japanese Version)")
    assert apple.find_title([dubbed], "Deca-Dence", None, "tv") is dubbed
    exact = _show("Deca-Dence")
    assert apple.find_title([dubbed, exact], "Deca-Dence", None, "tv") is exact


def test_find_title_checks_a_movies_year_within_one():
    remake = _movie("Alien", 1735689600000)   # 2025
    original = _movie("Alien")                # 1979
    assert apple.find_title([remake, original], "Alien", 1979, "movie") is original
    assert apple.find_title([remake, original], "Alien", 1980, "movie") is original
    assert apple.find_title([remake], "Alien", 1979, "movie") is None
    undated = _movie("Alien", None)
    assert apple.find_title([undated], "Alien", 1979, "movie") is undated


def test_item_year():
    assert apple.item_year(_movie()) == 1979
    assert apple.item_year({"releaseDate": -631152000000}) == 1950
    assert apple.item_year({"releaseDate": True}) is None
    assert apple.item_year({}) is None


# ---------------------------------------------------------------- shaping

def test_group_artwork_puts_a_shows_square_cover_in_squareart_and_a_movies_in_posters():
    show = apple.group_artwork(_show(), None, "en")
    assert [(s["width"], s["height"], s["language"]) for s in show["squareart"]] == [(3000, 3000, "en")]
    # The tall phone hero is offered whole among the posters, textless.
    assert [(p["width"], p["height"], p["language"]) for p in show["posters"]] == [(1680, 3636, None)]
    assert show["posters"][0]["url_thumb"].endswith("/400x866.jpg")
    assert show["squareart"][0]["url_full"].endswith("/3000x3000.jpg")
    assert show["squareart"][0]["url_thumb"].endswith("/400x400.jpg")

    movie = apple.group_artwork(_movie(), None, "en")
    assert [(p["width"], p["height"]) for p in movie["posters"]] == [(2000, 3000)]
    assert movie["posters"][0]["url_thumb"].endswith("/400x600.jpg")
    assert movie["squareart"] == []


def test_group_artwork_logos_are_png_and_backgrounds_keep_their_shapes():
    groups = apple.group_artwork(_show(), None, "en")
    assert [l["url_full"][-4:] for l in groups["logos"]] == [".png", ".png"]
    assert groups["logos"][0]["width"] == 4315 and groups["logos"][0]["language"] == "en"
    bgs = {b["width"]: b for b in groups["backgrounds"]}
    assert bgs[1920]["language"] == "en"            # 16:9 cover art carries the title
    assert bgs[3840]["language"] is None            # a still frame
    hero = bgs[4320]                                # the 4:3 hero, whole
    assert hero["height"] == 3240 and hero["language"] is None
    assert hero["url_full"].endswith("/4320x3240.jpg")
    assert hero["url_thumb"].endswith("/400x300.jpg")


def test_group_artwork_honours_the_language_preference():
    groups = apple.group_artwork(_show(), {"en", None}, "de")
    assert groups["logos"] == [] and groups["squareart"] == []
    assert [b["width"] for b in groups["backgrounds"]] == [3840, 4320]
    assert [p["width"] for p in groups["posters"]] == [1680]
    everything = apple.group_artwork(_show(), None, "de")
    assert everything["logos"][0]["language"] == "de"


def test_group_artwork_lists_an_asset_once_when_two_keys_share_it():
    # Battlefield Earth: Apple's single- and full-colour logos are the same file. Listing it twice
    # gave the gallery duplicate keys, which bled stale logo tiles into every other grid.
    item = _show()
    item["images"]["singleColorContentLogo"] = dict(item["images"]["fullColorContentLogo"])
    groups = apple.group_artwork(item, None, "en")
    assert len(groups["logos"]) == 1
    paths = [e["file_path"] for bucket in groups.values() for e in bucket]
    assert len(paths) == len(set(paths))


def test_group_artwork_drops_foreign_hosts_and_junk_entries():
    item = _show()
    item["images"]["coverArt"]["url"] = "https://evil.example.com/{w}x{h}.{f}"
    item["images"]["previewFrame"] = {"url": TMPL, "width": "wide", "height": 1}
    item["images"]["fullColorContentLogo"] = "not a dict"
    del item["images"]["singleColorContentLogo"]["width"]
    groups = apple.group_artwork(item, None, "en")
    assert groups["squareart"] == [] and groups["logos"] == []
    assert [b["width"] for b in groups["backgrounds"]] == [1920, 4320]
    assert [p["width"] for p in groups["posters"]] == [1680]
    assert apple.group_artwork({}, None, "en") == {"logos": [], "backgrounds": [], "posters": [], "squareart": []}


def test_season_posters_pick_one_seasons_cover_and_heroes():
    seasons = [{"type": "Season", "seasonNumber": 1, "images": {
                    "coverArt": {"url": TMPL, "width": 3000, "height": 3000},
                    "centeredFullScreenBackgroundImage": {"url": HERO, "width": 4320, "height": 3240},
                    "fullColorContentLogo": {"url": TMPL, "width": 4315, "height": 878}}},
               {"type": "Season", "seasonNumber": 2, "images": {}}]
    assert [(p["width"], p["height"]) for p in apple.season_posters(seasons, 1, None, "en")] == [(3000, 3000), (4320, 3240)]
    assert apple.season_posters(seasons, 2, None, "en") == []
    assert apple.season_posters(seasons, 3, None, "en") == []
    # Textless-only keeps the hero and drops the titled cover.
    assert [p["width"] for p in apple.season_posters(seasons, 1, {None}, "en")] == [4320]


@pytest.mark.parametrize("url, ok", [
    ("https://is1-ssl.mzstatic.com/image/thumb/abc/3000x3000.jpg", True),
    ("https://is5-ssl.mzstatic.com/image/thumb/abc/{w}x{h}.{f}", True),
    ("http://is1-ssl.mzstatic.com/image/thumb/abc/3000x3000.jpg", False),
    ("https://mzstatic.com.evil.example/x.jpg", False),
    ("https://evil.example.com/x.jpg", False),
    ("", False),
])
def test_is_apple_image_url(url, ok):
    assert apple.is_apple_image_url(url) is ok


def test_ref_size_reads_the_size_from_our_urls():
    assert apple.ref_size("https://is1-ssl.mzstatic.com/image/thumb/abc/4320x3240.jpg") == (4320, 3240)
    assert apple.ref_size("https://is1-ssl.mzstatic.com/image/thumb/abc/4320x2430sr.jpg") == (4320, 2430)
    assert apple.ref_size("https://is1-ssl.mzstatic.com/image/thumb/abc/{w}x{h}.{f}") is None
    assert apple.ref_size("") is None


@pytest.mark.parametrize("subtype, size, ok", [
    ("background", (3840, 2160), True),
    ("background", (1920, 1080), True),
    ("background", (4320, 3240), False),   # the 4:3 hero
    ("background", (1680, 3636), False),   # the tall phone hero
    ("squareart", (3000, 3000), True),
    ("squareart", (2000, 3000), False),
    ("logo", (4319, 251), True),
    ("logo", (0, 10), False),
])
def test_fits_role(subtype, size, ok):
    assert apple.fits_role(subtype, *size) is ok


def test_role_fit_problem_explains_the_shape():
    hero = "https://is1-ssl.mzstatic.com/image/thumb/abc/4320x3240.jpg"
    assert apple.role_fit_problem("background", hero) == "Apple TV artwork must be 16:9 to be saved as background; this one is 4320×3240."
    assert apple.role_fit_problem("squareart", hero).startswith("Apple TV artwork must be square")
    assert apple.role_fit_problem("logo", hero) is None
    assert apple.role_fit_problem("background", "https://is1-ssl.mzstatic.com/image/thumb/abc/3840x2160.jpg") is None
    assert apple.role_fit_problem("background", "https://is1-ssl.mzstatic.com/nope") == "Unrecognized Apple TV artwork URL."


def test_image_url_fills_the_template():
    assert apple.image_url(HERO, 3840, 2160, "jpg", "sr") == "https://is1-ssl.mzstatic.com/image/thumb/hero/3840x2160sr.jpg"
    assert apple.image_url(HERO, 400, 300, "jpg") == "https://is1-ssl.mzstatic.com/image/thumb/hero/400x300.jpg"


# ---------------------------------------------------------------- transport

class _Resp:
    def __init__(self, status=200, payload=None, bad_json=False):
        self.status_code = status
        self._payload = payload
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("nope")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _canvas(*items):
    return {"data": {"canvas": {"shelves": [{"title": "TV Shows", "items": list(items)},
                                             {"title": "People", "items": [{"type": "Person", "title": "x"}]}]}}}


def test_search_lists_shows_and_movies_and_sends_the_storefront(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        return _Resp(payload=_canvas(_show(), _movie()))

    monkeypatch.setattr(apple.requests, "get", fake_get)
    items = apple.search("Deca Dence", "143441")
    assert [i["type"] for i in items] == ["Show", "Movie"]
    url, params = calls[0]
    assert url.endswith("/search/incremental")
    assert params["sf"] == "143441" and params["q"] == "Deca Dence" and params["utsk"]

    # Cached: the same query in the same store makes no second call; another store does.
    apple.search("Deca Dence", "143441")
    apple.search("Deca Dence", "143444")
    assert len(calls) == 2


def test_search_treats_not_found_as_nothing_and_maps_failures(monkeypatch):
    monkeypatch.setattr(apple.requests, "get", lambda *a, **k: _Resp(status=404))
    assert apple.search("x", "143441") == []

    apple._cache.clear()
    monkeypatch.setattr(apple.requests, "get", lambda *a, **k: _Resp(status=503))
    with pytest.raises(apple.AppleTvError, match="HTTP 503"):
        apple.search("x", "143441")

    monkeypatch.setattr(apple.requests, "get", lambda *a, **k: _Resp(bad_json=True))
    with pytest.raises(apple.AppleTvError, match="unreadable"):
        apple.search("x", "143441")

    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(apple.requests, "get", boom)
    with pytest.raises(apple.AppleTvError, match="Could not reach"):
        apple.search("x", "143441")


def test_fetch_artwork_walks_the_plan_until_a_store_lists_the_title(monkeypatch):
    listings = {"143441": [], "143444": [_show()]}
    monkeypatch.setattr(apple, "search", lambda q, sf: listings.get(sf, []))
    plan = apple.plan_storefronts(["GB"], ["JP"])   # GB, then the US fallback — GB wins first anyway
    found = apple.fetch_artwork(media_type="tv", title="Deca-Dence", year=2020, plan=plan)
    assert found.iso == "GB" and found.storefront == "143444" and found.item["title"] == "DECA-DENCE"

    plan = apple.plan_storefronts([], ["JP"])
    assert apple.fetch_artwork(media_type="tv", title="Deca-Dence", year=2020, plan=plan) is None
    assert apple.fetch_artwork(media_type="collection", title="Alien Collection", year=None, plan=plan) is None


def test_fetch_seasons_reads_the_product_page(monkeypatch):
    seen = {}

    def fake_uts(path, params, *, what):
        seen.update(path=path, params=params)
        return {"data": {"seasons": [{"type": "Season", "seasonNumber": 1}, {"type": "Episode"}, "junk"]}}

    monkeypatch.setattr(apple, "_uts", fake_uts)
    assert apple.fetch_seasons("umc.cmc.1", "143444") == [{"type": "Season", "seasonNumber": 1}]
    assert seen["path"] == "/view/product/umc.cmc.1" and seen["params"] == {"sf": "143444", "locale": "en-US"}


# ---------------------------------------------------------------- language

def test_locales_for_reads_both_preference_forms():
    assert apple.locales_for("en+textless") == ["en"]
    assert apple.locales_for("en,null") == ["en"]
    assert apple.locales_for("de") == ["de"]
    assert apple.locales_for("de,null") == ["de"]
    assert apple.locales_for("all") == [] and apple.locales_for(None) == []
    assert apple.locales_for("null") == ["en"]


def _found(iso="US"):
    return apple.Found(item=_show(), iso=iso, storefront=apple.STOREFRONTS[iso])


def test_artwork_for_english_uses_the_listing_without_a_page_fetch(monkeypatch):
    monkeypatch.setattr(apple, "fetch_product_view", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fetch")))
    groups = apple.artwork_for(_found(), "en+textless", {"en", None})
    assert [l["language"] for l in groups["logos"]] == ["en", "en"]
    assert groups["squareart"][0]["language"] == "en"


def test_artwork_for_asks_the_product_page_in_the_chosen_language(monkeypatch):
    seen = {}
    spanish = "https://is1-ssl.mzstatic.com/image/thumb/es-logo/{w}x{h}.{f}"

    def fake_view(item_id, storefront, locale="en-US"):
        seen.update(item_id=item_id, storefront=storefront, locale=locale)
        return {"content": {"images": {
            "fullColorContentLogo": {"url": spanish, "width": 4000, "height": 900},
            "coverArt": {"url": TMPL, "width": 3000, "height": 3000},   # the store's own, same as English
        }}}

    monkeypatch.setattr(apple, "fetch_product_view", fake_view)
    groups = apple.artwork_for(_found("US"), "es", {"es"})
    assert seen == {"item_id": "umc.cmc.1", "storefront": "143441", "locale": "es-US"}
    assert [(l["language"], l["width"]) for l in groups["logos"]] == [("es", 4000)]
    # Cover art keeps the storefront's language, so a Spanish-only preference drops it in a US store.
    assert groups["squareart"] == [] and groups["backgrounds"] == []


def test_artwork_for_all_adds_the_storefronts_own_language_and_folds_fallbacks(monkeypatch):
    calls = []

    def fake_view(item_id, storefront, locale="en-US"):
        calls.append(locale)
        # Apple hands back the English logo when it has no German one.
        return {"content": {"images": {"fullColorContentLogo": {"url": TMPL, "width": 4315, "height": 878}}}}

    monkeypatch.setattr(apple, "fetch_product_view", fake_view)
    groups = apple.artwork_for(_found("DE"), "all", None)
    assert calls == ["de-DE"]
    assert [l["language"] for l in groups["logos"]] == ["en", "en"]    # the fallback duplicate is folded away
    assert groups["squareart"][0]["language"] == "de"                 # cover art is the store's

    monkeypatch.setattr(apple, "fetch_product_view", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fetch")))
    assert len(apple.artwork_for(_found("US"), "all", None)["logos"]) == 2


def test_storefront_hints_read_origin_and_apple_store_listings(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        if url.endswith("/watch/providers"):
            return _Resp(payload={"results": {
                "US": {"buy": [{"provider_id": 2, "provider_name": "Apple TV Store"}]},
                "GB": {"rent": [{"provider_id": 2, "provider_name": "Apple TV Store"}]},
                "DE": {"flatrate": [{"provider_id": 350, "provider_name": "Apple TV"}]},
                "FR": {"buy": [{"provider_id": 10, "provider_name": "Amazon Video"}]},
                "XX": "junk",
            }})
        return _Resp(payload={"origin_country": ["GB"], "production_countries": [{"iso_3166_1": "US"}]})

    monkeypatch.setattr(apple.requests, "get", fake_get)
    assert apple.storefront_hints(1396, "tv", "key") == (["GB", "US"], ["GB", "US"])


def test_storefront_hints_need_an_id_and_a_key(monkeypatch):
    monkeypatch.setattr(apple.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
    assert apple.storefront_hints(None, "tv", "key") == ([], [])
    assert apple.storefront_hints(1396, "tv", "") == ([], [])
    assert apple.storefront_hints(10, "collection", "key") == ([], [])


def test_storefront_hints_treat_a_tmdb_404_as_no_hints(monkeypatch):
    # P90X2: TheTVDB's remote id is a TMDB *movie*, so /tv/498801 is a 404 — search by title instead.
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _Resp(status=404, payload={"status_code": 34})

    monkeypatch.setattr(apple.requests, "get", fake_get)
    assert apple.storefront_hints(498801, "tv", "key") == ([], [])
    assert len(calls) == 1   # no watch-providers call for a title TMDB doesn't have


def test_storefront_hints_map_tmdb_failures(monkeypatch):
    monkeypatch.setattr(apple.requests, "get", lambda *a, **k: _Resp(status=500))
    with pytest.raises(apple.AppleTvError, match="TMDB"):
        apple.storefront_hints(1396, "tv", "key")
