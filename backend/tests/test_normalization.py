"""Tests for util/data/normalization.py"""
import pytest

from util.data.normalization import (
    normalize_file_names,
    normalize_titles,
    remove_common_words,
    remove_tokens,
)


# ---------------------------------------------------------------------------
# remove_common_words
# ---------------------------------------------------------------------------

class TestRemoveCommonWords:
    def test_removes_known_common_word(self):
        # "the" and "a" are in common_words
        result = remove_common_words("The Dark Knight")
        words = result.lower().split()
        assert "the" not in words

    def test_case_insensitive(self):
        result = remove_common_words("THE dark knight")
        assert "THE" not in result.split()
        assert "the" not in result.lower().split()

    def test_preserves_non_common_words(self):
        result = remove_common_words("Inception 2010")
        assert "Inception" in result or "inception" in result.lower()

    def test_empty_string(self):
        assert remove_common_words("") == ""

    def test_only_common_words_become_empty(self):
        # A string containing only filler words should collapse to empty string
        result = remove_common_words("a the")
        assert result.strip() == ""


# ---------------------------------------------------------------------------
# normalize_file_names
# ---------------------------------------------------------------------------

class TestNormalizeFileNames:
    def test_strips_extension(self):
        result = normalize_file_names("Inception.jpg")
        assert "jpg" not in result

    def test_lowercases(self):
        result = normalize_file_names("INCEPTION.jpg")
        assert result == result.lower()

    def test_removes_spaces(self):
        result = normalize_file_names("The Dark Knight.jpg")
        assert " " not in result

    def test_removes_year_curly_braces(self):
        # ID tokens in curly braces, e.g. {tmdb-12345}
        result = normalize_file_names("Inception {tmdb-27205}.jpg")
        assert "tmdb" not in result
        assert "{" not in result

    def test_unicode_transliterated(self):
        # Non-ASCII characters should be transliterated to ASCII
        result = normalize_file_names("Naïve.jpg")
        assert all(ord(c) < 128 for c in result)

    def test_html_entity_decoded(self):
        result = normalize_file_names("Jack &amp; Jill.jpg")
        assert "amp" not in result

    @pytest.mark.parametrize(
        "filename",
        [
            "Avatar (2009).jpg",
            "Avengers Endgame.jpg",
            "The.Dark.Knight.2008.jpg",
        ],
    )
    def test_returns_string(self, filename):
        result = normalize_file_names(filename)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# normalize_titles
# ---------------------------------------------------------------------------

class TestNormalizeTitles:
    def test_strips_year_tag(self):
        result = normalize_titles("Inception (2010)")
        assert "2010" not in result
        assert "(" not in result

    def test_lowercases(self):
        result = normalize_titles("Inception")
        assert result == result.lower()

    def test_removes_spaces(self):
        result = normalize_titles("The Dark Knight")
        assert " " not in result

    def test_unicode_transliterated(self):
        result = normalize_titles("Naïve Film")
        assert all(ord(c) < 128 for c in result)

    def test_html_entity_decoded(self):
        result = normalize_titles("Jack &amp; Jill")
        assert "amp" not in result

    def test_removes_id_curly_braces_content(self):
        result = normalize_titles("Inception {tmdb-27205}")
        assert "tmdb" not in result

    def test_empty_string(self):
        result = normalize_titles("")
        assert result == ""

    def test_same_title_normalizes_consistently(self):
        assert normalize_titles("The Dark Knight") == normalize_titles("The Dark Knight")

    def test_ampersand_matches_and(self):
        # "&" and "and" should normalize to the same string
        assert normalize_titles("Cloak & Dagger") == normalize_titles("Cloak and Dagger")

    def test_html_entity_ampersand_matches_and(self):
        # "&amp;" unescapes to "&", then expands to "and"
        assert normalize_titles("Jack &amp; Jill") == normalize_titles("Jack and Jill")

    @pytest.mark.parametrize(
        "title, expected_contains",
        [
            ("Avengers: Endgame", "avengersendgame"),
            ("Spider-Man: No Way Home", "spidermannowayhome"),
        ],
    )
    def test_special_chars_stripped(self, title, expected_contains):
        result = normalize_titles(title)
        assert expected_contains in result
