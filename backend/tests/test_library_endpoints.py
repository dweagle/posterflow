"""Library selection feeds serve only movie/show libraries — Jellyfin box sets
("Collections"), photo/music sections, and mixed folders aren't consumed by any
library-scoped feature and must not appear in selection UIs."""

import json

from models.setting import upsert_setting


def test_saved_library_config_filters_non_media_types(client, test_db):
    upsert_setting(test_db, "plex_library_config", json.dumps([
        {"instance_name": "jf", "libraries": [
            {"title": "Movies", "key": "a1", "type": "movie", "enabled": True},
            {"title": "Shows", "key": "b2", "type": "show", "enabled": True},
            {"title": "Collections", "key": "c3", "type": "boxsets", "enabled": True},
        ]},
        {"instance_name": "plex", "libraries": [
            {"title": "Movies", "key": 1, "type": "movie", "enabled": True},
            {"title": "Music", "key": 2, "type": "artist", "enabled": False},
        ]},
    ]))
    test_db.commit()

    response = client.get("/api/settings/plex-libraries")
    assert response.status_code == 200
    configs = {c["instance_name"]: c["libraries"] for c in response.json()["configs"]}
    assert [lib["title"] for lib in configs["jf"]] == ["Movies", "Shows"]
    assert [lib["title"] for lib in configs["plex"]] == ["Movies"]


def test_jellyfin_discovery_filters_non_media_types(client, test_db, monkeypatch):
    from util.media_server.types import MediaServerLibrary

    class FakeJellyfin:
        connect_status = True

        def __init__(self, url, api_key):
            pass

        def get_libraries(self):
            return [
                MediaServerLibrary(key="a1", title="Movies", type="movie"),
                MediaServerLibrary(key="b2", title="Shows", type="show"),
                MediaServerLibrary(key="c3", title="Collections", type="boxsets"),
                MediaServerLibrary(key="d4", title="Mixed", type=""),
            ]

    monkeypatch.setattr("util.media_server.jellyfin.JellyfinClient", FakeJellyfin)

    response = client.post(
        "/api/test/jellyfin/libraries",
        json={"url": "http://jf.local", "token": "k"},
    )
    assert response.status_code == 200
    titles = [lib["title"] for lib in response.json()["libraries"]]
    assert titles == ["Movies", "Shows"]


def test_library_override_response_filters_non_media_types(client, test_db):
    """The Asset Upload page's library targeting reads a separate path
    (build_library_override_response) — it must filter like the settings feed."""
    upsert_setting(test_db, "plex_library_config", json.dumps([
        {"instance_name": "jf", "libraries": [
            {"title": "Movies", "key": "a1", "type": "movie", "enabled": True},
            {"title": "Collections", "key": "c3", "type": "boxsets", "enabled": True},
        ]},
    ]))
    upsert_setting(test_db, "plex_upload_library_override", json.dumps({
        "enabled": True,
        "configs": [
            {"instance_name": "jf", "libraries": [
                {"title": "Shows", "key": "b2", "type": "show", "enabled": True},
                {"title": "Collections", "key": "c3", "type": "boxsets", "enabled": True},
            ]},
        ],
    }))
    test_db.commit()

    response = client.get("/api/posterflow/plex-upload/library-override")
    assert response.status_code == 200
    payload = response.json()
    assert [lib["title"] for lib in payload["global_configs"][0]["libraries"]] == ["Movies"]
    assert [lib["title"] for lib in payload["configs"][0]["libraries"]] == ["Shows"]


def test_cache_entries_show_server_labels(client, test_db):
    """Cache entries derive per-server labels from library-key prefixes via the
    media_server_id_map, so same-named libraries on two servers are tellable apart."""
    from models.plex_upload import PlexUploadRecord

    upsert_setting(test_db, "media_server_id_map", json.dumps({
        "plexsrv": {"name": "main", "type": "plex", "libraries": {"1": "Movies"}},
        "jfsrv": {"name": "jelly", "type": "jellyfin", "libraries": {"lib1": "Movies"}},
    }))
    test_db.add(PlexUploadRecord(
        file_path="/assets/Heat (1995)/poster.jpg",
        file_hash="x",
        uploaded_to_libraries=json.dumps(["Movies"]),
        uploaded_to_library_keys=json.dumps(["plexsrv:/library/sections/1", "jfsrv:lib1", "unknown:z"]),
        uploaded_to_rating_keys=json.dumps(["1", "g1"]),
        uploaded_editions=json.dumps([]),
        uploaded_media_types=json.dumps(["movies"]),
    ))
    test_db.commit()

    response = client.get("/api/posterflow/plex-upload/upload-cache/entries")
    assert response.status_code == 200
    entry = response.json()["entries"][0]
    assert entry["server_libraries"] == ["Movies (Plex 'main')", "Movies (Jellyfin 'jelly')"]
