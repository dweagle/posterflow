"""Phase-3 coverage: jellyfin instances flow through the upload service's
instance parsing, client routing, index building, and capability gating."""

import json

from models.setting import Setting
from services.plex_upload import PlexUploadService
from util.media_server.client import MediaServerClient
from util.media_server.types import (
    CAP_SQUAREART,
    IMAGE_KIND_POSTER,
    IMAGE_KIND_SQUAREART,
    MediaServerInfo,
    MediaServerItem,
    MediaServerLibrary,
)


class _FakeJellyfinClient(MediaServerClient):
    server_type = "jellyfin"
    capabilities = frozenset()

    def __init__(self, url, api_key, logger=None, app_version=""):
        super().__init__()
        self.server_id = "jf1"
        self.server_name = "Jelly"
        self.server_version = "12.0.0"
        self.connect_status = True
        self.uploads = []
        movie_native = {"Id": "m1"}
        self.movie = MediaServerItem(
            item_id="m1",
            item_type="movie",
            title="Heat",
            year=1995,
            provider_ids={"tmdb": "949"},
            library_key="jf1:lib1",
            library_name="Movies",
            paths=["/movies/Heat (1995)/Heat.mkv"],
            native=movie_native,
            client=self,
        )

    def get_server_info(self):
        return MediaServerInfo(self.server_id, self.server_name, self.server_version)

    def get_libraries(self):
        return [MediaServerLibrary(key="lib1", title="Movies", type="movie")]

    def get_library_items(self, library_key, item_types=None):
        return [self.movie] if library_key == "lib1" else []

    def get_collections(self, library_key=None):
        return []

    def get_collection_items(self, collection):
        return []

    def get_seasons(self, show):
        return []

    def get_library_seasons(self, library_key):
        return []

    def find_by_field(self, field, value, library_key, include_collections=True):
        return []

    def find_by_provider_ids(self, provider_ids, media_type, library_keys=None, title=None, year=None):
        if provider_ids.get("tmdb") == "949":
            return [self.movie]
        return []

    def find_by_title(self, title, media_type=None, library_keys=None):
        return [self.movie] if title == "Heat" else []

    def search(self, query, media_types=None, limit=30):
        return [self.movie] if query.lower() in "heat" else []

    def upload_image(self, item, kind, filepath):
        if kind == IMAGE_KIND_SQUAREART:
            return False
        self.uploads.append((item.item_id, kind, filepath))
        return True

    def image_url(self, item, max_width=None):
        return item.thumb_path


def _patch_jellyfin(monkeypatch):
    import util.media_server.jellyfin as jf_mod

    monkeypatch.setattr(jf_mod, "JellyfinClient", _FakeJellyfinClient)


def test_get_instances_includes_jellyfin_with_type(test_db):
    test_db.add(Setting(key="plex_instances", value=json.dumps([
        {"name": "P", "url": "http://p", "api_key": "t"},
        {"name": "J", "url": "http://j", "api_key": "k", "type": "jellyfin"},
    ])))
    test_db.commit()

    service = PlexUploadService(test_db)
    instances = service._get_plex_instances()
    assert [(i["name"], i["type"]) for i in instances] == [("P", "plex"), ("J", "jellyfin")]


def test_connect_routes_jellyfin_instances(test_db, monkeypatch):
    _patch_jellyfin(monkeypatch)
    service = PlexUploadService(test_db)
    client = service._connect_media_server_client(
        {"name": "J", "url": "http://j", "api_key": "k", "type": "jellyfin"}
    )
    assert client is not None and client.server_type == "jellyfin"


def test_build_index_over_jellyfin_instance(test_db, monkeypatch):
    _patch_jellyfin(monkeypatch)
    service = PlexUploadService(test_db)
    index, totals = service._build_plex_index(
        [{"name": "J", "url": "http://j", "api_key": "k", "type": "jellyfin"}],
        {},
    )
    assert [m.item_id for m in index["movies"]["heat"]] == ["m1"]
    assert [m.item_id for m in index["movies"]["id:tmdb:949"]] == ["m1"]
    assert totals == [
        {"instance": "J", "library": "Movies", "section_type": "movie", "items": 1, "collections": 0}
    ]


def test_targeted_index_over_jellyfin_instance(test_db, monkeypatch):
    _patch_jellyfin(monkeypatch)
    service = PlexUploadService(test_db)
    index, totals = service._build_plex_index_targeted(
        [{"name": "J", "url": "http://j", "api_key": "k", "type": "jellyfin"}],
        {},
        tmdb_id=949,
        title="Heat",
        year=1995,
        media_type="movie",
    )
    assert [m.item_id for m in index["movies"]["id:tmdb:949"]] == ["m1"]
    assert totals[0]["instance"] == "J"


def test_squareart_capability_gating(test_db):
    service = PlexUploadService(test_db)
    jf = _FakeJellyfinClient("http://j", "k")
    item = jf.movie
    assert service._item_supports_image_kind(item, IMAGE_KIND_POSTER) is True
    assert service._item_supports_image_kind(item, IMAGE_KIND_SQUAREART) is False

    class _PlexLike(_FakeJellyfinClient):
        capabilities = frozenset({CAP_SQUAREART})

    plex_like = _PlexLike("http://p", "t")
    plex_item = MediaServerItem(item_id="x", item_type="movie", title="X", client=plex_like)
    assert service._item_supports_image_kind(plex_item, IMAGE_KIND_SQUAREART) is True


class _FakeJellyfinWithCollections(_FakeJellyfinClient):
    def __init__(self, url, api_key, logger=None, app_version=""):
        super().__init__(url, api_key, logger=logger, app_version=app_version)
        self.boxset = MediaServerItem(
            item_id="box1",
            item_type="collection",
            title="Fast Saga",
            added_at="2026-08-01T00:00:00",
            client=self,
        )

    def get_collections(self, library_key=None):
        return [self.boxset]

    def get_collection_items(self, collection):
        return [self.movie] if collection.item_id == "box1" else []

    def find_by_field(self, field, value, library_key, include_collections=True):
        if field == "label" and value == "HasBorder":
            return [self.movie]
        return []


def _patch_jellyfin_with_collections(monkeypatch):
    import util.media_server.jellyfin as jf_mod

    monkeypatch.setattr(jf_mod, "JellyfinClient", _FakeJellyfinWithCollections)


def test_renamer_collections_from_jellyfin(test_db, monkeypatch):
    _patch_jellyfin_with_collections(monkeypatch)
    from services.poster_renamer import PosterRenameService

    service = PosterRenameService(test_db)
    media_dict = {"movies": [], "series": [], "collections": []}
    service._fetch_media_server_collections(
        {"name": "J", "url": "http://j", "api_key": "k", "type": "jellyfin"},
        media_dict,
    )
    assert len(media_dict["collections"]) == 1
    entry = media_dict["collections"][0]
    assert entry["title"] == "Fast Saga"
    assert entry["instance"] == "J"  # box sets are server-global, no library suffix
    assert entry["library_type"] == "movie"
    assert entry["added"] == "2026-08-01T00:00:00"


def test_renamer_collections_jellyfin_skipped_when_no_libraries_selected(test_db, monkeypatch):
    _patch_jellyfin_with_collections(monkeypatch)
    from services.poster_renamer import PosterRenameService

    service = PosterRenameService(test_db)
    media_dict = {"movies": [], "series": [], "collections": []}
    service._fetch_media_server_collections(
        {"name": "J", "url": "http://j", "api_key": "k", "type": "jellyfin"},
        media_dict,
        selected_libraries=["OtherPlex:1"],
    )
    assert media_dict["collections"] == []


def test_border_rules_query_jellyfin_instance(test_db, monkeypatch):
    _patch_jellyfin_with_collections(monkeypatch)
    from services.plex_border_rules import PlexBorderRule, _query_plex_matches

    test_db.add(Setting(key="plex_instances", value=json.dumps([
        {"name": "J", "url": "http://j", "api_key": "k", "type": "jellyfin"},
    ])))
    test_db.add(Setting(key="border_replacer_rule_libraries", value=json.dumps(["J:lib1"])))
    test_db.commit()

    label_rule = PlexBorderRule(
        name="tagged", match="label", value="HasBorder", mode="include", colors=["#fff"], style_opts={}
    )
    collection_rule = PlexBorderRule(
        name="saga", match="collection", value="Fast Saga", mode="include", colors=["#000"], style_opts={}
    )
    scanned, had_errors = _query_plex_matches(test_db, [label_rule, collection_rule])
    assert scanned == 1 and had_errors is False
    assert "tmdb:949" in label_rule.match_keys  # tag query found the movie
    assert "tmdb:949" in collection_rule.match_keys  # box-set membership found it too


def test_jellyfin_collections_indexed_once_across_libraries(test_db, monkeypatch):
    """Server-global box sets must not be re-indexed (and re-counted) per library."""

    class _TwoLibraryJellyfin(_FakeJellyfinWithCollections):
        def __init__(self, url, api_key, logger=None, app_version=""):
            super().__init__(url, api_key, logger=logger, app_version=app_version)
            self.collection_queries = 0

        def get_libraries(self):
            return [
                MediaServerLibrary(key="lib1", title="Movies", type="movie"),
                MediaServerLibrary(key="lib2", title="TV Shows", type="show"),
            ]

        def get_collections(self, library_key=None):
            self.collection_queries += 1
            return [self.boxset]

    import util.media_server.jellyfin as jf_mod
    monkeypatch.setattr(jf_mod, "JellyfinClient", _TwoLibraryJellyfin)

    service = PlexUploadService(test_db)
    index, totals = service._build_plex_index(
        [{"name": "J", "url": "http://j", "api_key": "k", "type": "jellyfin"}],
        {},
    )
    assert sum(t["collections"] for t in totals) == 1
    assert len(index["collections"]["fastsaga"]) == 1


def test_client_upload_dispatch_and_squareart_refusal(test_db):
    service = PlexUploadService(test_db)
    jf = _FakeJellyfinClient("http://j", "k")
    item = jf.movie
    service._client_upload(item, IMAGE_KIND_POSTER, "/tmp/p.jpg")
    assert jf.uploads == [("m1", IMAGE_KIND_POSTER, "/tmp/p.jpg")]

    try:
        service._client_upload(item, IMAGE_KIND_SQUAREART, "/tmp/s.jpg")
        raised = False
    except RuntimeError:
        raised = True
    assert raised


def test_record_server_identity_two_connects_one_session(test_db):
    """Two instance connects in one run must not stage duplicate settings rows.
    The prod session runs autoflush=False, so the second get_setting missed the
    first pending insert, staged a duplicate key, and the IntegrityError then
    detonated inside the NEXT job-status commit (zombie running job row)."""
    import json
    from models.setting import get_setting

    service = PlexUploadService(test_db)

    plex_like = _FakeJellyfinClient("http://p", "t")
    plex_like.server_id = "plexsrv"
    plex_like.server_type = "plex"
    plex_like.instance_name = "dev-plex"
    jf = _FakeJellyfinClient("http://j", "k")
    jf.instance_name = "dev-jelly"

    service._record_server_identity(plex_like)
    service._record_server_identity(jf)

    stored = json.loads(get_setting(test_db, "media_server_id_map").value)
    assert stored == {
        "plexsrv": {"name": "dev-plex", "type": "plex", "libraries": {"lib1": "Movies"}},
        "jf1": {"name": "dev-jelly", "type": "jellyfin", "libraries": {"lib1": "Movies"}},
    }
    # The session must remain usable for the job-status write that follows
    test_db.commit()
