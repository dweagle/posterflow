"""Tests for services/match_report.py (single-item unmatched diagnosis)."""
from typing import Any, Dict, List, Optional, Tuple

import services.match_report as match_report
from services.match_report import (
    IDS_MISMATCH,
    _build_verdicts,
    _dedupe_titles,
    _record_matches_item,
    build_match_report,
    render_match_report_text,
    report_filename,
)
from util.posters.index import build_search_index, create_new_empty_index
from util.posters.match import ID_CONFLICT, NO_SHARED_ID, YEAR_MISMATCH


def _base_report(**overrides: Any) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "generated_at": "2026-08-05 12:00 UTC",
        "app_version": "0.0.test",
        "item": {"media_type": "series", "title": "RIPLEY", "year": 2024,
                 "tmdb_id": None, "tvdb_id": 372727, "imdb_id": None, "missing_seasons": []},
        "library": {
            "found": True,
            "records": [{"instance": "Sonarr", "title": "RIPLEY", "year": 2024, "folder": "RIPLEY (2024)",
                         "folder_has_year": True, "tmdb_id": None, "tvdb_id": 372727, "imdb_id": None,
                         "monitored": True, "status": "ended", "available": True,
                         "alternate_titles": [], "seasons_with_episodes": [1]}],
            "effective_ids": {"tmdb_id": None, "tvdb_id": 372727, "imdb_id": None},
            "ids_source": "Sonarr",
            "manual_entry": False,
            "on_ignore_list": False,
            "excluded_by": [],
        },
        "reference": {"tmdb": {"skipped": "no TMDB API key configured"},
                      "tvdb": {"skipped": "no TVDB API key configured"},
                      "plex": {"skipped": "no Plex instance configured"}},
        "drives": {"scanned": [{"name": "DriveA", "style_type": "CL2K", "local_path": "/d/a",
                                "last_synced": "2026-08-01T10:00:00+00:00", "missing": False}],
                   "total_assets": 100, "error": None},
        "candidates": {"considered": 3, "shown": 0, "omitted": 0, "id_pool": 0, "items": []},
    }
    report.update(overrides)
    return report


def _candidate(**overrides: Any) -> Dict[str, Any]:
    candidate = {"title": "RIPLEY", "year": 2024, "type": "series", "tmdb_id": None, "tvdb_id": 372727,
                 "imdb_id": None, "drive": "DriveA", "files": ["RIPLEY (2024) {tvdb-372727}.jpg"],
                 "season_numbers": [1], "has_main": True, "malformed_tags": [], "file_ids": None,
                 "found_by": "id", "matched": True, "reason": "by tvdb_id", "newest_file": None}
    candidate.update(overrides)
    return candidate


class TestRecordMatchesItem:
    def test_shared_id_matches(self):
        assert _record_matches_item({"tvdb_id": 5, "title": "X"}, {"tvdb_id": 5, "title": "Y"}) is True

    def test_disagreeing_id_rejects_despite_title(self):
        assert _record_matches_item({"tvdb_id": 5, "title": "X"}, {"tvdb_id": 6, "title": "X"}) is False

    def test_series_tmdb_ref_counts_as_tmdb(self):
        assert _record_matches_item({"tmdb_id_ref": 9, "title": "X"}, {"tmdb_id": 9, "title": "Y"}) is True

    def test_no_ids_falls_back_to_title_year(self):
        assert _record_matches_item({"title": "The Show", "year": 2020}, {"title": "The Show", "year": 2020}) is True
        assert _record_matches_item({"title": "The Show", "year": 2020}, {"title": "The Show", "year": 2021}) is False


class TestVerdicts:
    def test_matched_candidate_says_run_renamer(self):
        report = _base_report(candidates={"considered": 3, "shown": 1, "omitted": 0, "items": [_candidate()]})
        verdicts = _build_verdicts(report)
        assert verdicts[0]["code"] == "poster_available"
        assert verdicts[0]["level"] == "ok"

    def test_missing_seasons_not_on_drive(self):
        report = _base_report(candidates={"considered": 3, "shown": 1, "omitted": 0,
                                          "items": [_candidate(season_numbers=[1])]})
        report["item"]["missing_seasons"] = [2]
        verdicts = _build_verdicts(report)
        assert verdicts[0]["code"] == "seasons_not_on_drive"
        assert "Season 2" in verdicts[0]["message"]

    def test_missing_seasons_available_on_drive(self):
        report = _base_report(candidates={"considered": 3, "shown": 1, "omitted": 0,
                                          "items": [_candidate(season_numbers=[1, 2])]})
        report["item"]["missing_seasons"] = [2]
        assert _build_verdicts(report)[0]["code"] == "seasons_available"

    def test_id_conflict_names_both_sides(self):
        candidate = _candidate(matched=False, reason=ID_CONFLICT, tvdb_id=111)
        report = _base_report(candidates={"considered": 3, "shown": 1, "omitted": 0, "items": [candidate]})
        verdicts = _build_verdicts(report)
        assert verdicts[0]["code"] == "poster_id_conflict"
        assert "{tvdb-111}" in verdicts[0]["message"]
        assert "{tvdb-372727}" in verdicts[0]["message"]

    def test_no_shared_id_advice(self):
        candidate = _candidate(matched=False, reason=NO_SHARED_ID, tvdb_id=None, imdb_id="tt1")
        report = _base_report(candidates={"considered": 3, "shown": 1, "omitted": 0, "items": [candidate]})
        assert _build_verdicts(report)[0]["code"] == "no_shared_id"

    def test_year_mismatch_advice(self):
        candidate = _candidate(matched=False, reason=YEAR_MISMATCH)
        report = _base_report(candidates={"considered": 3, "shown": 1, "omitted": 0, "items": [candidate]})
        assert _build_verdicts(report)[0]["code"] == "year_mismatch"

    def test_year_mismatch_names_yearless_arr_folder(self):
        candidate = _candidate(matched=False, reason=YEAR_MISMATCH)
        report = _base_report(candidates={"considered": 3, "shown": 1, "omitted": 0, "items": [candidate]})
        report["library"]["records"][0]["folder"] = "RIPLEY"
        report["library"]["records"][0]["folder_has_year"] = False
        verdicts = _build_verdicts(report)
        assert verdicts[0]["code"] == "yearless_folder"
        assert "[Sonarr] RIPLEY" in verdicts[0]["message"]
        assert "year_mismatch" not in [v["code"] for v in verdicts]

    def test_no_candidates_reports_not_found(self):
        verdicts = _build_verdicts(_base_report())
        assert verdicts[0]["code"] == "no_poster_found"

    def test_no_candidates_in_artwork_mode_names_the_artwork(self):
        report = _base_report()
        report["item"]["artwork_type"] = "logo"
        report["drives"]["scanned"][0]["style_type"] = "ART"
        verdicts = _build_verdicts(report)
        assert verdicts[0]["code"] == "no_poster_found"
        assert verdicts[0]["message"].startswith("No logo for this title was found on the 1 subscribed artwork drive(s)")
        assert "poster" not in verdicts[0]["message"]
        report["verdicts"] = verdicts
        assert "no matching or near-miss logo found" in render_match_report_text(report)

    def test_no_candidates_notes_yearless_arr_folder(self):
        report = _base_report()
        report["library"]["records"][0]["folder"] = "RIPLEY"
        report["library"]["records"][0]["folder_has_year"] = False
        verdicts = _build_verdicts(report)
        assert verdicts[0]["code"] == "no_poster_found"
        note = next(v for v in verdicts if v["code"] == "yearless_folder")
        assert note["level"] == "info"
        assert "[Sonarr] RIPLEY" in note["message"]

    def test_not_in_sources_leads(self):
        report = _base_report()
        report["library"]["found"] = False
        report["library"]["records"] = []
        verdicts = _build_verdicts(report)
        assert verdicts[0]["code"] == "not_in_sources"

    def test_ignored_item_noted(self):
        report = _base_report()
        report["library"]["on_ignore_list"] = True
        assert any(v["code"] == "ignored" for v in _build_verdicts(report))

    def test_library_id_conflict_with_reference(self):
        report = _base_report()
        report["reference"]["tvdb"] = {"tvdb_id": 999999, "tmdb_id": None, "imdb_id": None,
                                       "title": "Other", "year": 2024}
        codes = [v["code"] for v in _build_verdicts(report)]
        assert "library_id_conflict" in codes

    def test_plex_id_conflict_reported(self):
        report = _base_report()
        report["reference"]["plex"] = {"tvdb_id": 999999, "tmdb_id": None, "imdb_id": None,
                                       "title": "RIPLEY", "year": 2024, "library": "TV Shows", "instance": "Plex"}
        verdicts = _build_verdicts(report)
        conflict = next(v for v in verdicts if v["code"] == "library_id_conflict")
        assert "the media server's metadata agent" in conflict["message"]

    def test_plex_missing_is_not_a_conflict(self):
        report = _base_report()
        report["reference"]["plex"] = {"missing": "not found in any Plex library"}
        codes = [v["code"] for v in _build_verdicts(report)]
        assert "library_id_conflict" not in codes and "plex_unresolved" not in codes

    def test_references_disagree_on_id_the_library_lacks(self):
        report = _base_report()
        report["reference"]["tvdb"] = {"tvdb_id": 372727, "tmdb_id": 123, "imdb_id": None,
                                       "title": "RIPLEY", "year": 2024}
        report["reference"]["tmdb"] = {"tvdb_id": 372727, "tmdb_id": 456, "imdb_id": None,
                                       "title": "Ripley", "year": 2024}
        report["reference"]["plex"] = {
            "instance": "Plex", "tvdb_id": 372727, "tmdb_id": 123, "imdb_id": None,
            "title": "RIPLEY", "year": 2024, "library": "TV Shows",
            "servers": [{"instance": "Plex", "tvdb_id": 372727, "tmdb_id": 123, "imdb_id": None,
                         "title": "RIPLEY", "year": 2024, "library": "TV Shows"},
                        {"instance": "Jellyfin", "missing": True}],
        }
        verdicts = _build_verdicts(report)
        verdict = next(v for v in verdicts if v["code"] == "sources_disagree_on_id")
        assert "TMDB id" in verdict["message"]
        assert "TVDB=123" in verdict["message"] and "TMDB=456" in verdict["message"] and "Plex=123" in verdict["message"]
        assert not any(v["code"] == "library_id_conflict" for v in verdicts)

    def test_library_held_id_stays_a_library_conflict(self):
        report = _base_report()
        report["reference"]["tmdb"] = {"tvdb_id": 111, "tmdb_id": 456, "imdb_id": None,
                                       "title": "Ripley", "year": 2024}
        report["reference"]["plex"] = {"instance": "Plex", "tvdb_id": 111, "tmdb_id": 456, "imdb_id": None,
                                       "title": "RIPLEY", "year": 2024, "library": "TV Shows"}
        codes = [v["code"] for v in _build_verdicts(report)]
        assert "library_id_conflict" in codes and "sources_disagree_on_id" not in codes

    def test_agreeing_references_are_quiet(self):
        report = _base_report()
        report["reference"]["tvdb"] = {"tvdb_id": 372727, "tmdb_id": 123, "imdb_id": "tt1", "title": "RIPLEY", "year": 2024}
        report["reference"]["tmdb"] = {"tvdb_id": 372727, "tmdb_id": 123, "imdb_id": "tt1", "title": "Ripley", "year": 2024}
        report["reference"]["plex"] = {"error": "connection failed"}
        codes = [v["code"] for v in _build_verdicts(report)]
        assert "sources_disagree_on_id" not in codes and "library_id_conflict" not in codes

    def test_malformed_tag_named(self):
        candidate = _candidate(matched=False, reason=YEAR_MISMATCH,
                               malformed_tags=["{tvdb-372727 }"])
        report = _base_report(candidates={"considered": 1, "shown": 1, "omitted": 0, "id_pool": 0,
                                          "items": [candidate]})
        verdicts = _build_verdicts(report)
        conflict = next(v for v in verdicts if v["code"] == "malformed_id_tag")
        assert "{tvdb-372727 }" in conflict["message"]

    def test_yearless_poster_gets_specific_advice(self):
        candidate = _candidate(matched=False, reason=YEAR_MISMATCH, year=None, type="collections",
                               tvdb_id=None)
        report = _base_report(candidates={"considered": 1, "shown": 1, "omitted": 0, "id_pool": 0,
                                          "items": [candidate]})
        codes = [v["code"] for v in _build_verdicts(report)]
        assert "yearless_poster" in codes and "year_mismatch" not in codes

    def test_tag_on_file_not_folder(self):
        candidate = _candidate(matched=False, reason=YEAR_MISMATCH, tvdb_id=None,
                               file_ids={"tmdb_id": None, "tvdb_id": 372727, "imdb_id": None})
        report = _base_report(candidates={"considered": 1, "shown": 1, "omitted": 0, "id_pool": 0,
                                          "items": [candidate]})
        verdicts = _build_verdicts(report)
        note = next(v for v in verdicts if v["code"] == "tag_on_file_not_folder")
        assert "{tvdb-372727}" in note["message"]

    def test_missing_main_not_on_drive(self):
        candidate = _candidate(has_main=False, files=["RIPLEY (2024) - Season 1.jpg"])
        report = _base_report(candidates={"considered": 1, "shown": 1, "omitted": 0, "id_pool": 1,
                                          "items": [candidate]})
        report["item"]["missing_main"] = True
        assert _build_verdicts(report)[0]["code"] == "main_not_on_drive"

    def test_title_match_with_id_pool_warns_pipeline_skip(self):
        candidate = _candidate(found_by="title")
        report = _base_report(candidates={"considered": 2, "shown": 1, "omitted": 0, "id_pool": 1,
                                          "items": [candidate]})
        codes = [v["code"] for v in _build_verdicts(report)]
        assert "poster_available" in codes and "pipeline_may_skip" in codes

    def test_instances_disagree(self):
        report = _base_report()
        second = dict(report["library"]["records"][0], instance="Sonarr 4K", tvdb_id=999999)
        report["library"]["records"].append(second)
        verdicts = _build_verdicts(report)
        conflict = next(v for v in verdicts if v["code"] == "instances_disagree")
        assert "Sonarr 4K" in conflict["message"]

    def test_excluded_by_filter_noted(self):
        report = _base_report()
        report["library"]["excluded_by"] = ["unmonitored filter (item is unmonitored)"]
        assert any(v["code"] == "excluded_by_filter" for v in _build_verdicts(report))

    def test_nonpriority_drive_hit(self):
        report = _base_report(nonpriority_hits=[{"title": "RIPLEY", "year": 2024, "drive": "OtherDrive",
                                                 "reason": "by tvdb_id", "files": ["RIPLEY (2024).jpg"]}])
        verdicts = _build_verdicts(report)
        hit = next(v for v in verdicts if v["code"] == "poster_on_nonpriority_drive")
        assert "OtherDrive" in hit["message"]

    def test_unscannable_file_named(self):
        report = _base_report(unscannable=[{"file": "RIPLEY (2024).webp", "drive_dir": "DriveA",
                                            "reason": "the item folder holds no scannable image type"}])
        verdicts = _build_verdicts(report)
        entry = next(v for v in verdicts if v["code"] == "poster_unscannable")
        assert "RIPLEY (2024).webp" in entry["message"]

    def test_close_title_hint(self):
        report = _base_report(close_titles=[{"title": "Rocky 2", "year": 1979, "similarity": 0.77,
                                             "files": ["Rocky 2 (1979).jpg"]}])
        codes = [v["code"] for v in _build_verdicts(report)]
        assert "close_title" in codes

    def test_collection_ids_ignored(self):
        report = _base_report(collection_id_note={"title": "Wrong Name Collection",
                                                  "files": ["Wrong Name Collection {tmdb-1}.jpg"]})
        report["item"]["media_type"] = "collections"
        verdicts = _build_verdicts(report)
        note = next(v for v in verdicts if v["code"] == "collection_ids_ignored")
        assert "TITLE only" in note["message"]

    def test_sources_disagree_on_year(self):
        report = _base_report()
        report["reference"]["tvdb"] = {"tvdb_id": 372727, "title": "RIPLEY", "year": 2023}
        codes = [v["code"] for v in _build_verdicts(report)]
        assert "sources_disagree_on_year" in codes

    def test_artwork_available(self):
        candidate = _candidate(artwork_types=["logo", "background"])
        report = _base_report(candidates={"considered": 1, "shown": 1, "omitted": 0, "id_pool": 1,
                                          "items": [candidate]})
        report["item"]["artwork_type"] = "logo"
        assert _build_verdicts(report)[0]["code"] == "artwork_available"

    def test_artwork_type_not_on_drive(self):
        candidate = _candidate(artwork_types=["background"])
        report = _base_report(candidates={"considered": 1, "shown": 1, "omitted": 0, "id_pool": 1,
                                          "items": [candidate]})
        report["item"]["artwork_type"] = "squareart"
        verdict = _build_verdicts(report)[0]
        assert verdict["code"] == "artwork_type_not_on_drive"
        assert "square art" in verdict["message"] and "background" in verdict["message"]

    def test_artwork_near_misses_still_apply(self):
        candidate = _candidate(matched=False, reason=ID_CONFLICT, tvdb_id=111, artwork_types=["logo"])
        report = _base_report(candidates={"considered": 1, "shown": 1, "omitted": 0, "id_pool": 0,
                                          "items": [candidate]})
        report["item"]["artwork_type"] = "logo"
        assert any(v["code"] == "poster_id_conflict" for v in _build_verdicts(report))

    def test_artwork_filename_carries_type(self):
        name = report_filename({"title": "Some Show", "artwork_type": "logo"})
        assert "_logo_" in name

    def test_artwork_end_to_end_scan_branch(self, test_db, monkeypatch):
        box = {"title": "Boxed Show", "year": 2020, "tvdb_id": 777, "tmdb_id": None, "imdb_id": None,
               "normalized_title": "boxedshow", "type": None,
               "slots": {"poster": None, "logo": "/a/d/logos/Boxed Show (2020) {tvdb-777}.png",
                         "background": None, "square": None, "seasons": {}},
               "files": ["/a/d/logos/Boxed Show (2020) {tvdb-777}.png"]}

        def fake_artwork_scan(db_):
            from util.posters.index import build_search_index, create_new_empty_index
            index = create_new_empty_index()
            build_search_index(index, box["title"], box)
            drives = [{"name": "ArtDrive", "style_type": "ART", "local_path": "/a/d",
                       "last_synced": None, "missing": False}]
            return drives, index, [box], None

        monkeypatch.setattr(match_report, "_fetch_library_records", lambda db_, item_: [])
        monkeypatch.setattr(match_report, "_scan_artwork_drives", fake_artwork_scan)
        called = {"poster_scan": False}
        monkeypatch.setattr(match_report, "_scan_source_drives",
                            lambda db_, **kw: called.update(poster_scan=True) or ([], None, [], None))

        item = {"media_type": "series", "title": "Boxed Show", "year": 2020, "tmdb_id": None,
                "tvdb_id": 777, "imdb_id": None, "missing_seasons": [], "artwork_type": "logo"}
        report = build_match_report(test_db, item)

        assert called["poster_scan"] is False          # artwork mode never scans poster drives
        assert report["candidates"]["items"][0]["matched"] is True
        assert report["candidates"]["items"][0]["artwork_types"] == ["logo"]
        assert any(v["code"] == "artwork_available" for v in report["verdicts"])
        # Poster-layout-specific sweeps stay off in artwork mode.
        assert report["unscannable"] == [] and report["nonpriority_hits"] == []

    def test_seasons_note_names_carried_seasons(self):
        report = _base_report(candidates={"considered": 1, "shown": 1, "omitted": 0, "id_pool": 1,
                                          "items": [_candidate(season_numbers=[2, 3])]})
        report["item"]["missing_seasons"] = [1]
        verdict = _build_verdicts(report)[0]
        assert verdict["code"] == "seasons_not_on_drive"
        assert "2, 3" in verdict["message"] and "numbering" in verdict["message"]

    def test_dead_reference_id_reported(self):
        report = _base_report()
        report["reference"]["tvdb"] = {"error": "TVDB has no series with id 372727 (deleted or merged entry)"}
        codes = [v["code"] for v in _build_verdicts(report)]
        assert "tvdb_unresolved" in codes

    def test_missing_drive_folder_reported(self):
        report = _base_report()
        report["drives"]["scanned"][0]["missing"] = True
        codes = [v["code"] for v in _build_verdicts(report)]
        assert "drive_folder_missing" in codes

    def test_unreleased_movie_noted(self):
        report = _base_report()
        report["item"]["media_type"] = "movies"
        report["library"]["records"][0]["status"] = "announced"
        verdicts = _build_verdicts(report)
        assert any(v["code"] == "not_released" for v in verdicts)
        # The release note sits above the no-poster conclusion so the headline reads in context.
        codes = [v["code"] for v in verdicts]
        assert codes.index("not_released") < codes.index("no_poster_found")

    def test_upcoming_series_noted(self):
        report = _base_report()
        report["library"]["records"][0]["status"] = "upcoming"
        assert any(v["code"] == "not_released" for v in _build_verdicts(report))

    def test_released_status_stays_silent(self):
        report = _base_report()
        report["item"]["media_type"] = "movies"
        report["library"]["records"][0]["status"] = "released"
        assert not any(v["code"] == "not_released" for v in _build_verdicts(report))


class TestIdsMismatchVerdict:
    def test_same_year_names_both_sides(self):
        candidate = _candidate(matched=False, reason=IDS_MISMATCH, tvdb_id=111, tmdb_id=222,
                               files=["RIPLEY (2024) {tvdb-111} {tmdb-222}.jpg"])
        report = _base_report(candidates={"considered": 1, "shown": 1, "omitted": 0, "id_pool": 0,
                                          "items": [candidate]})
        verdict = _build_verdicts(report)[0]
        assert verdict["code"] == "poster_ids_mismatch"
        assert verdict["level"] == "problem"
        assert "{tvdb-111}" in verdict["message"] and "{tvdb-372727}" in verdict["message"]
        assert "same title and year" in verdict["message"]

    def test_different_year_calls_it_another_release(self):
        candidate = _candidate(matched=False, reason=IDS_MISMATCH, tvdb_id=111, year=1999,
                               files=["RIPLEY (1999) {tvdb-111}.jpg"])
        report = _base_report(candidates={"considered": 1, "shown": 1, "omitted": 0, "id_pool": 0,
                                          "items": [candidate]})
        verdict = _build_verdicts(report)[0]
        assert verdict["code"] == "poster_ids_mismatch"
        assert verdict["level"] == "info"
        assert "(1999)" in verdict["message"] and "(2024)" in verdict["message"]
        assert "different release" in verdict["message"]

    def test_id_conflict_outranks_ids_mismatch(self):
        conflict = _candidate(matched=False, reason=ID_CONFLICT, tvdb_id=111)
        mismatch = _candidate(matched=False, reason=IDS_MISMATCH, tvdb_id=333, tmdb_id=444)
        report = _base_report(candidates={"considered": 2, "shown": 2, "omitted": 0, "id_pool": 0,
                                          "items": [conflict, mismatch]})
        assert _build_verdicts(report)[0]["code"] == "poster_id_conflict"


class TestRendering:
    def test_report_text_structure(self):
        report = _base_report(candidates={"considered": 3, "shown": 1, "omitted": 2, "items": [_candidate()]})
        report["verdicts"] = _build_verdicts(report)
        text = render_match_report_text(report)
        lines = text.splitlines()
        assert lines[0].startswith("Posterflow match report — v0.0.test")
        assert "RIPLEY (2024)" in lines[1]
        # Verdict section leads; evidence and the JSON appendix follow.
        assert text.index("VERDICT") < text.index("LIBRARY RECORD") < text.index("ID CROSS-CHECK")
        assert text.index("DRIVE CANDIDATES") < text.index("DRIVES SCANNED") < text.index("RAW DATA (JSON)")
        assert "[CL2K] DriveA — synced 2026-08-01 10:00 UTC" in text
        assert "2 more near-miss candidate(s) omitted" in text
        assert "```" not in text

    def test_cross_check_names_the_id_source(self):
        report = _base_report()
        report["verdicts"] = []
        text = render_match_report_text(report)
        cross_check = text.split("ID CROSS-CHECK")[1].split("DRIVE CANDIDATES")[0]
        assert "Sonarr" in cross_check.splitlines()[1]
        # No live record → the ids came from the cached unmatched row.
        report["library"]["ids_source"] = "unmatched cache"
        text = render_match_report_text(report)
        assert "unmatched cache" in text.split("ID CROSS-CHECK")[1].split("DRIVE CANDIDATES")[0]

    def test_cross_check_renders_plex_row(self):
        report = _base_report()
        report["verdicts"] = []
        report["reference"]["plex"] = {"tvdb_id": 372727, "tmdb_id": None, "imdb_id": None,
                                       "title": "RIPLEY", "year": 2024, "library": "TV Shows", "instance": "Plex"}
        cross_check = render_match_report_text(report).split("ID CROSS-CHECK")[1].split("DRIVE CANDIDATES")[0]
        assert "server" in cross_check
        assert "in TV Shows [Plex]" in cross_check
        # Reachable-but-absent renders as a plain note, not an error.
        report["reference"]["plex"] = {"missing": "not found in any Plex library"}
        cross_check = render_match_report_text(report).split("ID CROSS-CHECK")[1].split("DRIVE CANDIDATES")[0]
        assert "not found in any Plex library" in cross_check
        assert "✗ not found" not in cross_check

    def test_cross_check_renders_one_row_per_server(self):
        report = _base_report()
        report["verdicts"] = []
        report["reference"]["plex"] = {
            "tvdb_id": 372727, "tmdb_id": None, "imdb_id": None,
            "title": "RIPLEY", "year": 2024, "library": "TV Shows", "instance": "Plex 'main'",
            "servers": [
                {"instance": "Plex 'main'", "tvdb_id": 372727, "tmdb_id": None, "imdb_id": None,
                 "title": "RIPLEY", "year": 2024, "library": "TV Shows"},
                {"instance": "Jellyfin 'jelly'", "missing": True},
                {"instance": "Plex 'backup'", "error": "connection failed"},
            ],
        }
        cross_check = render_match_report_text(report).split("ID CROSS-CHECK")[1].split("DRIVE CANDIDATES")[0]
        assert "in TV Shows [Plex 'main']" in cross_check
        assert "not found on Jellyfin 'jelly'" in cross_check
        assert "✗ Plex 'backup': connection failed" in cross_check
        assert cross_check.count("server") == 3

    def test_alternate_titles_section(self):
        report = _base_report()
        report["verdicts"] = []
        report["library"]["records"][0]["alternate_titles"] = ["Undercover im Seniorenheim"]
        report["reference"]["tvdb"] = {"tvdb_id": 372727, "title": "RIPLEY", "year": 2024,
                                       "alternate_titles": ["Il talento di Mr. Ripley"], "alternate_titles_total": 12}
        text = render_match_report_text(report)
        section = text.split("ALTERNATE TITLES")[1].split("DRIVE CANDIDATES")[0]
        assert "Sonarr" in section and "Undercover im Seniorenheim" in section
        assert "Il talento di Mr. Ripley (+11 more)" in section
        # The library aliases line moved out of LIBRARY RECORD into this section.
        assert "aliases" not in text.split("LIBRARY RECORD")[1].split("ID CROSS-CHECK")[0]

    def test_no_alternate_titles_no_section(self):
        report = _base_report()
        report["verdicts"] = []
        assert "ALTERNATE TITLES" not in render_match_report_text(report)

    def test_dedupe_titles(self):
        names = _dedupe_titles(["The Show", "the show!", "El Show", None, "", "Primary"], exclude="Primary")
        assert names == ["The Show", "El Show"]

    def test_malformed_tags_helper(self):
        bad = match_report._malformed_tags([
            "Show (2020) {tvdb-12a34}.jpg",       # digits broken by a letter — unparseable
            "Show (2020) {imdb-123456}.jpg",      # imdb without tt — unparseable
            "Show (2020) {tvdb-123 }.jpg",        # stray space — the lenient parser still reads it
            "Show (2020) {tvdb-456}.jpg",         # valid — not flagged
            "Movie {edition-Extended}.jpg",       # not an id tag — ignored
        ])
        assert "{tvdb-12a34}" in bad and "{imdb-123456}" in bad
        assert len(bad) == 2

    def test_unscannable_near_files(self, tmp_path):
        # Nested drive: item folders exist, so loose root files + webp-only folders are invisible.
        drive = tmp_path / "drive"
        (drive / "Web Show (2020)").mkdir(parents=True)
        (drive / "Web Show (2020)" / "poster.webp").touch()
        (drive / "Root Show (2020).jpg").touch()
        found = match_report._unscannable_near_files(
            [str(drive)], ["Web Show (2020)", "Root Show (2020)"]
        )
        reasons = {entry["file"]: entry["reason"] for entry in found}
        assert "outside the item folders" in reasons["Root Show (2020).jpg"]
        assert "image types Posterflow doesn't read" in reasons["Web Show (2020)/poster.webp"]

    def test_close_titles_helper(self):
        pool = [({"title": "Rocky 2", "year": 1979, "normalized_title": "rocky2", "files": []}, "title"),
                ({"title": "Alien", "year": 1979, "normalized_title": "alien", "files": []}, "title")]
        close = match_report._close_titles(pool, "Rocky II")
        assert len(close) == 1 and close[0]["title"] == "Rocky 2"

    def test_collection_record_renders_without_arr_noise(self):
        # Plex collections have no year, no monitored flag, and their "folder" is not an
        # *arr path — none of that noise may render.
        report = _base_report()
        report["item"]["media_type"] = "collections"
        report["item"]["year"] = None
        report["library"]["records"] = [{
            "instance": "Plex (Movies)", "title": "Newly Released Movies", "year": None,
            "folder": None, "folder_has_year": False, "tmdb_id": None, "tvdb_id": None,
            "imdb_id": None, "monitored": None, "status": None, "available": None,
            "alternate_titles": [], "seasons_with_episodes": [],
        }]
        report["verdicts"] = []
        section = render_match_report_text(report).split("LIBRARY RECORD")[1].split("ID CROSS-CHECK")[0]
        assert "(None)" not in section
        assert "no (year) in path" not in section
        assert "monitored=None" not in section
        assert "state" not in section

    def test_renderer_shows_extras(self):
        report = _base_report(
            nonpriority_hits=[{"title": "RIPLEY", "year": 2024, "drive": "OtherDrive",
                               "reason": "by tvdb_id", "files": ["RIPLEY (2024).jpg"]}],
            unscannable=[{"file": "RIPLEY (2024).webp", "drive_dir": "DriveA", "reason": "no scannable image type"}],
            close_titles=[{"title": "Ripley 2", "year": 2026, "similarity": 0.8, "files": []}],
        )
        report["verdicts"] = _build_verdicts(report)
        text = render_match_report_text(report)
        assert "on NON-PRIORITY drive [OtherDrive]" in text
        assert "unscannable on [DriveA]: RIPLEY (2024).webp" in text
        assert "similar title: Ripley 2 (2026)" in text

    def test_tmdb_reference_leads_with_original_language_title(self, test_db, monkeypatch):
        """TMDB keeps original_title off /alternative_titles; the reference must add it."""
        from models.setting import upsert_setting
        upsert_setting(test_db, "tmdb_api_key", "test-key")
        test_db.flush()

        class FakeResponse:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return {
                    "id": 316328,
                    "name": "Our Man with the Enemy",
                    "original_name": "Onze Man bij de Vijand",
                    "first_air_date": "2026-02-01",
                    "external_ids": {"imdb_id": "tt41908914", "tvdb_id": 475672},
                    "alternative_titles": {"results": [{"title": "Some AKA"}]},
                }

        monkeypatch.setattr(match_report.http_requests, "get", lambda *a, **k: FakeResponse())
        resolved = match_report._tmdb_reference(
            test_db, {"media_type": "series", "tmdb_id": 316328, "tvdb_id": None, "imdb_id": None}
        )
        assert resolved["alternate_titles"][0] == "Onze Man bij de Vijand"
        assert "Some AKA" in resolved["alternate_titles"]
        assert resolved["tvdb_id"] == 475672

    def test_verdicts_wrap_one_sentence_per_line(self):
        report = _base_report()
        report["verdicts"] = [{"level": "problem", "code": "x",
                               "message": "First sentence of the verdict. Second sentence with more detail. "
                                          "A third extremely long sentence that goes on and on and on and on "
                                          "and on and keeps going well past any sane single line length limit."}]
        text = render_match_report_text(report)
        verdict_block = text.split("VERDICT")[1].split("LIBRARY RECORD")[0]
        verdict_lines = [l for l in verdict_block.splitlines() if l.strip()]
        # One sentence per line (third one wraps), nothing running off the page.
        assert len(verdict_lines) >= 3
        assert all(len(l) <= 100 for l in verdict_lines)
        assert verdict_lines[0].lstrip().startswith("✗ First sentence")
        assert verdict_lines[1].lstrip().startswith("Second sentence")

    def test_json_appendix_is_valid_and_omits_verdicts(self):
        import json as json_mod
        report = _base_report()
        report["verdicts"] = _build_verdicts(report)
        text = render_match_report_text(report)
        appendix = json_mod.loads(text.split("RAW DATA (JSON)\n", 1)[1])
        assert "verdicts" not in appendix
        assert appendix["item"]["title"] == "RIPLEY"

    def test_filename_slug(self):
        name = report_filename({"title": "Onze Man bij de Vijand: Deel 2!"})
        assert name.startswith("posterflow-match-report_onze-man-bij-de-vijand-deel-2_")
        assert name.endswith(".txt")


class TestBuildReport:
    def _fake_scan(self, asset: Dict[str, Any]):
        index = create_new_empty_index()
        build_search_index(index, str(asset.get("title", "")), asset)
        drives = [{"name": "DriveA", "style_type": "CL2K", "local_path": "/d/a",
                   "last_synced": "2026-08-01T10:00:00+00:00", "missing": False}]
        return drives, index, [asset], None

    def test_end_to_end_with_matching_poster(self, test_db, monkeypatch):
        record = {"type": "series", "instance": "Sonarr", "title": "RIPLEY", "year": 2024,
                  "folder": "/tv/RIPLEY (2024)", "tvdb_id": 372727, "tmdb_id_ref": None, "imdb_id": None,
                  "monitored": True, "normalized_title": "ripley", "alternate_titles": [],
                  "normalized_alternate_titles": [], "seasons": [{"season_number": 1, "season_has_episodes": True}]}
        asset = {"type": "series", "title": "RIPLEY", "year": 2024, "tvdb_id": 372727,
                 "normalized_title": "ripley", "files": ["/d/a/RIPLEY (2024) {tvdb-372727}.jpg"],
                 "season_numbers": []}
        monkeypatch.setattr(match_report, "_fetch_library_records", lambda db, item: [record])
        monkeypatch.setattr(match_report, "_scan_source_drives", lambda db, **kw: self._fake_scan(asset))

        item = {"media_type": "series", "title": "RIPLEY", "year": 2024,
                "tmdb_id": None, "tvdb_id": 372727, "imdb_id": None, "missing_seasons": []}
        report = build_match_report(test_db, item)

        assert report["library"]["found"] is True
        assert report["library"]["effective_ids"]["tvdb_id"] == 372727
        assert report["candidates"]["shown"] == 1
        assert report["candidates"]["items"][0]["matched"] is True
        assert report["candidates"]["items"][0]["drive"] == "DriveA"
        assert report["verdicts"][0]["code"] == "poster_available"
        # No API keys configured in the test DB — reference checks skip, not fail.
        assert "skipped" in report["reference"]["tmdb"]
        assert "skipped" in report["reference"]["tvdb"]

    def test_prefix_noise_is_filtered(self, test_db, monkeypatch):
        noise = {"type": "movies", "title": "RIPLEY UNDER GROUND", "year": 2005, "tmdb_id": 1,
                 "normalized_title": "ripleyunderground",
                 "files": ["/d/a/RIPLEY UNDER GROUND (2005) {tmdb-1}.jpg"], "season_numbers": []}
        monkeypatch.setattr(match_report, "_fetch_library_records", lambda db, item: [])
        monkeypatch.setattr(match_report, "_scan_source_drives", lambda db, **kw: self._fake_scan(noise))

        item = {"media_type": "series", "title": "RIPLEY", "year": 2024,
                "tmdb_id": None, "tvdb_id": 372727, "imdb_id": None, "missing_seasons": []}
        report = build_match_report(test_db, item)

        assert report["candidates"]["considered"] >= 1
        assert report["candidates"]["items"] == []
        codes = [v["code"] for v in report["verdicts"]]
        assert "not_in_sources" in codes and "no_poster_found" in codes


    def test_all_ids_disagree_is_reported_not_dropped(self, test_db, monkeypatch):
        record = {"type": "series", "instance": "Sonarr", "title": "RIPLEY", "year": 2024,
                  "folder": "/tv/RIPLEY (2024)", "tvdb_id": 372727, "tmdb_id_ref": 555, "imdb_id": None,
                  "monitored": True, "normalized_title": "ripley", "alternate_titles": [],
                  "normalized_alternate_titles": [], "seasons": [{"season_number": 1, "season_has_episodes": True}]}
        # Same title and year, but every shared id type disagrees: is_match rejects silently.
        asset = {"type": "series", "title": "RIPLEY", "year": 2024, "tvdb_id": 111, "tmdb_id": 222,
                 "normalized_title": "ripley", "files": ["/d/a/RIPLEY (2024) {tvdb-111} {tmdb-222}.jpg"],
                 "season_numbers": []}
        monkeypatch.setattr(match_report, "_fetch_library_records", lambda db, item: [record])
        monkeypatch.setattr(match_report, "_scan_source_drives", lambda db, **kw: self._fake_scan(asset))

        item = {"media_type": "series", "title": "RIPLEY", "year": 2024,
                "tmdb_id": 555, "tvdb_id": 372727, "imdb_id": None, "missing_seasons": []}
        report = build_match_report(test_db, item)

        shown = report["candidates"]["items"]
        assert len(shown) == 1 and shown[0]["matched"] is False
        assert shown[0]["reason"] == IDS_MISMATCH
        assert report["verdicts"][0]["code"] == "poster_ids_mismatch"
        assert "rejected: ids mismatch" in render_match_report_text(report)

    def test_agreeing_id_is_never_an_ids_mismatch(self, test_db, monkeypatch):
        record = {"type": "series", "instance": "Sonarr", "title": "RIPLEY", "year": 2024,
                  "folder": "/tv/RIPLEY (2024)", "tvdb_id": 372727, "tmdb_id_ref": 555, "imdb_id": None,
                  "monitored": True, "normalized_title": "ripley", "alternate_titles": [],
                  "normalized_alternate_titles": [], "seasons": [{"season_number": 1, "season_has_episodes": True}]}
        asset = {"type": "series", "title": "RIPLEY", "year": 2024, "tvdb_id": 372727, "tmdb_id": 222,
                 "normalized_title": "ripley", "files": ["/d/a/RIPLEY (2024) {tvdb-372727} {tmdb-222}.jpg"],
                 "season_numbers": []}
        monkeypatch.setattr(match_report, "_fetch_library_records", lambda db, item: [record])
        monkeypatch.setattr(match_report, "_scan_source_drives", lambda db, **kw: self._fake_scan(asset))

        item = {"media_type": "series", "title": "RIPLEY", "year": 2024,
                "tmdb_id": 555, "tvdb_id": 372727, "imdb_id": None, "missing_seasons": []}
        report = build_match_report(test_db, item)
        assert report["candidates"]["items"][0]["matched"] is True
        assert report["verdicts"][0]["code"] == "poster_available"


class TestEndpoint:
    def test_endpoint_queues_the_report_job(self, client, monkeypatch):
        """The build runs as a background job; the request only hands back the job id."""
        submitted = []
        monkeypatch.setattr("api.poster_manager.job_queue.submit", lambda fn, jid, *a, **k: submitted.append((jid, a)))

        resp = client.post("/api/posterflow/unmatched-match-report", json={
            "media_type": "series", "title": "RIPLEY", "year": 2024, "tvdb_id": 372727,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True and data["status"] == "pending"
        assert submitted == [(data["job_id"], (data["job_id"], {
            "media_type": "series", "title": "RIPLEY", "year": 2024, "tmdb_id": None, "tvdb_id": 372727,
            "imdb_id": None, "missing_seasons": None, "missing_main": False, "artwork_type": None,
        }))]

    def test_endpoint_rejects_bad_media_type(self, client):
        resp = client.post("/api/posterflow/unmatched-match-report", json={
            "media_type": "banana", "title": "X",
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Destination side: the placed folder
# ---------------------------------------------------------------------------

def _placed_item(**overrides: Any) -> Dict[str, Any]:
    item = {"media_type": "series", "title": "Wonder Man", "year": 2026, "tmdb_id": None,
            "tvdb_id": 428629, "imdb_id": None, "missing_seasons": [], "artwork_type": None}
    item.update(overrides)
    return item


def _write(folder, *names: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"x")


class TestPlacedFolder:
    def _report(self, tmp_path, monkeypatch, item):
        monkeypatch.setattr(match_report, "get_poster_destination", lambda db_: str(tmp_path))
        media = match_report._synth_media(item)
        ids = {k: item.get(k) for k in ("tmdb_id", "tvdb_id", "imdb_id")}
        return match_report._placed_folder_report(None, item, media, ids, [])

    def test_yearless_folder_is_read_as_a_collection(self, tmp_path, monkeypatch):
        _write(tmp_path / "Wonder Man {tvdb-428629}", "poster.jpg", "Season01.jpg")
        placed = self._report(tmp_path, monkeypatch, _placed_item())
        assert placed["name"] == "Wonder Man {tvdb-428629}" and placed["layout"] == "folders"
        assert placed["type"] == "collections" and placed["year"] is None
        assert placed["matched"] is False
        # A collection read keeps only the main poster: the season file is invisible to it.
        assert placed["files"] == ["poster.jpg"] and placed["has_main"] is True

    def test_stale_tag_names_the_conflict(self, tmp_path, monkeypatch):
        _write(tmp_path / "A Teacher (2020) {tvdb-352440}", "poster.jpg")
        item = _placed_item(title="A Teacher", year=2020, tvdb_id=473725)
        placed = self._report(tmp_path, monkeypatch, item)
        assert placed["name"] == "A Teacher (2020) {tvdb-352440}"
        assert placed["tvdb_id"] == 352440 and placed["id_conflicts"] == ["tvdb_id"]
        assert placed["matched"] is False and placed["reason"] == ID_CONFLICT

    def test_matching_folder_is_reported_matched(self, tmp_path, monkeypatch):
        _write(tmp_path / "Wonder Man (2026) {tvdb-428629}", "poster.jpg", "Season01.jpg")
        _write(tmp_path / "Wonder Woman (2017) {tmdb-297762}", "poster.jpg")
        placed = self._report(tmp_path, monkeypatch, _placed_item())
        assert placed["matched"] is True and placed["reason"] == "by tvdb_id"
        assert placed["season_numbers"] == [1] and placed["siblings"] == []

    def test_id_tagged_folder_outranks_a_title_only_twin(self, tmp_path, monkeypatch):
        _write(tmp_path / "Wonder Man (2026)", "poster.jpg")
        _write(tmp_path / "Wonder Man (2026) {tvdb-428629}", "poster.jpg")
        placed = self._report(tmp_path, monkeypatch, _placed_item())
        assert placed["name"] == "Wonder Man (2026) {tvdb-428629}"
        assert placed["siblings"] == ["Wonder Man (2026)"]

    def test_flat_destination_layout(self, tmp_path, monkeypatch):
        _write(tmp_path, "Wonder Man (2026) {tvdb-428629}.jpg", "Wonder Man (2026) {tvdb-428629} - Season 1.jpg",
               "Other Show (2001).jpg")
        placed = self._report(tmp_path, monkeypatch, _placed_item())
        assert placed["layout"] == "flat" and placed["matched"] is True
        assert placed["season_numbers"] == [1]

    def test_nothing_placed_returns_none(self, tmp_path, monkeypatch):
        _write(tmp_path / "Other Show (2001) {tvdb-1}", "poster.jpg")
        assert self._report(tmp_path, monkeypatch, _placed_item()) is None

    def test_missing_destination_returns_none(self, tmp_path, monkeypatch):
        assert self._report(tmp_path / "nope", monkeypatch, _placed_item()) is None

    def test_artwork_reports_skip_the_check(self, tmp_path, monkeypatch):
        _write(tmp_path / "Wonder Man (2026) {tvdb-428629}", "poster.jpg")
        assert self._report(tmp_path, monkeypatch, _placed_item(artwork_type="logo")) is None


class TestPlacedVerdicts:
    def _report_with(self, placed: Dict[str, Any], **overrides: Any) -> Dict[str, Any]:
        report = _base_report(**overrides)
        report["placed"] = placed
        return report

    def _placed(self, **overrides: Any) -> Dict[str, Any]:
        placed = {"layout": "folders", "name": "RIPLEY (2024) {tvdb-372727}", "files": ["poster.jpg"],
                  "year": 2024, "type": "series", "tmdb_id": None, "tvdb_id": 372727, "imdb_id": None,
                  "season_numbers": [], "has_main": True, "matched": True, "reason": "by tvdb_id",
                  "id_conflicts": [], "siblings": []}
        placed.update(overrides)
        return placed

    def test_yearless_placed_folder_leads_with_rename_advice(self):
        report = self._report_with(self._placed(name="RIPLEY {tvdb-372727}", year=None, type="collections",
                                                tvdb_id=None, matched=False, reason=""),
                                   candidates={"considered": 1, "shown": 1, "omitted": 0, "items": [_candidate()]})
        verdicts = _build_verdicts(report)
        assert verdicts[0]["code"] == "placed_yearless"
        assert "'RIPLEY (2024) {tvdb-372727}'" in verdicts[0]["message"]
        assert "Rename the folder in Sonarr" in verdicts[0]["message"]
        # The drive side no longer tells the user to run the renamer again.
        assert any(v["code"] == "poster_placed" for v in verdicts)
        assert not any(v["code"] == "poster_available" for v in verdicts)

    def test_stale_tag_leads_with_both_tags(self):
        report = self._report_with(self._placed(name="RIPLEY (2024) {tvdb-111}", tvdb_id=111, matched=False,
                                                reason=ID_CONFLICT, id_conflicts=["tvdb_id"]))
        verdicts = _build_verdicts(report)
        assert verdicts[0]["code"] == "placed_tag_conflict"
        assert "{tvdb-111}" in verdicts[0]["message"] and "{tvdb-372727}" in verdicts[0]["message"]

    def test_matching_placed_folder_calls_the_row_stale(self):
        verdicts = _build_verdicts(self._report_with(self._placed()))
        assert any(v["code"] == "placed_matches" and v["level"] == "info" for v in verdicts)

    def test_partial_row_lists_what_the_folder_holds(self):
        report = self._report_with(self._placed(files=["poster.jpg", "Season01.jpg"]))
        report["item"]["missing_seasons"] = [2]
        verdicts = _build_verdicts(report)
        note = next(v for v in verdicts if v["code"] == "placed_partial")
        assert "poster.jpg, Season01.jpg" in note["message"]

    def test_collections_skip_the_yearless_rule(self):
        report = self._report_with(self._placed(name="Bond Collection", year=None, type="collections", tvdb_id=None))
        report["item"]["media_type"] = "collections"
        assert not any(v["code"] == "placed_yearless" for v in _build_verdicts(report))

    def test_no_placed_key_keeps_old_behaviour(self):
        report = _base_report(candidates={"considered": 1, "shown": 1, "omitted": 0, "items": [_candidate()]})
        assert _build_verdicts(report)[0]["code"] == "poster_available"

    def test_text_renders_placed_section_between_library_and_ids(self):
        report = self._report_with(self._placed(name="RIPLEY {tvdb-372727}", year=None, type="collections",
                                                tvdb_id=None, matched=False, reason=""))
        report["verdicts"] = _build_verdicts(report)
        text = render_match_report_text(report)
        assert text.index("LIBRARY RECORD") < text.index("PLACED FOLDER") < text.index("ID CROSS-CHECK")
        assert "RIPLEY {tvdb-372727}   (folders layout)" in text
        assert "read as  collections, year none" in text
        assert "matcher  ✗ not matched" in text

    def test_text_skips_section_when_not_checked(self):
        text = render_match_report_text(_base_report(verdicts=[]))
        assert "PLACED FOLDER" not in text


class TestBuildPlumbing:
    def test_progress_ticks_cover_every_stage(self, test_db, monkeypatch):
        monkeypatch.setattr(match_report, "_fetch_library_records", lambda db_, item_: [])
        monkeypatch.setattr(match_report, "_placed_folder_report", lambda *a, **k: None)
        monkeypatch.setattr(match_report, "_scan_source_drives", lambda db_, **kw: ([], None, [], None))
        seen: List[Tuple[str, int]] = []
        item = {"media_type": "movies", "title": "Heat", "year": 1995, "tmdb_id": None, "tvdb_id": None,
                "imdb_id": None, "missing_seasons": []}
        build_match_report(test_db, item, progress=lambda m, p: seen.append((m, p)))
        assert seen[0][0] == "Fetching the library record"
        assert seen[-1][0] == "Building the verdict"
        percents = [p for _, p in seen]
        assert percents == sorted(percents) and percents[-1] < 100

    def test_drive_scan_forwards_per_drive_progress(self, test_db, monkeypatch):
        monkeypatch.setattr(match_report, "_fetch_library_records", lambda db_, item_: [])
        monkeypatch.setattr(match_report, "_placed_folder_report", lambda *a, **k: None)

        def fake_scan(db_, per_dir_callback=None):
            per_dir_callback(0, 2, "DriveA")
            per_dir_callback(1, 2, "DriveB")
            return [], None, [], None

        monkeypatch.setattr(match_report, "_scan_source_drives", fake_scan)
        seen: List[str] = []
        item = {"media_type": "movies", "title": "Heat", "year": 1995, "tmdb_id": None, "tvdb_id": None,
                "imdb_id": None, "missing_seasons": []}
        build_match_report(test_db, item, progress=lambda m, p: seen.append(m))
        assert "Scanning drive 1/2: DriveA" in seen and "Scanning drive 2/2: DriveB" in seen

    def test_library_fetch_asks_servers_for_the_items_type_only(self, test_db, monkeypatch):
        import json as _json
        from models.setting import Setting
        from services.poster_renamer import PosterRenameService
        import util.poster_settings as poster_settings

        test_db.add(Setting(key="plex_instances", value=_json.dumps([{"name": "Plex", "url": "http://p", "api_key": "k"}])))
        test_db.commit()
        monkeypatch.setattr(poster_settings, "media_server_media_source_enabled", lambda db_: True)
        captured: Dict[str, Any] = {}

        def fake_fetch(self, instance, media_dict, log_tag, selected_libraries=None, client=None, media_types=None):
            captured["media_types"] = media_types

        monkeypatch.setattr(PosterRenameService, "_fetch_media_server_media", fake_fetch)
        match_report._fetch_library_records(test_db, {"media_type": "series", "title": "X", "year": None,
                                                      "tmdb_id": None, "tvdb_id": None, "imdb_id": None})
        assert captured["media_types"] == ["series"]

    def test_server_probe_turns_a_failing_lookup_into_a_row(self, test_db, monkeypatch):
        class Library:
            key, type, title = "1", "show", "Series"

        class Client:
            def get_libraries(self):
                return [Library()]

            def find_by_provider_ids(self, *a, **k):
                raise RuntimeError("Jellyfin hiccup")

        import util.media_server.client as ms_client
        monkeypatch.setattr(match_report, "_plex_instances",
                            lambda db_: [{"name": "Jellyfin", "url": "http://j", "api_key": "k", "type": "jellyfin"}])
        monkeypatch.setattr(ms_client, "create_media_server_client", lambda *a, **k: Client())
        result = match_report._plex_reference(test_db, {"media_type": "series", "title": "X", "year": 2020},
                                              {"tmdb_id": None, "tvdb_id": 5, "imdb_id": None})
        assert result["servers"][0]["error"] == "Jellyfin hiccup"
        assert result["error"].startswith("Media server lookup failed")
