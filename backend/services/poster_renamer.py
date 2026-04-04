import filecmp
import json
import os
import re
import shutil
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from pathvalidate import is_valid_filename, sanitize_filename

from core.logging import logger, LogTags, log_success, log_error, log_info, log_debug, log_section_start, log_section_end
from models.setting import get_setting
from sqlalchemy.orm import Session
from util.arr.client import create_arr_client
from util.constants import illegal_chars_regex
from util.data.construct import generate_title_variants
from util.data.normalization import normalize_titles
from util.posters.assets import get_assets_files
from util.posters.index import build_search_index, create_new_empty_index
from util.posters.match import match_assets_to_media

MediaItem = Dict[str, Any]
MediaDict = Dict[str, List[MediaItem]]
RenameProgressCallback = Callable[[int, int, str], None]
WorkflowProgressCallback = Callable[[str, int, int, str], None]


class PosterRenameService:
    """Service for renaming posters to Plex-compatible format."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.logger = logger

    def _asset_matches_target(
        self,
        asset: Dict[str, Any],
        *,
        target_media_type: Optional[str],
        target_title_keys: set[str],
        target_tmdb_id: Optional[int],
        target_tvdb_id: Optional[int],
        target_imdb_id: Optional[str],
        target_season_number: Optional[int],
    ) -> bool:
        asset_type = str(asset.get("type", "")).strip().lower()

        if target_media_type == "movie" and asset_type != "movies":
            return False
        if target_media_type == "series" and asset_type != "series":
            return False

        asset_tmdb_raw = asset.get("tmdb_id")
        asset_tvdb_raw = asset.get("tvdb_id")
        asset_imdb_raw = asset.get("imdb_id")
        try:
            asset_tmdb: Optional[int] = int(asset_tmdb_raw) if asset_tmdb_raw is not None else None
        except (ValueError, TypeError):
            asset_tmdb = None
        try:
            asset_tvdb: Optional[int] = int(asset_tvdb_raw) if asset_tvdb_raw is not None else None
        except (ValueError, TypeError):
            asset_tvdb = None
        asset_imdb = str(asset_imdb_raw).strip().lower() if asset_imdb_raw else None

        if target_tmdb_id is not None and asset_tmdb == target_tmdb_id:
            if target_media_type != "series":
                return True
        if target_tvdb_id is not None and asset_tvdb == target_tvdb_id:
            return True
        if target_imdb_id and asset_imdb and asset_imdb == target_imdb_id:
            return True

        normalized_title = str(asset.get("normalized_title") or "").strip().lower()
        if not normalized_title:
            normalized_title = normalize_titles(str(asset.get("title") or ""))

        if target_title_keys and normalized_title not in target_title_keys:
            normalized_alternates = asset.get("normalized_alternate_titles")
            if isinstance(normalized_alternates, list):
                alternate_keys = {
                    str(value).strip().lower()
                    for value in normalized_alternates
                    if isinstance(value, str) and value.strip()
                }
                if not (alternate_keys & target_title_keys):
                    return False
            else:
                return False

        if target_media_type == "series" and target_season_number is not None:
            season_numbers = asset.get("season_numbers")
            if isinstance(season_numbers, list) and season_numbers:
                if target_season_number not in {int(number) for number in season_numbers if isinstance(number, int)}:
                    return False

        return True

    def _filter_assets_for_target(
        self,
        assets_dict: List[Dict[str, Any]],
        *,
        target_media_type: Optional[str],
        target_title: Optional[str],
        target_year: Optional[int],
        target_tmdb_id: Optional[int],
        target_tvdb_id: Optional[int],
        target_imdb_id: Optional[str],
        target_season_number: Optional[int],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        title_keys: set[str] = set()
        if target_title:
            title_keys.add(normalize_titles(target_title))
            if target_year is not None:
                title_keys.add(normalize_titles(f"{target_title} ({target_year})"))

        normalized_imdb = target_imdb_id.strip().lower() if isinstance(target_imdb_id, str) and target_imdb_id.strip() else None

        filtered_assets = [
            asset
            for asset in assets_dict
            if self._asset_matches_target(
                asset,
                target_media_type=target_media_type,
                target_title_keys=title_keys,
                target_tmdb_id=target_tmdb_id,
                target_tvdb_id=target_tvdb_id,
                target_imdb_id=normalized_imdb,
                target_season_number=target_season_number,
            )
        ]

        filtered_index = create_new_empty_index()
        for asset in filtered_assets:
            build_search_index(filtered_index, str(asset.get("title", "")), asset)

        return filtered_assets, filtered_index

    def _fetch_plex_collections(
        self, 
        url: str, 
        token: str, 
        media_dict: MediaDict,
        instance_name: str = "Plex",
        log_tag: str = LogTags.POSTER_RENAMER,
        selected_libraries: Optional[List[str]] = None
    ) -> None:
        """
        Fetch collections from a Plex server.
        
        Args:
            url: Plex server URL
            token: Plex authentication token
            media_dict: Dictionary to append collections to
            instance_name: Name of this Plex instance
            log_tag: Tag to use for logging (LogTags constant)
            selected_libraries: List of library keys to include (format: "instance_name:library_key")
        """
        try:
            from plexapi.server import PlexServer
            from util.data.normalization import normalize_titles
            import html
            from unidecode import unidecode
            
            log_info(log_tag, f"Connecting to {instance_name} at {url} to fetch collections...")
            plex = PlexServer(url, token)
            
            # Get all library sections
            for section in plex.library.sections():
                if section.type in ['movie', 'show']:
                    # Check if this library is selected
                    library_key = f"{instance_name}:{section.key}"
                    if selected_libraries is not None and library_key not in selected_libraries:
                        log_debug(log_tag, f"Skipping library '{section.title}' (not selected)")
                        continue
                    
                    log_info(log_tag, f"Fetching collections from {instance_name} library: {section.title}")
                    collections = section.search(libtype='collection')
                    
                    for collection in collections:
                        if getattr(collection, "smart", False):
                            log_debug(log_tag, f"Including smart collection: {collection.title}")
                        
                        title = unidecode(html.unescape(collection.title))
                        normalized_title = normalize_titles(title)
                        
                        # Generate alternate title variants (like DAPS does)
                        title_variants = generate_title_variants(title)
                        
                        # Pre-sanitize folder name to remove illegal chars (matches DAPS behavior)
                        folder = illegal_chars_regex.sub("", title)
                        
                        media_dict["collections"].append({
                            "type": "collections",
                            "title": title,
                            "year": None,
                            "folder": folder,  # Use pre-sanitized folder name
                            "normalized_title": normalized_title,
                            "alternate_titles": title_variants["alternate_titles"],
                            "normalized_alternate_titles": title_variants["normalized_alternate_titles"],
                            "instance": f"{instance_name} ({section.title})",  # Track which library this is from
                            "library_type": section.type,  # Store the actual library type ('movie' or 'show')
                        })
                    
                    log_info(log_tag, f"Found {len(collections)} collections in '{section.title}' ({instance_name})")
                    
        except ImportError:
            log_error(log_tag, "plexapi not installed. Install with: pip install plexapi")
        except Exception as e:
            log_error(log_tag, f"Error connecting to {instance_name}: {e}")

    def _merge_duplicate_series(self, series_list: List[MediaItem], log_tag: str = LogTags.POSTER_RENAMER) -> List[MediaItem]:
        """
        Merge duplicate series entries from multiple Sonarr instances.
        Combines season lists to include all seasons across all instances (DAPS behavior).
        
        Args:
            series_list: List of series from all Sonarr instances
            log_tag: Tag to use for logging (LogTags constant)
            
        Returns:
            Deduplicated list with merged season data
        """
        if not series_list:
            return []
        
        # Group series by unique identifier (tvdb_id preferred, fallback to title+year)
        series_map: Dict[str, Dict[str, Any]] = {}
        
        for series in series_list:
            # Create unique key - prefer TVDB ID, fallback to title+year
            tvdb_id = series.get("tvdb_id")
            if tvdb_id:
                key = f"tvdb_{tvdb_id}"
            else:
                title = series.get("title", "").lower()
                year = series.get("year", "")
                key = f"{title}_{year}"
            
            if key in series_map:
                # Merge with existing entry
                existing = series_map[key]
                
                # Merge season lists (union of all seasons)
                existing_seasons = existing.get("seasons", [])
                new_seasons = series.get("seasons", [])
                
                # Create dict to deduplicate by season_number
                season_dict = {s["season_number"]: s for s in existing_seasons}
                for season in new_seasons:
                    season_num = season["season_number"]
                    if season_num not in season_dict:
                        season_dict[season_num] = season
                
                existing["seasons"] = sorted(season_dict.values(), key=lambda s: s["season_number"])
                
                # Combine instance names
                existing_instance = existing.get("instance", "")
                new_instance = series.get("instance", "")
                if new_instance and new_instance not in existing_instance:
                    existing["instance"] = f"{existing_instance} & {new_instance}"
                
                log_debug(
                    log_tag,
                    f"Merged '{series.get('title')}' from {new_instance} "
                    f"with {len(new_seasons)} seasons. Total unique seasons: {len(existing['seasons'])}"
                )
            else:
                # First time seeing this series
                series_map[key] = series
        
        merged_list = list(series_map.values())
        
        if len(merged_list) < len(series_list):
            log_info(
                log_tag,
                f"Deduplicated series: {len(series_list)} entries → {len(merged_list)} unique series "
                f"(merged {len(series_list) - len(merged_list)} duplicates from multiple Sonarr instances)"
            )
        
        return merged_list

    def _merge_duplicate_movies(self, movies_list: List[MediaItem], log_tag: str = LogTags.POSTER_RENAMER) -> List[MediaItem]:
        """
        Merge duplicate movie entries from multiple Radarr instances.
        
        Args:
            movies_list: List of movies from all Radarr instances
            log_tag: Tag to use for logging (LogTags constant)
            
        Returns:
            Deduplicated list
        """
        if not movies_list:
            return []
        
        # Group movies by unique identifier (tmdb_id preferred, fallback to title+year)
        movies_map: Dict[str, Dict[str, Any]] = {}
        
        for movie in movies_list:
            # Create unique key - prefer TMDB ID, fallback to title+year
            tmdb_id = movie.get("tmdb_id")
            if tmdb_id:
                key = f"tmdb_{tmdb_id}"
            else:
                title = movie.get("title", "").lower()
                year = movie.get("year", "")
                key = f"{title}_{year}"
            
            if key in movies_map:
                # Already exists - combine instance names
                existing = movies_map[key]
                existing_instance = existing.get("instance", "")
                new_instance = movie.get("instance", "")
                if new_instance and new_instance not in existing_instance:
                    existing["instance"] = f"{existing_instance} & {new_instance}"
                
                log_debug(
                    log_tag,
                    f"Merged '{movie.get('title')}' from {new_instance}"
                )
            else:
                # First time seeing this movie
                movies_map[key] = movie
        
        merged_list = list(movies_map.values())
        
        if len(merged_list) < len(movies_list):
            log_info(
                log_tag,
                f"Deduplicated movies: {len(movies_list)} entries → {len(merged_list)} unique movies "
                f"(merged {len(movies_list) - len(merged_list)} duplicates from multiple Radarr instances)"
            )
        
        return merged_list

    def process_file(
        self, file: str, new_file_path: str, action_type: str
    ) -> None:
        """
        Perform a file operation (copy, move, hardlink, or symlink) between paths.
        
        Args:
            file: Original file path.
            new_file_path: Destination file path.
            action_type: Operation type: 'copy', 'move', 'hardlink', or 'symlink'.
        """
        try:
            if action_type == "copy":
                shutil.copy(file, new_file_path)
            elif action_type == "move":
                shutil.move(file, new_file_path)
            elif action_type == "hardlink":
                os.link(file, new_file_path)
            elif action_type == "symlink":
                os.symlink(file, new_file_path)
        except OSError as e:
            log_error(LogTags.POSTER_RENAMER, f"Error {action_type}ing file: {e}")

    def rename_files(
        self,
        matched_assets: MediaDict,
        destination_dir: str,
        action_type: str = "copy",
        asset_folders: bool = True,
        dry_run: bool = False,
        progress_callback: Optional[RenameProgressCallback] = None,
    ) -> Tuple[MediaDict, List[str], List[str]]:
        """
        Rename matched assets to Plex-compatible filenames and handle folder structure.
        
        Args:
            matched_assets: Dictionary of matched poster assets.
            destination_dir: Destination directory for renamed posters.
            action_type: File operation type ('copy', 'move', 'hardlink', 'symlink').
            asset_folders: Whether to create asset folders.
            dry_run: If True, simulate without making changes.
            progress_callback: Optional callback function(current, total, item_name) for progress updates.
            
        Returns:
            Tuple of (output dict, destination files copied/updated, source files processed).
        """
        output: MediaDict = {}
        renamed_files = []  # Destination files that were copied/updated
        processed_source_files = []  # Source files that were checked (copied or skipped if identical)
        
        asset_types: List[str] = ["collections", "movies", "series"]
        log_info(LogTags.POSTER_RENAMER, "Renaming assets, please wait...")
        
        # Calculate total items to process
        total_items = sum(len(matched_assets[asset_type]) for asset_type in asset_types)
        current_item = 0
        
        for asset_type in asset_types:
            output[asset_type] = []
            if matched_assets[asset_type]:
                for item in matched_assets[asset_type]:
                    current_item += 1

                    item_name = f"{item.get('title', 'Unknown')} ({item.get('year', 'N/A')})"
                    item_had_changes = False
                    
                    messages: List[str] = []
                    files = item["files"]
                    folder = item["folder"]
                    
                    # Extract only the folder name (not full path) from media library path
                    if folder:
                        folder = os.path.basename(folder.rstrip("/"))
                    
                    # Sanitize folder name for collections
                    if asset_type == "collections":
                        if not is_valid_filename(folder):
                            folder = sanitize_filename(folder)
                    
                    # Construct destination folder
                    if asset_folders:
                        dest_dir = os.path.join(destination_dir, folder)
                        if not os.path.exists(dest_dir):
                            if not dry_run:
                                os.makedirs(dest_dir)
                    else:
                        dest_dir = destination_dir
                    
                    # Rename each asset file
                    for file in files:
                        file_name = os.path.basename(file)
                        file_extension = os.path.splitext(file)[1]
                        
                        # Handle season posters
                        if re.search(r" - Season| - Specials", file_name):
                            try:
                                season_number = (
                                    re.search(r"Season (\d+)", file_name).group(1)
                                    if "Season" in file_name
                                    else "00"
                                ).zfill(2)
                            except AttributeError:
                                log_debug(
                                    LogTags.POSTER_RENAMER,
                                    f"Error extracting season number from {file_name}"
                                )
                                continue
                            
                            if asset_folders:
                                new_file_name = f"Season{season_number}{file_extension}"
                            else:
                                new_file_name = (
                                    f"{folder}_Season{season_number}{file_extension}"
                                )
                            new_file_path = os.path.join(dest_dir, new_file_name)
                        else:
                            # Handle main posters
                            if asset_folders:
                                new_file_name = f"poster{file_extension}"
                            else:
                                new_file_name = f"{folder}{file_extension}"
                            new_file_path = os.path.join(dest_dir, new_file_name)
                        
                        # Check if destination exists and is different
                        if os.path.lexists(new_file_path):
                            existing_file = os.path.join(dest_dir, new_file_name)
                            try:
                                if not filecmp.cmp(file, existing_file):
                                    if file_name != new_file_name:
                                        messages.append(
                                            f"{file_name} -renamed-> {new_file_name}"
                                        )
                                    if not dry_run:
                                        if action_type in ["hardlink", "symlink"]:
                                            os.remove(new_file_path)
                                        self.process_file(file, new_file_path, action_type)
                                        renamed_files.append(new_file_path)
                                        processed_source_files.append(file)
                                        item_had_changes = True
                                        # Log the file as it's being renamed
                                        log_info(
                                            LogTags.POSTER_RENAMER,
                                            f"  → {file_name} → {new_file_name}",
                                            source=file_name, dest=new_file_name
                                        )
                                else:
                                    # Destination exists and is identical - considered processed
                                    processed_source_files.append(file)
                            except FileNotFoundError:
                                if not dry_run:
                                    os.remove(new_file_path)
                                    self.process_file(file, new_file_path, action_type)
                                    renamed_files.append(new_file_path)
                                    processed_source_files.append(file)
                                    item_had_changes = True
                                    # Log the file as it's being renamed
                                    log_info(
                                        LogTags.POSTER_RENAMER,
                                        f"  → {file_name} → {new_file_name}",
                                        source=file_name, dest=new_file_name
                                    )
                        else:
                            # Destination doesn't exist - need to copy
                            if file_name != new_file_name:
                                messages.append(
                                    f"{file_name} -renamed-> {new_file_name}"
                                )
                            if not dry_run:
                                self.process_file(file, new_file_path, action_type)
                                renamed_files.append(new_file_path)
                                processed_source_files.append(file)
                                item_had_changes = True
                                # Log the file as it's being renamed
                                log_info(
                                    LogTags.POSTER_RENAMER,
                                    f"  → {file_name} → {new_file_name}",
                                    source=file_name, dest=new_file_name
                                )

                    if progress_callback:
                        if item_had_changes:
                            progress_callback(current_item, total_items, f"Renaming: {item_name}")
                        else:
                            progress_callback(current_item, total_items, "Evaluating matches...")
                    
                    if messages:
                        output[asset_type].append(
                            {
                                "title": item["title"],
                                "year": item["year"],
                                "folder": item["folder"],
                                "messages": messages,
                            }
                        )
            else:
                log_debug(LogTags.POSTER_RENAMER, f"No {asset_type} to rename")
        
        return output, renamed_files, processed_source_files

    def get_media_from_instances(self, log_tag: str = LogTags.POSTER_RENAMER, setting_key: str = "poster_renamer_libraries") -> MediaDict:
        """
        Get media from configured Plex/Radarr/Sonarr instances.
        
        Args:
            log_tag: Tag to use for logging (LogTags constant, default: LogTags.POSTER_RENAMER)
            setting_key: Settings key for library filter (default: "poster_renamer_libraries")
        
        Returns:
            Dictionary of media by type (movies, series, collections).
        """
        media_dict: MediaDict = {
            "movies": [],
            "series": [],
            "collections": [],
        }

        # Load selected libraries setting
        selected_libraries_setting = get_setting(self.db, setting_key)
        selected_libraries = None
        if selected_libraries_setting and selected_libraries_setting.value:
            try:
                selected_libraries = json.loads(selected_libraries_setting.value)
                if selected_libraries:
                    log_info(log_tag, f"Using library filter: {len(selected_libraries)} libraries selected ({setting_key})")
            except Exception as e:
                log_error(log_tag, f"Error parsing {setting_key}: {e}")

        # Get Plex collections (supports both formats for backward compatibility)
        plex_instances = get_setting(self.db, "plex_instances")
        
        if plex_instances and plex_instances.value:
            # New format: instances array (supports multiple Plex servers)
            try:
                instances = json.loads(plex_instances.value)
                for instance in instances:
                    self._fetch_plex_collections(
                        instance["url"], 
                        instance["api_key"], 
                        media_dict, 
                        instance.get("name", "Plex"),
                        log_tag,
                        selected_libraries
                    )
            except Exception as e:
                log_error(LogTags.POSTER_RENAMER, f"Error parsing plex_instances: {e}")
        else:
            # Old format: separate url/token fields (single Plex server)
            plex_url_setting = get_setting(self.db, "plex_url")
            plex_token_setting = get_setting(self.db, "plex_token")
            
            if plex_url_setting and plex_token_setting and plex_url_setting.value and plex_token_setting.value:
                self._fetch_plex_collections(
                    plex_url_setting.value, 
                    plex_token_setting.value, 
                    media_dict,
                    "Plex",
                    log_tag,
                    selected_libraries
                )
        
        # Get Radarr instances
        radarr_instances = get_setting(self.db, "radarr_instances")
        if radarr_instances and radarr_instances.value:
            instances = json.loads(radarr_instances.value)
            for instance in instances:
                try:
                    client = create_arr_client(
                        instance["url"],
                        instance["api_key"],
                        "radarr",
                        self.logger
                    )
                    if client and client.connect_status:
                        # Use include_unmonitored=True to match DAPS behavior
                        results = client.get_parsed_media(include_unmonitored=True)
                        if results:
                            # Add instance info to each media item
                            for item in results:
                                item["instance"] = instance.get("name", "Radarr")
                            media_dict["movies"].extend(results)
                        else:
                            log_error(log_tag, f"No Radarr data found from {instance['url']}")
                except Exception as e:
                    log_error(log_tag, f"Error getting Radarr data: {e}")

        # Get Sonarr instances
        sonarr_instances = get_setting(self.db, "sonarr_instances")
        if sonarr_instances and sonarr_instances.value:
            instances = json.loads(sonarr_instances.value)
            for instance in instances:
                try:
                    client = create_arr_client(
                        instance["url"],
                        instance["api_key"],
                        "sonarr",
                        self.logger
                    )
                    if client and client.connect_status:
                        # Use include_unmonitored=True to match DAPS behavior
                        # This includes all seasons regardless of monitored status
                        results = client.get_parsed_media(include_unmonitored=True)
                        if results:
                            # Add instance info to each media item
                            for item in results:
                                item["instance"] = instance.get("name", "Sonarr")
                            media_dict["series"].extend(results)
                        else:
                            log_error(log_tag, f"No Sonarr data found from {instance['url']}")
                except Exception as e:
                    log_error(log_tag, f"Error getting Sonarr data: {e}")

        # Deduplicate and merge media from multiple instances
        # This matches DAPS behavior: combine season lists from multiple Sonarr instances
        media_dict["movies"] = self._merge_duplicate_movies(media_dict["movies"], log_tag)
        media_dict["series"] = self._merge_duplicate_series(media_dict["series"], log_tag)
        
        return media_dict

    def rename_posters(
        self,
        source_dirs: List[str],
        destination_dir: str,
        action_type: str = "copy",
        asset_folders: bool = True,
        dry_run: bool = False,
        use_temp_folder: bool = False,
        progress_callback: Optional[WorkflowProgressCallback] = None,
        target_media_type: Optional[str] = None,
        target_title: Optional[str] = None,
        target_year: Optional[int] = None,
        target_tmdb_id: Optional[int] = None,
        target_tvdb_id: Optional[int] = None,
        target_imdb_id: Optional[str] = None,
        target_season_number: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Main method to rename posters.
        
        Args:
            source_dirs: List of source directories containing posters.
            destination_dir: Destination directory for renamed posters.
            action_type: File operation type.
            asset_folders: Whether to use asset folders.
            dry_run: If True, simulate without making changes.
            use_temp_folder: If True, use 'tmp' subdirectory for workflow integration with border_replacer.
            progress_callback: Optional callback function(phase, current, total, message) for progress updates.
            
        Returns:
            Dictionary with results including output messages and stats.
        """
        try:
            # Log section start
            log_section_start(LogTags.POSTER_RENAMER, "Poster Renamer Starting")
            
            # If using temp folder (for border_replacer workflow), create tmp subdirectory
            actual_destination = destination_dir
            if use_temp_folder:
                actual_destination = os.path.join(destination_dir, "tmp")
                log_info(LogTags.POSTER_RENAMER, f"Using temp folder for border replacer workflow: {actual_destination}")
            
            # Create destination directory if it doesn't exist
            if not os.path.exists(actual_destination):
                log_info(LogTags.POSTER_RENAMER, f"Creating destination directory: {actual_destination}")
                if not dry_run:
                    os.makedirs(actual_destination)
            
            if dry_run:
                log_info(LogTags.POSTER_RENAMER, "DRY RUN - NO CHANGES WILL BE MADE")
            
            # Phase 1: Gather poster assets (0-20%)
            if progress_callback:
                progress_callback("gathering", 0, 100, "Gathering poster files...")
            
            log_info(LogTags.POSTER_RENAMER, "Gathering all the posters, please wait...")
            assets_dict, prefix_index = get_assets_files(source_dirs, self.logger)
            
            if not assets_dict:
                return {
                    "success": False,
                    "error": "No assets found in the source directories"
                }

            if target_media_type or target_title or target_tmdb_id or target_tvdb_id or target_imdb_id:
                assets_dict, prefix_index = self._filter_assets_for_target(
                    assets_dict,
                    target_media_type=target_media_type,
                    target_title=target_title,
                    target_year=target_year,
                    target_tmdb_id=target_tmdb_id,
                    target_tvdb_id=target_tvdb_id,
                    target_imdb_id=target_imdb_id,
                    target_season_number=target_season_number,
                )

                if not assets_dict:
                    return {
                        "success": False,
                        "error": "No source assets matched webhook target",
                    }

                log_info(
                    LogTags.POSTER_RENAMER,
                    f"Webhook-targeted rename: narrowed scope to {len(assets_dict)} asset group(s)",
                    target_media_type=target_media_type,
                    target_title=target_title,
                    target_year=target_year,
                    target_season_number=target_season_number,
                )
                
            # Phase 1.5: Prepare for processing (track all source files, let rename_files decide what to do)
            if progress_callback:
                progress_callback("preparing", 15, 100, "Preparing files for evaluation...")
            
            # Collect all source file paths - we'll check each one during rename
            all_source_files = set()
            for asset in assets_dict:
                all_source_files.update(asset['files'])
            
            total_files = len(all_source_files)
            log_info(LogTags.POSTER_RENAMER, f"Found {total_files} source poster files", count=total_files)
            
            # Note: We don't filter assets_dict here based on DB status
            # Instead, we let rename_files check each destination file individually
            # This allows precise detection of missing/changed destination files
            
            # Phase 2: Get media from instances (20-40%)
            if progress_callback:
                progress_callback("fetching", 20, 100, "Fetching media from configured instances...")
            
            log_info(LogTags.POSTER_RENAMER, "Fetching media from configured instances...")
            media_dict = self.get_media_from_instances()
            
            if not any(media_dict.values()):
                return {
                    "success": False,
                    "error": "No media found. Check Settings → Media tab to configure Plex/Radarr/Sonarr."
                }
            
            # Phase 3: Match assets to media (40-50%)
            if progress_callback:
                progress_callback("matching", 40, 100, "Matching assets to media...")
            
            log_info(LogTags.POSTER_RENAMER, "Matching assets to media, please wait...")
            
            # Log details before matching for debugging
            media_counts = {k: len(v) for k, v in media_dict.items() if v}
            total_media = sum(media_counts.values())
            total_assets = len(assets_dict)
            
            log_info(
                LogTags.POSTER_RENAMER,
                f"Matching {total_assets} assets against {total_media} media items "
                f"(movies: {media_counts.get('movies', 0)}, series: {media_counts.get('series', 0)}, "
                f"collections: {media_counts.get('collections', 0)})"
            )
            
            matched_assets = match_assets_to_media(
                media_dict,
                prefix_index,
                strict_folder_match=False,
            )
            
            if not matched_assets or not any(matched_assets.values()):
                # Log detailed diagnostic information
                log_error(LogTags.POSTER_RENAMER, "No assets matched to media - diagnostic info:")
                log_error(LogTags.POSTER_RENAMER, f"  Assets found: {total_assets}")
                log_error(LogTags.POSTER_RENAMER, f"  Media items: {total_media} total")
                for media_type, count in media_counts.items():
                    log_error(LogTags.POSTER_RENAMER, f"    - {media_type}: {count}")
                
                # Show sample assets and media for debugging
                if assets_dict and len(assets_dict) > 0:
                    sample_asset = assets_dict[0]
                    log_error(
                        LogTags.POSTER_RENAMER,
                        f"  Sample asset: '{sample_asset.get('title', 'N/A')}' "
                        f"(year: {sample_asset.get('year', 'N/A')}, "
                        f"type: {sample_asset.get('type', 'N/A')}, "
                        f"tmdb: {sample_asset.get('tmdb_id', 'N/A')})"
                    )
                
                for media_type in ['movies', 'series', 'collections']:
                    if media_dict.get(media_type) and len(media_dict[media_type]) > 0:
                        sample_media = media_dict[media_type][0]
                        log_error(
                            LogTags.POSTER_RENAMER,
                            f"  Sample {media_type}: '{sample_media.get('title', 'N/A')}' "
                            f"(year: {sample_media.get('year', 'N/A')}, "
                            f"tmdb: {sample_media.get('tmdb_id', 'N/A')}, "
                            f"folder: {sample_media.get('folder', 'N/A')})"
                        )
                        break
                
                return {
                    "success": False,
                    "error": f"No assets matched to media. Found {total_assets} assets and {total_media} media items but no matches. Check logs for details."
                }
            
            # Phase 4: Rename files (50-100%)
            if progress_callback:
                progress_callback("renaming", 50, 100, "Renaming and organizing files...")
            
            # Create nested callback for rename progress
            def rename_progress(current: int, total: int, status_message: str) -> None:
                if progress_callback:
                    # Map 0-total to 50-100%
                    progress = 50 + int((current / total) * 50)
                    progress_callback("renaming", progress, 100, status_message)
            
            output, renamed_files, processed_source_files = self.rename_files(
                matched_assets,
                actual_destination,  # Use actual_destination (may be tmp folder)
                action_type,
                asset_folders,
                dry_run,
                progress_callback=rename_progress,
            )
            
            # Log what actually happened
            files_copied = len(renamed_files)
            files_skipped = len(processed_source_files) - files_copied
            log_info(
                LogTags.POSTER_RENAMER,
                f"Results: {files_copied} files copied/updated, {files_skipped} already in place",
                copied=files_copied, skipped=files_skipped
            )
            
            # Calculate stats
            total_renamed = sum(len(items) for items in output.values())
            
            stats = {
                "total_assets": len(assets_dict),
                "total_media": sum(len(v) for v in media_dict.values()),
                "total_matched": total_renamed,
                "movies": len(output.get("movies", [])),
                "series": len(output.get("series", [])),
                "collections": len(output.get("collections", [])),
            }
            
            # Phase 6: Mark files as processed in DB (if not dry run)
            if not dry_run and processed_source_files:
                if progress_callback:
                    progress_callback("finalizing", 95, 100, "Updating processing status...")
                
                log_info(
                    LogTags.POSTER_RENAMER,
                    f"Marking {len(processed_source_files)} source files as processed...",
                    count=len(processed_source_files)
                )
                
                from models.poster import Poster
                from models.drive import Drive
                from datetime import datetime, timezone
                
                now = datetime.now(timezone.utc)
                marked_count = 0
                drives_updated = set()
                
                # Mark source files that were actually processed (copied or verified as identical)
                for file_path in processed_source_files:
                    resolved_path = str(Path(file_path).resolve())
                    poster = self.db.query(Poster).filter(Poster.file_path == resolved_path).first()
                    if poster:
                        poster.last_processed = now
                        drives_updated.add(poster.drive_id)
                        marked_count += 1
                
                self.db.commit()
                
                # Update drive stats
                for drive_id in drives_updated:
                    drive = self.db.query(Drive).filter(Drive.drive_id == drive_id).first()
                    if drive:
                        drive.last_rename_processed = now
                
                self.db.commit()
                
                log_success(
                    LogTags.POSTER_RENAMER,
                    f"Marked {marked_count} files as processed across {len(drives_updated)} drives",
                    marked=marked_count, drives=len(drives_updated)
                )
            
            # Log completion
            mode = "DRY RUN" if dry_run else action_type.upper()
            log_success(
                LogTags.POSTER_RENAMER,
                f"Completed: {stats['total_matched']} matched "
                f"(Movies: {stats['movies']}, Series: {stats['series']}, Collections: {stats['collections']}) - Mode: {mode}",
                matched=stats['total_matched'], movies=stats['movies'], 
                series=stats['series'], collections=stats['collections'], mode=mode
            )
            
            log_section_end(LogTags.POSTER_RENAMER, "Poster Renamer Complete")
            
            return {
                "success": True,
                "output": output,
                "renamed_files": renamed_files,
                "stats": stats,
                "dry_run": dry_run,
                "destination_dir": actual_destination,
                "using_temp_folder": use_temp_folder,
            }
            
        except Exception as e:
            # Provide detailed error context
            error_msg = f"{type(e).__name__}: {str(e)}"
            error_detail = traceback.format_exc()
            log_error(LogTags.POSTER_RENAMER, f"Error during poster renaming: {error_msg}\n{error_detail}")
            log_section_end(LogTags.POSTER_RENAMER, "Poster Renamer Failed")
            return {
                "success": False,
                "error": error_msg
            }
