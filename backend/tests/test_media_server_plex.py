from datetime import datetime

from util.media_server.plex import PlexClient, extract_provider_ids
from util.media_server.types import (
    IMAGE_KIND_POSTER,
    IMAGE_KIND_SQUAREART,
    MediaServerItem,
)


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeLabel:
    def __init__(self, tag):
        self.tag = tag


class FakeItem:
    def __init__(self, **attrs):
        self.uploads = []
        self.removed_labels = []
        for key, value in attrs.items():
            setattr(self, key, value)

    def uploadPoster(self, filepath=None):
        self.uploads.append(("uploadPoster", filepath))

    def uploadSquareArt(self, filepath=None):
        self.uploads.append(("uploadSquareArt", filepath))

    def removeLabel(self, label):
        self.removed_labels.append(label)


class FakeSection:
    def __init__(self, key, title, section_type, items=None, collections=None):
        self.key = key
        self.title = title
        self.type = section_type
        self.items = items or []
        self.collections_list = collections or []

    def all(self, includeGuids=None):
        return self.items

    def collections(self):
        return self.collections_list

    def search(self, guid=None, title=None):
        if guid is not None:
            return [i for i in self.items if guid in [g.id for g in getattr(i, "guids", [])]]
        if title is not None:
            return [i for i in self.items if getattr(i, "title", "") == title]
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

    def search(self, query, mediatype=None, limit=None):
        results = []
        for section in self.library.sections():
            results.extend(i for i in section.items if query.lower() in getattr(i, "title", "").lower())
        return results


def make_client(sections):
    return PlexClient("http://plex.local", "token", server=FakeServer(sections))


def make_movie(**overrides):
    attrs = {
        "ratingKey": 101,
        "type": "movie",
        "title": "Heat",
        "year": 1995,
        "guids": [FakeGuid("tmdb://949"), FakeGuid("imdb://tt0113277")],
        "librarySectionID": 1,
        "librarySectionKey": "/library/sections/1",
        "librarySectionTitle": "Movies",
        "editionTitle": "Director's Cut",
        "locations": ["/movies/Heat (1995)/Heat.mkv"],
        "labels": [FakeLabel("Overlay")],
        "addedAt": datetime(2026, 1, 2, 3, 4, 5),
    }
    attrs.update(overrides)
    return FakeItem(**attrs)


def test_extract_provider_ids_guids_and_attrs():
    item = FakeItem(guids=[FakeGuid("tmdb://949"), FakeGuid("junk")], imdbid="tt001", tvdbid=0)
    assert extract_provider_ids(item) == {"tmdb": "949", "imdb": "tt001"}


def test_parse_item_fields():
    client = make_client([])
    parsed = client._parse_item(make_movie())
    assert parsed.item_id == "101"
    assert parsed.item_type == "movie"
    assert parsed.provider_ids == {"tmdb": "949", "imdb": "tt0113277"}
    # librarySectionKey preferred over librarySectionID, same as _item_library_key
    assert parsed.library_key == "machine1:/library/sections/1"
    assert parsed.library_name == "Movies"
    assert parsed.edition_title == "Director's Cut"
    assert parsed.tags == ["Overlay"]
    assert parsed.added_at == "2026-01-02T03:04:05"


def test_find_by_provider_ids_guid_search():
    movie = make_movie()
    other = make_movie(ratingKey=102, title="Heat", guids=[FakeGuid("tmdb://555")])
    section = FakeSection(1, "Movies", "movie", items=[movie, other])
    client = make_client([section, FakeSection(2, "TV", "show")])
    matches = client.find_by_provider_ids({"tmdb": "949"}, "movie")
    assert [m.item_id for m in matches] == ["101"]


def test_find_by_title_returns_unconfirmed_matches():
    # Legacy Plex agents expose no guids; title search must NOT require id confirmation
    legacy = make_movie(guids=[])
    other_year = make_movie(ratingKey=103, guids=[], year=2013)
    section = FakeSection(1, "Movies", "movie", items=[legacy, other_year])
    client = make_client([section, FakeSection(2, "TV", "show")])
    matches = client.find_by_title("Heat", "movie")
    assert sorted(m.item_id for m in matches) == ["101", "103"]
    assert client.find_by_title("Heat", "movie", library_keys=["2"]) == []


def test_find_by_provider_ids_respects_library_selection():
    movie = make_movie()
    section_a = FakeSection(1, "Movies", "movie", items=[movie])
    section_b = FakeSection(2, "Movies 4K", "movie", items=[make_movie(ratingKey=201)])
    client = make_client([section_a, section_b])
    matches = client.find_by_provider_ids({"tmdb": "949"}, "movie", library_keys=["2"])
    assert [m.item_id for m in matches] == ["201"]


def test_get_collections_smart_flag():
    coll = FakeItem(ratingKey=300, type="collection", title="James Bond", smart=True)
    section = FakeSection(1, "Movies", "movie", collections=[coll])
    client = make_client([section, FakeSection(3, "Music", "artist")])
    collections = client.get_collections()
    assert [c.title for c in collections] == ["James Bond"]
    assert collections[0].smart is True
    assert collections[0].library_name == "Movies"


def test_upload_image_dispatch():
    client = make_client([])
    native = make_movie()
    item = MediaServerItem(item_id="101", item_type="movie", title="Heat", native=native)
    assert client.upload_image(item, IMAGE_KIND_POSTER, "/tmp/p.png")
    assert client.upload_image(item, IMAGE_KIND_SQUAREART, "/tmp/s.png")
    assert native.uploads == [("uploadPoster", "/tmp/p.png"), ("uploadSquareArt", "/tmp/s.png")]
    assert not client.upload_image(item, "bogus", "/tmp/x.png")


def test_remove_label_only_when_present():
    client = make_client([])
    native = make_movie()
    item = MediaServerItem(item_id="101", item_type="movie", title="Heat", native=native)
    assert client.remove_label(item, "Overlay")
    assert native.removed_labels == ["Overlay"]
    assert not client.remove_label(item, "Missing")
    assert native.removed_labels == ["Overlay"]


def test_get_libraries_and_seasons():
    show_native = FakeItem(
        ratingKey=400,
        type="show",
        title="The Wire",
        guids=[FakeGuid("tvdb://79126")],
        seasons_list=[
            FakeItem(ratingKey=401, type="season", title="Season 1", index=1, parentTitle="The Wire"),
        ],
    )
    show_native.seasons = lambda: show_native.seasons_list
    section = FakeSection(2, "TV", "show", items=[show_native])
    client = make_client([section])
    assert [(l.key, l.type) for l in client.get_libraries()] == [("2", "show")]
    show = client.get_library_items("2")[0]
    seasons = client.get_seasons(show)
    assert [(s.item_id, s.index, s.parent_title) for s in seasons] == [("401", 1, "The Wire")]
