"""IDarr enrichment: TMDB search/retries, canonical titles, confidence flags,
external-id handling, and progress callbacks."""

import json
from datetime import datetime, timezone
from pathlib import Path

from models.idarr import IdarrAssetCache
from services.idarr_runner import IdarrRunner


def test_enrich_grouped_assets_reuses_single_tmdb_search(test_db, monkeypatch):
    runner = IdarrRunner(test_db)

    search_calls = 0
    external_calls = 0

    def fake_tmdb_search(*, api_key, title, asset_type, year, tmdb_id=None, tvdb_id=None, imdb_id=None):
        nonlocal search_calls
        search_calls += 1
        return {
            "id": 118357,
            "name": "1883",
            "first_air_date": "2021-12-19",
        }, "exact"

    def fake_tmdb_external_ids(*, api_key, tmdb_id, asset_type):
        nonlocal external_calls
        external_calls += 1
        return {
            "tvdb_id": 396390,
            "imdb_id": "tt13991232",
        }

    monkeypatch.setattr(IdarrRunner, "_tmdb_search", staticmethod(fake_tmdb_search))
    monkeypatch.setattr(IdarrRunner, "_tmdb_external_ids", staticmethod(fake_tmdb_external_ids))
    monkeypatch.setattr(
        IdarrRunner,
        "_tmdb_verify_id",
        staticmethod(
            lambda *, api_key, tmdb_id, asset_type, title, year=None: (
                {"id": tmdb_id, "name": title, "first_air_date": "2021-12-19"},
                "tv_series",
                None,
            )
        ),
    )

    assets = [
        {
            "file_path": Path("/tmp/1883 (2021) - Season 1.jpg"),
            "title": "1883",
            "year": 2021,
            "type": "tv_series",
            "tmdb_id": None,
            "tvdb_id": None,
            "imdb_id": None,
            "has_id": False,
        },
        {
            "file_path": Path("/tmp/1883 (2021) - Specials.jpg"),
            "title": "1883",
            "year": 2021,
            "type": "tv_series",
            "tmdb_id": None,
            "tvdb_id": None,
            "imdb_id": None,
            "has_id": False,
        },
    ]

    stats, _details = runner._enrich_assets_with_tmdb(
        assets,
        "fake-api-key",
        frequency_days=30,
        tvdb_frequency=7,
    )

    assert search_calls == 1
    assert external_calls == 1
    assert stats["tmdb_search_api_calls"] == 1
    assert stats["tmdb_external_id_api_calls"] == 1

    for asset in assets:
        assert asset["tmdb_id"] == 118357
        assert asset["tvdb_id"] == 396390
        assert asset["imdb_id"] == "tt13991232"
        assert asset["has_id"] is True


def test_idarr_runner_enrich_uses_cached_canonical_title_for_filename_generation_without_network_lookup(test_db, tmp_path, monkeypatch):
    runner = IdarrRunner(test_db)

    source_file = tmp_path / "Zack Snyder's Justice League Justice is Gray (2021).jpg"
    source_file.write_bytes(b"image")

    asset_key = runner._asset_key(
        asset_type="movie",
        title="Zack Snyder's Justice League Justice is Gray",
        year=2021,
    )

    cached_payload = {
        "status": "found",
        "canonical_title": "Zack Snyder's Justice League",
        "canonical_year": 2021,
        "current_filenames": [source_file.name],
        "original_filenames": [source_file.name],
    }
    test_db.add(
        IdarrAssetCache(
            asset_key=asset_key,
            title="Zack Snyder's Justice League Justice is Gray",
            year=2021,
            asset_type="movie",
            tmdb_id=791373,
            imdb_id="tt12361974",
            matched=True,
            payload_json=json.dumps(cached_payload),
            last_checked_at=datetime.now(timezone.utc),
        )
    )
    test_db.commit()

    def _fail_tmdb_search(*args, **kwargs):
        raise AssertionError("TMDB search should not run when cache is fresh")

    monkeypatch.setattr(IdarrRunner, "_tmdb_search", staticmethod(_fail_tmdb_search))

    assets = [
        {
            "file_path": source_file,
            "title": "Zack Snyder's Justice League Justice is Gray",
            "year": 2021,
            "type": "movie",
            "tmdb_id": None,
            "tvdb_id": None,
            "imdb_id": None,
            "has_id": False,
        }
    ]

    stats, _details = runner._enrich_assets_with_tmdb(
        assets,
        "fake-api-key",
        frequency_days=30,
        tvdb_frequency=7,
    )

    assert stats["tmdb_search_api_calls"] == 0
    assert assets[0]["tmdb_id"] == 791373
    assert assets[0]["imdb_id"] == "tt12361974"
    assert assets[0].get("new_title") == "Zack Snyder's Justice League"

    renamed = runner.generate_new_filename(assets[0], source_file.name)
    assert renamed.startswith("Zack Snyder's Justice League (2021)")
    assert "Justice is Gray" not in renamed


def test_idarr_runner_enrich_applies_cached_canonical_title_for_case_difference(test_db, tmp_path, monkeypatch):
    """Cached canonical title from TMDB wins over filename casing, including accent/case-only differences."""
    runner = IdarrRunner(test_db)

    source_file = tmp_path / "LEGO MARVEL Super Heroes Maximum Overload (2013).jpg"
    source_file.write_bytes(b"image")

    asset_key = runner._asset_key(
        asset_type="movie",
        title="LEGO MARVEL Super Heroes Maximum Overload",
        year=2013,
    )
    cached_payload = {
        "canonical_title": "LEGO Marvel Super Heroes Maximum Overload",
        "canonical_year": 2013,
        "current_filenames": [source_file.name],
        "original_filenames": [source_file.name],
    }
    test_db.add(
        IdarrAssetCache(
            asset_key=asset_key,
            title="LEGO MARVEL Super Heroes Maximum Overload",
            year=2013,
            asset_type="movie",
            tmdb_id=62576,
            imdb_id="tt3331092",
            matched=True,
            payload_json=json.dumps(cached_payload),
            last_checked_at=datetime.now(timezone.utc),
        )
    )
    test_db.commit()

    def _fail_tmdb_search(*args, **kwargs):
        raise AssertionError("TMDB search should not run when cache is fresh")

    monkeypatch.setattr(IdarrRunner, "_tmdb_search", staticmethod(_fail_tmdb_search))

    assets = [
        {
            "file_path": source_file,
            "title": "LEGO MARVEL Super Heroes Maximum Overload",
            "year": 2013,
            "type": "movie",
            "tmdb_id": 62576,
            "tvdb_id": None,
            "imdb_id": "tt3331092",
            "has_id": True,
        }
    ]

    _stats, _details = runner._enrich_assets_with_tmdb(
        assets,
        "fake-api-key",
        frequency_days=30,
        tvdb_frequency=7,
    )

    assert assets[0].get("new_title") == "LEGO Marvel Super Heroes Maximum Overload"
    renamed = runner.generate_new_filename(assets[0], source_file.name)
    assert renamed.startswith("LEGO Marvel Super Heroes Maximum Overload (2013)")


def test_idarr_runner_marks_low_confidence_alternate_as_review_required(test_db, tmp_path, monkeypatch):
    runner = IdarrRunner(test_db)

    source_file = tmp_path / "Zack Snyder's Justice League Justice is Gray (2021).jpg"
    source_file.write_bytes(b"image")

    def _fake_tmdb_search(*, api_key, title, asset_type, year, tmdb_id=None, tvdb_id=None, imdb_id=None):
        return {
            "id": 791373,
            "title": "Zack Snyder's Justice League",
            "release_date": "2021-03-18",
            "_idarr_match": {
                "confidence": "medium",
                "reason": "fuzzy_alternate",
                "score": 76,
                "candidate_title": "Zack Snyder's Justice League",
                "candidate_year": 2021,
            },
        }, "fuzzy_alternate"

    monkeypatch.setattr(IdarrRunner, "_tmdb_search", staticmethod(_fake_tmdb_search))
    monkeypatch.setattr(IdarrRunner, "_tmdb_external_ids", staticmethod(lambda **kwargs: {"imdb_id": "tt12361974", "tvdb_id": None}))

    assets = [
        {
            "file_path": source_file,
            "title": "Zack Snyder's Justice League Justice is Gray",
            "year": 2021,
            "type": "movie",
            "tmdb_id": None,
            "tvdb_id": None,
            "imdb_id": None,
            "has_id": False,
        }
    ]

    _stats, _details = runner._enrich_assets_with_tmdb(
        assets,
        "fake-api-key",
        frequency_days=30,
        tvdb_frequency=7,
    )

    assert assets[0]["tmdb_id"] == 791373
    assert assets[0]["imdb_id"] == "tt12361974"
    assert assets[0]["has_id"] is False
    assert assets[0].get("review_required") is True
    assert assets[0].get("pending_reason") == "low_confidence_alternate"
    assert assets[0].get("match_reason") == "low_confidence_alternate"


def test_idarr_runner_live_tmdb_title_always_wins_regardless_of_case(test_db, tmp_path, monkeypatch):
    """Live TMDB result is the canonical source — its title always wins even if the
    difference from the current asset title is casing only."""
    runner = IdarrRunner(test_db)

    source_file = tmp_path / "LEGO MARVEL Super Heroes Maximum Overload (2013).jpg"
    source_file.write_bytes(b"image")

    def _fake_tmdb_search(*, api_key, title, asset_type, year, tmdb_id=None, tvdb_id=None, imdb_id=None):
        return {
            "id": 249331,
            "title": "LEGO Marvel Super Heroes Maximum Overload",
            "release_date": "2013-01-01",
            "imdb_id": "tt3466264",
            "_idarr_match": {
                "confidence": "high",
                "reason": "exact",
                "score": 95,
                "candidate_title": "LEGO Marvel Super Heroes Maximum Overload",
                "candidate_year": 2013,
            },
        }, "exact"

    monkeypatch.setattr(IdarrRunner, "_tmdb_search", staticmethod(_fake_tmdb_search))
    monkeypatch.setattr(IdarrRunner, "_tmdb_external_ids", staticmethod(lambda **kwargs: {}))

    assets = [
        {
            "file_path": source_file,
            "title": "LEGO MARVEL Super Heroes Maximum Overload",
            "year": 2013,
            "type": "movie",
            "tmdb_id": None,
            "tvdb_id": None,
            "imdb_id": "tt3466264",
            "has_id": False,
        }
    ]

    _stats, _details = runner._enrich_assets_with_tmdb(
        assets,
        "fake-api-key",
        frequency_days=30,
        tvdb_frequency=7,
    )

    assert assets[0]["tmdb_id"] == 249331
    # TMDB is the canonical source; its title wins even for case-only differences.
    assert assets[0].get("new_title") == "LEGO Marvel Super Heroes Maximum Overload"


def test_idarr_runner_skips_external_ids_for_collections(test_db, tmp_path, monkeypatch):
    runner = IdarrRunner(test_db)

    source_file = tmp_path / "Troll Collection (2022) {tmdb-1180834}.jpg"
    source_file.write_bytes(b"image")

    external_calls = 0

    def _fake_tmdb_verify(*, api_key, tmdb_id, asset_type, title, year=None):
        return {
            "id": 1180834,
            "name": "Troll Collection",
        }, "collection", None

    def _fake_tmdb_external_ids(*, api_key, tmdb_id, asset_type):
        nonlocal external_calls
        external_calls += 1
        return {"imdb_id": "tt9999999", "tvdb_id": 123456}

    monkeypatch.setattr(IdarrRunner, "_tmdb_verify_id", staticmethod(_fake_tmdb_verify))
    monkeypatch.setattr(IdarrRunner, "_tmdb_external_ids", staticmethod(_fake_tmdb_external_ids))

    assets = [
        {
            "file_path": source_file,
            "title": "Troll Collection",
            "year": 2022,
            "type": "collection",
            "tmdb_id": 1180834,
            "tvdb_id": None,
            "imdb_id": None,
            "has_id": True,
        }
    ]

    stats, _details = runner._enrich_assets_with_tmdb(
        assets,
        "fake-api-key",
        frequency_days=30,
        tvdb_frequency=7,
    )

    assert external_calls == 0
    assert stats["tmdb_external_id_api_calls"] == 0
    assert assets[0].get("imdb_id") is None
    assert assets[0].get("tvdb_id") is None


def test_idarr_runner_corrects_wrong_imdb_tag_against_fresh_cache(test_db, tmp_path, monkeypatch):
    """A tv_series artwork file imported with a WRONG {imdb-...} tag self-heals: when the
    on-disk imdb disagrees with the authoritative (fresh) cache row, IDarr re-fetches
    external IDs by the known-good tmdb and OVERWRITES the wrong tag — even though the cache
    is fresh and a (wrong) imdb is already present. Verify/search must not run."""
    runner = IdarrRunner(test_db)

    key = runner._asset_key(asset_type="tv_series", title="Doctor Who", year=1963, tmdb_id=121)
    test_db.add(IdarrAssetCache(
        asset_key=key,
        title="Doctor Who",
        year=1963,
        asset_type="tv_series",
        tmdb_id=121,
        tvdb_id=76107,
        imdb_id="tt0056751",  # authoritative / correct
        matched=True,
        last_checked_at=datetime.now(timezone.utc),  # fresh → normally skips TMDB
    ))
    test_db.commit()

    external_calls = 0

    def _fake_external_ids(*, api_key, tmdb_id, asset_type):
        nonlocal external_calls
        external_calls += 1
        assert tmdb_id == 121  # fetched by the trusted tmdb, not a title/year re-search
        return {"imdb_id": "tt0056751", "tvdb_id": 76107}

    def _must_not_run(*args, **kwargs):
        raise AssertionError("verify/search must not run — correction goes through external_ids by tmdb")

    monkeypatch.setattr(IdarrRunner, "_tmdb_external_ids", staticmethod(_fake_external_ids))
    monkeypatch.setattr(IdarrRunner, "_tmdb_verify_id", staticmethod(_must_not_run))
    monkeypatch.setattr(IdarrRunner, "_tmdb_search", staticmethod(_must_not_run))

    art = tmp_path / "Doctor Who (1963) {tmdb-121} {tvdb-76107} {imdb-tt0167261} - background.jpg"
    art.write_bytes(b"img")

    assets = [{
        "file_path": art,
        "title": "Doctor Who",
        "year": 1963,
        "type": "tv_series",
        "tmdb_id": 121,
        "tvdb_id": 76107,
        "imdb_id": "tt0167261",  # WRONG (LOTR) baked into the filename
        "has_id": True,
    }]

    runner._enrich_assets_with_tmdb(assets, "fake-api-key", frequency_days=30, tvdb_frequency=7)

    assert external_calls == 1
    assert assets[0]["imdb_id"] == "tt0056751"  # corrected from the authoritative source


def test_idarr_runner_enrichment_emits_progress_callback_updates(test_db, tmp_path, monkeypatch):
    runner = IdarrRunner(test_db)

    def _fake_tmdb_verify(*, api_key, tmdb_id, asset_type, title, year=None):
        payload = {
            "id": tmdb_id,
            "title": title,
            "name": title,
        }
        return payload, asset_type, None

    monkeypatch.setattr(IdarrRunner, "_tmdb_verify_id", staticmethod(_fake_tmdb_verify))
    # Uncached-but-tagged items now get a one-time authoritative re-verify; keep it offline.
    monkeypatch.setattr(
        IdarrRunner,
        "_tmdb_external_ids",
        staticmethod(lambda *, api_key, tmdb_id, asset_type: {}),
    )

    assets = []
    for index in range(30):
        source_file = tmp_path / f"Progress Movie {index} (2020).jpg"
        source_file.write_bytes(b"image")
        assets.append(
            {
                "file_path": source_file,
                "title": f"Progress Movie {index}",
                "year": 2020,
                "type": "movie",
                "tmdb_id": 500000 + index,
                "tvdb_id": None,
                "imdb_id": f"tt{7000000 + index}",
                "has_id": True,
            }
        )

    callback_updates: list[tuple[int, int, str]] = []

    runner._enrich_assets_with_tmdb(
        assets,
        "fake-api-key",
        frequency_days=30,
        tvdb_frequency=7,
        progress_callback=lambda current, total, message: callback_updates.append((current, total, message)),
    )

    assert callback_updates
    assert callback_updates[-1][0] == len(assets)
    assert callback_updates[-1][1] == len(assets)
    assert any("Checking" in message for _, _, message in callback_updates)


def test_idarr_runner_does_not_mark_low_confidence_when_existing_ids_present(test_db, tmp_path, monkeypatch):
    runner = IdarrRunner(test_db)

    source_file = tmp_path / "MASH (1972) - Season 1.jpg"
    source_file.write_bytes(b"image")

    def _fake_tmdb_search(*, api_key, title, asset_type, year=None, tmdb_id=None, tvdb_id=None, imdb_id=None):
        return {
            "id": 918,
            "name": "M*A*S*H",
            "first_air_date": "1972-09-17",
        }, "alternate_title"

    def _fake_classify_search_match(*, candidate, title, year):
        return {
            "confidence": "low",
            "reason": "alternate_title",
            "score": 40,
        }

    monkeypatch.setattr(IdarrRunner, "_tmdb_search", staticmethod(_fake_tmdb_search))
    monkeypatch.setattr(IdarrRunner, "_classify_search_match", staticmethod(_fake_classify_search_match))

    assets = [
        {
            "file_path": source_file,
            "title": "MASH",
            "year": 1972,
            "type": "tv_series",
            "tmdb_id": None,
            "tvdb_id": 70994,
            "imdb_id": "tt0068098",
            "has_id": True,
        }
    ]

    _stats, _details = runner._enrich_assets_with_tmdb(
        assets,
        "fake-api-key",
        frequency_days=30,
        tvdb_frequency=7,
    )

    assert assets[0].get("review_required") is not True
    assert assets[0].get("pending_reason") != "low_confidence_alternate"
    assert assets[0].get("has_id") is True


def test_idarr_runner_rename_files_skips_review_required_low_confidence_alternate(test_db, tmp_path):
    runner = IdarrRunner(test_db)

    source_file = tmp_path / "Zack Snyder's Justice League Justice is Gray (2021).jpg"
    source_file.write_bytes(b"image")

    rename_results = runner.rename_files(
        assets=[
            {
                "file_path": source_file,
                "title": "Zack Snyder's Justice League Justice is Gray",
                "year": 2021,
                "type": "movie",
                "tmdb_id": 791373,
                "imdb_id": "tt12361974",
                "new_title": "Zack Snyder's Justice League",
                "new_year": 2021,
                "review_required": True,
            }
        ],
        dry_run=False,
    )

    assert rename_results["renamed_count"] == 0
    assert rename_results["skipped_count"] == 1
    assert rename_results["duplicate_conflicts"] == 0
    assert source_file.exists() is True
    assert any(
        str(row.get("reason") or "") == "review_required_low_confidence_alternate"
        for row in rename_results.get("operation_rows") or []
    )


def test_idarr_runner_tmdb_search_flags_ambiguous_when_multiple_exact_title_year_matches_exist(test_db, monkeypatch):
    runner = IdarrRunner(test_db)

    class _MockResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {"id": 101, "title": "The Office", "first_air_date": "2005-01-01"},
                    {"id": 102, "title": "The Office", "first_air_date": "2005-01-01"},
                ]
            }

    monkeypatch.setattr("services.idarr_runner.requests.get", lambda *args, **kwargs: _MockResponse())

    def _ranked(*, title, year, candidates, query_title):
        return [
            {
                "candidate": {"id": 101, "title": "The Office", "first_air_date": "2005-01-01"},
                "score": 82,
                "reason": "exact_title_year",
                "confidence": "high",
                "candidate_title": "The Office",
                "candidate_year": 2005,
                "ratio": 1.0,
            },
            {
                    "candidate": {"id": 102, "title": "The Office", "first_air_date": "2005-01-01"},
                "score": 80,
                "reason": "fuzzy_title_year",
                "confidence": "medium",
                    "candidate_title": "The Office",
                "candidate_year": 2005,
                "ratio": 0.94,
            },
        ]

    monkeypatch.setattr(IdarrRunner, "_rank_tmdb_candidates", staticmethod(_ranked))

    candidate, reason = runner._tmdb_search(
        api_key="fake-key",
        title="The Office",
        asset_type="tv_series",
        year=2005,
    )

    assert candidate is None
    assert reason == "ambiguous"


def test_idarr_runner_tmdb_search_retries_with_transformed_title(test_db, monkeypatch):
    runner = IdarrRunner(test_db)
    seen_queries: list[str] = []

    class _MockResponse:
        def __init__(self, query: str):
            self.query = query

        def raise_for_status(self):
            return None

        def json(self):
            if self.query == "Spider_Man":
                return {"results": []}
            if self.query == "Spider Man":
                return {
                    "results": [
                        {"id": 321, "title": "Spider Man", "release_date": "2002-05-01"}
                    ]
                }
            return {"results": []}

    def _mock_get(url, params=None, timeout=20):
        query = str((params or {}).get("query") or "")
        seen_queries.append(query)
        return _MockResponse(query)

    monkeypatch.setattr("services.idarr_runner.requests.get", _mock_get)

    candidate, reason = runner._tmdb_search(
        api_key="fake-key",
        title="Spider_Man",
        asset_type="movie",
        year=2002,
    )

    assert candidate is not None
    assert candidate.get("id") == 321
    assert reason in {"exact", "fuzzy_title_year", "fuzzy_title"}
    assert "Spider_Man" in seen_queries
    assert "Spider Man" in seen_queries


def test_idarr_runner_query_transformations_include_original_script_variants():
    variants = IdarrRunner._query_transformations("Spider-Man+No_Way_Home")

    assert "Spider-Man+No_Way_Home" in variants
    assert "Spider Man+No_Way_Home" in variants
    assert "Spider-Man+No Way Home" in variants
    assert "Spider:Man+No_Way_Home" in variants
    assert "Spider-Man+No:Way:Home" in variants
    assert "Spider\\Man+No_Way_Home" in variants
    assert "Spider-Man\\No_Way_Home" in variants
    assert "Spider-Man/No_Way_Home" in variants
