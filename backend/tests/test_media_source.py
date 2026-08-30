"""Media-server media source: arr-less installs pull movies/shows from Plex/Jellyfin
libraries (toggle auto-on without arrs), plus the Plex webhook receiver."""

import json
from datetime import datetime

from models.setting import upsert_setting
from services.poster_renamer import PosterRenameService
from util.media_server.plex import PlexClient
from util.poster_settings import media_server_media_source_enabled


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakePart:
    def __init__(self, file):
        self.file = file


class FakeMedia:
    def __init__(self, file):
        self.parts = [FakePart(file)]


class FakeItem:
    def __init__(self, **attrs):
        for key, value in attrs.items():
            setattr(self, key, value)


class FakeSection:
    def __init__(self, key, title, section_type, items=None, seasons=None, locations=None):
        self.key = key
        self.title = title
        self.type = section_type
        self.items = items or []
        self.seasons = seasons or []
        self.locations = locations or []

    def all(self, includeGuids=None):
        return self.items

    def search(self, libtype=None, guid=None, title=None):
        if libtype == "season":
            return self.seasons
        return self.items


class FakeLibrary:
    def __init__(self, sections):
        self._sections = sections

    def sections(self):
        return self._sections


class FakeServer:
    def __init__(self, sections):
        self.machineIdentifier = "machine1"
        self.friendlyName = "Test Plex"
        self.version = "1.41.0"
        self.library = FakeLibrary(sections)


def make_movie(rating_key, title, year, file, guids=(), extra_files=()):
    return FakeItem(
        ratingKey=rating_key,
        type="movie",
        title=title,
        year=year,
        guids=[FakeGuid(g) for g in guids],
        media=[FakeMedia(file)] + [FakeMedia(f) for f in extra_files],
        thumb=f"/library/metadata/{rating_key}/thumb/1",
        addedAt=datetime(2026, 1, 2, 3, 4, 5),
        originallyAvailableAt=datetime(year, 6, 1),
    )


def make_show(rating_key, title, year, location, guids=()):
    return FakeItem(
        ratingKey=rating_key,
        type="show",
        title=title,
        year=year,
        guids=[FakeGuid(g) for g in guids],
        locations=[location],
        addedAt=datetime(2026, 2, 3, 4, 5, 6),
        originallyAvailableAt=datetime(year, 9, 1),
    )


def make_season(show_rating_key, index, leaf_count):
    return FakeItem(
        ratingKey=show_rating_key * 100 + index,
        type="season",
        parentRatingKey=show_rating_key,
        index=index,
        leafCount=leaf_count,
    )


def default_sections():
    movies = FakeSection(1, "Movies", "movie", items=[
        make_movie(101, "Heat", 1995, "/data/movies/Heat (1995)/Heat.mkv",
                   guids=["tmdb://949", "imdb://tt0113277"]),
        make_movie(102, "No Guid Film", 2001, "/data/movies/No Guid Film (2001)/film.mkv"),
        make_movie(103, "Flat One", 2010, "/data/flat/Flat One.mkv", guids=["tmdb://111"]),
        make_movie(104, "Flat Two", 2011, "/data/flat/Flat Two.mkv", guids=["tmdb://222"]),
        # Split editions: two items whose files share one proper movie folder
        make_movie(105, "The Godfather", 1972,
                   "/data/movies/The Godfather (1972) {imdb-tt0068646}/The Godfather.mkv",
                   guids=["tmdb://238", "imdb://tt0068646"]),
        make_movie(106, "The Godfather", 1972,
                   "/data/movies/The Godfather (1972) {imdb-tt0068646}/The Godfather {edition-Final Cut}.mkv",
                   guids=["tmdb://238", "imdb://tt0068646"]),
    ], locations=["/data/movies", "/data/flat"])
    shows = FakeSection(2, "TV Shows", "show", items=[
        make_show(201, "The Wire", 2002, "/data/tv/The Wire (2002)",
                  guids=["tvdb://79126", "tmdb://1438", "imdb://tt0306414"]),
    ], seasons=[
        make_season(201, 1, 13),
        make_season(201, 0, 2),
        make_season(201, 2, 0),
    ])
    return [movies, shows]


def run_fetch(test_db, monkeypatch, sections, selected_libraries=None):
    import util.media_server as media_server_module

    client = PlexClient("http://plex.local", "token", server=FakeServer(sections))
    monkeypatch.setattr(media_server_module, "create_media_server_client", lambda instance: client)
    service = PosterRenameService(test_db)
    media_dict = {"movies": [], "series": [], "collections": []}
    service._fetch_media_server_media(
        {"name": "Plex", "url": "http://plex.local", "api_key": "token"},
        media_dict,
        selected_libraries=selected_libraries,
    )
    return service, media_dict


# ── effective-mode helper ────────────────────────────────────────────────


def test_media_source_defaults_on_without_arrs(test_db):
    assert media_server_media_source_enabled(test_db) is True


def test_media_source_defaults_off_with_configured_arr(test_db):
    upsert_setting(test_db, "sonarr_instances", json.dumps([{"name": "Sonarr", "url": "http://s", "api_key": "k"}]))
    test_db.commit()
    assert media_server_media_source_enabled(test_db) is False


def test_media_source_blank_arr_entries_do_not_count(test_db):
    upsert_setting(test_db, "radarr_instances", json.dumps([{"name": "Radarr", "url": "", "api_key": ""}]))
    test_db.commit()
    assert media_server_media_source_enabled(test_db) is True


def test_media_source_explicit_setting_wins(test_db):
    upsert_setting(test_db, "radarr_instances", json.dumps([{"name": "Radarr", "url": "http://r", "api_key": "k"}]))
    upsert_setting(test_db, "media_server_media_source", "true")
    test_db.commit()
    assert media_server_media_source_enabled(test_db) is True

    upsert_setting(test_db, "radarr_instances", json.dumps([]))
    upsert_setting(test_db, "media_server_media_source", "false")
    test_db.commit()
    assert media_server_media_source_enabled(test_db) is False


# ── _fetch_media_server_media mapping ────────────────────────────────────


def test_fetch_movie_mapping(test_db, monkeypatch):
    _, media_dict = run_fetch(test_db, monkeypatch, default_sections())
    heat = next(m for m in media_dict["movies"] if m["title"] == "Heat")
    assert heat["type"] == "movies"
    assert heat["year"] == 1995
    assert heat["tmdb_id"] == 949
    assert heat["imdb_id"] == "tt0113277"
    assert heat["folder"] == "Heat (1995)"
    assert heat["root_folder"] == "/data/movies"
    assert heat["status"] == "released"
    assert heat["has_file"] is True
    assert heat["monitored"] is True
    assert heat["instance"] == "Plex (Movies)"
    assert heat["source"] == "plex"
    assert heat["added"] == "2026-01-02T03:04:05"
    assert heat["release_date"] == "1995-06-01T00:00:00"
    assert heat["thumb_url"] == (
        "/api/posterflow/plex-upload/plex-thumb?instance=Plex&key=%2Flibrary%2Fmetadata%2F101%2Fthumb%2F1"
    )
    assert heat["normalized_title"]
    assert "alternate_titles" in heat and "normalized_alternate_titles" in heat


def test_fetch_keeps_guidless_items(test_db, monkeypatch):
    _, media_dict = run_fetch(test_db, monkeypatch, default_sections())
    no_guid = next(m for m in media_dict["movies"] if m["title"] == "No Guid Film")
    assert no_guid["tmdb_id"] is None
    assert no_guid["imdb_id"] is None
    assert no_guid["folder"] == "No Guid Film (2001)"


def test_fetch_flat_layout_movies_fall_back_to_title_year_folder(test_db, monkeypatch):
    _, media_dict = run_fetch(test_db, monkeypatch, default_sections())
    flat_one = next(m for m in media_dict["movies"] if m["title"] == "Flat One")
    flat_two = next(m for m in media_dict["movies"] if m["title"] == "Flat Two")
    assert flat_one["folder"] == "Flat One (2010)"
    assert flat_two["folder"] == "Flat Two (2011)"
    assert flat_one["root_folder"] == "/data/flat"


def test_fetch_split_editions_sharing_a_folder_keep_the_folder_name(test_db, monkeypatch):
    """Split-edition/duplicate items share one proper movie folder; both must keep the
    real folder name (Kometa matches on it), not fall back to Title (Year) — only files
    directly in a library root are a flat layout."""
    _, media_dict = run_fetch(test_db, monkeypatch, default_sections())
    godfathers = [m for m in media_dict["movies"] if m["title"] == "The Godfather"]
    assert len(godfathers) == 2
    assert all(m["folder"] == "The Godfather (1972) {imdb-tt0068646}" for m in godfathers)
    assert all(m["root_folder"] == "/data/movies" for m in godfathers)


def test_fetch_multi_version_item_carries_extra_folders(test_db, monkeypatch):
    """One item whose versions live in differently named folders (Plex merges same-id
    files across folders) must expose every folder, so each gets an asset folder."""
    sections = [FakeSection(1, "Movies", "movie", items=[
        make_movie(301, "Gladiator", 2000,
                   "/data/movies/Gladiator (2000) {imdb-tt0172495}/Gladiator.mkv",
                   guids=["tmdb://98", "imdb://tt0172495"],
                   extra_files=["/data/movies/Gladiator (2000) {tmdb-98}/Gladiator-dupe.mkv"]),
    ], locations=["/data/movies"])]
    _, media_dict = run_fetch(test_db, monkeypatch, sections)
    glad = media_dict["movies"][0]
    assert glad["folder"] == "Gladiator (2000) {imdb-tt0172495}"
    assert glad["extra_folders"] == ["Gladiator (2000) {tmdb-98}"]


def test_merge_duplicate_items_union_folder_names(test_db):
    """Separate items for the same movie (Jellyfin split copies, or one per server) in
    differently named folders merge into one entry that keeps every folder name."""
    svc = PosterRenameService(test_db)
    a = {"type": "movies", "title": "Gladiator", "year": 2000, "tmdb_id": 98,
         "folder": "Gladiator (2000) {imdb-tt0172495}", "instance": "Plex (Movies)"}
    b = {"type": "movies", "title": "Gladiator", "year": 2000, "tmdb_id": 98,
         "folder": "Gladiator (2000) {tmdb-98}", "instance": "Jellyfin (Movies)"}
    merged = svc._merge_duplicate_movies([a, b])
    assert len(merged) == 1
    assert merged[0]["folder"] == "Gladiator (2000) {imdb-tt0172495}"
    assert merged[0]["extra_folders"] == ["Gladiator (2000) {tmdb-98}"]


def test_matched_entries_carry_extra_folders(test_db, tmp_path):
    """Regression: match_assets_to_media rebuilds matched entries from the media item,
    and used to drop extra_folders — so the real pipeline only ever created ONE folder
    while the direct rename_files test passed."""
    from util.posters.index import build_search_index, create_new_empty_index
    from util.posters.match import match_assets_to_media

    asset = {"type": "movies", "title": "Gladiator", "year": 2000, "tmdb_id": 98,
             "imdb_id": None, "tvdb_id": None, "normalized_title": "gladiator",
             "files": [str(tmp_path / "Gladiator (2000) {tmdb-98}.jpg")]}
    index = create_new_empty_index()
    build_search_index(index, asset["title"], asset)

    media = {"movies": [{
        "type": "movies", "title": "Gladiator", "year": 2000, "tmdb_id": 98,
        "normalized_title": "gladiator", "alternate_titles": [],
        "folder": "Gladiator (2000) {imdb-tt0172495}",
        "extra_folders": ["Gladiator (2000) {tmdb-98}"],
    }], "series": [], "collections": []}

    matched = match_assets_to_media(media, index, report_near_misses=False)
    assert matched["movies"][0]["extra_folders"] == ["Gladiator (2000) {tmdb-98}"]


def test_rename_files_places_into_every_folder_name(test_db, tmp_path):
    """An item with extra_folders gets its assets placed into each folder, so Kometa
    finds them for every copy."""
    src = tmp_path / "drive"
    src.mkdir()
    poster = src / "Gladiator (2000) {tmdb-98}.jpg"
    poster.write_bytes(b"poster-bytes")

    matched = {"collections": [], "series": [], "movies": [{
        "type": "movies", "title": "Gladiator", "year": 2000, "tmdb_id": 98,
        "folder": "Gladiator (2000) {imdb-tt0172495}",
        "extra_folders": ["Gladiator (2000) {tmdb-98}"],
        "files": [str(poster)],
    }]}

    dest = tmp_path / "dest"
    dest.mkdir()
    svc = PosterRenameService(test_db)
    svc.rename_files(matched, str(dest), action_type="copy", asset_folders=True, dry_run=False)

    assert (dest / "Gladiator (2000) {imdb-tt0172495}" / "poster.jpg").exists()
    assert (dest / "Gladiator (2000) {tmdb-98}" / "poster.jpg").exists()


def test_fetch_show_mapping(test_db, monkeypatch):
    _, media_dict = run_fetch(test_db, monkeypatch, default_sections())
    assert len(media_dict["series"]) == 1
    wire = media_dict["series"][0]
    assert wire["type"] == "series"
    assert wire["tvdb_id"] == 79126
    assert wire["tmdb_id_ref"] == 1438
    assert "tmdb_id" not in wire
    assert wire["imdb_id"] == "tt0306414"
    assert wire["folder"] == "The Wire (2002)"
    assert wire["root_folder"] == "/data/tv"
    assert wire["status"] == "continuing"
    assert wire["has_episodes"] is True
    assert wire["instance"] == "Plex (TV Shows)"
    # Empty season 2 excluded; Specials (0) and season 1 kept, sorted
    assert [s["season_number"] for s in wire["seasons"]] == [0, 1]
    assert all(s["season_has_episodes"] and s["monitored"] for s in wire["seasons"])


def test_fetch_respects_selected_libraries(test_db, monkeypatch):
    _, media_dict = run_fetch(test_db, monkeypatch, default_sections(), selected_libraries=["Plex:1"])
    assert media_dict["movies"]
    assert media_dict["series"] == []


def test_fetch_connection_failure_marks_both_types(test_db, monkeypatch):
    import util.media_server as media_server_module

    monkeypatch.setattr(media_server_module, "create_media_server_client", lambda instance: None)
    service = PosterRenameService(test_db)
    media_dict = {"movies": [], "series": [], "collections": []}
    service._fetch_media_server_media({"name": "Plex", "url": "u", "api_key": "k"}, media_dict)
    assert service.media_fetch_failed_types == {"movies", "series"}


def test_fetch_library_failure_marks_that_type(test_db, monkeypatch):
    import util.media_server as media_server_module

    sections = default_sections()

    def boom(includeGuids=None):
        raise RuntimeError("section listing failed")

    sections[1].all = boom
    client = PlexClient("http://plex.local", "token", server=FakeServer(sections))
    monkeypatch.setattr(media_server_module, "create_media_server_client", lambda instance: client)
    service = PosterRenameService(test_db)
    media_dict = {"movies": [], "series": [], "collections": []}
    service._fetch_media_server_media({"name": "Plex", "url": "u", "api_key": "k"}, media_dict)
    assert service.media_fetch_failed_types == {"series"}
    assert media_dict["movies"]


def test_arr_items_win_duplicate_merge(test_db):
    service = PosterRenameService(test_db)
    arr_movie = {"type": "movies", "title": "Heat", "year": 1995, "tmdb_id": 949,
                 "folder": "/arr/Heat (1995)", "instance": "Radarr"}
    plex_movie = {"type": "movies", "title": "Heat", "year": 1995, "tmdb_id": 949,
                  "folder": "Heat (1995)", "instance": "Plex (Movies)", "source": "plex"}
    merged = service._merge_duplicate_movies([arr_movie, plex_movie])
    assert len(merged) == 1
    assert merged[0]["folder"] == "/arr/Heat (1995)"
    assert merged[0]["instance"] == "Radarr & Plex (Movies)"


def test_get_media_skips_media_server_media_when_disabled(test_db, monkeypatch):
    import util.media_server as media_server_module

    upsert_setting(test_db, "plex_instances", json.dumps([{"name": "Plex", "url": "http://p", "api_key": "k"}]))
    upsert_setting(test_db, "media_server_media_source", "false")
    test_db.commit()
    client = PlexClient("http://plex.local", "token", server=FakeServer(default_sections()))
    monkeypatch.setattr(media_server_module, "create_media_server_client", lambda instance: client)
    media_dict = PosterRenameService(test_db).get_media_from_instances()
    assert media_dict["movies"] == []
    assert media_dict["series"] == []


def test_get_media_fetches_media_server_media_when_enabled(test_db, monkeypatch):
    import util.media_server as media_server_module

    upsert_setting(test_db, "plex_instances", json.dumps([{"name": "Plex", "url": "http://p", "api_key": "k"}]))
    test_db.commit()
    client = PlexClient("http://plex.local", "token", server=FakeServer(default_sections()))
    monkeypatch.setattr(media_server_module, "create_media_server_client", lambda instance: client)
    media_dict = PosterRenameService(test_db).get_media_from_instances()
    # 6 fetched; the two Godfather edition items merge by tmdb id
    assert len(media_dict["movies"]) == 5
    assert len(media_dict["series"]) == 1


def test_get_media_connects_once_per_instance(test_db, monkeypatch):
    import util.media_server as media_server_module

    upsert_setting(test_db, "plex_instances", json.dumps([{"name": "Plex", "url": "http://p", "api_key": "k"}]))
    test_db.commit()
    client = PlexClient("http://plex.local", "token", server=FakeServer(default_sections()))
    connects = []

    def factory(instance):
        connects.append(instance["name"])
        return client

    monkeypatch.setattr(media_server_module, "create_media_server_client", factory)
    media_dict = PosterRenameService(test_db).get_media_from_instances()
    # Collections AND movies/shows come over the same client — one connect, not two
    assert connects == ["Plex"]
    assert media_dict["movies"] and media_dict["series"]


# ── Plex webhook parsing + dedupe ────────────────────────────────────────


def _plex_payload(event="library.new", **metadata):
    return {
        "event": event,
        "Server": {"title": "Test Plex", "uuid": "machine1"},
        "Metadata": metadata,
    }


def test_parse_plex_webhook_ignores_non_library_new():
    from modules.upload import _parse_plex_webhook_payload

    parsed = _parse_plex_webhook_payload(_plex_payload(event="media.play", type="movie", ratingKey="1", title="X"))
    assert parsed["skip"] is True


def test_parse_plex_webhook_movie():
    from modules.upload import _parse_plex_webhook_payload

    parsed = _parse_plex_webhook_payload(
        _plex_payload(type="movie", ratingKey="101", title="Heat", year=1995)
    )
    assert parsed["media_type"] == "movie"
    assert parsed["source"] == "plex"
    assert parsed["plex_rating_key"] == "101"
    assert parsed["plex_server_uuid"] == "machine1"
    assert parsed["title"] == "Heat"
    assert parsed["year"] == 1995
    assert parsed["season_number"] is None


def test_parse_plex_webhook_episode_resolves_to_show():
    from modules.upload import _parse_plex_webhook_payload

    parsed = _parse_plex_webhook_payload(
        _plex_payload(
            type="episode", ratingKey="999", title="Ep 3",
            grandparentRatingKey="201", grandparentTitle="The Wire", parentIndex=2,
        )
    )
    assert parsed["media_type"] == "series"
    assert parsed["plex_rating_key"] == "201"
    assert parsed["title"] == "The Wire"
    assert parsed["season_number"] == 2


def test_parse_plex_webhook_season_resolves_to_show():
    from modules.upload import _parse_plex_webhook_payload

    parsed = _parse_plex_webhook_payload(
        _plex_payload(type="season", ratingKey="20102", parentRatingKey="201", parentTitle="The Wire", index=2)
    )
    assert parsed["plex_rating_key"] == "201"
    assert parsed["season_number"] == 2


def test_parse_plex_webhook_missing_metadata_raises():
    import pytest
    from modules.upload import _parse_plex_webhook_payload

    with pytest.raises(ValueError):
        _parse_plex_webhook_payload({"event": "library.new"})


def test_plex_episode_webhooks_dedupe_to_one_event(test_db):
    from modules.upload import _is_duplicate_webhook_event, _parse_plex_webhook_payload

    first = _parse_plex_webhook_payload(
        _plex_payload(type="episode", ratingKey="901", grandparentRatingKey="201",
                      grandparentTitle="The Wire", parentIndex=1)
    )
    second = _parse_plex_webhook_payload(
        _plex_payload(type="episode", ratingKey="902", grandparentRatingKey="201",
                      grandparentTitle="The Wire", parentIndex=1)
    )
    assert _is_duplicate_webhook_event(test_db, first) is False
    assert _is_duplicate_webhook_event(test_db, second) is True


def test_plex_webhook_route_queues_job(client, test_db, monkeypatch):
    submitted = []
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *a, **k: submitted.append(a))

    response = client.post(
        "/api/posterflow/plex-upload/webhook/plex",
        data={"payload": json.dumps(_plex_payload(type="movie", ratingKey="101", title="Heat", year=1995))},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["queued"] is True
    assert submitted


def test_plex_webhook_route_skips_play_events(client, test_db, monkeypatch):
    submitted = []
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *a, **k: submitted.append(a))

    response = client.post(
        "/api/posterflow/plex-upload/webhook/plex",
        data={"payload": json.dumps(_plex_payload(event="media.play", type="movie", ratingKey="101", title="Heat"))},
    )
    assert response.status_code == 200
    assert response.json()["queued"] is False
    assert not submitted


def test_plex_webhook_route_rejects_bad_json(client, test_db):
    response = client.post(
        "/api/posterflow/plex-upload/webhook/plex",
        data={"payload": "not json"},
    )
    assert response.status_code == 400

# ── Jellyfin webhook parsing + resolution ────────────────────────────────


def _jellyfin_payload(notification="ItemAdded", **fields):
    return {
        "NotificationType": notification,
        "ServerId": "jf-server-1",
        "ServerName": "Test Jellyfin",
        **fields,
    }


def test_parse_jellyfin_webhook_ignores_other_notification_types():
    from modules.upload import _parse_jellyfin_webhook_payload

    parsed = _parse_jellyfin_webhook_payload(
        _jellyfin_payload(notification="PlaybackStart", ItemType="Movie", ItemId="a1", Name="Heat")
    )
    assert parsed["skip"] is True


def test_parse_jellyfin_webhook_movie_takes_payload_ids():
    from modules.upload import _parse_jellyfin_webhook_payload

    parsed = _parse_jellyfin_webhook_payload(
        _jellyfin_payload(
            ItemType="Movie", ItemId="a1", Name="Heat", Year="1995",
            Provider_tmdb="949", Provider_imdb="tt0113277",
        )
    )
    assert parsed["media_type"] == "movie"
    assert parsed["source"] == "jellyfin"
    assert parsed["plex_rating_key"] == "a1"
    assert parsed["plex_server_uuid"] == "jf-server-1"
    assert parsed["tmdb_id"] == 949
    assert parsed["imdb_id"] == "tt0113277"
    assert parsed["year"] == 1995


def test_parse_jellyfin_webhook_series_takes_tvdb():
    from modules.upload import _parse_jellyfin_webhook_payload

    parsed = _parse_jellyfin_webhook_payload(
        _jellyfin_payload(ItemType="Series", ItemId="s1", Name="The Wire", Year="2002", Provider_tvdb="79126")
    )
    assert parsed["media_type"] == "series"
    assert parsed["tvdb_id"] == 79126
    assert parsed["plex_rating_key"] == "s1"


def test_parse_jellyfin_webhook_episode_resolves_to_series_and_drops_item_ids():
    from modules.upload import _parse_jellyfin_webhook_payload

    parsed = _parse_jellyfin_webhook_payload(
        _jellyfin_payload(
            ItemType="Episode", ItemId="e1", Name="Ep 3", SeriesId="s1",
            SeriesName="The Wire", SeasonNumber="2",
            # Episode-level provider ids are episode-namespace — must NOT be used
            Provider_tvdb="5555555", Provider_tmdb="777",
        )
    )
    assert parsed["media_type"] == "series"
    assert parsed["plex_rating_key"] == "s1"
    assert parsed["title"] == "The Wire"
    assert parsed["season_number"] == 2
    assert parsed["tvdb_id"] is None
    assert parsed["tmdb_id"] is None


def test_parse_jellyfin_webhook_season_without_seriesid_keeps_item_id():
    from modules.upload import _parse_jellyfin_webhook_payload

    parsed = _parse_jellyfin_webhook_payload(
        _jellyfin_payload(ItemType="Season", ItemId="sea2", SeriesName="The Wire", SeasonNumber="2")
    )
    assert parsed["plex_rating_key"] == "sea2"
    assert parsed["season_number"] == 2


def test_parse_jellyfin_webhook_missing_title_raises():
    import pytest
    from modules.upload import _parse_jellyfin_webhook_payload

    with pytest.raises(ValueError):
        _parse_jellyfin_webhook_payload(_jellyfin_payload(ItemType="Movie", ItemId="a1"))


def test_jellyfin_episode_webhooks_dedupe_to_one_event(test_db):
    from modules.upload import _is_duplicate_webhook_event, _parse_jellyfin_webhook_payload

    first = _parse_jellyfin_webhook_payload(
        _jellyfin_payload(ItemType="Episode", ItemId="e1", SeriesId="s1", SeriesName="The Wire", SeasonNumber="1")
    )
    second = _parse_jellyfin_webhook_payload(
        _jellyfin_payload(ItemType="Episode", ItemId="e2", SeriesId="s1", SeriesName="The Wire", SeasonNumber="1")
    )
    assert _is_duplicate_webhook_event(test_db, first) is False
    assert _is_duplicate_webhook_event(test_db, second) is True


class _ResolverFakeClient:
    def __init__(self, items):
        self.server_id = "jf-server-1"
        self.items = items

    def get_item(self, item_id):
        return self.items.get(str(item_id))


def _resolver_setup(test_db, monkeypatch, items):
    import util.media_server as media_server_module

    upsert_setting(test_db, "plex_instances", json.dumps([
        {"name": "Jelly", "url": "http://j", "api_key": "k", "type": "jellyfin"},
    ]))
    test_db.commit()
    client = _ResolverFakeClient(items)
    monkeypatch.setattr(media_server_module, "create_media_server_client", lambda instance: client)


def test_resolve_jellyfin_ids_noop_when_payload_has_ids(test_db):
    from modules.upload import _resolve_jellyfin_webhook_ids

    parsed = {"media_type": "movie", "tmdb_id": 949, "plex_rating_key": "a1"}
    assert _resolve_jellyfin_webhook_ids(test_db, parsed) is None


def test_resolve_jellyfin_ids_walks_season_to_series(test_db, monkeypatch):
    from modules.upload import _resolve_jellyfin_webhook_ids
    from util.media_server.types import MediaServerItem

    series = MediaServerItem(item_id="s1", item_type="show", title="The Wire", year=2002,
                             provider_ids={"tvdb": "79126", "imdb": "tt0306414"})
    season = MediaServerItem(item_id="sea2", item_type="season", title="Season 2", parent_id="s1")
    _resolver_setup(test_db, monkeypatch, {"s1": series, "sea2": season})

    parsed = {"media_type": "series", "title": "The Wire", "year": None,
              "plex_rating_key": "sea2", "plex_server_uuid": "jf-server-1"}
    assert _resolve_jellyfin_webhook_ids(test_db, parsed) is None
    assert parsed["tvdb_id"] == 79126
    assert parsed["imdb_id"] == "tt0306414"
    assert parsed["plex_rating_key"] == "s1"
    assert parsed["year"] == 2002


def test_resolve_jellyfin_ids_reports_missing_item(test_db, monkeypatch):
    from modules.upload import _resolve_jellyfin_webhook_ids

    _resolver_setup(test_db, monkeypatch, {})
    parsed = {"media_type": "movie", "plex_rating_key": "ghost", "plex_server_uuid": "jf-server-1"}
    error = _resolve_jellyfin_webhook_ids(test_db, parsed)
    assert error and "not found" in error


def test_jellyfin_webhook_route_queues_job(client, test_db, monkeypatch):
    submitted = []
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *a, **k: submitted.append(a))

    response = client.post(
        "/api/posterflow/plex-upload/webhook/jellyfin",
        content=json.dumps(_jellyfin_payload(ItemType="Movie", ItemId="a1", Name="Heat", Year="1995", Provider_tmdb="949")),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["queued"] is True
    assert submitted


def test_jellyfin_webhook_route_skips_other_events(client, test_db, monkeypatch):
    submitted = []
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *a, **k: submitted.append(a))

    response = client.post(
        "/api/posterflow/plex-upload/webhook/jellyfin",
        content=json.dumps(_jellyfin_payload(notification="PlaybackStart", ItemType="Movie", ItemId="a1", Name="Heat")),
    )
    assert response.status_code == 200
    assert response.json()["queued"] is False
    assert not submitted


def test_jellyfin_webhook_route_rejects_bad_json(client, test_db):
    response = client.post(
        "/api/posterflow/plex-upload/webhook/jellyfin",
        content="not json",
    )
    assert response.status_code == 400


def test_resolve_jellyfin_ids_skips_unconfigured_server(test_db, monkeypatch):
    from modules.upload import _resolve_jellyfin_webhook_ids

    _resolver_setup(test_db, monkeypatch, {})
    parsed = {"media_type": "movie", "plex_rating_key": "m1", "plex_server_uuid": "some-other-server"}
    assert _resolve_jellyfin_webhook_ids(test_db, parsed) is None
    assert parsed["_unconfigured_server"] is True


def test_resolve_plex_ids_skips_unconfigured_server(test_db, monkeypatch):
    import util.media_server as media_server_module
    from modules.upload import _resolve_plex_webhook_ids
    from util.media_server.types import MediaServerInfo

    class _PlexResolverFake:
        server_id = "plex-uuid-1"

        def get_server_info(self):
            return MediaServerInfo(server_id="plex-uuid-1", name="P", version="1")

        def get_item(self, item_id):
            return None

    upsert_setting(test_db, "plex_instances", json.dumps([
        {"name": "Plex", "url": "http://p", "api_key": "k"},
    ]))
    test_db.commit()
    monkeypatch.setattr(media_server_module, "create_media_server_client", lambda instance: _PlexResolverFake())

    parsed = {"media_type": "movie", "plex_rating_key": "101", "plex_server_uuid": "someone-elses-server"}
    assert _resolve_plex_webhook_ids(test_db, parsed) is None
    assert parsed["_unconfigured_server"] is True


def test_resolve_plex_ids_still_errors_when_uuid_matches_but_item_missing(test_db, monkeypatch):
    import util.media_server as media_server_module
    from modules.upload import _resolve_plex_webhook_ids
    from util.media_server.types import MediaServerInfo

    class _PlexResolverFake:
        server_id = "plex-uuid-1"

        def get_server_info(self):
            return MediaServerInfo(server_id="plex-uuid-1", name="P", version="1")

        def get_item(self, item_id):
            return None

    upsert_setting(test_db, "plex_instances", json.dumps([
        {"name": "Plex", "url": "http://p", "api_key": "k"},
    ]))
    test_db.commit()
    monkeypatch.setattr(media_server_module, "create_media_server_client", lambda instance: _PlexResolverFake())

    parsed = {"media_type": "movie", "plex_rating_key": "101", "plex_server_uuid": "plex-uuid-1"}
    error = _resolve_plex_webhook_ids(test_db, parsed)
    assert error and "not found" in error
    assert "_unconfigured_server" not in parsed
