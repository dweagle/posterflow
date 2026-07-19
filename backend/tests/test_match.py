"""Tests for util/posters/match.py"""
import pytest

from util.posters.index import build_search_index, create_new_empty_index
from util.posters.match import (
    NO_SHARED_ID,
    compare_strings,
    collection_title_variants,
    is_match,
    match_assets_to_media,
    match_tmdb_collection,
)


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
# match_tmdb_collection
# ---------------------------------------------------------------------------

class TestMatchTmdbCollection:
    def test_plex_plain_title_matches_tmdb_collection_suffix(self):
        # Plex names it "Star Wars"; TMDB always uses "X Collection".
        results = [{"id": 10, "name": "Star Wars Collection", "poster_path": "/a.jpg"}]
        match = match_tmdb_collection("Star Wars", results)
        assert match is not None
        assert match["id"] == 10

    def test_plex_collection_suffix_matches(self):
        results = [{"id": 404, "name": "John Wick Collection", "poster_path": "/jw.jpg"}]
        match = match_tmdb_collection("John Wick Collection", results)
        assert match is not None
        assert match["id"] == 404

    def test_picks_exact_match_among_several(self):
        results = [
            {"id": 1, "name": "The Avengers (1998) Collection"},
            {"id": 2, "name": "The Avengers Collection"},
        ]
        match = match_tmdb_collection("The Avengers", results)
        assert match is not None
        assert match["id"] == 2

    def test_no_match_returns_none(self):
        results = [{"id": 99, "name": "Star Wars Collection"}]
        assert match_tmdb_collection("My Favorite Movies", results) is None

    def test_empty_results_returns_none(self):
        assert match_tmdb_collection("Star Wars", []) is None

    def test_empty_title_returns_none(self):
        assert match_tmdb_collection("", [{"id": 1, "name": "Star Wars Collection"}]) is None

    def test_falls_back_to_original_name(self):
        results = [{"id": 7, "original_name": "James Bond Collection"}]
        match = match_tmdb_collection("James Bond", results)
        assert match is not None
        assert match["id"] == 7


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

    def test_series_matches_by_tmdb_id_ref(self):
        # Sonarr series carry tmdb on tmdb_id_ref (kept off search_matches). A
        # tmdb-tagged series poster with no tvdb should still match on it, even
        # when tvdb/imdb disagree.
        asset = {"title": "A", "type": "series", "tmdb_id": 302228, "normalized_title": "a"}
        media = {"title": "A", "type": "series", "tvdb_id": 479385,
                 "tmdb_id_ref": 302228, "imdb_id": "tt42028933", "normalized_title": "a"}
        matched, reason = is_match(asset, media)
        assert matched is True
        assert "tmdb_id" in reason

    def test_collection_does_not_match_by_tmdb_id_ref(self):
        # Collections also carry tmdb on tmdb_id_ref but must stay off the matcher,
        # so a same-numbered movie poster must not match a collection.
        asset = {"title": "A", "type": "movies", "tmdb_id": 302228, "normalized_title": "a"}
        media = {"title": "A", "type": "collections", "tvdb_id": 999,
                 "tmdb_id_ref": 302228, "normalized_title": "a"}
        matched, _ = is_match(asset, media)
        assert matched is False

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


class TestIsMatchUnknownYear:
    """*arr returns year 0 when a release year is unknown (unaired/unannounced).

    A 0 must be treated as "no year", not a literal year. The driving case: the
    renamer copies a poster into "Title"/poster.jpg, which re-scans with no year;
    that yearless asset must still match the year-0 media or Asset Cleanup orphans
    and deletes the folder every run while the renamer recreates it (rename loop).
    The folder year parsed from a stale/guessed *arr path must likewise not
    validate a fuzzy title match. ID matches are unaffected.
    """

    def test_zero_year_matches_rescanned_yearless_folder(self):
        # Regression: 'The Savant' has no year in Sonarr (year=0). The copied
        # poster.jpg/Season01.jpg folder re-scans with no year and no IDs; it must
        # still match the live show so cleanup keeps it instead of looping.
        rescanned = {"title": "The Savant", "normalized_title": "thesavant", "season_numbers": [1]}
        media = {
            "title": "The Savant",
            "normalized_title": "thesavant",
            "year": 0,
            "tvdb_id": 432966,
            "folder": "/tv/The Savant",
        }
        matched, _ = is_match(rescanned, media)
        assert matched is True

    def test_zero_year_does_not_match_via_stale_folder_year(self):
        asset = {"title": "Some Show", "normalized_title": "someshow", "year": 2019}
        media = {
            "title": "Some Show",
            "normalized_title": "someshow",
            "year": 0,
            "tvdb_id": 12345,
            "folder": "/tv/Some Show (2019)",
        }
        matched, _ = is_match(asset, media)
        assert matched is False

    def test_zero_year_still_matches_by_id(self):
        asset = {"title": "Some Show", "normalized_title": "someshow", "year": 2019, "tvdb_id": 12345}
        media = {
            "title": "Some Show",
            "normalized_title": "someshow",
            "year": 0,
            "tvdb_id": 12345,
            "folder": "/tv/Some Show (2019)",
        }
        matched, reason = is_match(asset, media)
        assert matched is True
        assert "tvdb_id" in reason

    def test_zero_year_matches_yearless_poster_by_title(self):
        # No year conflict on either side — an unambiguously-titled poster still resolves.
        asset = {"title": "Some Show", "normalized_title": "someshow"}
        media = {"title": "Some Show", "normalized_title": "someshow", "year": 0}
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


class TestIsMatchNormalizedFolder:
    """Fallback: asset normalized title matches the basename of media's on-disk folder."""

    def test_ampersand_title_matches_and_folder(self):
        # Radarr title uses "&" but the on-disk folder Radarr creates uses "and"
        # e.g. media title: "Cloak & Dagger", folder basename: "Cloak and Dagger (1984)"
        asset = {"title": "Cloak and Dagger", "normalized_title": "cloakanddagger", "year": 1984}
        media = {
            "title": "Cloak & Dagger",
            "normalized_title": "cloakanddagger",
            "folder": "/media/movies/Cloak and Dagger (1984)",
            "year": 1984,
        }
        matched, reason = is_match(asset, media)
        assert matched is True

    def test_normalized_folder_fallback_fires(self):
        # Asset title matches folder basename but not media title directly.
        # normalized_folder_title is only extracted when the folder contains "(YYYY)",
        # and year_matches() requires the asset year to match the folder year.
        asset = {"title": "Example Folder Title", "normalized_title": "examplefoldertitle", "year": 2020}
        media = {
            "title": "Example - Different Title",
            "normalized_title": "exampledifferenttitle",
            "folder": "/media/movies/Example Folder Title (2020)",
        }
        matched, reason = is_match(asset, media)
        assert matched is True
        assert "folder" in reason


# ---------------------------------------------------------------------------
# id-lock near miss (NO_SHARED_ID)
# ---------------------------------------------------------------------------

class TestNoSharedId:
    """The id lock skips the title fallback, so a title-agreeing pair with no shared id
    is reported rather than dropped silently."""

    def _series(self, **ids):
        base = {"type": "series", "title": "RIPLEY", "year": 2024,
                "normalized_title": "ripley", "tmdb_id": None, "tvdb_id": None, "imdb_id": None}
        return {**base, **ids}

    def test_imdb_only_poster_vs_tvdb_only_library_is_near_miss(self):
        asset = self._series(imdb_id="tt11016042", files=["/p/RIPLEY (2024) {imdb-tt11016042}.jpg"])
        media = self._series(tvdb_id=372727)
        matched, reason = is_match(asset, media)
        assert matched is False
        assert reason == NO_SHARED_ID

    def test_shared_imdb_matches(self):
        # His case: the folder imdb came from Sonarr, so it agrees and must not warn.
        asset = self._series(imdb_id="tt11016042")
        media = self._series(tvdb_id=372727, imdb_id="tt11016042")
        matched, reason = is_match(asset, media)
        assert matched is True
        assert reason == "by imdb_id"

    def test_conflicting_ids_are_not_near_miss(self):
        # Same title, genuinely different items — a shared id type that disagrees is a
        # real rejection, not a tagging gap.
        asset = self._series(tvdb_id=111)
        media = self._series(tvdb_id=222)
        matched, reason = is_match(asset, media)
        assert matched is False
        assert reason == ""

    def test_different_titles_are_not_near_miss(self):
        asset = self._series(title="Ripley Under Ground", normalized_title="ripleyunderground",
                             imdb_id="tt0219171")
        media = self._series(tvdb_id=372727)
        matched, reason = is_match(asset, media)
        assert matched is False
        assert reason == ""

    def _run_capturing_logs(self, asset, media):
        """Run a match, returning (result, logged_text). Logging is loguru, which neither
        propagates to caplog nor flushes through capfd, so attach a sink directly."""
        from loguru import logger

        index = create_new_empty_index()
        build_search_index(index, asset["title"], asset)

        messages: list = []
        sink_id = logger.add(messages.append, level="WARNING")
        try:
            result = match_assets_to_media({"series": [media]}, index)
        finally:
            logger.remove(sink_id)
        return result, "".join(messages)

    def test_near_miss_reported_and_item_unmatched(self):
        asset = self._series(imdb_id="tt11016042", files=["/p/RIPLEY (2024) {imdb-tt11016042}.jpg"])
        media = self._series(tvdb_id=372727)

        result, logged = self._run_capturing_logs(asset, media)

        assert result["series"] == []
        assert "share no id" in logged
        assert "{imdb-tt11016042}" in logged          # poster side
        assert "{tvdb-372727}" in logged              # library side
        assert "RIPLEY (2024) {imdb-tt11016042}.jpg" in logged

    def test_shared_imdb_produces_no_warning(self):
        # Regression: Sonarr-named "{imdb-...}" folders match and must stay silent.
        asset = self._series(imdb_id="tt11016042", files=["/p/RIPLEY (2024) {imdb-tt11016042}.jpg"])
        media = self._series(tvdb_id=372727, imdb_id="tt11016042")

        result, logged = self._run_capturing_logs(asset, media)

        assert len(result["series"]) == 1
        assert "share no id" not in logged
