"""
Utility modules for poster matching and processing.

Organized structure:
  - util.arr: ARR (Radarr/Sonarr) client
  - util.data: Data processing (extract, normalize, construct)
  - util.posters: Poster operations (scan, index, match, assets)

All utilities are also re-exported at the top level for convenience.
"""

# ARR client
from util.arr.client import ARRClient, create_arr_client

# Data processing utilities
from util.data.construct import (
    create_collection,
    create_movie,
    create_series,
    generate_title_variants,
)
from util.data.extract import extract_ids, extract_year
from util.data.normalization import (
    normalize_file_names,
    normalize_titles,
    remove_common_words,
    remove_tokens,
)

# Poster utilities
from util.posters.assets import get_assets_files, merge_assets
from util.posters.index import build_search_index, create_new_empty_index, search_matches
from util.posters.match import (
    compare_strings,
    handle_series_match,
    is_match,
    match_assets_to_media,
)
from util.posters.scanner import (
    parse_file_group,
    parse_folder_group,
    process_files,
    scan_files_in_flat_folder,
    scan_files_in_nested_folders,
)


__all__ = [
    # ARR
    'ARRClient',
    'create_arr_client',
    # Data processing
    'create_collection',
    'create_movie',
    'create_series',
    'generate_title_variants',
    'extract_ids',
    'extract_year',
    'normalize_file_names',
    'normalize_titles',
    'remove_common_words',
    'remove_tokens',
    # Posters
    'get_assets_files',
    'merge_assets',
    'build_search_index',
    'create_new_empty_index',
    'search_matches',
    'compare_strings',
    'handle_series_match',
    'is_match',
    'match_assets_to_media',
    'parse_file_group',
    'parse_folder_group',
    'process_files',
    'scan_files_in_flat_folder',
    'scan_files_in_nested_folders',
]
