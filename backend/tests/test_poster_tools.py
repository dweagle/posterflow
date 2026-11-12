"""The Tools page's poster tools: they cover every enabled library (not the overlay
selection); reset also forgets the overlay badge records of the items it touches."""

import hashlib
import json
import os

from models.job import Job
from models.overlay import OverlayItem
from models.plex_upload import PlexUploadRecord
from services.plex_bundles import load_plex_metadata_paths, plex_bundle_dir, save_plex_metadata_paths
from services.poster_tools import PosterTools
import pytest
from test_overlays import INSTANCE, LIBRARIES, FakeClient, FakeMetadata, _enabled_config, _engine, _item, _sources, _state_items

INSTANCE_JF = {"name": "jf", "url": "http://jf", "api_key": "k", "type": "jellyfin"}
LIBRARIES_JF = [{"instance_name": "jf", "libraries": [{"key": "9", "title": "JF Movies", "type": "movie", "enabled": True}]}]


class FakeJellyfin(FakeClient):
    server_type = "jellyfin"

    def __init__(self):
        super().__init__()
        self.items = {"9": [_item(90, "movie", "Jelly", 2021, tmdb=9, library="9")]}
        self.seasons = {}
        self.tasks = []
        self.reset_calls = []

    def run_scheduled_task(self, key):
        self.tasks.append(key)
        return True

    def reset_image(self, item, kind):
        self.reset_calls.append(item.item_id if kind == "poster" else f"{item.item_id}:{kind}")
        return True


@pytest.fixture
def isolated_overlay_files(tmp_path):
    return tmp_path


def _selected_upload(client):
    """FakeClient answers the posters lookup like Plex: the last upload, by its SHA1."""
    client.selected_poster_key = lambda item: next(
        (f"upload://posters/{hashlib.sha1(raw).hexdigest()}" for iid, raw in reversed(client.uploads) if iid == item.item_id), "")
    for lib in list(client.items.values()) + list(client.seasons.values()):
        for it in lib:
            it.client = client


def _tools(test_db, client):
    return PosterTools(test_db, instances=[INSTANCE], library_configs=LIBRARIES, client_factory=lambda inst: client)


def _bundle(root, guid, kind="show"):
    folder = plex_bundle_dir(str(root), guid, kind) / "Uploads" / "posters"
    folder.mkdir(parents=True)
    return folder


def test_cleanup_prunes_every_enabled_library_not_just_the_overlay_selection(test_db, isolated_overlay_files):
    root = isolated_overlay_files / "plexmeta"
    client = FakeClient()
    _selected_upload(client)
    show, movie = client.items["2"][1], client.items["1"][0]      # "Done" (show 30), "Film" (movie 10)
    show.guid, movie.guid = "plex://show/abc", "plex://movie/xyz"
    show_bundle, movie_bundle = _bundle(root, show.guid), _bundle(root, movie.guid, "movie")
    save_plex_metadata_paths(test_db, {"srv": str(root)})

    # Overlays only cover library 2; badging the show puts the composite in the bundle.
    cfg = _enabled_config()
    cfg["libraries"] = ["srv:2"]; cfg["all_libraries"] = False
    engine = _engine(test_db, client, FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}}), cfg,
                     sources=_sources(isolated_overlay_files, 30))
    engine.run()
    engine.flush()
    badged = client.uploads[-1][1]
    (show_bundle / hashlib.sha1(badged).hexdigest()).write_bytes(badged)
    (show_bundle / "old_show").write_bytes(b"a" * 100)
    # The movie library is outside the overlay selection, but Asset Upload put a poster there.
    asset = b"movie-poster-bytes"
    client.uploads.append(("10", asset))
    (movie_bundle / hashlib.sha1(asset).hexdigest()).write_bytes(asset)
    (movie_bundle / "old_movie").write_bytes(b"b" * 50)
    # Backgrounds and square art live in their own upload folders with their own selected image;
    # a logo folder that was never created costs no lookup at all.
    art, square = movie_bundle.parent / "art", show_bundle.parent / "squareArt"
    art.mkdir(); square.mkdir()
    (art / "artsha").write_bytes(b"c" * 10); (art / "old_art").write_bytes(b"d" * 20)
    (square / "sqsha").write_bytes(b"e" * 5); (square / "old_sq").write_bytes(b"f" * 7)
    lookups = []
    client.selected_image_key = lambda item, kind: lookups.append((item.item_id, kind)) or {("10", "background"): "upload://art/artsha", ("30", "squareart"): "upload://squareArt/sqsha"}.get((item.item_id, kind), "")
    # A collection: Plex hashes its collection:// guid under Metadata/Collections.
    coll = _item(40, "collection", "Films", None, library="1")
    coll.guid, coll.client = "collection://abc", client
    client.get_collections = lambda lib_key: [coll] if str(lib_key).endswith("1") else []
    coll_poster = b"collection-poster"
    client.uploads.append(("40", coll_poster))
    coll_bundle = _bundle(root, coll.guid, "collection")
    (coll_bundle / hashlib.sha1(coll_poster).hexdigest()).write_bytes(coll_poster)
    (coll_bundle / "old_coll").write_bytes(b"g" * 9)

    rehearsal = _tools(test_db, client)
    assert rehearsal.plex_cleanup(dry_run=True)["plex_pruned"] == 5
    assert (show_bundle / "old_show").is_file() and (movie_bundle / "old_movie").is_file()  # counted, not deleted
    assert sorted(set(lookups)) == [("10", "background"), ("30", "squareart")]  # only kinds with an upload folder are asked about

    stats = _tools(test_db, client).plex_cleanup()
    assert stats["plex_pruned"] == 5 and stats["plex_freed_bytes"] == 150 + 20 + 7 + 9 and stats["items"] == 5  # 1 movie, 2 shows, 1 season, 1 collection
    assert [f.name for f in show_bundle.iterdir()] == [hashlib.sha1(badged).hexdigest()]
    assert [f.name for f in movie_bundle.iterdir()] == [hashlib.sha1(asset).hexdigest()]
    assert [f.name for f in art.iterdir()] == ["artsha"] and [f.name for f in square.iterdir()] == ["sqsha"]
    assert [f.name for f in coll_bundle.iterdir()] == [hashlib.sha1(coll_poster).hexdigest()]
    assert str(coll_bundle).split("/plexmeta/")[1].startswith("Collections/")


def test_cleanup_only_asks_plex_about_folders_that_can_hold_something_superseded(test_db, isolated_overlay_files):
    """A folder with no upload, or just one, has nothing in it to supersede: Plex is not asked
    what it shows there. A folder with two or more costs one lookup."""
    root = isolated_overlay_files / "plexmeta"
    client = FakeClient()
    _selected_upload(client)
    movie, live, done = client.items["1"][0], client.items["2"][0], client.items["2"][1]
    movie.guid, live.guid, done.guid = "plex://movie/xyz", "plex://show/live", "plex://show/abc"
    movie_bundle, done_bundle = _bundle(root, movie.guid, "movie"), _bundle(root, done.guid)
    _bundle(root, live.guid)  # emptied, as a reset leaves it
    (movie_bundle.parent / "art").mkdir()
    for item_id, bundle, raws in (("10", movie_bundle, [b"movie"]), ("30", done_bundle, [b"old", b"done"])):
        for raw in raws:
            client.uploads.append((item_id, raw))
            (bundle / hashlib.sha1(raw).hexdigest()).write_bytes(raw)
    asked = []
    shown = client.selected_poster_key
    client.selected_poster_key = lambda item: asked.append((item.item_id, "poster")) or shown(item)
    client.selected_image_key = lambda item, kind: asked.append((item.item_id, kind)) or ""
    save_plex_metadata_paths(test_db, {"srv": str(root)})
    stats = _tools(test_db, client).plex_cleanup()
    assert asked == [("30", "poster")] and (stats["items"], stats["plex_pruned"]) == (4, 1)
    assert [f.name for f in done_bundle.iterdir()] == [hashlib.sha1(b"done").hexdigest()]
    assert [f.name for f in movie_bundle.iterdir()] == [hashlib.sha1(b"movie").hexdigest()]


def test_cleanup_needs_a_metadata_path_and_a_reachable_bundle(test_db, isolated_overlay_files):
    client = FakeClient()
    _selected_upload(client)
    assert _tools(test_db, client).plex_cleanup()["items"] == 0  # no path set: nothing walked
    show = client.items["2"][1]
    show.guid = "plex://show/abc"
    client.uploads.append(("30", b"x"))
    save_plex_metadata_paths(test_db, {"srv": str(isolated_overlay_files / "nowhere")})
    stats = _tools(test_db, client).plex_cleanup()
    assert stats["items"] == 0 and stats["skipped"] == ["srv"] and stats["plex_pruned"] == 0  # not a Metadata folder: nothing walked
    # A real Metadata folder, but another server's: the first item with an upload has no bundle
    # there, and nothing of this server has been found under it, so the run stops at once.
    elsewhere = isolated_overlay_files / "elsewhere"
    (elsewhere / "Movies").mkdir(parents=True)
    save_plex_metadata_paths(test_db, {"srv": str(elsewhere)})
    stats = _tools(test_db, client).plex_cleanup()
    assert stats["items"] == 3 and stats["stopped"] == ["srv"] and stats["plex_pruned"] == 0  # Film, Live, Done; the season is never reached
    assert "Done" in stats["problems"]["srv"] and str(elsewhere) in stats["problems"]["srv"]


def test_cleanup_keeps_going_once_a_bundle_proves_the_path(test_db, isolated_overlay_files):
    root = isolated_overlay_files / "plexmeta"
    client = FakeClient()
    _selected_upload(client)
    movie, show = client.items["1"][0], client.items["2"][1]
    movie.guid, show.guid = "plex://movie/xyz", "plex://show/abc"
    movie_bundle = _bundle(root, movie.guid, "movie")
    poster = b"movie-poster"
    client.uploads.append(("10", poster))
    (movie_bundle / hashlib.sha1(poster).hexdigest()).write_bytes(poster)
    (movie_bundle / "old").write_bytes(b"z" * 3)
    client.uploads.append(("30", b"show-poster"))  # its bundle is missing: one odd item, not a wrong path
    save_plex_metadata_paths(test_db, {"srv": str(root)})
    stats = _tools(test_db, client).plex_cleanup()
    assert stats["items"] == 4 and stats["plex_pruned"] == 1 and stats["stopped"] == [] and stats["problems"] == {}


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_cleanup_skips_and_counts_folders_it_may_not_delete_in(test_db, isolated_overlay_files):
    root = isolated_overlay_files / "plexmeta"
    client = FakeClient()
    _selected_upload(client)
    movie = client.items["1"][0]
    movie.guid = "plex://movie/xyz"
    bundle = _bundle(root, movie.guid, "movie")
    poster = b"p"
    client.uploads.append(("10", poster))
    (bundle / hashlib.sha1(poster).hexdigest()).write_bytes(poster)
    (bundle / "old").write_bytes(b"q")
    save_plex_metadata_paths(test_db, {"srv": str(root)})
    show = client.items["2"][1]
    show.guid = "plex://show/abc"
    show_bundle = _bundle(root, show.guid)
    show_poster = b"s"
    client.uploads.append(("30", show_poster))
    (show_bundle / hashlib.sha1(show_poster).hexdigest()).write_bytes(show_poster)
    (show_bundle / "old_show").write_bytes(b"t")
    bundle.chmod(0o555)  # the movie's folder is not writable, the show's is
    try:
        rehearsal = _tools(test_db, client).plex_cleanup(dry_run=True)
        assert rehearsal["unwritable"] == 1 and rehearsal["plex_pruned"] == 1 and rehearsal["stopped"] == []
        stats = _tools(test_db, client).plex_cleanup()
        assert stats["unwritable"] == 1 and stats["plex_pruned"] == 1 and stats["items"] == 4
        assert (bundle / "old").exists() and not (show_bundle / "old_show").exists()
    finally:
        bundle.chmod(0o755)


def test_cleanup_job_fails_when_nothing_could_be_removed_for_want_of_access(test_db, monkeypatch):
    from modules import tools as tools_module
    stats = dict(PosterTools(test_db).stats)
    stats.update(unwritable=3, plex_pruned=0, instances=1)
    monkeypatch.setattr(tools_module, "PosterTools", lambda db: type("T", (), {"plex_cleanup": lambda self, **kw: stats})())
    with pytest.raises(RuntimeError) as err:
        tools_module._plex_cleanup(test_db, lambda *a: None, None, True, {"kinds": ["poster"]})
    assert "3 bundle folder(s) are not writable" in str(err.value) and "PGID" in str(err.value)
    stats.update(plex_pruned=5)
    detail = tools_module._plex_cleanup(test_db, lambda *a: None, None, False, {"kinds": ["poster"]})
    assert detail.startswith("5 superseded upload(s) removed") and "3 bundle folder(s) not writable" in detail


def test_cleanup_accepts_the_plex_media_server_folder(test_db, isolated_overlay_files):
    pms = isolated_overlay_files / "Plex Media Server"
    root = pms / "Metadata"
    client = FakeClient()
    _selected_upload(client)
    movie = client.items["1"][0]
    movie.guid = "plex://movie/xyz"
    bundle = _bundle(root, movie.guid, "movie")
    poster = b"p"
    client.uploads.append(("10", poster))
    (bundle / hashlib.sha1(poster).hexdigest()).write_bytes(poster)
    (bundle / "old").write_bytes(b"q")
    save_plex_metadata_paths(test_db, {"srv": str(pms)})
    assert load_plex_metadata_paths(test_db) == {"srv": str(root)}            # what the tools use
    assert load_plex_metadata_paths(test_db, resolve=False) == {"srv": str(pms)}  # what the page shows
    assert _tools(test_db, client).plex_cleanup()["plex_pruned"] == 1 and not (bundle / "old").exists()


def test_reset_covers_every_library_and_forgets_badges(test_db, isolated_overlay_files, monkeypatch):
    root = isolated_overlay_files / "plexmeta"
    client = FakeClient()
    _selected_upload(client)
    client.reset_calls = []
    client.reset_image = lambda item, kind: client.reset_calls.append(item.item_id if kind == "poster" else f"{item.item_id}:{kind}") or True
    show = client.items["2"][1]
    show.guid = "plex://show/abc"
    bundle = _bundle(root, show.guid)
    save_plex_metadata_paths(test_db, {"srv": str(root)})
    engine = _engine(test_db, client, FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}}),
                     sources=_sources(isolated_overlay_files, 30))
    engine.run()
    engine.flush()
    (bundle / hashlib.sha1(client.uploads[-1][1]).hexdigest()).write_bytes(client.uploads[-1][1])
    assert _state_items(test_db)
    # Upload records: the reset forgets only the reset libraries, and only for the reset kinds.
    def record(path, keys):
        test_db.add(PlexUploadRecord(file_path=path, uploaded_to_libraries=json.dumps(["Movies"]), uploaded_to_library_keys=json.dumps(keys),
                                     uploaded_to_rating_keys="[]", uploaded_editions="[]", uploaded_media_types=json.dumps(["shows"])))
    record("/assets/Done (2010)/poster.jpg", ["jf:9", "srv:2"])
    record("/assets/Done (2010)/logo.png", ["jf:9", "srv:2"])
    record("/assets/Film (2026)/poster.jpg", ["srv:1"])
    test_db.commit()
    def keys(path):
        rec = test_db.query(PlexUploadRecord).filter_by(file_path=path).first()
        return json.loads(rec.uploaded_to_library_keys) if rec else None

    rehearsal = _tools(test_db, client).reset_posters(dry_run=True, kinds=["poster"])
    assert rehearsal["reset"] == 4 and client.reset_calls == [] and keys("/assets/Done (2010)/poster.jpg") == ["jf:9", "srv:2"]
    assert _tools(test_db, client).reset_posters(dry_run=True)["reset"] == 13  # every kind: 4 per movie/show, posters only for the season

    # Logos alone: seasons carry none, badge records (posters) stay, and only the logo record loses this server.
    stats = _tools(test_db, client).reset_posters(kinds=["logo"])
    assert stats["reset"] == 3 and sorted(client.reset_calls) == ["10:logo", "20:logo", "30:logo"] and _state_items(test_db)
    assert keys("/assets/Done (2010)/logo.png") == ["jf:9"] and keys("/assets/Done (2010)/poster.jpg") == ["jf:9", "srv:2"]
    client.reset_calls.clear()

    stats = _tools(test_db, client).reset_posters(kinds=["poster"])
    assert stats["reset"] == 4 and stats["failed"] == 0 and sorted(client.reset_calls) == ["10", "20", "21", "30"]
    assert list(bundle.iterdir()) == []  # every upload gone
    assert _state_items(test_db) == {}
    assert keys("/assets/Done (2010)/poster.jpg") == ["jf:9"]  # Jellyfin still cached; Plex forgotten
    assert keys("/assets/Film (2026)/poster.jpg") is None  # nowhere else cached: record gone
    assert keys("/assets/Done (2010)/logo.png") == ["jf:9"]  # untouched by a poster reset


def test_a_poster_reset_forgets_badges_of_items_the_server_no_longer_lists(test_db, isolated_overlay_files):
    """A renamed folder makes Plex drop a title and add it again under a new id. The badge
    record under the old id matches nothing the reset walks, and must not stay behind."""
    client = FakeClient()
    client.reset_image = lambda item, kind: True

    def badge(instance, item_id, library, kind="movie"):
        test_db.add(OverlayItem(instance=instance, item_id=item_id, item_type=kind, library=library, title=f"{kind} {item_id}", sig="s"))

    badge("srv", "999", "1")                    # its id is gone from library 1
    badge("srv", "997", "1", "collection")      # so is this collection
    badge("srv", "998", "7")                    # a library the reset does not list
    badge("other", "999", "1")                  # another server's record
    test_db.commit()

    _tools(test_db, client).reset_posters(dry_run=True)
    _tools(test_db, client).reset_posters(kinds=["logo", "background"])
    assert set(_state_items(test_db)) == {"srv|999", "srv|997", "srv|998", "other|999"}  # only a poster reset touches badge records

    _tools(test_db, client).reset_posters(kinds=["poster"])
    assert set(_state_items(test_db)) == {"srv|998", "other|999"}

    # A library that lists nothing says nothing about what is gone.
    badge("srv", "999", "1")
    test_db.commit()
    client.get_library_items = lambda lib_key, item_types=None: []
    _tools(test_db, client).reset_posters(kinds=["poster"])
    assert "srv|999" in _state_items(test_db)


def test_reset_unlocks_a_batch_at_once_and_skips_what_stays_locked(test_db, isolated_overlay_files, monkeypatch):
    """Titles are reset a batch at a time: one unlock per batch for the titles that share their
    kinds (a season only carries a poster), then each title's kinds in one call. A title that
    could not be unlocked is not reset, and its kinds count as failed."""
    from services import poster_tools

    monkeypatch.setattr(poster_tools, "RESET_BATCH", 2)
    client = FakeClient()
    _selected_upload(client)
    calls = []
    client.reset_images = lambda item, kinds, unlocked=False: calls.append((item.item_id, list(kinds), unlocked)) or {kind: True for kind in kinds}
    client.stay_locked = {"30"}
    stats = _tools(test_db, client).reset_posters(kinds=["poster", "logo"])
    assert client.unlocked == [(["10", "20"], ["poster", "logo"]), (["30"], ["poster", "logo"]), (["21"], ["poster"])]
    assert sorted(calls) == [("10", ["poster", "logo"], True), ("20", ["poster", "logo"], True), ("21", ["poster"], True)]
    assert (stats["items"], stats["reset"], stats["failed"]) == (4, 5, 2)


def test_reset_reports_each_title_once_it_is_done(test_db, isolated_overlay_files):
    """The job bar hears about a title when its reset is finished: one report per title, in
    order, never ahead of the work. A stop that arrives with a report lets the batch under way
    be accounted for in full before the run ends."""
    from core.job_cancel import JobCancelled

    def reset_client():
        client = FakeClient()
        _selected_upload(client)
        client.finished = []
        client.reset_images = lambda item, kinds, unlocked=False: client.finished.append(item.item_id) or {kind: True for kind in kinds}
        return client

    client, seen = reset_client(), []
    _tools(test_db, client).reset_posters(kinds=["poster"], progress=lambda phase, current, total, message: seen.append((current, total, message, len(client.finished))))
    assert [(current, total) for current, total, _message, _finished in seen] == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert all(finished >= current for current, _total, _message, finished in seen)
    assert all(message.startswith("Resetting: ") for _current, _total, message, _finished in seen)

    client, tools = reset_client(), None

    def stop_at_the_second(phase, current, total, message):
        if current == 2:
            raise JobCancelled("stopped")

    tools = _tools(test_db, client)
    with pytest.raises(JobCancelled):
        tools.reset_posters(kinds=["poster"], progress=stop_at_the_second)
    assert sorted(client.finished) == ["10", "20", "21", "30"] and (tools.stats["reset"], tools.stats["failed"]) == (4, 0)


def test_a_stopped_reset_still_forgets_what_it_had_reset(test_db, isolated_overlay_files, monkeypatch):
    """Stopping the job part-way: the titles reset by then have lost their uploads on the
    server, so their badge records and their libraries' upload records are still dropped, and
    the next upload run sends them again. What was not reached keeps its badge record."""
    from core.job_cancel import JobCancelled
    from services import poster_tools

    monkeypatch.setattr(poster_tools, "RESET_BATCH", 1)
    client = FakeClient()
    _selected_upload(client)
    client.reset_calls = []
    client.reset_image = lambda item, kind: client.reset_calls.append(item.item_id) or True
    meta = FakeMetadata({1: {"status": "Ended", "first_air_date": "2020-01-01"}, 2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    engine = _engine(test_db, client, meta, sources=_sources(isolated_overlay_files, 20, 30))
    engine.run()
    engine.flush()
    assert set(_state_items(test_db)) == {"srv|20", "srv|30"}
    for path, keys in (("/assets/Live (2020)/poster.jpg", ["srv:2"]), ("/assets/Film (2026)/poster.jpg", ["srv:1"])):
        test_db.add(PlexUploadRecord(file_path=path, uploaded_to_libraries=json.dumps(["x"]), uploaded_to_library_keys=json.dumps(keys),
                                     uploaded_to_rating_keys="[]", uploaded_editions="[]", uploaded_media_types=json.dumps(["shows"])))
    test_db.commit()
    asked = []

    def stop_after_the_first(job_id):
        asked.append(job_id)
        if len(asked) > 1:
            raise JobCancelled("stopped")

    monkeypatch.setattr(poster_tools, "check_cancelled", stop_after_the_first)
    tv_only = [{"instance_name": "srv", "libraries": [LIBRARIES[0]["libraries"][1]]}]
    tools = PosterTools(test_db, instances=[INSTANCE], library_configs=tv_only, client_factory=lambda inst: client)
    with pytest.raises(JobCancelled):
        tools.reset_posters(kinds=["poster"], job_id=7)
    assert client.reset_calls == ["20"] and tools.stats["reset"] == 1
    assert set(_state_items(test_db)) == {"srv|30"}  # the reset show's badge record is gone, the other one stays
    left = {r.file_path for r in test_db.query(PlexUploadRecord)}
    assert left == {"/assets/Film (2026)/poster.jpg"}  # the TV library's poster record is forgotten, the untouched library's is kept


def test_tool_job_records_the_outcome(test_db, isolated_overlay_files, monkeypatch):
    from models.job import JOB_TYPE_TOOLS, create_job
    from modules import tools as tools_mod

    client = FakeClient()
    _selected_upload(client)
    monkeypatch.setattr(tools_mod, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(tools_mod, "PosterTools", lambda db: _tools(db, client))
    job = create_job(test_db, job_type=JOB_TYPE_TOOLS, message="queued")
    db_close = test_db.close
    monkeypatch.setattr(test_db, "close", lambda: None)
    try:
        tools_mod.run_tool_background_job(job.id, "plex_reset_posters", True, {"kinds": ["poster"]})
    finally:
        monkeypatch.setattr(test_db, "close", db_close)
    job = test_db.query(Job).filter(Job.id == job.id).one()
    assert job.status == "completed" and "4 reset, 0 failed" in job.message and "Dry Run" in job.message


def _both(test_db, plex, jf):
    return PosterTools(test_db, instances=[INSTANCE, INSTANCE_JF], library_configs=LIBRARIES + LIBRARIES_JF,
                       client_factory=lambda inst: {"srv": plex, "jf": jf}[inst["name"]])


def test_tools_are_scoped_per_server_type(test_db, isolated_overlay_files, monkeypatch):
    plex, jf = FakeClient(), FakeJellyfin()
    _selected_upload(plex)
    plex.reset_calls = []
    plex.reset_image = lambda item, kind: plex.reset_calls.append(item.item_id if kind == "poster" else f"{item.item_id}:{kind}") or True

    assert _both(test_db, plex, jf).clean_jellyfin_image_cache()["asked"] == 1 and jf.tasks == ["DeleteCacheFiles"]
    stats = _both(test_db, plex, jf).reset_posters(server_type="jellyfin", kinds=["poster"])
    assert stats["reset"] == 1 and jf.reset_calls == ["90"] and plex.reset_calls == []
    assert _both(test_db, plex, jf).reset_posters(server_type="jellyfin", kinds=["squareart"])["reset"] == 0  # Jellyfin has no square art
    assert jf.reset_calls == ["90"]
    stats = _both(test_db, plex, jf).reset_posters(server_type="plex", kinds=["poster"])
    assert stats["reset"] == 4 and sorted(plex.reset_calls) == ["10", "20", "21", "30"] and jf.reset_calls == ["90"]
    # The Plex cleanup never walks a Jellyfin library, even with a path set for it.
    (isolated_overlay_files / "meta" / "Movies").mkdir(parents=True)
    save_plex_metadata_paths(test_db, {"srv": str(isolated_overlay_files / "meta"), "jf": str(isolated_overlay_files / "meta")})
    assert _both(test_db, plex, jf).plex_cleanup()["items"] == 4


def test_cleanup_and_reset_keep_what_every_copy_of_a_shared_title_shows(test_db, isolated_overlay_files):
    """The same movie in two libraries (Movies and 4K Movies) is two items with ONE Plex bundle:
    a prune for one keeps what the other shows now, so only old uploads go."""
    root = isolated_overlay_files / "plexmeta"
    client = FakeClient()
    client.items["3"] = [_item(11, "movie", "Film", 2026, tmdb=9, library="3")]  # the 4K copy of "Film"
    _selected_upload(client)
    reset = set()  # copies put back on Plex's own poster
    client.selected_image_key = lambda item, kind: "metadata://posters/agent" if item.item_id in reset else client.selected_poster_key(item)
    for it in client.items["1"] + client.items["3"]:
        it.guid = "plex://movie/xyz"
    bundle = _bundle(root, "plex://movie/xyz", "movie")
    hd, uhd = b"hd-poster", b"4k-poster"
    client.uploads += [("10", hd), ("11", uhd)]  # each copy shows its own upload
    for raw in (hd, uhd):
        (bundle / hashlib.sha1(raw).hexdigest()).write_bytes(raw)
    (bundle / "old").write_bytes(b"x" * 10)
    save_plex_metadata_paths(test_db, {"srv": str(root)})
    libraries = [{"instance_name": "srv", "libraries": LIBRARIES[0]["libraries"] + [{"key": "3", "title": "4K Movies", "type": "movie", "enabled": True}]}]

    def tools():
        return PosterTools(test_db, instances=[INSTANCE], library_configs=libraries, client_factory=lambda inst: client)

    stats = tools().plex_cleanup()
    assert (stats["shared"], stats["plex_pruned"]) == (0, 1)
    assert {f.name for f in bundle.iterdir()} == {hashlib.sha1(hd).hexdigest(), hashlib.sha1(uhd).hexdigest()}
    # Resetting both: the first keeps what the 4K copy still shows; once both are reset, nothing is left to keep.
    client.reset_image = lambda item, kind: reset.add(item.item_id) or True
    stats = tools().reset_posters(kinds=["poster"])
    assert stats["shared"] == 0 and stats["plex_pruned"] == 2 and list(bundle.iterdir()) == []
