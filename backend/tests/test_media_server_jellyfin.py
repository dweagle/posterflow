import base64
from urllib.parse import urlparse

import pytest

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
    rows = [{"Id": f"i{n}", "Type": "Movie", "Name": f"M{n}"} for n in range(3)]

    def items_handler(params, _data):
        # v12 folds box-set members out of listings unless explicitly disabled
        assert params.get("collapseBoxSetItems") == "false"
        start = int(params.get("startIndex", 0))
        return {"Items": rows[start:start + 2], "TotalRecordCount": len(rows)}

    client, _ = make_client(monkeypatch, {"/Items": items_handler})
    items = client.get_library_items("lib1")
    assert [i.item_id for i in items] == ["i0", "i1", "i2"]
    assert items[0].library_key == "srv123:lib1"


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
