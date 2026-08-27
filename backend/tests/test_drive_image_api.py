from models.drive import Drive


def test_drive_image_serves_file_inside_drive(client, test_db, tmp_path):
    root = tmp_path / "mm"
    root.mkdir()
    img = root / "Movie One (2024).jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fake")
    test_db.add(Drive(name="MM Drive", drive_id="mm-1", style_type="MM2K", subscribed=True, custom_path=str(root)))
    test_db.commit()

    resp = client.get("/api/posterflow/drive-image", params={"path": str(img)})
    assert resp.status_code == 200
    assert resp.content == b"\xff\xd8\xff\xe0fake"


def test_drive_image_rejects_path_outside_drives(client, test_db, tmp_path):
    outside = tmp_path / "elsewhere.jpg"
    outside.write_bytes(b"x")
    resp = client.get("/api/posterflow/drive-image", params={"path": str(outside)})
    assert resp.status_code == 403


def test_drive_image_missing_file_404(client, test_db, tmp_path):
    resp = client.get("/api/posterflow/drive-image", params={"path": str(tmp_path / "nope.jpg")})
    assert resp.status_code == 404
