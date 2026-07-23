"""Tests for the batch Artwork Pull (service helpers, list parsing, endpoint)."""
import io
import json

from PIL import Image

import modules.artwork_pull as ap
import services.artwork_finder as af
from api.idarr import SETTING_MAKER_IDARR_CONFIG
from core.job_queue import job_queue
from models.job import JOB_STATUS_RUNNING, JOB_TYPE_ARTWORK_PULL, Job, create_job
from models.setting import upsert_setting


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (20, 20), (255, 255, 255, 255)).save(buf, "PNG")
    return buf.getvalue()


def _set_scope(db, source_dir):
    upsert_setting(db, "tmdb_api_key", "k")
    upsert_setting(db, SETTING_MAKER_IDARR_CONFIG, json.dumps({
        "sync_targets": [{"personal_drive_id": "d", "source_dir": str(source_dir), "is_asset_drive": True}]}))
    db.commit()


# ---------------------------------------------------------------- service helpers


def test_scope_index_basic_have(tmp_path):
    from services.idarr_assets import place_asset_file
    item = af.FinderItem(title="Dune", year=2021, tmdb_id=438631, media_type="movie")
    assert af.scope_has_artwork(af.build_scope_artwork_index(tmp_path), item, "logo") is False
    place_asset_file(tmp_path, "logo", af.build_filename(item, "logo"), _png(), is_asset_drive=True)
    index = af.build_scope_artwork_index(tmp_path)
    assert af.scope_has_artwork(index, item, "logo") is True
    assert af.scope_has_artwork(index, item, "background") is False   # per-subtype


def test_scope_index_matches_despite_wrong_imdb(tmp_path):
    # Regression: scope holds squareart with the canonical imdb; the Sonarr-sourced item carries a
    # different imdb (the CW-revival mixup). Exact-name checking pulled a duplicate -> IDarr
    # conflict; the id index must match on tmdb/tvdb regardless.
    sq = tmp_path / "squareart"
    sq.mkdir(parents=True)
    (sq / "Whose Line Is It Anyway (1998) {tmdb-61018} {tvdb-76808} {imdb-tt0163507} - squareart.jpg").write_bytes(_png())
    item = af.FinderItem(title="Whose Line Is It Anyway", year=1998, tmdb_id=61018,
                         tvdb_id=76808, imdb_id="tt2919910", media_type="tv")
    assert af.scope_has_artwork(af.build_scope_artwork_index(tmp_path), item, "squareart") is True


def test_scope_index_title_year_fallback_and_negatives(tmp_path):
    bg = tmp_path / "backgrounds"
    bg.mkdir(parents=True)
    (bg / "Dune (2021) {tmdb-438631} - background.jpg").write_bytes(_png())
    index = af.build_scope_artwork_index(tmp_path)
    # id-less item, same title+year -> matched via the title key
    assert af.scope_has_artwork(index, af.FinderItem(title="Dune", year=2021, tmdb_id=None, media_type="movie"), "background") is True
    # the 1984 film must NOT match the 2021 file (year-scoped title key, different tmdb)
    assert af.scope_has_artwork(index, af.FinderItem(title="Dune", year=1984, tmdb_id=841, media_type="movie"), "background") is False
    # a same-numbered TV item must not match the movie's tmdb key (namespace split)
    assert af.scope_has_artwork(index, af.FinderItem(title="Other Show", year=2020, tmdb_id=438631, media_type="tv"), "background") is False


def test_scope_index_matches_degraded_special_char_title(tmp_path):
    # Regression: scope has the canonical "WALL·E" file; Radarr delivers the unidecode-degraded
    # title ("WALLE"). Must match — by tmdb key, and by normalized-title fallback when id-less.
    sq = tmp_path / "squareart"
    sq.mkdir(parents=True)
    (sq / "WALL·E (2008) {tmdb-10681} {imdb-tt0910970} - squareart.jpg").write_bytes(_png())
    index = af.build_scope_artwork_index(tmp_path)
    degraded = af.FinderItem(title="WALLE", year=2008, tmdb_id=10681, media_type="movie")
    assert af.scope_has_artwork(index, degraded, "squareart") is True
    idless = af.FinderItem(title="WALL*E", year=2008, tmdb_id=None, media_type="movie")
    assert af.scope_has_artwork(index, idless, "squareart") is True


def test_canonicalize_title_uses_own_tmdb_id_only(monkeypatch):
    def fake_get(path, key, **params):
        assert path == "/movie/10681"        # keyed on the item's own tmdb id, never /find
        return {"id": 10681, "title": "WALL·E", "release_date": "2008-06-22"}
    monkeypatch.setattr(af, "_tmdb_get", fake_get)
    item = af.FinderItem(title="WALLE", year=2008, tmdb_id=10681, imdb_id="tt0910970", media_type="movie")
    af.canonicalize_title(item, "k")
    assert item.title == "WALL·E" and item.year == 2008 and item.tmdb_id == 10681
    # no tmdb id -> no lookup, title untouched
    monkeypatch.setattr(af, "_tmdb_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
    bare = af.FinderItem(title="X", year=2020, tmdb_id=None, media_type="movie")
    af.canonicalize_title(bare, "k")
    assert bare.title == "X"


def test_scope_index_collection_name_variants(tmp_path):
    bg = tmp_path / "backgrounds"
    bg.mkdir(parents=True)
    (bg / "Harry Potter Collection {tmdb-1241} - background.jpg").write_bytes(_png())
    index = af.build_scope_artwork_index(tmp_path)
    # plain name (no "Collection" suffix, no id) still counts as have
    item = af.FinderItem(title="Harry Potter", year=None, tmdb_id=None, media_type="collection")
    assert af.scope_has_artwork(index, item, "background") is True


def test_auto_pick_prefers_white_logo(monkeypatch):
    monkeypatch.setattr(af, "list_candidates", lambda *a, **k: {
        "logos": [{"source": "tmdb", "ref": "/a.png", "is_white": False, "off_white_pct": 40.0},
                  {"source": "tmdb", "ref": "/b.png", "is_white": True, "off_white_pct": 0.5}],
        "backgrounds": [{"source": "tmdb", "ref": "/bg.jpg"}],
        "squareart": [], "posters": [], "plex_available": False,
    })
    item = af.FinderItem(title="X", year=2020, tmdb_id=1, media_type="movie")
    out = af.auto_pick_missing(item, ["logo", "background", "squareart"], tmdb_api_key="k", plex=None, session=None)
    assert out["picks"]["logo"]["ref"] == "/b.png"
    assert out["picks"]["background"]["ref"] == "/bg.jpg"
    assert "squareart" in out["misses"]                      # no Plex token


def test_auto_pick_make_white_when_none_pass(monkeypatch):
    monkeypatch.setattr(af, "list_candidates", lambda *a, **k: {
        "logos": [{"source": "tmdb", "ref": "/a.png", "is_white": False, "off_white_pct": 40.0},
                  {"source": "tmdb", "ref": "/c.png", "is_white": False, "off_white_pct": 12.0}],
        "backgrounds": [], "squareart": [], "posters": [], "plex_available": False,
    })
    item = af.FinderItem(title="X", year=2020, tmdb_id=1, media_type="movie")
    out = af.auto_pick_missing(item, ["logo"], tmdb_api_key="k", plex=None, session=None, make_white_logos=True)
    assert out["picks"]["logo"]["ref"] == "/c.png"           # least off-white
    assert out["picks"]["logo"]["make_white"] is True

    out2 = af.auto_pick_missing(item, ["logo"], tmdb_api_key="k", plex=None, session=None)  # default: skip
    assert "logo" not in out2["picks"] and "logo" in out2["misses"]


def test_resolve_identity_via_find_sets_type_title_year(monkeypatch):
    def fake_get(path, key, **params):
        if path.startswith("/find/"):
            return {"tv_results": [{"id": 2316, "name": "The Office", "first_air_date": "2005-03-24"}],
                    "movie_results": []}
        return None
    monkeypatch.setattr(af, "_tmdb_get", fake_get)
    item = af.FinderItem(title="tt0386676", year=None, tmdb_id=None, imdb_id="tt0386676", media_type="movie")
    out = af.resolve_tmdb_identity(item, "k", known_identity=False)
    assert out is not None
    assert (out.tmdb_id, out.media_type, out.title, out.year) == (2316, "tv", "The Office", 2005)


def test_resolve_bare_id_uses_title_for_namespace(monkeypatch):
    # tmdb 2316 exists as BOTH a movie and a show; the title picks the right one (the show).
    def fake_get(path, key, **params):
        if path == "/movie/2316":
            return {"id": 2316, "title": "Some Movie", "release_date": "1999-01-01"}
        if path == "/tv/2316":
            return {"id": 2316, "name": "The Office", "first_air_date": "2005-03-24"}
        return None
    monkeypatch.setattr(af, "_tmdb_get", fake_get)
    item = af.FinderItem(title="The Office", year=2005, tmdb_id=2316, media_type="movie")
    out = af.resolve_tmdb_identity(item, "k", known_identity=False)
    assert out.media_type == "tv" and out.title == "The Office"


def test_resolve_bare_id_no_title_defaults_movie(monkeypatch):
    def fake_get(path, key, **params):
        if path == "/movie/5":
            return {"id": 5, "title": "Movie Five", "release_date": "2000-01-01"}
        if path == "/tv/5":
            return {"id": 5, "name": "Show Five", "first_air_date": "2001-01-01"}
        return None
    monkeypatch.setattr(af, "_tmdb_get", fake_get)
    item = af.FinderItem(title="", year=None, tmdb_id=5, media_type="movie")
    out = af.resolve_tmdb_identity(item, "k", known_identity=False)
    assert out.media_type == "movie"


def test_resolve_identity_trusts_known(monkeypatch):
    monkeypatch.setattr(af, "_tmdb_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call")))
    item = af.FinderItem(title="Dune", year=2021, tmdb_id=438631, media_type="movie")
    assert af.resolve_tmdb_identity(item, "k", known_identity=True) is item   # no network


def test_is_unreleased_matches_unmatched_classification():
    mk = lambda s: af.FinderItem(title="X", year=2029, tmdb_id=1, media_type="movie", status=s)
    assert mk("announced").is_unreleased and mk("Upcoming").is_unreleased and mk("tba").is_unreleased
    assert not mk("released").is_unreleased and not mk("continuing").is_unreleased
    assert not mk(None).is_unreleased          # no status (drives/scope/list items) = not skipped


def test_items_from_libraries_carry_status(monkeypatch):
    class FakeSvc:
        def __init__(self, db):
            pass

        def get_media_from_instances(self, log_tag=None, setting_key=None):
            return {"movies": [{"title": "Avatar 4", "year": 2029, "tmdb_id": 5, "status": "announced"}],
                    "series": [], "collections": []}

    monkeypatch.setattr("services.poster_renamer.PosterRenameService", FakeSvc)
    items = ap._items_from_libraries(None)
    assert items[0].status == "announced" and items[0].is_unreleased


# ---------------------------------------------------------------- list parsing


def test_items_from_list_requires_title_year_id():
    items = ap._items_from_list(
        "Back to the Future (1985) {tmdb-105}\n"       # valid: title + year + id
        "Dune (2021)\n"                                # skipped: no id
        "{tmdb-438631}\n"                              # skipped: no title/year
        "Severance\n"                                  # skipped: no year/id
        "Loki (2021)\n"                                # skipped: no id
        "Harry Potter Collection {tmdb-1241}"          # valid collection: title + id, no year needed
    )
    assert {i.tmdb_id for i in items} == {105, 1241}
    assert all(i.imdb_id is None for i in items)       # imdb ids are not parsed from lists
    coll = next(i for i in items if i.tmdb_id == 1241)
    assert coll.media_type == "collection" and coll.title == "Harry Potter Collection"


# ---------------------------------------------------------------- endpoint


def test_batch_pull_queues_job(client, test_db, tmp_path, monkeypatch):
    _set_scope(test_db, tmp_path)
    submitted = {}
    monkeypatch.setattr(job_queue, "submit", lambda fn, jid, *a, **k: submitted.setdefault("id", jid))
    resp = client.post("/api/artwork-finder/batch-pull",
                       json={"sync_target_index": 0, "source": "scope", "types": ["logo"]})
    assert resp.status_code == 200, resp.text
    jid = resp.json()["job_id"]
    assert submitted["id"] == jid
    assert test_db.query(Job).filter(Job.id == jid, Job.job_type == JOB_TYPE_ARTWORK_PULL).first() is not None


def test_batch_pull_passes_force_refetch(client, test_db, tmp_path, monkeypatch):
    _set_scope(test_db, tmp_path)
    captured = {}
    monkeypatch.setattr(job_queue, "submit", lambda fn, jid, *a, **k: captured.setdefault("config", a[-1]))
    first = client.post("/api/artwork-finder/batch-pull",
                        json={"sync_target_index": 0, "source": "scope", "types": ["logo"], "force_refetch": True})
    assert captured["config"]["force_refetch"] is True
    # finalize the first job so the second isn't rejected by the already-active guard
    job = test_db.query(Job).filter(Job.id == first.json()["job_id"]).first()
    job.status = "completed"
    test_db.commit()
    captured.clear()
    client.post("/api/artwork-finder/batch-pull",
                json={"sync_target_index": 0, "source": "scope", "types": ["logo"]})
    assert captured["config"]["force_refetch"] is False


def test_batch_pull_409_when_active(client, test_db, tmp_path, monkeypatch):
    _set_scope(test_db, tmp_path)
    create_job(test_db, JOB_TYPE_ARTWORK_PULL, "running", status=JOB_STATUS_RUNNING)
    test_db.commit()
    monkeypatch.setattr(job_queue, "submit", lambda *a, **k: None)
    resp = client.post("/api/artwork-finder/batch-pull",
                       json={"sync_target_index": 0, "source": "scope", "types": ["logo"]})
    assert resp.status_code == 409


def test_batch_pull_requires_types(client, test_db, tmp_path):
    _set_scope(test_db, tmp_path)
    resp = client.post("/api/artwork-finder/batch-pull",
                       json={"sync_target_index": 0, "source": "scope", "types": []})
    assert resp.status_code == 400
