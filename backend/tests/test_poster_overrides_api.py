from models.drive import Drive


def _seed_drive(test_db, tmp_path):
    root = tmp_path / "cl"
    root.mkdir(exist_ok=True)
    test_db.add(Drive(name="CL Drive", drive_id="cl-1", style_type="CL2K", subscribed=True, custom_path=str(root)))
    test_db.commit()


def test_override_crud_and_upsert(client, test_db, tmp_path):
    _seed_drive(test_db, tmp_path)
    payload = {"media_type": "show", "tmdb_id": 77, "title": "Show One", "year": 2020,
               "scope": "slot", "season": 1, "drive_id": "cl-1"}

    created = client.post("/api/posterflow/overrides", json=payload)
    assert created.status_code == 200
    override_id = created.json()["id"]

    # Same target again -> updates in place, no duplicate row.
    again = client.post("/api/posterflow/overrides", json=payload)
    assert again.json()["id"] == override_id
    listing = client.get("/api/posterflow/overrides").json()
    assert len(listing) == 1 and listing[0]["season"] == 1

    # A different season is its own override.
    other = client.post("/api/posterflow/overrides", json={**payload, "season": 2})
    assert other.status_code == 200 and other.json()["id"] != override_id
    assert len(client.get("/api/posterflow/overrides").json()) == 2

    deleted = client.delete(f"/api/posterflow/overrides/{override_id}")
    assert deleted.status_code == 200
    assert len(client.get("/api/posterflow/overrides").json()) == 1


def test_override_validation(client, test_db, tmp_path):
    _seed_drive(test_db, tmp_path)
    base = {"media_type": "show", "title": "Show One", "year": 2020, "scope": "slot", "drive_id": "cl-1"}

    assert client.post("/api/posterflow/overrides", json={**base, "scope": "bogus"}).status_code == 400
    assert client.post("/api/posterflow/overrides", json={**base, "media_type": "bogus"}).status_code == 400
    assert client.post("/api/posterflow/overrides", json={**base, "drive_id": "nope"}).status_code == 404


def test_artwork_override_crud(client, test_db, tmp_path):
    from models.artwork_drive import ArtworkDrive

    root = tmp_path / "art"
    root.mkdir()
    test_db.add(ArtworkDrive(name="Art A", drive_id="art-a", subscribed=True, custom_path=str(root)))
    test_db.commit()

    base = {"media_type": "movie", "title": "Movie One", "year": 2024,
            "domain": "artwork", "scope": "slot", "slot": "logo", "drive_id": "art-a"}
    created = client.post("/api/posterflow/overrides", json=base)
    assert created.status_code == 200
    assert created.json()["domain"] == "artwork" and created.json()["slot"] == "logo"

    # Artwork drive ids validate against the artwork drive table, and slots are checked.
    assert client.post("/api/posterflow/overrides", json={**base, "drive_id": "nope"}).status_code == 404
    assert client.post("/api/posterflow/overrides", json={**base, "slot": "bogus"}).status_code == 400

    # A poster-domain override for the same title is a separate row.
    _seed_drive(test_db, tmp_path)
    poster = client.post("/api/posterflow/overrides", json={
        "media_type": "movie", "title": "Movie One", "year": 2024, "scope": "slot", "drive_id": "cl-1"})
    assert poster.status_code == 200 and poster.json()["id"] != created.json()["id"]
