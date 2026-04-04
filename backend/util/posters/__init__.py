"""Poster processing utilities - scanning, indexing, matching, and asset management."""
from util.posters.assets import get_assets_files, merge_assets
from util.posters.index import (
    build_search_index,
    create_new_empty_index,
    search_matches,
)
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
    # assets
    'get_assets_files',
    'merge_assets',
    # index
    'build_search_index',
    'create_new_empty_index',
    'search_matches',
    # match
    'compare_strings',
    'handle_series_match',
    'is_match',
    'match_assets_to_media',
    # scanner
    'parse_file_group',
    'parse_folder_group',
    'process_files',
    'scan_files_in_flat_folder',
    'scan_files_in_nested_folders',
]
