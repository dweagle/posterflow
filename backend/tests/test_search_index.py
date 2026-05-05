from util.posters.index import (
    build_search_index,
    create_new_empty_index,
    remove_common_words,
    search_matches,
)


# ---------------------------------------------------------------------------
# create_new_empty_index
# ---------------------------------------------------------------------------


def test_create_new_empty_index_returns_empty_dict():
    index = create_new_empty_index()
    assert index == {}
    assert isinstance(index, dict)


# ---------------------------------------------------------------------------
# remove_common_words
# ---------------------------------------------------------------------------


def test_remove_common_words_strips_articles():
    assert remove_common_words("the dark knight") == "dark knight"
    assert remove_common_words("a beautiful mind") == "beautiful mind"
    assert remove_common_words("an american tail") == "american tail"


def test_remove_common_words_leaves_content_words():
    assert remove_common_words("inception") == "inception"


def test_remove_common_words_handles_empty_string():
    assert remove_common_words("") == ""


def test_remove_common_words_is_case_insensitive():
    assert remove_common_words("The Dark Knight") == "Dark Knight"


def test_remove_common_words_does_not_strip_substrings():
    # "an" should not be stripped from "anarchy" (it's not a standalone word)
    result = remove_common_words("anarchy rules")
    assert "anarchy" in result


# ---------------------------------------------------------------------------
# build_search_index + search_matches
# ---------------------------------------------------------------------------


def _make_movie(title: str, tmdb_id: int | None = None) -> dict:
    return {"type": "movies", "title": title, "tmdb_id": tmdb_id, "tvdb_id": None}


def _make_series(title: str, tvdb_id: int | None = None) -> dict:
    return {"type": "series", "title": title, "tmdb_id": None, "tvdb_id": tvdb_id}


def test_build_and_search_by_tmdb_id():
    index = create_new_empty_index()
    asset = _make_movie("Inception", tmdb_id=27205)
    build_search_index(index, "Inception", asset)

    results = search_matches(index, "Inception", tmdb_id=27205)
    assert len(results) == 1
    assert results[0]["tmdb_id"] == 27205


def test_build_and_search_by_tvdb_id():
    index = create_new_empty_index()
    asset = _make_series("Breaking Bad", tvdb_id=81189)
    build_search_index(index, "Breaking Bad", asset)

    results = search_matches(index, "Breaking Bad", tvdb_id=81189)
    assert len(results) == 1
    assert results[0]["tvdb_id"] == 81189


def test_search_by_tmdb_returns_empty_for_wrong_id():
    index = create_new_empty_index()
    asset = _make_movie("Inception", tmdb_id=27205)
    build_search_index(index, "Inception", asset)

    results = search_matches(index, "Inception", tmdb_id=99999)
    assert results == []


def test_build_and_search_by_title_prefix():
    index = create_new_empty_index()
    asset = _make_movie("Inception")
    build_search_index(index, "Inception", asset)

    results = search_matches(index, "Inception")
    assert len(results) >= 1
    assert any(r["title"] == "Inception" for r in results)


def test_search_returns_empty_when_index_is_empty():
    index = create_new_empty_index()
    results = search_matches(index, "Anything")
    assert results == []


def test_multiple_assets_indexed_separately():
    index = create_new_empty_index()
    movie_a = _make_movie("Inception", tmdb_id=1)
    movie_b = _make_movie("Interstellar", tmdb_id=2)
    build_search_index(index, "Inception", movie_a)
    build_search_index(index, "Interstellar", movie_b)

    assert search_matches(index, "x", tmdb_id=1)[0]["tmdb_id"] == 1
    assert search_matches(index, "x", tmdb_id=2)[0]["tmdb_id"] == 2
