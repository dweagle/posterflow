import hashlib
import io
import json
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from PIL import Image

from models.overlay import OverlayItem
from models.setting import Setting
from services.overlays import server as server_mod
from services.overlays.base import DEFAULT_PLACEMENT, ItemContext, OverlayBadge, normalize_placement
from services.overlays.config import normalize_config, overlay_signature
from services.overlays.metadata import MetadataProvider
from services.overlays.registry import all_types, get_type
from services.overlays.render import measure_badge, parse_color, render_badge
from services.overlays.server import ServerOverlayEngine, build_server_engine
from services.overlays.types.status import format_date
from services.plex_bundles import normalize_plex_metadata_paths, plex_bundle_dir, plex_uploads_dir, save_plex_metadata_paths
from util.media_server.plex import NO_IMAGE_SELECTED, bundle_identity
from util.media_server.types import MediaServerItem

TODAY = date.today()


def _iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def _show_meta(**tmdb):
    return {"tmdb": tmdb}


def _ctx(kind="show", title="Show", year=2010, tmdb=1, tvdb=1, imdb=None, season=None):
    return ItemContext(media_type=kind, title=title, year=year, tmdb_id=tmdb, tvdb_id=tvdb, imdb_id=imdb,
                       season_number=season, rel_path=f"srv|{title}")


@pytest.fixture
def isolated_overlay_files(tmp_path):
    return tmp_path


class FakeMetadata:
    def begin_run(self, *, refresh: bool = False) -> None:  # the real provider sets its cache aside on a rebuild
        self.refresh = refresh

    """MetadataProvider stand-in keyed by tmdb/tvdb id."""

    def __init__(self, by_id, available=True):
        self.by_id = by_id
        self.calls = 0
        self.available = available

    def for_item(self, ctx):
        self.calls += 1
        if not self.available:
            return {}
        for key in (ctx.tmdb_id, ctx.tvdb_id):
            if key in self.by_id:
                return {"tmdb": self.by_id[key]}
        return {}

    def flush(self):
        pass


# ── registry / status resolution ─────────────────────────────────────────────

def test_registry_discovers_status_type():
    assert "status" in all_types()
    desc = get_type("status").describe()
    assert desc["key"] == "status"
    assert any(f["kind"] == "states" for f in desc["schema"])
    assert {v["key"] for v in desc["variants"]} >= {"airing", "airing:date", "ended"}
    assert desc["hidden_placement"] == ["back_color"]
    states = next(f for f in desc["schema"] if f["kind"] == "states")["items"]
    assert {i["key"]: i["disabled_fields"] for i in states}["ended"] == ["text_with_date"]
    assert {i["key"]: i["disabled_fields"] for i in states}["airing"] == []


@pytest.fixture
def status():
    return get_type("status")


@pytest.fixture
def show_ctx():
    return _ctx()


def test_status_priorities(status, show_ctx):
    opts = status.default_options()
    r = lambda **k: status.resolve(show_ctx, opts, _show_meta(**k))  # noqa: E731
    assert r(status="Planned", first_air_date=_iso(10)).text == f"PREMIERES {format_date(TODAY + timedelta(days=10), 'MM/DD', True)}"  # leading zeros dropped by default
    assert r(status="Returning Series", first_air_date=_iso(-5)).key == "new"
    assert r(status="Ended", first_air_date=_iso(-5)).key == "new"
    assert r(status="Canceled", first_air_date="2010-01-01").text == "CANCELED"
    assert r(status="Ended", first_air_date="2010-01-01").text == "ENDED"
    airing = r(status="Returning Series", first_air_date="2010-01-01",
               last_episode_to_air={"air_date": _iso(-3)}, next_episode_to_air={"air_date": _iso(4)})
    assert airing.key == "airing" and airing.text.startswith("AIRING ")
    assert r(status="Returning Series", first_air_date="2010-01-01",
             next_episode_to_air={"air_date": _iso(0)}).text == "AIRING TODAY"
    returning = r(status="Returning Series", first_air_date="2010-01-01",
                  last_episode_to_air={"air_date": _iso(-100)}, next_episode_to_air={"air_date": _iso(20)})
    assert returning.text.startswith("RETURNS ")
    assert r(status="Returning Series", first_air_date="2010-01-01",
             last_episode_to_air={"air_date": _iso(-100)}).text == "RETURNING"
    assert r(status="Returning Series", first_air_date="2010-01-01", last_air_date=_iso(-2)).key == "airing"


def test_status_movies_and_seasons(status, show_ctx):
    opts = status.default_options()
    movie = _ctx("movie", "Film", 2026, tmdb=9, tvdb=None)
    assert status.resolve(movie, opts, _show_meta(status="Released", release_date=_iso(-3))).text == "NEW RELEASE"
    assert status.resolve(movie, opts, _show_meta(status="Released", release_date="2010-01-01")) is None
    season = _ctx(season=2)
    meta = _show_meta(status="Ended", first_air_date="2010-01-01")
    assert status.resolve(season, opts, meta) is None
    opts["apply_to_seasons"] = True
    assert status.resolve(season, opts, meta).text == "ENDED"
    assert status.resolve(show_ctx, opts, {}) is None


def test_status_options_normalize_and_disable(status, show_ctx):
    opts = status.normalize_options({
        "new_days": "9999", "date_format": "bogus", "show_dates": "false",
        "states": {"ended": {"enabled": False, "text": "  DONE  "}, "airing": {"text": "LIVE"}},
    })
    assert opts["new_days"] == 365 and opts["date_format"] == "MM/DD" and opts["show_dates"] is False
    assert opts["states"]["ended"]["enabled"] is False and opts["states"]["ended"]["text"] == "DONE"
    assert status.resolve(show_ctx, opts, _show_meta(status="Ended", first_air_date="2010-01-01")) is None
    airing = status.resolve(show_ctx, opts, _show_meta(status="Returning Series", first_air_date="2010-01-01",
                                                       last_episode_to_air={"air_date": _iso(-1)},
                                                       next_episode_to_air={"air_date": _iso(6)}))
    assert airing.text == "LIVE"
    # Switched-off states stay listed, tagged with their row, so the page can drop them from the options it edits.
    assert [(v["group"], v["item"]) for v in status.variants(opts) if v["key"].startswith("ended")] == [("states", "ended")]


def test_status_variants_only_list_reachable_states(status):
    opts = status.default_options()
    keys = [v["key"] for v in status.variants(opts)]
    assert "upcoming:date" in keys and "upcoming" not in keys
    assert "new" in keys and "new:date" in keys and "airing" in keys and "airing:date" in keys
    opts["show_dates"] = False
    keys = [v["key"] for v in status.variants(opts)]
    assert "upcoming" in keys and not any(k.endswith(":date") for k in keys)


# ── rating type ──────────────────────────────────────────────────────────────

def test_rating_type_describe_and_variants():
    rating = get_type("rating")
    desc = rating.describe()
    assert desc["key"] == "rating" and desc["needs"] == [] and desc["hidden_placement"] == ["back_color"]
    tiers = next(f for f in desc["schema"] if f["kind"] == "states")
    assert tiers["key"] == "tiers" and [i["key"] for i in tiers["items"]] == ["top", "high", "mid", "low"]
    assert next(f for f in tiers["items"][0]["fields"] if f["key"] == "min")["step"] == 0.1
    assert desc["default_placement"]["horizontal_align"] == "right" and desc["default_placement"]["vertical_align"] == "bottom"
    opts = rating.default_options()
    assert [v["label"] for v in rating.variants(opts)] == ["Top (9.2)", "High (8.1)", "Mid (6.8)", "Low (4.3)"]
    assert all(v["group"] == "tiers" and v["item"] == v["key"] for v in rating.variants(opts))  # the page hides a tier that is off
    opts["decimals"] = 0
    assert [v["label"] for v in rating.variants(opts)] == ["Top (9)", "High (8)", "Mid (7)", "Low (4)"]
    assert rating.resolve_variant("top", opts).text == "9" and rating.resolve_variant("top", opts).back_color == "#0B6E0BFF"
    # TMDB is only needed when it is the source or the fallback.
    assert rating.needs_for({**rating.default_options(), "fallback_tmdb": False}) == ()  # the default falls back to TMDB
    assert rating.needs_for({**rating.default_options(), "source": "tmdb"}) == ("tmdb",)
    assert rating.needs_for({**rating.default_options(), "fallback_tmdb": True}) == ("tmdb",)


def test_rating_resolves_server_ratings_tmdb_and_tiers():
    rating = get_type("rating")
    opts = rating.default_options()
    ctx = _ctx(kind="movie", title="Film", tvdb=None)
    ctx.ratings = {"audience": 8.4, "critic": 6.1, "user": 9.3}
    badge = rating.resolve(ctx, opts, {})
    assert (badge.key, badge.text, badge.back_color) == ("high", "8.4", "#5CB85CFF")
    assert rating.resolve(ctx, {**opts, "source": "server_critic"}, {}).key == "mid"
    assert rating.resolve(ctx, {**opts, "source": "server_user"}, {}).key == "top"
    assert rating.resolve(ctx, {**opts, "source": "tmdb"}, {"tmdb": {"vote_average": 5.2}}).text == "5.2"
    assert rating.resolve(ctx, {**opts, "source": "tmdb"}, {"tmdb": {"vote_average": 0}}) is None  # no votes yet
    # Missing server rating: nothing, unless TMDB is the fallback.
    bare = _ctx(kind="movie", title="Bare", tvdb=None)
    assert rating.resolve(bare, {**opts, "fallback_tmdb": False}, {"tmdb": {"vote_average": 7.7}}) is None
    fallback = rating.resolve(bare, {**opts, "fallback_tmdb": True}, {"tmdb": {"vote_average": 7.7}})
    assert fallback.text == "7.7" and fallback.key == "high"
    # Hide below, decimals, disabled tier, seasons.
    assert rating.resolve(ctx, {**opts, "source": "server_critic", "hide_below": 7.0}, {}) is None
    assert rating.resolve(ctx, {**opts, "decimals": 2}, {}).text == "8.40"
    no_high = {**opts, "tiers": {**opts["tiers"], "high": {**opts["tiers"]["high"], "enabled": False}}}
    assert rating.resolve(ctx, no_high, {}).key == "mid"  # falls through to the next tier down
    season = _ctx(title="Show", season=1)
    season.ratings = {"audience": 8.0}
    assert rating.resolve(season, opts, {}) is None
    assert rating.resolve(season, {**opts, "apply_to_seasons": True}, {}).text == "8.0"


def test_rating_options_normalize():
    rating = get_type("rating")
    p = rating.normalize_options({"source": "letterboxd", "decimals": 9, "hide_below": "6.62", "fallback_tmdb": "true",
                                  "tiers": {"top": {"min": "9.5", "back_color": "#123456"}, "low": {"min": 42}}})
    assert p["source"] == "server_audience" and p["decimals"] == 2 and p["hide_below"] == 6.6 and p["fallback_tmdb"] is True
    assert p["tiers"]["top"]["min"] == 9.5 and p["tiers"]["top"]["back_color"] == "#123456"
    assert p["tiers"]["low"]["min"] == 0.0  # out of range → default
    assert p["tiers"]["high"]["min"] == 7.5


def test_items_carry_the_servers_ratings_into_the_context():
    from types import SimpleNamespace

    from util.media_server.jellyfin import _ratings as jellyfin_ratings
    from util.media_server.plex import _ratings as plex_ratings

    assert plex_ratings(SimpleNamespace(audienceRating=8.4, rating=None, userRating="7")) == {"audience": 8.4, "user": 7.0}
    assert jellyfin_ratings({"CommunityRating": 8.1, "CriticRating": 75}) == {"audience": 8.1, "critic": 7.5}
    assert jellyfin_ratings({}) == {}
    show = _item(20, "show", "Live", 2020, tmdb=1, ratings={"audience": 8.2})
    season = _item(21, "season", "Season 1", None, parent="20", index=1)
    assert ItemContext.from_server_item(show, "srv").ratings == {"audience": 8.2}
    assert ItemContext.from_server_item(season, "srv", parent=show).ratings == {"audience": 8.2}  # seasons inherit the show's


def test_server_engine_badges_ratings_without_tmdb(test_db, isolated_overlay_files):
    client = FakeClient()
    client.items["1"][0].ratings = {"audience": 8.4}  # the movie; the shows carry no rating
    config = normalize_config({"enabled": True, "types": {"rating": {"enabled": True, "options": {"fallback_tmdb": False}}}})
    engine = _engine(test_db, client, FakeMetadata({}), config, sources=_sources(isolated_overlay_files, 10, 20, 30))
    assert engine._needs_tmdb is False
    stats = engine.run()
    engine.flush()
    assert stats["applied"] == 1 and [u[0] for u in client.uploads] == ["10"]
    assert _state_items(test_db)["srv|10"]["sig"].startswith("rating:high|8.4")


def test_format_date_variants():
    d = date(2026, 9, 5)
    assert format_date(d, "MM/DD", False) == "09/05"
    assert format_date(d, "DD/MM", True) == "5/9"
    assert format_date(d, "MM/DD/YY", False) == "09/05/26"
    assert format_date(d, "MON D", False) == "SEP 5"
    assert format_date(d, "D MON", False) == "5 SEP"
    assert format_date(d, "Mon D", False) == "Sep 5"
    assert format_date(d, "D Mon", False) == "5 Sep"


# ── rendering ────────────────────────────────────────────────────────────────

def test_parse_color():
    assert parse_color("#FF0000") == (255, 0, 0, 255)
    assert parse_color("#ff000080") == (255, 0, 0, 128)
    assert parse_color("red", (1, 2, 3, 4)) == (1, 2, 3, 4)


def test_render_badge_scales_with_poster_size():
    badge = OverlayBadge(key="airing", text="AIRING", back_color="#1E88E5FF")
    small = Image.new("RGB", (1000, 1500), (90, 90, 90))
    big = Image.new("RGB", (2000, 3000), (90, 90, 90))
    out_s, rect_s = render_badge(small, badge, DEFAULT_PLACEMENT)
    out_b, rect_b = render_badge(big, badge, DEFAULT_PLACEMENT)
    assert out_s.size == (1000, 1500) and out_b.size == (2000, 3000)
    assert abs(rect_s[0] - rect_b[0]) <= 2 and abs(rect_s[2] - rect_b[2]) <= 2
    assert rect_s[1] == DEFAULT_PLACEMENT["vertical_offset"]
    cx = rect_s[0] + rect_s[2] // 2
    assert out_s.getpixel((cx, rect_s[1] + 4)) != (90, 90, 90)
    assert out_b.getpixel((cx * 2, (rect_s[1] + 4) * 2)) != (90, 90, 90)
    assert measure_badge(badge, DEFAULT_PLACEMENT)[2] == pytest.approx(rect_s[2], abs=2)


def test_render_badge_without_backdrop_draws_text_only():
    badge = OverlayBadge(key="x", text="ENDED", back_color="#FF0000FF")
    img = Image.new("RGB", (1000, 1500), (90, 90, 90))
    with_box = normalize_placement({"back_enabled": True, "back_style": "solid", "back_padding": 40})
    no_box = normalize_placement({"back_enabled": False, "back_padding": 40})
    out_box, rect_box = render_badge(img, badge, with_box)
    out_txt, rect_txt = render_badge(img, badge, no_box)
    assert rect_txt[2] < rect_box[2] and rect_txt[3] < rect_box[3]
    assert out_box.getpixel((rect_box[0] + 3, rect_box[1] + rect_box[3] // 2))[0] > 200
    assert out_txt.getpixel((rect_txt[0] - 2, rect_txt[1] + rect_txt[3] // 2)) == (90, 90, 90)
    assert any(out_txt.getpixel((x, rect_txt[1] + rect_txt[3] // 2)) != (90, 90, 90) for x in range(rect_txt[0], rect_txt[0] + rect_txt[2]))
    assert normalize_placement({"back_enabled": "false"})["back_enabled"] is False


def test_badge_text_uses_a_stand_in_font_for_a_character_its_font_lacks():
    """Roboto Condensed has no ●. A bundled font draws that one character, at the same size,
    instead of the font's empty box; everything the font does have it still draws itself."""
    from services.overlays import render

    assert render._glyph_fallbacks()[0].endswith("DejaVuSans.ttf")
    font = render._load_font(render.resolve_font_path("RobotoCondensed-Variable"), 80, "")
    assert all(render._font_for(ch, font) is font for ch in "Aa9/ •")
    stand_in = render._font_for("●", font)
    assert stand_in is not font and stand_in.size == font.size
    assert render._text_width(font, "A●", 0) == font.getlength("A") + stand_in.getlength("●")
    assert render._font_for("\uFFFE", font) is font  # no font has it: nothing to switch to

    # Drawn: a filled disc in the text color, where the missing-glyph box was hollow
    img = Image.new("RGB", (1000, 1500), (90, 90, 90))
    plain = normalize_placement({"back_enabled": False, "font": "RobotoCondensed-Variable", "font_size": 120, "font_color": "#FFFFFF"})
    out, (x, y, w, h) = render_badge(img, OverlayBadge(key="x", text="●"), plain)
    ink = [(px, py) for px in range(x, x + w) for py in range(y - 20, y + h + 20) if out.getpixel((px, py)) != (90, 90, 90)]
    cx = sum(px for px, _ in ink) // len(ink)
    cy = sum(py for _, py in ink) // len(ink)
    assert out.getpixel((cx, cy)) == (255, 255, 255)


def test_stand_in_mark_names_only_badges_drawn_with_a_stand_in_font():
    """The mark goes into an item's signature: posters badged while ● was an empty box are
    redone, and no other poster's signature moves."""
    from services.overlays.render import stand_in_mark

    roboto = normalize_placement({"font": "RobotoCondensed-Variable"})
    assert stand_in_mark(OverlayBadge(key="airing", text="AIRING 09/05"), roboto) == ""
    assert stand_in_mark(OverlayBadge(key="airing", text="● AIRING"), roboto) == "|glyphs=1"
    # A font that has the character draws it itself
    assert stand_in_mark(OverlayBadge(key="airing", text="● AIRING"), normalize_placement({"font": "LiberationSans-Regular"})) == ""
    assert stand_in_mark(OverlayBadge(key="logo", text="●", image="/x/logo.png"), roboto) == ""


def _ink_rows_of(text: str, **placement) -> tuple[int, int, int, int]:
    """(first ink row, last ink row, rect top, rect height) of a badge drawn without a backdrop."""
    img = Image.new("RGB", (1000, 1500), (90, 90, 90))
    plain = normalize_placement({"back_enabled": False, "font_size": 120, "vertical_align": "center", **placement})
    out, (x, y, w, h) = render_badge(img, OverlayBadge(key="x", text=text), plain)
    rows = [py for py in range(y - 80, y + h + 80) if any(out.getpixel((px, py)) != (90, 90, 90) for px in range(max(0, x - 4), x + w + 4))]
    return rows[0], rows[-1], y, h


def test_stand_in_symbol_is_centered_on_the_capitals():
    """The stand-in font draws its shapes lower than capitals sit; a dot alone, or beside
    text, is centered on the badge font's capital letters."""
    top, bottom, y, h = _ink_rows_of("●", font="RobotoCondensed-Variable")
    assert abs((top + bottom) / 2 - (y + h / 2)) <= 2


def test_stand_in_letter_shares_the_baseline():
    """Avenir Next has no Cyrillic: Ж comes from the stand-in font and stands where H stands."""
    assert abs(_ink_rows_of("Ж", font="AvenirNextLTPro-Bold")[1] - _ink_rows_of("H", font="AvenirNextLTPro-Bold")[1]) <= 3


def test_ink_below_the_capitals_is_not_cut_off_without_a_backdrop():
    """A badge without a backdrop is as tall as its capitals. A "g" (or a symbol) reaches past
    that and is drawn in full; the badge's rect stays the capital band."""
    cap_top, cap_bottom, y, h = _ink_rows_of("H", font="RobotoCondensed-Variable")
    g_top, g_bottom, g_y, g_h = _ink_rows_of("Hg", font="RobotoCondensed-Variable")
    assert (g_y, g_h) == (y, h) and g_top == cap_top
    assert g_bottom - cap_bottom >= 15  # the whole descender, where one row used to survive


def test_ink_above_the_box_leaves_the_box_where_it_is():
    """An accent taller than a tight box gets rows of its own above it; the box, a fading one
    included, is still drawn at the badge's rect."""
    bg = (90, 90, 90)
    img = Image.new("RGB", (1000, 1500), bg)
    for style in ("solid", "gradient"):
        tight = normalize_placement({"font": "RobotoCondensed-Variable", "font_size": 120, "back_padding": 0, "back_radius": 0,
                                     "back_min_width": 600, "vertical_align": "center", "back_style": style,
                                     "gradient_mode": "linear", "gradient_angle": 0})
        out, (x, y, w, h) = render_badge(img, OverlayBadge(key="x", text="É"), tight)
        edge = x + 4  # in the box, away from the letter
        assert out.getpixel((edge, y - 4)) == bg and out.getpixel((edge, y + h + 3)) == bg, style
        assert out.getpixel((edge, y + h - 2)) != bg, style  # the box reaches the rect's last rows
        # The accent itself is above the box, drawn in full
        assert any(out.getpixel((px, py)) != bg for px in range(x + w // 2 - 40, x + w // 2 + 40) for py in range(y - 24, y - 6)), style


def test_render_badge_baseline_is_stable_with_descenders():
    img = Image.new("RGB", (1000, 1500), (90, 90, 90))
    plain = normalize_placement({"back_enabled": False, "font_size": 80})

    def cap_top(text: str) -> tuple[int, int]:
        out, rect = render_badge(img, OverlayBadge(key="x", text=text), plain)
        x0, y0, w, h = rect
        rows = [y for y in range(max(0, y0 - 20), y0 + h + 40)
                if any(out.getpixel((x, y)) != (90, 90, 90) for x in range(max(0, x0), min(1000, x0 + w)))]
        return rows[0], h

    top_a, h_a = cap_top("ENDED")
    top_b, h_b = cap_top("ENDED,")
    top_c, h_c = cap_top("AIRING 09/05")
    assert h_a == h_b == h_c and top_a == top_b == top_c


def test_render_badge_alignment_and_clipping():
    badge = OverlayBadge(key="x", text="ENDED")
    img = Image.new("RGB", (1000, 1500), (90, 90, 90))
    right = normalize_placement({"horizontal_align": "right", "horizontal_offset": 40,
                                 "vertical_align": "bottom", "vertical_offset": 60})
    _, rect = render_badge(img, badge, right)
    assert rect[0] + rect[2] == 1000 - 40 and rect[1] + rect[3] == 1500 - 60
    off_edge = normalize_placement({"horizontal_align": "left", "horizontal_offset": -150, "vertical_offset": -20})
    out, rect = render_badge(img, badge, off_edge)
    assert out.size == (1000, 1500) and rect[0] == -150


def test_gradient_band_fades_down_from_the_top_edge():
    badge = OverlayBadge(key="x", text="NEW")
    img = Image.new("RGB", (1000, 1500), (90, 90, 90))
    top = normalize_placement({"vertical_align": "top", "vertical_offset": 30, "back_style": "band",
                               "gradient_start": "#000000FF", "gradient_band_height": 300})
    out, rect = render_badge(img, badge, top)
    _, text_only_rect = render_badge(img, badge, {**top, "back_enabled": False})
    assert rect == text_only_rect  # no box with a band: the drag handle hugs the text
    assert out.getpixel((rect[0] - 40, rect[1] + rect[3] // 2))[0] < 30  # band, not a box, beside the text
    assert out.getpixel((100, 0))[0] < 10            # top edge = start color
    assert out.getpixel((100, 296))[0] > 80          # inner end of the band ≈ transparent
    assert out.getpixel((100, 400)) == (90, 90, 90)  # below the band: untouched
    # The offset pushes the solid side down from the edge; the height still runs inward from there.
    out_drop, _ = render_badge(img, badge, normalize_placement({**top, "gradient_band_offset": 50}))
    assert out_drop.getpixel((100, 0)) == (90, 90, 90) and out_drop.getpixel((100, 50))[0] < 10 and out_drop.getpixel((100, 345))[0] > 80
    # Side insets narrow it.
    out_in, _ = render_badge(img, badge, normalize_placement({**top, "gradient_band_left": 200, "gradient_band_right": 200}))
    assert out_in.getpixel((100, 0)) == (90, 90, 90) and out_in.getpixel((500, 0))[0] < 10 and out_in.getpixel((899, 0)) == (90, 90, 90)
    # Several overlays with a band: the poster gets ONE band (the most opaque, then the taller), never a stack.
    from services.overlays.render import render_badges
    other = OverlayBadge(key="other", text="PG-13")
    low = normalize_placement({**top, "vertical_align": "bottom", "gradient_start": "#00000080"})
    once, rects = render_badges(img, [(badge, top), (other, low)])
    assert len(rects) == 2 and rects[0] == rect and once.getpixel((100, 5)) == out.getpixel((100, 5)) and once.getpixel((100, 0))[0] < 10
    assert render_badges(img, [(badge, low), (other, low)])[0].getpixel((100, 5)) == render_badge(img, badge, low)[0].getpixel((100, 5))  # two half bands = one half band
    assert render_badges(img, [(badge, normalize_placement({**top, "back_style": "solid"}))])[0].getpixel((100, 5)) == (90, 90, 90)  # no band asked: none drawn
    assert render_badges(img, [])[0].getpixel((100, 5)) == (90, 90, 90)
    # The band has no edge option: a stored edge choice is dropped.
    assert "gradient_band_edge" not in normalize_placement({"gradient_band_edge": "bottom"})
    # A badge with its own box keeps it on top of the band (the band replaces only the backdrop box).
    boxed = OverlayBadge(key="x", text="NEW", box={"fill": "#FF0000FF", "line_width": 0, "line_color": "#FFFFFFFF", "padding": 15, "radius": 0, "min_width": 0, "max_width": 0})
    both, box_rect = render_badge(img, boxed, top)
    assert both.getpixel((100, 0))[0] < 10 and all(abs(v - e) <= 3 for v, e in zip(both.getpixel((box_rect[0] + 5, box_rect[1] + 5)), (255, 0, 0)))
    assert box_rect[2] > rect[2]  # the badge's box pads the text; the band-only badge hugged it


def test_gradient_box_fills_the_backdrop_and_needs_it_on():
    badge = OverlayBadge(key="x", text="ENDED")
    img = Image.new("RGB", (1000, 1500), (90, 90, 90))
    box = normalize_placement({"back_style": "gradient", "gradient_mode": "linear", "gradient_start": "#FF0000FF", "gradient_angle": 90, "back_padding": 40})
    out, (x, y, w, h) = render_badge(img, badge, box)
    left, right = out.getpixel((x + 4, y + h // 2)), out.getpixel((x + w - 5, y + h // 2))  # mid-height: clear of the rounded corners
    assert left[0] > 200 and left[2] < 60        # solid red at the left edge…
    assert all(abs(c - 90) < 6 for c in right)   # …faded to (almost) nothing at the right edge
    assert out.getpixel((x - 5, y + h // 2)) == (90, 90, 90)  # nothing outside the box
    out_off, (x2, y2, w2, h2) = render_badge(img, badge, {**box, "back_enabled": False})
    assert out_off.getpixel((x2 - 2, y2 + h2 // 2)) == (90, 90, 90)  # text only: no box, no gradient
    # From the centre: the box itself is solid and glows out past its edges by `gradient_spread` px.
    glow = {**box, "gradient_mode": "center", "gradient_spread": 100}
    out_g, (x, y, w, h) = render_badge(img, badge, glow)
    assert (x, y, w, h) == render_badge(img, badge, {**glow, "gradient_spread": 0})[1]  # rect = the box, not the glow
    assert out_g.getpixel((x + 3, y + h // 2))[0] > 200                    # solid to the box edge
    assert out_g.getpixel((x - 3, y + h // 2))[0] > 230                    # no seam: full strength just outside it too
    assert out_g.getpixel((x - 20, y + h // 2))[0] > 120                   # still glowing past the edge…
    assert out_g.getpixel((x - 60, y + h // 2))[0] < out_g.getpixel((x - 20, y + h // 2))[0]  # …fading with distance…
    assert out_g.getpixel((x - 150, y + h // 2)) == (90, 90, 90)           # …and gone beyond the reach
    out_0, _ = render_badge(img, badge, {**glow, "gradient_spread": 0})
    assert out_0.getpixel((x - 3, y + h // 2)) == (90, 90, 90)             # no spread: nothing past the box
    p = normalize_placement({"gradient_mode": "radial", "gradient_spread": 5000})
    assert p["gradient_mode"] == DEFAULT_PLACEMENT["gradient_mode"] == "center" and p["gradient_spread"] == 500


def test_normalize_placement_gradient_fields():
    p = normalize_placement({"back_style": "circle", "gradient_angle": 400, "gradient_start": "", "gradient_band_right": 5000})
    assert p["back_style"] == DEFAULT_PLACEMENT["back_style"] == "solid" and p["gradient_angle"] == 359 and p["gradient_start"] == "" and p["gradient_band_right"] == 1000
    assert normalize_placement({"gradient_start": "blue"})["gradient_start"] == DEFAULT_PLACEMENT["gradient_start"]
    assert normalize_placement({"back_style": "BAND"})["back_style"] == "band"
    p = normalize_placement({"gradient_band_height": 9999, "gradient_band_left": -5, "gradient_band_offset": 5000})
    assert p["gradient_band_height"] == 1500 and p["gradient_band_left"] == 0 and p["gradient_band_offset"] == 1400


def test_normalize_placement_clamps_and_validates():
    p = normalize_placement({"horizontal_align": "middle", "font_size": 9999, "back_color": "blue", "font": "../x/Font"})
    assert p["horizontal_align"] == DEFAULT_PLACEMENT["horizontal_align"]
    assert p["font_size"] == 400 and p["back_color"] == DEFAULT_PLACEMENT["back_color"] and p["font"] == "Font"


# ── metadata ─────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_metadata_provider_caches_finds_and_refreshes(test_db, isolated_overlay_files, monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if "/find/" in url:
            return _Resp({"tv_results": [{"id": 77}], "movie_results": []})
        if url.endswith("/tv/77"):
            return _Resp({"status": "Ended", "first_air_date": "2010-01-01", "name": "S", "seasons": ["big"]})
        if url.endswith("/tv/1"):
            return _Resp({"name": "Live", "status": "Returning Series", "first_air_date": "2020-01-01",
                          "next_episode_to_air": {"air_date": "2030-01-01"}})
        return _Resp({}, 404)

    t0 = time.time()
    provider = MetadataProvider("key", now=t0, db=test_db)
    monkeypatch.setattr(provider._session, "get", fake_get)
    tvdb_only = _ctx("show", "S", 2010, tmdb=None, tvdb=5)
    meta = provider.for_item(tvdb_only)
    assert meta["tmdb"]["status"] == "Ended" and "seasons" not in meta["tmdb"]
    provider.for_item(tvdb_only)
    live = _ctx("show", "Live", 2020, tmdb=1, tvdb=9)
    provider.for_item(live)
    provider.for_item(_ctx(season=1, title="Live", year=2020, tmdb=1, tvdb=9))
    assert len(calls) == 3  # find + ended details + live details; the season shares the show record
    provider.flush()

    later = MetadataProvider("key", now=t0 + 3600, db=test_db)
    monkeypatch.setattr(later._session, "get", fake_get)
    later.for_item(tvdb_only)
    later.for_item(live)
    assert calls[3:] == [calls[2]]  # only the live show was re-fetched
    assert later.stats == {"requests": 1, "refreshed": 1, "cached": 1}
    assert MetadataProvider("").for_item(_ctx("show", "Other", 2011, tmdb=None, tvdb=6)) == {}
    # A rebuild sets the cache aside: the id mapping, the ended show and the live show are all asked again,
    # once each; the run then reuses its own answers.
    later.flush()
    rebuild = MetadataProvider("key", now=t0 + 7200, db=test_db)
    monkeypatch.setattr(rebuild._session, "get", fake_get)
    rebuild.begin_run(refresh=True)
    before = len(calls)
    assert rebuild.for_item(tvdb_only)["tmdb"]["status"] == "Ended" and rebuild.for_item(live)["tmdb"]["status"] == "Returning Series"
    assert [u.rsplit("/3", 1)[1] for u in calls[before:]] == ["/find/5", "/tv/77", "/tv/1"]
    rebuild.for_item(tvdb_only)
    rebuild.for_item(live)
    assert len(calls) == before + 3 and rebuild.stats == {"requests": 3, "refreshed": 2, "cached": 2}
    plain = MetadataProvider("key", now=t0 + 7200, db=test_db)  # without the flag the cache serves as before
    monkeypatch.setattr(plain._session, "get", fake_get)
    plain.begin_run()
    plain.for_item(tvdb_only)
    assert len(calls) == before + 3


def test_metadata_searches_idless_items_by_title_and_year(test_db, isolated_overlay_files, monkeypatch):
    def fake_get(url, params=None, timeout=None):
        if url.endswith("/search/movie"):
            return _Resp({"results": [{"id": 1, "title": "2 Fast 2 Furious", "release_date": "2003-06-06"}]})
        if url.endswith("/movie/1"):
            return _Resp({"title": "2 Fast 2 Furious", "status": "Released", "release_date": "2003-06-06"})
        return _Resp({"results": []})

    provider = MetadataProvider("key")
    monkeypatch.setattr(provider._session, "get", fake_get)
    meta = provider.for_item(_ctx("movie", "2 Fast 2 Furious", 2003, tmdb=None, tvdb=None))
    assert meta["tmdb"]["title"] == "2 Fast 2 Furious"
    assert provider.for_item(_ctx("movie", "Totally Made Up", 1999, tmdb=None, tvdb=None)) == {}


# ── server engine ────────────────────────────────────────────────────────────

def _enabled_config(**opts):
    return normalize_config({"enabled": True, "types": {"status": {"enabled": True, "options": opts}}})


def _item(item_id, kind, title, year, *, tmdb=None, tvdb=None, thumb="/thumb/1", parent=None, index=None, library="1", ratings=None, content_rating=None):
    ids = {k: str(v) for k, v in (("tmdb", tmdb), ("tvdb", tvdb)) if v}
    return MediaServerItem(item_id=str(item_id), item_type=kind, title=title, year=year, provider_ids=ids,
                           library_key=f"srv:{library}", thumb_path=thumb, parent_id=parent, index=index, ratings=dict(ratings or {}),
                           content_rating=content_rating)


class FakeClient:
    """A media server with one movie library and one show library."""

    def __init__(self):
        self.instance_name = None
        self.items = {
            "1": [_item(10, "movie", "Film", 2026, tmdb=9)],
            "2": [_item(20, "show", "Live", 2020, tmdb=1, tvdb=1, library="2"), _item(30, "show", "Done", 2010, tmdb=2, tvdb=2, library="2")],
        }
        self.seasons = {"2": [_item(21, "season", "Season 1", None, parent="20", index=1, library="2")]}
        self.uploads = []   # (item_id, bytes of the file)
        self.thumb_version = {}
        self.replaced = {}  # item_id → bytes someone else uploaded
        self.own = {}       # item_id → {reference: bytes}: the server's own posters, its pick first
        self.media = {}     # item_id → its media files (resolution, HDR, path, audio titles)
        self.media_calls = []  # ("library", key) / ("item", id): how the media details were asked for
        self.fetches = []   # (item_id, reference asked for)
        self.resets = []    # (item_id, reference) put back to the server's own poster
        self.unlocked = []  # (item ids, kinds) of every batch unlock
        self.stay_locked = set()  # item ids whose images cannot be unlocked

    def server_poster(self, item, ref=""):
        self.fetches.append((item.item_id, ref))
        own = self.own.get(item.item_id)
        if not own:
            return None
        key = ref if ref in own else next(iter(own))
        return key, own[key]

    def restore_server_poster(self, item, ref=""):
        self.resets.append((item.item_id, ref))
        return True

    def unlock_images(self, items, kinds):
        self.unlocked.append(([i.item_id for i in items], list(kinds)))
        return {i.item_id for i in items if i.item_id in self.stay_locked}

    def reset_images(self, item, kinds, unlocked=False):
        return {kind: self.reset_image(item, kind) for kind in kinds}

    def library_media(self, library_key):
        self.media_calls.append(("library", str(library_key)))
        return {k: list(v) for k, v in self.media.items()}

    def item_media(self, item):
        self.media_calls.append(("item", item.item_id))
        return list(self.media.get(item.item_id, []))

    def current_poster_bytes(self, item):
        """What the server serves for the item right now: an outside replacement, else our
        last upload, else the stock flat poster."""
        if item.item_id in self.replaced:
            return self.replaced[item.item_id]
        ours = [raw for iid, raw in self.uploads if iid == item.item_id]
        return ours[-1] if ours else _flat_poster_bytes()

    def bump_thumb_only(self, item_id):
        """A Plex metadata refresh: new thumb timestamp, same image."""
        self.thumb_version[item_id] = self.thumb_version.get(item_id, 0) + 1

    def _current(self, item):
        v = self.thumb_version.get(item.item_id)
        if v is None:
            return item
        return MediaServerItem(**{**item.__dict__, "thumb_path": f"/thumb/{item.item_id}/{v}"})

    def shared_copies(self, item, parent=None):
        """Like Plex: the copies of the item's title across every library, when it is in more than one."""
        identity = bundle_identity(item, parent)
        copies = [i for lib in self.items.values() for i in lib if identity and bundle_identity(i) == identity]
        return copies if len(copies) > 1 else []

    def get_library_items(self, library_key, item_types=None):
        return [self._current(i) for i in self.items.get(str(library_key).split(":")[-1], [])]

    def get_library_seasons(self, library_key):
        return [self._current(i) for i in self.seasons.get(str(library_key).split(":")[-1], [])]

    def get_seasons(self, show):
        return [s for s in self.get_library_seasons("2") if s.parent_id == show.item_id]

    def get_item(self, item_id):
        for items in list(self.items.values()) + list(self.seasons.values()):
            for i in items:
                if i.item_id == str(item_id):
                    return self._current(i)
        return None

    def upload_image(self, item, kind, filepath):
        self.uploads.append((item.item_id, Path(filepath).read_bytes()))
        self.thumb_version[item.item_id] = self.thumb_version.get(item.item_id, 0) + 1
        return True

    def find_by_provider_ids(self, provider_ids, media_type, library_keys=None, title=None, year=None):
        return [self._current(i) for items in self.items.values() for i in items
                if any(i.provider_ids.get(k) == v for k, v in provider_ids.items())]

    def find_by_title(self, title, media_type=None, library_keys=None):
        return [self._current(i) for items in self.items.values() for i in items if i.title == title]

    def replace_poster_externally(self, item_id):
        self.thumb_version[item_id] = self.thumb_version.get(item_id, 0) + 100
        img = Image.new("RGB", (1000, 1500))
        px = img.load()
        for y in range(0, 1500, 50):  # busy artwork (checkered blocks), unlike the flat stock poster
            for x in range(0, 1000, 50):
                shade = 40 + ((x // 50 * 7 + y // 50 * 13) % 5) * 45
                for dy in range(50):
                    for dx in range(50):
                        px[x + dx, y + dy] = (shade, 255 - shade, (x + y) % 255)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        self.replaced[item_id] = buf.getvalue()


INSTANCE = {"name": "srv", "url": "http://srv", "api_key": "k", "type": "plex"}
LIBRARIES = [{"instance_name": "srv", "libraries": [
    {"key": "1", "title": "Movies", "type": "movie", "enabled": True},
    {"key": "2", "title": "TV", "type": "show", "enabled": True},
]}]


def _flat_poster_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (1000, 1500), (90, 90, 90)).save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _engine(test_db, client, meta, config=None, *, sources=None):
    return ServerOverlayEngine(
        test_db, config or _enabled_config(), metadata=meta, instances=[INSTANCE], library_configs=LIBRARIES,
        client_factory=lambda inst: client, sources=sources,
    )


def _sources(tmp, *ids, instance="srv", raw=None):
    """Posterflow's clean poster files, one per item id, as the engine finds them."""
    folder = tmp / "assets"
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for item_id in ids:
        path = folder / f"{item_id}.jpg"
        path.write_bytes(raw or _flat_poster_bytes())
        out[(instance, str(item_id))] = str(path)
    return out


def _red_poster_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (1000, 1500), (200, 30, 30)).save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _off(config):
    return {**config, "types": {k: {**v, "enabled": False} for k, v in config["types"].items()}}


def _state_items(db):
    """The overlay state as the engine sees it: {"<instance>|<item id>": entry}."""
    return {row.key: row.to_entry() for row in db.query(OverlayItem)}


def _seed_items(db, *item_ids, instance="srv"):
    for item_id in item_ids:
        db.add(OverlayItem(instance=instance, item_id=str(item_id), sig="s"))
    db.commit()


def _has_badge(raw: bytes) -> bool:
    """Non-flat pixels along the top or the bottom edge of the flat test poster."""
    with Image.open(io.BytesIO(raw)) as img:
        rgb = img.convert("RGB")
        edges = list(rgb.crop((300, 0, 700, 150)).get_flattened_data()) + list(rgb.crop((300, 1350, 700, 1500)).get_flattened_data())
        return sum(1 for px in edges if max(abs(c - 90) for c in px) > 30) > 500


def test_server_engine_badges_from_source_files_and_refreshes(test_db, isolated_overlay_files):
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 10, 20, 30)
    meta = FakeMetadata({
        1: {"status": "Returning Series", "first_air_date": "2020-01-01", "last_episode_to_air": {"air_date": _iso(-2)}},
        2: {"status": "Ended", "first_air_date": "2010-01-01"},
        9: {"status": "Released", "release_date": _iso(-3)},
    })
    engine = _engine(test_db, client, meta, sources=sources)
    stats = engine.run()
    engine.flush()
    assert (stats["applied"], stats["current"], stats["failed"]) == (3, 0, 0)
    assert sorted(i for i, _ in client.uploads) == ["10", "20", "30"]
    assert all(_has_badge(raw) for _, raw in client.uploads)
    entry = _state_items(test_db)["srv|20"]
    assert entry["source_path"] == sources[("srv", "20")] and entry["source_sha1"] == hashlib.sha1(_flat_poster_bytes()).hexdigest()
    assert entry["composite_sha1"] == hashlib.sha1(dict(client.uploads)["20"]).hexdigest()

    again = _engine(test_db, client, meta, sources=sources)
    stats = again.run()
    again.flush()
    assert stats["current"] == 3 and len(client.uploads) == 3  # nothing changed → nothing uploaded, nothing downloaded

    meta.by_id[1]["next_episode_to_air"] = {"air_date": _iso(4)}  # a date appears → recomposed from the file
    third = _engine(test_db, client, meta, sources=sources)
    stats = third.run()
    third.flush()
    assert stats["refreshed"] == 1 and stats["current"] == 2 and client.uploads[-1][0] == "20"

    Path(sources[("srv", "30")]).write_bytes(_red_poster_bytes())  # a new poster file → recomposed on it
    fourth = _engine(test_db, client, meta, sources=sources)
    stats = fourth.run()
    fourth.flush()
    assert stats["refreshed"] == 1 and client.uploads[-1][0] == "30"
    with Image.open(io.BytesIO(client.uploads[-1][1])) as img:
        assert img.convert("RGB").getpixel((5, 1400)) != (90, 90, 90)

    meta.by_id[9] = {"status": "Released", "release_date": "2000-01-01"}  # no badge any more → the clean file goes back up
    fifth = _engine(test_db, client, meta, sources=sources)
    stats = fifth.run()
    fifth.flush()
    assert stats["restored"] == 1 and client.uploads[-1] == ("10", _flat_poster_bytes())
    assert "srv|10" not in _state_items(test_db)


def test_items_without_a_posterflow_poster_are_left_alone(test_db, isolated_overlay_files):
    client = FakeClient()
    meta = FakeMetadata({1: {"status": "Ended", "first_air_date": "2020-01-01"}, 2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    engine = _engine(test_db, client, meta, sources=_sources(isolated_overlay_files, 30))
    stats = engine.run()
    engine.flush()
    assert stats["applied"] == 1 and stats["no_poster"] == 1 and [i for i, _ in client.uploads] == ["30"]
    assert set(_state_items(test_db)) == {"srv|30"}


def _server_config(**opts):
    cfg = _enabled_config(**opts)
    cfg["badge_server_posters"] = True
    return cfg


def test_prune_plex_uploads_is_on_unless_switched_off():
    assert normalize_config({})["prune_plex_uploads"] is True
    assert normalize_config({"prune_plex_uploads": False})["prune_plex_uploads"] is False
    assert normalize_config({"prune_plex_uploads": "false"})["prune_plex_uploads"] is False
    assert normalize_config({"prune_plex_uploads": "yes"})["prune_plex_uploads"] is True


def test_badge_server_posters_is_an_opt_in_setting():
    assert normalize_config({})["badge_server_posters"] is False
    assert normalize_config({"badge_server_posters": True})["badge_server_posters"] is True
    assert normalize_config({"badge_server_posters": "true"})["badge_server_posters"] is True
    assert normalize_config({"badge_server_posters": "nope"})["badge_server_posters"] is False


def test_items_without_a_file_are_badged_on_the_servers_own_poster_when_asked(test_db, isolated_overlay_files):
    client = FakeClient()
    client.own = {"20": {"prov://pick": _red_poster_bytes(), "prov://other": _flat_poster_bytes()}}
    meta = FakeMetadata({1: {"status": "Ended", "first_air_date": "2020-01-01"}, 2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    sources = _sources(isolated_overlay_files, 30)  # Posterflow has a file for one of the two shows

    dry = _engine(test_db, client, meta, _server_config(), sources=sources)
    assert dry.run(dry_run=True)["applied"] == 2 and client.fetches == [] and client.uploads == []  # a dry run fetches nothing

    engine = _engine(test_db, client, meta, _server_config(), sources=sources)
    stats = engine.run()
    engine.flush()
    assert (stats["applied"], stats["server_sourced"], stats["no_poster"], stats["failed"]) == (2, 1, 0, 0)
    assert client.fetches == [("20", "")]  # the file-backed show never asks the server; the other asks for its pick
    entry = _state_items(test_db)["srv|20"]
    assert entry["source_path"] == "server:prov://pick" and entry["source_sha1"] == hashlib.sha1(_red_poster_bytes()).hexdigest()
    composite = dict(client.uploads)["20"]
    assert _has_badge(composite) and entry["composite_sha1"] == hashlib.sha1(composite).hexdigest()
    with Image.open(io.BytesIO(composite)) as img:
        assert img.convert("RGB").getpixel((5, 1400))[0] > 150  # drawn on the server's (red) poster
    assert _state_items(test_db)["srv|30"]["source_path"] == sources[("srv", "30")]

    again = _engine(test_db, client, meta, _server_config(), sources=sources)
    stats = again.run()
    again.flush()
    assert stats["current"] == 2 and len(client.uploads) == 2 and len(client.fetches) == 1  # unchanged: no fetch, no upload

    meta.by_id[1] = {"status": "Canceled", "first_air_date": "2020-01-01"}  # the badge changes: the SAME poster is fetched again
    third = _engine(test_db, client, meta, _server_config(), sources=sources)
    stats = third.run()
    third.flush()
    assert stats["refreshed"] == 1 and client.fetches[-1] == ("20", "prov://pick") and client.uploads[-1][0] == "20"
    assert _state_items(test_db)["srv|20"]["source_path"] == "server:prov://pick"

    # A Posterflow file arrives for it: the uploader is told its badge is stale, and the next run composes on the file.
    later = {**sources, **_sources(isolated_overlay_files, 20)}
    assert _engine(test_db, client, meta, _server_config(), sources=later).badge_stale("srv", client.get_item("20"), later[("srv", "20")]) is True
    fourth = _engine(test_db, client, meta, _server_config(), sources=later)
    stats = fourth.run()
    fourth.flush()
    assert stats["refreshed"] == 1 and len(client.fetches) == 2 and _state_items(test_db)["srv|20"]["source_path"] == later[("srv", "20")]


def test_a_server_without_a_poster_of_its_own_leaves_the_item_alone(test_db, isolated_overlay_files):
    client = FakeClient()  # the server offers nothing for any item
    meta = FakeMetadata({1: {"status": "Ended", "first_air_date": "2020-01-01"}, 2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    engine = _engine(test_db, client, meta, _server_config(), sources={})
    stats = engine.run()
    engine.flush()
    assert stats["no_poster"] == 2 and stats["applied"] == 0 and stats["by_state"] == {} and client.uploads == []
    assert sorted(client.fetches) == [("20", ""), ("30", "")] and _state_items(test_db) == {}


def test_badges_on_server_posters_are_removed_by_resetting_to_the_servers_poster(test_db, isolated_overlay_files):
    client = FakeClient()
    client.own = {"20": {"prov://pick": _flat_poster_bytes()}, "21": {"prov://s1": _flat_poster_bytes()}}
    meta = FakeMetadata({1: {"status": "Ended", "first_air_date": "2020-01-01"}, 2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    sources = _sources(isolated_overlay_files, 30)
    on = _engine(test_db, client, meta, _server_config(apply_to_seasons=True), sources=sources)
    stats = on.run()
    on.flush()
    # The show and its season have no Posterflow file: both are badged on the server's own posters.
    assert stats["server_sourced"] == 2 and sorted(i for i, _ in client.uploads) == ["20", "21", "30"]
    assert _state_items(test_db)["srv|21"]["source_path"] == "server:prov://s1"

    # The option is switched off again: those two go back to the server's poster, the file-backed show stays badged.
    plain = _engine(test_db, client, meta, _enabled_config(apply_to_seasons=True), sources=sources)
    stats = plain.run()
    plain.flush()
    assert stats["restored"] == 2 and sorted(client.resets) == [("20", "prov://pick"), ("21", "prov://s1")]
    assert len(client.uploads) == 3 and set(_state_items(test_db)) == {"srv|30"}  # nothing uploaded to remove them

    # On again, then overlays off altogether: server-sourced items are reset, the file-backed one gets its clean file.
    back = _engine(test_db, client, meta, _server_config(apply_to_seasons=True), sources=sources)
    back.run()
    back.flush()
    assert len(client.uploads) == 5 and len(_state_items(test_db)) == 3
    client.resets.clear()
    off = ServerOverlayEngine(test_db, _off(_server_config(apply_to_seasons=True)), metadata=meta, instances=[INSTANCE], library_configs=LIBRARIES,
                              client_factory=lambda inst: client, sources=sources)
    stats = off.restore_all()
    off.flush()
    assert stats["restored"] == 3 and sorted(client.resets) == [("20", "prov://pick"), ("21", "prov://s1")]
    assert client.uploads[-1] == ("30", _flat_poster_bytes()) and len(client.uploads) == 6 and _state_items(test_db) == {}


def test_sources_come_from_the_upload_records(test_db, isolated_overlay_files):
    """Without an injected map the engine reads the uploader's records: which file went to
    which item on which server. Artwork records are not posters; a vanished file is no source."""
    from models.plex_upload import PlexUploadRecord

    poster = isolated_overlay_files / "assets" / "Done (2010)" / "poster.jpg"
    poster.parent.mkdir(parents=True)
    poster.write_bytes(_flat_poster_bytes())
    logo = poster.parent / "logo.png"
    logo.write_bytes(b"png")
    keys = json.dumps(["abc:/library/sections/2"])
    test_db.add(PlexUploadRecord(file_path=str(poster), uploaded_to_rating_keys=json.dumps(["30"]), uploaded_to_library_keys=keys))
    test_db.add(PlexUploadRecord(file_path=str(logo), uploaded_to_rating_keys=json.dumps(["30", "20"]), uploaded_to_library_keys=keys))
    test_db.add(PlexUploadRecord(file_path=str(isolated_overlay_files / "gone.jpg"), uploaded_to_rating_keys=json.dumps(["20"]), uploaded_to_library_keys=keys))
    # The uploader records season files under the SHOW's id: they must never become the show's source.
    live_dir = isolated_overlay_files / "assets" / "Live (2020)"
    live_dir.mkdir()
    (live_dir / "poster.jpg").write_bytes(_flat_poster_bytes())
    (live_dir / "Season01.jpg").write_bytes(_red_poster_bytes())
    for name in ("poster.jpg", "Season01.jpg"):
        test_db.add(PlexUploadRecord(file_path=str(live_dir / name), uploaded_to_rating_keys=json.dumps(["20"]), uploaded_to_library_keys=keys))
    test_db.commit()
    client = FakeClient()
    client.server_id = "abc"
    engine = _engine(test_db, client, FakeMetadata({}))
    done, live, season = client.items["2"][1], client.items["2"][0], client.seasons["2"][0]
    assert engine.source_for(client, done) == str(poster)
    assert engine.source_for(client, live) == str(live_dir / "poster.jpg")           # not Season01.jpg
    assert engine.source_for(client, season, parent=live) == str(live_dir / "Season01.jpg")
    assert engine.source_for(client, season) is None                                 # a season needs its show
    other = FakeClient()
    other.server_id = "zzz"
    assert engine.source_for(other, done) is None


def test_upload_hook_composes_once_and_records(test_db, isolated_overlay_files):
    """The uploader hands the engine the file it is about to push: the badge is composed on
    it, one file goes up, and the record matches what the server shows."""
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 30, 10)
    meta = FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    engine = _engine(test_db, client, meta, sources=sources)
    show, movie = client.items["2"][1], client.items["1"][0]
    composite = engine.compose_for_upload("srv", show, sources[("srv", "30")])
    assert composite and _has_badge(Path(composite).read_bytes())
    client.upload_image(show, "poster", composite)  # what the uploader does with it
    engine.record_upload("srv", show, True)
    assert not Path(composite).exists() and engine.stats["applied"] == 1
    entry = _state_items(test_db)["srv|30"]
    assert entry["sig"].startswith("status:") and entry["composite_sha1"] == hashlib.sha1(client.uploads[-1][1]).hexdigest()
    assert entry["source_path"] == sources[("srv", "30")]
    # The next run finds it current: no second upload.
    assert _engine(test_db, client, meta, sources=sources).run()["current"] == 1 and len(client.uploads) == 1
    # No badge for this item (no TMDB record): the plain file goes up and nothing is recorded.
    assert engine.compose_for_upload("srv", movie, sources[("srv", "10")]) is None
    engine.record_upload("srv", movie, True)
    assert "srv|10" not in _state_items(test_db)
    # A failed upload leaves the record as it was.
    composite = engine.compose_for_upload("srv", show, sources[("srv", "30")])
    engine.record_upload("srv", show, False)
    assert not Path(composite).exists() and _state_items(test_db)["srv|30"]["sig"] == entry["sig"]
    # With overlays off, uploading the plain poster for a badged item drops its record.
    off = ServerOverlayEngine(test_db, _off(_enabled_config()), metadata=meta, instances=[INSTANCE], library_configs=LIBRARIES,
                              client_factory=lambda inst: client, sources=sources)
    assert off.compose_for_upload("srv", show, sources[("srv", "30")]) is None
    off.record_upload("srv", show, True)
    assert "srv|30" not in _state_items(test_db) and off.stats["restored"] == 1


def test_collections_are_never_badged(test_db, isolated_overlay_files):
    """A collection poster goes up plain whatever the settings, and a badge record a collection
    carries is restored and forgotten, by the uploader's stale check and by a run alike."""
    client = FakeClient()
    coll = _item(40, "collection", "Films", None, tmdb=2, library="1")  # a TMDB id a show lookup would resolve
    client.items["1"].append(coll)
    sources = _sources(isolated_overlay_files, 30, 10, 40)
    meta = FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    engine = _engine(test_db, client, meta, sources=sources)
    assert engine.compose_for_upload("srv", coll, sources[("srv", "40")]) is None
    engine.record_upload("srv", coll, True)
    assert "srv|40" not in _state_items(test_db)
    assert engine.badge_stale("srv", coll, sources[("srv", "40")]) is False
    stale = {"sig": "status:ENDED", "source_path": sources[("srv", "40")], "source_sha1": "x", "composite_sha1": "y",
             "title": "Films", "instance": "srv", "item_type": "collection", "library": "1"}
    # Badged before (a collection taken for a show): the uploader sees a stale poster and pushes the clean one.
    engine._set_entry("srv|40", dict(stale))
    engine._commit_state()
    assert engine.badge_stale("srv", coll, sources[("srv", "40")]) is True
    assert engine.compose_for_upload("srv", coll, sources[("srv", "40")]) is None
    engine.record_upload("srv", coll, True)
    assert "srv|40" not in _state_items(test_db)
    # A run puts the clean poster back and forgets the record, instead of forgetting it as "gone".
    engine._set_entry("srv|40", dict(stale))
    engine._commit_state()
    before = len(client.uploads)
    runner = _engine(test_db, client, meta, sources=sources)
    stats = runner.run()
    runner.flush()
    assert stats["restored"] == 1 and "40" in [item_id for item_id, _ in client.uploads[before:]]  # the show gets its badge, the collection its clean poster
    assert "srv|40" not in _state_items(test_db)


def test_the_badge_is_drawn_while_the_uploader_lets_its_other_uploads_run(test_db, isolated_overlay_files):
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 30)
    engine = _engine(test_db, client, FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}}), sources=sources)
    seen = []
    draw = engine._compose_bytes

    class Aside:
        def __enter__(self):
            seen.append("stepped aside")

        def __exit__(self, *exc):
            seen.append("back")

    engine.off_turn = Aside
    engine._compose_bytes = lambda *args: (seen.append("drawn"), draw(*args))[1]
    assert engine.compose_for_upload("srv", client.items["2"][1], sources[("srv", "30")])
    assert seen == ["stepped aside", "drawn", "back"]


def test_upload_hook_honors_the_library_selection(test_db, isolated_overlay_files):
    """Only libraries ticked on the Overlays page get badges at upload; the rest go up plain."""
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 10, 30)
    meta = FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}, 9: {"status": "Released", "release_date": _iso(-3)}})
    cfg = _enabled_config()
    cfg["all_libraries"] = False
    cfg["libraries"] = ["srv:2"]  # the TV library only
    engine = _engine(test_db, client, meta, cfg, sources=sources)
    movie, show = client.items["1"][0], client.items["2"][1]
    assert engine.compose_for_upload("srv", movie, sources[("srv", "10")]) is None  # would badge, but its library is not covered
    engine.record_upload("srv", movie, True)
    assert engine.compose_for_upload("srv", show, sources[("srv", "30")]) is not None
    engine.record_upload("srv", show, True)
    assert set(_state_items(test_db)) == {"srv|30"}


def test_badge_stale_tracks_status_settings_and_removal(test_db, isolated_overlay_files):
    """What the uploader asks about a poster it would otherwise skip."""
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 30, 10)
    meta = FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    show, movie = client.items["2"][1], client.items["1"][0]
    engine = _engine(test_db, client, meta, sources=sources)
    assert engine.badge_stale("srv", show, sources[("srv", "30")]) is True       # never badged, a badge applies
    assert engine.badge_stale("srv", movie, sources[("srv", "10")]) is False     # no badge applies, none recorded
    engine.run()
    engine.flush()
    again = _engine(test_db, client, meta, sources=sources)
    assert again.badge_stale("srv", show, sources[("srv", "30")]) is False      # current
    meta.by_id[2] = {"status": "Returning Series", "first_air_date": "2010-01-01", "next_episode_to_air": {"air_date": _iso(4)}}
    assert _engine(test_db, client, meta, sources=sources).badge_stale("srv", show, sources[("srv", "30")]) is True  # status moved on
    meta.by_id[2] = {"status": "Ended", "first_air_date": "2010-01-01"}
    moved = _enabled_config()
    moved["types"]["status"]["overlays"][0]["placement"]["vertical_offset"] = 400
    assert _engine(test_db, client, meta, moved, sources=sources).badge_stale("srv", show, sources[("srv", "30")]) is True  # settings changed the pixels
    same = _enabled_config(new_days=7)  # a movie-only option: identical pixels for this badge
    assert _engine(test_db, client, meta, same, sources=sources).badge_stale("srv", show, sources[("srv", "30")]) is False
    off = ServerOverlayEngine(test_db, _off(_enabled_config()), metadata=meta, instances=[INSTANCE], library_configs=LIBRARIES,
                              client_factory=lambda inst: client, sources=sources)
    assert off.badge_stale("srv", show, sources[("srv", "30")]) is True        # badged, overlays off → the clean poster must go up
    assert off.badge_stale("srv", movie, sources[("srv", "10")]) is False


def test_plex_poster_replaced_on_the_server_is_pushed_again(test_db, isolated_overlay_files):
    """The listing's thumb timestamp is only a hint: a metadata refresh moves it with the image
    untouched (confirm, no upload); a poster someone picked by hand is not ours (push ours)."""
    client = FakeClient()
    client.selected_poster_key = lambda item: ("metadata://posters/agent" if item.item_id in client.replaced else next(
        (f"upload://posters/{hashlib.sha1(raw).hexdigest()}" for iid, raw in reversed(client.uploads) if iid == item.item_id), ""))
    for lib in list(client.items.values()) + list(client.seasons.values()):
        for it in lib:
            it.client = client
    sources = _sources(isolated_overlay_files, 30)
    meta = FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    first = _engine(test_db, client, meta, sources=sources)
    first.run()
    first.flush()
    assert len(client.uploads) == 1 and _state_items(test_db)["srv|30"].get("server_key", "") == ""  # Plex: confirmed on the next look
    second = _engine(test_db, client, meta, sources=sources)
    assert second.run()["current"] == 1 and len(client.uploads) == 1
    second.flush()
    assert _state_items(test_db)["srv|30"]["server_key"] == "/thumb/30/1"  # our upload, now remembered by its thumb
    client.bump_thumb_only("30")  # a metadata refresh: new timestamp, same image
    third = _engine(test_db, client, meta, sources=sources)
    assert third.run()["current"] == 1 and len(client.uploads) == 1
    third.flush()
    assert _state_items(test_db)["srv|30"]["server_key"] == "/thumb/30/2"
    client.replace_poster_externally("30")  # someone picked another poster in Plex
    assert _engine(test_db, client, meta, sources=sources).badge_stale("srv", client.get_item("30"), sources[("srv", "30")]) is True
    fourth = _engine(test_db, client, meta, sources=sources)
    stats = fourth.run()
    fourth.flush()
    assert stats["refreshed"] == 1 and len(client.uploads) == 2 and _has_badge(client.uploads[-1][1])


def test_jellyfin_poster_replaced_on_the_server_is_pushed_again(test_db, isolated_overlay_files):
    """Jellyfin's image tag is the image: recorded right after our upload, so a listing with a
    different tag means a different image, with no extra request."""

    class FakeJellyfin(FakeClient):
        server_type = "jellyfin"

        def upload_image(self, item, kind, filepath):
            ok = super().upload_image(item, kind, filepath)
            item.native["ImageTags"]["Primary"] = hashlib.sha1(Path(filepath).read_bytes()).hexdigest()[:8]
            return ok

        def run_scheduled_task(self, key):
            return True

    client = FakeJellyfin()
    for lib in client.items.values():
        for it in lib:
            it.native = {"ImageTags": {"Primary": f"tag-{it.item_id}"}}
    sources = _sources(isolated_overlay_files, 30)
    meta = FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    first = _engine(test_db, client, meta, sources=sources)
    first.run()
    first.flush()
    recorded = _state_items(test_db)["srv|30"]["server_key"]
    assert recorded.startswith("jf:") and recorded != "jf:tag-30"  # the tag of what we uploaded
    second = _engine(test_db, client, meta, sources=sources)
    assert second.run()["current"] == 1 and len(client.uploads) == 1
    client.items["2"][1].native["ImageTags"]["Primary"] = "someone-elses"
    third = _engine(test_db, client, meta, sources=sources)
    stats = third.run()
    third.flush()
    assert stats["refreshed"] == 1 and len(client.uploads) == 2
    assert _state_items(test_db)["srv|30"]["server_key"] == "jf:" + hashlib.sha1(client.uploads[-1][1]).hexdigest()[:8]
    client.items["2"][1].native["ImageTags"] = {}  # the image was deleted outright
    fourth = _engine(test_db, client, meta, sources=sources)
    assert fourth.run()["refreshed"] == 1 and len(client.uploads) == 3


def test_duplicate_folders_for_one_item_do_not_churn(test_db, isolated_overlay_files):
    """Two placed folders both mapped to the same item: the badge is composed from whichever
    uploaded last; the other file is not a reason to re-upload on every run."""
    client = FakeClient()
    a = _sources(isolated_overlay_files, 30)[("srv", "30")]
    b = str(isolated_overlay_files / "assets" / "30-dupe.jpg")
    Path(b).write_bytes(_red_poster_bytes())
    meta = FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    show = client.items["2"][1]
    engine = _engine(test_db, client, meta, sources={("srv", "30"): b})
    client.upload_image(show, "poster", engine.compose_for_upload("srv", show, b))  # the uploader pushed folder B last
    engine.record_upload("srv", show, True)
    again = _engine(test_db, client, meta, sources={("srv", "30"): b})
    assert again.badge_stale("srv", show, a) is False   # folder A: a different file, not a change to ours
    assert again.badge_stale("srv", show, b) is False   # folder B: unchanged
    Path(b).write_bytes(_flat_poster_bytes())
    assert _engine(test_db, client, meta, sources={("srv", "30"): b}).badge_stale("srv", show, b) is True  # B itself changed


def test_stable_poster_key_reads_the_selected_plex_poster():
    class PlexPoster:
        def __init__(self, key, selected):
            self.ratingKey, self.key, self.selected = key, key, selected

    class Native:
        def posters(self):
            return [PlexPoster("metadata://posters/tmdb", False), PlexPoster("upload://posters/abc123", True)]

    item = MediaServerItem(item_id="1", item_type="show", title="S", native=Native(), thumb_path="/thumb/1/2")
    assert server_mod.stable_poster_key(item) == "plex:upload://posters/abc123"
    assert server_mod.stable_poster_key(MediaServerItem(item_id="2", item_type="show", title="S")) == ""
    Native.posters = lambda self: [PlexPoster("metadata://posters/tmdb", False)]  # listed, none selected: no poster shown
    assert server_mod.stable_poster_key(item) == server_mod.PLEX_NO_POSTER and server_mod.upload_sha(server_mod.PLEX_NO_POSTER) == ""


def test_state_flush_merges_concurrent_runs(test_db, isolated_overlay_files):
    """Two engines alive at once (a workflow run and a webhook upload) must not wipe each
    other's records when they flush."""
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 10, 30)
    meta = FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}, 9: {"status": "Released", "release_date": _iso(-3)}})
    movies = _enabled_config()
    movies["all_libraries"] = False; movies["libraries"] = ["srv:1"]
    shows = _enabled_config()
    shows["all_libraries"] = False; shows["libraries"] = ["srv:2"]
    a = _engine(test_db, client, meta, movies, sources=sources)
    b = _engine(test_db, client, meta, shows, sources=sources)
    a.run()
    b.run()
    a.flush()
    b.flush()
    assert set(_state_items(test_db)) == {"srv|10", "srv|30"}


def test_server_engine_seasons_libraries_and_dry_run(test_db, isolated_overlay_files):
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 10, 20, 21, 30)
    meta = FakeMetadata({1: {"status": "Ended", "first_air_date": "2020-01-01"}, 2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    cfg = _enabled_config(apply_to_seasons=True)
    cfg["all_libraries"] = False
    cfg["libraries"] = ["srv:2"]  # only the TV library
    engine = _engine(test_db, client, meta, cfg, sources=sources)
    stats = engine.run(dry_run=True)
    assert stats["applied"] == 3 and client.uploads == []  # 2 shows + 1 season, nothing uploaded
    engine = _engine(test_db, client, meta, cfg, sources=sources)
    stats = engine.run()
    engine.flush()
    assert sorted(i for i, _ in client.uploads) == ["20", "21", "30"]  # the movie library was left alone
    assert stats["by_state"] == {"ENDED": 3}


def test_server_engine_settings_change_recomposes_everything(test_db, isolated_overlay_files):
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 30)
    meta = FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    first = _engine(test_db, client, meta, sources=sources)
    first.run()
    first.flush()
    moved = _enabled_config()
    moved["types"]["status"]["overlays"][0]["placement"]["vertical_offset"] = 400
    second = _engine(test_db, client, meta, moved, sources=sources)
    stats = second.run()
    second.flush()
    assert stats["refreshed"] == 1 and len(client.uploads) == 2


def test_one_upload_after_a_settings_change_does_not_hide_it_from_the_next_run(test_db, isolated_overlay_files):
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 20, 30)
    meta = FakeMetadata({1: {"status": "Ended", "first_air_date": "2020-01-01"}, 2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    first = _engine(test_db, client, meta, sources=sources)
    first.run()
    first.flush()
    assert len(client.uploads) == 2
    moved = _enabled_config()
    moved["types"]["status"]["overlays"][0]["placement"]["vertical_offset"] = 400
    # A webhook upload badges one show with the new settings and commits its record.
    hook = _engine(test_db, client, meta, moved, sources=sources)
    done = client.items["2"][1]
    client.upload_image(done, "poster", hook.compose_for_upload("srv", done, sources[("srv", "30")]))
    hook.record_upload("srv", done, True)
    assert len(client.uploads) == 3 and "cfg:status:" in _state_items(test_db)["srv|30"]["sig"]
    # The next incremental run still re-composes the other show: each record says what it was drawn with.
    later = _engine(test_db, client, meta, moved, sources=sources)
    stats = later.run()
    later.flush()
    assert stats["refreshed"] == 1 and stats["current"] == 1 and client.uploads[-1][0] == "20"
    assert _state_items(test_db)["srv|20"]["sig"] == _state_items(test_db)["srv|30"]["sig"].replace("ENDED|", "ENDED|") and len(client.uploads) == 4


def test_season_badges_are_restored_when_season_posters_are_switched_off(test_db, isolated_overlay_files):
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 20, 21, 30)
    meta = FakeMetadata({1: {"status": "Ended", "first_air_date": "2020-01-01"}, 2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    on = _engine(test_db, client, meta, _enabled_config(apply_to_seasons=True), sources=sources)
    on.run()
    on.flush()
    assert set(_state_items(test_db)) == {"srv|20", "srv|21", "srv|30"}
    off = _engine(test_db, client, meta, _enabled_config(apply_to_seasons=False), sources=sources)
    stats = off.run()
    off.flush()
    assert stats["restored"] == 1 and stats["current"] == 2 and client.uploads[-1] == ("21", _flat_poster_bytes())
    assert set(_state_items(test_db)) == {"srv|20", "srv|30"}  # the season is restored and forgotten, not just forgotten
    again = _engine(test_db, client, meta, _enabled_config(apply_to_seasons=False), sources=sources)
    assert again.run()["restored"] == 0 and len(client.uploads) == 4  # nothing left to put back


def test_badges_in_a_library_overlays_no_longer_cover_are_restored(test_db, isolated_overlay_files):
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 20, 30)
    meta = FakeMetadata({1: {"status": "Ended", "first_air_date": "2020-01-01"}, 2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    tv_only = _enabled_config()
    tv_only["all_libraries"], tv_only["libraries"] = False, ["srv:2"]
    first = _engine(test_db, client, meta, tv_only, sources=sources)
    first.run()
    first.flush()
    assert set(_state_items(test_db)) == {"srv|20", "srv|30"} and len(client.uploads) == 2
    movies_only = _enabled_config()
    movies_only["all_libraries"], movies_only["libraries"] = False, ["srv:1"]
    second = _engine(test_db, client, meta, movies_only, sources=sources)
    stats = second.run()
    second.flush()
    assert stats["restored"] == 2 and _state_items(test_db) == {} and sorted(i for i, _ in client.uploads[2:]) == ["20", "30"]
    assert client.uploads[-1][1] == _flat_poster_bytes()  # the clean file, byte for byte
    assert _engine(test_db, client, meta, movies_only, sources=sources).run()["restored"] == 0  # nothing left to put back


def test_records_of_a_media_server_no_longer_configured_are_dropped_on_restore(test_db, isolated_overlay_files):
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 30)
    meta = FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    _seed_items(test_db, 7, 8, instance="gone")
    engine = _engine(test_db, client, meta, sources=sources)
    engine.run()
    engine.flush()
    off = ServerOverlayEngine(test_db, _off(_enabled_config()), metadata=meta, instances=[INSTANCE], library_configs=LIBRARIES,
                              client_factory=lambda inst: client, sources=sources)
    stats = off.restore_all()
    off.flush()
    # The one on the configured server is restored; the removed server's two are forgotten, not failed.
    assert (stats["restored"], stats["failed"]) == (1, 0) and _state_items(test_db) == {}
    assert ServerOverlayEngine(test_db, _off(_enabled_config()), metadata=meta, instances=[INSTANCE], library_configs=LIBRARIES,
                               client_factory=lambda inst: client, sources=sources).badged_count() == 0


def test_rebuild_mode_uploads_every_badge_again(test_db, isolated_overlay_files):
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 10, 20, 30)
    meta = FakeMetadata({
        1: {"status": "Returning Series", "first_air_date": "2020-01-01", "last_episode_to_air": {"air_date": _iso(-2)}},
        2: {"status": "Ended", "first_air_date": "2010-01-01"},
        9: {"status": "Released", "release_date": _iso(-3)},
    })
    first = _engine(test_db, client, meta, sources=sources)
    first.run()
    first.flush()
    assert len(client.uploads) == 3
    # full: recomposed, identical pixels, nothing uploaded. rebuild: uploaded regardless.
    full = _engine(test_db, client, meta, sources=sources)
    assert full.run(mode="full")["current"] == 3 and len(client.uploads) == 3
    full.flush()
    rebuild = _engine(test_db, client, meta, sources=sources)
    stats = rebuild.run(mode="rebuild")
    rebuild.flush()
    assert (stats["refreshed"], stats["current"], stats["applied"]) == (3, 0, 0)
    assert sorted(i for i, _ in client.uploads[3:]) == ["10", "20", "30"]


def test_identical_recompose_skips_the_upload(test_db, isolated_overlay_files):
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 30)
    meta = FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    first = _engine(test_db, client, meta, sources=sources)
    first.run()
    first.flush()
    # A settings change that does not alter this badge (a movie-only option) recomposes to
    # identical pixels → no upload, no new poster entry on the server.
    tweaked = _enabled_config(new_days=7)
    second = _engine(test_db, client, meta, tweaked, sources=sources)
    stats = second.run()
    second.flush()
    assert stats["current"] == 1 and stats["refreshed"] == 0 and len(client.uploads) == 1
    # Restoring uploads the clean file byte for byte.
    third = ServerOverlayEngine(test_db, _off(tweaked), metadata=meta, instances=[INSTANCE], library_configs=LIBRARIES,
                                client_factory=lambda inst: client, sources=sources)
    third.run()
    third.flush()
    assert client.uploads[-1] == ("30", _flat_poster_bytes())


def test_jellyfin_cache_clean_is_requested_after_uploads(test_db, isolated_overlay_files):
    client = FakeClient()
    client.server_type = "jellyfin"
    client.tasks_run = []
    client.run_scheduled_task = lambda key: client.tasks_run.append(key) or True
    sources = _sources(isolated_overlay_files, 30)
    meta = FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    engine = _engine(test_db, client, meta, sources=sources)
    engine.run()
    engine.flush()
    assert client.tasks_run == ["DeleteCacheFiles"]
    again = _engine(test_db, client, meta, sources=sources)
    again.run()  # nothing uploaded → nothing to clean
    assert client.tasks_run == ["DeleteCacheFiles"]


def test_restore_forgets_an_item_whose_poster_file_is_gone(test_db, isolated_overlay_files):
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 30)
    meta = FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    on = _engine(test_db, client, meta, sources=sources)
    on.run()
    on.flush()
    Path(sources[("srv", "30")]).unlink()
    off = ServerOverlayEngine(test_db, _off(_enabled_config()), metadata=meta, instances=[INSTANCE], library_configs=LIBRARIES,
                              client_factory=lambda inst: client, sources=sources)
    stats = off.run()
    off.flush()
    assert stats["failed"] == 1 and len(client.uploads) == 1  # nothing to upload back; the badge stays until a new poster lands
    assert _state_items(test_db) == {}


def test_restore_all_works_while_overlays_are_on(test_db, isolated_overlay_files):
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 30)
    meta = FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    on = _engine(test_db, client, meta, sources=sources)
    on.run()
    on.flush()
    still_on = _engine(test_db, client, meta, sources=sources)
    stats = still_on.restore_all()
    still_on.flush()
    assert stats["restored"] == 1 and not _has_badge(client.uploads[-1][1])
    assert _state_items(test_db) == {}


def test_server_engine_restores_everything_when_switched_off(test_db, isolated_overlay_files):
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 20, 30)
    meta = FakeMetadata({1: {"status": "Ended", "first_air_date": "2020-01-01"}, 2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    on = _engine(test_db, client, meta, sources=sources)
    on.run()
    on.flush()
    assert len(client.uploads) == 2
    off = ServerOverlayEngine(test_db, _off(normalize_config({"enabled": False, "types": {"status": {"enabled": True}}})),
                              metadata=meta, instances=[INSTANCE], library_configs=LIBRARIES, client_factory=lambda inst: client, sources=sources)
    stats = off.run()
    off.flush()
    assert stats["restored"] == 2 and len(client.uploads) == 4
    assert not any(_has_badge(raw) for _, raw in client.uploads[2:])
    assert _state_items(test_db) == {}


def test_build_server_engine_gates_on_master_switch_and_types(test_db, isolated_overlay_files):
    assert build_server_engine(test_db) is None
    cfg = normalize_config({"enabled": True, "types": {"status": {"enabled": False}}})
    test_db.add(Setting(key="overlays_config", value=json.dumps(cfg)))
    test_db.commit()
    assert build_server_engine(test_db) is None
    cfg["types"]["status"]["enabled"] = True
    test_db.query(Setting).filter(Setting.key == "overlays_config").update({"value": json.dumps(cfg)})
    test_db.commit()
    engine = build_server_engine(test_db)
    assert engine is not None and [t.key for t, _ in engine.active] == ["status"]
    narrowed = normalize_config({"all_libraries": False, "libraries": ["srv:2", " ", "srv:1"]})
    assert narrowed["all_libraries"] is False and narrowed["libraries"] == ["srv:1", "srv:2"]
    assert normalize_config({})["all_libraries"] is True


def test_context_from_server_items():
    show = _item(20, "show", "Live", 2020, tmdb=1, tvdb=7, library="2")
    season = _item(21, "season", "Season 1", None, parent="20", index=1, library="2")
    ctx = ItemContext.from_server_item(season, "srv", parent=show)
    assert (ctx.media_type, ctx.title, ctx.year, ctx.tvdb_id, ctx.season_number, ctx.rel_path) == ("show", "Live", 2020, 7, 1, "srv|21")
    assert ctx.library_id == "srv:2"
    movie = ItemContext.from_server_item(_item(10, "movie", "Film", 2026, tmdb=9), "srv")
    assert movie.media_type == "movie" and movie.tmdb_id == 9 and movie.library_id == "srv:1"
    # A Plex item carries the section path; a season without its own library takes the show's.
    plex_show = MediaServerItem(item_id="40", item_type="show", title="P", library_key="plex-a:/library/sections/3")
    assert ItemContext.from_server_item(plex_show, "Plex").library_id == "Plex:3"
    bare_season = MediaServerItem(item_id="41", item_type="season", title="Season 1", parent_id="40", index=1)
    assert ItemContext.from_server_item(bare_season, "Plex", parent=plex_show).library_id == "Plex:3"
    assert ItemContext.from_server_item(MediaServerItem(item_id="50", item_type="movie", title="X"), "Plex").library_id == ""


def test_config_overlays_normalize_and_name_each_library_once():
    cfg = normalize_config({"enabled": True, "types": {"status": {"enabled": True, "overlays": [
        {"name": "  Everything ", "all_libraries": True, "options": {"new_days": 30}},
        {"name": "TV", "libraries": ["srv:2", "srv:1", " "], "options": {"new_days": 7}},
        {"libraries": ["srv:2", "srv:3"], "placement": {"font_size": 70}},
        "junk",
    ]}}})
    overlays = cfg["types"]["status"]["overlays"]
    assert [o["name"] for o in overlays] == ["Everything", "TV", "Airing Status 3"]  # a blank name is filled in
    assert all(o["enabled"] is True for o in overlays)  # on unless switched off
    assert normalize_config({"types": {"status": {"overlays": [{"enabled": "false"}]}}})["types"]["status"]["overlays"][0]["enabled"] is False
    assert [o["all_libraries"] for o in overlays] == [True, False, False]
    assert [o["libraries"] for o in overlays] == [[], ["srv:1", "srv:2"], ["srv:3"]]  # a library is named by one overlay
    assert overlays[1]["options"]["new_days"] == 7 and overlays[1]["placement"]["font_size"] == get_type("status").default_placement()["font_size"]
    assert overlays[2]["placement"]["font_size"] == 70 and overlays[2]["options"]["new_days"] == 30
    assert normalize_config({})["types"]["status"]["overlays"] == []  # a type starts without overlays
    # Settings saved before overlays were named: one overlay for all libraries, called after the type.
    legacy = normalize_config({"enabled": True, "types": {"status": {"enabled": True, "options": {"new_days": 9}}}})
    assert legacy["types"]["status"]["overlays"] == [{**legacy["types"]["status"]["overlays"][0], "name": "Airing Status", "all_libraries": True, "libraries": []}]
    assert legacy["types"]["status"]["overlays"][0]["options"]["new_days"] == 9
    assert overlay_signature(overlays[1]) != overlay_signature(legacy["types"]["status"]["overlays"][0])  # an overlay edit re-composes like any settings change
    assert overlay_signature(overlays[1]) == overlay_signature({**overlays[1], "name": "renamed", "libraries": []})  # a name or library change draws nothing differently


def test_overlay_for_picks_the_named_library_then_all_libraries():
    pick = ServerOverlayEngine._overlay_for
    tcfg = {"overlays": [
        {"name": "All", "all_libraries": True, "libraries": []},
        {"name": "TV", "all_libraries": False, "libraries": ["srv:2"]},
    ]}
    assert pick(tcfg, "srv:2")["name"] == "TV"
    assert pick(tcfg, "srv:1")["name"] == "All"
    assert pick(tcfg, "Plex:/library/sections/2")["name"] == "All"  # another server's library 2 is not srv:2
    assert pick(tcfg, "")["name"] == "All"
    only_tv = {"overlays": [tcfg["overlays"][1]]}
    assert pick(only_tv, "srv:2")["name"] == "TV" and pick(only_tv, "srv:1") is None
    assert pick({"overlays": []}, "srv:1") is None
    # An overlay switched off is skipped: its library falls back to the all-libraries one, or to nothing.
    tv_off = {"overlays": [tcfg["overlays"][0], {**tcfg["overlays"][1], "enabled": False}]}
    assert pick(tv_off, "srv:2")["name"] == "All"
    assert pick({"overlays": [{**tcfg["overlays"][0], "enabled": False}]}, "srv:1") is None


def test_server_engine_uses_the_overlay_that_names_the_items_library(test_db, isolated_overlay_files):
    client = FakeClient()
    sources = _sources(isolated_overlay_files, 10, 20, 30)
    meta = FakeMetadata({
        1: {"status": "Returning Series", "first_air_date": "2020-01-01", "last_episode_to_air": {"air_date": _iso(-2)}},
        2: {"status": "Ended", "first_air_date": "2010-01-01"},
        9: {"status": "Released", "release_date": _iso(-3)},
    })
    # The TV library has its own overlay (wording, font size); everything else uses the all-libraries one.
    cfg = normalize_config({"enabled": True, "types": {"status": {"enabled": True, "overlays": [
        {"name": "Everywhere", "all_libraries": True},
        {"name": "TV", "libraries": ["srv:2"], "placement": {"font_size": 90}, "options": {"states": {"ended": {"text": "DONE"}}}},
    ]}}})
    engine = _engine(test_db, client, meta, cfg, sources=sources)
    stats = engine.run()
    engine.flush()
    items = _state_items(test_db)
    assert stats["applied"] == 3
    assert "status:ended|DONE|" in items["srv|30"]["sig"] and "ENDED" not in items["srv|30"]["sig"]
    assert "status:new_release|NEW RELEASE|" in items["srv|10"]["sig"]
    resolved, _, _, _ = engine._resolve(ItemContext.from_server_item(client.items["2"][1], "srv"))
    assert resolved[0][2]["font_size"] == 90  # the TV overlay's placement is what gets drawn
    assert engine._resolve(ItemContext.from_server_item(client.items["1"][0], "srv"))[0][0][2]["font_size"] == get_type("status").default_placement()["font_size"]
    # Seasons are listed when any overlay asks for them.
    assert engine._apply_to_seasons() is False
    cfg["types"]["status"]["overlays"][1]["options"]["apply_to_seasons"] = True
    assert _engine(test_db, client, meta, cfg, sources=sources)._apply_to_seasons() is True
    # Only a TV overlay: the movie library gets no status badge at all.
    tv_only = normalize_config({"enabled": True, "types": {"status": {"enabled": True, "overlays": [cfg["types"]["status"]["overlays"][1]]}}})
    second = _engine(test_db, client, FakeMetadata(meta.by_id), tv_only, sources=sources)
    stats = second.run()
    second.flush()
    assert stats["restored"] == 1 and "srv|10" not in _state_items(test_db) and "srv|30" in _state_items(test_db)


# ── API ──────────────────────────────────────────────────────────────────────

def test_overlays_api_types_and_config_roundtrip(client, test_db, isolated_overlay_files):
    types = client.get("/api/overlays/types").json()["types"]
    assert [t["key"] for t in types] == ["status", "rating", "content_rating", "streaming", "resolution", "audio", "edition"]
    cfg = client.get("/api/overlays/config").json()
    assert cfg["config"]["enabled"] is False and cfg["has_tmdb_key"] is False and cfg["badged_count"] == 0
    body = {"enabled": True, "all_libraries": False, "libraries": ["srv:1"],
            "types": {"status": {"enabled": True, "placement": {"vertical_offset": 120}, "options": {"new_days": 7}}}}
    saved = client.post("/api/overlays/config", json=body).json()["config"]
    assert saved["enabled"] is True and saved["all_libraries"] is False and saved["libraries"] == ["srv:1"]
    assert saved["types"]["status"]["overlays"][0]["placement"]["vertical_offset"] == 120
    assert saved["types"]["status"]["overlays"][0]["options"]["new_days"] == 7
    assert client.get("/api/overlays/config").json()["config"] == saved
    fonts = {f["id"]: f for f in client.get("/api/overlays/fonts").json()["fonts"]}
    assert "Bold" in fonts["RobotoCondensed-Variable"]["weights"]
    assert fonts["LiberationSans-Regular"]["weights"] == []


def test_saving_overlays_off_does_not_touch_the_servers(client, test_db, isolated_overlay_files, monkeypatch):
    submitted = []
    monkeypatch.setattr("api.overlays.job_queue", type("Q", (), {"submit": staticmethod(lambda *a: submitted.append(a))})())
    client.post("/api/overlays/config", json={"enabled": True, "types": {"status": {"enabled": True}}})
    _seed_items(test_db, 1, 2)
    resp = client.post("/api/overlays/config", json={"enabled": False, "types": {"status": {"enabled": True}}}).json()
    assert resp["badged_count"] == 2 and "restore_job_id" not in resp
    assert submitted == []  # restoring is the Remove overlays button's job, never a side effect of saving
    # …and that button's endpoint still works while off, because items are badged.
    assert client.post("/api/overlays/run", json={}).status_code == 200 and len(submitted) == 1


def test_overlays_api_preview_and_run(client, test_db, isolated_overlay_files, monkeypatch):
    resp = client.post("/api/overlays/preview", json={"type": "status", "variant": "airing:date",
                                                     "placement": {"vertical_align": "bottom"}, "options": {}})
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"
    x, y, w, h = (int(v) for v in resp.headers["x-overlay-rect"].split(","))
    assert w > 0 and h > 0 and y + h == 1500 - get_type("status").default_placement()["vertical_offset"]  # the type's own default (40), not the shared one
    assert resp.headers["x-overlay-text"].startswith("AIRING ")
    assert client.post("/api/overlays/preview", json={"type": "nope"}).status_code == 404
    # Other enabled overlays are drawn underneath, so the preview shows the whole composition;
    # the rect and text still belong to the one being edited.
    alone = client.post("/api/overlays/preview", json={"type": "status", "variant": "airing", "placement": {}, "options": {}})
    with_rating = client.post("/api/overlays/preview", json={
        "type": "status", "variant": "airing", "placement": {}, "options": {},
        "others": [{"type": "rating", "variant": "top", "placement": {}, "options": {}}, {"type": "nope"}, {"type": "status"}],
    })
    assert with_rating.status_code == 200 and with_rating.headers["x-overlay-rect"] == alone.headers["x-overlay-rect"]
    assert with_rating.headers["x-overlay-text"] == "AIRING"
    # The other layers that drew a badge come back with their rectangles (so they can be dragged too);
    # unknown types and the edited type itself are left out.
    rating_alone = client.post("/api/overlays/preview", json={"type": "rating", "variant": "top", "placement": {}, "options": {}})
    assert json.loads(with_rating.headers["x-overlay-layers"]) == [{"type": "rating", "rect": [int(v) for v in rating_alone.headers["x-overlay-rect"].split(",")]}]
    assert alone.headers["x-overlay-layers"] == "[]"
    # The text travels percent-encoded: a header carries plain ASCII only, and a state's text may be anything.
    accented = client.post("/api/overlays/preview", json={"type": "status", "variant": "airing", "placement": {}, "options": {"states": {"airing": {"text": "EN COURS · ÉTÉ"}}}})
    assert accented.status_code == 200 and accented.headers["x-overlay-text"] == "EN COURS %C2%B7 %C3%89T%C3%89"
    solo = Image.open(io.BytesIO(alone.content)).convert("RGB")
    both = Image.open(io.BytesIO(with_rating.content)).convert("RGB")
    assert solo.getpixel((940, 1440)) != both.getpixel((940, 1440))  # the rating badge, bottom right
    # The overlay being edited is switched off: the preview draws only the others.
    # Two banded overlays in one preview: the band is painted once, as dark as one alone.
    banded = {"back_enabled": True, "back_style": "band", "gradient_start": "#000000B3", "gradient_band_height": 300}
    one = client.post("/api/overlays/preview", json={"type": "status", "variant": "airing", "placement": banded, "options": {}})
    two = client.post("/api/overlays/preview", json={"type": "status", "variant": "airing", "placement": banded, "options": {},
                                                      "others": [{"type": "rating", "variant": "", "placement": banded, "options": {}}]})
    assert Image.open(io.BytesIO(two.content)).getpixel((500, 3)) == Image.open(io.BytesIO(one.content)).getpixel((500, 3))
    assert two.headers["x-overlay-rect"] == one.headers["x-overlay-rect"]  # the rect is still the edited overlay's
    off = client.post("/api/overlays/preview", json={"type": "status", "enabled": False})
    assert off.status_code == 200 and off.headers["x-overlay-rect"] == "0,0,0,0" and off.headers["x-overlay-text"] == ""

    assert client.post("/api/overlays/run", json={}).status_code == 400  # off and nothing badged
    assert client.post("/api/overlays/run", json={"mode": "restore"}).status_code == 400  # nothing badged to remove
    client.post("/api/overlays/config", json={"enabled": True, "types": {"status": {"enabled": True}}})
    submitted = {}
    monkeypatch.setattr("api.overlays.job_queue", type("Q", (), {"submit": staticmethod(lambda *a: submitted.setdefault("args", a))})())
    resp = client.post("/api/overlays/run", json={"mode": "full"})
    assert resp.status_code == 200 and resp.json()["job_id"] == submitted["args"][1]
    assert submitted["args"][2:] == (submitted["args"][1], False, "full")
    submitted.clear()
    assert client.post("/api/overlays/run", json={"mode": "rebuild"}).status_code == 200 and submitted["args"][4] == "rebuild"
    # Remove overlays while ON: an explicit restore run, allowed as soon as something is badged.
    _seed_items(test_db, 1)
    submitted.clear()
    resp = client.post("/api/overlays/run", json={"mode": "restore"})
    assert resp.status_code == 200 and submitted["args"][2:] == (submitted["args"][1], False, "restore")


def test_overlays_api_preview_with_saved_borders(client, test_db, isolated_overlay_files, monkeypatch):
    import api.poster_manager as pm

    flat = Image.new("RGB", (1000, 1500), (90, 90, 90))
    monkeypatch.setattr(pm, "_load_preview_base", lambda _db: flat.copy())

    def corner(base: str):
        resp = client.post("/api/overlays/preview", json={"type": "status", "variant": "ended", "base": base})
        assert resp.status_code == 200
        with Image.open(io.BytesIO(resp.content)) as img:
            return img.convert("RGB").getpixel((0, 0))

    assert corner("bordered") == (90, 90, 90)
    test_db.add(Setting(key="border_replacer_colors", value=json.dumps(["#00FF00"])))
    test_db.commit()
    assert all(abs(a - b) <= 3 for a, b in zip(corner("bordered"), (0, 255, 0)))
    assert corner("plain") == (90, 90, 90)


def test_workflow_has_no_overlays_step(client, test_db):
    """Badges ride on Asset Upload now; a saved config that still names the old step is ignored."""
    assert "overlays" not in client.get("/api/posterflow/flow/config").json()
    body = {**client.get("/api/posterflow/flow/config").json(), "overlays": {"enabled": True, "stop_on_error": True}}
    assert client.post("/api/posterflow/flow/config", json=body).json()["success"] is True
    assert "overlays" not in client.get("/api/posterflow/flow/config").json()


def test_overlays_job_is_a_noop_when_off_and_nothing_badged(test_db, isolated_overlay_files, monkeypatch):
    from models.job import JOB_TYPE_OVERLAYS, create_job
    from modules import overlays as overlays_mod

    monkeypatch.setattr(overlays_mod, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None)
    job = create_job(test_db, job_type=JOB_TYPE_OVERLAYS, message="test")
    overlays_mod.run_overlays_background_job(job.id)
    test_db.refresh(job)
    assert job.status == "completed" and "nothing to do" in (job.message or "")


class PlexNative:
    """Enough of a plexapi object: a guid, and posters() that lists uploads by content SHA1."""

    def __init__(self, guid, client, item_id):
        self.guid, self._client, self._item_id = guid, client, item_id

    def posters(self):
        class P:
            def __init__(self, key, selected):
                self.ratingKey, self.key, self.selected = key, key, selected
        ours = [raw for iid, raw in self._client.uploads if iid == self._item_id]
        if not ours:
            return []
        import hashlib as _h
        return [P(f"upload://posters/{_h.sha1(ours[-1]).hexdigest()}", True)]


class PlexLike(FakeClient):
    """A Plex that files every upload in the item's bundle under the SHA1 of the bytes and
    selects it for the item, like the real one. An item whose selected upload is gone from its
    bundle shows no poster."""

    def __init__(self, root):
        super().__init__()
        self.root = root
        self.selected = {}  # item id → the sha of the upload Plex shows for it

    def upload_image(self, item, kind, filepath):
        ok = super().upload_image(item, kind, filepath)
        raw = Path(filepath).read_bytes()
        sha = hashlib.sha1(raw).hexdigest()
        folder = plex_uploads_dir(str(self.root), item)
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / sha).write_bytes(raw)
        self.selected[item.item_id] = sha
        return ok

    def _shown(self, item):
        """The upload Plex shows: the one picked for the item, else its last upload."""
        return self.selected.get(item.item_id) or next((hashlib.sha1(raw).hexdigest() for iid, raw in reversed(self.uploads) if iid == item.item_id), "")

    def selected_poster_key(self, item):
        sha = self._shown(item)
        if not sha:
            return "metadata://posters/agent"  # nothing uploaded: the provider's poster
        folder = plex_uploads_dir(str(self.root), item)
        if folder is not None and folder.is_dir() and not (folder / sha).is_file():
            return NO_IMAGE_SELECTED
        return f"upload://posters/{sha}"

    def selected_image_key(self, item, kind):
        return self.selected_poster_key(item) if kind == "poster" else ""


def _two_copies_of_done(test_db, tmp, **sources_4k):
    """'Done' in two libraries (TV and 4K TV): two items, ONE Plex bundle. Returns the client,
    the shared posters folder and an engine factory."""
    root = tmp / "plexmeta"
    client = PlexLike(root)
    client.items["3"] = [_item(31, "show", "Done", 2010, tmdb=2, tvdb=2, library="3")]  # the 4K copy
    for lib in list(client.items.values()) + list(client.seasons.values()):
        for it in lib:
            it.client = client
            it.guid = {"30": "plex://show/abc", "31": "plex://show/abc", "20": "plex://show/live"}.get(it.item_id)
    bundle = plex_bundle_dir(str(root), "plex://show/abc", "show") / "Uploads" / "posters"
    bundle.mkdir(parents=True)
    save_plex_metadata_paths(test_db, {"srv": str(root)})
    cfg = _enabled_config()
    cfg["libraries"] = ["srv:2", "srv:3"]; cfg["all_libraries"] = False
    libraries = [{"instance_name": "srv", "libraries": LIBRARIES[0]["libraries"] + [{"key": "3", "title": "4K TV", "type": "show", "enabled": True}]}]
    meta = FakeMetadata({1: {"status": "Ended", "first_air_date": "2020-01-01"}, 2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    sources = {**_sources(tmp, 20, 30), **_sources(tmp, 31, **sources_4k)}

    def engine():
        return ServerOverlayEngine(test_db, cfg, metadata=meta, instances=[INSTANCE], library_configs=libraries,
                                   client_factory=lambda inst: client, sources=sources)
    return client, bundle, engine, sources


def _run(engine, dry_run=False):
    e = engine()
    stats = e.run(dry_run=dry_run)
    e.flush()
    return stats


def test_a_bundle_two_libraries_share_keeps_what_every_copy_shows(test_db, isolated_overlay_files):
    """The same show in two libraries (TV and 4K TV) is two items with ONE Plex bundle. A prune
    there keeps what every copy shows, asked of Plex at that moment, so old uploads still go but
    no copy loses its poster."""
    client, bundle, engine, _sources_ = _two_copies_of_done(test_db, isolated_overlay_files, raw=_red_poster_bytes())  # the 4K copy has its own poster
    (bundle / "junk").write_bytes(b"x" * 100)  # an old upload neither copy shows

    stats = _run(engine)
    hd, uhd = client.selected["30"], client.selected["31"]
    assert hd != uhd and stats["plex_pruned"] == 1 and stats["plex_shared"] == 0
    assert {f.name for f in bundle.iterdir()} == {hd, uhd}  # the old upload went; each copy's badge stayed
    assert _run(engine)["current"] == 3  # the next run confirms every badge is shown

    # What happened on a real server: another tool put one plain poster on both copies. Badging
    # the first copy again must leave that poster in the folder while the second still shows it.
    plain = hashlib.sha1(b"plain").hexdigest()
    (bundle / plain).write_bytes(b"plain")
    for item_id in ("30", "31"):
        client.selected[item_id] = plain
        client.bump_thumb_only(item_id)
    stats = _run(engine)
    assert (client.selected["30"], client.selected["31"]) == (hd, uhd) and stats["refreshed"] == 2
    assert stats["reuploaded"] == 0  # neither copy was ever left without a poster
    assert {f.name for f in bundle.iterdir()} == {hd, uhd}  # the plain poster went once neither copy showed it

    # A badge file deleted from the bundle: that copy gets it uploaded again, the other is untouched.
    _run(engine)
    uploads = len(client.uploads)
    (bundle / hd).unlink()
    stats = _run(engine)
    assert stats["reuploaded"] == 1 and [iid for iid, _ in client.uploads[uploads:]] == ["30"] and {f.name for f in bundle.iterdir()} == {hd, uhd}
    # Plex not saying what the other copy shows leaves the shared bundle alone.
    (bundle / "junk").write_bytes(b"x")
    client.selected_image_key = lambda item, kind: ""
    stats = _run(engine)
    assert stats["plex_shared"] >= 1 and (bundle / "junk").is_file()


def test_a_badge_plex_shows_no_poster_for_goes_back(test_db, isolated_overlay_files):
    """Plex can list an item's posters and select none of them, so it shows no poster (its
    selected upload was deleted): that is an answer, not "can't tell". The badge is uploaded
    again, for a title in one library and for a copy of a title in two, at upload time too."""
    client, bundle, engine, sources = _two_copies_of_done(test_db, isolated_overlay_files)
    _run(engine)
    assert _run(engine)["current"] == 3  # the server hints are confirmed
    badge_live, badge_done = client.selected["20"], client.selected["31"]
    assert client.selected["30"] == badge_done and [f.name for f in bundle.iterdir()] == [badge_done]  # one file for both copies
    for item_id in ("20", "31"):
        client.selected[item_id] = "an-upload-deleted-since"
        client.bump_thumb_only(item_id)
    uploads = len(client.uploads)
    stats = _run(engine, dry_run=True)
    assert stats["refreshed"] == 2 and len(client.uploads) == uploads  # a dry run only says so
    stats = _run(engine)
    assert stats["reuploaded"] == 2 and [iid for iid, _ in client.uploads[uploads:]] == ["20", "31"]
    assert (client.selected["20"], client.selected["31"]) == (badge_live, badge_done)

    # At upload time the same answer sends the badge up again.
    _run(engine)
    live = next(i for i in client.items["2"] if i.item_id == "20")
    client.selected["20"] = "an-upload-deleted-since"
    client.bump_thumb_only("20")
    assert engine().badge_stale("srv", client._current(live), sources[("srv", "20")]) is True


def test_plex_bundle_pruning_keeps_the_poster_plex_shows(test_db, isolated_overlay_files):
    root = isolated_overlay_files / "plexmeta"
    client = PlexLike(root)
    for lib in list(client.items.values()) + list(client.seasons.values()):
        for it in lib:
            it.client = client
            it.guid = {"30": "plex://show/abc", "20": "plex://show/live"}.get(it.item_id)
    sources = _sources(isolated_overlay_files, 20, 30)
    bundle = plex_bundle_dir(str(root), "plex://show/abc", "show") / "Uploads" / "posters"
    bundle.mkdir(parents=True)
    clean = _flat_poster_bytes()
    (bundle / hashlib.sha1(clean).hexdigest()).write_bytes(clean)  # the clean poster, uploaded earlier
    for junk in ("junk1", "junk2"):
        (bundle / junk).write_bytes(b"x" * 1000)                   # superseded uploads from the past
    meta = FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    cfg = _enabled_config()
    save_plex_metadata_paths(test_db, {"srv": str(root)})
    cfg["libraries"] = ["srv:2"]; cfg["all_libraries"] = False

    engine = _engine(test_db, client, meta, cfg, sources=sources)
    stats = engine.run()
    engine.flush()
    badged_sha = hashlib.sha1(client.uploads[-1][1]).hexdigest()
    assert stats["plex_pruned"] == 3 and stats["plex_freed_bytes"] == 2000 + len(clean)  # only the badge stays
    assert [f.name for f in bundle.iterdir()] == [badged_sha]
    (bundle / "junk_later").write_bytes(b"z" * 10)                 # something old turning up between runs
    again = _engine(test_db, client, meta, cfg, sources=sources)
    stats = again.run()
    again.flush()
    assert stats["plex_pruned"] == 1 and stats["plex_freed_bytes"] == 10 and [f.name for f in bundle.iterdir()] == [badged_sha]
    # A wrong path prunes nothing.
    save_plex_metadata_paths(test_db, {"srv": str(isolated_overlay_files / "nowhere")})
    (bundle / "junk3").write_bytes(b"y")
    third = _engine(test_db, client, meta, cfg, sources=sources)
    assert third.run()["plex_pruned"] == 0 and (bundle / "junk3").is_file()
    save_plex_metadata_paths(test_db, {"srv": str(root)})
    # Switched off: the bundle is left alone and no folder is looked at. An unusable folder is
    # noticed once, before any bundle is tried.
    import services.overlays.server as server_module
    calls = []
    original = server_module.prune_plex_uploads
    server_module.prune_plex_uploads = lambda *a, **kw: calls.append(a) or original(*a, **kw)
    try:
        off_cfg = dict(cfg)
        off_cfg["prune_plex_uploads"] = False
        quiet = _engine(test_db, client, meta, off_cfg, sources=sources)
        assert quiet.run()["plex_pruned"] == 0 and (bundle / "junk3").is_file() and calls == []
        save_plex_metadata_paths(test_db, {"srv": str(isolated_overlay_files / "nowhere")})
        unready = _engine(test_db, client, meta, cfg, sources=sources)
        assert unready.run()["plex_pruned"] == 0 and calls == []
    finally:
        server_module.prune_plex_uploads = original
    save_plex_metadata_paths(test_db, {"srv": str(root)})
    # Restoring uploads the clean file and keeps only that.
    fourth = ServerOverlayEngine(test_db, _off(cfg), metadata=meta, instances=[INSTANCE], library_configs=LIBRARIES,
                                 client_factory=lambda inst: client, sources=sources)
    fourth.run()
    fourth.flush()
    assert [f.name for f in bundle.iterdir()] == [hashlib.sha1(clean).hexdigest()]
    assert normalize_plex_metadata_paths({"srv": "/x/Metadata/", "": "/y", "z": ""}) == {"srv": "/x/Metadata"}
    # An item that never gets a badge is tidied too: only the poster Plex currently shows stays.
    client.uploads.append(("20", clean))                            # Plex shows this upload for it
    live_bundle = plex_bundle_dir(str(root), "plex://show/live", "show") / "Uploads" / "posters"
    live_bundle.mkdir(parents=True)
    (live_bundle / hashlib.sha1(clean).hexdigest()).write_bytes(clean)
    (live_bundle / "stale_upload").write_bytes(b"q" * 5)
    fifth = _engine(test_db, client, meta, cfg, sources=sources)
    stats = fifth.run()
    assert stats["plex_pruned"] >= 1 and [f.name for f in live_bundle.iterdir()] == [hashlib.sha1(clean).hexdigest()]


def test_plex_bundle_dir_handles_editions(tmp_path):
    import hashlib as _h

    class N:
        guid = "plex://movie/5d776825151a60001f24a5d2"

    plain = MediaServerItem(item_id="1", item_type="movie", title="Amadeus", native=N())
    edition = MediaServerItem(item_id="1", item_type="movie", title="Amadeus", native=N(), edition_title="Anniversary Edition")
    bundle = lambda item: plex_uploads_dir(str(tmp_path), item).parents[1].name  # noqa: E731
    assert bundle(plain) == _h.sha1(N.guid.encode()).hexdigest()[1:] + ".bundle"
    assert bundle(edition) == "fe607e2ee9fc75c7566de130f144adc337d5f2d.bundle"  # Plex's own hash for this edition


# ── finale markers (Sonarr → TheTVDB → TMDB inference) ───────────────────────

def _finales(last_season, last_episode, days_ago, finale, seasons=None, source="tvdb"):
    return {"source": source, "last": {"season": last_season, "episode": last_episode, "air_date": _iso(-days_ago), "finale": finale},
            "seasons": seasons or {}}


def test_status_reads_finale_markers(status, show_ctx):
    opts = status.default_options()
    base = dict(status="Returning Series", first_air_date="2010-01-01", last_episode_to_air={"air_date": _iso(-3), "season_number": 4, "episode_number": 10})
    r = lambda finales=None, **k: status.resolve(show_ctx, opts, {"tmdb": {**base, **k}, **({"finales": finales} if finales else {})})  # noqa: E731
    # The finale aired three days ago: season complete, dated with the finale's air date.
    done = r(_finales(4, 10, 3, "season"))
    assert done.key == "season_ended" and done.text == f"FINALE AIRED {format_date(TODAY - timedelta(days=3), 'MM/DD', True)}"
    # A premiere inside the return window beats it.
    assert r(_finales(4, 10, 3, "season"), next_episode_to_air={"air_date": _iso(20)}).key == "returning"
    assert r(_finales(4, 10, 3, "season"), next_episode_to_air={"air_date": _iso(200)}).key == "season_ended"
    # Mid-season finale: on a break, with the return date when known.
    mid = r(_finales(4, 5, 3, "midseason"), next_episode_to_air={"air_date": _iso(60)})
    assert mid.key == "midseason" and mid.text == f"RETURNS {format_date(TODAY + timedelta(days=60), 'MM/DD', True)}"
    assert r(_finales(4, 5, 3, "midseason")).text == "MID-SEASON FINALE"
    # A series finale counts as ended even while TMDB still says the show is returning.
    assert r(_finales(4, 10, 3, "series")).key == "ended"
    # No marker on the last aired episode: the usual airing/returning rules.
    assert r(_finales(4, 9, 3, None)).key == "airing"
    # The finale badge lasts finale_days, then the show reads as returning again.
    old = dict(status="Returning Series", first_air_date="2010-01-01", last_episode_to_air={"air_date": _iso(-45), "season_number": 4, "episode_number": 10})
    assert status.resolve(show_ctx, opts, {"tmdb": old, "finales": _finales(4, 10, 45, "season")}).key == "returning"
    assert status.resolve(show_ctx, opts, {"tmdb": old, "finales": _finales(4, 10, 45, "midseason")}).key == "returning"
    longer = status.normalize_options({"finale_days": 60})
    assert status.resolve(show_ctx, longer, {"tmdb": old, "finales": _finales(4, 10, 45, "season")}).key == "season_ended"
    assert status.normalize_options({"finale_days": "99999"})["finale_days"] == 3650 and status.default_options()["finale_days"] == 30


def test_status_infers_a_season_finale_from_tmdb_episode_counts(status, show_ctx):
    opts = status.default_options()
    r = lambda **k: status.resolve(show_ctx, opts, _show_meta(status="Returning Series", first_air_date="2010-01-01", **k))  # noqa: E731
    closed = r(last_episode_to_air={"air_date": _iso(-3), "season_number": 2, "episode_number": 10}, season_episode_counts={"1": 8, "2": 10})
    assert closed.key == "season_ended"
    assert r(last_episode_to_air={"air_date": _iso(-3), "season_number": 2, "episode_number": 9}, season_episode_counts={"2": 10}).key == "airing"
    assert r(last_episode_to_air={"air_date": _iso(-3), "season_number": 2, "episode_number": 10}).key == "airing"  # no counts: no guess


def test_season_posters_mark_finished_seasons_complete(status):
    opts = status.normalize_options({"apply_to_seasons": True})
    tmdb = {"status": "Returning Series", "first_air_date": "2010-01-01", "last_episode_to_air": {"air_date": _iso(-3), "season_number": 3, "episode_number": 4},
            "next_episode_to_air": {"air_date": _iso(4)}}
    finales = _finales(3, 4, 3, None, seasons={"1": {"finale_air_date": "2011-05-01"}, "2": {"finale_air_date": "2012-05-01"}})
    s1 = status.resolve(_ctx(season=1), opts, {"tmdb": tmdb, "finales": finales})
    assert s1.key == "season_ended" and s1.text == "FINALE AIRED 5/1"
    assert status.resolve(_ctx(season=3), opts, {"tmdb": tmdb, "finales": finales}).key == "airing"   # the running season follows the show
    # With episode counts only, earlier seasons are still complete (undated).
    counted = {**tmdb, "season_episode_counts": {"1": 8, "2": 8, "3": 10}}
    s2 = status.resolve(_ctx(season=2), opts, {"tmdb": counted})
    assert s2.key == "season_ended" and s2.text == "SEASON COMPLETE"


def test_finale_lookup_tries_sonarr_then_tvdb():
    from services.overlays.finales import FinaleLookup, summarize

    class FakeSonarr:
        url = "http://sonarr"

        def __init__(self, series, episodes, v4=True):
            self.series, self.episodes, self.v4, self.calls = series, episodes, v4, []

        def make_get_request(self, endpoint):
            self.calls.append(endpoint)
            if endpoint.endswith("/api/v3/series"):
                return self.series
            rows = [dict(e) for e in self.episodes]
            if not self.v4:
                for r in rows:
                    r.pop("finaleType", None)
            return rows

    sonarr = FakeSonarr([{"id": 7, "tvdbId": 383203, "tmdbId": 97546}],
                        [{"seasonNumber": 1, "episodeNumber": 9, "airDate": _iso(-10), "finaleType": None},
                         {"seasonNumber": 1, "episodeNumber": 10, "airDate": _iso(-3), "finaleType": "season"},
                         {"seasonNumber": 0, "episodeNumber": 1, "airDate": _iso(-1), "finaleType": "season"}])  # specials are ignored
    tvdb_calls = []

    def tvdb_get(path, params):
        tvdb_calls.append((path, params["page"]))
        return {"episodes": [{"seasonNumber": 1, "number": 10, "aired": _iso(-3), "finaleType": "midseason"}]}

    lookup = FinaleLookup([{"name": "main", "url": "http://sonarr", "api_key": "k"}], ("key", ""), arr_factory=lambda inst: sonarr, tvdb_get=tvdb_get)
    rec = lookup.finales(tvdb_id=383203, tmdb_id=None)
    assert rec["source"] == "sonarr:main" and rec["last"] == {"season": 1, "episode": 10, "air_date": _iso(-3), "finale": "season"}
    assert rec["seasons"] == {"1": {"finale_air_date": _iso(-3)}} and tvdb_calls == []
    assert lookup.finales(tvdb_id=None, tmdb_id=97546)["source"] == "sonarr:main"   # found by its TMDB id too
    # Not in Sonarr: TheTVDB answers.
    rec = lookup.finales(tvdb_id=1, tmdb_id=None)
    assert rec["source"] == "tvdb" and rec["last"]["finale"] == "midseason" and tvdb_calls == [("/series/1/episodes/default", 0)]
    # Sonarr v3 carries no markers: skipped, TheTVDB answers.
    v3 = FinaleLookup([{"name": "old", "url": "http://sonarr", "api_key": "k"}], ("key", ""),
                      arr_factory=lambda inst: FakeSonarr(sonarr.series, sonarr.episodes, v4=False), tvdb_get=tvdb_get)
    assert v3.finales(tvdb_id=383203, tmdb_id=None)["source"] == "tvdb"
    # Nothing configured, or a show nobody has: None (the status type falls back to TMDB counts).
    assert FinaleLookup([], ("", "")).finales(tvdb_id=383203, tmdb_id=None) is None
    assert FinaleLookup([], ("key", ""), tvdb_get=lambda path, params: {"episodes": []}).finales(tvdb_id=5, tmdb_id=None) is None
    assert summarize([], TODAY, "tvdb")["last"] is None


def test_metadata_provider_attaches_finales_for_live_shows(test_db, isolated_overlay_files, monkeypatch):
    class FakeLookup:
        def __init__(self):
            self.calls = 0

        def finales(self, *, tvdb_id, tmdb_id, today=None):
            self.calls += 1
            return _finales(2, 8, 2, "season")

    def fake_get(url, params=None, timeout=None):
        if url.endswith("/tv/1"):
            return _Resp({"name": "Live", "status": "Returning Series", "first_air_date": "2020-01-01",
                          "last_episode_to_air": {"air_date": _iso(-2), "season_number": 2, "episode_number": 8},
                          "seasons": [{"season_number": 0, "episode_count": 3}, {"season_number": 1, "episode_count": 8}, {"season_number": 2, "episode_count": 8}]})
        if url.endswith("/tv/2"):
            return _Resp({"name": "Done", "status": "Ended", "first_air_date": "2010-01-01"})
        return _Resp({}, 404)

    lookup = FakeLookup()
    t0 = time.time()
    provider = MetadataProvider("key", now=t0, db=test_db, finales=lookup)
    monkeypatch.setattr(provider._session, "get", fake_get)
    live = provider.for_item(_ctx("show", "Live", 2020, tmdb=1, tvdb=9))
    assert live["finales"]["last"]["finale"] == "season" and "finales via tvdb" in live["lookup"]
    assert live["tmdb"]["season_episode_counts"] == {"1": 8, "2": 8}  # specials dropped
    provider.for_item(_ctx("show", "Live", 2020, tmdb=1, tvdb=9))
    assert lookup.calls == 1  # once per run
    assert "finales" not in provider.for_item(_ctx("show", "Done", 2010, tmdb=2, tvdb=8))  # settled shows are not looked up
    provider.flush()
    later = MetadataProvider("key", now=t0 + 3600, db=test_db, finales=lookup)
    monkeypatch.setattr(later._session, "get", fake_get)
    later.for_item(_ctx("show", "Live", 2020, tmdb=1, tvdb=9))
    assert lookup.calls == 2  # a new run re-asks (the markers can change between runs)


def test_every_type_ends_its_cards_with_the_seasons_toggle():
    # The season toggle is its own card, the last one, on every type that badges seasons (the page
    # draws sections in schema order); a movies-only type has no Seasons card at all.
    seen = set()
    for otype in all_types().values():
        fields = [f for f in otype.schema() if f["kind"] != "states"]
        if "season" not in otype.targets:
            assert not any(f.get("section") == "Seasons" or f["key"] == "apply_to_seasons" for f in fields), otype.key
            seen.add(otype.key)
            continue
        assert fields[-1]["key"] == "apply_to_seasons" and fields[-1]["section"] == "Seasons" and fields[-1]["kind"] == "toggle", otype.key
        assert [f["key"] for f in fields if f.get("section") == "Seasons"] == ["apply_to_seasons"], otype.key
    assert seen == {"edition"}


# ── Content rating ───────────────────────────────────────────────────────────

def test_content_rating_type_describe_and_variants():
    cr = get_type("content_rating")
    desc = cr.describe()
    assert desc["key"] == "content_rating" and desc["needs"] == [] and desc["hidden_placement"] == ["text"]  # an image badge: no text to style
    assert desc["default_placement"]["back_enabled"] is False  # nothing behind the badge until asked for
    placement = desc["default_placement"]
    assert (placement["horizontal_align"], placement["horizontal_offset"], placement["vertical_align"], placement["vertical_offset"]) == ("left", 35, "bottom", 35)
    assert placement["back_style"] == "band"  # off by default; switching it on gives the edge band first
    assert (placement["back_color"], placement["back_padding"], placement["back_radius"], placement["back_min_width"], placement["back_max_width"]) == ("#000000FF", 6, 10, 130, 130)  # the box, when picked
    tables = [f for f in desc["schema"] if f["kind"] == "states"]
    assert [t["key"] for t in tables] == ["movie", "tv"]
    assert [i["key"] for i in tables[0]["items"]] == ["g", "pg", "pg-13", "r", "nc-17", "nr"]
    assert [i["key"] for i in tables[1]["items"]] == ["tv-y", "tv-g", "tv-pg", "tv-14", "tv-ma", "tv-nr"]
    assert [f["key"] for f in tables[0]["items"][0]["fields"]] == ["matches"]  # the badge is the file: no text or color per row
    assert [t["item_label"] for t in tables] == ["Rating", "Rating"]
    opts = cr.default_options()
    assert [v["key"] for v in cr.variants(opts)][:3] == ["movie:g", "movie:pg", "movie:pg-13"]
    assert cr.variants(opts)[-1] == {"key": "tv:tv-nr", "label": "NR (TV)", "group": "tv", "item": "tv-nr"}
    assert opts["badge_set"] == "color" and opts["badge_width"] == 130 and "style" not in opts  # the color boxes, a little over their own 119 px, no outline styles
    assert [f["key"] for f in desc["schema"] if f.get("section") == "Badge"] == ["badge_set", "badge_width", "badge_unrated"]
    badge_set = next(f for f in desc["schema"] if f["key"] == "badge_set")
    assert [(o["value"], o["label"]) for o in badge_set["options"]] == [("color", "Color boxes"), ("white", "Transparent")]
    uploads = next(f for f in desc["schema"] if f["kind"] == "images")
    assert (uploads["set_option"], uploads["noun"], uploads["section"]) == ("badge_set", "badge", "Custom badges")
    assert cr.image_items() == [("g", "G"), ("pg", "PG"), ("pg-13", "PG-13"), ("r", "R"), ("nc-17", "NC-17"), ("nr", "NR"), ("tv-y", "TV-Y"), ("tv-g", "TV-G"), ("tv-pg", "TV-PG"), ("tv-14", "TV-14"), ("tv-ma", "TV-MA"), ("tv-nr", "NR")]
    # The badge is the rating's file in the chosen set, as it is.
    plain = cr.resolve_variant("movie:r", opts)
    assert (plain.key, plain.text, plain.box, plain.back_color, plain.placement_overrides) == ("movie:r", "R", None, None, {})
    assert plain.image.endswith("/content_rating/color/r.png") and plain.image_width == 130 and plain.image_effect is None
    assert len(plain.image_stamp) == 8 and plain.signature == f"movie:r|R|||r.png@{plain.image_stamp}|130"
    white = cr.resolve_variant("movie:r", {**opts, "badge_set": "white"})
    assert white.image.endswith("/content_rating/white/r.png") and white.signature != plain.signature
    wide = cr.resolve_variant("tv:tv-ma", {**opts, "badge_width": 200})
    assert wide.image.endswith("/color/tv-ma.png") and wide.image_width == 200 and wide.image_effect is None
    assert cr.resolve_variant("tv:tv-nr", opts).image.endswith("/color/tv-nr.png") and cr.resolve_variant("movie:nr", opts).image.endswith("/color/nr.png")  # each table's own NR
    assert cr.resolve_variant("junk", opts).key == "movie:pg-13"


def test_content_rating_resolves_the_servers_rating_through_both_tables():
    cr = get_type("content_rating")
    opts = cr.default_options()

    def rated(kind, value, season=None):
        ctx = _ctx(kind=kind, title="X", season=season)
        ctx.content_rating = value
        return ctx

    assert cr.resolve(rated("movie", "R"), opts, {}).key == "movie:r"
    assert cr.resolve(rated("movie", "TV-14"), opts, {}).key == "movie:pg-13"  # a TV rating on a movie maps to the movie bucket
    assert cr.resolve(rated("show", "PG-13"), opts, {}).key == "tv:tv-14"      # and the other way round
    assert cr.resolve(rated("movie", "gb/15"), opts, {}).key == "movie:pg-13"
    assert cr.resolve(rated("show", "tv-ma"), opts, {}).key == "tv:tv-ma"       # not case-sensitive
    assert cr.resolve(rated("movie", "de/16"), opts, {}) is None                # unknown value: no badge
    assert cr.resolve(rated("movie", "Not Rated"), opts, {}).key == "movie:nr"
    assert cr.resolve(rated("movie", None), opts, {}).key == "movie:nr"         # unrated → NR by default
    assert cr.resolve(rated("show", ""), opts, {}).key == "tv:tv-nr"
    assert cr.resolve(rated("show", "Unrated"), opts, {}).key == "tv:tv-nr"
    assert cr.resolve(rated("movie", None), {**opts, "badge_unrated": False}, {}) is None
    assert cr.resolve(rated("show", "TV-MA", season=1), opts, {}) is None
    assert cr.resolve(rated("show", "TV-MA", season=1), {**opts, "apply_to_seasons": True}, {}).key == "tv:tv-ma"
    assert cr.resolve(rated("show", "TV-14"), opts, {}).image.endswith("/content_rating/color/tv-14.png")
    assert cr.resolve(rated("show", "TV-14"), {**opts, "badge_set": "white"}, {}).image.endswith("/content_rating/white/tv-14.png")
    # The Values are the user's: a rating listed on an earlier row lands there.
    custom = {**opts, "tv": {**opts["tv"], "tv-pg": {**opts["tv"]["tv-pg"], "matches": "TV-PG, TV-14"}}}
    assert cr.resolve(rated("show", "TV-14"), custom, {}).key == "tv:tv-pg"
    opts["movie"]["r"]["enabled"] = False
    assert cr.resolve(rated("movie", "R"), opts, {}) is None


def test_content_rating_options_normalize():
    cr = get_type("content_rating")
    opts = cr.normalize_options({"badge_set": "neon", "style": "outline", "badge_width": "5000", "badge_unrated": "false",
                                 "movie": {"r": {"matches": " R, 18 ", "enabled": "false", "text": "ignored"}}, "tv": "junk"})
    assert opts["badge_set"] == "color" and opts["badge_width"] == 1000 and opts["badge_unrated"] is False  # unknown choices → the defaults
    assert "style" not in opts and cr.normalize_options({"badge_set": "white"})["badge_set"] == "white"
    assert cr.normalize_options({"badge_width": 0})["badge_width"] == 20
    assert opts["movie"]["r"] == {"enabled": False, "matches": "R, 18"}
    assert opts["tv"]["tv-14"] == cr.default_options()["tv"]["tv-14"]
    assert cr.normalize_options(None) == cr.default_options()


def test_bundled_content_rating_badges_exist_for_every_rating_in_both_sets(monkeypatch, tmp_path):
    from services.overlays import images
    from services.overlays.types import content_rating

    cr = get_type("content_rating")
    lib = content_rating.IMAGES
    assert (lib.name, lib.frame, lib.margin, lib.noun) == ("content_rating", (119, 60), 0, "badge")
    keys = [k for k, _l in cr.image_items()]
    for image_set in ("color", "white"):
        assert {f.name.removesuffix(".png") for f in (lib.bundled_dir / image_set).glob("*.png")} == set(keys)
        for key in keys:
            with Image.open(lib.path(key, image_set)) as img:  # the user's Kometa files: the box fills the frame
                assert img.size == (119, 60) and img.mode == "RGBA" and img.getchannel("A").getbbox() == (0, 0, 119, 60), (key, image_set)
            assert lib.source(key, image_set) == "bundled"
    with Image.open(lib.bundled_dir / "color" / "r.png") as c:
        assert c.getpixel((8, 30))[:3] == (240, 28, 36)  # the R box's red fill
        assert c.getpixel((0, 0))[3] == 0 and c.getpixel((0, 30))[3] == 255  # rounded corners, straight edges
        ink = c.convert("RGBA").getchannel("G").point(lambda v: 255 if v > 200 else 0).crop((6, 6, 113, 54)).getbbox()  # the white R inside the box
        assert abs((ink[1] + ink[3]) / 2 - 24) <= 1.5 and abs((ink[0] + ink[2]) / 2 - 53.5) <= 1.5  # lettering centered in the box
    with Image.open(lib.bundled_dir / "white" / "r.png") as w:
        assert all(px[:3] == (255, 255, 255) for px in w.get_flattened_data())  # the transparent badge is white only, at varying alpha
        assert w.getpixel((1, 30))[3] > 200 and w.getpixel((8, 30))[3] == 0  # the line along the edge stays, the fill is gone
    # A file under config/overlays/content_rating/<set>/ replaces the bundled one, per set.
    monkeypatch.setattr(images, "config_overlays_dir", lambda: tmp_path)
    (tmp_path / "content_rating" / "color").mkdir(parents=True)
    Image.new("RGBA", (10, 10)).save(tmp_path / "content_rating" / "color" / "r.png")
    assert lib.path("r", "color") == tmp_path / "content_rating" / "color" / "r.png" and lib.source("r", "color") == "custom"
    assert lib.path("r", "white") == lib.bundled_dir / "white" / "r.png" and lib.source("r", "white") == "bundled"
    assert cr.resolve_variant("movie:r", cr.default_options()).image == str(tmp_path / "content_rating" / "color" / "r.png")


def test_items_carry_the_servers_content_rating_into_the_context():
    show = _item(20, "show", "Live", 2020, tmdb=1, content_rating="TV-14")
    season = _item(21, "season", "Season 1", None, parent="20", index=1)
    assert ItemContext.from_server_item(show, "srv").content_rating == "TV-14"
    assert ItemContext.from_server_item(season, "srv", parent=show).content_rating == "TV-14"  # seasons take the show's
    assert ItemContext.from_server_item(_item(10, "movie", "Film", 2026, content_rating="  "), "srv").content_rating is None


def test_bundled_noto_serif_condensed_bold_resolves():
    from services.overlays.render import resolve_font_path
    from services.text_logo import list_fonts

    assert resolve_font_path("NotoSerif-CondensedBold").endswith("NotoSerif-CondensedBold.ttf")
    assert any(f["id"] == "NotoSerif-CondensedBold" and f["source"] == "bundled" for f in list_fonts())


def test_render_badge_min_width_gives_every_rating_the_same_box():
    from PIL import Image

    from services.overlays.base import DEFAULT_PLACEMENT, OverlayBadge, normalize_placement
    from services.overlays.render import measure_badge, render_badge

    poster = Image.new("RGB", (1000, 1500), (20, 20, 20))
    hug = dict(DEFAULT_PLACEMENT)
    _img, short_rect = render_badge(poster, OverlayBadge(key="r", text="R"), hug)
    _img, long_rect = render_badge(poster, OverlayBadge(key="nc", text="NC-17"), hug)
    assert short_rect[2] < long_rect[2]
    wide = {**DEFAULT_PLACEMENT, "back_min_width": 300}
    _img, short_rect = render_badge(poster, OverlayBadge(key="r", text="R"), wide)
    _img, long_rect = render_badge(poster, OverlayBadge(key="nc", text="NC-17"), wide)
    assert short_rect[2] == long_rect[2] == 300
    assert measure_badge(OverlayBadge(key="r", text="R"), wide)[2] == 300
    # The page's drag rect follows a badge's own overrides (a text-only badge hugs its text).
    assert measure_badge(OverlayBadge(key="r", text="R", placement_overrides={"back_enabled": False}), wide)[2] < 300
    assert normalize_placement({"back_min_width": 5000})["back_min_width"] == 1000
    assert normalize_placement({"back_min_width": "x"})["back_min_width"] == 0
    assert normalize_placement({"back_min_width": 160, "back_max_width": 120})["back_max_width"] == 160  # a cap below the floor is the floor
    assert normalize_placement({"back_min_width": 160, "back_max_width": 0})["back_max_width"] == 0


def test_render_badge_max_width_shrinks_only_the_text_that_overflows():
    from PIL import Image

    from services.overlays.base import DEFAULT_PLACEMENT, OverlayBadge
    from services.overlays.render import measure_badge, render_badge

    poster = Image.new("RGB", (1000, 1500), (20, 20, 20))
    free = dict(DEFAULT_PLACEMENT)
    capped = {**DEFAULT_PLACEMENT, "back_max_width": 120}
    _img, short_free = render_badge(poster, OverlayBadge(key="r", text="R"), free)
    _img, short_capped = render_badge(poster, OverlayBadge(key="r", text="R"), capped)
    assert short_capped == short_free  # fits: untouched
    _img, long_free = render_badge(poster, OverlayBadge(key="nc", text="NC-17"), free)
    _img, long_capped = render_badge(poster, OverlayBadge(key="nc", text="NC-17"), capped)
    assert long_free[2] > 120 and 100 <= long_capped[2] <= 120  # shrunk to fit the cap
    assert long_capped[3] == long_free[3] == short_free[3]      # same box height as every other badge
    assert abs(measure_badge(OverlayBadge(key="nc", text="NC-17"), capped)[2] - long_capped[2]) <= 1  # the page's rect follows (render floors, measure rounds)
    # With a floor and a cap at the same value every badge is exactly that wide.
    fixed = {**DEFAULT_PLACEMENT, "back_min_width": 120, "back_max_width": 120}
    assert render_badge(poster, OverlayBadge(key="r", text="R"), fixed)[1][2] == 120
    assert render_badge(poster, OverlayBadge(key="nc", text="NC-17"), fixed)[1][2] == 120
    # The widths belong to the backdrop's box: with the backdrop off they do nothing (the page grays them out).
    bare = {**DEFAULT_PLACEMENT, "back_enabled": False, "back_min_width": 90, "back_max_width": 90}
    plain_w = render_badge(poster, OverlayBadge(key="nc", text="NC-17"), {**DEFAULT_PLACEMENT, "back_enabled": False})[1][2]
    assert render_badge(poster, OverlayBadge(key="nc", text="NC-17"), bare)[1][2] == plain_w > 90


# ── streaming service logos ──────────────────────────────────────────────────

def test_streaming_type_describe_and_variants():
    from services.overlays.types.streaming import PROVIDER_DEFS

    st = get_type("streaming")
    desc = st.describe()
    assert desc["key"] == "streaming" and desc["needs"] == ["tmdb", "providers"] and desc["hidden_placement"] == ["text"]
    assert desc["default_placement"]["back_enabled"] is False and desc["default_placement"]["back_style"] == "band"
    assert desc["targets"] == ["movie", "show", "season"]
    assert desc["default_placement"]["horizontal_align"] == "center" and desc["default_placement"]["vertical_offset"] == 30
    table = next(f for f in desc["schema"] if f["kind"] == "states")
    assert table["key"] == "providers" and table["item_label"] == "Service"
    assert [i["key"] for i in table["items"]] == [d[0] for d in PROVIDER_DEFS]
    assert [f["key"] for f in table["items"][0]["fields"]] == ["ids", "priority"]
    opts = st.default_options()
    assert opts["style"] == "outline" and opts["watch_region"] == "US" and opts["fallback"] == "plex" and opts["logo_width"] == 300
    assert opts["logo_set"] == "white" and [f["key"] for f in desc["schema"] if f.get("section") == "Logo"][:3] == ["logo_set", "style", "logo_width"]
    assert (opts["outline_width"], opts["outline_color"], opts["outline_opacity"]) == (6, "#000000", 100)
    assert (opts["soft_width"], opts["soft_color"], opts["soft_opacity"]) == (4, "#000000", 60)
    # Each outline style's settings show only for that style.
    assert {f["key"]: f["show_when"] for f in desc["schema"] if "show_when" in f} == {
        "outline_width": {"style": "outline"}, "outline_color": {"style": "outline"}, "outline_opacity": {"style": "outline"},
        "soft_width": {"style": "soft"}, "soft_color": {"style": "soft"}, "soft_opacity": {"style": "soft"},
    }
    assert all(opts[offer] is True for offer in ("flatrate", "free", "ads", "rent", "buy"))
    # Variants list every service from the highest priority down (the page keeps the ones switched
    # on in the options it edits, in the priorities it edits), the fallback last.
    keys = [v["key"] for v in st.variants(opts)]
    assert keys[:4] == ["netflix", "appletv", "disney", "hbo-max"] and keys[-1] == "fallback"
    assert "all4" in keys and next(v for v in st.variants(opts) if v["key"] == "all4") == {"key": "all4", "label": "Channel 4", "group": "providers", "item": "all4"}
    opts["providers"]["netflix"]["enabled"] = False
    assert "netflix" in [v["key"] for v in st.variants(opts)]
    assert "fallback" not in [v["key"] for v in st.variants({**opts, "fallback": "none"})]
    fallback = st.variants({**opts, "fallback": "jellyfin"})[-1]
    assert (fallback["key"], fallback["label"], fallback["option"]) == ("fallback", "On no service (Jellyfin logo)", "fallback")
    assert fallback["labels"] == {"plex": "On no service (Plex logo)", "jellyfin": "On no service (Jellyfin logo)"}  # the page relabels live
    assert st.resolve_variant("fallback", {**opts, "fallback": "jellyfin"}).image.endswith("/white/jellyfin.png")
    # One plain logo per service; the style becomes an outline effect drawn at render time, and
    # the signature follows the image, its width and the effect.
    plain = st.resolve_variant("netflix", {**opts, "style": "plain"})
    assert plain.key == "netflix" and plain.text == "Netflix" and plain.image.endswith("/streaming/white/netflix.png") and plain.image_effect is None
    assert len(plain.image_stamp) == 8 and plain.signature == f"netflix|Netflix|||netflix.png@{plain.image_stamp}|300"
    outlined = st.resolve_variant("netflix", {**opts, "logo_width": 200})
    assert outlined.image_effect == {"width": 6, "color": "#000000", "opacity": 100, "soft": 1.0}
    assert outlined.signature == f"netflix|Netflix|||netflix.png@{plain.image_stamp}|200|color=#000000,opacity=100,soft=1.0,width=6"
    soft = st.resolve_variant("netflix", {**opts, "style": "soft", "soft_color": "#FF0000", "soft_opacity": 45})
    assert soft.image_effect == {"width": 4, "color": "#FF0000", "opacity": 45, "soft": 3.0}
    assert st.resolve_variant("fallback", opts).image.endswith("/white/plex.png")
    # The color set draws the colored file, and falls back to the white logo for a service without one.
    assert st.resolve_variant("netflix", {**opts, "logo_set": "color"}).image.endswith("/streaming/color/netflix.png")
    assert st.resolve_variant("tnt", {**opts, "logo_set": "color"}).image.endswith("/streaming/color/tnt.png")
    assert st.resolve_variant("netflix", {**opts, "logo_set": "color"}).signature != plain.signature
    assert st.resolve_variant("", opts).key == "appletv"  # no variant: the top-ranked service (Netflix is off here)


def test_streaming_resolves_the_top_priority_service_for_the_region():
    st = get_type("streaming")
    opts = st.default_options()
    movie = _ctx("movie", "Film", 2020, tmdb=1)

    def meta(**regions):
        return {"tmdb": {"title": "Film", "providers": regions}}

    assert st.resolve(movie, opts, meta(US={"flatrate": [15, 9]})).key == "prime"           # Prime (140) outranks Hulu (100)
    assert st.resolve(movie, opts, meta(US={"flatrate": [15], "rent": [2]})).key == "hulu"
    assert st.resolve(movie, opts, meta(US={"ads": [2528]})).key == "plex"                   # YouTube TV: no row, so the fallback
    assert st.resolve(movie, {**opts, "fallback": "none"}, meta(US={"ads": [2528]})) is None
    assert st.resolve(movie, {**opts, "fallback": "jellyfin"}, meta(US={"ads": [2528]})).key == "jellyfin"
    assert st.resolve(movie, opts, meta(GB={"flatrate": [8]})).key == "plex"                 # another region does not count
    assert st.resolve(movie, {**opts, "watch_region": "GB"}, meta(GB={"flatrate": [8]})).key == "netflix"
    assert st.resolve(movie, {**opts, "rent": False, "buy": False}, meta(US={"rent": [8]})).key == "plex"  # offer kinds can be left out
    assert st.resolve(movie, {**opts, "rent": True}, meta(US={"rent": [8]})).key == "netflix"
    assert st.resolve(movie, opts, {"tmdb": {"title": "Film"}}) is None                      # a record without providers: nothing yet
    assert st.resolve(movie, opts, {}) is None
    # Priorities and ids are the user's: a bumped service wins, a switched-off one never shows.
    custom = {**opts, "providers": {**opts["providers"], "hulu": {"enabled": True, "ids": "15", "priority": 500}}}
    assert st.resolve(movie, custom, meta(US={"flatrate": [15, 8]})).key == "hulu"
    off = {**opts, "providers": {**opts["providers"], "netflix": {**opts["providers"]["netflix"], "enabled": False}}}
    assert st.resolve(movie, off, meta(US={"flatrate": [8]})).key == "plex"
    season = _ctx("show", "Show", 2020, tmdb=2, season=1)
    assert st.resolve(season, opts, meta(US={"flatrate": [8]})) is None
    assert st.resolve(season, {**opts, "apply_to_seasons": True}, meta(US={"flatrate": [8]})).key == "netflix"


def test_streaming_options_normalize():
    st = get_type("streaming")
    opts = st.normalize_options({"style": "neon", "logo_width": "5000", "watch_region": " gb ", "rent": "false", "fallback": "hbo",
                                 "outline_width": 0, "outline_color": "#abcdef", "soft_opacity": "999", "soft_color": "red",
                                 "providers": {"hulu": {"ids": " 15, 999 ", "priority": "abc", "enabled": "false"}, "netflix": "junk"}})
    assert opts["style"] == "outline" and opts["logo_width"] == 1000 and opts["watch_region"] == "GB"
    assert opts["logo_set"] == "white" and st.normalize_options({"logo_set": "color"})["logo_set"] == "color" and st.normalize_options({"logo_set": "neon"})["logo_set"] == "white"
    assert opts["outline_width"] == 1 and opts["outline_color"] == "#ABCDEF" and opts["soft_opacity"] == 100 and opts["soft_color"] == "#000000"
    assert st.normalize_options({"logo_width": 0})["logo_width"] == 20  # no 0 = own size; the default is the real 300
    assert opts["rent"] is False and opts["buy"] is True and opts["fallback"] == "plex"
    assert st.normalize_options({"fallback": "jellyfin"})["fallback"] == "jellyfin"
    assert opts["providers"]["hulu"] == {"enabled": False, "ids": "15, 999", "priority": 100}
    assert opts["providers"]["netflix"] == st.default_options()["providers"]["netflix"]
    assert st.normalize_options({"watch_region": "usa"})["watch_region"] == "US"
    assert st.normalize_options({"watch_region": "1"})["watch_region"] == "US"
    assert st.normalize_options(None) == st.default_options()


def test_bundled_streaming_logos_exist_for_every_service_and_style(monkeypatch, tmp_path):
    from services.overlays import images
    from services.overlays.types import streaming

    assert [s[0] for s in images.STYLES] == ["plain", "outline", "soft"]
    lib = streaming.IMAGES
    assert (lib.name, lib.frame, lib.margin, lib.noun) == ("streaming", (300, 100), 6, "logo")
    keys = [k for k, _l in get_type("streaming").image_items()]
    for key, _label, _ids, _priority, _on in streaming.PROVIDER_DEFS:
        path = lib.path(key)
        assert path is not None and path.is_file(), key
        with Image.open(path) as img:
            assert img.size == (300, 100) and img.mode == "RGBA", key
    # One white logo per listed service plus the fallback-only ones (the outline styles are drawn, not shipped), nothing loose.
    assert {f.name.removesuffix(".png") for f in (lib.bundled_dir / "white").glob("*.png")} == set(keys)
    assert keys == [d[0] for d in streaming.PROVIDER_DEFS] + ["jellyfin"]
    for key in ("jellyfin", "plex"):
        for logo_set in ("white", "color"):
            with Image.open(lib.path(key, logo_set)) as img:
                assert img.size == (300, 100) and img.mode == "RGBA", (key, logo_set)
    with Image.open(lib.bundled_dir / "white" / "jellyfin.png") as img:
        assert set(img.get_flattened_data()) <= {(255, 255, 255, a) for a in range(256)}  # the white one is the color one's shape
    # Every bundled logo is trimmed and fitted: its artwork touches the 288x88 inner box and keeps a 6 px margin.
    for f in lib.bundled_dir.glob("*/*.png"):
        with Image.open(f) as img:
            l, t, r, b = img.convert("RGBA").getchannel("A").getbbox()
        assert (r - l >= 287 or b - t >= 87) and l >= 5 and t >= 5 and r <= 295 and b <= 95, f.name
    assert list(lib.bundled_dir.glob("*.png")) == []
    # The color set (Kometa's plus the project's own) covers every service, every file on the same 300x100 frame.
    color = {f.name.removesuffix(".png") for f in (lib.bundled_dir / "color").glob("*.png")}
    assert color == set(keys) and {"netflix", "disney", "hulu", "peacock", "youtube", "plex", "jellyfin", "starz", "fxnow", "usa", "tnt"} <= color
    for f in (lib.bundled_dir / "color").glob("*.png"):
        with Image.open(f) as img:
            assert img.size == (300, 100) and img.mode == "RGBA", f.name
    assert lib.source("netflix", "color") == "bundled" and lib.source("starz", "color") == "bundled" and lib.source("tnt", "color") == "bundled"
    # A set without a file for a key draws the default set's (shown on a library of its own, since every bundled set is complete).
    mini = images.ImageLibrary("mini", sets=(("white", "White"), ("color", "Color")), frame=(300, 100))
    monkeypatch.setattr(images, "BUNDLED_DIR", tmp_path / "bundled")
    for folder, name in (("white", "a"), ("white", "b"), ("color", "a")):
        (tmp_path / "bundled" / "mini" / folder).mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (10, 10)).save(tmp_path / "bundled" / "mini" / folder / f"{name}.png")
    assert (mini.source("a", "color"), mini.source("b", "color"), mini.source("c", "color")) == ("bundled", "fallback", "none")
    assert mini.path("b", "color") == tmp_path / "bundled" / "mini" / "white" / "b.png" and mini.path("c", "color") is None
    monkeypatch.setattr(images, "BUNDLED_DIR", images.Path(images.__file__).resolve().parents[2] / "assets" / "overlays")
    # The white Apple TV mark is the color one's shape: the same logo in both sets.
    with Image.open(lib.bundled_dir / "white" / "appletv.png") as w, Image.open(lib.bundled_dir / "color" / "appletv.png") as c:
        assert w.getchannel("A").tobytes() == c.convert("RGBA").getchannel("A").tobytes()
        assert set(w.convert("RGBA").get_flattened_data()) <= {(255, 255, 255, a) for a in range(256)}
    # A file dropped into config/overlays/streaming/<set>/ replaces the bundled one.
    monkeypatch.setattr(images, "config_overlays_dir", lambda: tmp_path)
    (tmp_path / "streaming" / "white").mkdir(parents=True)
    (tmp_path / "streaming" / "color").mkdir(parents=True)
    Image.new("RGBA", (10, 10)).save(tmp_path / "streaming" / "white" / "netflix.png")
    Image.new("RGBA", (10, 10)).save(tmp_path / "streaming" / "color" / "starz.png")
    assert lib.path("netflix") == tmp_path / "streaming" / "white" / "netflix.png" and lib.source("netflix") == "custom"
    assert lib.path("netflix", "color") == lib.bundled_dir / "color" / "netflix.png"
    assert lib.path("starz", "color") == tmp_path / "streaming" / "color" / "starz.png" and lib.source("starz", "color") == "custom"
    assert lib.path("hulu") == lib.bundled_dir / "white" / "hulu.png"
    assert lib.path("nothing") is None and lib.source("nothing") == "none"


def test_fit_logo_puts_any_image_on_the_logo_frame():
    from services.overlays.images import ImageLibrary
    from services.overlays.types.streaming import IMAGES

    tall = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    tall.paste((255, 0, 0, 255), (100, 0, 300, 400))  # a 200x400 red block with clear margins
    out = IMAGES.fit(tall)
    assert out.size == (300, 100) and out.mode == "RGBA"
    assert out.getchannel("A").getbbox() == (128, 6, 172, 94)  # 44x88: scaled to the 88 px inner height, centered
    framed = IMAGES.fit(Image.new("RGB", (300, 100), (1, 2, 3)))  # a frame-sized file is trimmed and fitted like any other
    assert framed.size == (300, 100) and framed.getchannel("A").getbbox() == (18, 6, 282, 94)
    assert IMAGES.fit(Image.new("RGBA", (3000, 1000), (0, 255, 0, 255))).getchannel("A").getbbox() == (18, 6, 282, 94)  # 3:1 is height-limited: 264x88
    # A library without a margin fills its frame (the rating badges are edge-to-edge boxes).
    badges = ImageLibrary("x", frame=(119, 60), sets=(("white", "White"),))
    assert badges.fit(Image.new("RGB", (120, 40), (1, 2, 3))).getchannel("A").getbbox() == (0, 10, 119, 50)


def test_render_badge_draws_an_image_badge_scaled_to_the_poster(tmp_path):
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (300, 100), (255, 0, 0, 255)).save(logo)
    placement = {**DEFAULT_PLACEMENT, "horizontal_align": "center", "horizontal_offset": 0, "vertical_align": "top", "vertical_offset": 30, "back_enabled": False}
    badge = OverlayBadge(key="svc", text="Service", image=str(logo))
    assert measure_badge(badge, placement) == (350, 30, 300, 100)
    out, rect = render_badge(Image.new("RGB", (1000, 1500), (0, 0, 0)), badge, placement)
    assert rect == (350, 30, 300, 100)
    assert out.getpixel((500, 80)) == (255, 0, 0) and out.getpixel((500, 140)) == (0, 0, 0)
    # The same placement lands in the same spot on a 2x poster, and the logo scales with it.
    big, big_rect = render_badge(Image.new("RGB", (2000, 3000), (0, 0, 0)), badge, placement)
    assert big_rect == (350, 30, 300, 100) and big.getpixel((1000, 160)) == (255, 0, 0) and big.getpixel((1000, 270)) == (0, 0, 0)
    # image_width resizes it (height follows), and a badge past the edge is clipped, not an error.
    small = OverlayBadge(key="svc", text="Service", image=str(logo), image_width=150)
    assert measure_badge(small, placement) == (425, 30, 150, 50)
    _out, edge_rect = render_badge(Image.new("RGB", (1000, 1500)), badge, {**placement, "horizontal_align": "left", "horizontal_offset": -100})
    assert edge_rect == (-100, 30, 300, 100)
    # A text badge is untouched by the image path.
    assert measure_badge(OverlayBadge(key="t", text="TEXT"), placement)[2] > 0


def test_render_badge_outlines_an_image_badge(tmp_path):
    logo = tmp_path / "logo.png"
    canvas = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    canvas.paste((255, 255, 255, 255), (100, 30, 200, 70))  # a white block with clear air around it
    canvas.save(logo)
    placement = {**DEFAULT_PLACEMENT, "horizontal_align": "left", "horizontal_offset": 0, "vertical_align": "top", "vertical_offset": 0, "back_enabled": False}
    poster = Image.new("RGB", (1000, 1500), (120, 120, 120))
    plain, _ = render_badge(poster, OverlayBadge(key="s", text="S", image=str(logo)), placement)
    outlined, rect = render_badge(poster, OverlayBadge(key="s", text="S", image=str(logo), image_effect={"width": 6, "color": "#000000", "opacity": 100, "soft": 0}), placement)
    assert rect == (0, 0, 300, 100)  # the outline grows the shape, never the badge's box
    assert plain.getpixel((150, 50)) == (255, 255, 255) and outlined.getpixel((150, 50)) == (255, 255, 255)  # the logo itself is untouched
    assert all(abs(v - 120) <= 3 for v in plain.getpixel((97, 50))) and max(outlined.getpixel((97, 50))) < 10  # just outside: solid outline (plain: resampling only)
    assert outlined.getpixel((92, 50)) == (120, 120, 120)                                                 # 8 px out: clear again
    red = render_badge(poster, OverlayBadge(key="s", text="S", image=str(logo), image_effect={"width": 6, "color": "#FF0000", "opacity": 50, "soft": 0}), placement)[0]
    r, g, b = red.getpixel((97, 50))
    assert r > 150 and g < 80 and b < 80  # half red over gray
    soft = render_badge(poster, OverlayBadge(key="s", text="S", image=str(logo), image_effect={"width": 4, "color": "#000000", "opacity": 30, "soft": 3}), placement)[0]
    near, far = soft.getpixel((98, 50))[0], soft.getpixel((93, 50))[0]
    assert 60 < near < 110 and near < far <= 120  # a faint edge that fades out with distance
    # The backdrop's min/max widths belong to its box: with the backdrop off they leave the logo's width alone.
    capped = {**placement, "back_enabled": False, "back_min_width": 130, "back_max_width": 130}
    assert measure_badge(OverlayBadge(key="s", text="S", image=str(logo), image_width=300), capped)[2:] == (300, 100)
    assert measure_badge(OverlayBadge(key="s", text="S", image=str(logo), image_width=300), {**capped, "back_enabled": True, "back_style": "solid", "back_padding": 0})[2] == 130
    # The edge band from the shared placement sits behind a logo too (off = untouched poster).
    band = {**placement, "back_enabled": True, "back_style": "band", "gradient_start": "#000000FF", "gradient_band_offset": 0, "gradient_band_height": 300}
    banded, _ = render_badge(poster, OverlayBadge(key="s", text="S", image=str(logo)), band)
    assert max(banded.getpixel((600, 5))) < 10 and banded.getpixel((600, 150))[0] < 120 and banded.getpixel((600, 600)) == (120, 120, 120)
    assert render_badge(poster, OverlayBadge(key="s", text="S", image=str(logo)), {**band, "back_enabled": False})[0].getpixel((600, 5)) == (120, 120, 120)
    # A box behind the logo: the badge grows by the padding, min/max widths apply, and the box color shows around the logo.
    boxed = {**placement, "back_enabled": True, "back_style": "solid", "back_color": "#FF0000FF", "back_padding": 20, "back_radius": 0}
    out_box, box_rect = render_badge(poster, OverlayBadge(key="s", text="S", image=str(logo)), boxed)
    assert box_rect == (0, 0, 340, 140) and measure_badge(OverlayBadge(key="s", text="S", image=str(logo)), boxed) == (0, 0, 340, 140)
    assert out_box.getpixel((10, 10)) == (255, 0, 0) and out_box.getpixel((170, 70)) == (255, 255, 255)  # box around, logo inside
    assert measure_badge(OverlayBadge(key="s", text="S", image=str(logo)), {**boxed, "back_min_width": 500})[2] == 500
    narrow = measure_badge(OverlayBadge(key="s", text="S", image=str(logo)), {**boxed, "back_max_width": 240})
    assert narrow[2] == 240 and narrow[3] < 140  # the logo shrinks to fit the cap, keeping its shape
    # No width or no opacity = the plain image, and the signature tells the effects apart.
    assert all(abs(v - 120) <= 3 for v in render_badge(poster, OverlayBadge(key="s", text="S", image=str(logo), image_effect={"width": 0, "opacity": 100}), placement)[0].getpixel((97, 50)))
    assert OverlayBadge(key="s", text="S", image=str(logo), image_effect={"width": 6, "opacity": 100}).signature != OverlayBadge(key="s", text="S", image=str(logo)).signature


def test_metadata_keeps_watch_providers_when_asked_and_refetches_records_without_them(test_db, isolated_overlay_files, monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, (params or {}).get("append_to_response")))
        if url.endswith("/movie/1"):
            data = {"title": "Film", "status": "Released", "release_date": "2001-01-01"}
            if (params or {}).get("append_to_response") == "watch/providers":
                data["watch/providers"] = {"results": {
                    "US": {"link": "x", "flatrate": [{"provider_id": 8, "provider_name": "Netflix"}, {"provider_id": 8}], "rent": [{"provider_id": 2}], "buy": []},
                    "GB": {"free": [{"provider_id": 103}]},
                    "FR": {},
                }}
            return _Resp(data)
        return _Resp({}, 404)

    t0 = time.time()
    plain = MetadataProvider("key", now=t0, db=test_db)
    monkeypatch.setattr(plain._session, "get", fake_get)
    movie = _ctx("movie", "Film", 2001, tmdb=1, tvdb=None)
    assert "providers" not in plain.for_item(movie)["tmdb"]
    assert calls[-1] == ("https://api.themoviedb.org/3/movie/1", None)
    plain.flush()

    wanting = MetadataProvider("key", now=t0 + 60, db=test_db, providers=True)
    monkeypatch.setattr(wanting._session, "get", fake_get)
    meta = wanting.for_item(movie)  # the settled record is fresh, but it has no providers: fetched again, once
    assert calls[-1] == ("https://api.themoviedb.org/3/movie/1", "watch/providers")
    assert meta["tmdb"]["providers"] == {"US": {"flatrate": [8], "rent": [2]}, "GB": {"free": [103]}}
    wanting.for_item(movie)
    assert len(calls) == 2
    wanting.flush()

    later = MetadataProvider("key", now=t0 + 120, db=test_db, providers=True)
    monkeypatch.setattr(later._session, "get", fake_get)
    assert later.for_item(movie)["tmdb"]["providers"]["US"]["flatrate"] == [8] and len(calls) == 2
    assert later.stats["cached"] == 1


def test_server_engine_asks_for_providers_only_when_a_streaming_overlay_is_active(test_db, isolated_overlay_files):
    streaming = normalize_config({"enabled": True, "types": {"streaming": {"enabled": True}}})
    engine = ServerOverlayEngine(test_db, streaming, instances=[], library_configs=[])
    assert [t.key for t, _ in engine.active] == ["streaming"] and engine.metadata.want_providers is True
    status_only = normalize_config({"enabled": True, "types": {"status": {"enabled": True}}})
    assert ServerOverlayEngine(test_db, status_only, instances=[], library_configs=[]).metadata.want_providers is False


def test_overlay_image_upload_list_and_delete(client, monkeypatch, tmp_path):
    from services.overlays import images
    from services.overlays.types import streaming

    monkeypatch.setattr(images, "config_overlays_dir", lambda: tmp_path / "overlays")
    custom = tmp_path / "overlays" / "streaming"
    lib = streaming.IMAGES
    st = get_type("streaming")
    before = st.resolve_variant("netflix", st.default_options())

    listed = client.get("/api/overlays/images/streaming").json()
    assert (listed["type"], listed["set"], listed["set_label"], listed["noun"], listed["folder"]) == ("streaming", "white", "White", "logo", str(custom / "white"))
    assert len(listed["images"]) == len(st.image_items())
    assert listed["images"][-1] == {"key": "jellyfin", "label": "Jellyfin", "source": "bundled", "custom": False}
    assert all(l["custom"] is False for l in listed["images"]) and listed["images"][0] == {"key": "netflix", "label": "Netflix", "source": "bundled", "custom": False}
    colors = client.get("/api/overlays/images/streaming", params={"set": "color"}).json()
    assert colors["folder"] == str(custom / "color") and {l["key"]: l["source"] for l in colors["images"]}["tnt"] == "bundled" and colors["fallback_label"] == "White"
    assert client.get("/api/overlays/images/streaming", params={"set": "neon"}).status_code == 400
    assert client.get("/api/overlays/images/status").status_code == 404  # a text type has no files
    bundled = client.get("/api/overlays/images/streaming/netflix")
    assert bundled.status_code == 200 and bundled.headers["content-type"] == "image/png"
    assert bundled.content == lib.bundled_dir.joinpath("white", "netflix.png").read_bytes()

    buf = io.BytesIO()
    Image.new("RGB", (120, 40), (255, 0, 0)).save(buf, format="JPEG")  # any image works; it is fitted onto the frame and stored as a PNG with alpha
    resp = client.post("/api/overlays/images/streaming/netflix", files={"file": ("mine.jpg", buf.getvalue(), "image/jpeg")})
    assert resp.status_code == 200 and resp.json() == {"success": True, "type": "streaming", "key": "netflix", "set": "white", "custom": True, "width": 120, "height": 40}
    with Image.open(custom / "white" / "netflix.png") as img:
        assert img.mode == "RGBA" and img.size == (300, 100) and img.getchannel("A").getbbox() == (18, 6, 282, 94)  # fitted: 264x88, centered
    # The color set keeps its own custom files, under color/.
    resp = client.post("/api/overlays/images/streaming/starz", params={"set": "color"}, files={"file": ("starz.jpg", buf.getvalue(), "image/jpeg")})
    assert resp.status_code == 200 and (custom / "color" / "starz.png").is_file()
    assert {l["key"]: l["source"] for l in client.get("/api/overlays/images/streaming", params={"set": "color"}).json()["images"]}["starz"] == "custom"
    assert client.get("/api/overlays/images/streaming/starz", params={"set": "color"}).content == (custom / "color" / "starz.png").read_bytes()
    assert client.delete("/api/overlays/images/streaming/starz", params={"set": "color"}).json()["custom"] is False and not (custom / "color" / "starz.png").exists()
    assert lib.path("netflix") == custom / "white" / "netflix.png"
    assert [l for l in client.get("/api/overlays/images/streaming").json()["images"] if l["key"] == "netflix"][0]["custom"] is True
    assert client.get("/api/overlays/images/streaming/netflix").content == (custom / "white" / "netflix.png").read_bytes()
    # The badge draws the custom file, and its signature changes with it (posters re-badge).
    after = st.resolve_variant("netflix", st.default_options())
    assert after.image == str(custom / "white" / "netflix.png") and after.image_stamp and after.signature != before.signature

    assert client.post("/api/overlays/images/streaming/netflix", files={"file": ("x.txt", b"not an image", "text/plain")}).status_code == 400
    assert client.post("/api/overlays/images/streaming/netflix", files={"file": ("x.png", b"", "image/png")}).status_code == 400
    assert client.post("/api/overlays/images/streaming/nope", files={"file": ("x.png", buf.getvalue(), "image/png")}).status_code == 404
    assert client.get("/api/overlays/images/streaming/nope").status_code == 404

    assert client.delete("/api/overlays/images/streaming/netflix").json() == {"success": True, "type": "streaming", "key": "netflix", "set": "white", "custom": False}
    assert not (custom / "white" / "netflix.png").exists() and lib.path("netflix") == lib.bundled_dir / "white" / "netflix.png"
    assert client.delete("/api/overlays/images/streaming/netflix").status_code == 200  # nothing to remove is fine
    assert st.resolve_variant("netflix", st.default_options()).signature == before.signature

    # Content rating badges share the endpoints, under their own folder and on their own frame.
    cr = get_type("content_rating")
    ratings = client.get("/api/overlays/images/content_rating", params={"set": "color"}).json()
    assert (ratings["noun"], ratings["set_label"], ratings["folder"]) == ("badge", "Color boxes", str(tmp_path / "overlays" / "content_rating" / "color"))
    assert [i["key"] for i in ratings["images"]][:3] == ["g", "pg", "pg-13"] and all(i["source"] == "bundled" for i in ratings["images"])
    resp = client.post("/api/overlays/images/content_rating/r", params={"set": "color"}, files={"file": ("r.jpg", buf.getvalue(), "image/jpeg")})
    assert resp.status_code == 200 and resp.json()["type"] == "content_rating"
    with Image.open(tmp_path / "overlays" / "content_rating" / "color" / "r.png") as img:
        assert img.size == (119, 60) and img.getchannel("A").getbbox() == (0, 10, 119, 50)  # edge to edge, no margin
    assert cr.resolve_variant("movie:r", cr.default_options()).image == str(tmp_path / "overlays" / "content_rating" / "color" / "r.png")
    assert client.get("/api/overlays/images/content_rating/netflix").status_code == 404  # a streaming key is not a rating
    assert client.delete("/api/overlays/images/content_rating/r", params={"set": "color"}).json()["custom"] is False
    assert cr.resolve_variant("movie:r", cr.default_options()).image.endswith("/content_rating/color/r.png")


def test_render_badge_draws_the_badges_own_box_inside_the_backdrop():
    poster = Image.new("RGB", (1000, 1500), (120, 120, 120))
    box = {"fill": "#00000000", "line_width": 4, "line_color": "#FFFFFFFF", "padding": 8, "radius": 0, "min_width": 130, "max_width": 130}
    badge = OverlayBadge(key="r", text="R", box=box)
    alone = {**DEFAULT_PLACEMENT, "horizontal_align": "left", "horizontal_offset": 0, "vertical_align": "top", "vertical_offset": 0, "back_enabled": False, "font_size": 60}
    out, rect = render_badge(poster, badge, alone)
    assert rect[2] == 130 and measure_badge(badge, alone)[2] == 130  # the badge's box: its own min width, no backdrop around it
    assert max(out.getpixel((1, rect[3] // 2))) > 240                 # its white outline at the edge
    assert out.getpixel((65, rect[3] // 2 - 20))[0] in range(110, 131) or out.getpixel((65, 6))[0] in range(110, 131)  # transparent fill: poster shows through
    # With the backdrop on, a solid red box wraps the badge's box by the backdrop's padding, and the outline stays inside.
    behind = {**alone, "back_enabled": True, "back_style": "solid", "back_color": "#FF0000FF", "back_padding": 20, "back_radius": 0, "back_line_width": 0}
    out2, rect2 = render_badge(poster, badge, behind)
    assert rect2[2] == 170 and rect2[3] == rect[3] + 40 and measure_badge(badge, behind) == rect2
    assert all(abs(v - e) <= 3 for v, e in zip(out2.getpixel((5, 5)), (255, 0, 0)))  # the backdrop's own color at its edge
    assert max(out2.getpixel((21, rect2[3] // 2))) > 240                         # the badge's outline 20 px in
    # A colored badge fills its own box; the backdrop can be a gradient around it.
    colored = OverlayBadge(key="r", text="R", box={**box, "fill": "#F01C24FF", "line_width": 0})
    out3, rect3 = render_badge(poster, colored, {**behind, "back_style": "gradient", "gradient_mode": "center", "gradient_spread": 20, "gradient_start": "#000000FF"})
    assert all(abs(v - e) <= 3 for v, e in zip(out3.getpixel((26, rect3[3] // 2)), (240, 28, 36)))  # just inside the badge's box: the rating's color
    assert max(out3.getpixel((5, rect3[3] // 2))) < 30                                              # the backdrop's dark glow outside it



# ── resolution and audio (Kometa's rules) ────────────────────────────────────

def _file(path, resolution="1080", **kw):
    return {"resolution": resolution, "hdr": False, "dovi": False, "path": path, "audio_titles": [], **kw}


def test_resolution_badge_follows_kometas_weights_and_sources():
    from services.overlays import media

    assert len(media.RESOLUTION_ORDER) == 39 and media.RESOLUTION_ORDER[0] == ("4k", "dvhdrplus") and media.RESOLUTION_ORDER[-1] == ("", "hdr")
    assert media.RESOLUTION_WEIGHTS[("4k", "")] == 130 and media.RESOLUTION_WEIGHTS[("1080p", "dvhdrplus")] == 129  # every 4K badge outranks every 1080p one
    assert ("576p", "hlg") not in media.RESOLUTION_WEIGHTS and ("480p", "hlg") not in media.RESOLUTION_WEIGHTS and ("720p", "hlg") in media.RESOLUTION_WEIGHTS
    badge = lambda *files, **kw: media.resolution_badge(list(files), **kw)
    # The resolution is the server's word, mapped like Kometa: 2160/4k, 1080/2k, 720/hd, 576, 480/sd.
    assert [badge(_file("x.mkv", r)) for r in ("4k", "2k", "1080", "720", "576", "480", "sd")] == [("4k", ""), ("1080p", ""), ("1080p", ""), ("720p", ""), ("576p", ""), ("480p", ""), ("480p", "")]
    # HDR and Dolby Vision come from the server; the finer formats from the file name, the heaviest winning.
    assert badge(_file("x.mkv", "4k", hdr=True)) == ("4k", "hdr")
    assert badge(_file("x.mkv", "4k", hdr=True, dovi=True)) == ("4k", "dv")
    assert badge(_file("Film [Remux-2160p][DV HDR10][TrueHD].mkv", "4k")) == ("4k", "dvhdr")
    assert badge(_file("Film [WEBDL-2160p][DV HDR10Plus].mkv", "4k", dovi=True, hdr=True)) == ("4k", "dvhdrplus")
    assert badge(_file("Film [HDR10+].mkv", "1080")) == ("1080p", "plus") and badge(_file("Film [HLG].mkv", "720")) == ("720p", "hlg")
    assert badge(_file("Film [HLG].mkv", "576")) == ("576p", "hdr")  # no 576p HLG badge exists, but HLG is an HDR format the name spells
    assert badge(_file("Film [HLG].mkv", "576"), source="server") == ("576p", "")  # the server alone: plain 576p
    # An unknown resolution still earns a format-only badge; nothing at all earns none.
    assert badge(_file("x.mkv", "", dovi=True)) == ("", "dv") and badge(_file("x.mkv", "")) is None and badge() is None
    # A movie with two versions is as good as its best one.
    assert badge(_file("a [Bluray-1080p].mkv", "1080"), _file("b [Remux-2160p][HDR10+].mkv", "4k")) == ("4k", "plus")
    # Switched-off badges are skipped, so the next heaviest match is taken.
    assert badge(_file("x [DV HDR10].mkv", "4k", hdr=True), allowed=lambda r, f: f != "dvhdr") == ("4k", "dv")  # the next badge the name earns
    assert badge(_file("x [DV HDR10].mkv", "4k", hdr=True), allowed=lambda r, f: f != "dvhdr", source="server") == ("4k", "hdr")  # the server alone knows no DV here
    assert badge(_file("x.mkv", "4k", hdr=True), allowed=lambda r, f: r != "4k") == ("", "hdr")
    # A show: its resolution and plain HDR count when ANY episode has them; the rest need the share of episodes.
    eps = [_file(f"S01E{i:02d} [WEBDL-1080p][DV HDR10].mkv", "1080") for i in range(1, 7)] + [_file(f"S01E{i:02d} [HDTV-720p].mkv", "720") for i in range(7, 11)]
    assert media.resolution_badge(eps, episodes=True) == ("1080p", "dvhdr")           # 6 of 10 named DV HDR: 60 %
    assert media.resolution_badge(eps[1:], episodes=True) == ("1080p", "hdr")         # 5 of 9: under 60 % for DV HDR; HDR counts from any episode's name
    assert media.resolution_badge(eps[1:], episodes=True, source="server") == ("1080p", "")  # the server flags none of them
    assert media.resolution_badge(eps[1:], episodes=True, percent=50) == ("1080p", "dvhdr")
    assert media.resolution_badge(eps[6:] + [_file("S02E01.mkv", "4k", hdr=True)], episodes=True) == ("4k", "hdr")  # one 4K HDR episode is enough
    # Where the facts come from: the server's, the file name's, or both (the default: either counts).
    named = _file("Film (2020) [Bluray-2160p][HDR10+][DTS-HD MA 5.1].mkv", "1080")
    assert badge(named) == ("4k", "plus") and badge(named, source="server") == ("1080p", "") and badge(named, source="filename") == ("4k", "plus")
    flagged = _file("Film (2020) [Bluray-1080p].mkv", "4k", hdr=True, dovi=True)
    assert badge(flagged) == ("4k", "dv") and badge(flagged, source="server") == ("4k", "dv") and badge(flagged, source="filename") == ("1080p", "")
    assert badge(_file("Film [WEBDL-2160p][DV HDR10].mkv", ""), source="filename") == ("4k", "dvhdr") and badge(_file("Film [HDTV-720p][HLG].mkv", ""), source="filename") == ("720p", "hlg")
    assert badge(_file("Film [Bluray-1080p][DV].mkv", ""), source="filename") == ("1080p", "dv") and badge(_file("Film [Bluray-1080p][HDR10].mkv", ""), source="filename") == ("1080p", "hdr")
    assert badge(_file("Film [1440p].mkv", ""), source="filename") == ("1080p", "") and badge(named, source="junk") == badge(named)  # an unknown choice reads from both
    assert media.frame_resolution(3840, 1600) == "4k" and media.frame_resolution(1920, 800) == "1080" and media.frame_resolution(720, 576) == "576" and media.frame_resolution(None, None) == ""


def test_audio_badge_follows_kometas_patterns_and_priorities():
    from services.overlays import media

    assert [c[0] for c in media.AUDIO_CODECS][:4] == ["truehd_atmos", "dtsx", "plus_atmos", "dolby_atmos"] and media.AUDIO_WEIGHTS["opus"] == 10 and len(media.AUDIO_KEYS) == 16
    badge = lambda *files, **kw: media.audio_badge(list(files), **kw)
    names = {
        "Film [Remux-2160p][TrueHD Atmos 7.1].mkv": "truehd_atmos", "Film [DTS-X 7.1].mkv": "dtsx", "Film [EAC3 Atmos 5.1].mkv": "plus_atmos",
        "Film [Atmos].mkv": "dolby_atmos", "Film [TrueHD 5.1].mkv": "truehd", "Film [DTS-HD MA 5.1].mkv": "ma", "Film [FLAC 2.0].mkv": "flac",
        "Film [PCM 2.0].mkv": "pcm", "Film [DTS-HD HRA 5.1].mkv": "hra", "Film [EAC3 5.1].mkv": "plus", "Film [DTS-ES 6.1].mkv": "dtses",
        "Film [DTS 5.1].mkv": "dts", "Film [AC3 5.1].mkv": "digital", "Film [AAC 5.1].mkv": "aac", "Film [MP3].mkv": "mp3", "Film [Opus 5.1].mkv": "opus",
    }
    assert {name: badge(_file(name)) for name in names} == names
    # A track title counts as much as the file name; with neither there is no badge.
    assert badge(_file("Film.mkv", audio_titles=["English (TrueHD 7.1)"])) == "truehd" and badge(_file("Film.mkv")) is None and badge() is None
    assert badge(_file("Film [AAC 2.0].mkv", audio_titles=["Surround 7.1 (English DTS-HD MA)"])) == "ma"  # the higher priority of the two
    # Priorities are the user's, and a switched-off codec is simply not looked for.
    assert badge(_file("Film [DTS-HD MA 5.1][AAC].mkv"), priorities={"aac": 500, "ma": 110}) == "aac"
    assert badge(_file("Film [TrueHD Atmos 7.1].mkv"), priorities={k: w for k, w in media.AUDIO_WEIGHTS.items() if k != "truehd_atmos"}) == "dolby_atmos"
    # A show needs the share of episodes, by title or by file name.
    eps = [_file(f"S01E{i:02d} [EAC3 5.1].mkv") for i in range(1, 7)] + [_file(f"S01E{i:02d} [AAC 2.0].mkv") for i in range(7, 11)]
    assert media.audio_badge(eps, episodes=True) == "plus" and media.audio_badge(eps[2:], episodes=True) is None  # 6/10, then 4/8 each
    assert media.audio_badge(eps[2:], episodes=True, percent=50) == "plus"
    # Where the codec is read from: the track titles (the server), the file name, or both.
    titled = _file("Film (2020) [Bluray-1080p][AAC 2.0].mkv", audio_titles=["TrueHD Atmos 7.1"])
    assert badge(titled) == "truehd_atmos" and badge(titled, source="server") == "truehd_atmos" and badge(titled, source="filename") == "aac"
    assert badge(_file("Film [DTS-HD MA 5.1].mkv", audio_titles=["Unknown"]), source="server") is None and badge(titled, source="junk") == "truehd_atmos"


def test_resolution_and_audio_types_describe_and_resolve():
    res, audio = get_type("resolution"), get_type("audio")
    for otype, order in ((res, 50), (audio, 60)):
        desc = otype.describe()
        assert desc["order"] == order and desc["needs"] == ["media"] and desc["hidden_placement"] == ["text"] and desc["targets"] == ["movie", "show", "season"]
        p = desc["default_placement"]  # Kometa's box: translucent black, rounded, 26 px around the cropped art and at least 305 wide
        assert (p["back_enabled"], p["back_style"], p["back_color"], p["back_padding"], p["back_radius"], p["horizontal_align"], p["horizontal_offset"], p["vertical_align"]) == (True, "solid", "#00000099", 26, 30, "left", 35, "top")
        assert (p["back_min_width"], p["back_max_width"]) == (305, 0)
    assert res.default_placement()["vertical_offset"] == 35 and audio.default_placement()["vertical_offset"] == 150  # Audio sits under Resolution

    opts = res.default_options()
    assert (opts["badge_style"], opts["badge_scale"], opts["episode_percent"]) == ("color", 100, 60)
    assert len(opts["badges"]) == 39 and all(row == {"enabled": True} for row in opts["badges"].values())
    assert [f["key"] for f in res.schema() if f.get("section") == "Badge"] == ["badge_style", "badge_scale", "badge_height", "dot_gap", "show_dot", "episode_percent", "source"]
    assert opts["source"] == "both" and [o["value"] for o in next(f for f in res.schema() if f["key"] == "source")["options"]] == ["both", "server", "filename"]
    assert (opts["dot_gap"], opts["show_dot"]) == (60, True) and all(f["show_when"] == {"badge_style": ["lettering"]} for f in res.schema() if f["key"] in ("dot_gap", "show_dot"))
    assert [f["show_when"]["badge_style"] for f in res.schema() if f["key"] in ("badge_scale", "badge_height")] == [["color", "white"], ["lettering"]]
    assert next(f for f in res.schema() if f["kind"] == "images")["set_option"] == "badge_style"
    # One row per badge, a switch and nothing else, in three tables that end where a resolution's badges end.
    table = next(f for f in res.schema() if f["kind"] == "states")
    assert (table["key"], table["item_label"], table["split"], len(table["items"])) == ("badges", "Badge", [14, 13, 12], 39)
    assert [i["label"] for i in table["items"]][13:15] == ["1080p", "720p DV HDR10+"] and [i["label"] for i in table["items"]][26:28] == ["576p", "480p DV HDR10+"]
    assert all(i["fields"] == [] for i in table["items"])
    assert [v["key"] for v in res.variants(opts)][:3] == ["4kdvhdrplus", "4kdvhdr", "4kplus"] and len(res.variants(opts)) == 39
    assert res.variants(opts)[0] == {"key": "4kdvhdrplus", "label": "4K DV HDR10+", "group": "badges", "item": "4kdvhdrplus"}
    movie = _ctx("movie", "Film", 2026)
    movie.media = [_file("Film [Remux-2160p][DV HDR10].mkv", "4k")]
    badge = res.resolve(movie, opts, {})
    # Kometa's art, cropped to the badge and drawn in percent of its own size: 100 is the file's own height.
    with Image.open(badge.image) as art:
        own = art.height
    assert (badge.key, badge.text, badge.image_width, badge.image_height) == ("4kdvhdr", "4K DV HDR", 0, own) and badge.image.endswith("/resolution/color/4kdvhdr.png") and 35 <= own <= 53
    assert badge.signature == f"4kdvhdr|4K DV HDR|||4kdvhdr.png@{badge.image_stamp}|0|h={own}"
    assert res.resolve(movie, {**opts, "badge_scale": 200}, {}).image_height == 2 * own and res.resolve(movie, {**opts, "badge_scale": 50}, {}).image_height == round(own / 2)
    # A badge switched off is left out: the item gets the next one that fits, or none.
    without = lambda *keys: {**opts, "badges": {**opts["badges"], **{k: {"enabled": False} for k in keys}}}
    assert res.resolve(movie, without("4kdvhdr"), {}).key == "4kdv" and res.resolve(movie, without("4kdvhdr", "4kdv"), {}).key == "4khdr"
    assert res.resolve(movie, without("4kdvhdr", "4kdv", "4khdr", "4k"), {}).key == "dvhdr"
    assert res.resolve(movie, without("4kdvhdr", "4kdv", "4khdr", "4k", "dvhdr", "dv", "hdr"), {}) is None
    # The style picks the set, and a change of style is a different badge.
    white = res.resolve(movie, {**opts, "badge_style": "white"}, {})
    assert white.image.endswith("/resolution/white/4kdvhdr.png") and white.signature != badge.signature
    assert res.resolve_variant("4khlg", {**opts, "badge_style": "white"}).image.endswith("/resolution/white/4khlg.png")
    # Lettering is sized by height: the resolution lettering (100 px of art) gets the setting, the format lettering (80) follows in proportion.
    # A lettering badge is joined from its parts: the resolution, the dot (11 px of art at this size) and the format, 60 % of the height apart.
    lettering = {**opts, "badge_style": "lettering"}
    letter = res.resolve(movie, lettering, {})
    name = lambda path: path.rsplit("/", 1)[1]
    # DV HDR is two marks with a dot, as the pack writes it, so every dot in "4K · DOLBY VISION · HDR" follows the settings.
    assert name(letter.image) == "_res-4k.png" and [(name(p), h) for p, h in letter.image_tail] == [("_dot.png", 11), ("_fmt-dv.png", 40), ("_dot.png", 11), ("_fmt-hdr.png", 40)]
    assert (letter.image_height, letter.image_width, letter.image_gap) == (50, 0, 30) and letter.signature.endswith("|0|h=50+11+40+11+40|g=30") and "+" in letter.image_stamp
    assert letter.signature != badge.signature
    alone = res.resolve_variant("dvhdr", lettering)
    assert name(alone.image) == "_fmt-dv.png" and ([name(p) for p, _h in alone.image_tail], alone.image_height, alone.image_gap) == (["_dot.png", "_fmt-hdr.png"], 40, 30)
    single = res.resolve_variant("hdr", lettering)
    assert name(single.image) == "_fmt-hdr.png" and (single.image_height, single.image_tail, single.image_gap) == (40, (), 0)
    no_dot = res.resolve_variant("4kdvhdr", {**lettering, "show_dot": False, "dot_gap": 100})
    assert [name(p) for p, _h in no_dot.image_tail] == ["_fmt-dv.png", "_fmt-hdr.png"] and no_dot.image_gap == 50
    big = res.resolve_variant("4kdvhdr", {**lettering, "badge_height": 80})
    assert (big.image_height, [h for _p, h in big.image_tail], big.image_gap) == (80, [18, 64, 18, 64], 48)
    # Read from the server only (no flags here) or the file name only.
    assert res.resolve(movie, {**opts, "source": "server"}, {}).key == "4k" and res.resolve(movie, {**opts, "source": "filename"}, {}).key == "4kdvhdr"
    assert res.normalize_options({"source": "filename"})["source"] == "filename" and res.normalize_options({"source": "junk"})["source"] == "both"
    movie.media = []
    assert res.resolve(movie, opts, {}) is None  # nothing known about the media: no badge
    movie.media = None
    assert res.resolve(movie, opts, {}) is None
    show = _ctx("show", "Show", 2020)
    show.media = [_file("S01E01 [HDTV-720p].mkv", "720"), _file("S02E01 [WEBDL-2160p].mkv", "4k")]
    assert res.resolve(show, opts, {}).key == "4k"  # any episode at 4K
    season = _ctx("show", "Show", 2020, season=1)
    season.media = show.media[:1]
    assert res.resolve(season, opts, {}) is None and res.resolve(season, {**opts, "apply_to_seasons": True}, {}).key == "720p"
    assert res.resolve_variant("junk", opts).key == "4kdvhdrplus" and res.resolve_variant("junk", without("4kdvhdrplus", "4kdvhdr")).key == "4kplus"
    n = res.normalize_options({"badge_style": "neon", "badges": {"hdr": {"enabled": "false"}, "4k": "junk", "nope": {"enabled": False}}})
    assert n["badge_style"] == "color" and n["badges"]["hdr"] == {"enabled": False} and n["badges"]["4k"] == {"enabled": True} and len(n["badges"]) == 39
    assert res.normalize_options({"badge_style": "white"})["badge_style"] == "white" and res.normalize_options({"badge_height": 999})["badge_height"] == 300
    assert (res.normalize_options({"dot_gap": "999", "show_dot": "false"})["dot_gap"], res.normalize_options({"show_dot": "false"})["show_dot"]) == (200, False)
    assert res.normalize_options({"badge_scale": "9999", "episode_percent": 0})["badge_scale"] == 400 and res.normalize_options({"episode_percent": 0})["episode_percent"] == 1

    aopts = audio.default_options()
    assert aopts["badge_style"] == "compact" and aopts["badge_height"] == 40 and aopts["codecs"]["truehd_atmos"] == {"enabled": True, "priority": 160} and len(aopts["codecs"]) == 16
    table = next(f for f in audio.schema() if f["kind"] == "states")
    assert (table["key"], table["item_label"], [f["key"] for f in table["items"][0]["fields"]]) == ("codecs", "Codec", ["priority"])
    assert audio.variants(aopts)[0] == {"key": "truehd_atmos", "label": "Dolby TrueHD Atmos", "group": "codecs", "item": "truehd_atmos"}
    movie.media = [_file("Film [TrueHD Atmos 7.1].mkv", "4k")]
    assert audio.resolve(movie, aopts, {}).image.endswith("/audio/compact/truehd_atmos.png")
    assert aopts["source"] == "both" and audio.resolve(movie, {**aopts, "source": "server"}, {}) is None and audio.resolve(movie, {**aopts, "source": "filename"}, {}).key == "truehd_atmos"
    assert audio.normalize_options({"source": "server"})["source"] == "server" and audio.normalize_options({"source": "x"})["source"] == "both"
    standard = audio.resolve(movie, {**aopts, "badge_style": "standard"}, {})
    assert standard.image.endswith("/audio/standard/truehd_atmos.png") and standard.signature != audio.resolve(movie, aopts, {}).signature
    lettered = audio.resolve(movie, {**aopts, "badge_style": "lettering"}, {})
    assert lettered.image.endswith("/audio/lettering/truehd_atmos.png") and (lettered.image_height, lettered.image_width) == (40, 0)
    off = {**aopts, "codecs": {**aopts["codecs"], "truehd_atmos": {"enabled": False, "priority": 160}}}
    assert audio.resolve(movie, off, {}).key == "dolby_atmos"
    assert audio.resolve(season, {**aopts, "apply_to_seasons": True}, {}) is None  # its one episode names no codec
    n = audio.normalize_options({"badge_style": "huge", "codecs": {"aac": {"enabled": "false", "priority": "5000"}, "mp3": "junk"}})
    assert n["badge_style"] == "compact" and n["codecs"]["aac"] == {"enabled": False, "priority": 1000} and n["codecs"]["mp3"] == aopts["codecs"]["mp3"]


def test_bundled_resolution_and_audio_images_are_kometas_on_its_backdrop_box():
    from services.overlays.types import audio, resolution

    res_keys = [k for k, _l in get_type("resolution").image_items()]
    assert (resolution.IMAGES.name, resolution.IMAGES.cropped, resolution.IMAGES.default_set, [s for s, _l in resolution.IMAGES.sets]) == ("resolution", True, "color", ["color", "white", "lettering"])
    # Two sets of the same 39 badges: Kometa's (the format logos in color) and those made all white, each cropped to its art.
    for style in ("color", "white"):
        assert {f.name.removesuffix(".png") for f in (resolution.IMAGES.bundled_dir / style).glob("*.png")} == set(res_keys) and len(res_keys) == 39, style
        for key in res_keys:
            assert resolution.IMAGES.source(key, style) == "bundled", (style, key)
            with Image.open(resolution.IMAGES.path(key, style)) as img:
                assert img.mode == "RGBA" and img.getchannel("A").getbbox() == (0, 0, img.width, img.height) and 35 <= img.height <= 53 and 116 <= img.width <= 305, (style, key)
    for key in res_keys:
        with Image.open(resolution.IMAGES.path(key, "white")) as white, Image.open(resolution.IMAGES.path(key, "color")) as color:
            assert white.getchannel("A").tobytes() == color.getchannel("A").tobytes(), key           # the same shapes, soft edges included
            assert {white.getchannel(c).getextrema() for c in "RGB"} == {(255, 255)}, key           # every pixel white
    with Image.open(resolution.IMAGES.path("4kdv", "color")) as color:
        r, g, b, _a = color.split()
        assert r.tobytes() != g.tobytes() and g.tobytes() != b.tobytes()                             # the color set keeps its colors
    audio_keys = [k for k, _l in get_type("audio").image_items()]
    assert (audio.IMAGES.default_set, [s for s, _l in audio.IMAGES.sets]) == ("compact", ["compact", "standard", "lettering"])
    tall = set()
    for style in ("compact", "standard"):
        assert {f.name.removesuffix(".png") for f in (audio.IMAGES.bundled_dir / style).glob("*.png")} == set(audio_keys), style
        for key in audio_keys:
            with Image.open(audio.IMAGES.path(key, style)) as img:  # cropped to the art: no air around it
                assert img.mode == "RGBA" and img.getchannel("A").getbbox() == (0, 0, img.width, img.height) and img.width <= 305, (style, key)
                if img.height > 105:
                    tall.add((style, key))
    assert tall == {("standard", "truehd_atmos"), ("standard", "plus_atmos")}  # the two Atmos badges stack in the standard style
    assert audio.IMAGES.cropped and audio.IMAGES.drawn_height("aac", "compact", 100, reference=100) == audio.IMAGES.bundled_height("aac", "compact")
    # Lettering: audio ships whole badges; resolution ships the parts a badge is joined from. All white, cropped, no frame.
    parts = {f.stem for f in (resolution.IMAGES.bundled_dir / "lettering").glob("*.png")}
    assert parts == {"_res-4k", "_res-1080p", "_res-720p", "_res-576p", "_res-480p", "_fmt-dv", "_fmt-hdr", "_fmt-plus", "_fmt-hlg", "_dot"}
    assert resolution.IMAGES.file("4k", "lettering") is None and resolution.IMAGES.cropped
    assert {f.stem for f in (audio.IMAGES.bundled_dir / "lettering").glob("*.png")} == set(audio_keys)
    checks = [(resolution.IMAGES, n, 100 if n.startswith("_res") else 22 if n == "_dot" else 80) for n in parts] + [(audio.IMAGES, k, 80) for k in audio_keys]
    for lib, name, height in checks:
        with Image.open(lib.file(name, "lettering")) as img:
            assert img.mode == "RGBA" and img.getchannel("A").getbbox() == (0, 0, img.width, img.height) and img.height == height, (lib.name, name)
            assert {img.getchannel(c).getextrema() for c in "RGB"} == {(255, 255)}, (lib.name, name)
    assert (resolution.IMAGES.height, audio.IMAGES.height) == (100, 80)
    assert (resolution.IMAGES.drawn_height("_res-4k", "lettering", 50), resolution.IMAGES.drawn_height("_fmt-hdr", "lettering", 50),
            resolution.IMAGES.drawn_height("_dot", "lettering", 50), audio.IMAGES.drawn_height("aac", "lettering", 40)) == (50, 40, 11, 40)
    assert resolution.IMAGES.drawn_height("_res-4k", "lettering", 0) == 0 and resolution.IMAGES.bundled_height("nope", "lettering") == 100


def test_server_engine_badges_resolution_and_audio_from_the_media_files(test_db, isolated_overlay_files):
    client = FakeClient()
    for lib in list(client.items.values()) + list(client.seasons.values()):
        for it in lib:
            it.client = client
    episodes = [_file(f"Live S01E{i:02d} [WEBDL-1080p][EAC3 5.1].mkv", "1080") for i in range(1, 4)]
    client.media = {"10": [_file("Film (2026) [Remux-2160p][DV HDR10][TrueHD Atmos 7.1].mkv", "4k", dovi=True, hdr=True)], "20": episodes, "21": episodes}
    cfg = normalize_config({"enabled": True, "types": {"resolution": {"enabled": True}, "audio": {"enabled": True}}})
    sources = _sources(isolated_overlay_files, 10, 20, 30)
    engine = _engine(test_db, client, FakeMetadata({}), cfg, sources=sources)
    assert engine._needs_media is True and engine._needs_tmdb is False
    stats = engine.run()
    engine.flush()
    # One media listing per library, none per item; the show without media gets no badge.
    assert client.media_calls == [("library", "1"), ("library", "2")]
    assert (stats["applied"], stats["no_badge"], stats["failed"]) == (2, 1, 0) and sorted(i for i, _ in client.uploads) == ["10", "20"]
    assert stats["by_state"] == {"4KDVHDR": 1, "TRUEHD ATMOS": 1, "1080P": 1, "PLUS": 1}
    sig = _state_items(test_db)["srv|10"]["sig"]
    assert sig.startswith("resolution:4kdvhdr|4K DV HDR|") and "\naudio:truehd_atmos|Dolby TrueHD Atmos|" in sig
    # At upload time the one item's media is asked for, and an unchanged badge is not stale.
    later = _engine(test_db, client, FakeMetadata({}), cfg, sources=sources)
    assert later.badge_stale("srv", client.get_item("10"), sources[("srv", "10")]) is False and client.media_calls[-1] == ("item", "10")
    client.media["10"] = [_file("Film (2026) [Bluray-1080p][AAC 2.0].mkv", "1080")]  # the file was replaced by a lesser one
    assert _engine(test_db, client, FakeMetadata({}), cfg, sources=sources).badge_stale("srv", client.get_item("10"), sources[("srv", "10")]) is True
    again = _engine(test_db, client, FakeMetadata({}), cfg, sources=sources)
    stats = again.run()
    again.flush()
    assert stats["refreshed"] == 1 and stats["current"] == 1 and "resolution:1080p|" in _state_items(test_db)["srv|10"]["sig"]
    # A status-only setup never asks for media.
    client.media_calls.clear()
    _engine(test_db, client, FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}}), sources=sources).run()
    assert client.media_calls == []


def test_image_endpoints_default_to_the_types_first_set(client, monkeypatch, tmp_path):
    from services.overlays import images

    monkeypatch.setattr(images, "config_overlays_dir", lambda: tmp_path / "overlays")
    listed = client.get("/api/overlays/images/audio").json()
    assert (listed["set"], listed["set_label"], listed["fallback_label"], listed["noun"]) == ("compact", "Compact", "Compact", "badge") and len(listed["images"]) == 16
    assert client.get("/api/overlays/images/audio", params={"set": "standard"}).json()["folder"] == str(tmp_path / "overlays" / "audio" / "standard")
    assert client.get("/api/overlays/images/audio", params={"set": "white"}).status_code == 400
    res = client.get("/api/overlays/images/resolution").json()
    assert (res["set"], res["set_label"], len(res["images"]), res["images"][0]) == ("color", "Color", 39, {"key": "4kdvhdrplus", "label": "4K DV HDR10+", "source": "bundled", "custom": False})
    assert client.get("/api/overlays/images/resolution", params={"set": "white"}).json()["folder"] == str(tmp_path / "overlays" / "resolution" / "white")
    buf = io.BytesIO()
    Image.new("RGBA", (200, 50), (255, 255, 255, 255)).save(buf, format="PNG")
    assert client.post("/api/overlays/images/resolution/4k", files={"file": ("mine.png", buf.getvalue(), "image/png")}).json()["set"] == "color"
    with Image.open(tmp_path / "overlays" / "resolution" / "color" / "4k.png") as img:
        assert img.size == (188, 47) and img.getchannel("A").getbbox() == (0, 0, 188, 47)  # cropped, as tall as the bundled 4K art (47)
    assert get_type("resolution").resolve_variant("4k", get_type("resolution").default_options()).image == str(tmp_path / "overlays" / "resolution" / "color" / "4k.png")
    assert client.delete("/api/overlays/images/resolution/4k").json()["custom"] is False
    # A lettering badge is joined from parts: listed as bundled, drawn on request with the set's own spacing.
    lettered = client.get("/api/overlays/images/resolution", params={"set": "lettering"}).json()
    assert {i["source"] for i in lettered["images"]} == {"bundled"} and len(lettered["images"]) == 39
    joined = client.get("/api/overlays/images/resolution/4kdv", params={"set": "lettering"})
    assert joined.status_code == 200 and joined.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(joined.content)) as img:
        assert img.size == (192 + 60 + 22 + 60 + 608, 100)  # 4K, the dot and DOLBY VISION, 60 px of air each side of the dot
    with Image.open(io.BytesIO(client.get("/api/overlays/images/resolution/1080pdvhdrplus", params={"set": "lettering"}).content)) as img:
        assert img.size == (222 + 142 + 608 + 142 + 426, 100)  # HD · DOLBY VISION · HDR10+: both dots with the same air
    # A lettering set: the upload is cropped to its art and scaled to the bundled badge's height, with no frame.
    buf = io.BytesIO()
    canvas = Image.new("RGBA", (600, 300), (0, 0, 0, 0))
    canvas.paste((255, 255, 255, 255), (100, 100, 500, 200))  # 400 x 100 of art in a bigger canvas
    canvas.save(buf, format="PNG")
    assert client.post("/api/overlays/images/resolution/720p", params={"set": "lettering"}, files={"file": ("mine.png", buf.getvalue(), "image/png")}).json()["set"] == "lettering"
    with Image.open(tmp_path / "overlays" / "resolution" / "lettering" / "720p.png") as img:
        assert img.size == (400, 100) and img.getchannel("A").getbbox() == (0, 0, 400, 100)
    own = get_type("resolution").resolve_variant("720p", {**get_type("resolution").default_options(), "badge_style": "lettering"})
    assert own.image == str(tmp_path / "overlays" / "resolution" / "lettering" / "720p.png") and (own.image_height, own.image_tail) == (50, ())  # drawn whole
    assert next(i for i in client.get("/api/overlays/images/resolution", params={"set": "lettering"}).json()["images"] if i["key"] == "720p")["source"] == "custom"
    with Image.open(io.BytesIO(client.get("/api/overlays/images/resolution/720p", params={"set": "lettering"}).content)) as img:
        assert img.size == (400, 100)  # the thumbnail is the custom file itself
    assert client.post("/api/overlays/images/audio/aac", params={"set": "lettering"}, files={"file": ("mine.png", buf.getvalue(), "image/png")}).status_code == 200
    with Image.open(tmp_path / "overlays" / "audio" / "lettering" / "aac.png") as img:
        assert img.size == (320, 80)  # the audio lettering is 80 tall


# ── Edge band fade ───────────────────────────────────────────────────────────

# ── Edition (the cut of a movie, from the {edition-...} tag of its file name) ───────

def test_edition_rules_read_the_edition_tag_and_tell_near_names_apart():
    from services.overlays import editions

    assert len(editions.EDITIONS) == 18 and editions.EDITIONS[0][:3] == ("imax", "IMAX", 420) and editions.EDITIONS[-1][:3] == ("ultimate-edition", "Ultimate Edition", 590)
    f = lambda path: {"resolution": "4k", "path": path}
    tags = {"{edition-IMAX}": "imax", "{edition-Extended Cut}": "extended-cut", "{edition-Extended}": "extended-edition", "{edition-Theatrical Cut}": "theatrical-cut",
            "{edition-Theatrical}": "theatrical", "{edition-Ultimate Director's Cut}": "directors-cut", "{edition-Ultimate}": "ultimate-edition", "{edition-25th Anniversary}": "anniversary-edition",
            "{edition-Director's Cut}": "directors-cut", "{edition-Unrated}": "unrated-edition", "{edition-Open Matte}": "open-matte", "Extended Cut": None, "": None}
    assert {tag: editions.edition_badge([f(f"Film (2000) {tag} [Bluray-1080p].mkv")]) for tag in tags} == tags
    # Several versions: the highest priority named wins; a switched-off edition (not in the priorities) is passed over.
    both = [f("Film {edition-IMAX}.mkv"), f("Film {edition-Director's Cut}.mkv")]
    assert editions.edition_badge(both) == "directors-cut" and editions.edition_badge(both, {"imax": 900, "directors-cut": 440}) == "imax"
    assert editions.edition_badge(both, {"imax": 420}) == "imax" and editions.edition_badge(both, {}) is None and editions.edition_badge([]) is None


def test_edition_type_badges_movies_from_the_packs_marks(client, monkeypatch, tmp_path):
    from services.overlays import images
    from services.overlays.types import edition

    ed = get_type("edition")
    desc = ed.describe()
    assert (desc["label"], desc["order"], desc["needs"], desc["hidden_placement"], desc["targets"]) == ("Edition", 65, ["media"], ["text"], ["movie"])
    p = desc["default_placement"]
    assert (p["horizontal_align"], p["horizontal_offset"], p["vertical_align"], p["vertical_offset"], p["back_enabled"], p["gradient_band_height"]) == ("left", 35, "top", 280, True, 200)
    opts = ed.default_options()
    assert opts["badge_height"] == 35 and len(opts["editions"]) == 18 and opts["editions"]["imax"] == {"enabled": True, "priority": 420}
    assert [f["key"] for f in ed.schema()] == ["badge_height", "custom_badges", "editions"] and not any(f["key"] == "apply_to_seasons" for f in ed.schema())
    table = next(f for f in ed.schema() if f["kind"] == "states")
    assert (table["item_label"], table["split"], [f["key"] for f in table["items"][0]["fields"]]) == ("Edition", 2, ["priority"])
    assert ed.variants(opts)[0] == {"key": "ultimate-edition", "label": "Ultimate Edition", "group": "editions", "item": "ultimate-edition"} and len(ed.variants(opts)) == 18
    # The pack's 18 marks, white lettering cropped to the art, the pack's leading dot cut off; IMAX is 80 px tall, the rest 84 (round letters overshoot).
    lib = edition.IMAGES
    assert (lib.default_set, lib.cropped, lib.height) == ("lettering", True, 84)
    assert {f.stem for f in (lib.bundled_dir / "lettering").glob("*.png")} == set(k for k, _l in ed.image_items()) and len(ed.image_items()) == 18
    for key, _label in ed.image_items():
        with Image.open(lib.path(key)) as img:
            assert img.mode == "RGBA" and img.getchannel("A").getbbox() == (0, 0, img.width, img.height) and 80 <= img.height <= 84, key
            assert {img.getchannel(c).getextrema() for c in "RGB"} == {(255, 255)} and img.width > 400, key
            left = img.crop((0, 0, 40, img.height)).getchannel("A").getbbox()
            assert left is not None and left[3] - left[1] > 40, key  # the badge starts with lettering, not the pack's small dot
    # A movie's edition comes from its file name; the badge is sized by height in proportion to its art.
    movie = _ctx("movie", "Film", 2021)
    movie.media = [_file("Film (2021) {edition-Director's Cut} [Remux-2160p][DV HDR10][TrueHD Atmos 7.1].mkv", "4k")]
    badge = ed.resolve(movie, opts, {})
    assert (badge.key, badge.text, badge.image_height, badge.image_width) == ("directors-cut", "Director's Cut", 35, 0) and badge.image.endswith("/edition/lettering/directors-cut.png")
    assert badge.signature == f"directors-cut|Director's Cut|||directors-cut.png@{badge.image_stamp}|0|h=35"
    movie.media = [_file("Film (2021) {edition-IMAX} [Bluray-1080p].mkv", "1080")]
    assert ed.resolve(movie, opts, {}).image_height == 33 and ed.resolve(movie, {**opts, "badge_height": 84}, {}).image_height == 80  # IMAX: 80 of 84
    off = {**opts, "editions": {**opts["editions"], "imax": {"enabled": False, "priority": 420}}}
    assert ed.resolve(movie, off, {}) is None and [v["key"] for v in ed.variants(off)][:1] == ["ultimate-edition"]
    movie.media = [_file("Film (2021) [Bluray-1080p].mkv", "1080")]
    assert ed.resolve(movie, opts, {}) is None
    show = _ctx("show", "Show", 2020)
    show.media = [_file("Show S01E01 {edition-IMAX}.mkv", "4k")]
    assert ed.resolve(show, opts, {}) is None  # movies only
    assert ed.resolve_variant("junk", opts).key == "ultimate-edition" and ed.resolve_variant("junk", off).key == "ultimate-edition" and ed.resolve_variant("uncut", opts).key == "uncut"
    n = ed.normalize_options({"badge_height": "999", "editions": {"imax": {"enabled": "false", "priority": "5000"}, "uncut": "junk"}})
    assert n["badge_height"] == 300 and n["editions"]["imax"] == {"enabled": False, "priority": 1000} and n["editions"]["uncut"] == opts["editions"]["uncut"]
    # The images API: listed, drawn, replaceable (an upload is cropped and scaled to the bundled badge's height).
    monkeypatch.setattr(images, "config_overlays_dir", lambda: tmp_path / "overlays")
    listed = client.get("/api/overlays/images/edition").json()
    assert (listed["set"], listed["noun"], len(listed["images"]), listed["images"][0]["source"]) == ("lettering", "badge", 18, "bundled")
    assert client.get("/api/overlays/images/edition/imax").status_code == 200
    buf = io.BytesIO()
    canvas = Image.new("RGBA", (600, 300), (0, 0, 0, 0))
    canvas.paste((255, 255, 255, 255), (100, 100, 500, 200))
    canvas.save(buf, format="PNG")
    assert client.post("/api/overlays/images/edition/imax", files={"file": ("mine.png", buf.getvalue(), "image/png")}).json()["custom"] is True
    with Image.open(tmp_path / "overlays" / "edition" / "lettering" / "imax.png") as img:
        assert img.size == (320, 80)
    assert ed.resolve_variant("imax", opts).image == str(tmp_path / "overlays" / "edition" / "lettering" / "imax.png")


def test_render_sizes_an_image_badge_by_height(tmp_path):
    a = tmp_path / "a.png"
    Image.new("RGBA", (200, 160), (255, 255, 255, 255)).save(a)
    poster = Image.new("RGB", (1000, 1500), (20, 20, 20))
    place = {**DEFAULT_PLACEMENT, "horizontal_align": "left", "horizontal_offset": 50, "vertical_align": "top", "vertical_offset": 50, "back_enabled": False}
    tall = OverlayBadge(key="t", text="T", image=str(a), image_height=40)
    out, rect = render_badge(poster, tall, place)
    assert rect == (50, 50, 50, 40) and measure_badge(tall, place) == rect  # 40 tall, the width follows the image's shape
    assert out.getpixel((60, 60)) == (255, 255, 255) and out.getpixel((110, 60)) == (20, 20, 20)
    assert tall.signature == "t|T|||a.png|0|h=40" and OverlayBadge(key="t", text="T", image=str(a)).signature == "t|T|||a.png|0"
    # A line of images: the second at its own (smaller) height, centered on the line, after a gap.
    b = tmp_path / "b.png"
    Image.new("RGBA", (300, 160), (255, 0, 0, 255)).save(b)
    line = OverlayBadge(key="t", text="T", image=str(a), image_tail=((str(b), 40),), image_height=80, image_gap=10)
    out, rect = render_badge(poster, line, place)
    assert rect == (50, 50, 185, 80) and measure_badge(line, place) == rect and line.signature == "t|T|||a.png+b.png|0|h=80+40|g=10"  # 100 + 10 + 75 wide
    assert out.getpixel((60, 90)) == (255, 255, 255) and out.getpixel((155, 90)) == (20, 20, 20)   # the first image, then the gap
    assert all(abs(v - e) <= 3 for v, e in zip(out.getpixel((197, 90)), (255, 0, 0)))             # the small one, on the line's middle
    assert out.getpixel((197, 58)) == (20, 20, 20) and out.getpixel((197, 122)) == (20, 20, 20)   # nothing above or below it
    assert render_badge(poster, OverlayBadge(key="t", text="T", image=str(a), image_tail=((str(b), 120),), image_height=80), place)[1] == (50, 50, 325, 120)


def test_soft_band_eases_out_toward_its_end():
    from services.overlays.render import render_badges

    grey = Image.new("RGB", (1000, 1500), (255, 255, 255))
    badge = OverlayBadge(key="x", text="X")
    base = {**DEFAULT_PLACEMENT, "back_enabled": True, "back_style": "band", "gradient_start": "#000000CA", "gradient_band_height": 331}
    assert DEFAULT_PLACEMENT["gradient_band_curve"] == "linear" and normalize_placement({"gradient_band_curve": "SOFT"})["gradient_band_curve"] == "soft"
    assert normalize_placement({"gradient_band_curve": "wavy"})["gradient_band_curve"] == "linear"
    linear, _ = render_badges(grey, [(badge, base)])
    soft, _ = render_badges(grey, [(badge, {**base, "gradient_band_curve": "soft"})])
    dark = lambda img, y: 255 - img.getpixel((900, y))[0]  # how much black the band laid over white
    assert abs(dark(linear, 0) - 202) <= 3 and abs(dark(soft, 0) - 202) <= 3            # both start at the fade color's 79 %
    assert dark(linear, 340) == 0 and dark(soft, 340) == 0                                # and are gone past the height
    # The soft curve: 141 left at 100 px, 68 at 200 px, 12 at 300 px.
    assert [abs(dark(soft, y) - want) <= 6 for y, want in ((100, 141), (200, 68), (300, 12))] == [True, True, True]
    assert dark(soft, 300) < dark(linear, 300) - 4                                        # softer toward the end than the straight line
