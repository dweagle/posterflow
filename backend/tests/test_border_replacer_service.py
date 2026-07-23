import filecmp
import json
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from models.setting import Setting
from models.poster import Poster
from services.border_replacer import (
    BorderReplacerService,
    build_border_run_settings,
    _render_bordered_image,
)


def _create_source_image(path: Path, color: tuple[int, int, int] = (255, 255, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1000, 1500), color)
    image.save(path)


def _get_corner_color(path: Path) -> tuple[int, int, int]:
    """Return the top-left corner pixel color of a processed poster."""
    with Image.open(path) as img:
        return img.convert("RGB").getpixel((0, 0))


def test_resolve_effective_colors_uses_active_holiday(test_db):
    test_db.add(
        Setting(
            key="border_replacer_holidays",
            value=json.dumps([
                {
                    "name": "Always On",
                    "schedule": "range(01/01-12/31)",
                    "colors": ["#FF0000", "#00FF00"],
                }
            ]),
        )
    )
    test_db.commit()

    service = BorderReplacerService(test_db)
    is_holiday, holiday_name, colors, style_opts = service._resolve_effective_border_colors(["#0000FF"])

    assert is_holiday is True
    assert holiday_name == "Always On"
    assert colors == ["#FF0000", "#00FF00"]
    assert style_opts["style"] == "solid"  # no style object → solid


def test_main_style_remove_strips_border(test_db, tmp_path):
    """A main border style of 'remove' strips the border (no colors needed) instead of leaving
    the poster unchanged — the STYLE way to remove, distinct from the global toggle."""
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    bordered = Image.new("RGB", (1000, 1500), (0, 255, 0))  # green border
    bordered.paste(Image.new("RGB", (1000 - 52, 1500 - 52), (0, 0, 255)), (26, 26))  # blue interior
    (source_dir / "Movie (2020)").mkdir(parents=True)
    bordered.save(source_dir / "Movie (2020)" / "poster.png")

    service = BorderReplacerService(test_db)
    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=None,       # no colors: 'remove' is a complete config on its own
        remove_borders=False,     # the STYLE removes, not the global toggle
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="full",
        style_opts={"style": "remove"},
    )
    assert result["success"] is True
    # Border stripped: the top-left corner is now the blue interior, not green.
    assert _get_corner_color(destination_dir / "Movie (2020)" / "poster.png") == (0, 0, 255)


def test_border_skips_artwork_files(test_db, tmp_path):
    """Artwork (logo/background/square) now shares the item folder + tmp/ staging with posters,
    so the border replacer must skip it — only posters get bordered."""
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    item = source_dir / "Movie (2020)"
    item.mkdir(parents=True)
    for name in ("poster.png", "logo.png", "background.jpg", "square.jpg"):
        _create_source_image(item / name, (0, 255, 0))
    # flat-named artwork must be skipped too
    _create_source_image(source_dir / "Movie (2020)-logo.png", (0, 255, 0))

    service = BorderReplacerService(test_db)
    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=["#FF0000"],
        remove_borders=False,
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="full",
        style_opts={"style": "solid"},
    )
    assert result["success"] is True
    dest_item = destination_dir / "Movie (2020)"
    assert (dest_item / "poster.png").is_file()                 # poster bordered → placed
    assert _get_corner_color(dest_item / "poster.png") == (255, 0, 0)  # red border applied
    assert not (dest_item / "logo.png").exists()                # artwork skipped
    assert not (dest_item / "background.jpg").exists()
    assert not (dest_item / "square.jpg").exists()
    assert not (destination_dir / "Movie (2020)-logo.png").exists()  # flat artwork skipped


def test_active_holiday_overrides_main_remove_style(test_db, tmp_path):
    """With main style='remove', an active holiday still applies its border — the whole point
    of using the style instead of the global remove-borders toggle."""
    test_db.add(
        Setting(
            key="border_replacer_holidays",
            value=json.dumps([
                {"name": "Always On", "schedule": "range(01/01-12/31)", "colors": ["#FF0000"]}
            ]),
        )
    )
    test_db.commit()

    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    _create_source_image(source_dir / "Movie (2020)" / "poster.png", (0, 255, 0))

    service = BorderReplacerService(test_db)
    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=None,
        remove_borders=False,
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="full",
        style_opts={"style": "remove"},
    )
    assert result["success"] is True
    # The holiday's red border wins over the main 'remove' style.
    assert _get_corner_color(destination_dir / "Movie (2020)" / "poster.png") == (255, 0, 0)


def test_process_posters_uses_default_colors_when_skip_disabled(test_db, tmp_path):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    source_file = source_dir / "Movie Two" / "poster.png"
    destination_file = destination_dir / "Movie Two" / "poster.png"

    _create_source_image(source_file, (255, 255, 255))

    service = BorderReplacerService(test_db)
    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=["#0000FF"],
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="full",
    )

    assert result["success"] is True
    assert result["changed"] == 1
    assert destination_file.exists()
    assert not filecmp.cmp(str(source_file), str(destination_file), shallow=False)



def test_process_posters_holiday_colors_override_default(test_db, tmp_path):
    test_db.add(
        Setting(
            key="border_replacer_holidays",
            value=json.dumps([
                {
                    "name": "Always On",
                    "schedule": "range(01/01-12/31)",
                    "colors": ["#FF0000"],
                }
            ]),
        )
    )
    test_db.commit()

    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    source_file = source_dir / "Movie Three" / "poster.png"
    destination_file = destination_dir / "Movie Three" / "poster.png"

    _create_source_image(source_file, (255, 255, 255))

    service = BorderReplacerService(test_db)
    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=["#0000FF"],
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="full",
    )

    assert result["success"] is True
    assert destination_file.exists()

    with Image.open(destination_file) as output_image:
        top_left = output_image.convert("RGB").getpixel((0, 0))

    assert top_left == (255, 0, 0)


def test_incremental_mode_reprocesses_when_profile_switches_to_holiday(test_db, tmp_path):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    source_file = source_dir / "Movie Four" / "poster.png"

    _create_source_image(source_file, (255, 255, 255))

    service = BorderReplacerService(test_db)

    # First run: non-holiday profile
    first_result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=["#0000FF"],
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="incremental",
    )

    assert first_result["success"] is True
    assert first_result["changed"] == 1

    first_hash_setting = test_db.query(Setting).filter(Setting.key == "border_replacer_settings_hash").first()
    assert first_hash_setting is not None
    first_hash = first_hash_setting.value

    # Enable always-active holiday schedule with same color list.
    # The profile changes from "default" to "holiday:Always On", which should trigger settings reset.
    test_db.add(
        Setting(
            key="border_replacer_holidays",
            value=json.dumps([
                {
                    "name": "Always On",
                    "schedule": "range(01/01-12/31)",
                    "colors": ["#0000FF"],
                }
            ]),
        )
    )
    test_db.commit()

    second_result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=["#0000FF"],
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="incremental",
    )

    assert second_result["success"] is True
    # Should re-evaluate at least one item due to settings/profile hash change
    assert second_result["processed"] == 1

    second_hash_setting = test_db.query(Setting).filter(Setting.key == "border_replacer_settings_hash").first()
    assert second_hash_setting is not None
    assert second_hash_setting.value != first_hash


def test_process_posters_leaves_unchanged_when_no_colors_and_remove_disabled(test_db, tmp_path):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    source_file = source_dir / "Movie Five" / "poster.png"

    _create_source_image(source_file, (255, 255, 255))

    service = BorderReplacerService(test_db)
    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=None,
        remove_borders=False,
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="full",
    )

    dest_file = destination_dir / "Movie Five" / "poster.png"
    # An empty config leaves posters untouched — copied through, borders intact.
    assert result["success"] is True
    assert dest_file.exists()
    assert dest_file.read_bytes() == source_file.read_bytes()


def _poster_with_border(border=(0, 150, 0)) -> "Image.Image":
    """A 1000x1500 poster with a distinct existing border and a vertical-gradient interior."""
    img = Image.new("RGB", (1000, 1500), border)
    draw = ImageDraw.Draw(img)
    for y in range(26, 1474):
        t = (y - 26) / (1474 - 26)
        draw.line([(26, y), (973, y)], fill=(int(30 + 200 * t), 60, 255))
    return img


def test_solid_style_remove_existing_trims_glow_behind_border():
    """Solid/gradient with remove_existing trims a few px PAST the border (to tuck the
    baked-in edge glow behind the new band) then stretches to fill — a clean 1000x1500
    with a crisp border and a different interior than the plain crop. A larger glow_trim
    trims more."""
    src = _poster_with_border()

    plain = _render_bordered_image(src, 26, (255, 0, 0), {"style": "solid"})
    removed = _render_bordered_image(src, 26, (255, 0, 0), {"style": "solid", "remove_existing": True})
    removed_more = _render_bordered_image(src, 26, (255, 0, 0), {"style": "solid", "remove_existing": True, "glow_trim": 60})

    assert plain.size == removed.size == removed_more.size == (1000, 1500)
    # The new border band is crisp red on all outer edges (not squished/shifted).
    assert removed.getpixel((500, 2)) == (255, 0, 0)      # top edge
    assert removed.getpixel((2, 750)) == (255, 0, 0)      # left edge
    assert removed.getpixel((500, 1497)) == (255, 0, 0)   # bottom edge
    # Trimming past the border produces a different, more zoomed interior than a plain crop.
    assert plain.tobytes() != removed.tobytes()
    assert removed.tobytes() != removed_more.tobytes()


def test_process_posters_honors_remove_existing_during_run(test_db, tmp_path):
    """remove_existing takes effect during an actual replacer run (via style_opts),
    not only in the live preview."""
    source_dir = tmp_path / "source"
    (source_dir / "Movie").mkdir(parents=True, exist_ok=True)
    _poster_with_border().save(source_dir / "Movie" / "poster.png")

    service = BorderReplacerService(test_db)

    plain_dir = tmp_path / "plain"
    r1 = service.process_posters(
        source_dir=str(source_dir), destination_dir=str(plain_dir),
        border_colors=["#FF0000"], border_width=26, exclusion_list=[], dry_run=False,
        mode="full", style_opts={"style": "solid"},
    )
    assert r1["success"] is True

    removed_dir = tmp_path / "removed"
    r2 = service.process_posters(
        source_dir=str(source_dir), destination_dir=str(removed_dir),
        border_colors=["#FF0000"], border_width=26, exclusion_list=[], dry_run=False,
        mode="full", style_opts={"style": "solid", "remove_existing": True},
    )
    assert r2["success"] is True

    plain_out = (plain_dir / "Movie" / "poster.png").read_bytes()
    removed_out = (removed_dir / "Movie" / "poster.png").read_bytes()
    assert plain_out != removed_out


def test_remove_existing_trims_more_for_narrow_border():
    """'Remove existing border first' compensates for a narrow band: at the reference width
    (26) and above the trim is unchanged, but below it we trim 1px more per pixel so the
    leftover baked-in border doesn't peek past the narrower band. The trimmed edge becomes
    the black bottom bar (before the band is laid over it), so its height tracks the trim."""
    from services.border_replacer import _remove_existing_base

    src = _poster_with_border()  # green border + gradient interior, contains no black

    def black_bottom(img):
        w, h = img.size
        px = img.convert("RGB")
        n = 0
        for y in range(h - 1, -1, -1):
            if px.getpixel((w // 2, y)) == (0, 0, 0):
                n += 1
            else:
                break
        return n

    at_ref = black_bottom(_remove_existing_base(src, {}, 26))
    above = black_bottom(_remove_existing_base(src, {}, 40))
    below1 = black_bottom(_remove_existing_base(src, {}, 20))
    below2 = black_bottom(_remove_existing_base(src, {}, 12))

    # At/above the reference width the trim is identical (wide band already covers the edge).
    assert at_ref == above
    # Below it, the trim jumps to the FULL reference width so nothing peeks -> a taller black
    # bar, and it's the same no matter how much narrower the border is (full coverage, not a
    # partial slope that would leave a residual line).
    assert below1 > at_ref
    assert below1 == below2


def test_incremental_updates_tracking_when_no_change_needed(test_db, tmp_path):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    source_file = source_dir / "Movie Six" / "poster.png"
    destination_file = destination_dir / "Movie Six" / "poster.png"

    _create_source_image(source_file, (255, 255, 255))
    service = BorderReplacerService(test_db)

    # First run creates destination in remove mode and initializes tracking.
    first_result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=["#0000FF"],
        remove_borders=True,
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="incremental",
    )

    assert first_result["success"] is True

    tracked_before = test_db.query(Poster).filter(Poster.file_path == str(destination_file)).first()
    assert tracked_before is not None

    # Simulate stale incremental tracking so file gets re-evaluated,
    # but destination is already identical (should hit "No change needed" path).
    tracked_before.file_mtime = 0
    test_db.commit()

    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=["#0000FF"],
        remove_borders=True,
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="incremental",
    )

    assert result["success"] is True
    assert result["changed"] == 0
    assert result["skipped"] >= 1

    tracked = test_db.query(Poster).filter(Poster.file_path == str(destination_file)).first()
    assert tracked is not None
    assert tracked.file_mtime is not None
    assert tracked.file_mtime > 0


def test_incremental_reprocesses_when_source_size_changes_with_same_mtime(test_db, tmp_path):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    source_file = source_dir / "Movie Seven" / "poster.png"

    _create_source_image(source_file, (255, 255, 255))
    service = BorderReplacerService(test_db)

    first_result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=["#0000FF"],
        remove_borders=True,
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="incremental",
    )

    assert first_result["success"] is True
    assert first_result["changed"] == 1

    original_mtime = os.path.getmtime(source_file)

    source_file.parent.mkdir(parents=True, exist_ok=True)
    resized_image = Image.new("RGB", (1100, 1600), (0, 0, 0))
    resized_image.save(source_file)
    os.utime(source_file, (original_mtime, original_mtime))

    second_result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=["#0000FF"],
        remove_borders=True,
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="incremental",
    )

    assert second_result["success"] is True
    assert second_result["changed"] == 1


# ---------------------------------------------------------------------------
# Season-specific border tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename,expected", [
    ("Season01.jpg", True),
    ("Season00.jpg", True),   # specials
    ("Season12.png", True),
    ("season01.jpg", True),   # case-insensitive
    ("poster.jpg", False),
    ("poster.jpeg", False),
    ("Season.jpg", False),    # no digits
    ("Season01Extra.jpg", False),
    ("MyShow.jpg", False),
])
def test_is_season_file(filename, expected):
    result = BorderReplacerService._is_season_file(filename)
    assert result is expected


def test_season_mode_remove_strips_borders_from_season_files_only(test_db, tmp_path):
    """season_mode='remove' removes borders from Season files; main posters get colored borders."""
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"

    show_folder = source_dir / "My Show"
    main_poster = show_folder / "poster.png"
    season_poster = show_folder / "Season01.png"

    _create_source_image(main_poster, (200, 200, 200))
    _create_source_image(season_poster, (200, 200, 200))

    service = BorderReplacerService(test_db)
    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=["#FF0000"],
        remove_borders=False,
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="full",
        season_mode="remove",
    )

    assert result["success"] is True
    assert result["changed"] == 2

    dest_main = destination_dir / "My Show" / "poster.png"
    dest_season = destination_dir / "My Show" / "Season01.png"

    assert dest_main.exists()
    assert dest_season.exists()

    # Main poster should have a red border
    assert _get_corner_color(dest_main) == (255, 0, 0)

    # Season poster should NOT have a red border (borders removed)
    assert _get_corner_color(dest_season) != (255, 0, 0)


def _bordered_poster(path: Path, border=(0, 200, 0), interior=(0, 0, 255), bw=26) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1000, 1500), border)
    img.paste(Image.new("RGB", (1000 - 2 * bw, 1500 - 2 * bw), interior), (bw, bw))
    img.save(path)


def _bottom_center_color(path: Path) -> tuple[int, int, int]:
    with Image.open(path) as img:
        w, h = img.size
        return img.convert("RGB").getpixel((w // 2, h - 1))


def test_excluded_season_poster_under_global_remove_strips_all_four_borders(test_db, tmp_path):
    """Regression: with global remove-borders on and season_mode='inherit', a season poster
    whose folder is in the exclusion list must get ALL FOUR borders stripped (exclude=True,
    no black bottom bar), not the top/left/right + black-bar geometry. Guards the exclusion
    predicate against dropping the inherit-season removal case (protected DAPS geometry)."""
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"

    _bordered_poster(source_dir / "Excluded Show (2020)" / "Season01.png")
    _bordered_poster(source_dir / "Normal Show (2021)" / "Season01.png")

    service = BorderReplacerService(test_db)
    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=None,
        remove_borders=True,          # global remove-borders on
        border_width=26,
        exclusion_list=["Excluded Show (2020)"],
        dry_run=False,
        mode="full",
        season_mode="inherit",        # seasons follow the main (removal) path
    )
    assert result["success"] is True

    # Excluded → all four borders removed → bottom is interior (no black bar).
    assert _bottom_center_color(destination_dir / "Excluded Show (2020)" / "Season01.png") == (0, 0, 255)
    # Not excluded → top/left/right removed + black bottom bar convention.
    assert _bottom_center_color(destination_dir / "Normal Show (2021)" / "Season01.png") == (0, 0, 0)


def test_season_mode_colors_applies_different_colors_to_seasons(test_db, tmp_path):
    """season_mode='colors' applies season-specific colors to Season files."""
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"

    show_folder = source_dir / "My Show"
    main_poster = show_folder / "poster.png"
    season_poster = show_folder / "Season01.png"

    _create_source_image(main_poster, (200, 200, 200))
    _create_source_image(season_poster, (200, 200, 200))

    service = BorderReplacerService(test_db)
    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=["#FF0000"],
        remove_borders=False,
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="full",
        season_mode="colors",
        season_border_colors=["#0000FF"],
    )

    assert result["success"] is True
    assert result["changed"] == 2

    dest_main = destination_dir / "My Show" / "poster.png"
    dest_season = destination_dir / "My Show" / "Season01.png"

    assert _get_corner_color(dest_main) == (255, 0, 0)    # red main border
    assert _get_corner_color(dest_season) == (0, 0, 255)  # blue season border


def test_season_mode_custom_style_renders_independently(test_db, tmp_path):
    """season_mode='custom' gives seasons their own full style (gradient here) while
    main posters keep the main solid style."""
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"

    _create_source_image(source_dir / "Show" / "poster.png", (255, 255, 255))
    _create_source_image(source_dir / "Show" / "Season01.png", (255, 255, 255))

    service = BorderReplacerService(test_db)
    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=["#FF0000"],          # main solid red
        border_width=50,
        exclusion_list=[],
        dry_run=False,
        mode="full",
        season_mode="custom",
        season_border_colors=[],
        season_border_width=50,
        style_opts={"style": "solid"},
        season_style_opts={"style": "gradient", "gradient_colors": ["#00FF00", "#0000FF"], "gradient_direction": "vertical"},
    )
    assert result["success"] is True

    # Main poster: solid red border.
    main_corner = _get_corner_color(destination_dir / "Show" / "poster.png")
    assert main_corner[0] > 150 and main_corner[1] < 100 and main_corner[2] < 100

    # Season poster: independent gradient (green<->blue), non-uniform top vs bottom.
    with Image.open(destination_dir / "Show" / "Season01.png") as out:
        season = out.convert("RGB")
        top = season.getpixel((500, 2))
        bottom = season.getpixel((500, 1497))
    assert top != bottom
    ends = (top, bottom)
    assert any(px[1] > 120 and px[0] < 120 for px in ends)  # a greenish end
    assert any(px[2] > 120 and px[0] < 120 for px in ends)  # a bluish end


def test_settings_hash_differs_when_season_style_changes(test_db):
    """Changing the custom season style changes the settings hash (drives reprocessing)."""
    service = BorderReplacerService(test_db)

    base = service.calculate_settings_hash(
        ["#FF0000"], 26, [], season_mode="custom",
        season_style_opts={"style": "solid", "inner_effect": "none"},
    )
    changed = service.calculate_settings_hash(
        ["#FF0000"], 26, [], season_mode="custom",
        season_style_opts={"style": "gradient", "gradient_colors": ["#00FF00", "#0000FF"]},
    )
    assert base != changed


def test_season_mode_inherit_treats_seasons_same_as_main(test_db, tmp_path):
    """season_mode='inherit' (default) applies identical color logic to all files."""
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"

    show_folder = source_dir / "My Show"
    main_poster = show_folder / "poster.png"
    season_poster = show_folder / "Season01.png"

    _create_source_image(main_poster, (200, 200, 200))
    _create_source_image(season_poster, (200, 200, 200))

    service = BorderReplacerService(test_db)
    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=["#00FF00"],
        remove_borders=False,
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="full",
        season_mode="inherit",
    )

    assert result["success"] is True
    assert result["changed"] == 2

    dest_main = destination_dir / "My Show" / "poster.png"
    dest_season = destination_dir / "My Show" / "Season01.png"

    assert _get_corner_color(dest_main) == (0, 255, 0)
    assert _get_corner_color(dest_season) == (0, 255, 0)


def test_season_mode_colors_empty_falls_back_to_inherit(test_db, tmp_path):
    """season_mode='colors' with no colors falls back to inherit behavior."""
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"

    show_folder = source_dir / "My Show"
    main_poster = show_folder / "poster.png"
    season_poster = show_folder / "Season01.png"

    _create_source_image(main_poster, (200, 200, 200))
    _create_source_image(season_poster, (200, 200, 200))

    service = BorderReplacerService(test_db)
    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=["#FF0000"],
        remove_borders=False,
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="full",
        season_mode="colors",
        season_border_colors=[],
    )

    assert result["success"] is True
    dest_season = destination_dir / "My Show" / "Season01.png"
    # Falls back to main color (red)
    assert _get_corner_color(dest_season) == (255, 0, 0)


def test_settings_hash_differs_when_season_params_change(test_db):
    """Changing season_mode or season_colors produces a different settings hash."""
    service = BorderReplacerService(test_db)

    hash_inherit = service.calculate_settings_hash(
        border_colors=["#FF0000"],
        border_width=26,
        exclusion_list=[],
        season_mode="inherit",
        season_border_colors=[],
    )

    hash_remove = service.calculate_settings_hash(
        border_colors=["#FF0000"],
        border_width=26,
        exclusion_list=[],
        season_mode="remove",
        season_border_colors=[],
    )

    hash_colors = service.calculate_settings_hash(
        border_colors=["#FF0000"],
        border_width=26,
        exclusion_list=[],
        season_mode="colors",
        season_border_colors=["#0000FF"],
    )

    assert hash_inherit != hash_remove
    assert hash_inherit != hash_colors
    assert hash_remove != hash_colors


# --- Border style + inner-edge effect rendering ---

def _crop_for(image: Image.Image, border_width: int) -> Image.Image:
    width, height = image.size
    return image.crop((border_width, border_width, width - border_width, height - border_width))


def test_solid_none_matches_legacy_render():
    """Solid style with no inner effect must be byte-identical to the legacy render path."""
    source = Image.new("RGB", (1000, 1500), (10, 20, 30))
    bw = 26
    cropped = _crop_for(source, bw)

    # Legacy path: new canvas filled with border color, paste art, resize, convert.
    legacy = Image.new("RGB", (cropped.width + 2 * bw, cropped.height + 2 * bw), (200, 100, 50))
    legacy.paste(cropped, (bw, bw))
    legacy = legacy.resize((1000, 1500)).convert("RGB")

    rendered_default = _render_bordered_image(source, bw, (200, 100, 50), None)
    rendered_explicit = _render_bordered_image(source, bw, (200, 100, 50), {"style": "solid", "inner_effect": "none"})

    assert rendered_default.tobytes() == legacy.tobytes()
    assert rendered_explicit.tobytes() == legacy.tobytes()


def test_gradient_style_produces_non_uniform_band():
    """A vertical gradient border varies from top to bottom (red -> blue)."""
    source = Image.new("RGB", (1000, 1500), (255, 255, 255))
    bw = 50

    out = _render_bordered_image(
        source,
        bw,
        (0, 0, 0),
        {"style": "gradient", "gradient_colors": ["#FF0000", "#0000FF"], "gradient_direction": "vertical"},
    )

    top = out.getpixel((500, 2))
    bottom = out.getpixel((500, 1497))
    # The FIRST color stop (red) sits at the top, the last (blue) at the bottom — matching
    # horizontal's first-at-start convention and the top-to-bottom order of the color list.
    assert top[0] > top[2] + 50     # top is reddish (first stop)
    assert bottom[2] > bottom[0] + 50  # bottom is bluish (last stop)


def test_inner_glow_darkens_edge_not_center():
    """A dark inner glow darkens the inner border edge but leaves the art center untouched."""
    source = Image.new("RGB", (1000, 1500), (255, 255, 255))
    bw = 30

    out = _render_bordered_image(
        source,
        bw,
        (255, 255, 255),  # white border so only the glow changes pixels
        {"style": "solid", "inner_effect": "glow", "inner_color": "#000000", "inner_opacity": 100, "inner_width": 20},
    )

    edge = out.getpixel((500, bw))       # first glow ring, fully opaque black
    center = out.getpixel((500, 750))
    assert sum(edge) < sum(center)
    assert center == (255, 255, 255)


def test_border_fade_bleeds_border_color_inward():
    """Border-color fade pushes the border color into the art at the inner edge."""
    source = Image.new("RGB", (1000, 1500), (255, 255, 255))
    bw = 30

    out = _render_bordered_image(
        source,
        bw,
        (255, 0, 0),  # red border
        {"style": "solid", "inner_effect": "fade", "fade_width": 20},
    )

    edge = out.getpixel((500, bw))  # i=0 → full border color
    center = out.getpixel((500, 750))
    assert edge[0] > edge[1] and edge[0] > edge[2]  # reddish
    assert center == (255, 255, 255)                 # untouched art


def test_image_overlay_composites_frame_and_keeps_center(tmp_path):
    """An image-overlay frame replaces the border while the transparent center shows the art."""
    frame = Image.new("RGBA", (1000, 1500), (0, 0, 0, 0))
    ImageDraw.Draw(frame).rectangle([0, 0, 999, 1499], outline=(255, 0, 255, 255), width=40)
    frame_path = tmp_path / "frame.png"
    frame.save(frame_path)

    # White poster with a distinct marker pixel inside the transparent-center region.
    source = Image.new("RGB", (1000, 1500), (255, 255, 255))
    source.putpixel((500, 750), (10, 200, 30))
    bw = 26

    out = _render_bordered_image(source, bw, (0, 0, 0), {"style": "image", "overlay_path": str(frame_path)})

    assert out.getpixel((5, 5)) == (255, 0, 255)        # opaque frame
    # Image overlays do NOT crop/expand the poster: the marker stays at the same spot.
    assert out.getpixel((500, 750)) == (10, 200, 30)
    assert out.getpixel((600, 750)) == (255, 255, 255)  # surrounding art untouched


def test_image_overlay_missing_path_falls_back_to_solid():
    """A missing overlay path falls back to a solid border instead of erroring."""
    source = Image.new("RGB", (1000, 1500), (255, 255, 255))
    bw = 26

    out = _render_bordered_image(source, bw, (12, 34, 56), {"style": "image", "overlay_path": "/does/not/exist.png"})
    assert out.getpixel((2, 2)) == (12, 34, 56)  # solid border color


def test_image_overlay_remove_existing_trims_edge(tmp_path):
    """remove_existing DAPS-strips a few px off the edge (glow_trim) and resizes UP before
    compositing the frame — the same 'trim less' approach as the solid/gradient band — so
    it differs from keeping the full poster, and a larger glow_trim trims more."""
    frame = Image.new("RGBA", (1000, 1500), (0, 0, 0, 0))
    ImageDraw.Draw(frame).rectangle([0, 0, 999, 1499], outline=(255, 255, 255, 255), width=40)
    frame_path = tmp_path / "frame.png"
    frame.save(frame_path)

    source = _poster_with_border()  # green edge + gradient interior
    bw = 26

    keep = _render_bordered_image(source, bw, (0, 0, 0), {"style": "image", "overlay_path": str(frame_path)})
    trimmed = _render_bordered_image(source, bw, (0, 0, 0), {"style": "image", "overlay_path": str(frame_path), "remove_existing": True})
    trimmed_less = _render_bordered_image(source, bw, (0, 0, 0), {"style": "image", "overlay_path": str(frame_path), "remove_existing": True, "glow_trim": 4})

    assert keep.size == trimmed.size == (1000, 1500)
    assert keep.tobytes() != trimmed.tobytes()          # the edge glow was trimmed
    assert trimmed.tobytes() != trimmed_less.tobytes()  # glow_trim controls how much


def test_inner_glow_applies_over_image_overlay(tmp_path):
    """An inner-edge effect is applied on top of an image-overlay frame when set."""
    frame = Image.new("RGBA", (1000, 1500), (0, 0, 0, 0))
    ImageDraw.Draw(frame).rectangle([0, 0, 999, 1499], outline=(255, 0, 255, 255), width=12)  # thin frame
    frame_path = tmp_path / "frame.png"
    frame.save(frame_path)

    source = Image.new("RGB", (1000, 1500), (255, 255, 255))
    bw = 26
    out = _render_bordered_image(
        source,
        bw,
        (0, 0, 0),
        {"style": "image", "overlay_path": str(frame_path), "inner_effect": "glow", "inner_color": "#000000", "inner_opacity": 100, "inner_width": 20},
    )

    edge = out.getpixel((500, bw))      # just inside the border, in the transparent center
    center = out.getpixel((500, 750))
    assert sum(edge) < sum(center)       # glow darkened the inner edge
    assert center == (255, 255, 255)     # art center untouched


def test_settings_hash_changes_with_style_effect_and_overlay(test_db):
    """Changing border style, inner-effect params, or overlay image changes the settings hash."""
    service = BorderReplacerService(test_db)

    base = service.calculate_settings_hash(["#000000"], 26, [])
    gradient = service.calculate_settings_hash(
        ["#000000"], 26, [], style_opts={"style": "gradient", "gradient_colors": ["#FF0000", "#0000FF"]}
    )
    assert base != gradient

    glow_70 = service.calculate_settings_hash(["#000000"], 26, [], style_opts={"inner_effect": "glow", "inner_opacity": 70})
    glow_40 = service.calculate_settings_hash(["#000000"], 26, [], style_opts={"inner_effect": "glow", "inner_opacity": 40})
    assert glow_70 != glow_40

    img_a = service.calculate_settings_hash(["#000000"], 26, [], style_opts={"style": "image", "overlay_path": "/x/a.png"})
    img_b = service.calculate_settings_hash(["#000000"], 26, [], style_opts={"style": "image", "overlay_path": "/y/b.png"})
    assert img_a != img_b  # overlay name participates in the hash


def test_holiday_border_image_overrides_with_overlay(test_db, tmp_path, monkeypatch):
    """An active holiday with a border_image composites that frame over posters."""
    import services.border_replacer as br

    overlay_dir = tmp_path / "overlays"
    overlay_dir.mkdir()
    frame = Image.new("RGBA", (1000, 1500), (0, 0, 0, 0))
    ImageDraw.Draw(frame).rectangle([0, 0, 999, 1499], outline=(0, 255, 0, 255), width=40)
    frame.save(overlay_dir / "xmas.png")
    monkeypatch.setattr(br, "USER_OVERLAY_DIR", overlay_dir)

    test_db.add(
        Setting(
            key="border_replacer_holidays",
            value=json.dumps([
                {"name": "Always", "schedule": "range(01/01-12/31)", "colors": ["#FF0000"], "border_image": "xmas.png"}
            ]),
        )
    )
    test_db.commit()

    source_dir = tmp_path / "source"
    dest_dir = tmp_path / "dest"
    _create_source_image(source_dir / "Movie" / "poster.jpg", (255, 255, 255))

    service = BorderReplacerService(test_db)
    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(dest_dir),
        border_colors=["#0000FF"],
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="full",
    )

    assert result["success"] is True
    with Image.open(dest_dir / "Movie" / "poster.jpg") as out_img:
        out = out_img.convert("RGB")
        frame_px = out.getpixel((5, 5))
        center_px = out.getpixel((500, 750))
    # JPEG compression shifts values, so assert dominant channels rather than equality.
    assert frame_px[1] > 150 and frame_px[0] < 100 and frame_px[2] < 100  # green frame
    assert center_px[0] > 200 and center_px[1] > 200 and center_px[2] > 200  # white art center


def test_build_border_run_settings_includes_style_and_custom_season(test_db):
    """The shared settings helper (used by the workflow, Plex-webhook pre-upload, and
    post-rename runs) carries the main style AND the custom season style, and keeps the
    'custom' season mode — the settings the webhook path used to drop."""
    test_db.add(Setting(key="border_replacer_style", value="gradient"))
    test_db.add(Setting(key="border_replacer_gradient_colors", value=json.dumps(["#FF0000", "#0000FF"])))
    test_db.add(Setting(key="border_replacer_season_mode", value="custom"))
    test_db.add(Setting(key="border_replacer_season_style", value="image"))
    test_db.add(Setting(key="border_replacer_season_inner_effect", value="glow"))
    test_db.commit()

    kwargs = build_border_run_settings(test_db)

    assert kwargs["season_mode"] == "custom"                     # not downgraded to inherit
    assert kwargs["style_opts"]["style"] == "gradient"           # main style carried
    assert kwargs["season_style_opts"] is not None
    assert kwargs["season_style_opts"]["style"] == "image"       # season style carried
    assert kwargs["season_style_opts"]["inner_effect"] == "glow"


def test_build_border_run_settings_no_season_style_when_not_custom(test_db):
    """season_style_opts is only built for custom season mode."""
    test_db.add(Setting(key="border_replacer_season_mode", value="remove"))
    test_db.commit()

    kwargs = build_border_run_settings(test_db)
    assert kwargs["season_mode"] == "remove"
    assert kwargs["season_style_opts"] is None


def test_build_border_run_settings_splits_removal_and_band_width(test_db):
    """The top width is the removal width; a separate band width is used when adding. The
    Remove Borders toggle selects which one process_posters receives."""
    test_db.add(Setting(key="border_replacer_width", value="30"))        # removal width
    test_db.add(Setting(key="border_replacer_band_width", value="50"))   # added-band width
    test_db.commit()

    # Adding borders (toggle off) → band width.
    assert build_border_run_settings(test_db)["border_width"] == 50

    # Removing borders (toggle on) → removal width.
    test_db.add(Setting(key="border_replacer_remove_borders", value="true"))
    test_db.commit()
    assert build_border_run_settings(test_db)["border_width"] == 30


def test_build_border_run_settings_band_width_falls_back_to_removal_width(test_db):
    """Configs saved before the split (no band width) keep using the single width for adding."""
    test_db.add(Setting(key="border_replacer_width", value="40"))
    test_db.commit()
    assert build_border_run_settings(test_db)["border_width"] == 40


def test_holiday_custom_style_applies_gradient(test_db, tmp_path):
    """A holiday carrying a full `style` object (gradient) renders that style."""
    test_db.add(
        Setting(
            key="border_replacer_holidays",
            value=json.dumps([
                {
                    "name": "Always",
                    "schedule": "range(01/01-12/31)",
                    "colors": ["#FF0000"],
                    "style": {
                        "style": "gradient",
                        "gradient_colors": ["#00FF00", "#0000FF"],
                        "gradient_direction": "vertical",
                    },
                }
            ]),
        )
    )
    test_db.commit()

    source_dir = tmp_path / "source"
    dest_dir = tmp_path / "dest"
    _create_source_image(source_dir / "Movie" / "poster.png", (255, 255, 255))

    service = BorderReplacerService(test_db)
    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(dest_dir),
        border_colors=["#0000FF"],
        border_width=50,
        exclusion_list=[],
        dry_run=False,
        mode="full",
        style_opts={"style": "solid"},
    )

    assert result["success"] is True
    with Image.open(dest_dir / "Movie" / "poster.png") as out_img:
        out = out_img.convert("RGB")
        top = out.getpixel((500, 2))
        bottom = out.getpixel((500, 1497))
    assert top != bottom  # holiday gradient applied, not the main solid blue
    ends = (top, bottom)
    assert any(px[1] > 120 and px[0] < 120 for px in ends)  # green end
    assert any(px[2] > 120 and px[0] < 120 for px in ends)  # blue end


def test_gradient_style_without_solid_colors_applies_gradient(test_db, tmp_path):
    """Gradient style is self-sufficient: no solid colors needed, and it must not
    fall through to border removal."""
    source_dir = tmp_path / "source"
    dest_dir = tmp_path / "dest"
    _create_source_image(source_dir / "Movie" / "poster.png", (255, 255, 255))

    service = BorderReplacerService(test_db)
    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(dest_dir),
        border_colors=None,
        border_width=50,
        exclusion_list=[],
        dry_run=False,
        mode="full",
        style_opts={"style": "gradient", "gradient_colors": ["#FF0000", "#0000FF"], "gradient_direction": "vertical"},
    )

    assert result["success"] is True
    with Image.open(dest_dir / "Movie" / "poster.png") as out_img:
        out = out_img.convert("RGB")
        top = out.getpixel((500, 2))
        bottom = out.getpixel((500, 1497))
    assert top != bottom
    ends = (top, bottom)
    assert any(px[0] > px[2] + 50 for px in ends)  # a reddish end
    assert any(px[2] > px[0] + 50 for px in ends)  # a bluish end
