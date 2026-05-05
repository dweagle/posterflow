from util.data.extract import extract_ids, extract_year


# ---------------------------------------------------------------------------
# extract_year
# ---------------------------------------------------------------------------


def test_extract_year_from_parentheses():
    assert extract_year("The Batman (2022)") == 2022


def test_extract_year_ignores_curly_brace_format():
    # year_regex only matches (YYYY) parentheses, not curly braces
    assert extract_year("Inception {2010}") is None


def test_extract_year_returns_first_match():
    assert extract_year("Show (2019) Season (2020)") == 2019


def test_extract_year_returns_none_when_absent():
    assert extract_year("No Year Here") is None


def test_extract_year_returns_none_for_empty_string():
    assert extract_year("") is None


def test_extract_year_ignores_bare_four_digit_number():
    # year_regex requires (YYYY) format — bare numbers are not matched
    assert extract_year("2001: A Space Odyssey") is None


# ---------------------------------------------------------------------------
# extract_ids
# ---------------------------------------------------------------------------


def test_extract_ids_tmdb_only():
    tmdb, tvdb, imdb = extract_ids("Breaking Bad {tmdb-1396}")
    assert tmdb == 1396
    assert tvdb is None
    assert imdb is None


def test_extract_ids_tvdb_only():
    tmdb, tvdb, imdb = extract_ids("Breaking Bad {tvdb-81189}")
    assert tmdb is None
    assert tvdb == 81189
    assert imdb is None


def test_extract_ids_imdb_only():
    tmdb, tvdb, imdb = extract_ids("Breaking Bad {imdb-tt0903747}")
    assert tmdb is None
    assert tvdb is None
    assert imdb == "tt0903747"


def test_extract_ids_all_three():
    tmdb, tvdb, imdb = extract_ids(
        "Show {tmdb-1234} {tvdb-5678} {imdb-tt9999999}"
    )
    assert tmdb == 1234
    assert tvdb == 5678
    assert imdb == "tt9999999"


def test_extract_ids_none_present():
    tmdb, tvdb, imdb = extract_ids("Plain Title (2022)")
    assert tmdb is None
    assert tvdb is None
    assert imdb is None


def test_extract_ids_empty_string():
    tmdb, tvdb, imdb = extract_ids("")
    assert tmdb is None
    assert tvdb is None
    assert imdb is None
