import filecmp
import json
import os
from pathlib import Path

import pytest
from PIL import Image

from models.setting import Setting
from models.poster import Poster
from services.border_replacer import BorderReplacerService


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
    is_holiday, holiday_name, colors = service._resolve_effective_border_colors(["#0000FF"])

    assert is_holiday is True
    assert holiday_name == "Always On"
    assert colors == ["#FF0000", "#00FF00"]


def test_process_posters_skips_outside_holiday_and_copies_unchanged(test_db, tmp_path):
    test_db.add(Setting(key="border_replacer_skip_non_holiday", value="true"))
    test_db.add(
        Setting(
            key="border_replacer_holidays",
            value=json.dumps([
                {
                    "name": "Future Holiday",
                    "schedule": "range(12/31-12/31)",
                    "colors": ["#FF0000"],
                }
            ]),
        )
    )
    test_db.commit()

    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    source_file = source_dir / "Movie One" / "poster.png"
    destination_file = destination_dir / "Movie One" / "poster.png"

    _create_source_image(source_file, (12, 34, 56))

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
    assert filecmp.cmp(str(source_file), str(destination_file), shallow=False)


def test_process_posters_skips_outside_holiday_incremental_restores_missing_from_tmp(test_db, tmp_path):
    test_db.add(Setting(key="border_replacer_skip_non_holiday", value="true"))
    test_db.add(
        Setting(
            key="border_replacer_holidays",
            value=json.dumps([
                {
                    "name": "Future Holiday",
                    "schedule": "range(12/31-12/31)",
                    "colors": ["#FF0000"],
                }
            ]),
        )
    )
    test_db.commit()

    source_dir = tmp_path / "tmp"
    destination_dir = tmp_path / "assets"

    source_missing_file = source_dir / "Movie Missing" / "poster.png"
    source_same_file = source_dir / "Movie Same" / "poster.png"
    destination_same_file = destination_dir / "Movie Same" / "poster.png"
    destination_missing_file = destination_dir / "Movie Missing" / "poster.png"

    _create_source_image(source_missing_file, (10, 20, 30))
    _create_source_image(source_same_file, (11, 22, 33))
    _create_source_image(destination_same_file, (11, 22, 33))

    service = BorderReplacerService(test_db)
    result = service.process_posters(
        source_dir=str(source_dir),
        destination_dir=str(destination_dir),
        border_colors=["#0000FF"],
        border_width=26,
        exclusion_list=[],
        dry_run=False,
        mode="incremental",
    )

    assert result["success"] is True
    assert result["changed"] == 1
    assert result["skipped"] == 1
    assert destination_missing_file.exists()
    assert filecmp.cmp(str(source_missing_file), str(destination_missing_file), shallow=False)
    assert filecmp.cmp(str(source_same_file), str(destination_same_file), shallow=False)


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


def test_process_posters_fails_when_no_colors_and_remove_disabled(test_db, tmp_path):
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

    assert result["success"] is False
    assert "No border colors configured" in result["error"]


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
