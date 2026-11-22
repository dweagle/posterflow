import io
import json
import os
import time
from datetime import date, timedelta

import pytest
from PIL import Image

from services.overlays import images, lookups as lookups_mod
from services.overlays.base import ItemContext
from services.overlays.config import normalize_config, overlay_signature
from models.setting import upsert_setting
from services.overlays.lookups import Lookups, arr_tag_choices, read_mdblist, rule_values, server_values
from services.overlays.metadata import MetadataProvider
from services.overlays.registry import get_type
from services.overlays.rules import FIELDS, Unavailable, describe_fields, list_address, matches, needs, normalize_rules, rule_holds
from services.overlays.types import custom as custom_mod
from test_overlays import FakeClient, FakeMetadata, _engine, _has_badge, _item, _sources, _state_items

TODAY = date(2026, 11, 20)


@pytest.fixture(autouse=True)
def _fresh_shared_answers():
    lookups_mod.clear_shared()
    yield
    lookups_mod.clear_shared()


@pytest.fixture
def custom():
    return get_type("custom")


def _ctx(kind="movie", **kw):
    base = dict(media_type=kind, title="Heat", year=1995, tmdb_id=949, tvdb_id=None, imdb_id="tt0113277", season_number=None, rel_path="srv|10",
                owner_id="10", library_key="1")
    return ItemContext(**{**base, **kw})


def _rule(field, op, value):
    return {"field": field, "op": op, "value": value}


def _holds(field, op, value, ctx=None, meta=None):
    return rule_holds(_rule(field, op, value), ctx or _ctx(), meta or {}, TODAY)


# ── the rules ────────────────────────────────────────────────────────────────

def test_rules_normalize_keeps_valid_rules_only():
    rules = normalize_rules([
        {"field": "label", "op": "is", "value": "  Kids  "},
        {"field": "label", "op": "bogus", "value": "x"},           # an unknown comparison: the field's first
        {"field": "nope", "op": "is", "value": "x"},               # an unknown field
        {"field": "genre", "op": "is", "value": "   "},            # nothing to compare with
        {"field": "released", "op": "within", "value": "90"},
        {"field": "released", "op": "within", "value": "soon"},
        {"field": "tmdb_rating", "op": "gte", "value": "7.55"},
        {"field": "year", "op": "gte", "value": "1990.7"},
        {"field": "resolution", "op": "is", "value": "8k"},        # not one of the choices
        {"field": "resolution", "op": "is_not", "value": "4k"},
        {"field": "list", "op": "is", "value": "mdblist.com/lists/someone/top-10/json?limit=5"},
        "junk",
    ])
    assert rules == [
        _rule("label", "is", "Kids"), _rule("label", "is", "x"), _rule("released", "within", 90), _rule("tmdb_rating", "gte", 7.55),
        _rule("year", "gte", 1990), _rule("resolution", "is_not", "4k"), _rule("list", "is", "https://mdblist.com/lists/someone/top-10"),
    ]
    assert normalize_rules(None) == [] and len(normalize_rules([_rule("title", "is", "x")] * 50)) == 20
    assert list_address(" https://www.mdblist.com/lists/a/b/ ") == "https://mdblist.com/lists/a/b"


def test_rules_catalog_is_what_the_page_draws():
    described = {f["key"]: f for f in describe_fields()}
    assert set(described) == set(FIELDS) and all(f["ops"] and f["group"] and f["label"] for f in described.values())
    assert [o["value"] for o in described["released"]["ops"]] == ["within", "older", "before", "after", "regex"] and described["released"]["kind"] == "days"
    assert [o["label"] for o in described["list"]["ops"]] == ["has the item", "does not have it"] and described["list"]["pattern"]
    assert {o["value"] for o in described["resolution"]["options"]} >= {"4k", "1080p"}
    assert needs([_rule("label", "is", "x"), _rule("audio", "is", "dts"), _rule("tmdb_keyword", "is", "x"), _rule("tmdb_genre", "is", "x")]) \
        == ("media", "tmdb", "keywords", "show_keywords", "details")
    assert needs([_rule("title", "is", "x")]) == ()


def test_rules_read_the_item_itself():
    assert _holds("title", "contains", "hea") and not _holds("title", "is", "hea") and _holds("title", "is", "HEAT")
    assert _holds("title", "regex", "^he.t$") and not _holds("title", "regex", "(") and _holds("title", "not_contains", "cold")
    assert _holds("year", "gte", 1995) and _holds("year", "lte", 1995) and not _holds("year", "gte", 1996) and _holds("year", "is_not", 2000)
    assert not _holds("year", "gte", 1900, _ctx(year=None)) and _holds("year", "is_not", 1900, _ctx(year=None))
    rated = _ctx(content_rating="R", ratings={"audience": 8.4})
    assert _holds("content_rating", "is", "r", rated) and _holds("content_rating", "is_not", "PG", rated) and not _holds("content_rating", "is", "R")
    assert _holds("audience_rating", "gte", 8, rated) and not _holds("audience_rating", "lte", 8, rated) and not _holds("critic_rating", "gte", 0, rated)


def test_rules_released_and_added_count_days():
    def on(days_ago, field="released", **kw):
        stamp = (TODAY - timedelta(days=days_ago)).isoformat()
        return _ctx(**{"release_date" if field == "released" else "added_at": stamp + ("T10:00:00" if field == "added" else ""), **kw})

    assert _holds("released", "within", 90, on(30)) and _holds("released", "within", 90, on(90)) and not _holds("released", "within", 90, on(91))
    assert not _holds("released", "within", 90, on(-5))  # not out yet
    assert _holds("released", "older", 90, on(91)) and not _holds("released", "older", 90, on(30))
    assert _holds("added", "within", 7, on(3, "added")) and not _holds("added", "within", 7, _ctx())  # no date: no match


def test_rules_edition_and_file_name():
    cut = _ctx(edition="Director's Cut", paths=["/movies/Heat (1995)/Heat (1995) {edition-Extended-Cut} [Remux-2160p].mkv"])
    assert _holds("edition", "contains", "director", cut) and _holds("edition", "is", "extended cut", cut) and not _holds("edition", "contains", "imax", cut)
    assert not _holds("edition", "contains", "director", _ctx("show", edition="Director's Cut"))  # movies only
    assert _holds("filename", "contains", "remux", cut) and _holds("filename", "regex", r"\[Remux-\d+p\]", cut) and _holds("filename", "not_contains", "web", cut)
    episodes = [{"path": f"/tv/Show/S01E0{n} [WEBDL-1080p].mkv"} for n in (1, 2, 3)] + [{"path": "/tv/Show/S01E04 [Bluray-1080p].mkv"}, {"path": "/tv/Show/S01E05 [Bluray-1080p].mkv"}]
    show = _ctx("show", media=episodes)
    assert _holds("filename", "contains", "webdl", show) and not _holds("filename", "contains", "bluray", show)  # 3 of 5, 2 of 5
    assert _holds("filename", "contains", "bluray", _ctx(media=episodes))  # a movie: any file


def test_rules_video_and_audio():
    files = [{"resolution": "4k", "hdr": True, "dovi": True, "path": "/m/Film [Remux-2160p DV HDR10 TrueHD Atmos 7.1].mkv", "audio_titles": ["English (TRUEHD 7.1)"]}]
    film = _ctx(media=files)
    assert _holds("resolution", "is", "4k", film) and _holds("resolution", "is_not", "1080p", film)
    assert _holds("hdr", "is", "any", film) and _holds("hdr", "is", "dv", film) and not _holds("hdr", "is", "hlg", film)
    assert _holds("audio", "is", "truehd_atmos", film) and not _holds("audio", "is", "dts", film)
    assert _holds("versions", "lte", 1, film) and _holds("versions", "gte", 2, _ctx(media=files * 2)) and not _holds("versions", "gte", 1, _ctx("show", media=files))
    plain = _ctx(media=[{"resolution": "1080", "hdr": False, "dovi": False, "path": "/m/Film [Bluray-1080p].mkv", "audio_titles": []}])
    assert _holds("hdr", "is_not", "any", plain) and not _holds("resolution", "is", "4k", _ctx())  # nothing known: no match


def test_rules_tmdb_facts():
    meta = {"tmdb": {"genres": ["Animation", "Family"], "keywords": ["based on novel or book"], "original_language": "ja", "status": "Released",
                     "vote_average": 7.9, "vote_count": 1200}}
    assert _holds("tmdb_genre", "is", "animation", meta=meta) and _holds("tmdb_genre", "is_not", "Horror", meta=meta)
    assert _holds("tmdb_keyword", "is", "Based on Novel or Book", meta=meta) and _holds("original_language", "is", "JA", meta=meta)
    assert _holds("tmdb_status", "is", "Released", meta=meta) and _holds("tmdb_rating", "gte", 7.5, meta=meta) and not _holds("tmdb_rating", "lte", 7.5, meta=meta)
    assert not _holds("tmdb_genre", "is", "animation") and not _holds("tmdb_rating", "gte", 0, meta={"tmdb": {"vote_average": 0, "vote_count": 0}})


def test_a_rule_may_hold_several_values_and_any_one_will_do():
    rule = lambda field, op, *values: {"field": field, "op": op, "value": values[0], "also": [{"value": v} for v in values[1:]]}  # noqa: E731
    assert rule_holds(rule("title", "is", "Speed", "heat", "Jaws"), _ctx(), {}) and not rule_holds(rule("title", "is", "Speed", "Jaws"), _ctx(), {})
    assert rule_holds(rule("title", "contains", "zz", "ea"), _ctx(), {})
    # A negative comparison holds when none of them fits.
    assert not rule_holds(rule("title", "is_not", "Speed", "Heat"), _ctx(), {}) and rule_holds(rule("title", "is_not", "Speed", "Jaws"), _ctx(), {})
    assert rule_holds(rule("title", "not_contains", "zz", "yy"), _ctx(), {}) and not rule_holds(rule("title", "not_contains", "zz", "ea"), _ctx(), {})
    uhd = _ctx(media=[{"resolution": "4k", "hdr": False, "dovi": False, "path": "/m/Film [Remux-2160p].mkv", "audio_titles": []}])
    assert rule_holds(rule("resolution", "is", "1080p", "4k"), uhd, {}) and not rule_holds(rule("resolution", "is", "1080p", "720p"), uhd, {})
    meta = {"tmdb": {"genres": ["Animation", "Family"]}}
    assert rule_holds(rule("tmdb_genre", "is", "Horror", "family"), _ctx(), meta) and rule_holds(rule("tmdb_genre", "is_not", "Horror", "Western"), _ctx(), meta)
    # Each value may name its own instance or server.
    client = LookupClient()
    client.carriers[("genre", "action")] = ["10"]
    look = Lookups(None)
    on_jelly = _ctx(rel_path="Jelly|10", client=client, lookups=look)
    genres = {"field": "genre", "op": "is", "value": "Action", "instance": "Plex", "also": [{"value": "Comedy", "instance": "Plex"}, {"value": "Action", "instance": "Jelly"}]}
    assert rule_holds(genres, on_jelly, {}) and not rule_holds({**genres, "also": genres["also"][:1]}, on_jelly, {})
    # A source that does not answer only matters when no other value settles it.
    lists = Lookups(None, list_reader=lambda url: (_ for _ in ()).throw(ConnectionError("down")) if url.endswith("/down") else {"movie:tmdb:949"})
    listed = _ctx(lookups=lists)
    both = {"field": "list", "op": "is", "value": "https://mdblist.com/lists/a/down", "also": [{"value": "https://mdblist.com/lists/a/top"}]}
    assert rule_holds(both, listed, {})  # the second list has it: the first one's silence does not matter
    with pytest.raises(Unavailable):
        rule_holds(both, _ctx(tmdb_id=1, imdb_id=None, lookups=lists), {})  # not on the one that answers, and the other cannot say


def test_rules_normalize_several_values():
    rules = normalize_rules([
        {"field": "genre", "op": "is", "value": " Action ", "instance": "Plex", "also": [
            {"value": "Comedy", "instance": "Plex"}, {"value": "action", "instance": "Plex"}, {"value": "Action", "instance": "Jelly"}, {"value": "  "}, "junk", {"value": "Drama"}]},
        {"field": "title", "op": "is", "value": "", "also": [{"value": "Heat", "instance": "Plex"}, {"value": "Jaws"}]},  # the first one is empty: the next leads
        {"field": "resolution", "op": "is", "value": "4k", "also": [{"value": "8k"}, {"value": "1080p"}]},
        {"field": "year", "op": "gte", "value": 1990, "also": [{"value": 2000}]},  # a number is one number
        {"field": "list", "op": "is", "value": "mdblist.com/lists/a/one/json", "also": [{"value": "https://mdblist.com/lists/a/two/"}]},
        {"field": "label", "op": "is", "value": "", "also": [{"value": ""}]},
        {"field": "title", "op": "is", "value": "x", "also": [{"value": f"t{n}"} for n in range(40)]},
    ])
    assert rules[0] == {"field": "genre", "op": "is", "value": "Action", "instance": "Plex", "also": [
        {"value": "Comedy", "instance": "Plex"}, {"value": "Action", "instance": "Jelly"}, {"value": "Drama"}]}  # the same value of the same server once
    assert rules[1] == {"field": "title", "op": "is", "value": "Heat", "also": [{"value": "Jaws"}]}  # a title names no server
    assert rules[2] == {"field": "resolution", "op": "is", "value": "4k", "also": [{"value": "1080p"}]}
    assert rules[3] == _rule("year", "gte", 1990)
    assert rules[4] == {"field": "list", "op": "is", "value": "https://mdblist.com/lists/a/one", "also": [{"value": "https://mdblist.com/lists/a/two"}]}
    assert len(rules) == 6 and len(rules[5]["also"]) == 19  # twenty values at most
    multi = {f["key"]: f["multi"] for f in describe_fields()}
    assert multi["genre"] and multi["title"] and multi["resolution"] and multi["list"] and not multi["year"] and not multi["released"] and not multi["tmdb_rating"]


def _file(**kw):
    return {"resolution": "1080", "hdr": False, "dovi": False, "path": "/m/Film.mkv", "audio_titles": [], "video_codec": "h264", "channels": 6.0, "aspect": 2.35,
            "minutes": 107.7, "bitrate": 12.7, "width": 1920.0, "height": 816.0, "audio_languages": ["en"], "subtitle_languages": ["en"], **kw}


def test_rules_more_comparisons():
    assert _holds("title", "begins", "he") and not _holds("title", "begins", "at") and _holds("title", "ends", "AT") and not _holds("title", "ends", "he")
    assert _holds("year", "gt", 1994) and not _holds("year", "gt", 1995) and _holds("year", "lt", 1996) and not _holds("year", "lt", 1995)
    assert _holds("year", "is", 1995) and _holds("year", "is_not", 1996) and not _holds("year", "gt", 1900, _ctx(year=None))
    path = _ctx(media=[_file(path="/movies/Heat (1995)/Heat (1995) [Remux-1080p].mkv")])
    assert _holds("filename", "begins", "/movies/heat", path) and _holds("filename", "ends", "1080p].mkv", path) and not _holds("filename", "ends", ".mp4", path)
    described = {f["key"]: f for f in describe_fields()}
    assert [o["label"] for o in described["title"]["ops"]] == ["contains", "does not contain", "is", "is not", "begins with", "ends with", "matches (regex)"]
    assert [o["label"] for o in described["tmdb_rating"]["ops"]] == ["is at least", "is at most", "is more than", "is less than", "is", "is not"]


def test_rules_numbers_are_equal_to_the_decimals_the_field_keeps():
    rated = _ctx(ratings={"audience": 8.4})
    assert _holds("audience_rating", "is", 8.4, rated) and not _holds("audience_rating", "is", 8.5, rated) and _holds("audience_rating", "is_not", 8.5, rated)
    scope = _ctx(media=[_file(aspect=2.3881, minutes=127.4, width=1920.0, channels=6.0)])
    assert _holds("aspect_ratio", "is", 2.39, scope) and not _holds("aspect_ratio", "is", 2.35, scope) and _holds("aspect_ratio", "is_not", 1.78, scope)
    assert _holds("runtime", "is", 127, scope) and not _holds("runtime", "is", 128, scope) and _holds("video_width", "is", 1920, scope) and _holds("audio_channels", "is", 6, scope)
    assert _holds("tmdb_votes", "is", 10, meta={"tmdb": {"vote_count": 10}}) and _holds("tmdb_rating", "is", 7.6, meta={"tmdb": {"vote_count": 10, "vote_average": 7.649}})
    assert not _holds("audience_rating", "is", 0)  # a number that is not known is equal to nothing


def test_rules_has_an_edition():
    assert _holds("has_edition", "is", "no") and not _holds("has_edition", "is", "yes")
    cut = _ctx(edition="Director's Cut")
    assert _holds("has_edition", "is", "yes", cut) and not _holds("has_edition", "is", "no", cut)
    assert _holds("has_edition", "is", "yes", _ctx(paths=["/movies/Heat (1995)/Heat (1995) {edition-Extended}.mkv"]))  # named in the file only
    assert _holds("has_edition", "is", "no", _ctx("show", edition="Director's Cut"))  # movies only
    assert normalize_rules([_rule("has_edition", "is", "maybe"), _rule("has_edition", "is_not", "yes")]) == [_rule("has_edition", "is", "yes")]


def test_rules_names_are_compared_whole_or_in_part():
    meta = {"tmdb": {"genres": ["Science Fiction", "Action"], "keywords": ["based on novel or book"], "companies": ["Pixar Animation Studios"],
                     "networks": ["HBO Max"], "collection": "Toy Story Collection"}}
    assert _holds("tmdb_genre", "contains", "fiction", meta=meta) and not _holds("tmdb_genre", "contains", "horror", meta=meta) and _holds("tmdb_genre", "not_contains", "horror", meta=meta)
    assert _holds("tmdb_genre", "begins", "sci", meta=meta) and _holds("tmdb_genre", "ends", "TION", meta=meta) and not _holds("tmdb_genre", "begins", "fiction", meta=meta)
    assert _holds("tmdb_genre", "regex", "^(action|war)$", meta=meta) and not _holds("tmdb_genre", "regex", "^(war|crime)$", meta=meta) and not _holds("tmdb_genre", "is", "science", meta=meta)
    assert _holds("tmdb_keyword", "regex", "novel|comic", meta=meta) and _holds("tmdb_company", "begins", "pixar", meta=meta) and _holds("tmdb_network", "contains", "hbo", meta=meta)
    assert _holds("tmdb_collection", "ends", "collection", meta=meta) and not _holds("tmdb_collection", "is", "Toy Story", meta=meta)
    rated = _ctx(content_rating="TV-MA")
    assert _holds("content_rating", "begins", "tv-", rated) and _holds("content_rating", "regex", "^(R|TV-MA)$", rated) and not _holds("content_rating", "contains", "pg", rated)
    described = {f["key"]: f for f in describe_fields()}
    for key in ("label", "genre", "collection", "studio", "content_rating", "radarr_tag", "sonarr_tag", "tmdb_genre", "tmdb_keyword", "tmdb_network", "tmdb_company", "tmdb_collection"):
        assert [o["value"] for o in described[key]["ops"]] == ["is", "is_not", "contains", "not_contains", "begins", "ends", "regex"], key
    for key in ("audio_language", "subtitle_language", "original_language", "country", "list"):
        assert [o["value"] for o in described[key]["ops"]] == ["is", "is_not"], key  # codes and addresses: the whole value only
    # Several values, as before: any one that fits will do, and for a negative none may.
    both = {"field": "tmdb_genre", "op": "contains", "value": "horror", "also": [{"value": "fict"}]}
    assert rule_holds(both, _ctx(), meta, TODAY) and not rule_holds({**both, "op": "not_contains"}, _ctx(), meta, TODAY)


def test_rules_dates_by_days_or_by_a_date():
    released = _ctx(release_date="2026-06-15", added_at="2026-11-01T10:00:00")
    assert _holds("released", "before", "2026-07-01", released) and not _holds("released", "before", "2026-06-15", released)
    assert _holds("released", "after", "2026-01-01", released) and not _holds("released", "after", "2026-06-15", released)
    assert _holds("added", "after", "2026-10-31", released) and not _holds("released", "after", "2026-01-01", _ctx())  # no date: no match
    # The value is a date for before and after, a number of days for the other two.
    assert normalize_rules([_rule("released", "before", "2026-07-01T00:00"), _rule("released", "after", "soon"), _rule("released", "within", "2026-07-01"),
                            _rule("added", "older", "30")]) == [_rule("released", "before", "2026-07-01"), _rule("added", "older", 30)]
    # A show's latest episode, from TMDB.
    show = _ctx("show")
    meta = {"tmdb": {"last_air_date": "2026-01-01", "last_episode_to_air": {"air_date": (TODAY - timedelta(days=10)).isoformat()}}}
    assert _holds("last_aired", "within", 30, show, meta) and not _holds("last_aired", "within", 5, show, meta) and _holds("last_aired", "after", "2026-11-01", show, meta)
    assert _holds("last_aired", "before", "2026-06-01", show, {"tmdb": {"last_air_date": "2026-01-01"}}) and not _holds("last_aired", "within", 9999, _ctx(), meta)  # shows only
    assert needs([_rule("last_aired", "within", 30)]) == ("tmdb",)


def test_a_broken_pattern_matches_nothing_and_is_said_once_a_run(monkeypatch):
    from services.overlays import rules as rules_mod

    warnings = []
    monkeypatch.setattr(rules_mod, "log_warning", lambda tag, message: warnings.append(message))
    rules_mod.forget_bad_patterns()
    christmas = _ctx(release_date="2024-12-25")
    for _ in range(3):
        assert not _holds("title", "regex", "he(at") and not _holds("released", "regex", "12/(25", christmas) and _holds("title", "regex", "he.t")
    assert len(warnings) == 2 and warnings[0].startswith("Custom overlays: the pattern 'he(at' is not a valid regex (") and warnings[0].endswith("Conditions that use it match nothing.")
    Lookups(None).begin_run()  # the next run says it again
    assert not _holds("title", "regex", "he(at") and len(warnings) == 3


def test_rules_dates_by_a_pattern():
    christmas = _ctx(release_date="2024-12-25", added_at="2026-01-05T10:00:00")
    # The date is read as month/day/year, from its start.
    assert _holds("released", "regex", "12/25", christmas) and _holds("released", "regex", "12/", christmas) and _holds("released", "regex", r"\d\d/\d\d/2024$", christmas)
    assert not _holds("released", "regex", "25", christmas) and not _holds("released", "regex", "2024", christmas) and _holds("added", "regex", "01/05/2026", christmas)
    assert not _holds("released", "regex", "(", christmas) and not _holds("released", "regex", "12/", _ctx())  # a broken pattern, no date
    # The pattern is kept as text; it is no number of days.
    assert normalize_rules([_rule("released", "regex", " ^12/ "), _rule("released", "regex", ""), _rule("released", "within", "^12/")]) == [_rule("released", "regex", "^12/")]
    show = _ctx("show")
    assert _holds("last_aired", "regex", "^06/", show, {"tmdb": {"last_air_date": "2026-06-01"}})


def test_rules_file_details_and_languages():
    film = _ctx(media=[_file(), _file(video_codec="hevc", channels=8.0, bitrate=40.0, audio_languages=["en", "ja"], subtitle_languages=["en", "de", "fr"], width=3840.0, height=1600.0)])
    assert _holds("video_codec", "is", "h264", film) and _holds("video_codec", "is_not", "hevc", film)  # a movie's first file
    assert _holds("audio_channels", "gte", 8, film) and not _holds("audio_channels", "gt", 8, film)  # its best file
    assert _holds("aspect_ratio", "gte", 2.2, film) and _holds("aspect_ratio", "lt", 2.4, film)
    assert _holds("runtime", "gt", 100, film) and _holds("runtime", "lte", 108, film) and _holds("bitrate", "gte", 40, film)
    assert _holds("video_width", "gte", 3840, film) and _holds("video_height", "lt", 2160, film)
    assert _holds("audio_language", "is", "JA", film) and _holds("audio_language", "is_not", "de", film) and _holds("subtitle_language", "is", "fr", film)
    assert _holds("audio_language_count", "gte", 2, film) and _holds("audio_language_count", "is", 2, film) and _holds("subtitle_language_count", "gt", 2, film)
    # A show: what most of its episodes have, and its middle episode's numbers.
    episodes = [_file(audio_languages=["en", "ja"], minutes=24.0, channels=2.0), _file(audio_languages=["en", "ja"], minutes=25.0, channels=2.0),
                _file(audio_languages=["en"], minutes=48.0, channels=6.0, video_codec="hevc")]
    show = _ctx("show", media=episodes)
    assert _holds("audio_language", "is", "ja", show) and _holds("audio_language_count", "is", 2, show)  # two of three
    assert not _holds("audio_language", "is", "ja", _ctx("show", media=[*episodes, _file(), _file()]))  # two of five
    assert _holds("runtime", "lte", 25, show) and _holds("audio_channels", "lt", 6, show) and _holds("video_codec", "is", "h264", show)
    # What the server did not say fits nothing.
    bare = _ctx(media=[{"resolution": "sd", "path": "/m/Old.avi"}])
    assert not _holds("audio_channels", "gte", 0, bare) and not _holds("runtime", "lt", 999, bare) and not _holds("video_codec", "is", "h264", bare)
    assert _holds("audio_language_count", "is", 0, bare) and not _holds("audio_language_count", "is", 0, _ctx())  # no file details asked for at all
    assert needs([_rule("audio_language", "is", "ja"), _rule("runtime", "gte", 90)]) == ("media",)


def test_rules_more_tmdb_facts():
    meta = {"tmdb": {"genres": [], "companies": ["Pixar", "Walt Disney Pictures"], "networks": ["HBO"], "type": "Miniseries", "collection": "Toy Story Collection",
                     "countries": ["JP", "US"], "vote_average": 8.0, "vote_count": 1500}}
    assert _holds("tmdb_company", "is", "pixar", meta=meta) and _holds("tmdb_company", "is_not", "A24", meta=meta) and _holds("tmdb_network", "is", "hbo", meta=meta)
    assert _holds("tmdb_type", "is", "Miniseries", meta=meta) and _holds("tmdb_collection", "is", "toy story collection", meta=meta)
    assert _holds("country", "is", "jp", meta=meta) and not _holds("country", "is", "FR", meta=meta)
    assert _holds("tmdb_votes", "gte", 1000, meta=meta) and not _holds("tmdb_votes", "gt", 1500, meta=meta) and not _holds("tmdb_votes", "gte", 0)
    assert needs([_rule("tmdb_company", "is", "Pixar"), _rule("tmdb_votes", "gte", 5)]) == ("tmdb", "details")
    assert {"Scripted", "Documentary"} <= {o["value"] for o in next(f for f in describe_fields() if f["key"] == "tmdb_type")["options"]}


class _Charts:
    """TMDB as the lookups read it: charts of 20 a page, a list, with every request noted."""

    def __init__(self):
        self.asked = []

    def __call__(self, path, params=None):
        page = int((params or {}).get("page") or 1)
        self.asked.append((path, page) if not (params or {}).get("region") else (path, page, params["region"]))
        if path.startswith("/list/"):
            rows = [{"id": 949, "media_type": "movie"}, {"id": 1399, "media_type": "tv"}] if page == 1 else [{"id": 7, "media_type": "movie"}]
            return {"items": rows, "total_pages": 2}
        first = (page - 1) * 20 + 1
        return {"results": [{"id": 1000 + n} for n in range(first, first + 20)], "total_pages": 50}


def test_lookups_tmdb_charts_by_rank():
    tmdb = _Charts()
    look = Lookups(None, tmdb_reader=tmdb)
    third, far, nowhere = (_ctx(tmdb_id=n, lookups=look) for n in (1003, 1150, 5))
    assert look.chart_rank(third, "tmdb_popular", 20) == 3 and look.chart_rank(nowhere, "tmdb_popular", 20) is None
    assert tmdb.asked == [("/movie/popular", n) for n in range(1, 6)]  # the top hundred, read once
    assert _holds("tmdb_popular", "lte", 10, third) and not _holds("tmdb_popular", "lte", 2, third) and _holds("tmdb_popular", "gt", 2, third)
    assert not _holds("tmdb_popular", "lte", 100, far) and _holds("tmdb_popular", "gt", 100, far) and _holds("tmdb_popular", "gt", 50, nowhere)
    assert len(tmdb.asked) == 5
    assert _holds("tmdb_popular", "lte", 200, far) and len(tmdb.asked) == 30  # deeper than a hundred: the top five hundred, once
    assert _holds("tmdb_popular", "lte", 9999, far) and len(tmdb.asked) == 30
    # A show's chart is its own, and each chart has its place at TMDB.
    show = _ctx("show", tmdb_id=1003, lookups=look)
    assert look.chart_rank(show, "tmdb_now_playing", 20) == 3 and tmdb.asked[-1][0] == "/tv/on_the_air"
    assert look.chart_rank(third, "tmdb_trending_week", 20) == 3 and tmdb.asked[-1][0] == "/trending/movie/week"
    assert look.chart_rank(_ctx(tmdb_id=None, lookups=look), "tmdb_popular", 20) is None  # nothing to look up by
    assert [o["label"] for o in next(f for f in describe_fields() if f["key"] == "tmdb_popular")["ops"]] == ["is in the top", "is not in the top"]


def test_a_chart_may_name_its_region_for_movies():
    tmdb = _Charts()
    look = Lookups(None, tmdb_reader=tmdb)
    movie, show = _ctx(tmdb_id=1003, lookups=look), _ctx("show", tmdb_id=1003, lookups=look)
    playing = {"field": "tmdb_now_playing", "op": "lte", "value": 20}
    assert rule_holds({**playing, "region": "GB"}, movie, {}) and tmdb.asked[0] == ("/movie/now_playing", 1, "GB")  # the country's chart
    assert rule_holds(playing, movie, {}) and tmdb.asked[5] == ("/movie/now_playing", 1)  # TMDB's own, read apart from it
    rule_holds({**playing, "region": "GB"}, movie, {})
    assert len(tmdb.asked) == 10  # each kept by itself
    assert rule_holds({**playing, "region": "GB"}, show, {}) and tmdb.asked[10] == ("/tv/on_the_air", 1)  # shows have one chart for all
    # Popular and top rated are the same everywhere, and trending has no regions: a region there is not sent.
    assert rule_holds({"field": "tmdb_popular", "op": "lte", "value": 20, "region": "GB"}, movie, {}) and tmdb.asked[-1] == ("/movie/popular", 5)
    assert rule_holds({"field": "tmdb_trending_week", "op": "lte", "value": 20, "region": "GB"}, movie, {}) and tmdb.asked[-1] == ("/trending/movie/week", 5)
    # A region is kept where a chart has them, as two capital letters.
    assert normalize_rules([{**playing, "region": " gb "}, {**playing, "region": "Great Britain"}, {**playing, "region": ""},
                            {"field": "tmdb_popular", "op": "lte", "value": 20, "region": "GB"}, {"field": "year", "op": "gte", "value": 2000, "region": "GB"}]) == [
        {**playing, "region": "GB"}, playing, playing, {"field": "tmdb_popular", "op": "lte", "value": 20}, _rule("year", "gte", 2000)]
    assert {f["key"] for f in describe_fields() if f["region"]} == {"tmdb_now_playing", "tmdb_upcoming"}


def test_lookups_tmdb_lists_and_lists_of_ids(tmp_path, monkeypatch):
    tmdb = _Charts()
    look = Lookups(None, tmdb_reader=tmdb)
    url = "https://www.themoviedb.org/list/8504994"
    assert look.in_tmdb_list(_ctx(), url) and look.in_tmdb_list(_ctx("show", tmdb_id=1399, imdb_id=None), url) and look.in_tmdb_list(_ctx(tmdb_id=7, imdb_id=None), url)
    assert not look.in_tmdb_list(_ctx("show", tmdb_id=949, imdb_id=None), url)  # the list's movie 949 is not the show 949
    assert tmdb.asked == [("/list/8504994", 1), ("/list/8504994", 2)]
    with pytest.raises(Unavailable):
        look.in_tmdb_list(_ctx(), "https://trakt.tv/users/x/lists/y")
    assert normalize_rules([{**_rule("tmdb_list", "is", " 8504994 "), "also": [{"value": "https://www.themoviedb.org/list/8504994-my-list?x=1"}, {"value": "77"}]}]) == [
        {**_rule("tmdb_list", "is", url), "also": [{"value": "https://www.themoviedb.org/list/77"}]}]  # the number alone will do, and the same list twice is once

    # A list of ids: a file under config/overlays/lists, or a web address.
    from services.overlays.lookups import parse_id_list
    assert parse_id_list("tt0113277\n# a note\ntmdb:949, movie:tmdb:5\nshow:tmdb:1399 tv/tmdb/8\ntvdb:121361\nTT0000001x\n12345") == {
        "imdb:tt0113277", "movie:tmdb:949", "show:tmdb:949", "movie:tmdb:5", "show:tmdb:1399", "show:tmdb:8", "show:tvdb:121361"}
    assert parse_id_list('[{"id": 949, "imdb_id": "tt0113277", "mediatype": "movie"}]') == {"movie:tmdb:949", "imdb:tt0113277"}  # MDBList's own form
    # From a web address the list is read as it comes, and given up on at 5 MB.
    class Stream:
        encoding = None

        def __init__(self, chunks):
            self.chunks, self.read = chunks, 0

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=None):
            for chunk in self.chunks:
                self.read += 1
                yield chunk

    small = Stream([b"tt0113277\n", b"tmdb:949\n"])
    monkeypatch.setattr(lookups_mod.requests, "get", lambda url, **kw: small if kw.get("stream") else None)
    assert lookups_mod.read_id_list("https://example.com/ids.txt") == {"imdb:tt0113277", "movie:tmdb:949", "show:tmdb:949"}
    huge = Stream([b"x" * (1024 * 1024)] * 50)
    monkeypatch.setattr(lookups_mod.requests, "get", lambda url, **kw: huge)
    with pytest.raises(ValueError, match="larger than 5 MB"):
        lookups_mod.read_id_list("https://example.com/huge.txt")
    assert huge.read == 6  # stopped at the limit: the rest was never read
    monkeypatch.setattr(images, "config_overlays_dir", lambda: tmp_path / "overlays")
    (tmp_path / "overlays" / "lists").mkdir(parents=True)
    (tmp_path / "overlays" / "lists" / "mine.txt").write_text("tt0113277\nshow:tmdb:1399\n")
    (tmp_path / "secret.txt").write_text("tt0113277")
    files = Lookups(None)
    assert files.in_id_list(_ctx(), "mine.txt") and files.in_id_list(_ctx("show", tmdb_id=1399, imdb_id=None), "mine.txt") and not files.in_id_list(_ctx(tmdb_id=1, imdb_id=None), "mine.txt")
    for outside in ("../../secret.txt", "nope.txt"):  # only files in that folder, and only ones that are there
        with pytest.raises(Unavailable):
            files.in_id_list(_ctx(), outside)
    fetched = []
    web = Lookups(None, ids_reader=lambda source: fetched.append(source) or {"imdb:tt0113277"})
    assert web.in_id_list(_ctx(), "https://example.com/ids.txt") and web.in_id_list(_ctx(), "https://example.com/ids.txt") and fetched == ["https://example.com/ids.txt"]
    assert _holds("id_list", "is", "mine.txt", _ctx(lookups=files)) and _holds("tmdb_list", "is_not", url, _ctx(tmdb_id=1, imdb_id=None, lookups=look))


def test_lookups_in_a_collection():
    class Collected(LookupClient):
        def __init__(self, ids):
            super().__init__()
            self.ids = ids

        def collected_item_ids(self, library_key):
            self.asked.append(("collected", str(library_key)))
            if self.ids is None:
                raise ConnectionError("no route")
            return set(self.ids)

    client = Collected({"10"})
    look = Lookups(None)
    inside, outside = (_ctx(owner_id=i, rel_path=f"srv|{i}", client=client, lookups=look) for i in ("10", "11"))
    assert _holds("collected", "is", "yes", inside) and not _holds("collected", "is", "no", inside)
    assert _holds("collected", "is", "no", outside) and not _holds("collected", "is", "yes", outside)
    assert client.asked == [("collected", "1")]  # one answer for the whole library
    down = Lookups(None)
    with pytest.raises(Unavailable):
        down.collected(_ctx(client=Collected(None)))
    with pytest.raises(Unavailable):
        Lookups(None).collected(_ctx(client=FakeClient()))  # a server that cannot say


def test_rules_all_any_and_none():
    rules = [_rule("title", "is", "heat"), _rule("year", "gte", 2000)]
    assert matches([], "all", _ctx(), {}) is True  # no condition: every item
    assert matches(rules, "all", _ctx(), {}) is False and matches(rules, "any", _ctx(), {}) is True
    assert matches(rules, "all", _ctx(year=2001), {}) is True
    with pytest.raises(Unavailable):
        matches([_rule("label", "is", "x")], "all", _ctx(), {})  # nothing to ask the server with
    assert matches([_rule("year", "gte", 2000), _rule("label", "is", "x")], "all", _ctx(), {}) is False  # settled before the server is asked


# ── what the rules ask outside the item ──────────────────────────────────────

class LookupClient(FakeClient):
    """A server that answers the field searches: {(field, value lower): [item ids]}."""

    def __init__(self):
        super().__init__()
        self.carriers = {}
        self.collections = {}  # title → member item ids
        self.names = {}  # field → the names the library has
        self.asked = []
        self.down = False
        self.silent = False  # like Jellyfin's client: a request given up on comes back empty, and is counted
        self.failed_requests = 0

    def _gone(self):
        if self.down:
            raise ConnectionError("no route")
        if self.silent:
            self.failed_requests += 1
        return self.silent

    def field_values(self, field, library_key):
        self.asked.append(("names", field, str(library_key)))
        return [] if self._gone() else list(self.names.get(field, []))

    def find_by_field(self, field, value, library_key, include_collections=True):
        self.asked.append((field, value, str(library_key)))
        return [] if self._gone() else [_item(i, "movie", f"t{i}", 2000) for i in self.carriers.get((field, value.lower()), [])]

    def get_collections(self, library_key=None):
        self.asked.append(("collections", "", str(library_key)))
        return [] if self._gone() else [_item(f"c{n}", "collection", title, None) for n, title in enumerate(self.collections)]

    def get_collection_items(self, collection):
        return [_item(i, "movie", f"t{i}", 2000) for i in self.collections[collection.title]]


def test_lookups_ask_the_server_once_per_label_and_library():
    client = LookupClient()
    client.carriers[("label", "kids")] = ["10", "11"]
    client.collections["Pixar"] = ["11"]
    look = Lookups(None)
    ten, eleven, twelve = (_ctx(owner_id=i, rel_path=f"srv|{i}", client=client, lookups=look) for i in ("10", "11", "12"))
    assert look.on_server(ten, "label", "Kids") and look.on_server(eleven, "label", "kids") and not look.on_server(twelve, "label", "KIDS")
    assert client.asked == [("label", "Kids", "1")]
    assert look.on_server(_ctx(owner_id="10", library_key="9", client=client), "label", "Kids")  # another library: asked again
    assert not look.on_server(ten, "genre", "Horror")  # nobody has it
    assert look.on_server(eleven, "collection", "pixar") and not look.on_server(ten, "collection", "pixar")
    season = _ctx("show", season_number=2, owner_id="11", rel_path="srv|99", client=client)
    assert look.on_server(season, "label", "kids")  # a season is asked about through its show


def test_lookups_leave_items_alone_when_the_server_does_not_answer():
    client = LookupClient()
    client.down = True
    look = Lookups(None)
    for _ in range(3):
        with pytest.raises(Unavailable):
            look.on_server(_ctx(client=client), "label", "kids")
    assert len(client.asked) == 1  # asked once, not per item
    with pytest.raises(Unavailable):
        look.on_server(_ctx(client=None), "genre", "x")


def test_lookups_a_name_in_part_asks_for_the_names_that_fit():
    client = LookupClient()
    client.names["label"] = ["Kids", "Kids 4K", "kids", "Pixar"]
    client.carriers.update({("label", "kids"): ["10"], ("label", "kids 4k"): ["11"], ("label", "pixar"): ["12"]})
    client.collections.update({"Pixar Shorts": ["11"], "Toy Story": ["12"], "Empty": []})
    look = Lookups(None)
    ten, eleven, twelve = (_ctx(owner_id=i, rel_path=f"srv|{i}", client=client, lookups=look) for i in ("10", "11", "12"))
    assert _holds("label", "contains", "kid", ten) and _holds("label", "contains", "KID", eleven) and not _holds("label", "contains", "kid", twelve)
    # The names are listed once; only the ones that fit are asked for, each once.
    assert client.asked == [("names", "label", "1"), ("label", "Kids", "1"), ("label", "Kids 4K", "1")]
    assert _holds("label", "begins", "pix", twelve) and _holds("label", "ends", "4k", eleven) and not _holds("label", "ends", "4k", ten)
    assert _holds("label", "regex", "^(pixar|kids)$", ten) and not _holds("label", "regex", "^kids$", eleven) and _holds("label", "not_contains", "kid", twelve)
    assert not _holds("label", "not_contains", "kid", ten) and not _holds("label", "contains", "nothing like it", ten)
    assert [a for a in client.asked if a[0] == "names"] == [("names", "label", "1")]
    # Collections: by their titles, listed once for the library.
    assert _holds("collection", "contains", "pixar", eleven) and not _holds("collection", "contains", "pixar", twelve) and _holds("collection", "regex", "^toy", twelve)
    assert _holds("collection", "is", "toy story", twelve) and len([a for a in client.asked if a[0] == "collections"]) == 1
    # Another library has names of its own.
    assert _holds("label", "contains", "kid", _ctx(owner_id="10", library_key="9", client=client, lookups=look))
    assert [a for a in client.asked if a[0] == "names"] == [("names", "label", "1"), ("names", "label", "9")]


def test_a_studio_is_asked_for_by_the_spelling_the_library_has():
    client = LookupClient()
    client.names["studio"] = ["HBO", "HBO Max", "Bohbot Communications"]
    client.carriers.update({("studio", "hbo"): ["10"], ("studio", "hbo max"): ["11"], ("studio", "bohbot communications"): ["12"]})
    look = Lookups(None)
    ten, eleven, twelve = (_ctx(owner_id=i, rel_path=f"srv|{i}", client=client, lookups=look) for i in ("10", "11", "12"))
    assert _holds("studio", "is", "hbo", ten) and not _holds("studio", "is", "hbo", eleven) and not _holds("studio", "is", "HBO", twelve)
    assert client.asked == [("names", "studio", "1"), ("studio", "HBO", "1")]  # the library's own spelling, once
    assert _holds("studio", "is_not", "hbo", twelve) and not _holds("studio", "is", "Showtime", ten)  # a name the library has not: nobody
    assert _holds("studio", "contains", "hbo", eleven) and _holds("studio", "contains", "hbo", twelve)  # in part is another comparison


def test_lookups_a_name_in_part_leaves_items_alone_when_the_names_are_not_listed():
    client = LookupClient()
    client.down = True
    look = Lookups(None)
    for _ in range(3):
        with pytest.raises(Unavailable):
            _holds("genre", "contains", "act", _ctx(client=client, lookups=look))
    assert client.asked == [("names", "genre", "1")]  # asked once, not per item
    with pytest.raises(Unavailable):
        _holds("genre", "begins", "act", _ctx(client=None, lookups=look))


def test_lookups_an_empty_answer_from_a_request_that_failed_is_no_answer():
    """Jellyfin's client gives up on a request quietly and hands back an empty list: nobody
    carrying the label is not what that says."""
    client = LookupClient()
    client.names["label"] = ["Kids"]
    client.carriers[("label", "kids")] = ["10"]
    client.collections["Pixar"] = ["10"]
    client.silent = True
    look = Lookups(None)
    ten = _ctx(client=client, lookups=look)
    for field, op, value in (("label", "is", "Kids"), ("label", "contains", "kid"), ("collection", "is", "Pixar"), ("label", "is_not", "Kids"), ("studio", "is", "HBO")):
        with pytest.raises(Unavailable):
            _holds(field, op, value, ten)

    class Collected(LookupClient):
        def collected_item_ids(self, library_key):
            self.failed_requests += 1
            return set()

    with pytest.raises(Unavailable):
        _holds("collected", "is", "no", _ctx(client=Collected(), lookups=Lookups(None)))
    # Once it answers, the same questions are settled (by an engine of their own: an answer is kept for a run).
    client.silent = False
    again = _ctx(client=client, lookups=Lookups(None))
    assert _holds("label", "is", "Kids", again) and _holds("label", "contains", "kid", again) and _holds("collection", "is", "Pixar", again)


RADARR = {"tags": {"kids": 1, "4k": 2}, "media": [({1}, {"movie:tmdb:949", "imdb:tt0113277"}), ({2}, {"movie:tmdb:5"})]}
RADARR_4K = {"tags": {"kids": 3}, "media": [({3}, {"movie:tmdb:77"})]}
SONARR = {"tags": {"kids": 7}, "media": [({7}, {"show:tvdb:300"})]}
ARRS = {("radarr", "Radarr"): RADARR, ("radarr", "Radarr 4k"): RADARR_4K, ("sonarr", "Sonarr"): SONARR}


def _arr_lookups(calls, down=()):
    def reader(kind, name):
        calls.append((kind, name))
        if name in down:
            raise ConnectionError("it could not be reached")
        return ARRS[(kind, name)]

    return Lookups(None, instance_names=lambda kind: [name for k, name in ARRS if k == kind], arr_reader=reader)


def test_lookups_arr_tags_by_app_and_instance_and_shared_for_a_while():
    calls = []
    look = _arr_lookups(calls)
    heat, five, uhd = _ctx(), _ctx(tmdb_id=5, imdb_id=None), _ctx(tmdb_id=77, imdb_id=None)
    show = _ctx("show", tmdb_id=None, tvdb_id=300, imdb_id=None)
    # One instance is asked about, and only that one is read.
    assert look.arr_tagged(heat, "radarr", "Kids", "Radarr") and not look.arr_tagged(heat, "radarr", "4k", "Radarr") and not look.arr_tagged(heat, "radarr", "nope", "Radarr")
    assert not look.arr_tagged(five, "radarr", "kids", "Radarr") and look.arr_tagged(five, "radarr", "4K", "Radarr")
    assert calls == [("radarr", "Radarr")]
    # The same tag name in another instance is another tag.
    assert not look.arr_tagged(heat, "radarr", "kids", "Radarr 4k") and look.arr_tagged(uhd, "radarr", "kids", "Radarr 4k") and not look.arr_tagged(uhd, "radarr", "kids", "Radarr")
    assert calls == [("radarr", "Radarr"), ("radarr", "Radarr 4k")]
    # No instance named: any instance of the app will do.
    assert look.arr_tagged(heat, "radarr", "kids") and look.arr_tagged(uhd, "radarr", "kids") and not look.arr_tagged(five, "radarr", "kids")
    # Radarr's tags are a movie's, Sonarr's a show's: the other kind never has one, and is not asked.
    assert not look.arr_tagged(heat, "sonarr", "kids", "Sonarr") and not look.arr_tagged(show, "radarr", "kids", "Radarr") and len(calls) == 2
    assert look.arr_tagged(show, "sonarr", "kids", "Sonarr")
    assert not look.arr_tagged(_ctx("show", tmdb_id=949, tvdb_id=None, imdb_id=None), "sonarr", "kids", "Sonarr")  # a movie's TMDB id is not a show's
    assert calls == [("radarr", "Radarr"), ("radarr", "Radarr 4k"), ("sonarr", "Sonarr")]
    # An instance that was removed from the settings matches nothing, and nothing is read for it.
    assert not look.arr_tagged(heat, "radarr", "kids", "Old Radarr") and len(calls) == 3
    assert _arr_lookups(calls).arr_tagged(heat, "radarr", "kids", "Radarr") and len(calls) == 3  # another job, minutes later: not asked again
    fresh = _arr_lookups(calls)
    fresh.begin_run(refresh=True)  # a rebuild asks again
    assert fresh.arr_tagged(heat, "radarr", "kids", "Radarr") and calls[3:] == [("radarr", "Radarr")]
    # The two conditions, as a badge reads them.
    movie = _ctx(lookups=look)
    radarr = {"field": "radarr_tag", "op": "is", "value": "kids", "instance": "Radarr"}
    assert rule_holds(radarr, movie, {}) and not rule_holds({**radarr, "instance": "Radarr 4k"}, movie, {}) and rule_holds({**radarr, "op": "is_not", "instance": "Radarr 4k"}, movie, {})
    assert not _holds("sonarr_tag", "is", "kids", movie) and _holds("sonarr_tag", "is_not", "kids", movie)
    assert _holds("sonarr_tag", "is", "kids", _ctx("show", tmdb_id=None, tvdb_id=300, imdb_id=None, lookups=look))
    # The instance is kept where a rule can name one (an Arr's tag, something a server holds), and only when it names one.
    assert normalize_rules([{**radarr, "instance": "  Radarr 4k  ", "junk": 1}, {"field": "sonarr_tag", "op": "is", "value": "kids", "instance": ""},
                            {"field": "label", "op": "is", "value": "x", "instance": "Plex"}, {"field": "title", "op": "is", "value": "x", "instance": "Plex"}]) == [
        {**radarr, "instance": "Radarr 4k"}, _rule("sonarr_tag", "is", "kids"), {**_rule("label", "is", "x"), "instance": "Plex"}, _rule("title", "is", "x")]


def test_lookups_arr_tags_by_part_of_their_name():
    calls = []
    look = _arr_lookups(calls)
    heat, five, uhd = _ctx(lookups=look), _ctx(tmdb_id=5, imdb_id=None, lookups=look), _ctx(tmdb_id=77, imdb_id=None, lookups=look)
    assert _holds("radarr_tag", "contains", "kid", heat) and not _holds("radarr_tag", "contains", "kid", five) and _holds("radarr_tag", "contains", "kid", uhd)
    assert _holds("radarr_tag", "regex", r"^\dk$", five) and not _holds("radarr_tag", "regex", r"^\dk$", heat) and _holds("radarr_tag", "begins", "4", five)
    assert _holds("radarr_tag", "not_contains", "kid", five) and not _holds("radarr_tag", "not_contains", "kid", heat) and _holds("radarr_tag", "ends", "ids", heat)
    # One instance named: its tags only.
    named = {"field": "radarr_tag", "op": "contains", "value": "kid", "instance": "Radarr 4k"}
    assert not rule_holds(named, heat, {}, TODAY) and rule_holds(named, uhd, {}, TODAY)
    assert sorted(calls) == [("radarr", "Radarr"), ("radarr", "Radarr 4k")]  # each instance read once
    show = _ctx("show", tmdb_id=None, tvdb_id=300, imdb_id=None, lookups=look)
    assert _holds("sonarr_tag", "begins", "ki", show) and not _holds("radarr_tag", "begins", "ki", show)


def test_lookups_arr_instance_that_does_not_answer():
    calls = []
    look = _arr_lookups(calls, down=("Radarr 4k",))
    heat, other = _ctx(), _ctx(tmdb_id=77, imdb_id=None)
    for _ in range(2):
        with pytest.raises(Unavailable):
            look.arr_tagged(heat, "radarr", "kids", "Radarr 4k")
    assert calls == [("radarr", "Radarr 4k")]  # asked once, not per item
    # The instance that answers still does: only what the silent one might hold is unknown.
    assert look.arr_tagged(heat, "radarr", "kids", "Radarr") and not look.arr_tagged(other, "radarr", "kids", "Radarr")
    assert look.arr_tagged(heat, "radarr", "kids")  # found in the one that answers
    with pytest.raises(Unavailable):
        look.arr_tagged(other, "radarr", "kids")  # not there, and the other one cannot say


def test_arr_tags_to_pick_from_are_listed_instance_by_instance(client, monkeypatch):
    def reader(kind):
        if kind == "radarr":
            return [("Radarr", ["kids", "4K-only", "kids"]), ("Radarr 4k", ["Kids"])]
        return [("Sonarr", ["anime", "kids"]), ("Sonarr 4k", None)]  # the last one did not answer

    radarr = arr_tag_choices(None, "radarr", reader)
    assert radarr == {"unreachable": [], "values": [
        {"value": "4K-only", "instance": "Radarr", "label": "4K-only", "note": "Radarr"}, {"value": "kids", "instance": "Radarr", "label": "kids", "note": "Radarr"},
        {"value": "Kids", "instance": "Radarr 4k", "label": "Kids", "note": "Radarr 4k"}]}
    sonarr = arr_tag_choices(None, "sonarr", reader)
    assert [(v["value"], v["instance"]) for v in sonarr["values"]] == [("anime", "Sonarr"), ("kids", "Sonarr")] and sonarr["unreachable"] == ["Sonarr 4k"]
    assert arr_tag_choices(None, "radarr", lambda kind: []) == {"values": [], "unreachable": []}
    assert "arr_tag" not in FIELDS
    monkeypatch.setattr(lookups_mod, "read_arr_tags", lambda db, kind: reader(kind))
    answer = client.get("/api/overlays/rule-values/radarr_tag").json()
    assert answer["field"] == "radarr_tag" and [(v["label"], v["note"]) for v in answer["values"]] == [("4K-only", "Radarr"), ("kids", "Radarr"), ("Kids", "Radarr 4k")]
    assert answer["unreachable"] == []
    assert [(v["label"], v["note"]) for v in client.get("/api/overlays/rule-values/sonarr_tag").json()["values"]] == [("anime", "Sonarr"), ("kids", "Sonarr")]
    assert client.get("/api/overlays/rule-values/title").status_code == 404 and client.get("/api/overlays/rule-values/arr_tag").status_code == 404


class ValuesClient:
    """A media server that lists what its libraries hold: {(field, library key): [values]}."""

    def __init__(self, server_type="plex", values=None, collections=None, labels=None):
        self.server_type = server_type
        self.values = values or {}
        self.collections = collections or {}
        self.labels = labels  # {library key: {label: movies or shows carrying it}}; None = the label list is exact
        self.asked = []

    def label_counts(self, library_key):
        return None if self.labels is None else dict(self.labels.get(library_key, {}))

    def field_values(self, field, library_key):
        self.asked.append((field, library_key))
        return list(self.values.get((field, library_key), []))

    def get_collections(self, library_key=None):
        self.asked.append(("collection", library_key))
        return [_item(f"c{n}", "collection", title, None) for n, title in enumerate(self.collections.get(library_key, []))]


def _servers(test_db):
    upsert_setting(test_db, "plex_instances", json.dumps([
        {"name": "Plex", "url": "http://plex", "api_key": "k"}, {"name": "Jelly", "url": "http://jelly", "api_key": "k", "type": "jellyfin"},
        {"name": "Down", "url": "http://down", "api_key": "k"}]))
    test_db.commit()


def _listed(found):
    return [(v["value"], v["instance"]) for v in found["values"]]


def test_values_to_pick_from_come_from_the_overlays_libraries_server_by_server(test_db):
    _servers(test_db)
    plex = ValuesClient(values={("genre", "1"): ["Action", "Comedy"], ("genre", "2"): ["comedy", "Anime"], ("label", "1"): ["Kids"]},
                        collections={"1": ["Pixar", "Bond"], "2": ["Bond"]})
    jelly = ValuesClient("jellyfin", values={("genre", "9"): ["Drama", "Action"]}, collections={"9": ["Box"], "8": ["Box"]})
    clients = {"Plex": plex, "Jelly": jelly, "Down": None}
    connect = lambda inst: clients[inst["name"]]  # noqa: E731
    genres = server_values(test_db, "genre", ["Jelly:9", "Plex:1", "Plex:2", "Gone:4", "Plex:1"], connect)
    # Each server's own, in the order the servers are set up; a server's libraries together (one Comedy, however it is spelled).
    assert _listed(genres) == [("Action", "Plex"), ("Anime", "Plex"), ("Comedy", "Plex"), ("Action", "Jelly"), ("Drama", "Jelly")]
    assert [(v["label"], v["note"]) for v in genres["values"]] == [("Action", "Plex"), ("Anime", "Plex"), ("Comedy", "Plex"), ("Action", "Jelly"), ("Drama", "Jelly")]
    assert genres["unreachable"] == []  # the name is the label, the server what is shown beside it
    assert plex.asked == [("genre", "1"), ("genre", "2")]  # a library named twice is asked once; an unknown server not at all
    assert _listed(server_values(test_db, "label", ["Plex:1"], connect)) == [("Kids", "Plex")]
    assert [(v["label"], v["note"]) for v in server_values(test_db, "collection", ["Plex:1", "Plex:2"], connect)["values"]] == [("Bond", "Plex"), ("Pixar", "Plex")]
    jelly.asked.clear()
    assert _listed(server_values(test_db, "collection", ["Jelly:9", "Jelly:8"], connect)) == [("Box", "Jelly")]
    assert jelly.asked == [("collection", "9")]  # Jellyfin's collections are the server's: asked once
    down = server_values(test_db, "genre", ["Plex:1", "Down:1"], connect)
    assert _listed(down) == [("Action", "Plex"), ("Comedy", "Plex")] and down["unreachable"] == ["Down"]
    assert server_values(test_db, "genre", [], connect) == {"values": [], "unreachable": []}


def test_labels_to_pick_from_are_the_ones_a_movie_or_show_carries(test_db):
    _servers(test_db)
    # Plex lists every label of the server for any library; the counts say which are this library's.
    plex = ValuesClient(labels={"1": {"Pixar": 31, "Kometa": 0, "Trending": 35, "keyU": 0}, "2": {"Pixar": 0, "Kometa": 0, "trending": 27, "keyU": 84}})
    jelly = ValuesClient("jellyfin", values={("label", "9"): ["anime", "Pixar"]})
    connect = lambda inst: {"Plex": plex, "Jelly": jelly}[inst["name"]]  # noqa: E731
    movies = server_values(test_db, "label", ["Plex:1"], connect)
    noted = lambda found: [f"{v['label']} | {v['note']}" for v in found["values"]]  # noqa: E731
    assert movies["values"] == [{"value": "Pixar", "instance": "Plex", "label": "Pixar", "note": "Plex · 31"},
                                {"value": "Trending", "instance": "Plex", "label": "Trending", "note": "Plex · 35"}]  # not the collections' Kometa
    shows = server_values(test_db, "label", ["Plex:2"], connect)
    assert noted(shows) == ["keyU | Plex · 84", "trending | Plex · 27"]
    both = server_values(test_db, "label", ["Plex:1", "Plex:2"], connect)
    assert noted(both) == ["keyU | Plex · 84", "Pixar | Plex · 31", "Trending | Plex · 62"]  # the server's libraries: counted together
    assert plex.asked == []  # the plain list is not asked for where labels are counted
    # Jellyfin's own list is exact: no counts there. The same name on two servers is two entries.
    mixed = server_values(test_db, "label", ["Jelly:9", "Plex:1"], connect)
    assert noted(mixed) == ["Pixar | Plex · 31", "Trending | Plex · 35", "anime | Jelly", "Pixar | Jelly"]
    # Other fields are listed as before, with no count.
    plex.values[("genre", "1")] = ["Action"]
    assert server_values(test_db, "genre", ["Plex:1"], connect)["values"] == [{"value": "Action", "instance": "Plex", "label": "Action", "note": "Plex"}] and plex.asked == [("genre", "1")]


def test_a_condition_naming_a_server_that_is_gone_is_warned_about_once(test_db, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(lookups_mod, "log_warning", lambda tag, message: warnings.append(message))
    look = Lookups(None, server_names=lambda: ["Plex", "Jelly"])
    on_plex = _ctx(rel_path="Plex|10", lookups=look, content_rating="PG")
    assert not rule_holds({"field": "content_rating", "op": "is", "value": "PG", "instance": "Jelly"}, on_plex, {}) and warnings == []  # another server that is there
    for _ in range(3):
        assert not rule_holds({"field": "content_rating", "op": "is", "value": "PG", "instance": "Old Plex"}, on_plex, {})
        assert not rule_holds({"field": "genre", "op": "is", "value": "Action", "instance": "Old Plex"}, on_plex, {})
    assert warnings == ["Custom overlays: the media server 'Old Plex' is not set up any more. Conditions that name it match nothing. "
                        "Pick their values again in the overlay's conditions."]
    Lookups(None).check_server("Anything")  # the servers are not known here: nothing to say
    assert len(warnings) == 1
    # In a run the engine's own servers are the ones that count.
    warnings.clear()
    cfg = _custom_config({"name": "Gone", "text": "X", "rules": [{"field": "genre", "op": "is", "value": "Action", "instance": "Old Plex"}]},
                         {"name": "Here", "text": "Y", "rules": [{"field": "content_rating", "op": "is_not", "value": "R", "instance": "srv"}]})
    engine = _engine(test_db, FakeClient(), FakeMetadata({}), cfg, sources=_sources(tmp_path, 10, 20, 30))
    stats = engine.run()
    assert stats["by_state"] == {"HERE": 3} and stats["unanswered"] == 0
    assert [w for w in warnings if "not set up" in w] == ["Custom overlays: the media server 'Old Plex' is not set up any more. Conditions that name it match nothing. "
                                                          "Pick their values again in the overlay's conditions."]


def test_a_condition_on_one_servers_value_matches_that_servers_posters_only():
    client = LookupClient()
    client.carriers[("label", "kids")] = ["10"]
    look = Lookups(None)
    on_plex = _ctx(rel_path="Plex|10", client=client, lookups=look, content_rating="PG", edition="IMAX")
    on_jelly = _ctx(rel_path="Jelly|10", client=client, lookups=look, content_rating="PG", edition="IMAX")
    label = {"field": "label", "op": "is", "value": "Kids", "instance": "Plex"}
    assert rule_holds(label, on_plex, {}) and not rule_holds(label, on_jelly, {})
    assert rule_holds({**label, "instance": ""}, on_jelly, {}) and rule_holds(_rule("label", "is", "Kids"), on_jelly, {})  # no server named: any server's posters
    assert len([a for a in client.asked if a[0] == "label"]) == 2  # Plex's and Jelly's library, each once; never for the other server's rule
    # The same for what is read from the item itself.
    assert rule_holds({"field": "content_rating", "op": "is", "value": "PG", "instance": "Plex"}, on_plex, {})
    assert not rule_holds({"field": "content_rating", "op": "is", "value": "PG", "instance": "Plex"}, on_jelly, {})
    assert rule_holds({"field": "edition", "op": "contains", "value": "imax", "instance": "Jelly"}, on_jelly, {})
    assert not rule_holds({"field": "edition", "op": "contains", "value": "imax", "instance": "Jelly"}, on_plex, {})
    assert rule_holds({"field": "content_rating", "op": "is_not", "value": "PG", "instance": "Plex"}, on_jelly, {})  # it does not carry Plex's PG


def test_values_to_pick_from_tmdb(test_db, monkeypatch):
    asked = []

    class Resp:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    def fake_get(url, params=None, timeout=None):
        asked.append((url.rsplit("/3", 1)[-1], (params or {}).get("query")))
        assert params["api_key"] == "tmdb-key"
        if url.endswith("/genre/movie/list"):
            return Resp({"genres": [{"id": 16, "name": "Animation"}, {"id": 35, "name": "Comedy"}]})
        if url.endswith("/genre/tv/list"):
            return Resp({"genres": [{"id": 35, "name": "Comedy"}, {"id": 10765, "name": "Sci-Fi & Fantasy"}]})
        if url.endswith("/configuration/languages"):
            return Resp([{"iso_639_1": "ja", "english_name": "Japanese", "name": "x"}, {"iso_639_1": "xx", "english_name": "No Language", "name": ""},
                         {"iso_639_1": "en", "english_name": "English", "name": "English"}])
        if url.endswith("/configuration/countries"):
            return Resp([{"iso_3166_1": "US", "english_name": "United States of America"}, {"iso_3166_1": "JP", "english_name": "Japan"}])
        return Resp({"results": [{"id": 1, "name": "based on novel or book"}, {"id": 2, "name": "based on comic"}]})

    monkeypatch.setattr(lookups_mod.requests, "get", fake_get)
    assert rule_values(test_db, "tmdb_genre") == {"values": [], "unreachable": ["TMDB"]} and asked == []  # no key yet
    upsert_setting(test_db, "tmdb_api_key", "tmdb-key")
    test_db.commit()
    assert [v["value"] for v in rule_values(test_db, "tmdb_genre")["values"]] == ["Animation", "Comedy", "Sci-Fi & Fantasy"]  # movies and TV together
    assert rule_values(test_db, "original_language")["values"] == [{"value": "en", "label": "English", "note": "en"}, {"value": "ja", "label": "Japanese", "note": "ja"}]
    assert rule_values(test_db, "country")["values"] == [{"value": "JP", "label": "Japan", "note": "JP"}, {"value": "US", "label": "United States of America", "note": "US"}]
    assert len(asked) == 4
    rule_values(test_db, "tmdb_genre"), rule_values(test_db, "original_language"), rule_values(test_db, "country")
    assert len(asked) == 4  # TMDB's lists are kept for the day
    assert rule_values(test_db, "tmdb_keyword", query="b") == {"values": [], "unreachable": []} and len(asked) == 4  # too short to search
    found = rule_values(test_db, "tmdb_keyword", query=" Based ")
    assert [v["value"] for v in found["values"]] == ["based on novel or book", "based on comic"] and asked[-1] == ("/search/keyword", "Based")
    assert rule_values(test_db, "title") is None and rule_values(test_db, "released") is None
    # The audio and subtitle languages are picked from the same list, asked for once.
    before = len(asked)
    assert rule_values(test_db, "audio_language") == rule_values(test_db, "original_language") == rule_values(test_db, "subtitle_language") and len(asked) == before
    # Companies and collections are searched for like keywords.
    assert [v["value"] for v in rule_values(test_db, "tmdb_company", query="pix")["values"]] == ["based on novel or book", "based on comic"] and asked[-1] == ("/search/company", "pix")
    assert rule_values(test_db, "tmdb_collection", query="toy")["values"] and asked[-1] == ("/search/collection", "toy")
    assert rule_values(test_db, "tmdb_collection", query="t") == {"values": [], "unreachable": []}


def test_api_rule_values_take_the_libraries_and_the_search(client, monkeypatch):
    seen = []

    def fake(db, field, libraries=None, query=""):
        seen.append((field, libraries, query))
        return {"values": [{"value": "Action", "label": "Action"}], "unreachable": []} if field != "title" else None

    monkeypatch.setattr("api.overlays.rule_values", fake)
    answer = client.get("/api/overlays/rule-values/genre", params={"libraries": "Plex:1,Plex:2"}).json()
    assert answer == {"field": "genre", "values": [{"value": "Action", "label": "Action"}], "unreachable": []}
    client.get("/api/overlays/rule-values/tmdb_keyword", params={"q": "based"})
    assert seen == [("genre", ["Plex:1", "Plex:2"], ""), ("tmdb_keyword", [], "based")]
    assert client.get("/api/overlays/rule-values/title").status_code == 404
    # What the catalog says can be picked: listed, or searched as you type.
    assert {f["key"]: f["suggest"] for f in describe_fields() if f["suggest"]} == {
        "label": "list", "genre": "list", "collection": "list", "studio": "list", "content_rating": "list", "radarr_tag": "list", "sonarr_tag": "list",
        "edition": "list", "audio_language": "list", "subtitle_language": "list", "tmdb_genre": "list", "tmdb_keyword": "search", "original_language": "list",
        "country": "list", "tmdb_company": "search", "tmdb_collection": "search"}


def test_lookups_lists():
    calls = []

    def reader(url):
        calls.append(url)
        return {"movie:tmdb:949", "imdb:tt1"}

    look = Lookups(None, list_reader=reader)
    url = "https://mdblist.com/lists/someone/top-10"
    assert look.listed(_ctx(), url) and not look.listed(_ctx(tmdb_id=1, imdb_id=None), url) and look.listed(_ctx("show", tmdb_id=None, imdb_id="tt1"), url)
    assert not look.listed(_ctx("show", imdb_id=None), url)  # the list's movie 949 is not the show 949
    assert calls == [url]
    with pytest.raises(Unavailable):
        look.listed(_ctx(), "https://trakt.tv/users/someone/lists/top-10")
    assert calls == [url]


def test_read_mdblist_pages_through_the_list(monkeypatch):
    asked = []

    class Resp:
        def __init__(self, rows):
            self.rows = rows

        def raise_for_status(self):
            pass

        def json(self):
            return self.rows

    def fake_get(url, params=None, headers=None, timeout=None):
        asked.append((url, params["offset"]))
        if params["offset"] == 0:
            return Resp([{"id": n, "imdb_id": f"tt{n}", "tvdbid": None, "mediatype": "movie"} for n in range(1, 1001)])
        return Resp([{"id": 7, "tvdbid": 70, "imdb_id": None, "mediatype": "show"}])

    monkeypatch.setattr(lookups_mod.requests, "get", fake_get)
    keys = read_mdblist("https://mdblist.com/lists/someone/top-10")
    assert asked == [("https://mdblist.com/lists/someone/top-10/json", 0), ("https://mdblist.com/lists/someone/top-10/json", 1000)]
    assert {"movie:tmdb:1", "imdb:tt1000", "show:tmdb:7", "show:tvdb:70"} <= keys and len(keys) == 2002


# ── the type ─────────────────────────────────────────────────────────────────

def _png(size=(120, 60), box=(20, 10, 100, 50)):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    img.paste((255, 200, 0, 255), box)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


_ROW_KEYS = ("text", "image", "rules", "match")


def _badge_row(name, n=1, **kw):
    return {"id": f"{n:08x}", "name": name, **kw}


def _options(**kw):
    """Custom options, text badges unless told otherwise. Without `badges`, the wording, image and
    conditions given are the overlay's one badge."""
    if "badges" in kw:
        return {"badge": "text", **kw}
    row = {key: kw.pop(key) for key in _ROW_KEYS if key in kw}
    return {"badge": "text", **kw, "badges": [_badge_row("Badge 1", **row)]}


def test_custom_type_describes_itself(custom):
    desc = custom.describe()
    assert desc["multi"] is True and desc["targets"] == ["movie", "show", "season"] and desc["hidden_placement_when"] == {"text": {"badge": "image"}}
    assert desc["hidden_placement"] == ["stroke_width", "stroke_color"]  # the Text card has no letter outline here
    assert all(get_type(key).describe()["multi"] is False for key in ("status", "rating", "streaming"))
    schema = {f["key"]: f for f in desc["schema"]}
    rows = schema["badges"]
    assert rows["kind"] == "badges" and rows["kind_option"] == "badge" and rows["fields"] == describe_fields() and rows["max"] == 30
    assert [m["value"] for m in rows["modes"]] == ["all", "any"]
    assert schema["badge"]["in_text_card"] and schema["image_width"]["show_when"] == {"badge": "image"}
    assert desc["group_options"] == {"group": "group", "weight": "weight"} and get_type("status").describe()["group_options"] is None
    pair = schema["group_weight"]
    assert pair["kind"] == "pair" and [(f["key"], f["kind"]) for f in pair["pair"]] == [("group", "text"), ("weight", "number")]
    assert pair["pair"][1]["disabled_when"] == {"group": ""}  # a weight means nothing outside a group
    assert "whole overlay" in pair["pair"][1]["help"] and "each badge in the table below has its own" in pair["pair"][1]["help"]  # whose weight it is
    # A new overlay starts as an image, with one badge.
    start = desc["default_options"]
    assert start["badge"] == "image" and [(r["name"], r["enabled"], r["rules"]) for r in start["badges"]] == [("Badge 1", True, [])]
    assert desc["variants"] == [{"key": start["badges"][0]["id"], "label": "Badge 1"}]
    assert desc["default_placement"]["back_enabled"] is False and get_type("edition").describe()["default_placement"]["back_enabled"] is True  # no box behind a new one
    assert normalize_config({"types": {"custom": {"overlays": [{"placement": {"back_enabled": True}}]}}})["types"]["custom"]["overlays"][0]["placement"]["back_enabled"] is True


def test_custom_options_normalize(custom):
    opts = custom.normalize_options({"badge": "text", "image_width": "5000", "movies": "false", "group": " Top 10 ", "weight": "60", "extra": 1, "badges": [
        {"id": "0000000a", "name": "  Netflix  ", "text": "  TOP 10  ", "image": "../../etc", "weight": "70", "match": "some",
         "rules": [_rule("label", "is", "x"), _rule("nope", "is", "x")]},
        {"id": "0000000a", "enabled": "false", "weight": 123456, "match": "any"},  # the same id twice: the second gets its own
        "junk",
    ]})
    first, second = opts.pop("badges")
    assert opts == {"badge": "text", "image_width": 1000, "movies": False, "shows": True, "apply_to_seasons": False, "group": "Top 10", "weight": 60}
    assert first == {"id": "0000000a", "name": "Netflix", "enabled": True, "text": "TOP 10", "image": "", "size": 0, "weight": 70, "match": "all", "rules": [_rule("label", "is", "x")]}
    assert second["id"] != "0000000a" and len(second["id"]) == 8
    assert {**second, "id": ""} == {"id": "", "name": "Badge 2", "enabled": False, "text": "CUSTOM", "image": "", "size": 0, "weight": 9999, "match": "any", "rules": []}
    assert custom.normalize_options("junk") == custom.default_options()
    assert custom.normalize_options({"badge": "bogus", "weight": "heavy"})["badge"] == "image" and custom.normalize_options({"weight": "heavy"})["weight"] == 0
    # There is always a badge to edit, and never more than thirty.
    assert custom.normalize_options({"badges": []})["badges"] == custom.default_options()["badges"]
    assert len(custom.normalize_options({"badges": [{"name": f"b{n}"} for n in range(50)]})["badges"]) == 30
    assert custom.needs_for(custom.normalize_options(_options(rules=[_rule("resolution", "is", "4k")]))) == ("media",)
    assert custom.needs_for(custom.normalize_options(_options(badges=[
        _badge_row("a", 1, rules=[_rule("resolution", "is", "4k")]), _badge_row("b", 2, rules=[_rule("tmdb_genre", "is", "x")]),
        _badge_row("off", 3, enabled=False, rules=[_rule("tmdb_keyword", "is", "x")])]))) == ("media", "tmdb", "details")
    assert custom.needs_for(custom.default_options()) == ()


def test_custom_badge_follows_the_poster_kinds_and_the_conditions(custom):
    opts = custom.normalize_options(_options(text="NEW RELEASE", rules=[_rule("year", "gte", 1990)], group="top"))
    badge = custom.resolve(_ctx(), opts, {})
    assert badge.text == "NEW RELEASE" and badge.group == "top" and badge.weight == 0 and badge.image is None
    assert badge.key == ""  # the overlay's one badge: its overlay's name says it all
    assert badge.placement_overrides == {"stroke_width": 0}  # never a letter outline, whatever an older setting says
    assert custom.resolve(_ctx(), {**opts, "weight": 60}, {}).weight == 60
    assert custom.resolve(_ctx(year=1980), opts, {}) is None
    assert custom.resolve(_ctx("show"), opts, {}).text == "NEW RELEASE" and custom.resolve(_ctx("show", season_number=1), opts, {}) is None
    assert custom.resolve(_ctx("show", season_number=1), {**opts, "apply_to_seasons": True, "shows": False}, {}).text == "NEW RELEASE"
    assert custom.resolve(_ctx(), {**opts, "movies": False}, {}) is None
    assert custom.resolve(_ctx(), custom.normalize_options(_options(text="")), {}) is None
    assert custom.resolve_variant(opts["badges"][0]["id"], opts).text == "NEW RELEASE"  # the preview draws it whatever the conditions


def test_custom_overlay_draws_the_heaviest_of_its_badges_that_match(custom):
    opts = custom.normalize_options(_options(group="awards", weight=5, badges=[
        _badge_row("Nineties", 1, text="90s", weight=10, rules=[_rule("year", "lte", 1999)]),
        _badge_row("Classic", 2, text="CLASSIC", weight=50, rules=[_rule("year", "lte", 1979)]),
        _badge_row("Heat", 3, text="HEAT", weight=50, rules=[_rule("title", "is", "heat")]),
        _badge_row("Switched off", 4, text="OFF", weight=99, enabled=False),
        _badge_row("Nothing to say", 5, text="", weight=99),
        _badge_row("Any", 6, text="ANY", weight=1),
    ]))
    draw = lambda **kw: custom.resolve(_ctx(**kw), opts, {})  # noqa: E731
    heat = draw()  # Heat (1995): Nineties, Heat and Any match
    assert (heat.text, heat.key, heat.group, heat.weight) == ("HEAT", "Heat", "awards", 5)  # the group weighs the overlay, not the badge
    assert draw(title="Jaws", year=1975).text == "CLASSIC"  # as heavy as Heat, and before it
    assert draw(title="Heat", year=1975).text == "CLASSIC" and draw(title="Speed", year=1994).text == "90s" and draw(title="Up", year=2009).text == "ANY"
    # The preview draws the badge it is asked for, or the heaviest one there is.
    assert custom.resolve_variant(opts["badges"][0]["id"], opts).text == "90s" and custom.resolve_variant(opts["badges"][3]["id"], opts).text == "OFF"
    assert custom.resolve_variant("", opts).text == "CLASSIC" and custom.resolve_variant(opts["badges"][4]["id"], opts) is None


def test_text_variables_are_filled_in_per_poster():
    from services.overlays import text_vars

    film = _ctx(content_rating="PG-13", edition="Director's Cut", ratings={"audience": 8.4, "critic": 7.0}, release_date="2024-07-19",
                media=[_file(minutes=127.6, bitrate=12.7), _file(minutes=131.0, bitrate=40.2)])
    meta = {"tmdb": {"vote_average": 7.64, "vote_count": 900}}
    say = lambda text, ctx=film: text_vars.render(text, ctx, meta)  # noqa: E731
    assert say("NO VARIABLES") == "NO VARIABLES" and say("") == ""
    assert say("<<title>> (<<year>>)") == "Heat (1995)" and say("<<titleU>> <<titleL>> <<content_rating>> <<editionU>>") == "HEAT heat PG-13 DIRECTOR'S CUT"
    assert say("<<audience_rating>> <<audience_rating%>>% <<critic_rating#>> <<critic_rating/>> <<tmdb_rating>>") == "8.4 84% 7 3.5 7.6"
    assert say("<<runtime>> min, <<runtimeH>>h <<runtimeM>>m") == "131 min, 2h 11m"  # a movie's longest version
    assert say("<<versions>> versions, <<versionsW>>, <<versions0>>, <<versionsWU>>") == "2 versions, two, 02, TWO"
    assert say("<<bitrate>> <<bitrateH>> <<bitrateL>>") == "12700 40200 12700"
    assert say("<<originally_available>> / <<originally_available[%b %d, %Y]>>") == "2024-07-19 / Jul 19, 2024"
    assert say("<<nonsense>> <<title>>") == "<<nonsense>> Heat"  # not a variable: as it was typed
    # Nothing to say for one of them: no wording at all.
    assert say("<<user_rating>>") is None and say("<<total_runtime>>") is None and say("<<runtime>>", _ctx()) is None and say("<<show_title>>") is None
    # A show's runtime is its average episode; a season says which one it is.
    season = _ctx("show", title="Lost", season_number=3, media=[_file(minutes=40.0), _file(minutes=44.0), _file(minutes=45.0)])
    assert say("<<show_title>> <<title>>: <<season_numberW>> (<<season_number0>>), <<episode_count>> eps, <<runtime>> min, <<total_runtimeH>>h<<total_runtimeM>>", season) \
        == "Lost Season 3: three (03), 3 eps, 43 min, 2h9"
    assert [text_vars._words(n) for n in (0, 7, 13, 20, 42, 100, 215, 1200)] == ["zero", "seven", "thirteen", "twenty", "forty-two", "one hundred", "two hundred fifteen", "1200"]
    assert text_vars.needs("<<runtime>> <<tmdb_rating>>") == ("media", "tmdb") and text_vars.needs("<<title>>") == () and text_vars.needs("<<nonsense>>") == ()
    assert text_vars.sample("<<runtimeH>>h <<runtimeM>>m · <<audience_rating%>>%") == "2h 7m · 84%"


def test_custom_badge_wording_takes_variables(custom):
    opts = custom.normalize_options(_options(badges=[
        _badge_row("Score", 1, text="<<audience_rating%>>%", weight=9),
        _badge_row("Length", 2, text="<<runtimeH>>h <<runtimeM>>m", weight=5),
        _badge_row("Plain", 3, text="NEW", weight=1),
    ]))
    assert custom.needs_for(opts) == ("media",)
    rated = _ctx(ratings={"audience": 8.4}, media=[_file(minutes=127.0)])
    assert custom.resolve(rated, opts, {}).text == "84%"
    unrated = _ctx(media=[_file(minutes=127.0)])
    assert custom.resolve(unrated, opts, {}).text == "2h 7m"  # no score to show: the next badge that has something to say
    assert custom.resolve(_ctx(media=[]), opts, {}).text == "NEW"
    assert custom.resolve_variant(opts["badges"][0]["id"], opts).text == "84%" and custom.resolve_variant(opts["badges"][1]["id"], opts).text == "2h 7m"  # the preview's sample
    # The wording has room for a few variables; an image overlay reads none of them.
    assert len(custom.normalize_options(_options(text="x" * 300))["badges"][0]["text"]) == 120
    assert custom.needs_for(custom.normalize_options(_options(badge="image", text="<<runtime>>"))) == ()
    schema = {f["key"]: f for f in custom.schema()}
    assert "<<runtime>>" in schema["badges"]["text_help"] and schema["badges"]["text_limit"] == 120


def test_a_badge_may_have_a_size_of_its_own(custom, tmp_path, monkeypatch):
    monkeypatch.setattr(images, "config_overlays_dir", lambda: tmp_path / "overlays")
    image_id, _w, _h = custom_mod.store_badge(Image.open(io.BytesIO(_png())))
    pictures = custom.normalize_options(_options(badge="image", image_width=150, badges=[
        _badge_row("Big", 1, image=image_id, size=400, weight=9, rules=[_rule("year", "lte", 1999)]), _badge_row("Usual", 2, image=image_id, size="nonsense")]))
    assert [row["size"] for row in pictures["badges"]] == [400, 0]
    assert custom.resolve(_ctx(), pictures, {}).image_width == 400  # its own width
    assert custom.resolve(_ctx(year=2020), pictures, {}).image_width == 150  # none of its own: the overlay's
    words = custom.normalize_options(_options(badges=[_badge_row("Loud", 1, text="NEW", size=96, weight=9, rules=[_rule("year", "lte", 1999)]),
                                                      _badge_row("Quiet", 2, text="new"), _badge_row("Tiny", 3, text="x", size=2), _badge_row("Huge", 4, text="x", size=5000)]))
    assert custom.resolve(_ctx(), words, {}).placement_overrides == {"stroke_width": 0, "font_size": 96}  # a wording's size is its text size
    assert custom.resolve(_ctx(year=2020), words, {}).placement_overrides == {"stroke_width": 0}
    assert custom.resolve_variant(words["badges"][2]["id"], words).placement_overrides["font_size"] == 8  # kept within what a font can be
    assert words["badges"][3]["size"] == 1000 and custom.resolve_variant(words["badges"][3]["id"], words).placement_overrides["font_size"] == 400
    # A size of its own is a setting like any other: changing it is seen per poster.
    assert overlay_signature({"placement": {}, "options": words}) != overlay_signature({"placement": {}, "options": {**words, "badges": [{**words["badges"][0], "size": 97}, *words["badges"][1:]]}})


def test_custom_overlay_asks_only_as_far_as_the_first_match(custom):
    opts = custom.normalize_options(_options(badges=[
        _badge_row("Light", 1, text="LIGHT", weight=1, rules=[_rule("label", "is", "kids")]),  # would have to ask the server
        _badge_row("Heavy", 2, text="HEAVY", weight=9, rules=[_rule("year", "gte", 1990)]),
    ]))
    assert custom.resolve(_ctx(), opts, {}).text == "HEAVY"  # settled by the heavier badge: the label is never asked about
    with pytest.raises(Unavailable):
        custom.resolve(_ctx(year=1980), opts, {})  # now the lighter one decides, and there is nobody to ask


def test_custom_image_badge_and_its_files(custom, tmp_path, monkeypatch):
    monkeypatch.setattr(images, "config_overlays_dir", lambda: tmp_path / "overlays")
    image_id, width, height = custom_mod.store_badge(Image.open(io.BytesIO(_png())))
    assert (width, height) == (80, 40)  # cropped to its artwork
    assert custom_mod.badge_file(image_id) == tmp_path / "overlays" / "custom" / f"{image_id}.png"
    assert custom_mod.badge_file("../../secret") is None and custom_mod.badge_file("0" * 12) is None
    opts = custom.normalize_options(_options(badge="image", image_width=150, badges=[
        _badge_row("No picture yet", 1, weight=9), _badge_row("Ribbon", 2, image=image_id)]))
    badge = custom.resolve(_ctx(), opts, {})  # the heavier badge has no image: the next one is drawn
    assert badge.image.endswith(f"{image_id}.png") and badge.image_width == 150 and badge.image_stamp and badge.key == "Ribbon"
    assert custom.resolve(_ctx(), custom.normalize_options(_options(badge="image")), {}) is None  # no image yet: nothing to draw
    # Saving drops the files no badge names, but not one uploaded a moment ago (it may not be saved yet).
    other, _w, _h = custom_mod.store_badge(Image.open(io.BytesIO(_png())))
    assert custom_mod.prune_badges([{"options": opts}]) == 0 and custom_mod.badge_file(other) is not None
    old = time.time() - 7200
    os.utime(custom_mod.badge_file(other), (old, old))
    os.utime(custom_mod.badge_file(image_id), (old, old))
    assert custom_mod.prune_badges([{"options": opts}]) == 1
    assert custom_mod.badge_file(other) is None and custom_mod.badge_file(image_id) is not None


# ── the engine ───────────────────────────────────────────────────────────────

def _custom_config(*overlays, **top):
    return normalize_config({"enabled": True, **top, "types": {"custom": {"enabled": True, "overlays": [
        {"name": o.pop("name", f"Badge {n}"), "libraries": o.pop("libraries", []), "placement": o.pop("placement", {}), "options": _options(**o)}
        for n, o in enumerate(overlays, start=1)]}}})


def test_custom_overlays_may_name_the_same_library():
    cfg = _custom_config({"libraries": ["srv:1"]}, {"libraries": ["srv:1", "srv:2"]})
    assert [o["libraries"] for o in cfg["types"]["custom"]["overlays"]] == [["srv:1"], ["srv:1", "srv:2"]]
    status = normalize_config({"types": {"status": {"overlays": [{"libraries": ["srv:1"]}, {"libraries": ["srv:1", "srv:2"]}]}}})
    assert [o["libraries"] for o in status["types"]["status"]["overlays"]] == [["srv:1"], ["srv:2"]]  # one overlay per library there


def test_engine_draws_every_matching_custom_overlay(test_db, tmp_path):
    client = FakeClient()
    cfg = _custom_config(
        {"name": "Nineties", "text": "90s", "rules": [_rule("year", "lte", 1999)], "placement": {"vertical_align": "top"}},
        {"name": "Recent", "text": "RECENT", "rules": [_rule("year", "gte", 2020)]},
        {"name": "Everything", "text": "MINE", "placement": {"vertical_align": "center"}},
        {"name": "TV only", "text": "TV", "libraries": ["srv:2"]},
    )
    engine = _engine(test_db, client, FakeMetadata({}), cfg, sources=_sources(tmp_path, 10, 20, 30))
    stats = engine.run()
    engine.flush()
    assert stats["applied"] == 3 and stats["by_state"] == {"EVERYTHING": 3, "RECENT": 2, "TV ONLY": 2}
    items = _state_items(test_db)
    film = items["srv|10"]["sig"].split("\n")  # Film (2026), movie library
    assert film[:2] == ["custom:Recent|RECENT||", "custom:Everything|MINE||"] and len([line for line in film if line.startswith("cfg:custom:")]) == 2
    done = items["srv|30"]["sig"].split("\n")  # Done (2010), TV library
    assert [line.split("|")[0] for line in done if not line.startswith("cfg:")] == ["custom:Everything", "custom:TV only"]
    assert all(_has_badge(raw) for _id, raw in client.uploads)
    # Nothing changed: nothing is uploaded again.
    again = _engine(test_db, client, FakeMetadata({}), cfg, sources=_sources(tmp_path, 10, 20, 30))
    assert again.run()["current"] == 3 and len(client.uploads) == 3
    # One overlay's wording changes: only the items it badges are done again.
    cfg["types"]["custom"]["overlays"][3]["options"]["badges"][0]["text"] = "SHOW"
    edited = _engine(test_db, client, FakeMetadata({}), cfg, sources=_sources(tmp_path, 10, 20, 30))
    stats = edited.run()
    assert stats["refreshed"] == 2 and stats["current"] == 1 and [i for i, _raw in client.uploads[3:]] == ["20", "30"]


def test_engine_draws_the_first_match_of_a_group(test_db, tmp_path):
    client = FakeClient()
    cfg = _custom_config(
        {"name": "Netflix", "text": "TOP 10", "group": "top10", "rules": [_rule("year", "gte", 2025)]},
        {"name": "Disney", "text": "TOP 10", "group": "Top10", "rules": [_rule("year", "gte", 2000)]},
        {"name": "Loose", "text": "X", "rules": [_rule("year", "gte", 2000)]},
    )
    engine = _engine(test_db, client, FakeMetadata({}), cfg, sources=_sources(tmp_path, 10, 20, 30))
    engine.run()
    engine.flush()
    names = {key: [line.split("|")[0] for line in entry["sig"].split("\n") if not line.startswith("cfg:")] for key, entry in _state_items(test_db).items()}
    assert names == {"srv|10": ["custom:Netflix", "custom:Loose"], "srv|20": ["custom:Disney", "custom:Loose"], "srv|30": ["custom:Disney", "custom:Loose"]}


def test_engine_draws_the_heaviest_match_of_a_group(test_db, tmp_path):
    client = FakeClient()
    cfg = _custom_config(
        {"name": "Netflix", "text": "NETFLIX", "group": "top10", "weight": 10, "rules": [_rule("year", "gte", 2025)]},
        {"name": "Disney", "text": "DISNEY", "group": "top10", "weight": 60, "rules": [_rule("year", "gte", 2020)]},
        {"name": "Hulu", "text": "HULU", "group": "top10", "weight": 60, "rules": [_rule("year", "gte", 2000)]},  # as heavy as Disney, but after it
        {"name": "Other group", "text": "X", "group": "other", "weight": 1},
    )
    engine = _engine(test_db, client, FakeMetadata({}), cfg, sources=_sources(tmp_path, 10, 20, 30))
    engine.run()
    engine.flush()
    names = {key: [line.split("|")[0] for line in entry["sig"].split("\n") if not line.startswith("cfg:")] for key, entry in _state_items(test_db).items()}
    # Film (2026) matches all three, Live (2020) Disney and Hulu, Done (2010) Hulu alone.
    assert names == {"srv|10": ["custom:Disney", "custom:Other group"], "srv|20": ["custom:Disney", "custom:Other group"], "srv|30": ["custom:Hulu", "custom:Other group"]}
    # Raising a weight changes who wins, and those posters are done again.
    cfg["types"]["custom"]["overlays"][0]["options"]["weight"] = 99
    again = _engine(test_db, client, FakeMetadata({}), cfg, sources=_sources(tmp_path, 10, 20, 30))
    stats = again.run()
    again.flush()
    assert stats["refreshed"] == 1 and [line.split("|")[0] for line in _state_items(test_db)["srv|10"]["sig"].split("\n")][0] == "custom:Netflix"


def test_engine_names_a_badge_after_its_overlay_and_itself(test_db, tmp_path):
    client = FakeClient()
    cfg = _custom_config(
        {"name": "Decade", "badges": [_badge_row("Twenties", 1, text="20s", weight=2, rules=[_rule("year", "gte", 2020)]),
                                      _badge_row("Older", 2, text="OLD", weight=1)]},
        {"name": "Mine", "text": "MINE", "placement": {"vertical_align": "top"}},
    )
    engine = _engine(test_db, client, FakeMetadata({}), cfg, sources=_sources(tmp_path, 10, 20, 30))
    stats = engine.run()
    engine.flush()
    names = {key: [line.split("|")[0] for line in entry["sig"].split("\n") if not line.startswith("cfg:")] for key, entry in _state_items(test_db).items()}
    assert names == {"srv|10": ["custom:Decade: Twenties", "custom:Mine"], "srv|20": ["custom:Decade: Twenties", "custom:Mine"], "srv|30": ["custom:Decade: Older", "custom:Mine"]}
    assert stats["by_state"] == {"DECADE: TWENTIES": 2, "DECADE: OLDER": 1, "MINE": 3}
    # Another badge of the overlay starts to win: only that poster is done again.
    cfg["types"]["custom"]["overlays"][0]["options"]["badges"][1]["weight"] = 5
    again = _engine(test_db, client, FakeMetadata({}), cfg, sources=_sources(tmp_path, 10, 20, 30))
    stats = again.run()
    assert stats["refreshed"] == 2 and stats["current"] == 1  # Done kept its badge: looked at again, nothing uploaded
    again.flush()
    assert all(entry["sig"].startswith("custom:Decade: Older") for entry in _state_items(test_db).values())


def test_engine_asks_the_server_for_labels_and_badges_seasons_by_their_show(test_db, tmp_path):
    client = LookupClient()
    client.carriers[("label", "kids")] = ["20"]
    cfg = _custom_config({"name": "Kids", "text": "KIDS", "apply_to_seasons": True, "rules": [_rule("label", "is", "Kids")]})
    engine = _engine(test_db, client, FakeMetadata({}), cfg, sources=_sources(tmp_path, 10, 20, 30))
    stats = engine.run()
    engine.flush()
    assert set(_state_items(test_db)) == {"srv|20"} and stats["applied"] == 1
    assert stats["no_badge"] == 2 and stats["no_poster"] == 1  # the show's season matches too, but has no poster file here
    assert sorted(a for a in client.asked if a[0] == "label") == [("label", "Kids", "1"), ("label", "Kids", "2")]
    season = client.seasons["2"][0]
    ctx = engine._context("srv", season, client.items["2"][0], client)
    assert ctx.owner_id == "20" and [b.text for _t, b, _p in engine._resolve(ctx)[0]] == ["KIDS"]


def test_engine_leaves_items_alone_when_a_source_does_not_answer(test_db, tmp_path):
    client = LookupClient()
    client.carriers[("label", "kids")] = ["10"]
    cfg = _custom_config({"name": "Kids", "text": "KIDS", "rules": [_rule("label", "is", "kids")]})
    sources = _sources(tmp_path, 10, 20, 30)
    first = _engine(test_db, client, FakeMetadata({}), cfg, sources=sources)
    first.run()
    first.flush()
    assert set(_state_items(test_db)) == {"srv|10"} and len(client.uploads) == 1
    client.down = True
    outage = _engine(test_db, client, FakeMetadata({}), cfg, sources=sources)
    stats = outage.run()
    outage.flush()
    assert stats["unanswered"] == 3 and stats["restored"] == 0 and len(client.uploads) == 1  # the badge stays, nothing is added
    assert set(_state_items(test_db)) == {"srv|10"}
    assert outage.badge_stale("srv", client.items["1"][0], sources[("srv", "10")]) is False
    client.down = False
    client.carriers[("label", "kids")] = []
    back = _engine(test_db, client, FakeMetadata({}), cfg, sources=sources)
    assert back.run()["restored"] == 1  # the label is gone: so is the badge
    back.flush()
    assert _state_items(test_db) == {}


def test_engine_fetches_only_what_the_conditions_read(test_db, tmp_path):
    plain = _engine(test_db, FakeClient(), FakeMetadata({}), _custom_config({"text": "X", "rules": [_rule("year", "gte", 2000)]}))
    assert not plain._needs_tmdb and not plain._needs_media
    client = FakeClient()
    client.media["10"] = [{"resolution": "4k", "hdr": False, "dovi": False, "path": "/m/Film [Remux-2160p].mkv", "audio_titles": []}]
    meta = FakeMetadata({9: {"genres": ["Animation"], "status": "Released"}})
    cfg = _custom_config({"name": "UHD toons", "text": "4K", "rules": [_rule("resolution", "is", "4k"), _rule("tmdb_genre", "is", "Animation")]})
    engine = _engine(test_db, client, meta, cfg, sources=_sources(tmp_path, 10, 20, 30))
    assert engine._needs_tmdb and engine._needs_media and engine.metadata is meta
    engine.run()
    engine.flush()
    assert set(_state_items(test_db)) == {"srv|10"}
    real = _engine(test_db, client, None, cfg)
    assert real.metadata.want_details and not real.metadata.want_keywords


# ── TMDB details for the conditions ──────────────────────────────────────────

class _Resp:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_metadata_keeps_genres_language_and_show_keywords(monkeypatch):
    asked = []

    def fake_get(url, params=None, timeout=None):
        asked.append((url.rsplit("/3", 1)[-1], (params or {}).get("append_to_response")))
        return _Resp({"name": "Show", "status": "Ended", "first_air_date": "2010-01-01", "original_language": "ja",
                      "genres": [{"id": 16, "name": "Animation"}], "keywords": {"results": [{"id": 1, "name": "Anime"}]}})

    plain = MetadataProvider("key")
    monkeypatch.setattr(plain._session, "get", fake_get)
    show = ItemContext(media_type="show", title="Show", year=2010, tmdb_id=1, tvdb_id=None, imdb_id=None, season_number=None, rel_path="srv|1")
    assert "genres" not in plain.for_item(show)["tmdb"] and asked == [("/tv/1", None)]
    rich = MetadataProvider("key", keywords=True, show_keywords=True, details=True)
    rich._cache = plain._cache  # the plain record is cached: it lacks what the conditions read, so it is asked again once
    monkeypatch.setattr(rich._session, "get", fake_get)
    record = rich.for_item(show)["tmdb"]
    assert record["genres"] == ["Animation"] and record["original_language"] == "ja" and record["keywords"] == ["anime"]
    assert asked[-1] == ("/tv/1", "keywords") and len(asked) == 2
    assert rich.for_item(show)["tmdb"] == record and len(asked) == 2
    movies_only = MetadataProvider("key", keywords=True)  # the Credits Scenes badge alone: a show's keywords are not asked for
    assert movies_only._appended("show") == {} and movies_only._appended("movie") == {"keywords": "keywords"}


# ── the API ──────────────────────────────────────────────────────────────────

def test_api_custom_badge_upload_and_preview(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(images, "config_overlays_dir", lambda: tmp_path / "overlays")
    assert client.post("/api/overlays/badges", files={"file": ("x.txt", b"not an image", "text/plain")}).status_code == 400
    assert client.post("/api/overlays/badges", files={"file": ("x.png", b"", "image/png")}).status_code == 400
    up = client.post("/api/overlays/badges", files={"file": ("ribbon.png", _png(), "image/png")}).json()
    assert up["success"] and (up["width"], up["height"]) == (80, 40)
    got = client.get(f"/api/overlays/badges/{up['id']}")
    assert got.status_code == 200 and Image.open(io.BytesIO(got.content)).size == (80, 40)
    assert client.get("/api/overlays/badges/000000000000").status_code == 404 and client.get("/api/overlays/badges/nope").status_code == 404

    row = _badge_row("Ribbon", 1, image=up["id"])
    image = {"type": "custom", "variant": row["id"], "placement": {"horizontal_align": "left", "vertical_align": "top"},
             "options": {"badge": "image", "image_width": 160, "badges": [row]}}
    alone = client.post("/api/overlays/preview", json=image)
    x, y, w, h = (int(v) for v in alone.headers["x-overlay-rect"].split(","))
    assert alone.status_code == 200 and w >= 160 and h > 0
    # The other custom overlays are drawn too, each told apart by the id it was sent with.
    sibling = {"type": "custom", "id": "custom:1", "placement": {}, "options": _options(text="NEW RELEASE")}
    rating = {"type": "rating", "variant": "", "placement": {}, "options": {}}
    both = client.post("/api/overlays/preview", json={**image, "others": [sibling, rating]})
    layers = json.loads(both.headers["x-overlay-layers"])
    assert [(layer["type"], layer["id"]) for layer in layers] == [("custom", "custom:1"), ("rating", "rating")] and all(layer["rect"][2] > 0 for layer in layers)
    assert both.headers["x-overlay-rect"] == alone.headers["x-overlay-rect"]
    # One overlay, several badges: the preview draws the one it is asked for.
    rows = [_badge_row("Netflix", 1, text="NETFLIX"), _badge_row("Disney", 2, text="DISNEY", weight=5)]
    pick = lambda variant: client.post("/api/overlays/preview", json={"type": "custom", "variant": variant, "options": _options(badges=rows)}).headers["x-overlay-text"]  # noqa: E731
    assert (pick(rows[0]["id"]), pick(rows[1]["id"]), pick("")) == ("NETFLIX", "DISNEY", "DISNEY")

    # Saving keeps the images the badges name.
    saved = client.post("/api/overlays/config", json={"enabled": True, "types": {"custom": {"enabled": True, "overlays": [
        {"name": "Ribbon", "options": image["options"]},
        {"name": "New", "libraries": ["srv:1"], "options": _options(text="NEW", rules=[_rule("released", "within", 90)])}]}}}).json()["config"]
    overlays = saved["types"]["custom"]["overlays"]
    assert [o["name"] for o in overlays] == ["Ribbon", "New"] and overlays[0]["options"]["badges"][0]["image"] == up["id"]
    assert overlays[1]["options"]["badges"][0]["rules"] == [_rule("released", "within", 90)] and overlays[1]["all_libraries"] is False
    assert custom_mod.badge_file(up["id"]) is not None
