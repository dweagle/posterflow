from services.poster_renamer import PosterRenameService


def test_rename_posters_fails_when_no_assets_found(test_db, monkeypatch):
    service = PosterRenameService(test_db)

    monkeypatch.setattr("services.poster_renamer.get_assets_files", lambda source_dirs, logger: ([], {}))

    result = service.rename_posters(
        source_dirs=["/tmp/source"],
        destination_dir="/tmp/dest",
        dry_run=True,
    )

    assert result["success"] is False
    assert result["error"] == "No assets found in the source directories"


def test_rename_posters_fails_when_no_media_found(test_db, monkeypatch):
    service = PosterRenameService(test_db)

    assets = [
        {
            "title": "Movie One",
            "year": 2024,
            "files": ["/tmp/source/Movie One.jpg"],
            "folder": "Movie One (2024)",
        }
    ]

    monkeypatch.setattr("services.poster_renamer.get_assets_files", lambda source_dirs, logger: (assets, {"m": assets}))
    monkeypatch.setattr(service, "get_media_from_instances", lambda: {"movies": [], "series": [], "collections": []})

    result = service.rename_posters(
        source_dirs=["/tmp/source"],
        destination_dir="/tmp/dest",
        dry_run=True,
    )

    assert result["success"] is False
    assert "No media found" in result["error"]


def test_rename_posters_fails_when_no_assets_match_media(test_db, monkeypatch):
    service = PosterRenameService(test_db)

    assets = [
        {
            "title": "Movie One",
            "year": 2024,
            "files": ["/tmp/source/Movie One.jpg"],
            "folder": "Movie One (2024)",
        }
    ]
    media = {
        "movies": [{"title": "Different Movie", "year": 2024, "folder": "Different Movie (2024)", "tmdb_id": 123}],
        "series": [],
        "collections": [],
    }

    monkeypatch.setattr("services.poster_renamer.get_assets_files", lambda source_dirs, logger: (assets, {"m": assets}))
    monkeypatch.setattr(service, "get_media_from_instances", lambda: media)
    monkeypatch.setattr(
        "services.poster_renamer.match_assets_to_media",
        lambda media_dict, prefix_index, strict_folder_match=False: {"movies": [], "series": [], "collections": []},
    )

    result = service.rename_posters(
        source_dirs=["/tmp/source"],
        destination_dir="/tmp/dest",
        dry_run=True,
    )

    assert result["success"] is False
    assert "No assets matched to media" in result["error"]


def test_rename_posters_successful_flow_returns_stats(test_db, monkeypatch):
    service = PosterRenameService(test_db)

    assets = [
        {
            "title": "Movie One",
            "year": 2024,
            "files": ["/tmp/source/Movie One.jpg"],
            "folder": "Movie One (2024)",
        }
    ]
    media = {
        "movies": [{"title": "Movie One", "year": 2024, "folder": "Movie One (2024)", "tmdb_id": 123}],
        "series": [],
        "collections": [],
    }
    matched_assets = {
        "movies": [
            {
                "title": "Movie One",
                "year": 2024,
                "folder": "Movie One (2024)",
                "files": ["/tmp/source/Movie One.jpg"],
            }
        ],
        "series": [],
        "collections": [],
    }

    monkeypatch.setattr("services.poster_renamer.get_assets_files", lambda source_dirs, logger: (assets, {"m": assets}))
    monkeypatch.setattr(service, "get_media_from_instances", lambda: media)
    monkeypatch.setattr(
        "services.poster_renamer.match_assets_to_media",
        lambda media_dict, prefix_index, strict_folder_match=False: matched_assets,
    )
    monkeypatch.setattr(
        service,
        "rename_files",
        lambda matched_assets, destination_dir, action_type, asset_folders, dry_run, progress_callback=None: (
            {"movies": [{"title": "Movie One", "year": 2024, "folder": "Movie One (2024)", "messages": ["renamed"]}], "series": [], "collections": []},
            ["/tmp/dest/Movie One (2024)/poster.jpg"],
            ["/tmp/source/Movie One.jpg"],
            {"/tmp/dest/Movie One (2024)/poster.jpg": ("/tmp/source/Movie One.jpg", "Movie One", 2024, "movie", None)},
        ),
    )

    result = service.rename_posters(
        source_dirs=["/tmp/source"],
        destination_dir="/tmp/dest",
        dry_run=True,
    )

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["stats"]["total_assets"] == 1
    assert result["stats"]["total_media"] == 1
    assert result["stats"]["total_matched"] == 1
    assert result["stats"]["movies"] == 1


def test_filter_assets_for_target_prefers_matching_ids_and_title(test_db):
    service = PosterRenameService(test_db)

    assets = [
        {
            "type": "movies",
            "title": "Movie One",
            "year": 2024,
            "normalized_title": "movieone2024",
            "tmdb_id": 101,
            "imdb_id": "tt0101",
            "files": ["/tmp/source/Movie One.jpg"],
        },
        {
            "type": "movies",
            "title": "Movie Two",
            "year": 2024,
            "normalized_title": "movietwo2024",
            "tmdb_id": 202,
            "imdb_id": "tt0202",
            "files": ["/tmp/source/Movie Two.jpg"],
        },
    ]

    filtered_assets, _filtered_index = service._filter_assets_for_target(
        assets,
        target_media_type="movie",
        target_title="Movie One",
        target_year=2024,
        target_tmdb_id=101,
        target_tvdb_id=None,
        target_imdb_id="tt0101",
        target_season_number=None,
    )

    assert len(filtered_assets) == 1
    assert filtered_assets[0]["title"] == "Movie One"


def test_filter_assets_for_target_respects_series_season_scope(test_db):
    service = PosterRenameService(test_db)

    assets = [
        {
            "type": "series",
            "title": "The Show",
            "year": 2023,
            "normalized_title": "theshow2023",
            "tvdb_id": 300,
            "season_numbers": [1, 2],
            "files": ["/tmp/source/The Show/Season01.jpg", "/tmp/source/The Show/Season02.jpg"],
        },
        {
            "type": "series",
            "title": "Other Show",
            "year": 2023,
            "normalized_title": "othershow2023",
            "tvdb_id": 301,
            "season_numbers": [1],
            "files": ["/tmp/source/Other Show/Season01.jpg"],
        },
    ]

    filtered_assets, _filtered_index = service._filter_assets_for_target(
        assets,
        target_media_type="series",
        target_title="The Show",
        target_year=2023,
        target_tmdb_id=None,
        target_tvdb_id=300,
        target_imdb_id=None,
        target_season_number=2,
    )

    assert len(filtered_assets) == 1
    assert filtered_assets[0]["title"] == "The Show"
