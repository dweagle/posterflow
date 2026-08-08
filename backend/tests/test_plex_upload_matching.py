"""Asset discovery, selection and matching: no-id assets, index candidates, target
media type, stale-item guards, show folder keys, and unmatched-reason diagnosis."""

from models.setting import Setting
from services.plex_upload import AssetOutcome, PlexUploadService, format_unmatched_reasons
from plex_upload_fakes import _FakePlexItem


def test_service_selected_libraries_invalid_json_returns_configuration_error(test_db):
    service = PlexUploadService(test_db)

    test_db.add(
        Setting(
            key="plex_library_config",
            value="{invalid-json",
        )
    )
    test_db.commit()

    selected, error = service._get_selected_libraries([
        {"name": "Plex", "url": "http://localhost:32400", "api_key": "token"}
    ])

    assert selected == {}
    assert error == service.ERROR_INVALID_LIBRARY_CONFIG


def test_service_library_override_invalid_json_returns_error_tuple(test_db):
    service = PlexUploadService(test_db)

    test_db.add(
        Setting(
            key="plex_upload_library_override",
            value="{invalid-json",
        )
    )
    test_db.commit()

    enabled, configs, error = service._load_plex_upload_library_override()

    assert enabled is False
    assert configs == []
    assert error is not None
    assert "Invalid Plex Upload library override configuration" in error


def test_service_get_arr_instances_invalid_json_returns_empty_list(test_db):
    service = PlexUploadService(test_db)

    test_db.add(
        Setting(
            key=service.SETTING_RADARR_INSTANCES,
            value="{invalid-json",
        )
    )
    test_db.commit()

    instances = service._get_arr_instances(service.SETTING_RADARR_INSTANCES)
    assert instances == []


def test_plex_upload_ambiguous_no_id_asset_is_skipped(test_db, monkeypatch):
    """No-ID assets matching both movie and show sections should be skipped as ambiguous when no collection candidate exists."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "thefall",
        "path": "/tmp/posters/The_Fall/poster.jpg",
        "display_name": "The Fall",
        "asset_type": "main",
    }
    index = {
        "movies": {"thefall": [_FakePlexItem("movie", "The Fall", 2006, "Movies")]},
        "shows": {"thefall": [_FakePlexItem("show", "The Fall", 2013, "TV Shows")]},
        "collections": {"thefall": []},
    }

    info_messages: list[str] = []

    def _capture_info(_tag, message, **_context):
        info_messages.append(message)

    monkeypatch.setattr("services.plex_upload.log_info", _capture_info)

    outcome = service._upload_asset(
        asset,
        index,
        dry_run=True,
    )

    assert outcome.uploaded == 0
    assert outcome.matched is False
    assert outcome.skip_reason == "type_unresolved"
    assert outcome.plex_targets == 0
    assert outcome.media_counts == service._empty_media_upload_counts()
    assert any("Skipping ambiguous no-ID asset" in message for message in info_messages)


def test_plex_upload_no_id_asset_prefers_collection_when_type_unknown(test_db):
    """No-ID assets should prefer collections when no explicit type signal is available."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "alien",
        "path": "/tmp/posters/Alien/poster.jpg",
        "display_name": "Alien",
        "asset_type": "main",
    }
    index = {
        "movies": {"alien": [_FakePlexItem("movie", "Alien", 1979, "Movies")]},
        "shows": {"alien": []},
        "collections": {"alien": [_FakePlexItem("collection", "Alien Collection", None, "Movies")]},
    }

    outcome = service._upload_asset(
        asset,
        index,
        dry_run=True,
    )

    assert outcome.uploaded == 1
    assert outcome.matched is True
    assert outcome.plex_targets == 1
    assert outcome.media_counts["collections"] == 1
    assert outcome.media_counts["movies"] == 0


def test_plex_upload_no_id_asset_prefers_collection_over_arr_movie_hint(test_db):
    """No-ID assets with Plex collection candidates should resolve to collections, even if ARR has a movie match."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "alien",
        "path": "/tmp/posters/Alien/poster.jpg",
        "display_name": "Alien",
        "asset_type": "main",
    }
    index = {
        "movies": {"alien": [_FakePlexItem("movie", "Alien", 1979, "Movies")]},
        "shows": {"alien": []},
        "collections": {"alien": [_FakePlexItem("collection", "Alien Collection", None, "Movies")]},
    }
    arr_availability = {
        "movies": {"alien": {"has_file": True}},
        "shows": {},
    }

    outcome = service._upload_asset(
        asset,
        index,
        dry_run=True,
        arr_availability=arr_availability,
    )

    assert outcome.uploaded == 1
    assert outcome.matched is True
    assert outcome.plex_targets == 1
    assert outcome.media_counts["collections"] == 1
    assert outcome.media_counts["movies"] == 0


def test_plex_upload_discovery_excludes_tmp_assets_nested_anywhere(test_db, tmp_path):
    """Uploader discovery should ignore staged tmp files even when tmp is nested below destination root."""
    destination = tmp_path / "root"
    processed = destination / "assets" / "Zootopia (2016)" / "poster.jpg"
    staged = destination / "assets" / "tmp" / "Zootopia (2016)" / "poster.jpg"

    processed.parent.mkdir(parents=True, exist_ok=True)
    staged.parent.mkdir(parents=True, exist_ok=True)
    processed.write_bytes(b"processed")
    staged.write_bytes(b"staged")

    service = PlexUploadService(test_db)
    assets = service._discover_local_assets(destination)
    asset_paths = {str(asset.get("path")) for asset in assets}

    assert str(processed) in asset_paths
    assert str(staged) not in asset_paths


def test_plex_upload_no_id_asset_falls_back_to_collection_when_untyped_only(test_db):
    """No-ID assets should fallback to collections only when section-typed matches are absent."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "middleearth",
        "path": "/tmp/posters/Middle_Earth/poster.jpg",
        "display_name": "Middle Earth",
        "asset_type": "main",
    }
    index = {
        "movies": {"middleearth": []},
        "shows": {"middleearth": []},
        "collections": {"middleearth": [_FakePlexItem("collection", "Middle Earth", None, "Movies")]},
    }

    outcome = service._upload_asset(
        asset,
        index,
        dry_run=True,
    )

    assert outcome.uploaded == 1
    assert outcome.matched is True
    assert outcome.plex_targets == 1
    assert outcome.media_counts["collections"] == 1
    assert outcome.media_counts["movies"] == 0


def test_plex_upload_matches_show_by_tvdb_id_when_title_key_misses(test_db):
    """Uploader should match by tvdb ID key even when normalized title key differs."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "pluribus2025",
        "path": "/tmp/Pluribus (2025) {tvdb-436457}/poster.jpg",
        "display_name": "Pluribus (2025) {tvdb-436457}",
        "asset_type": "main",
    }
    index = {
        "movies": {},
        "shows": {
            "plur1bus2025": [_FakePlexItem("show", "Plur1bus", 2025, "4k TV Shows")],
            "id:tvdb:436457": [_FakePlexItem("show", "Plur1bus", 2025, "4k TV Shows")],
        },
        "collections": {},
    }

    outcome = service._upload_asset(
        asset,
        index,
        dry_run=True,
    )

    assert outcome.uploaded == 1
    assert outcome.matched is True
    assert outcome.plex_targets == 1
    assert outcome.media_counts["shows"] == 1


def test_discover_local_assets_sorted_show_then_seasons(test_db, tmp_path):
    """Discovery returns assets grouped by title, main poster before seasons, seasons ascending."""
    show_dir = tmp_path / "Chicago Fire (2012)"
    show_dir.mkdir(parents=True, exist_ok=True)
    # Create on disk in deliberately scrambled order.
    for name in ["Season03.jpg", "Season01.jpg", "poster.jpg", "Season14.jpg", "Season02.jpg"]:
        (show_dir / name).write_bytes(b"x")

    service = PlexUploadService(test_db)
    assets = service._discover_local_assets(tmp_path)

    order = [
        (a.get("asset_type"), a.get("season_number"))
        for a in assets
        if a.get("media_key") == "chicagofire"
    ]
    assert order == [
        ("main", None),
        ("season", 1),
        ("season", 2),
        ("season", 3),
        ("season", 14),
    ]


def test_missing_show_match_logged_once_per_run(test_db, monkeypatch):
    """When a show isn't in Plex yet, its season assets log the no-match line once, not per season."""
    messages = []
    monkeypatch.setattr("services.plex_upload.log_debug", lambda _tag, msg, **_kw: messages.append(msg))

    service = PlexUploadService(test_db)
    index = {"movies": {}, "shows": {}, "collections": {}}
    for season in (1, 2, 3):
        asset = {
            "media_key": "chicagofire",
            "display_name": "Chicago Fire",
            "asset_type": "season",
            "path": f"/tmp/Chicago Fire/Season{season:02d}.jpg",
            "season_number": season,
            "folder_year": 2012,
        }
        service._upload_asset(asset, index, dry_run=True, media_type_filter="series")

    missing_lines = [m for m in messages if "can't be applied yet" in m]
    assert len(missing_lines) == 1
    assert "Chicago Fire" in missing_lines[0]
    assert "no Plex show match" in missing_lines[0]  # no *arr index, so no availability claim


def test_no_match_log_level_depends_on_run_type(test_db, monkeypatch):
    """No-match lines are INFO for full runs (auditing) but DEBUG for single/webhook uploads."""
    info_msgs, debug_msgs = [], []
    monkeypatch.setattr("services.plex_upload.log_info", lambda _t, m, **_k: info_msgs.append(m))
    monkeypatch.setattr("services.plex_upload.log_debug", lambda _t, m, **_k: debug_msgs.append(m))

    service = PlexUploadService(test_db)
    asset = {
        "media_key": "nomatch",
        "path": "/tmp/No Match (2020)/poster.jpg",
        "display_name": "No Match",
        "asset_type": "main",
        "folder_year": 2020,
    }
    index = {"movies": {}, "shows": {}, "collections": {}}

    service._quiet_unmatched_logging = False  # full run
    service._upload_asset(asset, index, dry_run=True, media_type_filter="movie")
    assert any("No Plex match for asset" in m for m in info_msgs)
    assert not any("No Plex match for asset" in m for m in debug_msgs)

    info_msgs.clear()
    debug_msgs.clear()
    service._quiet_unmatched_logging = True  # single/webhook upload
    service._upload_asset(asset, index, dry_run=True, media_type_filter="movie")
    assert any("No Plex match for asset" in m for m in debug_msgs)
    assert not any("No Plex match for asset" in m for m in info_msgs)


def test_run_single_upload_series_season_only_processes_season_asset(test_db, monkeypatch):
    """Series single upload with season_number should only process season poster assets."""
    service = PlexUploadService(test_db)

    monkeypatch.setattr(service, "_get_destination_dir", lambda: "/tmp/organized")
    monkeypatch.setattr(service, "_get_plex_instances", lambda: [{"name": "Plex", "url": "http://plex", "api_key": "token"}])
    monkeypatch.setattr(service, "_get_selected_libraries", lambda instances: ({"Plex": [{"key": "1", "type": "show", "title": "TV"}]}, None))
    monkeypatch.setattr(
        service,
        "_build_plex_index",
        lambda instances, selected: (
            {"movies": {}, "shows": {}, "collections": {}},
            [{"instance": "Plex", "library": "TV", "section_type": "show", "items": 0, "collections": 0}],
        ),
    )
    monkeypatch.setattr(service, "_build_arr_availability_index", lambda *args, **kwargs: {})

    assets = [
        {"media_key": "theshow", "asset_type": "main", "path": "/tmp/organized/The Show/poster.jpg"},
        {"media_key": "theshow", "asset_type": "season", "season_number": 1, "path": "/tmp/organized/The Show/Season01.jpg"},
        {"media_key": "theshow", "asset_type": "season", "season_number": 2, "path": "/tmp/organized/The Show/Season02.jpg"},
    ]
    monkeypatch.setattr(service, "_discover_local_assets", lambda destination: assets)

    processed_paths = []

    def _fake_upload_asset(asset, index, dry_run, **kwargs):
        processed_paths.append(asset["path"])
        return AssetOutcome(1, True, 1, service._empty_media_upload_counts())

    monkeypatch.setattr(service, "_upload_asset", _fake_upload_asset)

    result = service.run_single_upload(
        media_type="series",
        title="The Show",
        year=None,
        season_number=2,
        dry_run=True,
    )

    assert result["success"] is True
    assert result["stats"]["scanned"] == 1
    assert processed_paths == ["/tmp/organized/The Show/Season02.jpg"]


def test_run_single_upload_prefers_id_matched_asset_over_title_overlap(test_db, monkeypatch):
    """Single upload should select ID-matched movie asset instead of title-overlap collection-style folder."""
    service = PlexUploadService(test_db)

    monkeypatch.setattr(service, "_get_destination_dir", lambda: "/tmp/organized")
    monkeypatch.setattr(service, "_get_plex_instances", lambda: [{"name": "Plex", "url": "http://plex", "api_key": "token"}])
    monkeypatch.setattr(service, "_get_selected_libraries", lambda instances: ({"Plex": [{"key": "1", "type": "movie", "title": "Movies"}]}, None))
    monkeypatch.setattr(
        service,
        "_build_plex_index",
        lambda instances, selected: (
            {"movies": {}, "shows": {}, "collections": {}},
            [{"instance": "Plex", "library": "Movies", "section_type": "movie", "items": 0, "collections": 0}],
        ),
    )
    monkeypatch.setattr(service, "_build_arr_availability_index", lambda *args, **kwargs: {})

    assets = [
        {
            "media_key": "zootopia",
            "asset_type": "main",
            "path": "/tmp/organized/Zootopia/poster.jpg",
        },
        {
            "media_key": "zootopia2016",
            "asset_type": "main",
            "path": "/tmp/organized/Zootopia (2016) {tmdb-269149} {imdb-tt2948356}/poster.jpg",
        },
    ]
    monkeypatch.setattr(service, "_discover_local_assets", lambda destination: assets)

    processed_paths = []

    def _fake_upload_asset(asset, index, dry_run, **kwargs):
        processed_paths.append(asset["path"])
        return AssetOutcome(1, True, 1, service._empty_media_upload_counts())

    monkeypatch.setattr(service, "_upload_asset", _fake_upload_asset)

    result = service.run_single_upload(
        media_type="movie",
        title="Zootopia",
        year=2016,
        tmdb_id=269149,
        imdb_id="tt2948356",
        dry_run=True,
    )

    assert result["success"] is True
    assert result["stats"]["scanned"] == 1
    assert processed_paths == ["/tmp/organized/Zootopia (2016) {tmdb-269149} {imdb-tt2948356}/poster.jpg"]


def test_select_local_assets_for_target_collection_suffix_stripped(test_db):
    """_select_local_assets_for_target should find a local asset named 'X' when
    the search title is 'X Collection' (source drives often append the suffix)."""
    service = PlexUploadService(test_db)

    # Destination has folder "Men in Black" (no suffix),
    # but user searched for "Men in Black Collection" (from source drive).
    asset = {
        "media_key": "meninblack",
        "path": "/assets/Men in Black/poster.jpg",
        "display_name": "Men in Black",
        "asset_type": "main",
    }

    result = service._select_local_assets_for_target(
        [asset],
        media_type="collection",
        title="Men in Black Collection",
        year=None,
    )

    assert len(result) == 1
    assert result[0]["media_key"] == "meninblack"


def test_select_local_assets_for_target_collection_no_suffix_also_added(test_db):
    """_select_local_assets_for_target should find a local asset named 'X Collection'
    when the search title is 'X' (reverse case: destination has suffix)."""
    service = PlexUploadService(test_db)

    asset = {
        "media_key": "meninblackcollection",
        "path": "/assets/Men in Black Collection/poster.jpg",
        "display_name": "Men in Black Collection",
        "asset_type": "main",
    }

    result = service._select_local_assets_for_target(
        [asset],
        media_type="collection",
        title="Men in Black",
        year=None,
    )

    assert len(result) == 1
    assert result[0]["media_key"] == "meninblackcollection"


def test_select_local_assets_for_target_id_match_ignores_year_mismatch(test_db):
    """An asset matched by a unique ID must not be discarded by the year filter when
    the on-disk folder year disagrees with the Plex-reported year (regression: folder
    'Michael (2025)' vs Plex year 2026 caused the webhook to skip the upload)."""
    service = PlexUploadService(test_db)

    # Destination folder named (2025) but Plex reports the movie as 2026.
    asset = {
        "media_key": "michael2025",
        "path": "/assets/Michael (2025) {tmdb-936075} {imdb-tt11378946}/poster.jpg",
        "display_name": "Michael",
        "asset_type": "main",
        "folder_year": 2025,
    }

    result = service._select_local_assets_for_target(
        [asset],
        media_type="movie",
        title="Michael",
        year=2026,
        tmdb_id=936075,
        imdb_id="tt11378946",
    )

    assert len(result) == 1
    assert result[0]["path"] == "/assets/Michael (2025) {tmdb-936075} {imdb-tt11378946}/poster.jpg"


def test_select_local_assets_for_target_title_match_still_year_filtered(test_db):
    """Without an ID match, the year filter must still reject a folder whose year
    differs from the target (e.g. 'Hairspray (1988)' should not satisfy a 2007 lookup)."""
    service = PlexUploadService(test_db)

    asset = {
        "media_key": "hairspray",
        "path": "/assets/Hairspray (1988)/poster.jpg",
        "display_name": "Hairspray",
        "asset_type": "main",
        "folder_year": 1988,
    }

    result = service._select_local_assets_for_target(
        [asset],
        media_type="movie",
        title="Hairspray",
        year=2007,
    )

    assert result == []


def test_resolve_index_candidates_id_match_trusts_id_over_year(test_db):
    """A unique ID match against a Plex item is authoritative: a folder year that
    disagrees with the Plex item's year (folder 'Michael (2025)' vs Plex year 2026)
    must NOT discard the ID match and force a retry."""
    service = PlexUploadService(test_db)

    plex_item = _FakePlexItem("movie", "Michael", year=2026, rating_key="m1")
    index_map = {"id:tmdb:936075": [plex_item], "id:imdb:tt11378946": [plex_item]}

    result = service._resolve_index_candidates(
        index_map,
        media_key="michael",
        asset_id_keys=["id:tmdb:936075", "id:imdb:tt11378946"],
        folder_year=2025,
    )

    assert len(result) == 1
    assert result[0] is plex_item


def test_resolve_index_candidates_id_match_prefers_year_when_multiple(test_db):
    """When a single ID key maps to multiple Plex items, the folder year is still
    used to narrow to the year-correct one (year as tie-breaker, not a reject)."""
    service = PlexUploadService(test_db)

    item_2025 = _FakePlexItem("movie", "Michael", year=2025, rating_key="m1")
    item_2026 = _FakePlexItem("movie", "Michael", year=2026, rating_key="m2")
    index_map = {"id:tmdb:936075": [item_2025, item_2026]}

    result = service._resolve_index_candidates(
        index_map,
        media_key="michael",
        asset_id_keys=["id:tmdb:936075"],
        folder_year=2025,
    )

    assert result == [item_2025]


def test_note_year_discrepancy_records_id_match_with_year_mismatch(test_db):
    """An ID-matched upload whose folder year disagrees with the Plex item year should be
    recorded (for log/Discord surfacing) without blocking the upload."""
    service = PlexUploadService(test_db)
    service._year_discrepancies = []

    asset = {
        "media_key": "michael",
        "display_name": "Michael (2025)",
        "path": "/assets/Michael (2025) {tmdb-936075} {imdb-tt11378946}/poster.jpg",
        "asset_type": "main",
        "folder_year": 2025,
    }
    matched_items = [_FakePlexItem("movie", "Michael", year=2026, rating_key="m1")]

    service._note_year_discrepancy(asset, matched_items, ["id:tmdb:936075"], asset["path"])

    assert service._year_discrepancies == [
        {"title": "Michael (2025)", "folder_year": 2025, "plex_year": 2026}
    ]

    # Idempotent: re-recording the same discrepancy does not duplicate it.
    service._note_year_discrepancy(asset, matched_items, ["id:tmdb:936075"], asset["path"])
    assert len(service._year_discrepancies) == 1


def test_note_year_discrepancy_silent_when_years_match(test_db):
    """No discrepancy is recorded when the folder year matches the Plex item year."""
    service = PlexUploadService(test_db)
    service._year_discrepancies = []

    asset = {"media_key": "michael", "folder_year": 2026, "asset_type": "main"}
    matched_items = [_FakePlexItem("movie", "Michael", year=2026)]

    service._note_year_discrepancy(asset, matched_items, ["id:tmdb:936075"], "/p")

    assert service._year_discrepancies == []


def test_note_year_discrepancy_skipped_for_title_only_match(test_db):
    """Without an ID match (title-only), a year mismatch should not be flagged as an ID
    discrepancy — title matches are already year-filtered upstream."""
    service = PlexUploadService(test_db)
    service._year_discrepancies = []

    asset = {"media_key": "michael", "folder_year": 2025, "asset_type": "main"}
    matched_items = [_FakePlexItem("movie", "Michael", year=2026)]

    service._note_year_discrepancy(asset, matched_items, [], "/p")

    assert service._year_discrepancies == []


def test_format_year_discrepancy_text_empty_single_and_multiple():
    """The shared Discord/log formatter handles none, one, and many discrepancies."""
    import modules.upload as upload_module

    assert upload_module._format_year_discrepancy_text([]) == ""

    single = upload_module._format_year_discrepancy_text(
        [{"title": "Michael (2025)", "folder_year": 2025, "plex_year": 2026}]
    )
    assert "Michael (2025)" in single
    assert "Plex year 2026" in single
    assert "folder year 2025" in single

    many = upload_module._format_year_discrepancy_text(
        [
            {"title": "A", "folder_year": 2001, "plex_year": 2002},
            {"title": "B", "folder_year": 2003, "plex_year": 2004},
            {"title": "C", "folder_year": 2005, "plex_year": 2006},
            {"title": "D", "folder_year": 2007, "plex_year": 2008},
        ]
    )
    assert many.startswith("4 item(s)")
    assert "A, B, C" in many
    assert "(+1 more)" in many


# ---------------------------------------------------------------------------
# _resolve_target_media_type  — folder_year disambiguation
# ---------------------------------------------------------------------------


def _fake_movie(rating_key="m1"):
    return _FakePlexItem("movie", "300", year=2007, rating_key=rating_key)


def _fake_collection(rating_key="c1"):
    return _FakePlexItem("collection", "300", rating_key=rating_key)


def test_resolve_target_media_type_year_bearing_asset_not_redirected_to_collection(test_db):
    """An asset folder WITH a year (movie convention) must not be sent to a
    same-named collection even when both a movie and a collection exist in Plex."""
    service = PlexUploadService(test_db)

    asset = {
        "media_key": "300",
        "path": "/assets/300 (2007) {imdb-tt0416449} {tmdb-1271}/poster.jpg",
        "display_name": "300 (2007)",
        "asset_type": "main",
        "folder_year": 2007,
    }

    result, reason = service._resolve_target_media_type(
        asset,
        media_type_filter=None,
        arr_availability=None,
        movies_raw=[_fake_movie()],
        shows_raw=[],
        collections_raw=[_fake_collection()],
    )

    assert result == "movie", f"expected 'movie' but got {result!r} (reason={reason})"
    assert reason is None


def test_resolve_target_media_type_no_year_asset_goes_to_collection(test_db):
    """An asset folder WITHOUT a year (collection convention) should still be
    directed to the Plex collection when one exists for the same title."""
    service = PlexUploadService(test_db)

    asset = {
        "media_key": "300",
        "path": "/assets/300/poster.jpg",
        "display_name": "300",
        "asset_type": "main",
        "folder_year": None,
    }

    result, reason = service._resolve_target_media_type(
        asset,
        media_type_filter=None,
        arr_availability=None,
        movies_raw=[_fake_movie()],
        shows_raw=[],
        collections_raw=[_fake_collection()],
    )

    assert result == "collection", f"expected 'collection' but got {result!r} (reason={reason})"
    assert reason is None


def test_resolve_target_media_type_explicit_movie_filter_not_redirected(test_db):
    """When media_type_filter='movie' is set explicitly, the result must be
    'movie' even if a collection with that title also exists in Plex."""
    service = PlexUploadService(test_db)

    asset = {
        "media_key": "300",
        "path": "/assets/300 (2007) {tmdb-1271}/poster.jpg",
        "display_name": "300",
        "asset_type": "main",
        "folder_year": 2007,
    }

    result, reason = service._resolve_target_media_type(
        asset,
        media_type_filter="movie",
        arr_availability=None,
        movies_raw=[_fake_movie()],
        shows_raw=[],
        collections_raw=[_fake_collection()],
    )

    assert result == "movie", f"expected 'movie' but got {result!r} (reason={reason})"
    assert reason is None


def test_upload_asset_season_missing_in_plex_returns_seasons_missing_flag(test_db):
    """When the show exists in Plex but the season hasn't scanned yet,
    _upload_asset should return seasons_missing=1 so the retry loop knows
    to wait rather than treating it as already up-to-date."""
    service = PlexUploadService(test_db)

    class _ShowWithNoSeasons(_FakePlexItem):
        def seasons(self):
            return []  # show present, season not yet scanned

    asset = {
        "media_key": "legendofkorra2012",
        "asset_type": "season",
        "season_number": 1,
        "path": "/tmp/organized/The Legend of Korra (2012)/Season01.jpg",
        "display_name": "The Legend of Korra (2012)",
    }
    index = {
        "movies": {},
        "shows": {"legendofkorra2012": [_ShowWithNoSeasons("show", "The Legend of Korra", 2012, "TV Shows")]},
        "collections": {},
    }

    outcome = service._upload_asset(
        asset,
        index,
        dry_run=False,
    )

    assert outcome.uploaded == 0
    assert outcome.matched is True  # show was found in Plex
    assert outcome.seasons_missing == 1  # season not yet scanned — should trigger retry


def test_upload_asset_season_present_in_plex_returns_zero_seasons_missing(test_db):
    """When the show and season both exist in Plex, seasons_missing should be 0."""
    service = PlexUploadService(test_db)

    class _FakeSeason:
        def __init__(self, index):
            self.index = index

        def uploadPoster(self, filepath):
            pass

    class _ShowWithSeason(_FakePlexItem):
        def seasons(self):
            return [_FakeSeason(1)]

    asset = {
        "media_key": "legendofkorra2012",
        "asset_type": "season",
        "season_number": 1,
        "path": "/tmp/organized/The Legend of Korra (2012)/Season01.jpg",
        "display_name": "The Legend of Korra (2012)",
    }
    index = {
        "movies": {},
        "shows": {"legendofkorra2012": [_ShowWithSeason("show", "The Legend of Korra", 2012, "TV Shows")]},
        "collections": {},
    }

    outcome = service._upload_asset(
        asset,
        index,
        dry_run=True,
    )

    assert outcome.uploaded == 1
    assert outcome.matched is True
    assert outcome.seasons_missing == 0


# ---------------------------------------------------------------------------
# _item_library_name / _item_library_key — stale-item 404 resilience
# ---------------------------------------------------------------------------


class _RaisingAttr:
    """Simulates a plexapi item whose attribute access triggers a lazy reload
    that raises NotFound (the same exception path as a 404 stale-metadata ID)."""

    def __getattribute__(self, name):
        if name.startswith("_") or name == "__class__":
            return super().__getattribute__(name)
        raise Exception("(404) not_found; http://plex/library/metadata/99999 Not Found")


def test_item_library_name_returns_empty_string_on_404(test_db):
    """_item_library_name must return '' rather than propagating a 404."""
    service = PlexUploadService(test_db)
    stale_item = _RaisingAttr()
    result = service._item_library_name(stale_item)
    assert result == ""


def test_item_library_key_returns_empty_string_on_404(test_db):
    """_item_library_key must return '' rather than propagating a 404."""
    service = PlexUploadService(test_db)
    stale_item = _RaisingAttr()
    result = service._item_library_key(stale_item)
    assert result == ""


def test_item_library_name_returns_value_for_normal_item(test_db):
    """_item_library_name must still return the correct value for a valid item."""
    service = PlexUploadService(test_db)
    item = _FakePlexItem("movie", "The Da Vinci Code", library="Movies")
    result = service._item_library_name(item)
    assert result == "Movies"


def test_item_library_key_returns_value_for_normal_item(test_db):
    """_item_library_key must still return a key for a valid item."""
    service = PlexUploadService(test_db)
    item = _FakePlexItem("movie", "The Da Vinci Code", library="Movies", section_id=3, server_id="abc123")
    result = service._item_library_key(item)
    assert result == "abc123:3"


def test_is_asset_fully_cached_skips_stale_item_gracefully(test_db):
    """When a Plex item raises 404 on attribute access, the cache check must
    return False (treat as not cached) rather than crashing."""
    service = PlexUploadService(test_db)

    stale_item = _RaisingAttr()
    index = {
        "movies": {"davinci2006": [stale_item]},
        "shows": {},
        "collections": {},
    }
    asset = {
        "media_key": "davinci2006",
        "asset_type": "main",
        "season_number": None,
        "path": "/posters/The Da Vinci Code (2006)/poster.jpg",
        "display_name": "The Da Vinci Code (2006)",
        "folder_year": 2006,
    }

    result = service._is_asset_fully_cached_for_targets(
        asset,
        index=index,
        media_type_filter="movie",
        arr_availability=None,
    )

    assert result is False


# ---------------------------------------------------------------------------
# _show_folder_key
# ---------------------------------------------------------------------------


def test_show_folder_key_uses_locations_when_available(test_db):
    """Primary path: show.locations gives the folder directly, no episode traversal needed."""
    service = PlexUploadService(test_db)

    class _FakeShow:
        locations = ["/tv/Friday Night Lights (2006)"]
        title = "Friday Night Lights"
        year = 2006

    # normalize_titles strips the year, so (2006) is not in the result
    assert service._show_folder_key(_FakeShow()) == "fridaynightlights"


def test_show_folder_key_episode_traversal_with_season_subfolder(test_db):
    """Fallback: episode path has a Season subfolder → return its parent (the show folder)."""
    service = PlexUploadService(test_db)

    class _FakePart:
        file = "/tv/Friday Night Lights (2006)/Season 01/s01e01.mkv"

    class _FakeMedia:
        parts = [_FakePart()]

    class _FakeEpisode:
        media = [_FakeMedia()]

    class _FakeSeason:
        def episodes(self):
            return [_FakeEpisode()]

    class _FakeShow:
        locations = None
        title = "Friday Night Lights"
        year = 2006

        def seasons(self):
            return [_FakeSeason()]

    assert service._show_folder_key(_FakeShow()) == "fridaynightlights"


def test_show_folder_key_episode_traversal_flat_layout(test_db):
    """Flat layout fix: episodes stored directly in show folder (no Season subfolder) must
    return the show folder name, not the TV root directory above it."""
    service = PlexUploadService(test_db)

    class _FakePart:
        file = "/tv/Friday Night Lights (2006)/s01e01.mkv"  # flat — no Season xx

    class _FakeMedia:
        parts = [_FakePart()]

    class _FakeEpisode:
        media = [_FakeMedia()]

    class _FakeSeason:
        def episodes(self):
            return [_FakeEpisode()]

    class _FakeShow:
        locations = None
        title = "Friday Night Lights"
        year = 2006

        def seasons(self):
            return [_FakeSeason()]

    # Must be the show folder name, not the parent "tv" directory
    key = service._show_folder_key(_FakeShow())
    assert key == "fridaynightlights"
    assert key != "tv"


def test_show_folder_key_locations_takes_priority_over_episode_traversal(test_db):
    """When locations is present, episode traversal should not be used even if seasons() exists."""
    service = PlexUploadService(test_db)

    traversal_called = {"value": False}

    class _FakeShow:
        locations = ["/tv/Ghosts (2021)"]
        title = "Ghosts"
        year = 2021

        def seasons(self):
            traversal_called["value"] = True
            return []

    key = service._show_folder_key(_FakeShow())
    assert key == "ghosts"
    assert traversal_called["value"] is False


# ---------------------------------------------------------------------------
# Unmatched reason breakdown — the scanned/matched gap has to explain itself
# ---------------------------------------------------------------------------


def test_diagnose_no_match_reports_year_mismatch_when_plex_has_other_year(test_db):
    """Title is in Plex but under a different year — not the same as 'not in Plex'."""
    service = PlexUploadService(test_db)
    index = {
        "movies": {"michael": [_FakePlexItem("movie", "Michael", 2026, "Movies")]},
        "shows": {},
        "collections": {},
    }

    assert service._diagnose_no_match(index, "michael", [], 2025) == "year_mismatch"
    assert service._diagnose_no_match(index, "michael", [], 2026) == "no_plex_match"
    assert service._diagnose_no_match(index, "nothinghere", [], 2025) == "no_plex_match"
    assert service._diagnose_no_match(index, "michael", [], None) == "no_plex_match"


def test_upload_asset_year_mismatch_is_counted_separately_from_missing(test_db):
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "michael",
        "path": "/tmp/posters/Michael (2025)/poster.jpg",
        "display_name": "Michael (2025)",
        "asset_type": "main",
        "folder_year": 2025,
    }
    index = {
        "movies": {"michael": [_FakePlexItem("movie", "Michael", 2026, "Movies")]},
        "shows": {},
        "collections": {},
    }

    outcome = service._upload_asset(asset, index, dry_run=True, media_type_filter="movie")

    assert outcome.matched is False
    assert outcome.skip_reason == "year_mismatch"


def test_process_assets_accumulates_reasons_and_plex_targets(test_db, monkeypatch):
    """Run stats must carry the per-reason breakdown and the multi-library target count."""
    service = PlexUploadService(test_db)
    outcomes = [
        AssetOutcome(2, True, 2, service._empty_media_upload_counts()),
        AssetOutcome(1, True, 1, service._empty_media_upload_counts()),
        AssetOutcome(0, False, 0, service._empty_media_upload_counts(), skip_reason="no_plex_match"),
        AssetOutcome(0, False, 0, service._empty_media_upload_counts(), skip_reason="year_mismatch"),
        AssetOutcome(0, False, 0, service._empty_media_upload_counts(), skip_reason="not_downloaded"),
    ]
    monkeypatch.setattr(service, "_upload_asset", lambda *a, **k: outcomes.pop(0))

    assets = [{"path": f"/tmp/{i}.jpg", "asset_type": "main"} for i in range(5)]
    stats = service._build_run_stats(assets, [])
    service._process_assets_for_upload(
        local_assets=assets,
        index={"movies": {}, "shows": {}, "collections": {}},
        stats=stats,
        dry_run=False,
        arr_availability={},
        remove_overlay_label=False,
    )

    assert stats["scanned"] == 5
    assert stats["matched"] == 2
    assert stats["plex_targets"] == 3
    assert stats["multi_library_assets"] == 1  # only the 2-target asset
    assert stats["unmatched_reasons"] == {
        "no_plex_match": 1,
        "year_mismatch": 1,
        "not_downloaded": 1,
        "type_unresolved": 0,
        "edition_pending": 0,
    }
    # Buckets partition the scan: nothing is double-counted, nothing vanishes.
    assert stats["uploaded_files"] == 2
    assert stats["already_current"] == 0
    assert stats["awaiting_plex"] == 0
    assert (
        stats["uploaded_files"] + stats["already_current"] + stats["awaiting_plex"]
        + sum(stats["unmatched_reasons"].values()) + stats["errors"] == stats["scanned"]
    )


def test_format_unmatched_reasons_lists_only_non_zero_reasons():
    assert format_unmatched_reasons({"no_plex_match": 40, "year_mismatch": 0, "not_downloaded": 15}) == (
        "40 no Plex match, 15 not downloaded"
    )
    assert format_unmatched_reasons({"no_plex_match": 0}) == ""
    assert format_unmatched_reasons(None) == ""


def test_format_match_detail_buckets_account_for_every_scanned_file():
    from modules.upload import _format_match_detail

    detail = _format_match_detail({
        "scanned": 5715,
        "uploaded_files": 4,
        "already_current": 5648,
        "awaiting_plex": 2,
        "errors": 0,
        "unmatched_reasons": {"no_plex_match": 48, "year_mismatch": 9, "not_downloaded": 4},
    })

    assert detail == (
        "5,715 file(s): 4 uploaded, 5,648 already current, 2 awaiting Plex scan, "
        "61 unmatched (48 no Plex match, 9 year differs, 4 not downloaded)"
    )

    clean = _format_match_detail({"scanned": 12, "uploaded_files": 0, "already_current": 12})
    assert clean == "12 file(s): 0 uploaded, 12 already current"


def test_format_match_detail_says_would_upload_on_a_dry_run():
    from modules.upload import _format_match_detail

    detail = _format_match_detail({"scanned": 3, "uploaded_files": 3, "already_current": 0}, dry_run=True)
    assert detail == "3 file(s): 3 would upload"


def test_format_match_detail_does_not_invent_unmatched_without_buckets():
    """A stats dict with no buckets must fall back, not report every file as unmatched."""
    from modules.upload import _format_match_detail

    detail = _format_match_detail({"scanned": 10, "matched": 8})
    assert detail == "10 file(s): 8 matched, 2 unmatched"


def test_season_poster_of_undownloaded_show_reports_not_downloaded(test_db):
    """A show with no Sonarr episodes is missing from Plex because of that — its season
    posters must report the same cause as the show's own poster, not 'no Plex match'."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "gracepoint2014",
        "path": "/tmp/posters/Gracepoint (2014) {tvdb-276396}/Season01.jpg",
        "display_name": "Gracepoint (2014) {tvdb-276396}",
        "asset_type": "season",
        "season_number": 1,
        "folder_year": 2014,
    }
    index = {"movies": {}, "shows": {}, "collections": {}}
    arr_availability = {"shows": {"gracepoint2014": {"has_episodes": False, "seasons": {}}}}

    outcome = service._upload_asset(asset, index, dry_run=True, arr_availability=arr_availability)

    assert outcome.matched is False
    assert outcome.skip_reason == "not_downloaded"


def test_season_poster_of_downloaded_show_missing_from_plex_still_reports_no_match(test_db):
    """*arr has the episodes, so a Plex miss really is a match problem — keep saying so."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "someshow2020",
        "path": "/tmp/posters/Some Show (2020)/Season01.jpg",
        "display_name": "Some Show (2020)",
        "asset_type": "season",
        "season_number": 1,
        "folder_year": 2020,
    }
    index = {"movies": {}, "shows": {}, "collections": {}}
    arr_availability = {"shows": {"someshow2020": {"has_episodes": True, "seasons": {1: True}}}}

    outcome = service._upload_asset(asset, index, dry_run=True, arr_availability=arr_availability)

    assert outcome.matched is False
    assert outcome.skip_reason == "no_plex_match"


def test_season_poster_without_arr_configured_falls_back_to_match_diagnosis(test_db):
    """No *arr index means no availability opinion — don't invent one."""
    service = PlexUploadService(test_db)
    asset = {
        "media_key": "someshow2020",
        "path": "/tmp/posters/Some Show (2020)/Season01.jpg",
        "display_name": "Some Show (2020)",
        "asset_type": "season",
        "season_number": 1,
        "folder_year": 2020,
    }
    index = {"movies": {}, "shows": {}, "collections": {}}

    outcome = service._upload_asset(asset, index, dry_run=True, arr_availability=None)

    assert outcome.matched is False
    assert outcome.skip_reason == "no_plex_match"
