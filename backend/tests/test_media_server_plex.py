from datetime import datetime
from xml.etree.ElementTree import Element, SubElement

from util.media_server.plex import NO_IMAGE_SELECTED, PlexClient, extract_provider_ids
from util.media_server.types import (
    IMAGE_KIND_BACKGROUND,
    IMAGE_KIND_LOGO,
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

    def matches(self, agent=None, title=None, **kwargs):
        # Plex's match endpoint: "tmdb-949" → the plex:// guid of the item carrying that id
        wanted = str(title or "").replace("-", "://", 1)
        section_items = getattr(getattr(self, "_section", None), "items", [])
        hits = [i for i in section_items if wanted in [g.id for g in getattr(i, "guids", [])]]
        return [FakeItem(guid=getattr(i, "guid", "")) for i in hits]


class FakeSection:
    def __init__(self, key, title, section_type, items=None, collections=None):
        self.key = key
        self.title = title
        self.type = section_type
        self.items = items or []
        self.collections_list = collections or []
        self.search_calls = []
        for item in self.items:
            item._section = self

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
            # like Plex: only the primary guid is filterable, never the tmdb/tvdb/imdb Guid children
            rows = [i for i in rows if getattr(i, "guid", None) == guid]
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

    def delete(self, *args, **kwargs):
        return None


class FakeServer:
    def __init__(self, sections):
        self.machineIdentifier = "machine1"
        self.friendlyName = "Test Plex"
        self.version = "1.41.0"
        self.library = FakeLibrary(sections)
        self.calls = []  # (key, http method name, data)
        self.fetches = []  # keys asked for as lists of items
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
            for rating_key in key[len("/library/metadata/"):].split("?")[0].split(","):
                raw = self._raw(rating_key)
                if raw is None:
                    continue
                el = SubElement(root, "Directory", ratingKey=rating_key)
                # Full metadata carries Location; the fakes keep it apart from the listing row
                for path in getattr(raw, "full_locations", None) or []:
                    SubElement(el, "Location", path=str(path))
                for name in getattr(raw, "full_locked", None) or []:
                    SubElement(el, "Field", name=name, locked="1")
                for tag in getattr(raw, "full_labels", None) or []:
                    SubElement(el, "Label", tag=tag)
        return root

    def fetchItems(self, key, cls=None, **kwargs):
        self.fetches.append(key)
        if key.startswith("/library/collections/"):
            raw = self._raw(key.split("/")[3])
            return list(getattr(raw, "members", []) or [])
        if key.startswith("/library/all?id="):
            raw = self._raw(key.split("id=")[1].split("&")[0])
            return [raw] if raw is not None else []
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
    attrs.setdefault("guid", f"plex://movie/{attrs['ratingKey']}")
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


def test_find_by_provider_ids_translates_external_id_to_plex_guid():
    movie = make_movie()
    other = make_movie(ratingKey=102, title="Heat", guids=[FakeGuid("tmdb://555")])
    section = FakeSection(1, "Movies", "movie", items=[movie, other])
    client = make_client([section, FakeSection(2, "TV", "show")])
    # The external id never hits Plex's guid filter directly; it goes through matches() first
    assert section.search(guid="tmdb://949") == []
    assert [m.item_id for m in client.find_by_provider_ids({"tmdb": "949"}, "movie")] == ["101"]
    assert [m.item_id for m in client.find_by_provider_ids({"imdb": "tt0113277"}, "movie")] == ["101"]
    assert client.find_by_provider_ids({"tmdb": "999"}, "movie") == []


def _row(loaded: bool) -> Element:
    # A library row as Plex delivers it: updatedAt appears once the item's details are loaded
    return Element("Video", type="movie", **({"updatedAt": "1794461217"} if loaded else {}))


def test_an_item_plex_is_still_loading_is_read_as_delivered():
    loading = make_movie(ratingKey=301, title="New", guids=[], _data=_row(False), _autoReload=True)
    ready = make_movie(ratingKey=302, title="New", _data=_row(True), _autoReload=True)
    section = FakeSection(1, "Movies", "movie", items=[loading, ready])
    client = make_client([section])

    found = {m.item_id: m for m in client.find_by_title("New", "movie")}

    # plexapi would refetch the item on its empty guid list; that fetch is what damages a loading item
    assert found["301"].loading and found["301"].native is None and loading._autoReload is False
    assert not found["302"].loading and found["302"].native is ready and ready._autoReload is True
    assert not any(key.startswith("/library/metadata/") for key, _method, _data in client.server.calls)


def test_the_id_search_holds_a_loading_item_it_finds_by_its_plex_guid():
    # The agent already matched the new item (plex:// guid) but its tmdb/imdb ids are not written yet
    ready = make_movie(ratingKey=302, guid="plex://movie/abc", _data=_row(True))
    loading = make_movie(ratingKey=301, guid="plex://movie/abc", guids=[], _data=_row(False), _autoReload=True)
    client = make_client([FakeSection(1, "Movies", "movie", items=[ready, loading])])

    found = {m.item_id: m.loading for m in client.find_by_provider_ids({"tmdb": "949"}, "movie")}

    assert found == {"302": False, "301": True}
    assert loading._autoReload is False


def test_item_is_loading_asks_the_library_listing_not_the_item():
    loading = make_movie(ratingKey=301, guids=[], _data=_row(False))
    ready = make_movie(ratingKey=302, _data=_row(True))
    client = make_client([FakeSection(1, "Movies", "movie", items=[loading, ready])])

    assert client.item_is_loading("301") is True
    assert client.item_is_loading("302") is False
    assert client.item_is_loading("999") is False
    assert client.server.fetches == [f"/library/all?id={key}&includeGuids=1" for key in ("301", "302", "999")]
    assert client.server.calls == []


def test_the_probe_item_is_never_one_plex_is_still_loading():
    loading = make_movie(ratingKey=301, title="A New One", guids=[], _data=_row(False))
    ready = make_movie(ratingKey=302, _data=_row(True))
    section = FakeSection(1, "Movies", "movie", items=[loading, ready])
    client = make_client([section])

    assert client._section_probe_item(section) is ready

    only_loading = FakeSection(2, "New Library", "movie", items=[make_movie(ratingKey=401, guids=[], _data=_row(False))])
    assert client._section_probe_item(only_loading) is None
    only_loading.items[0]._data.set("updatedAt", "1794461217")
    assert client._section_probe_item(only_loading) is only_loading.items[0]  # asked again, not remembered as empty


def test_find_by_provider_ids_handles_empty_section():
    client = make_client([FakeSection(1, "Movies", "movie", items=[])])
    assert client.find_by_provider_ids({"tmdb": "949"}, "movie") == []


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


def test_get_collections_asks_for_locks_and_labels_in_batches():
    """A listing row carries neither the poster lock nor the labels: both come a batch at a
    time, never one request per collection."""
    rows = [FakeItem(ratingKey=300 + n, type="collection", title=f"Set {n}") for n in range(5)]
    rows[1].full_locked, rows[1].full_labels = ["thumb"], ["Overlay", "Pinned"]
    rows[4].full_locked = ["art"]
    client = make_client([FakeSection(1, "Movies", "movie", collections=rows)])
    client.METADATA_BATCH = 2
    listed = client.get_collections("1")
    assert [key.split("?")[0] for key, _method, _data in client.server.calls] == [
        "/library/metadata/300,301", "/library/metadata/302,303", "/library/metadata/304"]
    assert [c.thumb_locked for c in listed] == [False, True, False, False, False]
    assert [c.tags for c in listed] == [[], ["Overlay", "Pinned"], [], [], []]
    assert all(c.native is None for c in listed)


def test_get_collections_keeps_the_listing_when_the_details_cannot_be_read():
    client = make_client([FakeSection(1, "Movies", "movie", collections=[FakeItem(ratingKey=300, type="collection", title="Bond")])])

    def down(*args, **kwargs):
        raise ConnectionError("down")

    client.server.query = down
    listed = client.get_collections("1")
    assert [(c.title, c.thumb_locked, c.tags) for c in listed] == [("Bond", None, [])]


def test_get_collection_items_lists_the_members_in_one_request():
    """Members come as listing rows from the collection's id: the collection itself is not
    fetched and no member costs a request of its own."""
    members = [make_movie(ratingKey=101, title="Dr. No"), make_movie(ratingKey=102, title="Goldfinger")]
    bond = FakeItem(ratingKey=300, type="collection", title="Bond", members=members)
    client = make_client([FakeSection(1, "Movies", "movie", collections=[bond])])
    listed = client.get_collections("1")
    client.server.calls.clear()
    client.server.fetchItem = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("the collection is not fetched"))
    found = client.get_collection_items(listed[0])
    assert [(m.title, m.library_name, m.native) for m in found] == [("Dr. No", "Movies", None), ("Goldfinger", "Movies", None)]
    assert found[0].provider_ids == client._parse_item(members[0]).provider_ids != {}
    assert client.server.fetches == ["/library/collections/300/children?includeGuids=1"] and client.server.calls == []
    assert all(m._autoReload is False for m in members)

    # No answer is not an empty collection.
    def down(*args, **kwargs):
        raise ConnectionError("down")

    client.server.fetchItems = down
    try:
        client.get_collection_items(listed[0])
        raise AssertionError("a failed listing must raise")
    except ConnectionError:
        pass
    assert client.get_collection_items(MediaServerItem(item_id="", item_type="collection", title="X")) == []


def test_find_by_field_reads_listing_rows():
    """A label or genre search takes the rows as listed, items first and then the collections
    carrying it: a full parse would cost one request per match."""
    elf = make_movie(ratingKey=101, title="Elf", labels=[FakeLabel("Christmas")])
    heat = make_movie(ratingKey=102, title="Heat", labels=[])
    xmas = FakeItem(ratingKey=300, type="collection", title="Christmas Movies", labels=[FakeLabel("Christmas")])
    section = FakeSection(1, "Movies", "movie", items=[elf, heat], collections=[xmas])
    client = make_client([section])
    found = client.find_by_field("label", "Christmas", "1")
    assert [(i.title, i.item_type, i.native) for i in found] == [("Elf", "movie", None), ("Christmas Movies", "collection", None)]
    assert found[0].provider_ids == client._parse_item(elf).provider_ids != {}
    assert elf._autoReload is False and xmas._autoReload is False
    assert [(c["libtype"], c["label"], c["maxresults"]) for c in section.search_calls] == [(None, "Christmas", 500), ("collection", "Christmas", 500)]
    assert client.server.calls == [] and client.server.fetches == []
    assert [i.title for i in client.find_by_field("label", "Christmas", "1", include_collections=False)] == ["Elf"]
    assert client.find_by_field("bogus", "x", "1") == [] and client.find_by_field("label", "Christmas", "nope") == []


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


def _listed(item_id, library, item_type="movie"):
    return MediaServerItem(item_id=str(item_id), item_type=item_type, title=f"Item {item_id}", library_key=f"machine1:/library/sections/{library}")


def test_unlock_images_takes_one_edit_per_library_and_item_type():
    """Plex unlocks the image fields of many items in one library edit. The items are grouped
    by library and item type, every wanted kind goes in the same request, and the ids of the
    items that could not be unlocked come back."""
    client = make_client([FakeSection(1, "Movies", "movie"), FakeSection(2, "TV", "show")])
    items = [_listed(101, 1), _listed(102, 1), _listed(201, 2, "show"), _listed(211, 2, "season"), _listed(301, 9), _listed(401, 1, "episode"),
             MediaServerItem(item_id="501", item_type="movie", title="No library")]
    failed = client.unlock_images(items, [IMAGE_KIND_POSTER, IMAGE_KIND_LOGO])
    assert failed == {"301", "401", "501"}  # an unknown library, an item type with no edit, no library at all
    assert client.server.calls == [
        ("/library/sections/1/all?type=1&id=101%2C102&thumb.locked=0&clearLogo.locked=0", "put", None),
        ("/library/sections/2/all?type=2&id=201&thumb.locked=0&clearLogo.locked=0", "put", None),
        ("/library/sections/2/all?type=3&id=211&thumb.locked=0&clearLogo.locked=0", "put", None),
    ]

    # A long list goes in several requests; one Plex refuses fails just its own ids.
    client = make_client([FakeSection(1, "Movies", "movie")])
    client.EDIT_BATCH = 2
    answer = client.server.query

    def refuse_second(key, method=None, **kwargs):
        if "id=3%2C4" in key:
            raise RuntimeError("bad request")
        return answer(key, method=method, **kwargs)

    client.server.query = refuse_second
    assert client.unlock_images([_listed(n, 1) for n in range(1, 6)], [IMAGE_KIND_SQUAREART]) == {"3", "4"}
    assert [key for key, _method, _data in client.server.calls] == [
        "/library/sections/1/all?type=1&id=1%2C2&squareArt.locked=0", "/library/sections/1/all?type=1&id=5&squareArt.locked=0"]


def test_reset_images_selects_plexs_own_image_without_fetching_the_item():
    """Each kind costs a list of the item's images and one select of the first provider image.
    A kind with uploads only has its selection deleted, and one refresh follows for all of
    those. The item itself is never fetched, and one kind failing leaves the others alone."""
    class Image:
        def __init__(self, key, picked):
            self.ratingKey, self._picked = key, picked

        def select(self):
            self._picked.append(self.ratingKey)

    picked, listed = [], []
    lists = {
        "/library/metadata/101/posters": lambda: [Image("upload://posters/abc", picked), Image("metadata://posters/tmdb", picked), Image("metadata://posters/other", picked)],
        "/library/metadata/101/arts": lambda: [Image("upload://art/def", picked)],
        "/library/metadata/101/clearLogos": lambda: (_ for _ in ()).throw(RuntimeError("timeout")),
        "/library/metadata/101/squareArts": lambda: [],
    }

    def fetch(key, cls=None, **kwargs):
        listed.append(key)
        return lists[key]()

    client = make_client([FakeSection(1, "Movies", "movie")])
    client.server.fetchItems = fetch
    client.server.fetchItem = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("the item is not fetched"))
    kinds = [IMAGE_KIND_POSTER, IMAGE_KIND_BACKGROUND, IMAGE_KIND_LOGO, IMAGE_KIND_SQUAREART]
    done = client.reset_images(_listed(101, 1), kinds, unlocked=True)
    assert done == {IMAGE_KIND_POSTER: True, IMAGE_KIND_BACKGROUND: True, IMAGE_KIND_LOGO: False, IMAGE_KIND_SQUAREART: True}
    assert picked == ["metadata://posters/tmdb"] and listed == list(lists)
    assert client.server.calls == [  # no unlock: the batch did that
        ("/library/metadata/101/art", "delete", None), ("/library/metadata/101/squareArt", "delete", None), ("/library/metadata/101/refresh", "put", None)]

    # On its own the reset unlocks first, and an item that stays locked is left as it is.
    client = make_client([FakeSection(1, "Movies", "movie")])
    client.server.fetchItems = lambda key, cls=None, **kw: [Image("metadata://posters/tmdb", picked)]
    assert client.reset_image(_listed(102, 1), IMAGE_KIND_POSTER) is True
    assert client.server.calls == [("/library/sections/1/all?type=1&id=102&thumb.locked=0", "put", None)] and picked[-1] == "metadata://posters/tmdb"
    assert client.reset_image(_listed(103, 9), IMAGE_KIND_POSTER) is False and len(client.server.calls) == 1

    # A collection refreshes through its own address, as Plex wants it.
    client = make_client([FakeSection(1, "Movies", "movie")])
    client.server.fetchItems = lambda key, cls=None, **kw: []
    assert client.reset_images(_listed(900, 1, "collection"), [IMAGE_KIND_POSTER], unlocked=True) == {IMAGE_KIND_POSTER: True}
    assert client.server.calls == [("/library/metadata/900/thumb", "delete", None), ("/library/collections/900/refresh", "put", None)]


def test_selected_image_key_tells_a_blank_poster_from_no_answer():
    """Plex listing the item's posters with none selected shows no poster at all: that is an
    answer, unlike a lookup that fails."""
    class Image:
        def __init__(self, key, selected=False):
            self.ratingKey, self.selected = key, selected

    client = make_client([])
    item = MediaServerItem(item_id="101", item_type="movie", title="Heat")
    images = [Image("metadata://posters/agent"), Image("upload://posters/abc", selected=True)]
    client.server.fetchItems = lambda key, cls=None, **kw: images
    assert client.selected_image_key(item, IMAGE_KIND_POSTER) == "upload://posters/abc"
    images[1].selected = False
    assert client.selected_image_key(item, IMAGE_KIND_POSTER) == NO_IMAGE_SELECTED
    client.server.fetchItems = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down"))
    assert client.selected_image_key(item, IMAGE_KIND_POSTER) == ""


def test_shared_copies_asks_each_title_then_lists_every_library_once():
    """A title in two libraries (Movies and 4K Movies) is two items with ONE bundle folder. A run
    touching a few titles asks Plex about each, one request per title; past SHARED_LOOKUPS titles
    every library is listed once instead. An edition has a bundle of its own, a season answers
    for its show, and a collection is never asked about."""
    from urllib.parse import unquote

    def where(key, title):
        return {"librarySectionID": key, "librarySectionKey": f"/library/sections/{key}", "librarySectionTitle": title}

    hd = make_movie(ratingKey=101, guid="plex://movie/heat", editionTitle=None)
    uhd = make_movie(ratingKey=201, guid="plex://movie/heat", editionTitle=None, **where(2, "4K Movies"))
    cut = make_movie(ratingKey=202, guid="plex://movie/heat", **where(2, "4K Movies"))  # Director's Cut
    solo = make_movie(ratingKey=102, title="Thief", guid="plex://movie/thief", editionTitle=None)
    show = FakeItem(ratingKey=301, type="show", title="Done", guid="plex://show/done", guids=[], **where(3, "TV Shows"))
    show_4k = FakeItem(ratingKey=401, type="show", title="Done", guid="plex://show/done", guids=[], **where(4, "4K TV"))
    sections = [FakeSection(1, "Movies", "movie", [hd, solo]), FakeSection(2, "4K Movies", "movie", [uhd, cut]),
                FakeSection(3, "TV Shows", "show", [show]), FakeSection(4, "4K TV", "show", [show_4k])]
    client = make_client(sections)
    asked = []

    def by_guid(key, cls=None, **kwargs):
        assert key.startswith("/library/all?guid=")
        guid = unquote(key.split("=", 1)[1])
        asked.append(guid)
        return [i for s in sections for i in s.items if i.guid == guid]

    client.server.fetchItems = by_guid
    parsed = {raw.ratingKey: client._parse_item(raw, bulk=True) for raw in (hd, uhd, cut, solo, show, show_4k)}
    copies = client.shared_copies(parsed[101])
    assert sorted(c.item_id for c in copies) == ["101", "201"] and asked == ["plex://movie/heat"]
    assert client.shared_copies(parsed[201]) == copies and len(asked) == 1  # one request per title
    assert client.shared_copies(parsed[202]) == [] and client.shared_copies(parsed[102]) == []
    season = MediaServerItem(item_id="311", item_type="season", title="Season 1", index=1)
    assert sorted(c.item_id for c in client.shared_copies(season, parsed[301])) == ["301", "401"]
    collection = MediaServerItem(item_id="9", item_type="collection", title="Heat Collection", guid="collection://heat")
    assert client.shared_copies(collection) == []
    assert asked == ["plex://movie/heat", "plex://movie/heat", "plex://movie/thief", "plex://show/done"]

    # A big run: once enough titles were asked about, every library is listed once.
    client.SHARED_LOOKUPS = 4
    pages = sum(len(s.search_calls) for s in sections)
    ronin = MediaServerItem(item_id="103", item_type="movie", title="Ronin", guid="plex://movie/ronin")
    assert client.shared_copies(ronin) == [] and len(asked) == 4
    assert sum(len(s.search_calls) for s in sections) == pages + 4  # one page per library
    assert sorted(c.item_id for c in client.shared_copies(parsed[201])) == ["101", "201"]
    assert sorted(c.item_id for c in client.shared_copies(season, parsed[401])) == ["301", "401"]
    assert sum(len(s.search_calls) for s in sections) == pages + 4 and len(asked) == 4

    # Plex not answering: None, so callers leave a bundle that may be shared alone.
    down = make_client(sections)
    down.server.fetchItems = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down"))
    assert down.shared_copies(parsed[101]) is None
    down.SHARED_LOOKUPS = 1
    for section in sections:
        section.search = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down"))
    assert down.shared_copies(parsed[102]) is None and down.shared_copies(parsed[101]) is None


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


class FakePoster:
    def __init__(self, rating_key, thumb, selected=False):
        self.ratingKey = rating_key
        self.thumb = thumb
        self.selected = selected
        self.selects = 0

    def select(self):
        self.selects += 1


def _poster_client(posters):
    client = make_client([])
    served = []

    class Got:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            return None

    client.server.fetchItems = lambda key, cls=None, **kw: list(posters) if key.endswith("/posters") else []
    client.server.url = lambda path, includeToken=False: f"http://plex.local{path}{'&token' if includeToken else ''}"
    client.server._session.get = lambda url, timeout=None: (served.append(url), Got(f"image at {url}".encode()))[1]
    return client, served


def test_server_poster_is_a_provider_poster_never_an_upload():
    item = MediaServerItem(item_id="7", item_type="movie", title="Heat")
    ours = FakePoster("upload://posters/abc", "/library/metadata/7/file?url=upload", selected=True)
    first = FakePoster("metadata://posters/tmdb1", "/library/metadata/7/file?url=first")
    picked = FakePoster("https://image.example/p2.jpg", "/library/metadata/7/file?url=second")
    client, served = _poster_client([ours, first, picked])

    # Our upload is selected (state lost, or a hand upload): the first provider poster is the source, never the upload.
    ref, raw = client.server_poster(item)
    assert ref == "metadata://posters/tmdb1" and raw == b"image at http://plex.local/library/metadata/7/file?url=first&token"
    # A recorded reference wins while Plex still lists it; a vanished one falls back.
    assert client.server_poster(item, "https://image.example/p2.jpg")[0] == "https://image.example/p2.jpg"
    assert client.server_poster(item, "metadata://posters/gone")[0] == "metadata://posters/tmdb1"
    # Before any upload, the provider poster Plex has selected is the one kept.
    ours.selected, picked.selected = False, True
    assert client.server_poster(item)[0] == "https://image.example/p2.jpg"
    # Nothing but uploads: Plex has no poster of its own to give.
    client, _ = _poster_client([ours])
    assert client.server_poster(item) is None


def test_restore_server_poster_selects_the_recorded_one_or_resets():
    unlocked = []
    native = FakeItem(unlockPoster=lambda: unlocked.append("unlock"))
    item = MediaServerItem(item_id="7", item_type="movie", title="Heat", native=native)
    ours = FakePoster("upload://posters/abc", "/x", selected=True)
    first = FakePoster("metadata://posters/tmdb1", "/a")
    picked = FakePoster("https://image.example/p2.jpg", "/b")
    client, _ = _poster_client([ours, first, picked])
    resets = []
    client.reset_poster = lambda it: (resets.append(it.item_id), True)[1]

    assert client.restore_server_poster(item, "https://image.example/p2.jpg") is True
    assert picked.selects == 1 and first.selects == 0 and unlocked == ["unlock"] and resets == []
    # The recorded poster is gone, or none was recorded: the ordinary reset picks Plex's first provider poster.
    assert client.restore_server_poster(item, "metadata://posters/gone") is True and resets == ["7"]
    assert client.restore_server_poster(item) is True and resets == ["7", "7"]
    assert client.restore_server_poster(item, "upload://posters/abc") is True and ours.selects == 0  # an upload is never restored


def _video(rating_key, resolution, file, *, dovi=False, transfer=None, audio=(), parent=None, grandparent=None):
    row = Element("Video", ratingKey=str(rating_key))
    if parent:
        row.set("parentRatingKey", str(parent))
    if grandparent:
        row.set("grandparentRatingKey", str(grandparent))
    part = SubElement(SubElement(row, "Media", videoResolution=resolution), "Part", file=file)
    video = SubElement(part, "Stream", streamType="1")
    if dovi:
        video.set("DOVIPresent", "1")
    if transfer:
        video.set("colorTrc", transfer)
    for title in audio:
        SubElement(part, "Stream", streamType="2", extendedDisplayTitle=title)
    return row


def test_library_media_lists_every_file_with_its_streams_in_one_listing():
    movies = FakeSection("1", "Movies", "movie")
    shows = FakeSection("2", "TV", "show")
    client = make_client([movies, shows])
    asked = []
    rows = {
        "1": [_video(101, "4k", "/m/Film [DV HDR10].mkv", dovi=True, transfer="smpte2084", audio=["English (TrueHD 7.1)", "Commentary (AAC Stereo)"]),
              _video(102, "1080", "/m/Other.mkv", transfer="arib-std-b67"), _video(103, "sd", "/m/Old.avi")],
        "2": [_video(301, "720", "/tv/S01E01.mkv", parent=21, grandparent=20), _video(302, "720", "/tv/S01E02.mkv", parent=21, grandparent=20),
              _video(303, "4k", "/tv/S02E01.mkv", parent=22, grandparent=20)],
    }

    def query(path, **kwargs):
        asked.append(path)
        section = path.split("/library/sections/")[1].split("/")[0]
        root = Element("MediaContainer")
        if "X-Plex-Container-Start=0&" in path:
            root.extend(rows[section])
        return root

    client.server.query = query
    media = client.library_media("machine1:/library/sections/1")
    assert asked == ["/library/sections/1/all?type=1&includeElements=Stream&X-Plex-Container-Start=0&X-Plex-Container-Size=500"]  # one request, streams included
    assert media["101"] == [{"resolution": "4k", "hdr": True, "dovi": True, "path": "/m/Film [DV HDR10].mkv", "audio_titles": ["English (TrueHD 7.1)", "Commentary (AAC Stereo)"]}]
    assert (media["102"][0]["hdr"], media["102"][0]["dovi"]) == (True, False)   # HLG counts as HDR, as Plex's own HDR filter does
    assert (media["103"][0]["resolution"], media["103"][0]["hdr"], media["103"][0]["audio_titles"]) == ("sd", False, [])
    # Episodes are grouped under their show and under their season.
    tv = client.library_media("2")
    assert "type=4&includeElements=Stream" in asked[-1]
    assert [f["path"] for f in tv["20"]] == ["/tv/S01E01.mkv", "/tv/S01E02.mkv", "/tv/S02E01.mkv"]
    assert [f["path"] for f in tv["21"]] == ["/tv/S01E01.mkv", "/tv/S01E02.mkv"] and [f["resolution"] for f in tv["22"]] == ["4k"]
    assert client.library_media("nope") == {}

    # One item at a time (an upload): the movie itself, a show's leaves, a season's children.
    asked.clear()
    client.server.query = lambda path, **kwargs: (asked.append(path), Element("MediaContainer"))[1]
    for item_type, expected in (("movie", "/library/metadata/7?X-Plex"), ("show", "/library/metadata/7/allLeaves?includeElements=Stream&X-Plex"), ("season", "/library/metadata/7/children?includeElements=Stream&X-Plex")):
        assert client.item_media(MediaServerItem(item_id="7", item_type=item_type, title="X")) == [] and asked[-1].startswith(expected)
    assert client.item_media(MediaServerItem(item_id="7", item_type="collection", title="X")) == []

