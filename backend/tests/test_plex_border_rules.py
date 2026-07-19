import json
from pathlib import Path

from PIL import Image

from models.setting import Setting
from services.border_replacer import BorderReplacerService
from services.plex_border_rules import (
    PlexBorderRule,
    PlexRuleMatcher,
    RuleMatch,
    build_plex_matcher,
    folder_match_keys,
    load_plex_rules,
    rules_enabled_for,
    _selected_library_keys,
)


def _create_source_image(path: Path, color=(255, 255, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1000, 1500), color).save(path)


def _corner(path: Path):
    with Image.open(path) as img:
        return img.convert("RGB").getpixel((0, 0))


# ---------------------------------------------------------------- match keys

def test_folder_match_keys_extracts_ids_and_title():
    keys = folder_match_keys("Elf (2003) {tmdb-10719} {imdb-tt0319343}")
    assert "tmdb:10719" in keys
    assert "imdb:tt0319343" in keys
    # year + {..} tokens stripped, title normalized
    assert any(k.startswith("title:") and "elf" in k for k in keys)


def test_folder_match_keys_empty():
    assert folder_match_keys("") == set()


def test_folder_match_keys_tolerates_spaced_tokens():
    # Uses the shared POSTER_ID_PATTERN, which also matches spaced tokens like "{tvdb - 456}".
    keys = folder_match_keys("Some Show (2021) {tvdb - 456}")
    assert "tvdb:456" in keys


# ---------------------------------------------------------------- parsing

def test_load_plex_rules_parses_valid_and_drops_invalid(test_db):
    test_db.add(Setting(key="border_replacer_plex_rules", value=json.dumps([
        {"name": "Xmas", "match": "label", "value": "Christmas", "mode": "include", "colors": ["#ff0000"]},
        {"match": "genre", "value": "Horror", "mode": "except"},
        {"match": "collection", "value": "Marvel", "mode": "skip"},
        {"match": "bogus", "value": "x", "mode": "include"},   # bad match -> dropped
        {"match": "label", "value": "", "mode": "include"},      # empty value -> dropped
        {"match": "label", "value": "x", "mode": "nope"},        # bad mode -> dropped
        "not a dict",                                            # -> dropped
    ])))
    test_db.commit()

    rules = load_plex_rules(test_db)
    assert [r.match for r in rules] == ["label", "genre", "collection"]
    assert rules[0].name == "Xmas"
    assert rules[0].colors == ["#ff0000"]
    # a rule without an explicit name gets a derived one
    assert rules[1].name == "genre:Horror"
    assert rules[1].mode == "except"
    assert rules[2].mode == "skip"


def test_load_plex_rules_invalid_json_returns_empty(test_db):
    test_db.add(Setting(key="border_replacer_plex_rules", value="{not json"))
    test_db.commit()
    assert load_plex_rules(test_db) == []


# ---------------------------------------------------------------- gating / scope

def test_rules_enabled_for_run_type(test_db):
    test_db.add(Setting(key="border_replacer_rule_run_types", value="manual, webhook"))
    test_db.commit()
    assert rules_enabled_for(test_db, "manual") is True
    assert rules_enabled_for(test_db, "webhook") is True
    assert rules_enabled_for(test_db, "autorun") is False


def test_rules_enabled_for_empty_is_false(test_db):
    assert rules_enabled_for(test_db, "manual") is False


def test_selected_library_keys(test_db):
    test_db.add(Setting(key="border_replacer_rule_libraries", value=json.dumps(["Main:1", "Main:2"])))
    test_db.commit()
    assert _selected_library_keys(test_db) == {"Main:1", "Main:2"}


def test_selected_library_keys_empty_when_unset(test_db):
    # Unset = scan nothing (only checked libraries run).
    assert _selected_library_keys(test_db) == set()


# ---------------------------------------------------------------- resolve / precedence

def _rule(name, mode, keys):
    return PlexBorderRule(
        name=name, match="label", value=name, mode=mode,
        colors=["#ff0000"], style_opts={"style": "solid"}, match_keys=set(keys),
    )


def test_resolve_include_matches():
    m = PlexRuleMatcher([_rule("xmas", "include", folder_match_keys("Elf (2003)"))])
    res = m.resolve("Elf (2003)")
    assert isinstance(res, RuleMatch) and res.skip is False and res.name == "xmas"


def test_resolve_include_no_match_returns_none():
    m = PlexRuleMatcher([_rule("xmas", "include", folder_match_keys("Die Hard (1988)"))])
    assert m.resolve("Elf (2003)") is None


def test_resolve_except_applies_to_non_matching():
    m = PlexRuleMatcher([_rule("rest", "except", folder_match_keys("Elf (2003)"))])
    # non-matching item -> gets the rule
    assert m.resolve("Die Hard (1988)").name == "rest"
    # matching item -> except rule does NOT apply, falls through to None
    assert m.resolve("Elf (2003)") is None


def test_resolve_skip_leaves_untouched():
    m = PlexRuleMatcher([_rule("leave", "skip", folder_match_keys("Elf (2003)"))])
    res = m.resolve("Elf (2003)")
    assert res is not None and res.skip is True and res.colors == []


def test_resolve_first_matching_rule_wins():
    r1 = _rule("first", "skip", folder_match_keys("Elf (2003)"))
    r2 = _rule("second", "include", folder_match_keys("Elf (2003)"))
    assert PlexRuleMatcher([r1, r2]).resolve("Elf (2003)").skip is True
    assert PlexRuleMatcher([r2, r1]).resolve("Elf (2003)").name == "second"


def test_resolve_same_title_different_year_does_not_collide():
    """Regression: labeling the 1989 movie "The 'Burbs" must NOT also match the unrelated
    2026 show of the same title — they share a normalized title but differ by year/IDs."""
    # Keys as they'd come from the matched 1989 movie's folder (tmdb id + year-scoped title).
    burbs_movie = _rule("thanks", "include", folder_match_keys("The 'Burbs (1989) {tmdb-12345}"))
    m = PlexRuleMatcher([burbs_movie])
    assert m.resolve("The 'Burbs (1989) {tmdb-12345}") is not None   # the labeled movie
    assert m.resolve("The 'Burbs (2026) {tvdb-99999}") is None       # different item, same title
    assert m.resolve("The 'Burbs (2026)") is None                    # even without an id token


def test_resolve_movie_and_show_same_title_and_year_do_not_collide():
    """Media-type gate (mirrors _asset_matches_target): a movie rule must not bleed onto a
    show even when title AND year are identical — the tvdb token marks the show and its
    tmdb id is never trusted as a movie."""
    m = PlexRuleMatcher([_rule("r", "include", folder_match_keys("Foo (2020) {tmdb-1}"))])
    assert m.resolve("Foo (2020) {tmdb-1}") is not None    # the movie
    assert m.resolve("Foo (2020) {tvdb-2}") is None        # the show, same title + year


# ---------------------------------------------------------------- Plex query (mocked)

class _FakeGuid:
    def __init__(self, gid):
        self.id = gid


class _FakeItem:
    def __init__(self, title, guids=None, year=None):
        self.title = title
        self.year = year
        self.guids = [_FakeGuid(g) for g in (guids or [])]


class _FakeCollection:
    def __init__(self, title, items):
        self.title = title
        self.type = "collection"
        self.year = None
        self.guids = []
        self._items = items

    def items(self):
        return self._items


class _FakeSection:
    def __init__(self, key, type_, results, collections=None, collection_results=None):
        self.key = key
        self.type = type_
        self.title = f"Section {key}"
        self._results = results
        self._collections = collections or []
        # (field, value) -> [collections] carrying that label/genre (server-side filter).
        self._collection_results = collection_results or {}

    def search(self, **kwargs):
        if kwargs.get("libtype") == "collection":
            rest = {k: v for k, v in kwargs.items() if k != "libtype"}
            if not rest:
                return self._collections  # title-match path enumerates every collection
            (field, value), = rest.items()
            return self._collection_results.get((field, value), [])
        # label=... / genre=... filter
        (field, value), = kwargs.items()
        return self._results.get((field, value), [])


class _FakeLibrary:
    def __init__(self, sections):
        self._sections = sections

    def sections(self):
        return self._sections


class _FakePlexServer:
    instances = {}

    def __init__(self, url, token):
        self.library = _FakeLibrary(_FakePlexServer.instances[url])


def _patch_plex(monkeypatch, sections_by_url):
    _FakePlexServer.instances = sections_by_url
    monkeypatch.setattr("plexapi.server.PlexServer", _FakePlexServer)


def test_build_plex_matcher_queries_labels(test_db, monkeypatch):
    xmas_movie = _FakeItem("Elf", year=2003, guids=["tmdb://10719"])
    section = _FakeSection("1", "movie", results={("label", "Christmas"): [xmas_movie]})
    _patch_plex(monkeypatch, {"http://plex": [section]})

    test_db.add(Setting(key="plex_instances", value=json.dumps([
        {"name": "Main", "url": "http://plex", "api_key": "token"},
    ])))
    test_db.add(Setting(key="border_replacer_rule_run_types", value="manual"))
    test_db.add(Setting(key="border_replacer_rule_libraries", value=json.dumps(["Main:1"])))
    test_db.add(Setting(key="border_replacer_plex_rules", value=json.dumps([
        {"name": "Xmas", "match": "label", "value": "Christmas", "mode": "include", "colors": ["#ff0000"]},
    ])))
    test_db.commit()

    matcher = build_plex_matcher(test_db, "manual")
    assert matcher is not None
    # queried item is matchable by tmdb id and by title
    assert matcher.resolve("Elf (2003) {tmdb-10719}") is not None
    assert matcher.resolve("Elf (2003)") is not None
    assert matcher.resolve("Die Hard (1988)") is None


def test_build_plex_matcher_collection(test_db, monkeypatch):
    m1 = _FakeItem("Iron Man", year=2008)
    marvel = _FakeCollection("Marvel", [m1])
    section = _FakeSection("1", "movie", results={}, collections=[marvel])
    _patch_plex(monkeypatch, {"http://plex": [section]})

    test_db.add(Setting(key="plex_instances", value=json.dumps([
        {"name": "Main", "url": "http://plex", "api_key": "token"},
    ])))
    test_db.add(Setting(key="border_replacer_rule_run_types", value="manual"))
    test_db.add(Setting(key="border_replacer_rule_libraries", value=json.dumps(["Main:1"])))
    test_db.add(Setting(key="border_replacer_plex_rules", value=json.dumps([
        {"match": "collection", "value": "Marvel", "mode": "include", "colors": ["#00ff00"]},
    ])))
    test_db.commit()

    matcher = build_plex_matcher(test_db, "manual")
    assert matcher.resolve("Iron Man (2008)") is not None


def test_item_match_keys_collection_is_yearless_movie():
    """A collection keys as a yearless movie regardless of its library, matching the
    token-less collection poster folder that folder_match_keys infers."""
    from services.plex_border_rules import _item_match_keys

    tv_coll = _FakeCollection("Disney+ Top 10", [])
    # is_movie=False (a TV library) but the collection still keys as a movie.
    keys = _item_match_keys(tv_coll, is_movie=False)
    assert keys == folder_match_keys("Disney+ Top 10") == {"title:disneytop10::movie"}


def test_build_plex_matcher_label_on_collection_borders_collection_poster(test_db, monkeypatch):
    """A label put on a COLLECTION borders the collection's own poster, not its member items.
    Plex keeps collection labels separate from item labels, so the item search returns nothing
    while the collection search finds it. Covers a TV-library collection too, whose yearless,
    tvdb-less poster folder is inferred as a movie."""
    member = _FakeItem("Iron Man", year=2008, guids=["tmdb://1726"])
    movie_coll = _FakeCollection("The 80s", [member])
    tv_coll = _FakeCollection("Disney+ Top 10", [])
    movie_section = _FakeSection(
        "1", "movie",
        results={},  # NO item carries the label — only the collection does
        collection_results={("label", "Test"): [movie_coll]},
    )
    tv_section = _FakeSection(
        "2", "show",
        results={},
        collection_results={("label", "Test"): [tv_coll]},
    )
    _patch_plex(monkeypatch, {"http://plex": [movie_section, tv_section]})

    test_db.add(Setting(key="plex_instances", value=json.dumps([
        {"name": "Main", "url": "http://plex", "api_key": "token"},
    ])))
    test_db.add(Setting(key="border_replacer_rule_run_types", value="manual"))
    test_db.add(Setting(key="border_replacer_rule_libraries", value=json.dumps(["Main:1", "Main:2"])))
    test_db.add(Setting(key="border_replacer_plex_rules", value=json.dumps([
        {"name": "Test", "match": "label", "value": "Test", "mode": "include", "colors": ["#d22d2d"]},
    ])))
    test_db.commit()

    matcher = build_plex_matcher(test_db, "manual")
    assert matcher is not None
    # Both collection posters match (the TV collection keyed as a yearless movie).
    assert matcher.resolve("The 80s") is not None
    assert matcher.resolve("Disney+ Top 10") is not None
    # The member item is NOT bordered — it does not carry the label itself.
    assert matcher.resolve("Iron Man (2008) {tmdb-1726}") is None


def test_build_plex_matcher_no_libraries_selected_returns_none(test_db, monkeypatch):
    section = _FakeSection("1", "movie", results={("label", "Christmas"): [_FakeItem("Elf")]})
    _patch_plex(monkeypatch, {"http://plex": [section]})

    test_db.add(Setting(key="plex_instances", value=json.dumps([
        {"name": "Main", "url": "http://plex", "api_key": "token"},
    ])))
    test_db.add(Setting(key="border_replacer_rule_run_types", value="manual"))
    # No border_replacer_rule_libraries set → nothing checked → rules do not run.
    test_db.add(Setting(key="border_replacer_plex_rules", value=json.dumps([
        {"match": "label", "value": "Christmas", "mode": "include", "colors": ["#ff0000"]},
    ])))
    test_db.commit()

    assert build_plex_matcher(test_db, "manual") is None


def test_build_plex_matcher_disabled_returns_none(test_db, monkeypatch):
    _patch_plex(monkeypatch, {"http://plex": []})
    test_db.add(Setting(key="border_replacer_plex_rules", value=json.dumps([
        {"match": "label", "value": "X", "mode": "include"},
    ])))
    # run types not enabled
    test_db.commit()
    assert build_plex_matcher(test_db, "manual") is None


def test_build_plex_matcher_respects_library_scope(test_db, monkeypatch):
    item = _FakeItem("Elf", year=2003)
    s1 = _FakeSection("1", "movie", results={("label", "Christmas"): [item]})
    s2 = _FakeSection("2", "movie", results={("label", "Christmas"): [item]})
    _patch_plex(monkeypatch, {"http://plex": [s1, s2]})

    test_db.add(Setting(key="plex_instances", value=json.dumps([
        {"name": "Main", "url": "http://plex", "api_key": "token"},
    ])))
    test_db.add(Setting(key="border_replacer_rule_run_types", value="manual"))
    # only scan section 2 (which we can distinguish by giving s1 a different item)
    s1._results = {("label", "Christmas"): [_FakeItem("Only In One", year=2000)]}
    test_db.add(Setting(key="border_replacer_rule_libraries", value=json.dumps(["Main:2"])))
    test_db.add(Setting(key="border_replacer_plex_rules", value=json.dumps([
        {"match": "label", "value": "Christmas", "mode": "include"},
    ])))
    test_db.commit()

    matcher = build_plex_matcher(test_db, "manual")
    # section 2's item matched, section 1's did not (out of scope)
    assert matcher.resolve("Elf (2003)") is not None
    assert matcher.resolve("Only In One (2000)") is None


def _add_rule_settings(test_db, mode="except"):
    test_db.add(Setting(key="plex_instances", value=json.dumps([
        {"name": "Main", "url": "http://plex", "api_key": "token"},
    ])))
    test_db.add(Setting(key="border_replacer_rule_run_types", value="manual"))
    test_db.add(Setting(key="border_replacer_rule_libraries", value=json.dumps(["Main:1"])))
    test_db.add(Setting(key="border_replacer_plex_rules", value=json.dumps([
        {"match": "label", "value": "Christmas", "mode": mode, "colors": ["#ff0000"]},
    ])))
    test_db.commit()


def test_build_plex_matcher_unreachable_plex_returns_none(test_db, monkeypatch):
    """Plex unreachable → no matcher (fail closed). Otherwise an empty match set would make
    an 'except' rule paint the whole library, and per-item tracking would be wiped."""
    class _BoomServer:
        def __init__(self, url, token):
            raise ConnectionError("boom")
    monkeypatch.setattr("plexapi.server.PlexServer", _BoomServer)
    _add_rule_settings(test_db, mode="except")
    assert build_plex_matcher(test_db, "manual") is None


def test_build_plex_matcher_query_error_with_except_rule_returns_none(test_db, monkeypatch):
    """A per-rule query failure with an 'except' rule fails closed — the incomplete match set
    would otherwise make 'except' match too many items."""
    class _BoomSection(_FakeSection):
        def search(self, **kwargs):
            raise RuntimeError("query boom")
    _patch_plex(monkeypatch, {"http://plex": [_BoomSection("1", "movie", results={})]})
    _add_rule_settings(test_db, mode="except")
    assert build_plex_matcher(test_db, "manual") is None


def test_build_plex_matcher_query_error_include_rule_still_runs(test_db, monkeypatch):
    """A per-rule query failure with only include/skip rules degrades safely: still returns a
    matcher (the item just misses its border until the next clean run)."""
    class _BoomSection(_FakeSection):
        def search(self, **kwargs):
            raise RuntimeError("query boom")
    _patch_plex(monkeypatch, {"http://plex": [_BoomSection("1", "movie", results={})]})
    _add_rule_settings(test_db, mode="include")
    assert build_plex_matcher(test_db, "manual") is not None


# ---------------------------------------------------------------- integration with process_posters

def test_process_posters_applies_rule_to_matching_item(test_db, tmp_path):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    _create_source_image(source_dir / "Elf (2003)" / "poster.png", (255, 255, 255))
    _create_source_image(source_dir / "Die Hard (1988)" / "poster.png", (255, 255, 255))

    matcher = PlexRuleMatcher([PlexBorderRule(
        name="xmas", match="label", value="Christmas", mode="include",
        colors=["#ff0000"], style_opts={"style": "solid"}, match_keys=folder_match_keys("Elf (2003)"),
    )])

    service = BorderReplacerService(test_db)
    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=None,          # no main style -> non-matching items pass through
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="full",
        plex_matcher=matcher,
    )

    assert result["success"] is True
    # matching item got a red border
    assert _corner(destination_dir / "Elf (2003)" / "poster.png") == (255, 0, 0)
    # non-matching item left as-is (still white)
    assert _corner(destination_dir / "Die Hard (1988)" / "poster.png") == (255, 255, 255)


def test_process_posters_skip_rule_leaves_untouched(test_db, tmp_path):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    _create_source_image(source_dir / "Elf (2003)" / "poster.png", (10, 20, 30))

    matcher = PlexRuleMatcher([PlexBorderRule(
        name="leave", match="label", value="NoBorder", mode="skip",
        colors=[], style_opts={}, match_keys=folder_match_keys("Elf (2003)"),
    )])

    service = BorderReplacerService(test_db)
    service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=["#0000ff"],   # main style set, but skip rule wins
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="full",
        plex_matcher=matcher,
    )

    # left completely untouched despite a main border being configured
    assert _corner(destination_dir / "Elf (2003)" / "poster.png") == (10, 20, 30)


def test_process_posters_remove_style_strips_border(test_db, tmp_path):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    # Green 26px border around a blue interior.
    bordered = Image.new("RGB", (1000, 1500), (0, 255, 0))
    bordered.paste(Image.new("RGB", (1000 - 52, 1500 - 52), (0, 0, 255)), (26, 26))
    (source_dir / "Elf (2003)").mkdir(parents=True)
    bordered.save(source_dir / "Elf (2003)" / "poster.png")

    matcher = PlexRuleMatcher([PlexBorderRule(
        name="strip", match="label", value="NoBorder", mode="include",
        colors=[], style_opts={"style": "remove"}, match_keys=folder_match_keys("Elf (2003)"),
    )])

    service = BorderReplacerService(test_db)
    service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=None,
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="full",
        plex_matcher=matcher,
    )

    # The green border is stripped: the top-left corner is now the blue interior.
    assert _corner(destination_dir / "Elf (2003)" / "poster.png") == (0, 0, 255)


# ---------------------------------------------------------------- incremental per-item

def _inc_rule(colors, keys):
    return PlexBorderRule(
        name="r", match="label", value="X", mode="include",
        colors=colors, style_opts={"style": "solid"}, match_keys=set(keys),
    )


def test_incremental_reprocesses_only_rule_changed_items(test_db, tmp_path):
    """The core fix: Plex-side churn reprocesses ONLY the items whose applicable rule
    changed — not every poster."""
    from services.plex_border_rules import folder_match_keys

    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    for title in ("Elf (2003)", "Grinch (2000)", "Die Hard (1988)"):
        _create_source_image(source_dir / title / "poster.png", (255, 255, 255))

    service = BorderReplacerService(test_db)

    def run(matcher):
        return service.process_posters(
            source_dir=str(source_dir),
            destination_dir=str(destination_dir),
            border_colors=None,           # non-matched items pass through unchanged
            border_width=26,
            exclusion_list=[],
            dry_run=False,
            mode="incremental",
            plex_matcher=matcher,
        )

    # Run 1: a rule paints Elf red; the other two pass through white.
    elf_rule = PlexRuleMatcher([_inc_rule(["#ff0000"], folder_match_keys("Elf (2003)"))])
    r1 = run(elf_rule)
    assert r1["success"] is True
    assert _corner(destination_dir / "Elf (2003)" / "poster.png") == (255, 0, 0)
    assert _corner(destination_dir / "Grinch (2000)" / "poster.png") == (255, 255, 255)

    # Run 2: identical matcher, nothing touched → nothing reprocessed (the bug fix).
    r2 = run(elf_rule)
    assert r2["changed"] == 0

    # Run 3: the rule now matches Die Hard instead (Plex churn). ONLY Elf (reverts) and
    # Die Hard (gains red) reprocess; Grinch is never touched.
    dh_rule = PlexRuleMatcher([_inc_rule(["#ff0000"], folder_match_keys("Die Hard (1988)"))])
    r3 = run(dh_rule)
    assert r3["changed"] == 2
    assert _corner(destination_dir / "Elf (2003)" / "poster.png") == (255, 255, 255)      # reverted
    assert _corner(destination_dir / "Die Hard (1988)" / "poster.png") == (255, 0, 0)     # now painted


def test_incremental_stable_when_matched_set_grows(test_db, tmp_path):
    """Adding a match key to an unrelated item does not reprocess already-correct items."""
    from services.plex_border_rules import folder_match_keys

    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    _create_source_image(source_dir / "Elf (2003)" / "poster.png", (255, 255, 255))

    service = BorderReplacerService(test_db)
    matcher = PlexRuleMatcher([_inc_rule(["#00ff00"], folder_match_keys("Elf (2003)"))])
    common = dict(
        source_dir=str(source_dir), destination_dir=str(destination_dir),
        border_colors=None, border_width=26, exclusion_list=[], dry_run=False,
        mode="incremental",
    )
    service.process_posters(plex_matcher=matcher, **common)

    # Kometa labels some OTHER movie (not in this folder set) — matcher gains a key.
    matcher.rules[0].match_keys.add("title:someothermovie")
    r2 = service.process_posters(plex_matcher=matcher, **common)
    assert r2["changed"] == 0  # Elf's own rule is unchanged, so it is not reprocessed


def test_incremental_run_without_rules_preserves_rule_styled_items(test_db, tmp_path):
    """A run where rules aren't evaluated (matcher=None: disabled run type, no libraries, or
    Plex unreachable) must NOT revert rule-styled items or churn their tracking — otherwise
    every scheduled/webhook run would flip rule posters back to default and the next rules
    run would flip them forward again (ping-pong)."""
    from services.plex_border_rules import folder_match_keys

    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    for title in ("Elf (2003)", "Die Hard (1988)"):
        _create_source_image(source_dir / title / "poster.png", (255, 255, 255))

    service = BorderReplacerService(test_db)

    def run(matcher):
        return service.process_posters(
            source_dir=str(source_dir),
            destination_dir=str(destination_dir),
            border_colors=None,
            border_width=26,
            exclusion_list=[],
            dry_run=False,
            mode="incremental",
            plex_matcher=matcher,
        )

    # Run 1: rules active, Elf painted red.
    elf_rule = PlexRuleMatcher([_inc_rule(["#ff0000"], folder_match_keys("Elf (2003)"))])
    run(elf_rule)
    assert _corner(destination_dir / "Elf (2003)" / "poster.png") == (255, 0, 0)

    # Run 2: rules NOT evaluated this run (matcher is None). Nothing reprocesses; Elf stays red.
    r2 = run(None)
    assert r2["changed"] == 0
    assert _corner(destination_dir / "Elf (2003)" / "poster.png") == (255, 0, 0)

    # Run 3: rules active again with the SAME matcher. Run 2 preserved Elf's stored sig, so
    # there is no ping-pong back — nothing reprocesses.
    r3 = run(elf_rule)
    assert r3["changed"] == 0
    assert _corner(destination_dir / "Elf (2003)" / "poster.png") == (255, 0, 0)


def test_incremental_non_rules_reprocess_clears_then_restores_rule(test_db, tmp_path):
    """If a non-rules run (matcher=None) reprocesses a rule-styled item because its SOURCE
    changed, it bakes a non-rule border and its stored sig clears — so the NEXT rules run
    detects the mismatch and restores the rule border (no stale sig left behind)."""
    import os
    from services.plex_border_rules import folder_match_keys

    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    poster = source_dir / "Elf (2003)" / "poster.png"
    _create_source_image(poster, (255, 255, 255))

    service = BorderReplacerService(test_db)
    common = dict(
        source_dir=str(source_dir), destination_dir=str(destination_dir),
        border_colors=None, border_width=26, exclusion_list=[], dry_run=False,
        mode="incremental",
    )
    elf_rule = PlexRuleMatcher([_inc_rule(["#ff0000"], folder_match_keys("Elf (2003)"))])

    # Run 1: rule paints Elf red.
    service.process_posters(plex_matcher=elf_rule, **common)
    assert _corner(destination_dir / "Elf (2003)" / "poster.png") == (255, 0, 0)

    # Source changes; a non-rules run reprocesses it → passes through (white), sig cleared.
    os.utime(poster, (2_000_000_000, 2_000_000_000))
    service.process_posters(plex_matcher=None, **common)
    assert _corner(destination_dir / "Elf (2003)" / "poster.png") == (255, 255, 255)

    # A rules run now restores the rule border because the cleared sig no longer matches.
    r3 = service.process_posters(plex_matcher=elf_rule, **common)
    assert r3["changed"] == 1
    assert _corner(destination_dir / "Elf (2003)" / "poster.png") == (255, 0, 0)
