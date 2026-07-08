from util.data.construct import (
    create_collection,
    create_movie,
    create_series,
    generate_title_variants,
)


# ---------------------------------------------------------------------------
# generate_title_variants
# ---------------------------------------------------------------------------


def test_generate_title_variants_returns_required_keys():
    result = generate_title_variants("The Dark Knight")
    assert "alternate_titles" in result
    assert "normalized_alternate_titles" in result


def test_generate_title_variants_strips_known_prefix():
    result = generate_title_variants("The Dark Knight")
    # "The" is a known prefix; stripped version should appear
    assert "Dark Knight" in result["alternate_titles"]


def test_generate_title_variants_strips_known_suffix():
    result = generate_title_variants("Avengers Collection")
    # "Collection" is a known suffix; stripped version should appear
    assert "Avengers" in result["alternate_titles"]


def test_generate_title_variants_appends_collection_for_non_collection():
    result = generate_title_variants("Star Wars")
    assert "Star Wars Collection" in result["alternate_titles"]


def test_generate_title_variants_does_not_double_append_collection():
    result = generate_title_variants("Avengers Collection")
    # Should not add "Avengers Collection Collection"
    assert "Avengers Collection Collection" not in result["alternate_titles"]


def test_generate_title_variants_deduplicates():
    result = generate_title_variants("Batman")
    # No prefix/suffix to strip, so duplicates would appear without dedup
    assert len(result["alternate_titles"]) == len(set(result["alternate_titles"]))


# ---------------------------------------------------------------------------
# create_collection
# ---------------------------------------------------------------------------


def test_create_collection_returns_correct_type():
    result = create_collection("Avengers", 131292, "avengers", ["/a.jpg", "/b.jpg"])
    assert result["type"] == "collections"


def test_create_collection_uses_last_file():
    result = create_collection("Avengers", 131292, "avengers", ["/a.jpg", "/b.jpg"])
    assert result["files"] == ["/b.jpg"]


def test_create_collection_sets_year_to_none():
    result = create_collection("Avengers", 131292, "avengers", ["/a.jpg"])
    assert result["year"] is None


def test_create_collection_includes_tmdb_id():
    result = create_collection("Avengers", 131292, "avengers", ["/a.jpg"])
    assert result["tmdb_id"] == 131292


def test_create_collection_stores_folder_and_media_folder():
    result = create_collection(
        "Avengers", 131292, "avengers", ["/a.jpg"],
        parent_folder="movies", media_folder="/media/avengers"
    )
    assert result["folder"] == "movies"
    assert result["media_folder"] == "/media/avengers"


# ---------------------------------------------------------------------------
# create_movie
# ---------------------------------------------------------------------------


def test_create_movie_returns_correct_type():
    result = create_movie("Inception", 2010, 27205, None, "inception", ["/poster.jpg"])
    assert result["type"] == "movies"


def test_create_movie_uses_last_file():
    result = create_movie("Inception", 2010, 27205, None, "inception", ["/a.jpg", "/b.jpg"])
    assert result["files"] == ["/b.jpg"]


def test_create_movie_stores_metadata():
    result = create_movie("Inception", 2010, 27205, "tt1375666", "inception", ["/p.jpg"])
    assert result["title"] == "Inception"
    assert result["year"] == 2010
    assert result["tmdb_id"] == 27205
    assert result["imdb_id"] == "tt1375666"
    assert result["normalized_title"] == "inception"


def test_create_movie_none_ids_accepted():
    result = create_movie("Mystery", None, None, None, "mystery", ["/p.jpg"])
    assert result["tmdb_id"] is None
    assert result["year"] is None


# ---------------------------------------------------------------------------
# create_series
# ---------------------------------------------------------------------------


def test_create_series_returns_correct_type():
    result = create_series(
        "Breaking Bad", 2008, 81189, "tt0903747", "breaking bad",
        ["/poster.jpg", "/Season 01.jpg", "/Season 02.jpg"]
    )
    assert result["type"] == "series"


def test_create_series_extracts_season_numbers():
    result = create_series(
        "Breaking Bad", 2008, 81189, None, "breaking bad",
        ["/Season 01.jpg", "/Season 02.jpg", "/Season 03.jpg"]
    )
    assert result["season_numbers"] == [1, 2, 3]


def test_create_series_season_numbers_sorted():
    result = create_series(
        "Show", 2020, 1, None, "show",
        ["/Season 03.jpg", "/Season 01.jpg", "/Season 02.jpg"]
    )
    assert result["season_numbers"] == [1, 2, 3]


def test_create_series_handles_specials():
    result = create_series(
        "Show", 2020, 1, None, "show",
        ["/Season 01.jpg", "/Specials.jpg"]
    )
    assert 0 in result["season_numbers"]


def test_create_series_stores_poster_separately():
    result = create_series(
        "Show", 2020, 1, None, "show",
        ["/poster.jpg", "/Season 01.jpg"]
    )
    # Series poster (no season match) should be included
    assert any("poster.jpg" in f for f in result["files"])


def test_create_series_stores_ids():
    result = create_series(
        "Show", 2020, 99, "tt123", "show", ["/Season 01.jpg"]
    )
    assert result["tvdb_id"] == 99
    assert result["imdb_id"] == "tt123"
    assert result["year"] == 2020


def test_create_series_stores_tmdb_id():
    result = create_series(
        "Show", 2020, 99, "tt123", "show", ["/Season 01.jpg"], tmdb_id=302228
    )
    assert result["tmdb_id"] == 302228


def test_create_series_tmdb_id_defaults_none():
    result = create_series(
        "Show", 2020, 99, "tt123", "show", ["/Season 01.jpg"]
    )
    assert result["tmdb_id"] is None
