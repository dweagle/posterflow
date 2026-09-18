from pathlib import Path

from models.drive import Drive
from models.poster import Poster


def test_poster_search_hits_carry_the_file_path(client, test_db):
    """Each drive hit names its file so the Poster Search page can pin it as an override.
    (Made-up root: the search only checks the path sits inside the drive folder, and it
    skips anything under a 'tmp' segment, which rules out pytest's temp dir.)"""
    root = Path("/srv/posterflow-search-test/cl")
    poster = root / "Movie One (2020).jpg"
    test_db.add(Drive(name="CL Drive", drive_id="cl-1", style_type="CL2K", subscribed=True, custom_path=str(root)))
    test_db.add(Poster(drive_id="cl-1", file_name=poster.name, file_path=str(poster)))
    test_db.commit()

    res = client.get("/api/stats/poster-search", params={"q": "Movie One"})
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 1
    hit = items[0]["drives"][0]
    assert hit["drive_id"] == "cl-1"
    assert hit["file_path"] == str(poster)


def test_artwork_search_groups_by_name_and_type(client, test_db):
    """Artwork hits mirror the poster search: grouped per name + type, one entry per drive,
    with the file path and the artwork image route; `types` narrows the kinds returned."""
    from models.artwork import Artwork
    from models.artwork_drive import ArtworkDrive

    root = Path("/srv/posterflow-search-test/art")
    test_db.add(ArtworkDrive(name="Art A", display_name="Art Drive A", drive_id="art-a", subscribed=True, custom_path=str(root)))
    test_db.add_all([
        Artwork(artwork_drive_id="art-a", artwork_type="logo", file_name="Heat (1995).png",
                file_path=str(root / "logos" / "Heat (1995).png")),
        Artwork(artwork_drive_id="art-a", artwork_type="background", file_name="Heat (1995).jpg",
                file_path=str(root / "backgrounds" / "Heat (1995).jpg")),
        Artwork(artwork_drive_id="art-a", artwork_type="squareart", file_name="Dead Heat (1988).png",
                file_path=str(root / "squareart" / "Dead Heat (1988).png")),
    ])
    test_db.commit()

    res = client.get("/api/stats/artwork-search", params={"q": "heat"})
    assert res.status_code == 200
    items = res.json()["items"]
    assert [(i["artwork_name"], i["artwork_type"]) for i in items] == [
        ("Dead Heat (1988)", "squareart"), ("Heat (1995)", "logo"), ("Heat (1995)", "background"),
    ]
    logo = items[1]["drives"][0]
    assert logo["drive_id"] == "art-a" and logo["drive_name"] == "Art Drive A" and logo["drive_type"] == "artwork"
    assert logo["file_path"] == str(root / "logos" / "Heat (1995).png")
    assert logo["image_url"].startswith("/api/stats/artwork/") and logo["image_url"].endswith("/image")

    narrowed = client.get("/api/stats/artwork-search", params={"q": "heat", "types": "background"}).json()
    assert [i["artwork_type"] for i in narrowed["items"]] == ["background"]


def test_poster_search_ignores_digits_inside_id_tags(client, test_db):
    """"1883" finds the show called 1883, not every file whose tmdb/tvdb id contains 1883.
    Ids still match when searched as ids: with their source, or as a whole number."""
    root = Path("/srv/posterflow-search-test/cl")
    test_db.add(Drive(name="CL Drive", drive_id="cl-1", style_type="CL2K", subscribed=True, custom_path=str(root)))
    for name in (
        "1883 (2021) {tmdb-118357} {tvdb-407668}.jpg",
        "Some Movie (2019) {tmdb-118834}.jpg",
        "Another Show (2020) {tvdb-318835} {imdb-tt0188345}.jpg",
    ):
        test_db.add(Poster(drive_id="cl-1", file_name=name, file_path=str(root / name)))
    test_db.commit()

    def names(q):
        return [i["poster_name"] for i in client.get("/api/stats/poster-search", params={"q": q}).json()["items"]]

    assert names("1883") == ["1883 (2021) {tmdb-118357} {tvdb-407668}"]
    assert names("tmdb-118834") == ["Some Movie (2019) {tmdb-118834}"]
    assert names("tvdb 318835") == ["Another Show (2020) {tvdb-318835} {imdb-tt0188345}"]
    assert names("tt0188345") == ["Another Show (2020) {tvdb-318835} {imdb-tt0188345}"]
    assert names("118834") == ["Some Movie (2019) {tmdb-118834}"]  # a whole id still works bare
    assert names("1188") == []  # a fragment of an id never does
