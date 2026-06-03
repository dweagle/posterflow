"""
Border Replacer Service

Applies or removes borders from poster images.
Adapted from DAPS border_replacerr.py - image processing logic preserved exactly.
"""

import filecmp
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from core.logging import (
    logger,
    LogTags,
    log_success,
    log_error,
    log_warning,
    log_info,
    log_debug,
    log_section_start,
    log_section_end,
)
from models.setting import get_setting, upsert_setting

try:
    from PIL import Image, UnidentifiedImageError
except ImportError as e:
    raise ImportError(
        f"PIL/Pillow not installed: {e}\n"
        "Install with: pip install Pillow"
    )


def _drop_file_cache(path: str) -> None:
    """Advise the kernel to evict a file's pages from the page cache."""
    try:
        with open(path, "rb") as f:
            os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
    except (AttributeError, OSError, Exception):
        pass


class BorderReplacerService:
    """Service for applying or removing borders from poster images."""

    ProgressCallback = Callable[[str, int, int, str], None]

    def __init__(self, db: Session) -> None:
        self.db = db
        self.logger = logger

    @staticmethod
    def _is_season_file(filename: str) -> bool:
        """Return True if the filename represents a season poster (e.g. Season01.jpg)."""
        import re as _re
        name = os.path.splitext(filename)[0]
        return bool(_re.match(r"^Season\d+$", name, _re.IGNORECASE))

    def calculate_settings_hash(
        self,
        border_colors: Optional[List[str]],
        border_width: int,
        exclusion_list: Optional[List[str]],
        processing_profile: Optional[str] = None,
        season_mode: str = "inherit",
        season_border_colors: Optional[List[str]] = None,
    ) -> str:
        """
        Calculate a hash of border replacer settings to detect changes.
        When settings change, we need to reprocess all files even in incremental mode.
        
        Args:
            border_colors: List of hex colors or None
            border_width: Border width in pixels
            exclusion_list: List of titles to exclude
            season_mode: Season border mode ("inherit", "remove", "colors")
            season_border_colors: Colors to use for season posters when season_mode is "colors"
            
        Returns:
            MD5 hash of the settings as a hex string
        """
        settings_dict = {
            "colors": sorted(border_colors or []),  # Sort for consistent hashing
            "width": border_width,
            "exclusions": sorted(exclusion_list or []),  # Sort for consistent hashing
            "profile": processing_profile or "default",
            "season_mode": season_mode,
            "season_colors": sorted(season_border_colors or []),
        }
        settings_json = json.dumps(settings_dict, sort_keys=True)
        return hashlib.md5(settings_json.encode(), usedforsecurity=False).hexdigest()

    def detect_settings_change(
        self,
        border_colors: Optional[List[str]],
        border_width: int,
        exclusion_list: Optional[List[str]],
        destination_dir: str,
        dry_run: bool = False,
        processing_profile: Optional[str] = None,
        season_mode: str = "inherit",
        season_border_colors: Optional[List[str]] = None,
    ) -> bool:
        """
        Check if border replacer settings have changed since last run.
        If changed, reset incremental tracking so all files are reprocessed.
        
        Args:
            border_colors: Current border colors
            border_width: Current border width
            exclusion_list: Current exclusion list
            destination_dir: Destination directory path to scope the reset
            dry_run: If True, only log what would happen without modifying database
            season_mode: Season border mode ("inherit", "remove", "colors")
            season_border_colors: Colors for season posters when season_mode is "colors"
            
        Returns:
            True if settings changed (or would change in dry run), False otherwise
        """
        from models.poster import Poster
        
        # Calculate current settings hash
        current_hash = self.calculate_settings_hash(
            border_colors,
            border_width,
            exclusion_list,
            processing_profile=processing_profile,
            season_mode=season_mode,
            season_border_colors=season_border_colors,
        )
        
        # Get stored hash from database
        stored_hash_setting = get_setting(self.db, "border_replacer_settings_hash")
        
        stored_hash = stored_hash_setting.value if stored_hash_setting else None
        
        # If hash changed, reset tracking for posters in destination directory
        if stored_hash != current_hash:
            if dry_run:
                # During dry run, only log what would happen - don't modify database
                if stored_hash:
                    log_info(
                        LogTags.BORDER_REPLACER,
                        "[DRY RUN] Border settings changed - would reset incremental tracking",
                        old_hash=stored_hash[:8],
                        new_hash=current_hash[:8]
                    )
                else:
                    log_debug(
                        LogTags.BORDER_REPLACER,
                        "[DRY RUN] Would initialize settings tracking",
                        hash=current_hash[:8]
                    )
                
                # Count how many would be reset
                try:
                    dest_pattern = destination_dir.rstrip('/') + '/%'
                    count = self.db.query(Poster).filter(
                        Poster.file_path.like(dest_pattern)
                    ).count()
                    
                    if count > 0:
                        log_info(
                            LogTags.BORDER_REPLACER,
                            f"[DRY RUN] Would reset tracking for {count} poster(s) in destination directory",
                            count=count,
                            destination=destination_dir
                        )
                except Exception as e:
                    log_error(LogTags.BORDER_REPLACER, f"Failed to count posters: {e}")
                
                return True
            else:
                # Real run - actually modify the database
                if stored_hash:
                    log_info(
                        LogTags.BORDER_REPLACER,
                        "Border settings changed - resetting incremental tracking",
                        old_hash=stored_hash[:8],
                        new_hash=current_hash[:8]
                    )
                else:
                    log_debug(
                        LogTags.BORDER_REPLACER,
                        "Initializing settings tracking",
                        hash=current_hash[:8]
                    )
                
                # Reset file_mtime values only for posters in the destination directory
                # This avoids resetting tracking for all 100k+ posters from synced drives
                try:
                    # Normalize destination_dir path for comparison
                    dest_pattern = destination_dir.rstrip('/') + '/%'
                    
                    updated_count = self.db.query(Poster).filter(
                        Poster.file_path.like(dest_pattern)
                    ).update({"file_mtime": 0}, synchronize_session=False)
                    self.db.commit()
                    
                    if updated_count > 0:
                        log_info(
                            LogTags.BORDER_REPLACER,
                            f"Reset tracking for {updated_count} poster(s) in destination directory",
                            count=updated_count,
                            destination=destination_dir
                        )
                except Exception as e:
                    log_error(LogTags.BORDER_REPLACER, f"Failed to reset tracking: {e}")
                    self.db.rollback()
                
                # Store new hash (only during real runs)
                upsert_setting(self.db, "border_replacer_settings_hash", current_hash)
                
                try:
                    self.db.commit()
                except Exception as e:
                    log_error(LogTags.BORDER_REPLACER, f"Failed to save settings hash: {e}")
                    self.db.rollback()
                
                return True
        
        return False

    def convert_to_rgb(self, hex_color: str) -> Tuple[int, int, int]:
        """
        Converts a hexadecimal color string to an RGB tuple.

        Args:
            hex_color: Hexadecimal color string (e.g., "#FF0000" or "FF0000")

        Returns:
            RGB color tuple (r, g, b)
        """
        hex_color = hex_color.strip("#")
        if len(hex_color) == 3:
            hex_color = hex_color * 2
        try:
            color_code = tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
            return color_code
        except ValueError:
            log_error(
                LogTags.BORDER_REPLACER,
                f"Invalid hex color: {hex_color}, defaulting to white",
                color=hex_color,
            )
            return (255, 255, 255)

    def _is_within_holiday_range(self, schedule: str, now: datetime) -> bool:
        """Check whether current date is inside a DAPS-style holiday range schedule."""
        if not schedule or not schedule.startswith("range(") or not schedule.endswith(")"):
            return False

        try:
            inside = schedule[len("range(") : -1]
            start_str, end_str = inside.split("-", 1)
            start_month, start_day = map(int, start_str.split("/"))
            end_month, end_day = map(int, end_str.split("/"))
        except Exception:
            log_warning(
                LogTags.BORDER_REPLACER,
                f"Invalid holiday schedule format '{schedule}' - expected range(MM/DD-MM/DD)",
            )
            return False

        year = now.year
        start_date = datetime(year, start_month, start_day)
        end_date = datetime(year, end_month, end_day)

        if end_date < start_date:
            if now.month < start_month:
                start_date = start_date.replace(year=year - 1)
            else:
                end_date = end_date.replace(year=year + 1)

        return start_date <= now <= end_date

    def _load_holiday_schedules(self) -> List[Dict[str, Any]]:
        """Load holiday schedules from DB setting `border_replacer_holidays`."""
        holidays_setting = get_setting(self.db, "border_replacer_holidays")
        if not holidays_setting or not holidays_setting.value:
            return []

        try:
            parsed = json.loads(holidays_setting.value)
        except json.JSONDecodeError:
            log_warning(
                LogTags.BORDER_REPLACER,
                "Invalid border_replacer_holidays JSON - ignoring holiday schedules",
            )
            return []

        normalized: List[Dict[str, Any]] = []
        if isinstance(parsed, dict):
            for holiday_name, data in parsed.items():
                if not isinstance(data, dict):
                    continue
                colors = data.get("colors", data.get("color", []))
                if isinstance(colors, str):
                    colors = [colors]
                normalized.append(
                    {
                        "name": str(holiday_name),
                        "schedule": str(data.get("schedule", "")).strip(),
                        "colors": [str(c) for c in (colors or []) if str(c).strip()],
                    }
                )
        elif isinstance(parsed, list):
            for entry in parsed:
                if not isinstance(entry, dict):
                    continue
                colors = entry.get("colors", entry.get("color", []))
                if isinstance(colors, str):
                    colors = [colors]
                name = str(entry.get("name", "")).strip()
                schedule = str(entry.get("schedule", "")).strip()
                if not name or not schedule:
                    continue
                normalized.append(
                    {
                        "name": name,
                        "schedule": schedule,
                        "colors": [str(c) for c in (colors or []) if str(c).strip()],
                    }
                )

        return normalized

    def _resolve_effective_border_colors(
        self,
        default_border_colors: Optional[List[str]],
    ) -> Tuple[bool, Optional[str], List[str]]:
        """
        Resolve active holiday colors for today if a holiday schedule matches.

        Returns:
            (is_holiday_active, holiday_name, effective_border_colors)
        """
        now = datetime.now()
        default_colors = list(default_border_colors or [])
        holiday_schedules = self._load_holiday_schedules()

        for holiday in holiday_schedules:
            schedule = holiday.get("schedule", "")
            if not self._is_within_holiday_range(schedule, now):
                continue

            holiday_name = str(holiday.get("name", "Holiday")).strip() or "Holiday"
            holiday_colors = holiday.get("colors") or default_colors
            if isinstance(holiday_colors, str):
                holiday_colors = [holiday_colors]

            effective_colors = [str(c) for c in (holiday_colors or []) if str(c).strip()]
            return True, holiday_name, effective_colors

        return False, None, default_colors

    def _copy_images_without_border_changes(
        self,
        source_dir: str,
        destination_dir: str,
        dry_run: bool,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        """Copy images from source to destination without border transformations."""
        image_entries: List[Tuple[str, str]] = []
        for root, _, files in os.walk(source_dir):
            rel_path = os.path.relpath(root, source_dir)
            folder = None if rel_path == "." else rel_path
            for file in files:
                if not file.lower().endswith((".jpg", ".jpeg", ".png")):
                    continue
                src_file = os.path.join(root, file)
                dest_file = (
                    os.path.join(destination_dir, folder, file)
                    if folder
                    else os.path.join(destination_dir, file)
                )
                image_entries.append((src_file, dest_file))

        total_items = len(image_entries)
        if total_items == 0:
            return {
                "success": False,
                "error": "No poster files found in source directory",
            }

        changed_count = 0
        skipped_count = 0

        for idx, (src_file, dest_file) in enumerate(image_entries, start=1):
            if progress_callback:
                progress_callback(
                    "copying",
                    idx,
                    total_items,
                    f"Copying: {os.path.basename(src_file)}",
                )

            if dry_run:
                changed_count += 1
                continue

            os.makedirs(os.path.dirname(dest_file), exist_ok=True)

            if os.path.exists(dest_file) and filecmp.cmp(src_file, dest_file, shallow=False):
                skipped_count += 1
                continue

            shutil.copy2(src_file, dest_file)
            changed_count += 1

        return {
            "success": True,
            "processed": total_items,
            "changed": changed_count,
            "skipped": skipped_count,
            "changed_count": changed_count,
            "skipped_count": skipped_count,
            "dry_run": dry_run,
        }

    def replace_borders(
        self,
        input_file: str,
        output_path: str,
        border_color: Tuple[int, int, int],
        border_width: int,
        folder: Optional[str],
    ) -> bool:
        """
        Removes existing border and applies a new one with specified color.
        
        CRITICAL: Image processing logic must match DAPS exactly:
        1. Crop image by border_width on all sides
        2. Add new border with border_color
        3. Resize to exactly (1000, 1500)
        4. Convert to RGB

        Args:
            input_file: Path to input image
            output_path: Base directory for output
            border_color: RGB color tuple for new border
            border_width: Width of border in pixels
            folder: Optional subfolder for organization

        Returns:
            True if file was saved/updated, False if unchanged
        """
        try:
            with Image.open(input_file) as image:
                width, height = image.size
                # PIL has read the file — evict input pages from page cache.
                _drop_file_cache(input_file)

                # Remove existing border by cropping
                cropped_image = image.crop(
                    (
                        border_width,
                        border_width,
                        width - border_width,
                        height - border_width,
                    )
                )

                # Add new border by expanding canvas
                new_width = cropped_image.width + 2 * border_width
                new_height = cropped_image.height + 2 * border_width
                final_image = Image.new("RGB", (new_width, new_height), border_color)
                final_image.paste(cropped_image, (border_width, border_width))

                # Construct output path
                file_name = os.path.basename(input_file)
                if folder:
                    final_path = os.path.join(output_path, folder, file_name)
                else:
                    final_path = os.path.join(output_path, file_name)

                # CRITICAL: Resize to DAPS standard dimensions and convert to RGB
                final_image = final_image.resize((1000, 1500)).convert("RGB")

                # Check if file exists and is different before saving
                if os.path.isfile(final_path):
                    # Use temp file to compare
                    with tempfile.NamedTemporaryFile(
                        suffix=os.path.splitext(file_name)[1], delete=False
                    ) as tmp:
                        tmp_path = tmp.name
                        final_image.save(tmp_path)

                    try:
                        if not filecmp.cmp(final_path, tmp_path):
                            _drop_file_cache(tmp_path)
                            _drop_file_cache(final_path)
                            final_image.save(final_path)
                            _drop_file_cache(final_path)
                            os.remove(tmp_path)
                            return True
                        else:
                            _drop_file_cache(tmp_path)
                            _drop_file_cache(final_path)
                            os.remove(tmp_path)
                            return False
                    except Exception:
                        _drop_file_cache(tmp_path)
                        os.remove(tmp_path)
                        raise
                else:
                    # Create directory if needed
                    os.makedirs(os.path.dirname(final_path), exist_ok=True)
                    final_image.save(final_path)
                    _drop_file_cache(final_path)
                    return True

        except UnidentifiedImageError as e:
            log_error(
                LogTags.BORDER_REPLACER,
                f"Unidentified image format: {input_file}",
                error=str(e),
            )
            return False
        except Exception as e:
            log_error(
                LogTags.BORDER_REPLACER, f"Error processing {input_file}: {str(e)}"
            )
            return False

    def remove_borders(
        self,
        input_file: str,
        output_path: str,
        border_width: int,
        exclude: bool,
        folder: Optional[str],
    ) -> bool:
        """
        Crops an image to remove its borders.
        
        CRITICAL: Image processing logic must match DAPS exactly:
        - If exclude=False: Remove top/left/right, add black bottom border
        - If exclude=True: Remove all borders
        - Resize to exactly (1000, 1500)
        - Convert to RGB

        Args:
            input_file: Path to input image
            output_path: Base directory for output
            border_width: Width of border to remove
            exclude: If True, remove all borders; if False, keep black bottom
            folder: Optional subfolder for organization

        Returns:
            True if file was saved/updated, False if unchanged
        """
        try:
            with Image.open(input_file) as image:
                width, height = image.size
                # PIL has read the file — evict input pages from page cache.
                _drop_file_cache(input_file)

                if not exclude:
                    # Remove top, left, right borders, add black bottom border
                    final_image = image.crop(
                        (border_width, border_width, width - border_width, height)
                    )
                    bottom_border = Image.new(
                        "RGB", (width - 2 * border_width, border_width), color="black"
                    )
                    bottom_border_position = (0, height - border_width - border_width)
                    final_image.paste(bottom_border, bottom_border_position)
                else:
                    # Remove all borders
                    final_image = image.crop(
                        (
                            border_width,
                            border_width,
                            width - border_width,
                            height - border_width,
                        )
                    )

                # CRITICAL: Resize to DAPS standard dimensions and convert to RGB
                final_image = final_image.resize((1000, 1500)).convert("RGB")

                # Construct output path
                file_name = os.path.basename(input_file)
                if folder:
                    final_path = os.path.join(output_path, folder, file_name)
                else:
                    final_path = os.path.join(output_path, file_name)

                # Check if file exists and is different before saving
                if os.path.isfile(final_path):
                    with tempfile.NamedTemporaryFile(
                        suffix=os.path.splitext(file_name)[1], delete=False
                    ) as tmp:
                        tmp_path = tmp.name
                        final_image.save(tmp_path)

                    try:
                        if not filecmp.cmp(final_path, tmp_path):
                            _drop_file_cache(tmp_path)
                            _drop_file_cache(final_path)
                            final_image.save(final_path)
                            _drop_file_cache(final_path)
                            os.remove(tmp_path)
                            return True
                        else:
                            _drop_file_cache(tmp_path)
                            _drop_file_cache(final_path)
                            os.remove(tmp_path)
                            return False
                    except Exception:
                        _drop_file_cache(tmp_path)
                        os.remove(tmp_path)
                        raise
                else:
                    os.makedirs(os.path.dirname(final_path), exist_ok=True)
                    final_image.save(final_path)
                    _drop_file_cache(final_path)
                    return True

        except UnidentifiedImageError as e:
            log_error(
                LogTags.BORDER_REPLACER,
                f"Unidentified image format: {input_file}",
                error=str(e),
            )
            return False
        except Exception as e:
            log_error(
                LogTags.BORDER_REPLACER, f"Error processing {input_file}: {str(e)}"
            )
            return False

    def process_posters(
        self,
        source_dir: str,
        destination_dir: str,
        border_colors: Optional[List[str]] = None,
        remove_borders: bool = False,
        border_width: int = 26,
        exclusion_list: Optional[List[str]] = None,
        dry_run: bool = False,
        progress_callback: Optional[ProgressCallback] = None,
        mode: str = "full",
        season_mode: str = "inherit",
        season_border_colors: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Process posters by applying or removing borders.

        Args:
            source_dir: Source directory containing posters (typically /posters/assets/tmp/)
            destination_dir: Final destination directory (/posters/assets/)
            border_colors: List of hex colors to cycle through for replacement mode
            remove_borders: If True, run explicit border-removal mode for main/movie posters
            border_width: Border width in pixels (default: 26)
            exclusion_list: List of titles to exclude from processing
            dry_run: If True, simulate without making changes
            progress_callback: Optional callback(phase, current, total, message)
            mode: "full" or "incremental" - full processes all, incremental only changed items
            season_mode: How to handle season posters — "inherit" (same as main), "remove"
                         (strip borders), or "colors" (use season_border_colors)
            season_border_colors: Hex colors used when season_mode is "colors"

        Returns:
            Dictionary with results including processed count and messages
        """
        try:
            # Log section start
            action_title = "Border Removal Starting" if remove_borders else "Border Replacement Starting"
            log_section_start(LogTags.BORDER_REPLACER, action_title)
            
            if not os.path.exists(source_dir):
                return {
                    "success": False,
                    "error": f"Source directory does not exist: {source_dir}",
                }

            if not os.path.exists(destination_dir):
                log_info(
                    LogTags.BORDER_REPLACER,
                    f"Creating destination directory: {destination_dir}",
                )
                if not dry_run:
                    os.makedirs(destination_dir, exist_ok=True)

            skip_non_holiday_setting = get_setting(self.db, "border_replacer_skip_non_holiday")
            skip_non_holiday = (
                skip_non_holiday_setting.value.lower() == "true"
                if skip_non_holiday_setting and skip_non_holiday_setting.value
                else False
            )

            is_holiday_active, active_holiday_name, effective_border_colors = self._resolve_effective_border_colors(border_colors)

            if remove_borders:
                effective_border_colors = []

            if is_holiday_active:
                log_info(
                    LogTags.BORDER_REPLACER,
                    f"Holiday schedule active: {active_holiday_name}",
                    holiday=active_holiday_name,
                    colors=len(effective_border_colors),
                )
            elif skip_non_holiday:
                log_info(
                    LogTags.BORDER_REPLACER,
                    "Skip non-holiday runs is enabled and no holiday schedule is active - copying files without border replacement",
                )

                if mode == "incremental":
                    self.detect_settings_change(
                        border_colors=None,
                        border_width=border_width,
                        exclusion_list=exclusion_list,
                        destination_dir=destination_dir,
                        dry_run=dry_run,
                        processing_profile="non_holiday_skip",
                    )

                copy_result = self._copy_images_without_border_changes(
                    source_dir=source_dir,
                    destination_dir=destination_dir,
                    dry_run=dry_run,
                    progress_callback=progress_callback,
                )

                if copy_result.get("success"):
                    log_success(
                        LogTags.BORDER_REPLACER,
                        f"Holiday skip copy complete: {copy_result.get('changed', 0)} changed, {copy_result.get('skipped', 0)} skipped",
                        changed=copy_result.get("changed", 0),
                        skipped=copy_result.get("skipped", 0),
                    )
                    log_section_end(LogTags.BORDER_REPLACER, "Border Processing Complete")
                else:
                    log_section_end(LogTags.BORDER_REPLACER, "Border Processing Failed")

                return copy_result

            if not remove_borders and len(effective_border_colors) == 0:
                # Check if season colors compensate — if seasons use "colors" mode with colors, and everything
                # else is expected to remove borders, that's still a valid config (remove_borders handles main).
                # But if remove_borders is also False, there's truly nothing configured for main posters.
                log_section_end(LogTags.BORDER_REPLACER, "Border Processing Failed")
                return {
                    "success": False,
                    "error": "No border colors configured. Add colors or enable 'Remove Borders' mode.",
                }

            # Resolve season border colors
            effective_season_mode = season_mode if season_mode in ("inherit", "remove", "colors") else "inherit"
            effective_season_colors: List[str] = []
            if effective_season_mode == "colors":
                effective_season_colors = list(season_border_colors or [])
                if not effective_season_colors:
                    # Fall back to inherit if colors mode is selected but no colors configured
                    effective_season_mode = "inherit"

            # Convert hex colors to RGB
            rgb_border_colors: List[Tuple[int, int, int]] = []
            if not remove_borders and effective_border_colors:
                for color in effective_border_colors:
                    rgb_color = self.convert_to_rgb(color)
                    rgb_border_colors.append(rgb_color)

            rgb_season_colors: List[Tuple[int, int, int]] = []
            if effective_season_mode == "colors" and effective_season_colors:
                for color in effective_season_colors:
                    rgb_season_colors.append(self.convert_to_rgb(color))

            action = "Removing borders" if remove_borders else "Replacing borders"
            mode_str = "(incremental)" if mode == "incremental" else "(full)"
            log_info(LogTags.BORDER_REPLACER, f"{action} {mode_str}")
            if effective_season_mode != "inherit":
                season_action = "remove" if effective_season_mode == "remove" else f"custom colors ({len(rgb_season_colors)})"
                log_info(LogTags.BORDER_REPLACER, f"Season poster override: {season_action}")

            # Import Poster model for incremental tracking
            from models.poster import Poster

            # Detect if settings have changed (only relevant for incremental mode)
            # During dry runs, only logs what would happen without modifying database
            # During real runs, resets tracking if colors/width/exclusions changed
            if mode == "incremental":
                if remove_borders:
                    profile = "remove_borders"
                else:
                    profile = f"holiday:{active_holiday_name}" if is_holiday_active and active_holiday_name else "default"
                self.detect_settings_change(
                    effective_border_colors,
                    border_width,
                    exclusion_list,
                    destination_dir,
                    dry_run,
                    processing_profile=profile,
                    season_mode=effective_season_mode,
                    season_border_colors=effective_season_colors,
                )

            incremental_tracking_records: Dict[str, Poster] = {}
            pending_tracking_updates = 0
            tracking_updates_enabled = not dry_run  # Both full and incremental maintain tracking

            def _flush_tracking_updates(force: bool = False) -> None:
                nonlocal pending_tracking_updates, tracking_updates_enabled
                if not tracking_updates_enabled:
                    return
                if pending_tracking_updates == 0:
                    return
                if not force and pending_tracking_updates < 250:
                    return

                try:
                    self.db.commit()
                    pending_tracking_updates = 0
                except Exception as db_error:
                    log_warning(
                        LogTags.BORDER_REPLACER,
                        f"Failed to flush incremental tracking updates: {db_error}",
                        error=str(db_error),
                    )
                    self.db.rollback()
                    pending_tracking_updates = 0
                    tracking_updates_enabled = False

            # For incremental mode, build a set of files that need processing
            # by querying database once instead of per-file
            files_needing_processing = None
            if mode == "incremental":
                log_info(LogTags.BORDER_REPLACER, "Building list of files needing processing...")
                files_needing_processing = set()
                
                # Walk source directory to get all source files with their mtimes
                source_files_with_mtime = {}
                for root, dirs, files in os.walk(source_dir):
                    rel_path = os.path.relpath(root, source_dir)
                    folder = None if rel_path == "." else rel_path
                    
                    for file in files:
                        if not file.lower().endswith((".jpg", ".jpeg", ".png")):
                            continue
                        
                        input_file = os.path.join(root, file)
                        dest_file = os.path.join(destination_dir, folder, file) if folder else os.path.join(destination_dir, file)
                        
                        try:
                            source_mtime = os.path.getmtime(input_file)
                            source_size = os.path.getsize(input_file)
                            source_files_with_mtime[dest_file] = (input_file, source_mtime, source_size, folder)
                        except OSError:
                            # If can't get mtime, add to processing list
                            files_needing_processing.add(dest_file)
                
                # Query database for all tracked files in one go
                dest_paths = list(source_files_with_mtime.keys())
                if dest_paths:
                    tracked_files = self.db.query(Poster).filter(
                        Poster.file_path.in_(dest_paths)
                    ).all()
                    
                    # Build dict of tracked files with their mtimes
                    tracked_metadata = {
                        p.file_path: (p.file_mtime, p.file_size, p.dest_file_mtime)
                        for p in tracked_files
                    }

                    def _mark_for_processing(
                        dest_file_path: str,
                        input_file_path: str,
                        reason: str,
                        title_folder: Optional[str],
                        source_mtime_value: Optional[float],
                        tracked_mtime_value: Optional[float],
                        source_size_value: Optional[int],
                        tracked_size_value: Optional[int],
                    ) -> None:
                        if title_folder:
                            display_title = title_folder
                        else:
                            display_title = os.path.splitext(os.path.basename(input_file_path))[0]

                        files_needing_processing.add(dest_file_path)
                        log_debug(
                            LogTags.BORDER_REPLACER,
                            f"Incremental reprocess queued: {display_title} ({reason})",
                            reason=reason,
                            source=input_file_path,
                            destination=dest_file_path,
                            source_mtime=source_mtime_value,
                            tracked_mtime=tracked_mtime_value,
                            source_size=source_size_value,
                            tracked_size=tracked_size_value,
                        )
                    
                    # Compare mtimes to determine which files need processing
                    for dest_file, (input_file, source_mtime, source_size, folder) in source_files_with_mtime.items():
                        tracked_entry = tracked_metadata.get(dest_file)

                        if tracked_entry is None:
                            _mark_for_processing(
                                dest_file,
                                input_file,
                                "missing_tracking",
                                folder,
                                source_mtime,
                                None,
                                source_size,
                                None,
                            )
                            continue

                        tracked_mtime, tracked_source_size, tracked_dest_mtime = tracked_entry

                        # Reprocess if output file disappeared.
                        if not os.path.exists(dest_file):
                            _mark_for_processing(
                                dest_file,
                                input_file,
                                "missing_destination",
                                folder,
                                source_mtime,
                                tracked_mtime,
                                source_size,
                                tracked_source_size,
                            )
                            continue

                        # Reprocess when source mtime changed materially.
                        if tracked_mtime is None or abs(float(source_mtime) - float(tracked_mtime)) > 0.0001:
                            _mark_for_processing(
                                dest_file,
                                input_file,
                                "mtime_changed",
                                folder,
                                source_mtime,
                                tracked_mtime,
                                source_size,
                                tracked_source_size,
                            )
                            continue

                        # Reprocess when source size changed even if mtime is preserved.
                        if tracked_source_size is not None and int(source_size) != int(tracked_source_size):
                            _mark_for_processing(
                                dest_file,
                                input_file,
                                "size_changed",
                                folder,
                                source_mtime,
                                tracked_mtime,
                                source_size,
                                tracked_source_size,
                            )
                            continue

                        # First-time population: dest_file_mtime not yet recorded for this
                        # file (e.g. existing records from before the column was added).
                        # Process once so the value gets written and future runs can detect
                        # external modifications.
                        if tracked_dest_mtime is None:
                            _mark_for_processing(
                                dest_file,
                                input_file,
                                "dest_mtime_not_recorded",
                                folder,
                                source_mtime,
                                tracked_mtime,
                                source_size,
                                tracked_source_size,
                            )
                            continue

                        # Reprocess if destination was externally modified (e.g. replaced by another app).
                        if tracked_dest_mtime is not None:
                            try:
                                current_dest_mtime = os.path.getmtime(dest_file)
                                if abs(float(current_dest_mtime) - float(tracked_dest_mtime)) > 0.0001:
                                    _mark_for_processing(
                                        dest_file,
                                        input_file,
                                        "dest_modified_externally",
                                        folder,
                                        source_mtime,
                                        tracked_mtime,
                                        source_size,
                                        tracked_source_size,
                                    )
                            except OSError:
                                pass
                
                # Clean up stale tracking records before (potentially) short-circuiting
                if tracking_updates_enabled and source_files_with_mtime:
                    current_dest_paths = set(source_files_with_mtime.keys())
                    try:
                        all_tracked = self.db.query(Poster).filter(
                            Poster.drive_id == "border_processed"
                        ).all()
                        stale = [r for r in all_tracked if r.file_path not in current_dest_paths]
                        if stale:
                            for record in stale:
                                self.db.delete(record)
                            self.db.commit()
                            log_info(
                                LogTags.BORDER_REPLACER,
                                f"Removed {len(stale)} stale tracking record(s) for deleted/moved files",
                                count=len(stale),
                            )
                    except Exception as stale_err:
                        log_warning(LogTags.BORDER_REPLACER, f"Failed to clean up stale tracking records: {stale_err}")
                        self.db.rollback()

                log_info(
                    LogTags.BORDER_REPLACER, 
                    f"Incremental mode: {len(files_needing_processing)} of {len(source_files_with_mtime)} files need processing",
                    needing_processing=len(files_needing_processing),
                    total=len(source_files_with_mtime)
                )
                
                # Short-circuit if no files need processing in incremental mode
                if len(files_needing_processing) == 0:
                    log_info(
                        LogTags.BORDER_REPLACER,
                        "No files need processing - all up to date",
                    )
                    log_success(
                        LogTags.BORDER_REPLACER,
                        f"Border processing complete: 0 changed, {len(source_files_with_mtime)} skipped",
                        changed=0,
                        skipped=len(source_files_with_mtime),
                        total=len(source_files_with_mtime),
                    )
                    log_section_end(LogTags.BORDER_REPLACER, "Border Processing Complete")
                    return {
                        "success": True,
                        "processed": 0,
                        "changed": 0,
                        "skipped": len(source_files_with_mtime),
                        "changed_count": 0,
                        "skipped_count": len(source_files_with_mtime),
                    }

                tracked_records = self.db.query(Poster).filter(
                    Poster.file_path.in_(list(files_needing_processing))
                ).all()
                incremental_tracking_records = {
                    record.file_path: record
                    for record in tracked_records
                }

            elif mode == "full" and tracking_updates_enabled:
                # Full mode: pre-load all existing tracking records so we can update
                # vs insert correctly, and clean up stale records in one pass.
                log_info(LogTags.BORDER_REPLACER, "Full mode: loading tracking records for update...")

                # Walk source dir to build the complete set of dest paths this run will produce
                full_mode_dest_paths: set[str] = set()
                for root, dirs, files in os.walk(source_dir):
                    rel_path = os.path.relpath(root, source_dir)
                    folder = None if rel_path == "." else rel_path
                    for file in files:
                        if not file.lower().endswith((".jpg", ".jpeg", ".png")):
                            continue
                        dest_file = os.path.join(destination_dir, folder, file) if folder else os.path.join(destination_dir, file)
                        full_mode_dest_paths.add(dest_file)

                # Load existing tracking records (filter in Python to avoid SQLite 999-var limit)
                if full_mode_dest_paths:
                    all_existing = self.db.query(Poster).filter(
                        Poster.drive_id == "border_processed"
                    ).all()
                    incremental_tracking_records = {
                        record.file_path: record
                        for record in all_existing
                        if record.file_path in full_mode_dest_paths
                    }

                # Remove stale records (dest paths no longer produced by source tree)
                # Filter in Python to avoid SQLite's 999-variable IN clause limit
                try:
                    all_tracked = self.db.query(Poster).filter(
                        Poster.drive_id == "border_processed"
                    ).all()
                    stale = [r for r in all_tracked if r.file_path not in full_mode_dest_paths]
                    if stale:
                        for record in stale:
                            self.db.delete(record)
                        self.db.commit()
                        log_info(
                            LogTags.BORDER_REPLACER,
                            f"Removed {len(stale)} stale tracking record(s) for deleted/moved files",
                            count=len(stale),
                        )
                except Exception as stale_err:
                    log_warning(LogTags.BORDER_REPLACER, f"Failed to clean up stale tracking records: {stale_err}")
                    self.db.rollback()

            # Scan source directory for folders and posters
            processed_count = 0
            changed_count = 0
            skipped_count = 0
            total_items = 0

            # Count total items for progress
            # If incremental mode already walked the tree, use that count
            if mode == "incremental" and files_needing_processing is not None:
                total_items = len(files_needing_processing)
            else:
                # Otherwise, count by walking
                for root, dirs, files in os.walk(source_dir):
                    for file in files:
                        if file.lower().endswith((".jpg", ".jpeg", ".png")):
                            total_items += 1

            if total_items == 0:
                return {
                    "success": False,
                    "error": "No poster files found in source directory",
                }

            log_info(
                LogTags.BORDER_REPLACER,
                f"Found {total_items} poster files to process",
                count=total_items,
            )

            current_color_index = 0
            current_season_color_index = 0

            # Process each poster file
            for root, dirs, files in os.walk(source_dir):
                # Get relative path from source_dir for folder structure
                rel_path = os.path.relpath(root, source_dir)
                folder = None if rel_path == "." else rel_path

                for file in files:
                    if not file.lower().endswith((".jpg", ".jpeg", ".png")):
                        continue

                    input_file = os.path.join(root, file)

                    # Determine whether this is a season poster
                    is_season = self._is_season_file(file)

                    # Check exclusion list
                    excluded = False
                    if exclusion_list and folder:
                        # Check if folder name matches any exclusion
                        for exclusion in exclusion_list:
                            if exclusion.lower() in folder.lower():
                                excluded = True
                                log_debug(
                                    LogTags.BORDER_REPLACER,
                                    f"Excluding {folder}/{file}",
                                    file=file,
                                )
                                break

                    # Incremental mode: Skip files not in processing list (applies to both dry run and real run)
                    if mode == "incremental" and files_needing_processing is not None:
                        dest_file = os.path.join(destination_dir, folder, file) if folder else os.path.join(destination_dir, file)

                        if dest_file not in files_needing_processing:
                            continue

                    processed_count += 1

                    progress_item = f"{folder}/{file}" if folder else file
                    if progress_callback:
                        progress_callback(
                            "processing",
                            processed_count,
                            total_items,
                            f"Processing: {progress_item}",
                        )

                    if not dry_run:
                        dest_file = os.path.join(destination_dir, folder, file) if folder else os.path.join(destination_dir, file)

                        # Determine the action for this specific file based on whether it's a season poster
                        if is_season and effective_season_mode == "remove":
                            # Season-specific: remove borders
                            result = self.remove_borders(
                                input_file,
                                destination_dir,
                                border_width,
                                excluded,
                                folder,
                            )
                        elif is_season and effective_season_mode == "colors" and rgb_season_colors:
                            # Season-specific: apply season colors
                            rgb_season_color = rgb_season_colors[current_season_color_index]
                            result = self.replace_borders(
                                input_file,
                                destination_dir,
                                rgb_season_color,
                                border_width,
                                folder,
                            )
                            current_season_color_index = (current_season_color_index + 1) % len(rgb_season_colors)
                        elif not remove_borders and rgb_border_colors:
                            # Main behavior: replace border with color
                            rgb_border_color = rgb_border_colors[current_color_index]
                            result = self.replace_borders(
                                input_file,
                                destination_dir,
                                rgb_border_color,
                                border_width,
                                folder,
                            )
                            current_color_index = (current_color_index + 1) % len(rgb_border_colors)
                        else:
                            # Fallback: remove borders (main remove_borders=True, or no colors configured)
                            result = self.remove_borders(
                                input_file,
                                destination_dir,
                                border_width,
                                excluded,
                                folder,
                            )

                        if mode == "incremental" and tracking_updates_enabled:
                            # Update tracking for both changed and unchanged files so
                            # incremental mode does not repeatedly re-check identical items.
                            try:
                                current_mtime = os.path.getmtime(input_file)
                                current_size = os.path.getsize(input_file)
                                current_time = datetime.now(timezone.utc)

                                # Record the destination file's mtime so we can detect
                                # external modifications on subsequent runs.
                                try:
                                    written_dest_mtime = os.path.getmtime(dest_file)
                                except OSError:
                                    written_dest_mtime = None

                                poster_record = incremental_tracking_records.get(dest_file)

                                if poster_record:
                                    poster_record.file_mtime = current_mtime
                                    poster_record.last_processed = current_time
                                    poster_record.file_size = current_size
                                    poster_record.dest_file_mtime = written_dest_mtime
                                else:
                                    poster_record = Poster(
                                        drive_id="border_processed",
                                        file_name=file,
                                        file_path=dest_file,
                                        file_size=current_size,
                                        file_mtime=current_mtime,
                                        last_processed=current_time,
                                        dest_file_mtime=written_dest_mtime,
                                    )
                                    self.db.add(poster_record)
                                    incremental_tracking_records[dest_file] = poster_record

                                pending_tracking_updates += 1
                                _flush_tracking_updates()
                            except Exception as db_error:
                                log_warning(
                                    LogTags.BORDER_REPLACER,
                                    f"Failed to update database tracking for {file}: {db_error}",
                                    file=file,
                                    error=str(db_error),
                                )
                                self.db.rollback()
                                tracking_updates_enabled = False

                        if result:
                            changed_count += 1
                            
                            # Log the actual change
                            title_info = f" - {folder}" if folder else ""
                            log_debug(
                                LogTags.BORDER_REPLACER,
                                f"Applied changes: {file}{title_info}",
                                file=file,
                                folder=folder or "root"
                            )
                            
                        else:
                            skipped_count += 1
                            # Log when file was checked but no changes needed (already identical)
                            title_info = f" - {folder}" if folder else ""
                            log_debug(
                                LogTags.BORDER_REPLACER,
                                f"No change needed: {file}{title_info}",
                                file=file,
                                folder=folder or "root"
                            )
                    else:
                        # Dry run - just count
                        changed_count += 1

            _flush_tracking_updates(force=True)

            log_success(
                LogTags.BORDER_REPLACER,
                f"Border processing complete: {changed_count} changed, {skipped_count} skipped",
                changed=changed_count,
                skipped=skipped_count,
                total=processed_count,
            )
            
            log_section_end(LogTags.BORDER_REPLACER, "Border Processing Complete")

            return {
                "success": True,
                "processed": processed_count,
                "changed": changed_count,
                "skipped": skipped_count,
                "dry_run": dry_run,
            }

        except Exception as e:
            log_error(
                LogTags.BORDER_REPLACER, f"Border processing failed: {str(e)}", error=str(e)
            )
            log_section_end(LogTags.BORDER_REPLACER, "Border Processing Failed")
            return {"success": False, "error": str(e)}

    def get_border_settings(self) -> Dict[str, Any]:
        """
        Get border replacer settings from database.

        Returns:
            Dictionary with border settings
        """
        settings = {
            "enabled": False,
            "colors": [],
            "width": 75,
            "exclusions": [],
            "holiday_schedules": [],
            "skip_non_holiday": False,
            "remove_borders": False,
        }

        # Get enabled setting
        enabled_setting = get_setting(self.db, "border_replacer_enabled")
        if enabled_setting:
            settings["enabled"] = enabled_setting.value.lower() == "true"

        # Get colors
        colors_setting = get_setting(self.db, "border_replacer_colors")
        if colors_setting:
            try:
                settings["colors"] = json.loads(colors_setting.value)
            except json.JSONDecodeError:
                log_warning(
                    LogTags.BORDER_REPLACER, "Invalid border colors JSON, using empty list"
                )

        # Get width
        width_setting = get_setting(self.db, "border_replacer_width")
        if width_setting:
            try:
                settings["width"] = int(width_setting.value)
            except ValueError:
                log_warning(
                    LogTags.BORDER_REPLACER, "Invalid border width, using default 26"
                )

        # Get exclusions
        exclusions_setting = get_setting(self.db, "border_replacer_exclusions")
        if exclusions_setting:
            try:
                settings["exclusions"] = json.loads(exclusions_setting.value)
            except json.JSONDecodeError:
                log_warning(
                    LogTags.BORDER_REPLACER,
                    "Invalid border exclusions JSON, using empty list",
                )

        skip_setting = get_setting(self.db, "border_replacer_skip_non_holiday")
        if skip_setting and skip_setting.value:
            settings["skip_non_holiday"] = skip_setting.value.lower() == "true"

        remove_setting = get_setting(self.db, "border_replacer_remove_borders")
        if remove_setting and remove_setting.value:
            settings["remove_borders"] = remove_setting.value.lower() == "true"

        settings["holiday_schedules"] = self._load_holiday_schedules()

        return settings
