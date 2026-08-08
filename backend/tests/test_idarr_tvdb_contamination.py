"""Regression pack: the TVDB-contamination fix (movie alongside TV files must
never inherit the TV group's tvdb id at any stage)."""

from datetime import datetime, timezone

from models.idarr import IdarrAssetCache
from services.idarr_runner import IdarrRunner


# ---------------------------------------------------------------------------
# Regression tests for the TVDB-contamination fix (movie alongside TV files)
# ---------------------------------------------------------------------------


def test_generate_filename_tv_series_retains_tvdb_in_output(test_db):
    """TV show assets must still include {tvdb-...} in the generated filename."""
    runner = IdarrRunner(test_db)
    asset = {
        "title": "1883",
        "year": 2021,
        "type": "tv_series",
        "tmdb_id": 118357,
        "tvdb_id": 396390,
        "imdb_id": "tt13991232",
    }
    new_name = runner._generate_new_filename(asset, "1883 (2021).jpg")
    assert "{tmdb-118357}" in new_name, f"Expected tmdb tag in TV filename, got: {new_name!r}"
    assert "{tvdb-396390}" in new_name, f"Expected tvdb tag in TV filename, got: {new_name!r}"


def test_generate_filename_movie_type_never_includes_tvdb(test_db):
    """A movie asset with a tvdb_id (e.g. contaminated from a TV group) must
    NOT emit {tvdb-...} in the generated filename."""
    runner = IdarrRunner(test_db)
    asset = {
        "title": "LEGO Marvel Super Heroes Maximum Overload",
        "year": 2013,
        "type": "movie",
        "tmdb_id": 763861,
        "tvdb_id": 354864,  # incorrectly inherited from the TV show group
    }
    old_name = "LEGO Marvel Super Heroes Maximum Overload (2013) {tmdb-763861} {tvdb-354864}.jpg"
    new_name = runner._generate_new_filename(asset, old_name)
    assert "{tmdb-763861}" in new_name, f"Expected tmdb tag retained, got: {new_name!r}"
    assert "{tvdb-354864}" not in new_name, f"tvdb must be stripped from movie, got: {new_name!r}"


def test_scan_assets_movie_not_contaminated_by_tv_group_when_tmdb_differs(test_db, tmp_path):
    """When a movie poster and TV show posters with the same title/year share a folder,
    the movie must keep its own type and must not inherit the TV group's TVDB ID."""
    runner = IdarrRunner(test_db)

    # Movie poster — carries its own TMDB, no TVDB
    (tmp_path / "LEGO Marvel Super Heroes Maximum Overload (2013) {tmdb-763861}.jpg").write_bytes(b"img")
    # TV show base poster and season — carry different TMDB + TVDB
    (tmp_path / "LEGO Marvel Super Heroes Maximum Overload (2013) {tmdb-62576} {tvdb-354864}.jpg").write_bytes(b"img")
    (tmp_path / "LEGO Marvel Super Heroes Maximum Overload (2013) {tmdb-62576} {tvdb-354864} - Season 1.jpg").write_bytes(b"img")

    assets = runner._scan_assets(tmp_path)

    movie = next((a for a in assets if a.get("tmdb_id") == 763861), None)
    assert movie is not None, "Movie asset not found in scan results"
    assert movie["type"] == "movie", (
        f"Movie should retain 'movie' type; scan set it to {movie['type']!r}"
    )
    assert movie.get("tvdb_id") is None, (
        f"Movie must not inherit TVDB from TV group; got tvdb_id={movie.get('tvdb_id')}"
    )

    # TV show files must be unaffected
    tv_files = [a for a in assets if a.get("tmdb_id") == 62576]
    assert len(tv_files) == 2, f"Expected 2 TV assets, found {len(tv_files)}"
    assert all(a["type"] == "tv_series" for a in tv_files), "TV assets must keep tv_series type"
    assert all(a.get("tvdb_id") == 354864 for a in tv_files), "TV assets must keep their TVDB ID"


def test_enrich_movie_not_contaminated_by_tv_group_prefill_when_tmdb_differs(test_db, tmp_path, monkeypatch):
    """When a movie poster's TMDB ID differs from the TV group's resolved TMDB ID,
    the enrichment group-prefill must be skipped so the movie is not mis-typed as
    tv_series and does not receive the TV show's TVDB ID."""
    runner = IdarrRunner(test_db)

    def fake_verify(*, api_key, tmdb_id, asset_type, title, year=None):
        if tmdb_id == 62576:
            return {"id": 62576, "name": title, "first_air_date": "2013-01-01"}, "tv_series", None
        if tmdb_id == 763861:
            return {"id": 763861, "title": title, "release_date": "2013-01-01"}, "movie", None
        return None, None, "not_found"

    def fake_external_ids(*, api_key, tmdb_id, asset_type):
        if tmdb_id == 62576:
            return {"tvdb_id": 354864, "imdb_id": "tt3398228"}
        return {}

    monkeypatch.setattr(IdarrRunner, "_tmdb_verify_id", staticmethod(fake_verify))
    monkeypatch.setattr(IdarrRunner, "_tmdb_external_ids", staticmethod(fake_external_ids))

    # TV base poster (sorted alphabetically first because {tmdb-62576} < {tmdb-763861})
    tv_file = tmp_path / "LEGO Marvel Super Heroes Maximum Overload (2013) {tmdb-62576} {tvdb-354864}.jpg"
    tv_file.write_bytes(b"img")
    # Movie poster — type intentionally set to "tv_series" to simulate scan-phase contamination
    movie_file = tmp_path / "LEGO Marvel Super Heroes Maximum Overload (2013) {tmdb-763861}.jpg"
    movie_file.write_bytes(b"img")

    assets = [
        {
            "file_path": tv_file,
            "title": "LEGO Marvel Super Heroes Maximum Overload",
            "year": 2013,
            "type": "tv_series",
            "tmdb_id": 62576,
            "tvdb_id": 354864,
            "imdb_id": None,
            "has_id": True,
        },
        {
            # type deliberately set to tv_series to simulate scan contamination
            "file_path": movie_file,
            "title": "LEGO Marvel Super Heroes Maximum Overload",
            "year": 2013,
            "type": "tv_series",
            "tmdb_id": 763861,
            "tvdb_id": None,
            "imdb_id": None,
            "has_id": True,
        },
    ]

    runner._enrich_assets_with_tmdb(assets, "fake-api-key", frequency_days=30, tvdb_frequency=7)

    tv = next(a for a in assets if a.get("tmdb_id") == 62576)
    movie = next(a for a in assets if a.get("tmdb_id") == 763861)

    assert tv["type"] == "tv_series"
    assert tv.get("tvdb_id") == 354864

    assert movie["type"] == "movie", (
        f"Movie must be reclassified to 'movie' after TMDB verify; got {movie['type']!r}"
    )
    assert movie.get("tvdb_id") is None, (
        f"Movie must not receive TVDB from TV group prefill; got tvdb_id={movie.get('tvdb_id')}"
    )


def test_cache_prefill_does_not_restore_tvdb_for_movie_asset(test_db, tmp_path, monkeypatch):
    """When a movie asset has no tvdb_id but the cache row does, the cache pre-fill
    must NOT copy that tvdb_id onto the movie — movies have no TVDB IDs.
    This guards against the oscillation pattern: run-1 strips TVDB from the filename,
    _store_asset_cache_rows still has the stale tvdb in the row, and run-2 must not
    re-inject it via the cache pre-fill path."""
    runner = IdarrRunner(test_db)

    # Seed a fresh cache row for the movie with a stale tvdb_id (as would exist after
    # an earlier contaminated run stored it under the movie key).
    movie_key = runner._asset_key(
        asset_type="movie",
        title="LEGO Marvel Super Heroes Maximum Overload",
        year=2013,
        tmdb_id=763861,
    )
    stale_cache_row = IdarrAssetCache(
        asset_key=movie_key,
        title="LEGO Marvel Super Heroes Maximum Overload",
        year=2013,
        asset_type="movie",
        tmdb_id=763861,
        tvdb_id=354864,  # stale contamination from a previous tv_series mis-classification
        matched=True,
        last_checked_at=datetime.now(timezone.utc),  # fresh → verify will be skipped
    )
    test_db.add(stale_cache_row)
    test_db.commit()

    # TMDB should never be called because the cache is fresh — fail the test if it is.
    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("TMDB API must not be called when cache is fresh")

    monkeypatch.setattr(IdarrRunner, "_tmdb_verify_id", staticmethod(_should_not_be_called))
    monkeypatch.setattr(IdarrRunner, "_tmdb_search", staticmethod(_should_not_be_called))

    movie_file = tmp_path / "LEGO Marvel Super Heroes Maximum Overload (2013) {tmdb-763861}.jpg"
    movie_file.write_bytes(b"img")

    assets = [
        {
            "file_path": movie_file,
            "title": "LEGO Marvel Super Heroes Maximum Overload",
            "year": 2013,
            "type": "movie",
            "tmdb_id": 763861,
            "tvdb_id": None,  # no TVDB in filename on run-2
            "imdb_id": None,
            "has_id": True,
        }
    ]

    runner._enrich_assets_with_tmdb(assets, "fake-api-key", frequency_days=30, tvdb_frequency=7)

    movie = assets[0]
    assert movie.get("tvdb_id") is None, (
        f"Cache pre-fill must not restore tvdb_id for a movie asset; got tvdb_id={movie.get('tvdb_id')}"
    )


def test_store_cache_rows_does_not_persist_tvdb_for_movie_asset(test_db, tmp_path):
    """_store_asset_cache_rows must never write a tvdb_id into a cache row for a movie
    asset, even when the asset dict carries one (e.g. from _parse_asset parsing the
    original contaminated filename).  Persisting a tvdb onto a movie row would cause
    the cache pre-fill on subsequent runs to keep injecting it."""
    runner = IdarrRunner(test_db)

    movie_file = tmp_path / "LEGO Marvel Super Heroes Maximum Overload (2013) {tmdb-763861}.jpg"
    movie_file.write_bytes(b"img")

    assets = [
        {
            "file_path": movie_file,
            "title": "LEGO Marvel Super Heroes Maximum Overload",
            "year": 2013,
            "type": "movie",
            "tmdb_id": 763861,
            "tvdb_id": 354864,  # contaminated value — must NOT be stored for movie rows
            "imdb_id": None,
            "has_id": True,
            "_cache_touch": True,
        }
    ]

    runner._store_asset_cache_rows(assets)
    test_db.commit()

    expected_key = runner._asset_key(
        asset_type="movie",
        title="LEGO Marvel Super Heroes Maximum Overload",
        year=2013,
        tmdb_id=763861,
    )
    stored = test_db.query(IdarrAssetCache).filter(IdarrAssetCache.asset_key == expected_key).first()

    assert stored is not None, "Cache row must be created"
    assert stored.tvdb_id is None, (
        f"tvdb_id must not be stored for movie cache rows; got {stored.tvdb_id}"
    )
