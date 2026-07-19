from pathlib import Path

from util.posters.scanner import (
    _is_asset_folders,
    parse_file_group,
    parse_folder_group,
    process_files,
    scan_files_in_flat_folder,
    scan_files_in_nested_folders,
)


# ---------------------------------------------------------------------------
# _is_asset_folders
# ---------------------------------------------------------------------------


def test_is_asset_folders_true_when_subfolder_present(tmp_path):
    (tmp_path / "MovieFolder").mkdir()
    assert _is_asset_folders(str(tmp_path)) is True


def test_is_asset_folders_false_when_only_files(tmp_path):
    (tmp_path / "poster.jpg").write_bytes(b"img")
    assert _is_asset_folders(str(tmp_path)) is False


def test_is_asset_folders_false_for_nonexistent_path():
    assert _is_asset_folders("/nonexistent/path/xyz") is False


def test_is_asset_folders_skips_hidden_dirs(tmp_path):
    (tmp_path / ".hidden").mkdir()
    assert _is_asset_folders(str(tmp_path)) is False


def test_is_asset_folders_skips_tmp_dir(tmp_path):
    (tmp_path / "tmp").mkdir()
    assert _is_asset_folders(str(tmp_path)) is False


# ---------------------------------------------------------------------------
# scan_files_in_flat_folder
# ---------------------------------------------------------------------------


def test_scan_flat_folder_parses_movie(tmp_path):
    (tmp_path / "Inception (2010) {tmdb-27205}.jpg").write_bytes(b"img")
    results = scan_files_in_flat_folder(str(tmp_path))
    assert len(results) == 1
    assert results[0]["type"] == "movies"
    assert results[0]["year"] == 2010
    assert results[0]["tmdb_id"] == 27205


def test_scan_flat_folder_parses_series_with_season_files(tmp_path):
    (tmp_path / "Breaking Bad (2008) {tvdb-81189}.jpg").write_bytes(b"img")
    (tmp_path / "Breaking Bad (2008) {tvdb-81189} - Season 01.jpg").write_bytes(b"img")
    results = scan_files_in_flat_folder(str(tmp_path))
    assert len(results) == 1
    assert results[0]["type"] == "series"


def test_scan_flat_folder_returns_empty_for_missing_dir():
    results = scan_files_in_flat_folder("/no/such/folder")
    assert results == []


def test_scan_flat_folder_skips_hidden_files(tmp_path):
    (tmp_path / ".DS_Store").write_bytes(b"junk")
    (tmp_path / "Inception (2010).jpg").write_bytes(b"img")
    results = scan_files_in_flat_folder(str(tmp_path))
    assert len(results) == 1


def test_scan_flat_folder_groups_multiple_files_for_same_title(tmp_path):
    (tmp_path / "Dune (2021).jpg").write_bytes(b"img")
    (tmp_path / "Dune (2021) - Season 01.jpg").write_bytes(b"img")
    results = scan_files_in_flat_folder(str(tmp_path))
    # Both files belong to the same title → one group
    assert len(results) == 1


# ---------------------------------------------------------------------------
# scan_files_in_nested_folders
# ---------------------------------------------------------------------------


def test_scan_nested_folders_parses_movie_folder(tmp_path):
    movie_dir = tmp_path / "Inception (2010) {tmdb-27205}"
    movie_dir.mkdir()
    (movie_dir / "poster.jpg").write_bytes(b"img")
    results = scan_files_in_nested_folders(str(tmp_path))
    assert len(results) == 1
    assert results[0]["type"] == "movies"
    assert results[0]["tmdb_id"] == 27205


def test_scan_nested_folders_skips_empty_subfolders(tmp_path):
    (tmp_path / "EmptyFolder").mkdir()
    results = scan_files_in_nested_folders(str(tmp_path))
    assert results == []


def test_scan_nested_folders_skips_hidden_dirs(tmp_path):
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "poster.jpg").write_bytes(b"img")
    results = scan_files_in_nested_folders(str(tmp_path))
    assert results == []


def test_scan_nested_folders_skips_non_image_files(tmp_path):
    folder = tmp_path / "Movie (2020)"
    folder.mkdir()
    (folder / "readme.txt").write_bytes(b"text")
    results = scan_files_in_nested_folders(str(tmp_path))
    # txt file is not an image → folder is skipped as empty
    assert results == []


# ---------------------------------------------------------------------------
# parse_folder_group
# ---------------------------------------------------------------------------


def test_parse_folder_group_movie(tmp_path):
    folder = tmp_path / "Inception (2010) {tmdb-27205}"
    folder.mkdir()
    files = ["poster.jpg"]
    (folder / "poster.jpg").write_bytes(b"img")
    result = parse_folder_group(str(folder), "Inception (2010) {tmdb-27205}", files)
    assert result["type"] == "movies"
    assert result["year"] == 2010
    assert result["tmdb_id"] == 27205


def test_parse_folder_group_series_detected_by_multiple_season_files(tmp_path):
    folder = tmp_path / "Breaking Bad (2008)"
    folder.mkdir()
    files = ["Season 01.jpg", "Season 02.jpg"]
    for f in files:
        (folder / f).write_bytes(b"img")
    result = parse_folder_group(str(folder), "Breaking Bad (2008)", files)
    assert result["type"] == "series"


def test_parse_folder_group_collection_when_no_year(tmp_path):
    folder = tmp_path / "Avengers Collection"
    folder.mkdir()
    (folder / "poster.jpg").write_bytes(b"img")
    result = parse_folder_group(str(folder), "Avengers Collection", ["poster.jpg"])
    assert result["type"] == "collections"
    assert result["year"] is None


# ---------------------------------------------------------------------------
# parse_file_group
# ---------------------------------------------------------------------------


def test_parse_file_group_movie(tmp_path):
    (tmp_path / "Inception (2010) {tmdb-27205}.jpg").write_bytes(b"img")
    result = parse_file_group(
        str(tmp_path),
        "Inception (2010) {tmdb-27205}",
        ["Inception (2010) {tmdb-27205}.jpg"],
    )
    assert result["type"] == "movies"
    assert result["year"] == 2010


def test_parse_file_group_series_by_season_file(tmp_path):
    (tmp_path / "Show (2020) - Season 01.jpg").write_bytes(b"img")
    result = parse_file_group(
        str(tmp_path),
        "Show (2020)",
        ["Show (2020) - Season 01.jpg"],
    )
    assert result["type"] == "series"


def test_parse_file_group_collection_no_year(tmp_path):
    (tmp_path / "Marvel Collection.jpg").write_bytes(b"img")
    result = parse_file_group(
        str(tmp_path),
        "Marvel Collection",
        ["Marvel Collection.jpg"],
    )
    assert result["type"] == "collections"


# ---------------------------------------------------------------------------
# process_files (integration: chooses flat vs nested automatically)
# ---------------------------------------------------------------------------


def test_process_files_flat_folder(tmp_path):
    (tmp_path / "Inception (2010).jpg").write_bytes(b"img")
    results = process_files(str(tmp_path))
    assert results is not None
    assert len(results) >= 1


def test_process_files_nested_folder(tmp_path):
    movie_dir = tmp_path / "Inception (2010) {tmdb-27205}"
    movie_dir.mkdir()
    (movie_dir / "poster.jpg").write_bytes(b"img")
    results = process_files(str(tmp_path))
    assert results is not None
    assert len(results) == 1


def test_process_files_returns_none_for_empty_folder(tmp_path):
    results = process_files(str(tmp_path))
    assert results is None


# ---------------------------------------------------------------------------
# id parsing (the scanner reports ids as-found; no-match diagnosis lives in match.py)
# ---------------------------------------------------------------------------


def test_series_imdb_only_parsed_as_found(tmp_path):
    # An imdb-only series folder is legitimate when the library item also carries an imdb
    # (e.g. Sonarr folders named "{imdb-...}"), so the scanner reports it without judging
    # matchability — that call needs the media side and lives in match.py.
    folder = tmp_path / "RIPLEY (2024) {imdb-tt11016042}"
    folder.mkdir()
    (folder / "poster.jpg").write_bytes(b"x")
    (folder / "Season01.jpg").write_bytes(b"x")

    results = scan_files_in_nested_folders(str(tmp_path))

    assert len(results) == 1
    assert results[0]["type"] == "series"
    assert results[0]["imdb_id"] == "tt11016042"
    assert results[0]["tvdb_id"] is None


def test_series_all_ids_parsed(tmp_path):
    folder = tmp_path / "RIPLEY (2024) {tmdb-94028} {tvdb-372727} {imdb-tt11016042}"
    folder.mkdir()
    (folder / "poster.jpg").write_bytes(b"x")

    results = scan_files_in_nested_folders(str(tmp_path))

    assert (results[0]["tmdb_id"], results[0]["tvdb_id"], results[0]["imdb_id"]) == (
        94028, 372727, "tt11016042",
    )
