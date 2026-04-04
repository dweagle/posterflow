import filecmp
import json
import os
from pathlib import Path

from PIL import Image

from models.setting import Setting
from models.poster import Poster
from services.border_replacer import BorderReplacerService


def _create_source_image(path: Path, color: tuple[int, int, int] = (255, 255, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1000, 1500), color)
    image.save(path)


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
