from datetime import datetime
from xml.etree.ElementTree import Element, SubElement

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
        for key, value in attrs.items():
            setattr(self, key, value)


class FakeSection:
    def __init__(self, key, title, section_type, items=None, collections=None):
        self.key = key
        self.title = title
        self.type = section_type
        self.items = items or []
        self.collections_list = collections or []
        self.search_calls = []

    def collections(self):
        return self.collections_list

    def search(self, libtype=None, guid=None, title=None, label=None, container_start=0, maxresults=None, **kwargs):
        self.search_calls.append({"libtype": libtype, "label": label, "container_start": container_start, "maxresults": maxresults})
        if libtype == "collection":
            rows = list(self.collections_list)
        elif libtype == "season":
            rows = [season for show in self.items for season in getattr(show, "seasons_list", [])]
        else:
            rows = list(self.items)
        if guid is not None:
            rows = [i for i in rows if guid in [g.id for g in getattr(i, "guids", [])]]
        if title is not None:
            rows = [i for i in rows if getattr(i, "title", "") == title]
        if label is not None:
            rows = [i for i in rows if label in [lab.tag for lab in getattr(i, "labels", [])]]
        rows = rows[container_start:]
        return rows[:maxresults] if maxresults is not None else rows


class FakeLibrary:
    def __init__(self, sections):
        self._sections = sections

    def sections(self):
        return self._sections


class FakeSession:
    def post(self, *args, **kwargs):
        return None

    def put(self, *args, **kwargs):
        return None


class FakeServer:
    def __init__(self, sections):
        self.machineIdentifier = "machine1"
        self.friendlyName = "Test Plex"
        self.version = "1.41.0"
        self.library = FakeLibrary(sections)
        self.calls = []  # (key, http method name, data)
        self._session = FakeSession()

    def _all_raw(self):
        for section in self.library.sections():
            yield from section.items
            yield from section.collections_list
            for show in section.items:
                yield from getattr(show, "seasons_list", [])

    def _raw(self, rating_key):
        return next((i for i in self._all_raw() if str(getattr(i, "ratingKey", "")) == str(rating_key)), None)

    def query(self, key, method=None, data=None, headers=None, params=None):
        self.calls.append((key, getattr(method, "__name__", None), data))
        root = Element("MediaContainer")
        if method is None and key.startswith("/library/metadata/") and "/" not in key[len("/library/metadata/"):]:
            for rating_key in key[len("/library/metadata/"):].split(","):
                raw = self._raw(rating_key)
                if raw is None:
                    continue
                el = SubElement(root, "Directory", ratingKey=rating_key)
                # Full metadata carries Location; the fakes keep it apart from the listing row
                for path in getattr(raw, "full_locations", None) or []:
                    SubElement(el, "Location", path=str(path))
        return root

    def fetchItems(self, key, cls=None, **kwargs):
        prefix = "/library/metadata/"
        if key.startswith(prefix) and "/children" in key:
            raw = self._raw(key[len(prefix):].split("/")[0])
            return list(getattr(raw, "seasons_list", []) or [])
        return []

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


def test_extract_provider_ids_reads_legacy_agent_guids():
    # HAMA keys its TVDB-mapped shows in the legacy guid string and exposes no Guid children.
    hama = FakeItem(guids=[], guid="com.plexapp.agents.hama://tvdb-267440?lang=en")
    assert extract_provider_ids(hama) == {"tvdb": "267440"}
    assert extract_provider_ids(FakeItem(guids=[], guid="com.plexapp.agents.hama://tvdb2-267440?lang=en")) == {"tvdb": "267440"}
    assert extract_provider_ids(FakeItem(guids=[], guid="com.plexapp.agents.hama://tsdb-1429?lang=en")) == {"tmdb": "1429"}
    assert extract_provider_ids(FakeItem(guids=[], guid="com.plexapp.agents.hama://tmdb-949?lang=en")) == {"tmdb": "949"}
    assert extract_provider_ids(FakeItem(guids=[], guid="com.plexapp.agents.hama://imdb-tt0113277?lang=en")) == {"imdb": "tt0113277"}
    # AniDB-keyed HAMA shows have no TVDB equivalent in the guid; they stay id-less.
    assert extract_provider_ids(FakeItem(guids=[], guid="com.plexapp.agents.hama://anidb-10261?lang=en")) == {}
    # The retired first-party agents.
    assert extract_provider_ids(FakeItem(guids=[], guid="com.plexapp.agents.thetvdb://79126?lang=en")) == {"tvdb": "79126"}
    assert extract_provider_ids(FakeItem(guids=[], guid="com.plexapp.agents.themoviedb://949?lang=en")) == {"tmdb": "949"}
    assert extract_provider_ids(FakeItem(guids=[], guid="com.plexapp.agents.imdb://tt0113277?lang=en")) == {"imdb": "tt0113277"}
    # Modern and unmatched guids contribute nothing through this path.
    assert extract_provider_ids(FakeItem(guids=[], guid="plex://movie/5d7768274de0ee001fcc8cc8")) == {}
    assert extract_provider_ids(FakeItem(guids=[], guid="local://1234")) == {}
    assert extract_provider_ids(FakeItem(guids=[], guid="com.plexapp.agents.none://1234?lang=en")) == {}


def test_legacy_guid_never_overrides_a_modern_guid():
    item = FakeItem(guids=[FakeGuid("tvdb://999")], guid="com.plexapp.agents.thetvdb://79126?lang=en")
    assert extract_provider_ids(item) == {"tvdb": "999"}


def test_parse_item_uses_legacy_guid_for_provider_ids():
    client = make_client([])
    parsed = client._parse_item(make_movie(guids=[], guid="com.plexapp.agents.hama://tvdb-267440?lang=en"))
    assert parsed.provider_ids == {"tvdb": "267440"}


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
    assert parsed.original_title is None


def test_parse_item_carries_original_title():
    client = make_client([])
    parsed = client._parse_item(make_movie(title="Seven", originalTitle="Se7en"))
    assert parsed.title == "Seven" and parsed.original_title == "Se7en"
    assert client._parse_item(make_movie(originalTitle="")).original_title is None


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


def test_upload_image_posts_to_the_rating_key(tmp_path):
    client = make_client([])
    poster = tmp_path / "p.png"
    poster.write_bytes(b"poster-bytes")
    square = tmp_path / "s.png"
    square.write_bytes(b"square-bytes")
    # No plexapi object needed: the card's id is enough
    item = MediaServerItem(item_id="101", item_type="movie", title="Heat")
    assert client.upload_image(item, IMAGE_KIND_POSTER, str(poster))
    assert client.upload_image(item, IMAGE_KIND_SQUAREART, str(square))
    assert client.server.calls == [
        ("/library/metadata/101/posters", "post", b"poster-bytes"),
        ("/library/metadata/101/squareArts", "post", b"square-bytes"),
    ]
    assert not client.upload_image(item, "bogus", str(poster))
    assert not client.upload_image(MediaServerItem(item_id="", item_type="movie", title="?"), IMAGE_KIND_POSTER, str(poster))


def test_remove_label_only_when_present():
    section = FakeSection(1, "Movies", "movie", items=[make_movie(), make_movie(ratingKey=102, title="Thief", labels=[])])
    client = make_client([section])
    heat, thief = client.get_library_items("1")
    assert client.remove_label(heat, "Overlay")
    assert client.server.calls[-1] == (
        "/library/sections/1/all?id=101&label.locked=1&label[].tag.tag-=Overlay&type=1",
        "put",
        None,
    )
    assert not client.remove_label(heat, "Overlay")  # gone now
    assert not client.remove_label(heat, "Missing")
    assert not client.remove_label(thief, "Overlay")
    assert len([c for c in client.server.calls if c[1] == "put"]) == 1


def test_labeled_item_ids_covers_shows_seasons_and_collections():
    season = FakeItem(ratingKey=401, type="season", title="Season 1", index=1, labels=[FakeLabel("Overlay")])
    show = FakeItem(ratingKey=400, type="show", title="The Wire", labels=[FakeLabel("Overlay")], seasons_list=[season])
    plain = FakeItem(ratingKey=500, type="show", title="Plain", labels=[], seasons_list=[])
    collection = FakeItem(ratingKey=900, type="collection", title="HBO", labels=[FakeLabel("Overlay")])
    section = FakeSection(2, "TV", "show", items=[show, plain], collections=[collection])
    client = make_client([section])
    assert client.labeled_item_ids("2", "Overlay") == {"400", "401", "900"}
    assert [c["libtype"] for c in section.search_calls] == ["show", "season", "collection"]
    assert client.labeled_item_ids("machine1:2", "Overlay") == {"400", "401", "900"}
    assert client.labeled_item_ids("2", "Other") == set()


def test_get_library_items_pages_and_keeps_only_the_card():
    rows = [make_movie(ratingKey=100 + n, title=f"Movie {n}") for n in range(5)]
    section = FakeSection(1, "Movies", "movie", items=rows)
    client = make_client([section])
    client.LISTING_PAGE = 2
    items = client.get_library_items("1")
    assert [i.item_id for i in items] == ["100", "101", "102", "103", "104"]
    assert [(c["container_start"], c["maxresults"]) for c in section.search_calls] == [(0, 2), (2, 2), (4, 2)]
    assert all(i.native is None for i in items)
    assert all(row._autoReload is False for row in rows)
    assert items[0].paths == ["/movies/Heat (1995)/Heat.mkv"]


def test_get_libraries_and_seasons():
    show_native = FakeItem(
        ratingKey=400,
        type="show",
        title="The Wire",
        guids=[FakeGuid("tvdb://79126")],
        librarySectionID=2,
        full_locations=["/tv/The Wire (2002)"],
        seasons_list=[
            FakeItem(ratingKey=401, type="season", title="Season 1", index=1, parentTitle="The Wire"),
        ],
    )
    section = FakeSection(2, "TV", "show", items=[show_native])
    client = make_client([section])
    assert [(l.key, l.type) for l in client.get_libraries()] == [("2", "show")]
    show = client.get_library_items("2")[0]
    assert show.native is None
    assert show.paths == ["/tv/The Wire (2002)"]
    seasons = client.get_seasons(show)
    # Listing rows only carry the section id; the key keeps the full-object form upload records use
    assert show.library_key == "machine1:/library/sections/2"
    assert [(s.item_id, s.index, s.parent_title, s.library_key) for s in seasons] == [("401", 1, "The Wire", "machine1:/library/sections/2")]
    assert client.get_seasons(MediaServerItem(item_id="", item_type="show", title="?")) == []


def test_get_library_items_fills_show_paths_in_one_batch():
    shows = [FakeItem(ratingKey=400 + n, type="show", title=f"Show {n}", full_locations=[f"/tv/Show {n} (2000)"]) for n in range(3)]
    shows.append(FakeItem(ratingKey=499, type="show", title="No files", full_locations=[]))
    section = FakeSection(2, "TV", "show", items=shows)
    client = make_client([section])
    items = client.get_library_items("2")
    # Listings carry no Location for shows; one multi-key metadata request fills them
    assert [c[0] for c in client.server.calls] == ["/library/metadata/400,401,402,499"]
    assert [i.paths for i in items] == [["/tv/Show 0 (2000)"], ["/tv/Show 1 (2000)"], ["/tv/Show 2 (2000)"], []]
