"""Characterization tests for merge_assets — the cross-drive merge pre-pass.

Pins CURRENT behavior empirically (probed, not assumed) so the slot-keyed merge that lets
artwork ride the poster matcher can be proven equivalent for posters. merge_assets had no
direct coverage before this file; every other reference in the suite monkeypatches it.

Drive priority is positional: source_priority maps a source dir to its index, lower wins.
"""
from util.data.construct import create_movie, create_series
from util.posters.assets import merge_assets
from util.posters.index import create_new_empty_index

A, B = "/posters/driveA", "/posters/driveB"
SOURCE_PRIORITY = {A: 0, B: 1}


def _merge(*boxes):
    """Merge boxes in order into a fresh final list, as get_assets_files does per drive."""
    final, index = [], create_new_empty_index()
    for box in boxes:
        merge_assets([box], final, index, SOURCE_PRIORITY)
    return final


def _movie(drive, name="Dune", fname="Dune (2020).jpg", **ids):
    return create_movie(
        title=name, year=2020, tmdb_id=ids.get("tmdb"), imdb_id=ids.get("imdb"),
        normalized_title=name.lower().replace(" ", ""),
        files=[f"{drive}/{fname}"], parent_folder=f"{drive}/{name} (2020)",
    )


def _series(drive, fnames, name="Show", **ids):
    return create_series(
        title=name, year=2020, tmdb_id=ids.get("tmdb"), tvdb_id=ids.get("tvdb"),
        imdb_id=ids.get("imdb"), normalized_title=name.lower().replace(" ", ""),
        files=[f"{drive}/{f}" for f in fnames], parent_folder=f"{drive}/{name} (2020)",
    )


class TestFileDedup:
    """Files dedup by normalize_file_names, which strips the extension."""

    def test_identical_filename_keeps_higher_priority_drive(self):
        final = _merge(_movie(A, tmdb=1), _movie(B, tmdb=1))
        assert len(final) == 1
        assert final[0]["files"] == [f"{A}/Dune (2020).jpg"]

    def test_same_stem_different_extension_dedups(self):
        # Correct for posters (one poster per item). This same collapse is what makes a
        # filename-keyed merge unusable for artwork, whose slots share a stem.
        final = _merge(_movie(A, tmdb=1), _movie(B, fname="Dune (2020).png", tmdb=1))
        assert final[0]["files"] == [f"{A}/Dune (2020).jpg"]

    def test_unique_files_union_across_drives(self):
        final = _merge(
            _series(A, ["Show (2020).jpg", "Show (2020) - Season 01.jpg"], tvdb=99),
            _series(B, ["Show (2020).jpg", "Show (2020) - Season 02.jpg"], tvdb=99),
        )
        assert final[0]["files"] == [
            f"{A}/Show (2020) - Season 01.jpg",
            f"{A}/Show (2020).jpg",
            f"{B}/Show (2020) - Season 02.jpg",
        ]
        assert sorted(final[0]["season_numbers"]) == [1, 2]

    def test_files_sort_by_drive_priority_regardless_of_merge_order(self):
        # Lower-priority drive merged FIRST; the priority sort still puts A ahead of B.
        final = _merge(
            _series(B, ["Show (2020) - Season 02.jpg"], tvdb=99),
            _series(A, ["Show (2020) - Season 01.jpg"], tvdb=99),
        )
        assert final[0]["files"] == [
            f"{A}/Show (2020) - Season 01.jpg",
            f"{B}/Show (2020) - Season 02.jpg",
        ]


class TestMergeLogging:
    """A merge reports as a header (title/year/type/source + reason) plus ONE detail line for
    what changed. It used to be up to four records: the reason on its own line next to a
    "Files: N → N" counter that read "1 → 1" exactly when a duplicate was skipped, then the
    detail wrapped across two more."""

    def _merge_capturing(self, *boxes):
        from loguru import logger

        final, index = [], create_new_empty_index()
        messages: list = []
        sink_id = logger.add(messages.append, level="DEBUG")
        try:
            for box in boxes:
                merge_assets([box], final, index, SOURCE_PRIORITY)
        finally:
            logger.remove(sink_id)
        return [m for m in messages if "MERGE" in m]

    def test_skipped_duplicate_is_a_header_plus_one_detail_line(self):
        lines = self._merge_capturing(_movie(A, tmdb=1), _movie(B, tmdb=1))
        header = [m for m in lines if "Reason" in m]
        detail = [m for m in lines if "Kept" in m]
        assert len(header) == 1 and len(detail) == 1
        assert "Dune (2020) (movies) - merged poster from [driveB]: Reason by tmdb_id" in header[0]
        assert "Kept 1 higher priority file(s) from [driveA] over [driveB]: Dune (2020).jpg" in detail[0]

    def test_added_files_are_named_on_the_detail_line(self):
        # Only the distinguishing tail — the line already names the item, and drives repeat
        # its title/year/ids in every filename.
        lines = self._merge_capturing(
            _series(A, ["Show (2020).jpg"], tvdb=99),
            _series(B, ["Show (2020).jpg", "Show (2020) - Season 02.jpg"], tvdb=99),
        )
        detail = [m for m in lines if "Added" in m]
        assert len(detail) == 1
        assert "Added 1 file(s) from [driveB]: Season 02.jpg" in detail[0]

    def test_added_artwork_lists_slots_not_repeated_identities(self):
        # The real shape: three artwork files sharing a ~70-char stem produced a line that
        # wrapped four times. Only the suffix distinguishes them.
        from util.data.construct import build_slots
        stem = "English Teacher (2024) {tmdb-258902} {tvdb-421968} {imdb-tt20782190}"
        art = {
            "title": "English Teacher", "year": 2024, "tmdb_id": 258902, "tvdb_id": 421968,
            "imdb_id": "tt20782190", "type": None, "normalized_title": "englishteacher",
            "slots": build_slots(logo=f"{B}/logos/{stem} - logo.png",
                                 background=f"{B}/backgrounds/{stem} - background.jpg",
                                 square=f"{B}/squareart/{stem} - squareart.jpg"),
        }
        art["files"] = sorted(v for v in art["slots"].values() if isinstance(v, str))
        poster = _series(A, ["English Teacher (2024).jpg"], name="English Teacher", tvdb=421968)
        poster["normalized_title"] = "englishteacher"

        lines = self._merge_capturing(poster, art)
        detail = [m for m in lines if "Added" in m]
        assert len(detail) == 1
        assert "Added 3 file(s) from [driveB]: background.jpg, logo.png, squareart.jpg" in detail[0]
        assert stem not in detail[0], "must not restate the item identity per file"

    def test_a_merge_that_changed_nothing_is_a_single_line(self):
        # No detail line when there's nothing to report — the old "Files: 1 → 1" said nothing.
        lines = self._merge_capturing(_movie(A, tmdb=1), _movie(B, fname="Dune (2020).png", tmdb=1))
        assert len([m for m in lines if "Reason" in m]) == 1

    def test_artwork_callers_can_suppress_the_new_asset_line(self):
        # The artwork scan already logs each item with its slots and real drive name; the
        # merge's "new asset" line duplicated it 12,842 times in one workflow run.
        from loguru import logger

        final, index = [], create_new_empty_index()
        messages: list = []
        sink_id = logger.add(messages.append, level="DEBUG")
        try:
            merge_assets([_movie(A, tmdb=1)], final, index, SOURCE_PRIORITY, log_new=False)
        finally:
            logger.remove(sink_id)
        assert not [m for m in messages if "SCANNER" in m or "MERGE" in m]
        assert len(final) == 1, "suppressing the log must not skip the merge itself"


class TestArtworkJoinsAnyType:
    """A type-less box is artwork; a filename can't say movie vs series, so it must merge into
    whatever item it matched. The gate previously required equal types OR season_numbers, so
    only SERIES artwork ever joined its poster — movie and collection artwork was orphaned in
    the index and never placed."""

    def _artwork(self, title, year, **ids):
        from util.data.construct import build_slots
        stem = f"{title} ({year})"
        slots = build_slots(logo=f"{B}/logos/{stem} - logo.png",
                            background=f"{B}/backgrounds/{stem} - background.jpg")
        box = {"title": title, "year": year, "type": None,
               "normalized_title": title.lower().replace(" ", ""),
               "tmdb_id": ids.get("tmdb"), "tvdb_id": ids.get("tvdb"), "imdb_id": None,
               "slots": slots}
        box["files"] = sorted(v for v in slots.values() if isinstance(v, str))
        return box

    def test_movie_artwork_merges_into_its_poster(self):
        final = _merge(_movie(A, tmdb=1), self._artwork("Dune", 2020, tmdb=1))
        assert len(final) == 1, "movie artwork must join the poster box, not sit beside it"
        assert final[0]["slots"]["poster"] == f"{A}/Dune (2020).jpg"
        assert final[0]["slots"]["logo"] and final[0]["slots"]["background"]

    def test_collection_artwork_merges_into_its_poster(self):
        from util.data.construct import create_collection
        poster = create_collection(title="Marvel Collection", tmdb_id=None,
                                   normalized_title="marvelcollection",
                                   files=[f"{A}/Marvel Collection.jpg"],
                                   parent_folder=f"{A}/Marvel Collection")
        art = self._artwork("Marvel Collection", None)
        art["year"] = None
        final = _merge(poster, art)
        assert len(final) == 1
        assert final[0]["slots"]["logo"]

    def test_series_artwork_still_merges(self):
        final = _merge(
            _series(A, ["Show (2020).jpg", "Show (2020) - Season 01.jpg"], tvdb=99),
            self._artwork("Show", 2020, tvdb=99),
        )
        assert len(final) == 1
        assert final[0]["slots"]["logo"]
        assert sorted(final[0]["slots"]["seasons"]) == [1]


class TestMergeGating:
    """What blocks a merge — same directory, and the is_match id lock."""

    def test_same_directory_is_not_merged(self):
        # new_dirs & final_dirs short-circuits, so a re-scan of one dir stays separate.
        final = _merge(_movie(A, tmdb=1), _movie(A, tmdb=1))
        assert len(final) == 2

    def test_disjoint_id_types_do_not_merge(self):
        # A has tmdb only, B has tvdb only -> the id lock rejects (NO_SHARED_ID), so these
        # stay two boxes and the "Merge IDs" step never runs. Not a defect: it's the same
        # id-lock rule the matcher uses, surfaced here as an unmerged duplicate.
        final = _merge(_series(A, ["Show (2020).jpg"], tmdb=5),
                       _series(B, ["Show (2020).jpg"], tvdb=99))
        assert len(final) == 2
        assert (final[0]["tmdb_id"], final[0].get("tvdb_id")) == (5, None)
        assert (final[1]["tmdb_id"], final[1]["tvdb_id"]) == (None, 99)

    def test_shared_id_type_merges_and_fills_missing_ids(self):
        final = _merge(_series(A, ["Show (2020).jpg"], tvdb=99),
                       _series(B, ["Show (2020).jpg"], tvdb=99, imdb="tt1"))
        assert len(final) == 1
        assert final[0]["tvdb_id"] == 99
        assert final[0]["imdb_id"] == "tt1"


class TestSlotsMergePerSlot:
    """Slots merge per-slot by drive priority, so the box stays consistent with files.

    Before the slot-keyed merge these went stale: merge_assets touched files/season_numbers/
    ids but never `slots`, so the merged box kept whichever box landed in `final` first.
    """

    def test_merged_in_season_reaches_slots(self):
        final = _merge(
            _series(A, ["Show (2020).jpg", "Show (2020) - Season 01.jpg"], tvdb=99),
            _series(B, ["Show (2020).jpg", "Show (2020) - Season 02.jpg"], tvdb=99),
        )
        assert sorted(final[0]["season_numbers"]) == [1, 2]
        assert sorted(final[0]["slots"]["seasons"]) == [1, 2]
        assert final[0]["slots"]["seasons"][2] == f"{B}/Show (2020) - Season 02.jpg"

    def test_slots_follow_drive_priority_not_merge_order(self):
        final = _merge(
            _series(B, ["Show (2020) - Season 02.jpg"], tvdb=99),
            _series(A, ["Show (2020) - Season 01.jpg"], tvdb=99),
        )
        assert final[0]["files"][0].startswith(A)
        assert sorted(final[0]["slots"]["seasons"]) == [1, 2]

    def test_higher_priority_drive_wins_a_contested_slot(self):
        # Both drives supply the poster slot; A (rank 0) must keep it.
        final = _merge(_movie(A, tmdb=1), _movie(B, fname="Dune (2020).png", tmdb=1))
        assert final[0]["slots"]["poster"] == f"{A}/Dune (2020).jpg"

    def test_lower_priority_drive_fills_a_slot_the_winner_lacks(self):
        # The artwork rule, exercised on poster boxes: per-SLOT, not per-box.
        a = _movie(A, tmdb=1)
        a["slots"]["logo"] = None
        b = _movie(B, fname="Dune (2020).png", tmdb=1)
        b["slots"]["logo"] = f"{B}/logos/Dune (2020).png"
        final = _merge(a, b)
        assert final[0]["slots"]["poster"] == f"{A}/Dune (2020).jpg"
        assert final[0]["slots"]["logo"] == f"{B}/logos/Dune (2020).png"


class TestNoSpaceSeasonQuirk:
    """construct's season regex tolerates "Season01"; the RENAMER's placement regex does not.

    The quirk lives in placement (services/poster_renamer.py `r"Season (\\d+)"`), not here —
    so a merged box can carry a season that placement will skip.
    """

    def test_no_space_season_is_parsed_into_the_box(self):
        final = _merge(_series(A, ["Show (2020).jpg", "Show (2020) - Season01.jpg"], tvdb=99))
        assert final[0]["season_numbers"] == [1]
        assert final[0]["slots"]["seasons"][1] == f"{A}/Show (2020) - Season01.jpg"
