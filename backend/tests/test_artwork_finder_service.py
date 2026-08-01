"""Tests for services/artwork_finder.py (the Artwork Finder feature)."""
import io
import json

from PIL import Image

import services.artwork_finder as af
from models.setting import upsert_setting


def _png_bytes(color=(200, 0, 0, 255), size=(20, 20)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


def _jpg_bytes(color=(10, 20, 30), size=(1920, 1080)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------- white logo


def test_is_white_logo_classifies_white_red_black():
    white = Image.new("RGBA", (10, 10), (255, 255, 255, 255))
    red = Image.new("RGBA", (10, 10), (200, 0, 0, 255))
    black = Image.new("RGBA", (10, 10), (0, 0, 0, 255))
    assert af.is_white_logo(white, 2.0)[0] is True
    assert af.is_white_logo(red, 2.0)[0] is False       # colored
    assert af.is_white_logo(black, 2.0)[0] is False      # dark counts as off-white


def test_make_logo_white_forces_white_keeps_alpha():
    im = Image.new("RGBA", (8, 8), (10, 120, 200, 128))
    out = af.make_logo_white(im).convert("RGBA")
    r, g, b, a = out.getpixel((4, 4))
    assert (r, g, b) == (255, 255, 255)
    assert a == 128                                        # alpha preserved
    # a fully-transparent pixel stays transparent
    im2 = Image.new("RGBA", (8, 8), (10, 120, 200, 0))
    assert af.make_logo_white(im2).convert("RGBA").getpixel((0, 0))[3] == 0


# ---------------------------------------------------------------- pickers


def _backdrops() -> dict:
    return {"backdrops": [
        {"file_path": "/c.jpg", "iso_639_1": None, "width": 1920, "vote_average": 5},
        {"file_path": "/d.jpg", "iso_639_1": None, "width": 3840, "vote_average": 8},
        {"file_path": "/e.jpg", "iso_639_1": "en", "width": 3840, "vote_average": 10},
        {"file_path": "/f.jpg", "iso_639_1": None, "width": 1280, "vote_average": 9},
    ]}


def test_backdrop_candidates_strict_drops_text_and_narrow():
    # Auto-pick's rule: textless only, at least min_width wide, best first.
    out = af.backdrop_candidates(_backdrops(), 1920)
    assert [b["file_path"] for b in out] == ["/d.jpg", "/c.jpg"]


def test_backdrop_candidates_browse_lists_everything_textless_first():
    # The interactive browser keeps the narrow (/f) and text-tagged (/e) ones — obscure titles
    # often have nothing else — but ranks textless ahead of them.
    out = af.backdrop_candidates(_backdrops(), 1920, textless_only=False)
    assert [b["file_path"] for b in out] == ["/f.jpg", "/d.jpg", "/c.jpg", "/e.jpg"]


def test_logo_candidates_png_only_sorted():
    images = {"logos": [
        {"file_path": "/a.svg", "vote_average": 10, "width": 800},
        {"file_path": "/b.png", "vote_average": 3, "width": 800},
        {"file_path": "/c.png", "vote_average": 7, "width": 800},
    ]}
    out = af.logo_candidates(images)
    assert [l["file_path"] for l in out] == ["/c.png", "/b.png"]


# ---------------------------------------------------------------- canonical filename (IDarr writer)


def test_build_filename_uses_idarr_convention():
    movie = af.FinderItem(title="Back to the Future", year=1985, tmdb_id=105,
                          imdb_id="tt0088763", media_type="movie")
    assert af.build_filename(movie, "logo") == "Back to the Future (1985) {tmdb-105} {imdb-tt0088763} - logo.png"

    tv = af.FinderItem(title="The Office", year=2005, tmdb_id=2316, tvdb_id=73244,
                       imdb_id="tt0386676", media_type="tv")
    # tvdb only appears for tv, order tmdb/tvdb/imdb, square -> .jpg
    assert af.build_filename(tv, "squareart") == "The Office (2005) {tmdb-2316} {tvdb-73244} {imdb-tt0386676} - squareart.jpg"
    # a movie never carries a tvdb tag even if one is set
    movie2 = af.FinderItem(title="X", year=2000, tmdb_id=1, tvdb_id=999, media_type="movie")
    assert "{tvdb-" not in af.build_filename(movie2, "background")


def test_build_download_filename_by_role():
    movie = af.FinderItem(title="Dune", year=2021, tmdb_id=438631, imdb_id="tt1160419", media_type="movie")
    assert af.build_download_filename(movie, "poster", None, ".jpg") == "Dune (2021) {tmdb-438631} {imdb-tt1160419}.jpg"
    assert af.build_download_filename(movie, "background", None, ".jpg") == "Dune (2021) {tmdb-438631} {imdb-tt1160419} - background.jpg"
    assert af.build_download_filename(movie, "logo", None, ".png") == "Dune (2021) {tmdb-438631} {imdb-tt1160419} - logo.png"
    tv = af.FinderItem(title="The Office", year=2005, tmdb_id=2316, tvdb_id=73244, media_type="tv")
    assert af.build_download_filename(tv, "poster", 3, ".jpg") == "The Office (2005) {tmdb-2316} {tvdb-73244} - Season 3.jpg"
    assert af.build_download_filename(tv, "poster", 0, ".jpg") == "The Office (2005) {tmdb-2316} {tvdb-73244} - Specials.jpg"


# ---------------------------------------------------------------- save into a scope


def test_save_candidate_writes_convention_name_into_subfolder(tmp_path, monkeypatch):
    monkeypatch.setattr(af, "_download_bytes", lambda *a, **k: _jpg_bytes())
    item = af.FinderItem(title="Dune", year=2021, tmdb_id=438631, media_type="movie")
    res = af.save_candidate(source_dir=tmp_path, is_asset_drive=True, item=item,
                            subtype="background", source="tmdb", ref="/x.jpg", session=None)
    assert res["written"] == "Dune (2021) {tmdb-438631} - background.jpg"
    assert res["subfolder"] == "backgrounds"
    assert (tmp_path / "backgrounds" / res["written"]).is_file()


def test_save_candidate_make_white_recolors_logo(tmp_path, monkeypatch):
    monkeypatch.setattr(af, "_download_bytes", lambda *a, **k: _png_bytes((200, 0, 0, 255)))
    item = af.FinderItem(title="Foo", year=2020, tmdb_id=7, media_type="movie")
    res = af.save_candidate(source_dir=tmp_path, is_asset_drive=True, item=item, subtype="logo",
                            source="tmdb", ref="/l.png", session=None, make_white=True)
    saved = tmp_path / "logos" / res["written"]
    assert saved.is_file()
    with Image.open(saved) as im:
        assert im.convert("RGBA").getpixel((10, 10))[:3] == (255, 255, 255)


def test_save_candidate_exists_then_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(af, "_download_bytes", lambda *a, **k: _jpg_bytes())
    item = af.FinderItem(title="Dune", year=2021, tmdb_id=438631, media_type="movie")
    kw = dict(source_dir=tmp_path, is_asset_drive=True, item=item, subtype="background",
              source="tmdb", ref="/x.jpg", session=None)
    first = af.save_candidate(**kw)
    assert first["status"] == "added" and first["archived"] is False
    # a second add without confirmation reports the collision and leaves the file untouched
    second = af.save_candidate(**kw)
    assert second["status"] == "exists" and second["archived"] is False
    assert (tmp_path / "backgrounds" / first["written"]).is_file()
    # confirming overwrites (archiving the previous copy to duplicates)
    third = af.save_candidate(**kw, confirm_overwrite=True)
    assert third["status"] == "added" and third["archived"] is True


def test_save_candidate_crop_makes_square(tmp_path, monkeypatch):
    # a 400x200 source cropped to a 150x150 square becomes the item's square art
    def _wide(*a, **k):
        buf = io.BytesIO()
        Image.new("RGB", (400, 200), (10, 20, 30)).save(buf, "JPEG")
        return buf.getvalue()

    monkeypatch.setattr(af, "_download_bytes", _wide)
    item = af.FinderItem(title="Dune", year=2021, tmdb_id=438631, media_type="movie")
    res = af.save_candidate(source_dir=tmp_path, is_asset_drive=True, item=item, subtype="squareart",
                            source="tmdb", ref="/p.jpg", session=None, crop=(50, 25, 150, 150))
    assert res["status"] == "added"
    assert res["written"] == "Dune (2021) {tmdb-438631} - squareart.jpg"
    with Image.open(tmp_path / "squareart" / res["written"]) as im:
        assert im.size == (150, 150)


# ---------------------------------------------------------------- Plex token + provider


def test_get_plex_token_reads_first_instance(test_db):
    assert af.get_plex_token(test_db) is None
    upsert_setting(test_db, "plex_instances", json.dumps([{"name": "P", "url": "http://x", "api_key": "TOKEN123"}]))
    test_db.commit()
    assert af.get_plex_token(test_db) == "TOKEN123"


def test_list_candidates_logos_tmdb_only_square_from_plex(monkeypatch):
    # Logos/backgrounds are TMDB only; Plex is used ONLY for square art (its logo/backdrop are
    # not offered). Square art has no dims from Plex, so they're probed.
    monkeypatch.setattr(af, "tmdb_images", lambda *a, **k: {
        "logos": [{"file_path": "/l.png", "width": 800, "height": 310, "vote_average": 9}]})
    monkeypatch.setattr(af, "_download_bytes", lambda *a, **k: _png_bytes((255, 255, 255, 255), size=(800, 310)))
    monkeypatch.setattr(af, "_probe_size", lambda *a, **k: (1400, 1400))

    class FakePlex:
        def images(self, item):
            return {"clearLogo": "https://provider-static.plex.tv/l.png",
                    "background": "https://provider-static.plex.tv/bg.jpg",
                    "backgroundSquare": "https://provider-static.plex.tv/sq.jpg"}

    item = af.FinderItem(title="X", year=2020, tmdb_id=1, media_type="movie")
    out = af.list_candidates(item, ["logo", "background", "squareart"], tmdb_api_key="k",
                             plex=FakePlex(), session=None, evaluate_white=True)
    # logos: TMDB only, no Plex clearLogo
    assert len(out["logos"]) == 1 and out["logos"][0]["source"] == "tmdb"
    assert out["logos"][0]["is_white"] is True
    # backgrounds: never Gracenote (Plex background dropped)
    assert not any(c["source"] == "gracenote" for c in out["backgrounds"])
    # square art: Plex only, dims probed
    assert len(out["squareart"]) == 1 and out["squareart"][0]["source"] == "gracenote"
    assert out["squareart"][0]["width"] == 1400


def test_plex_provider_parses_images(monkeypatch):
    import requests
    provider = af.PlexMetadataProvider("tok", requests.Session())

    def fake_get(path, **params):
        if path == "/library/metadata/matches":
            return {"MediaContainer": {"Metadata": [{"guid": "plex://movie/RK123"}]}}
        if path == "/library/metadata/RK123":
            return {"MediaContainer": {"Metadata": [{"Image": [
                {"type": "backgroundSquare", "url": "https://provider-static.plex.tv/sq.jpg"},
                {"type": "clearLogo", "url": "https://provider-static.plex.tv/logo.png"},
            ]}]}}
        return None

    monkeypatch.setattr(provider, "_get", fake_get)
    item = af.FinderItem(title="X", year=2020, tmdb_id=1, media_type="movie")
    assert provider.rating_key(item) == "RK123"
    imgs = provider.images(item)
    assert imgs["backgroundSquare"].endswith("sq.jpg")
    assert imgs["clearLogo"].endswith("logo.png")
