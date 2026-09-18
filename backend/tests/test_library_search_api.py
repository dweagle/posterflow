import pytest

from services import library_search
from services.poster_renamer import PosterRenameService


def _media():
    return {
        "movies": [
            {"type": "movies", "title": "Heat", "year": 1995, "tmdb_id": 949, "imdb_id": "tt0113277", "instance": "Radarr"},
            {"type": "movies", "title": "Dead Heat", "year": 1988, "tmdb_id": 26171, "instance": "Radarr"},
            {"type": "movies", "title": "1917", "year": 2019, "tmdb_id": 530915, "instance": "Radarr"},
        ],
        "series": [{
            "type": "series", "title": "Breaking Bad", "year": 2008, "tvdb_id": 81189, "tmdb_id_ref": 1396,
            "imdb_id": "tt0903747", "instance": "Sonarr", "alternate_titles": ["Br Ba"],
            "seasons": [{"season_number": 1}, {"season_number": 0}, {"season_number": 2}],
        }],
        "collections": [{"type": "collections", "title": "Heat Collection", "year": None, "tmdb_id_ref": 5, "instance": "Plex (Movies)"}],
    }


@pytest.fixture
def stub_fetch(monkeypatch):
    library_search.clear_cache()
    calls = []

    def fake(self, log_tag=None, setting_key=None):
        calls.append(setting_key)
        return _media()

    monkeypatch.setattr(PosterRenameService, "get_media_from_instances", fake)
    yield calls
    library_search.clear_cache()


def test_library_search_returns_the_renamer_items(client, stub_fetch):
    """Hits come from the renamer's own media list, title-starts first, with the override keys
    (series tmdb from tmdb_id_ref, seasons sorted) and the source that supplied them."""
    res = client.get("/api/posterflow/library-search", params={"q": "heat"})
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 5
    assert [(i["media_type"], i["title"]) for i in body["items"]] == [
        ("movie", "Heat"), ("collection", "Heat Collection"), ("movie", "Dead Heat"),
    ]
    assert body["items"][0] == {
        "media_type": "movie", "title": "Heat", "year": 1995, "tmdb_id": 949, "tvdb_id": None,
        "imdb_id": "tt0113277", "seasons": [], "source": "Radarr", "poster_url": None, "thumb_url": None,
    }

    show = client.get("/api/posterflow/library-search", params={"q": "BAD"}).json()["items"][0]
    assert show["media_type"] == "show" and show["tmdb_id"] == 1396 and show["tvdb_id"] == 81189
    assert show["seasons"] == [0, 1, 2] and show["source"] == "Sonarr"

    # Alternate titles count, and every query word must appear (any order).
    assert client.get("/api/posterflow/library-search", params={"q": "br ba"}).json()["count"] == 1
    assert client.get("/api/posterflow/library-search", params={"q": "bad breaking"}).json()["count"] == 1
    assert client.get("/api/posterflow/library-search", params={"q": "nothing"}).json()["count"] == 0


def test_library_search_caches_the_fetch(client, stub_fetch):
    client.get("/api/posterflow/library-search", params={"q": "heat"})
    client.get("/api/posterflow/library-search", params={"q": "bad"})
    assert stub_fetch == ["poster_renamer_libraries"]

    warm = client.get("/api/posterflow/library-search", params={"q": "", "refresh": "true"}).json()
    assert warm["items"] == [] and warm["total"] == 5
    assert len(stub_fetch) == 2


def test_library_search_understands_pasted_drive_names(client, stub_fetch):
    """A drive file name — year, id tags, artwork or season suffix and all — still finds its
    item; the year ranks matches, it is never a required word."""
    def titles(q):
        return [i["title"] for i in client.get("/api/posterflow/library-search", params={"q": q}).json()["items"]]

    assert titles("Breaking Bad (2008)") == ["Breaking Bad"]
    assert titles("Breaking Bad (2008) {tmdb-1396} - background") == ["Breaking Bad"]
    assert titles("Breaking Bad (2008) {tvdb-81189} - Season 2") == ["Breaking Bad"]
    assert titles("Heat 1995") == ["Heat", "Heat Collection", "Dead Heat"]  # the 1995 one first
    assert titles("Heat (1988)") == ["Dead Heat", "Heat", "Heat Collection"]
    assert titles("(1995)") == ["Heat"]  # a bare year still narrows


def test_library_search_bare_year_falls_back_to_title(client, stub_fetch):
    """"1917" narrows by year first (nothing from 1917 here), then matches the title "1917"."""
    res = client.get("/api/posterflow/library-search", params={"q": "1917"}).json()
    assert [i["title"] for i in res["items"]] == ["1917"]
    res = client.get("/api/posterflow/library-search", params={"q": "1917 (2019)"}).json()
    assert [i["title"] for i in res["items"]] == ["1917"]
