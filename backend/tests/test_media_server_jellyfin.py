import base64
from urllib.parse import urlparse


import util.media_server.jellyfin as jf
from util.media_server.jellyfin import (
    JellyfinClient,
    build_auth_header,
    normalize_provider_ids,
    provider_ids_match,
)
from util.media_server.types import IMAGE_KIND_POSTER, IMAGE_KIND_SQUAREART


class FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self.json_data = json_data
        self.status_code = status_code
        self.content = b"" if json_data is None and status_code == 204 else b"x"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise jf.requests.exceptions.HTTPError(f"status {self.status_code}")

    def json(self):
        return self.json_data


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.headers = {}
        self.calls = []

    def request(self, method, endpoint, params=None, data=None, headers=None, timeout=None):
        path = urlparse(endpoint).path
        self.calls.append({"method": method, "path": path, "params": params or {}, "data": data, "headers": headers})
        handler = self.routes.get(path)
        if handler is None:
            return FakeResponse(status_code=404)
        result = handler(params or {}, data) if callable(handler) else handler
        if isinstance(result, FakeResponse):
            return result
        return FakeResponse(json_data=result)


SYSTEM_INFO = {"Id": "srv123", "ServerName": "Test Jellyfin", "Version": "12.0.0"}


def make_client(monkeypatch, routes):
    routes = {"/System/Info": SYSTEM_INFO, **routes}
    session = FakeSession(routes)
    monkeypatch.setattr(jf.requests, "Session", lambda: session)
    client = JellyfinClient("http://jf.local/", "key123", app_version="0.14.1")
    return client, session


def test_auth_header_format():
    header = build_auth_header("abc", "1.0")
    assert header.startswith("MediaBrowser ")
    assert 'Token="abc"' in header
    assert 'Client="Posterflow"' in header
    assert 'Version="1.0"' in header


def test_normalize_provider_ids():
    ids = normalize_provider_ids({"Tmdb": 123, "Imdb": "tt001", "Tvdb": "", "X": None})
    assert ids == {"tmdb": "123", "imdb": "tt001"}


def test_provider_ids_match():
    assert provider_ids_match({"tmdb": "1"}, {"tmdb": "1", "imdb": "tt9"})
    assert not provider_ids_match({"tmdb": "1"}, {"tmdb": "2"})
    assert not provider_ids_match({"tmdb": "1"}, {"imdb": "tt9"})
    assert not provider_ids_match({}, {"tmdb": "1"})


def test_connect_sets_identity(monkeypatch):
    client, session = make_client(monkeypatch, {})
    assert client.connect_status
    assert client.server_id == "srv123"
    assert client.server_name == "Test Jellyfin"
    assert session.headers["Authorization"].startswith("MediaBrowser ")


def test_get_libraries_maps_types(monkeypatch):
    routes = {
        "/Library/VirtualFolders": [
            {"Name": "Movies", "ItemId": "lib1", "CollectionType": "movies"},
            {"Name": "TV", "ItemId": "lib2", "CollectionType": "tvshows"},
            {"Name": "Music", "ItemId": "lib3", "CollectionType": "music"},
        ]
    }
    client, _ = make_client(monkeypatch, routes)
    libs = client.get_libraries()
    assert [(l.key, l.type) for l in libs] == [("lib1", "movie"), ("lib2", "show"), ("lib3", "music")]


def test_items_pagination(monkeypatch):
    rows = [{"Id": f"i{n}", "Type": "Movie", "Name": f"M{n}", "OriginalTitle": f"O{n}"} for n in range(3)]

    def items_handler(params, _data):
        # v12 folds box-set members out of listings unless explicitly disabled
        assert params.get("collapseBoxSetItems") == "false"
        assert "OriginalTitle" in str(params.get("fields", ""))
        start = int(params.get("startIndex", 0))
        return {"Items": rows[start:start + 2], "TotalRecordCount": len(rows)}

    client, _ = make_client(monkeypatch, {"/Items": items_handler})
    items = client.get_library_items("lib1")
    assert [i.item_id for i in items] == ["i0", "i1", "i2"]
    assert items[0].library_key == "srv123:lib1"
    assert items[0].original_title == "O0"
    assert items[0].virtual is False


def test_seasons_flag_virtual_placeholders(monkeypatch):
    rows = [
        {"Id": "s1", "Type": "Season", "Name": "Season 1", "IndexNumber": 1, "SeriesId": "show", "ChildCount": 8, "LocationType": "FileSystem"},
        {"Id": "s2", "Type": "Season", "Name": "Season 2", "IndexNumber": 2, "SeriesId": "show", "ChildCount": 10, "LocationType": "Virtual"},
        {"Id": "s3", "Type": "Season", "Name": "Season 3", "IndexNumber": 3, "SeriesId": "show", "ChildCount": 1, "IsVirtualItem": True},
    ]
    client, _ = make_client(monkeypatch, {"/Items": lambda params, _d: {"Items": rows, "TotalRecordCount": len(rows)}})
    seasons = client.get_library_seasons("lib1")
    assert [(s.index, s.virtual) for s in seasons] == [(1, False), (2, True), (3, True)]


def test_find_by_provider_ids_title_pass(monkeypatch):
    def items_handler(params, _data):
        assert params.get("searchTerm") == "Heat"
        return {
            "Items": [
                {"Id": "a", "Type": "Movie", "Name": "Heat", "ProviderIds": {"Tmdb": "949"}},
                {"Id": "b", "Type": "Movie", "Name": "Heat", "ProviderIds": {"Tmdb": "0"}},
            ],
            "TotalRecordCount": 2,
        }

    client, session = make_client(monkeypatch, {"/Items": items_handler})
    matches = client.find_by_provider_ids({"tmdb": "949"}, "movie", title="Heat")
    assert [m.item_id for m in matches] == ["a"]
    assert all(c["path"] != "/Library/VirtualFolders" for c in session.calls)


def test_find_by_provider_ids_library_walk_fallback(monkeypatch):
    def items_handler(params, _data):
        if params.get("searchTerm"):
            return {"Items": [], "TotalRecordCount": 0}
        assert params.get("hasTmdbId") == "true"
        return {
            "Items": [{"Id": "z", "Type": "Movie", "Name": "Renamed Title", "ProviderIds": {"Tmdb": "949"}}],
            "TotalRecordCount": 1,
        }

    routes = {
        "/Items": items_handler,
        "/Library/VirtualFolders": [
            {"Name": "Movies", "ItemId": "lib1", "CollectionType": "movies"},
            {"Name": "TV", "ItemId": "lib2", "CollectionType": "tvshows"},
        ],
    }
    client, _ = make_client(monkeypatch, routes)
    matches = client.find_by_provider_ids({"tmdb": "949"}, "movie", title="Old Title")
    assert [m.item_id for m in matches] == ["z"]
    assert matches[0].library_name == "Movies"


def test_upload_image_base64_body(monkeypatch, tmp_path):
    poster = tmp_path / "poster.png"
    poster.write_bytes(b"fakepng")
    routes = {"/Items/abc/Images/Primary": FakeResponse(status_code=204)}
    client, session = make_client(monkeypatch, routes)
    item = jf.MediaServerItem(item_id="abc", item_type="movie", title="X")
    assert client.upload_image(item, IMAGE_KIND_POSTER, str(poster))
    call = session.calls[-1]
    assert call["method"] == "POST"
    assert call["data"] == base64.b64encode(b"fakepng")
    assert call["headers"]["Content-Type"] == "image/png"


def test_upload_image_squareart_unsupported(monkeypatch, tmp_path):
    art = tmp_path / "square.jpg"
    art.write_bytes(b"x")
    client, session = make_client(monkeypatch, {})
    item = jf.MediaServerItem(item_id="abc", item_type="movie", title="X")
    before = len(session.calls)
    assert not client.upload_image(item, IMAGE_KIND_SQUAREART, str(art))
    assert len(session.calls) == before


def test_get_collections_attributes_boxsets_to_collections_folder(monkeypatch):
    routes = {
        "/Library/VirtualFolders": [
            {"Name": "Movies", "ItemId": "lib1", "CollectionType": "movies"},
            {"Name": "Collections", "ItemId": "boxlib", "CollectionType": "boxsets"},
        ],
        "/Items": {"Items": [{"Id": "box1", "Type": "BoxSet", "Name": "Fast Saga"}], "TotalRecordCount": 1},
    }
    client, _ = make_client(monkeypatch, routes)
    collections = client.get_collections()
    # Without a library_key the upload cache can never mark a box set as applied
    assert [(c.title, c.library_key, c.library_name) for c in collections] == [
        ("Fast Saga", "srv123:boxlib", "Collections")
    ]


def test_find_by_provider_ids_title_pass_attributes_library(monkeypatch):
    def items_handler(params, _data):
        assert params.get("parentId") == "lib1"
        return {
            "Items": [{"Id": "a", "Type": "Movie", "Name": "Heat", "ProviderIds": {"Tmdb": "949"}}],
            "TotalRecordCount": 1,
        }

    routes = {
        "/Items": items_handler,
        "/Library/VirtualFolders": [{"Name": "Movies", "ItemId": "lib1", "CollectionType": "movies"}],
    }
    client, _ = make_client(monkeypatch, routes)
    matches = client.find_by_provider_ids({"tmdb": "949"}, "movie", library_keys=["lib1"], title="Heat")
    assert [(m.item_id, m.library_key, m.library_name) for m in matches] == [("a", "srv123:lib1", "Movies")]


def test_find_by_field_maps_label_to_tags(monkeypatch):
    def items_handler(params, _data):
        if params.get("includeItemTypes") == "BoxSet":
            assert params.get("tags") == "Overlay"
            return {"Items": [{"Id": "c1", "Type": "BoxSet", "Name": "Tagged Set"}], "TotalRecordCount": 1}
        assert params.get("tags") == "Overlay"
        assert params.get("parentId") == "lib1"
        return {"Items": [{"Id": "m1", "Type": "Movie", "Name": "Tagged Movie"}], "TotalRecordCount": 1}

    client, _ = make_client(monkeypatch, {"/Items": items_handler})
    items = client.find_by_field("label", "Overlay", "lib1")
    assert [(i.item_id, i.item_type) for i in items] == [("m1", "movie"), ("c1", "collection")]
    assert client.find_by_field("bogus", "x", "lib1") == []


def test_get_collection_items(monkeypatch):
    def items_handler(params, _data):
        assert params.get("parentId") == "box1"
        # Box-set children come back empty without a user view (verified on 10.11)
        assert params.get("userId") == "u1"
        return {"Items": [{"Id": "m1", "Type": "Movie", "Name": "Member"}], "TotalRecordCount": 1}

    routes = {"/Items": items_handler, "/Users": [{"Id": "u1", "Name": "dev"}]}
    client, session = make_client(monkeypatch, routes)
    box = jf.MediaServerItem(item_id="box1", item_type="collection", title="Set")
    assert [i.item_id for i in client.get_collection_items(box)] == ["m1"]
    client.get_collection_items(box)
    assert sum(1 for c in session.calls if c["path"] == "/Users") == 1  # user id cached


def test_image_url(monkeypatch):
    client, _ = make_client(monkeypatch, {})
    item = jf.MediaServerItem(item_id="abc", item_type="movie", title="X", thumb_path="/Items/abc/Images/Primary")
    assert client.image_url(item) == "/Items/abc/Images/Primary"
    assert client.image_url(item, max_width=400) == "/Items/abc/Images/Primary?maxWidth=400"
    assert client.image_url(jf.MediaServerItem(item_id="n", item_type="movie", title="N")) is None


def test_server_poster_is_the_first_provider_poster_or_the_recorded_one(monkeypatch):
    from util.media_server.types import MediaServerItem

    listing = {"Images": [{"Url": "https://img.example/top.jpg", "ProviderName": "TheMovieDb"}, {"Url": "https://img.example/second.jpg"}], "TotalRecordCount": 2}
    client, session = make_client(monkeypatch, {"/Items/5/RemoteImages": lambda params, _d: listing})
    downloads = []

    class Download:
        def __init__(self, url, ok=True):
            self.content = b"" if not ok else f"bytes of {url}".encode()
            self.ok = ok

        def raise_for_status(self):
            if not self.ok:
                raise jf.requests.exceptions.HTTPError("gone")

    gone = set()

    def fake_get(url, timeout=None, **kwargs):
        downloads.append(url)
        assert "headers" not in kwargs  # a plain request: the provider never sees the server's API key
        return Download(url, ok=url not in gone)

    monkeypatch.setattr(jf.requests, "get", fake_get)
    item = MediaServerItem(item_id="5", item_type="movie", title="Film")

    # No reference yet: the server's list is asked for its posters, and the first one is the source.
    assert client.server_poster(item) == ("https://img.example/top.jpg", b"bytes of https://img.example/top.jpg")
    asked = [c for c in session.calls if c["path"] == "/Items/5/RemoteImages"]
    assert len(asked) == 1 and asked[0]["params"] == {"type": "Primary"}

    # The recorded address is fetched directly, without asking the server again.
    downloads.clear()
    assert client.server_poster(item, "https://img.example/second.jpg")[0] == "https://img.example/second.jpg"
    assert downloads == ["https://img.example/second.jpg"] and len([c for c in session.calls if c["path"] == "/Items/5/RemoteImages"]) == 1

    # A recorded address that stopped working falls back to the server's current first poster.
    gone.add("https://img.example/old.jpg")
    assert client.server_poster(item, "https://img.example/old.jpg")[0] == "https://img.example/top.jpg"

    # No provider poster at all, or the provider unreachable: nothing to give.
    listing["Images"] = []
    assert client.server_poster(item) is None
    listing["Images"] = [{"Url": "https://img.example/top.jpg"}, {"Url": "https://img.example/second.jpg"}]
    gone.update({"https://img.example/top.jpg", "https://img.example/second.jpg"})
    downloads.clear()
    assert client.server_poster(item) is None and downloads == ["https://img.example/top.jpg"]  # one failure is enough


def test_restore_server_poster_is_the_ordinary_reset(monkeypatch):
    from util.media_server.types import MediaServerItem

    client, session = make_client(monkeypatch, {"/Items/5/Images/Primary": FakeResponse(status_code=204), "/Items/5/Refresh": FakeResponse(status_code=204)})
    assert client.restore_server_poster(MediaServerItem(item_id="5", item_type="movie", title="Film"), "https://img.example/top.jpg") is True
    assert [(c["method"], c["path"]) for c in session.calls if c["path"].startswith("/Items/5")] == [("DELETE", "/Items/5/Images/Primary"), ("POST", "/Items/5/Refresh")]


def test_reset_images_deletes_each_kind_and_refreshes_once(monkeypatch):
    """Several kinds of one item: every kind's images are deleted, then one refresh fetches
    fresh ones for all of them. A kind Jellyfin does not have, or could not delete, is not done."""
    from util.media_server.types import MediaServerItem

    item = MediaServerItem(item_id="5", item_type="movie", title="Film")
    ok = FakeResponse(status_code=204)
    client, session = make_client(monkeypatch, {
        "/Items": {"Items": [{"Id": "5", "Type": "Movie", "Name": "Film", "BackdropImageTags": ["a", "b"]}], "TotalRecordCount": 1},
        "/Items/5/Images/Primary": ok, "/Items/5/Images/Backdrop/0": ok, "/Items/5/Images/Backdrop/1": ok, "/Items/5/Refresh": ok})
    client.max_retries = 1
    done = client.reset_images(item, ["poster", "background", "logo", "squareart"])
    assert done == {"poster": True, "background": True, "logo": False, "squareart": False}  # no Logo route here, and no square art on Jellyfin
    calls = [(c["method"], c["path"]) for c in session.calls if c["path"].startswith("/Items")]
    assert calls == [("DELETE", "/Items/5/Images/Primary"), ("GET", "/Items"), ("DELETE", "/Items/5/Images/Backdrop/1"), ("DELETE", "/Items/5/Images/Backdrop/0"),
                     ("DELETE", "/Items/5/Images/Logo"), ("POST", "/Items/5/Refresh")]

    # Nothing could be deleted: no refresh is asked for.
    client, session = make_client(monkeypatch, {})
    client.max_retries = 1
    assert client.reset_images(item, ["poster"]) == {"poster": False}
    assert [(c["method"], c["path"]) for c in session.calls if c["path"].startswith("/Items/5")] == [("DELETE", "/Items/5/Images/Primary")]


def test_library_media_reads_resolution_range_and_audio_from_the_streams(monkeypatch):
    from util.media_server.types import MediaServerItem

    def streams(width, height, range_type, audio):
        return [{"Type": "Video", "Width": width, "Height": height, "VideoRange": "SDR" if range_type == "SDR" else "HDR", "VideoRangeType": range_type}] + [
            {"Type": "Audio", "DisplayTitle": title, "Codec": codec, "Profile": profile} for title, codec, profile in audio]

    rows = [
        {"Id": "m1", "Type": "Movie", "Path": "/m/Film [DV HDR10].mkv", "MediaStreams": streams(3840, 1600, "DOVIWithHDR10", [("English - TrueHD - 7.1", "truehd", "Dolby TrueHD + Dolby Atmos")])},
        {"Id": "m2", "Type": "Movie", "Path": "/m/Old.avi", "MediaStreams": streams(720, 480, "SDR", [("English - MP3 - Stereo", "mp3", None)])},
        {"Id": "e1", "Type": "Episode", "SeriesId": "s1", "SeasonId": "s1a", "Path": "/tv/S01E01.mkv", "MediaStreams": streams(1920, 1080, "HDR10", [("English - Dolby Digital+ - 5.1", "eac3", None)])},
        {"Id": "e2", "Type": "Episode", "SeriesId": "s1", "SeasonId": "s1a", "LocationType": "Virtual", "MediaStreams": []},  # a missing episode: nothing on disk
    ]
    asked = []

    def items(params, _d):
        asked.append(dict(params))
        wanted = set(str(params.get("includeItemTypes", "Movie,Episode")).split(","))
        ids = set(str(params.get("ids", "")).split(",")) - {""}
        found = [r for r in rows if r["Type"] in wanted and (not ids or r["Id"] in ids)]
        return {"Items": found if params.get("startIndex", 0) == 0 else [], "TotalRecordCount": len(found)}

    client, _session = make_client(monkeypatch, {"/Items": items})
    media = client.library_media("srv123:lib1")
    assert asked[0]["parentId"] == "lib1" and asked[0]["fields"] == "MediaStreams,Path" and asked[0]["includeItemTypes"] == "Movie,Episode"
    assert media["m1"] == [{"resolution": "4k", "hdr": True, "dovi": True, "path": "/m/Film [DV HDR10].mkv",
                            "audio_titles": ["English - TrueHD - 7.1", "TRUEHD Dolby TrueHD + Dolby Atmos"]}]
    assert (media["m2"][0]["resolution"], media["m2"][0]["hdr"], media["m2"][0]["audio_titles"]) == ("480", False, ["English - MP3 - Stereo", "MP3"])
    # An episode sits under its show and its season; the codec is added in the form the file-name patterns expect.
    assert media["s1"] == media["s1a"] and len(media["s1"]) == 1 and media["s1"][0]["audio_titles"] == ["English - Dolby Digital+ - 5.1", "EAC3"]
    assert (media["s1"][0]["resolution"], media["s1"][0]["hdr"], media["s1"][0]["dovi"]) == ("1080", True, False)
    assert "e2" not in media

    asked.clear()
    assert client.item_media(MediaServerItem(item_id="m2", item_type="movie", title="Old"))[0]["path"] == "/m/Old.avi" and asked[-1]["ids"] == "m2"
    assert [f["path"] for f in client.item_media(MediaServerItem(item_id="s1", item_type="show", title="Show"))] == ["/tv/S01E01.mkv"]
    assert asked[-1]["parentId"] == "s1" and asked[-1]["includeItemTypes"] == "Episode"
    assert client.item_media(MediaServerItem(item_id="c1", item_type="collection", title="Box")) == []

