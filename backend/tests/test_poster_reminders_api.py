from models.poster_reminder import PosterReminder


def _payload(**overrides):
    base = {
        "kind": "poster",
        "media_type": "tv",
        "tmdb_id": 153312,
        "tvdb_id": 405297,
        "imdb_id": "tt16358384",
        "title": "Tulsa King",
        "year": "2022",
        "poster_url": "https://image.tmdb.org/t/p/w300/x.jpg",
        "homepage": "https://www.themoviedb.org/tv/153312",
        "note": "Only a titled backdrop so far",
    }
    base.update(overrides)
    return base


def test_create_then_list_reminder(client, test_db):
    response = client.post("/api/maker-tools/reminders", json=_payload())
    assert response.status_code == 200
    data = response.json()
    assert data["kind"] == "poster"
    assert data["title"] == "Tulsa King"
    assert data["year"] == "2022"
    assert data["note"] == "Only a titled backdrop so far"
    assert data["created_at"]

    listed = client.get("/api/maker-tools/reminders").json()
    assert [r["id"] for r in listed] == [data["id"]]


def test_same_item_same_kind_updates_note_instead_of_duplicating(client, test_db):
    first = client.post("/api/maker-tools/reminders", json=_payload(note="first")).json()
    second = client.post("/api/maker-tools/reminders", json=_payload(note="second")).json()
    assert second["id"] == first["id"]
    assert second["note"] == "second"
    assert test_db.query(PosterReminder).count() == 1


def test_poster_and_artwork_reminders_for_one_item_stay_separate(client, test_db):
    poster = client.post("/api/maker-tools/reminders", json=_payload(kind="poster")).json()
    artwork = client.post("/api/maker-tools/reminders", json=_payload(kind="artwork", note="no logo")).json()
    assert poster["id"] != artwork["id"]
    kinds = sorted(r["kind"] for r in client.get("/api/maker-tools/reminders").json())
    assert kinds == ["artwork", "poster"]


def test_idless_items_match_on_title_and_year(client, test_db):
    a = client.post("/api/maker-tools/reminders", json=_payload(tmdb_id=0, tvdb_id=None, imdb_id=None, note="a")).json()
    b = client.post("/api/maker-tools/reminders", json=_payload(tmdb_id=None, tvdb_id=None, imdb_id=None, note="b")).json()
    assert a["id"] == b["id"]
    assert a["tmdb_id"] is None
    # A different year is a different item.
    c = client.post("/api/maker-tools/reminders", json=_payload(tmdb_id=None, year="1999", note="c")).json()
    assert c["id"] != a["id"]


def test_update_note_and_delete(client, test_db):
    created = client.post("/api/maker-tools/reminders", json=_payload()).json()

    updated = client.put(f"/api/maker-tools/reminders/{created['id']}", json={"note": "  fixed the logo  "})
    assert updated.status_code == 200
    assert updated.json()["note"] == "fixed the logo"

    deleted = client.delete(f"/api/maker-tools/reminders/{created['id']}")
    assert deleted.status_code == 200
    assert client.get("/api/maker-tools/reminders").json() == []
    assert client.delete(f"/api/maker-tools/reminders/{created['id']}").status_code == 404


def test_validation(client, test_db):
    assert client.post("/api/maker-tools/reminders", json=_payload(kind="other")).status_code == 400
    assert client.post("/api/maker-tools/reminders", json=_payload(media_type="show")).status_code == 400
    assert client.post("/api/maker-tools/reminders", json=_payload(title="   ")).status_code == 400
    assert client.post("/api/maker-tools/reminders", json=_payload(note="x" * 2001)).status_code == 400
    assert client.put("/api/maker-tools/reminders/9999", json={"note": "x"}).status_code == 404
