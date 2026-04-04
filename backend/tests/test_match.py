"""Tests for util/posters/match.py"""
import pytest

from util.posters.match import compare_strings, collection_title_variants, is_match


# ---------------------------------------------------------------------------
# compare_strings
# ---------------------------------------------------------------------------

class TestCompareStrings:
    def test_exact_match(self):
        assert compare_strings("Inception", "Inception") is True

    def test_case_insensitive(self):
        assert compare_strings("inception", "INCEPTION") is True

    def test_punctuation_stripped(self):
        assert compare_strings("Spider-Man: No Way Home", "SpiderMan No Way Home") is True

    def test_different_strings(self):
        assert compare_strings("Avatar", "Inception") is False

    def test_empty_strings(self):
        assert compare_strings("", "") is True

    def test_one_empty(self):
        assert compare_strings("Avatar", "") is False


# ---------------------------------------------------------------------------
# collection_title_variants
# ---------------------------------------------------------------------------

class TestCollectionTitleVariants:
    def test_title_with_collection_suffix_strips_it(self):
        variants = collection_title_variants("Avengers Collection")
        assert "Avengers Collection" in variants
        assert "Avengers" in variants

    def test_title_with_collections_suffix_strips_it(self):
        variants = collection_title_variants("Marvel Collections")
        assert "Marvel Collections" in variants
        assert "Marvel" in variants

    def test_title_without_suffix_adds_collection(self):
        variants = collection_title_variants("Avengers")
        assert "Avengers" in variants
        assert "Avengers Collection" in variants

    def test_empty_string_does_not_raise(self):
        variants = collection_title_variants("")
        assert isinstance(variants, list)

    def test_case_insensitive_suffix_check(self):
        # suffix matching should be case-insensitive
        variants = collection_title_variants("Avengers collection")
        assert "Avengers" in variants


# ---------------------------------------------------------------------------
# is_match
# ---------------------------------------------------------------------------

class TestIsMatchByIds:
    """When both asset and media carry numeric IDs, matching should be ID-only."""

    def _base_asset(self, **kwargs):
        return {"title": "Generic", "tmdb_id": 1, "normalized_title": "generic", **kwargs}

    def _base_media(self, **kwargs):
        return {"title": "Generic", "tmdb_id": 1, "normalized_title": "generic", **kwargs}

    def test_matches_by_tmdb_id(self):
        asset = self._base_asset(tmdb_id=27205)
        media = self._base_media(tmdb_id=27205)
        matched, reason = is_match(asset, media)
        assert matched is True
        assert "tmdb_id" in reason

    def test_no_match_when_tmdb_ids_differ(self):
        asset = self._base_asset(tmdb_id=27205)
        media = self._base_media(tmdb_id=99999)
        matched, _ = is_match(asset, media)
        assert matched is False

    def test_matches_by_tvdb_id(self):
        asset = {"title": "A", "tvdb_id": 888, "normalized_title": "a"}
        media = {"title": "A", "tvdb_id": 888, "normalized_title": "a"}
        matched, reason = is_match(asset, media)
        assert matched is True
        assert "tvdb_id" in reason

    def test_matches_by_imdb_id(self):
        asset = {"title": "A", "imdb_id": "tt1234567", "normalized_title": "a"}
        media = {"title": "A", "imdb_id": "tt1234567", "normalized_title": "a"}
        matched, reason = is_match(asset, media)
        assert matched is True
        assert "imdb_id" in reason

    def test_imdb_id_without_tt_prefix_not_used(self):
        # imdb_id without "tt" prefix is not considered a valid ID
        asset = {"title": "A", "imdb_id": "1234567", "normalized_title": "a"}
        media = {"title": "A", "imdb_id": "1234567", "normalized_title": "a"}
        # Both lack valid IDs → falls through to title matching
        matched, reason = is_match(asset, media)
        # Should still match by title
        assert matched is True


class TestIsMatchByTitle:
    """Fallback title matching when IDs are absent."""

    def test_exact_title_match(self):
        asset = {"title": "Inception", "normalized_title": "inception"}
        media = {"title": "Inception", "normalized_title": "inception"}
        matched, reason = is_match(asset, media)
        assert matched is True
        assert "title" in reason

    def test_normalized_title_match(self):
        asset = {"title": "The Dark Knight", "normalized_title": "darkknight"}
        media = {"title": "The Dark Knight", "normalized_title": "darkknight"}
        matched, reason = is_match(asset, media)
        assert matched is True

    def test_no_match_different_titles(self):
        asset = {"title": "Avatar", "normalized_title": "avatar"}
        media = {"title": "Inception", "normalized_title": "inception"}
        matched, _ = is_match(asset, media)
        assert matched is False

    def test_year_mismatch_blocks_title_match(self):
        asset = {"title": "Avatar", "normalized_title": "avatar", "year": 2009}
        media = {"title": "Avatar", "normalized_title": "avatar", "year": 2022}
        matched, _ = is_match(asset, media)
        assert matched is False

    def test_matching_years_allows_title_match(self):
        asset = {"title": "Avatar", "normalized_title": "avatar", "year": 2009}
        media = {"title": "Avatar", "normalized_title": "avatar", "year": 2009}
        matched, _ = is_match(asset, media)
        assert matched is True

    def test_no_years_on_either_side_still_matches(self):
        asset = {"title": "Avatar", "normalized_title": "avatar"}
        media = {"title": "Avatar", "normalized_title": "avatar"}
        matched, _ = is_match(asset, media)
        assert matched is True


class TestIsMatchStrictFolder:
    def test_strict_folder_match_by_media_folder(self):
        asset = {"media_folder": "/media/movies/Avatar (2009)", "year": 2009}
        media = {"folder": "/media/movies/Avatar (2009)", "year": 2009}
        matched, reason = is_match(asset, media, strict_folder_match=True)
        assert matched is True
        assert "folder" in reason

    def test_strict_folder_no_match_when_folders_differ(self):
        asset = {"media_folder": "/media/movies/Inception", "year": 2010}
        media = {"folder": "/media/movies/Avatar", "year": 2009}
        matched, _ = is_match(asset, media, strict_folder_match=True)
        assert matched is False
