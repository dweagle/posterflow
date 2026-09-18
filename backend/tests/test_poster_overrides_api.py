import os

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


def test_file_override_crud(client, test_db, tmp_path):
    """A slot override can pin one exact file on a drive: stored relative to the drive root,
    returned absolute, and cleared again when the same target is repointed at a whole drive."""
    from models.poster_override import PosterOverride

    _seed_drive(test_db, tmp_path)
    root = tmp_path / "cl"
    (root / "Sub").mkdir()
    pick = root / "Sub" / "Some Other Poster (1999).jpg"
    pick.write_bytes(b"x")
    base = {"media_type": "movie", "tmdb_id": 5, "title": "Movie One", "year": 2020,
            "scope": "slot", "drive_id": "cl-1"}

    created = client.post("/api/posterflow/overrides", json={**base, "file": str(pick)})
    assert created.status_code == 200, created.json()
    assert created.json()["file"] == str(pick.resolve())
    override_id = created.json()["id"]
    assert test_db.get(PosterOverride, override_id).file == os.path.join("Sub", pick.name)

    listing = client.get("/api/posterflow/overrides").json()
    assert listing[0]["file"] == str(pick.resolve())

    # Repointing the same slot at the drive itself drops the file pick.
    again = client.post("/api/posterflow/overrides", json=base)
    assert again.json()["id"] == override_id and again.json()["file"] is None


def test_file_override_validation(client, test_db, tmp_path):
    from models.artwork_drive import ArtworkDrive

    _seed_drive(test_db, tmp_path)
    root = tmp_path / "cl"
    pick = root / "Poster.jpg"
    pick.write_bytes(b"x")
    outside = tmp_path / "elsewhere.jpg"
    outside.write_bytes(b"x")
    base = {"media_type": "movie", "title": "Movie One", "year": 2020, "scope": "slot", "drive_id": "cl-1"}

    assert client.post("/api/posterflow/overrides", json={**base, "file": str(outside)}).status_code == 400
    assert client.post("/api/posterflow/overrides", json={**base, "file": str(root / "missing.jpg")}).status_code == 404
    assert client.post("/api/posterflow/overrides", json={**base, "scope": "set", "file": str(pick)}).status_code == 400

    test_db.add(ArtworkDrive(name="Art A", drive_id="art-a", subscribed=True, custom_path=str(tmp_path / "art")))
    test_db.commit()
    art = {**base, "domain": "artwork", "slot": "logo", "drive_id": "art-a", "file": str(pick)}
    assert client.post("/api/posterflow/overrides", json=art).status_code == 400


def test_artwork_file_override_crud(client, test_db, tmp_path):
    """An artwork slot override can pin one exact file on an artwork drive, validated
    against that drive's folder (not the poster drives')."""
    from models.artwork_drive import ArtworkDrive
    from models.poster_override import PosterOverride

    _seed_drive(test_db, tmp_path)
    art_root = tmp_path / "art"
    (art_root / "logos").mkdir(parents=True)
    pick = art_root / "logos" / "Some Show (2020).png"
    pick.write_bytes(b"x")
    poster_side = tmp_path / "cl" / "Poster.jpg"
    poster_side.write_bytes(b"x")
    test_db.add(ArtworkDrive(name="Art A", drive_id="art-a", subscribed=True, custom_path=str(art_root)))
    test_db.commit()

    base = {"media_type": "show", "tmdb_id": 9, "title": "Some Show", "year": 2020,
            "domain": "artwork", "scope": "slot", "slot": "logo", "drive_id": "art-a"}
    created = client.post("/api/posterflow/overrides", json={**base, "file": str(pick)})
    assert created.status_code == 200, created.json()
    assert created.json()["file"] == str(pick.resolve()) and created.json()["slot"] == "logo"
    assert test_db.get(PosterOverride, created.json()["id"]).file == os.path.join("logos", pick.name)
    assert client.get("/api/posterflow/overrides").json()[0]["file"] == str(pick.resolve())

    # A poster-drive file is outside the artwork drive's folder; set scope can't carry a file.
    assert client.post("/api/posterflow/overrides", json={**base, "file": str(poster_side)}).status_code == 400
    assert client.post("/api/posterflow/overrides", json={**base, "scope": "set", "file": str(pick)}).status_code == 400
