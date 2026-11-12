"""A full run handles several assets at once: uploads overlap, the upload logic takes turns."""

import threading
import time
from types import SimpleNamespace

import pytest

from core.job_cancel import JobCancelled
from models.plex_upload import PlexUploadRecord
from services import plex_upload
from services.plex_upload import PlexUploadService
from tests.plex_upload_fakes import _FakePlexItem, _WRAP_CLIENT
from util.media_server.types import MediaServerItem


def _movies(tmp_path, count):
    """`count` placed posters, each matching its own movie."""
    assets, index = [], {"movies": {}, "shows": {}, "collections": {}}
    for n in range(count):
        path = tmp_path / f"Movie {n} (2026)" / "poster.jpg"
        path.parent.mkdir(parents=True)
        path.write_bytes(f"poster {n}".encode())
        key = f"sidebyside{n}"
        assets.append({"media_key": key, "path": str(path), "display_name": f"Movie {n}", "asset_type": "main", "folder_year": 2026})
        index["movies"][key] = [_FakePlexItem("movie", f"Movie {n}", 2026, "Movies", section_id=1, server_id="plex-a", rating_key=f"sbs-{n}")]
    return assets, index


def _run(service, assets, index, *, at_once=True, dry_run=False, progress=None):
    stats = service._build_run_stats(assets, [])
    service._process_assets_for_upload(
        local_assets=assets, index=index, stats=stats, dry_run=dry_run, arr_availability={},
        remove_overlay_label=False, media_type_filter="movie", progress_callback=progress, at_once=at_once,
    )
    return stats


def _recorded(db):
    return {row.file_path for row in db.query(PlexUploadRecord).all()}


def test_a_full_run_sends_uploads_side_by_side_and_records_each(test_db, tmp_path, monkeypatch):
    service = PlexUploadService(test_db, upload_delay_ms=0)
    service.UPLOAD_WORKERS = 2
    assets, index = _movies(tmp_path, 4)
    together = threading.Barrier(2, timeout=10)  # passes only while two uploads are under way at once
    sent = []

    def upload(item, kind, filepath):
        together.wait()
        sent.append(item.item_id)
        return True

    monkeypatch.setattr(_WRAP_CLIENT, "upload_image", upload)
    writing = []
    mark = service._mark_uploaded

    def one_at_a_time(*args, **kwargs):
        assert not writing, "two uploads were recorded at the same moment"
        writing.append(1)
        time.sleep(0.01)
        try:
            return mark(*args, **kwargs)
        finally:
            writing.pop()

    monkeypatch.setattr(service, "_mark_uploaded", one_at_a_time)
    steps = []
    stats = _run(service, assets, index, progress=lambda done, total, counts, message: steps.append((done, total, counts["uploaded"])))

    assert sorted(sent) == ["sbs-0", "sbs-1", "sbs-2", "sbs-3"]
    assert (stats["uploaded"], stats["matched"], stats["errors"]) == (4, 4, 0)
    assert _recorded(test_db) == {a["path"] for a in assets}
    assert steps == [(1, 4, 1), (2, 4, 2), (3, 4, 3), (4, 4, 4)]  # the bar counts finished assets


def test_dry_runs_and_single_uploads_stay_one_at_a_time(test_db, tmp_path, monkeypatch):
    service = PlexUploadService(test_db, upload_delay_ms=0)
    assets, index = _movies(tmp_path, 3)
    threads = set()
    real = service._upload_asset

    def watched(*args, **kwargs):
        threads.add(threading.current_thread().name)
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "_upload_asset", watched)
    monkeypatch.setattr(_WRAP_CLIENT, "upload_image", lambda item, kind, filepath: True)
    assert _run(service, assets, index, dry_run=True)["would_upload"] == 3
    assert _run(service, assets, index, at_once=False)["uploaded"] == 3
    assert threads == {threading.current_thread().name}


def test_a_failed_upload_counts_as_an_error_and_the_rest_go_on(test_db, tmp_path, monkeypatch):
    service = PlexUploadService(test_db, upload_delay_ms=0)
    assets, index = _movies(tmp_path, 5)

    def upload(item, kind, filepath):
        if item.item_id == "sbs-2":
            raise ConnectionError("server went away")
        return True

    monkeypatch.setattr(_WRAP_CLIENT, "upload_image", upload)
    stats = _run(service, assets, index)
    assert (stats["uploaded"], stats["errors"]) == (4, 1)
    assert _recorded(test_db) == {a["path"] for a in assets if "Movie 2" not in a["path"]}


def test_a_cancelled_run_starts_nothing_new_and_records_what_was_under_way(test_db, tmp_path, monkeypatch):
    service = PlexUploadService(test_db, upload_delay_ms=0)
    service.UPLOAD_WORKERS = 2
    assets, index = _movies(tmp_path, 8)
    sent = []

    def upload(item, kind, filepath):
        sent.append(item.item_id)
        time.sleep(0.05)
        return True

    monkeypatch.setattr(_WRAP_CLIENT, "upload_image", upload)

    def cancel(done, total, counts, message):
        raise JobCancelled("stopped by the user")

    with pytest.raises(JobCancelled):
        _run(service, assets, index, progress=cancel)
    assert 1 <= len(sent) < 8
    by_key = {a["path"]: index["movies"][a["media_key"]][0].item_id for a in assets}
    assert sorted(by_key[path] for path in _recorded(test_db)) == sorted(sent)  # every upload sent is on record


def _plex_item(item_id, guid, *, season=None):
    client = SimpleNamespace(server_type="plex", instance_name="Plex")
    if season is not None:
        return MediaServerItem(item_id=item_id, item_type="season", title=f"Season {season}", index=season, parent_guid=guid, client=client)
    return MediaServerItem(item_id=item_id, item_type="movie", title="Heat", guid=guid, client=client)


def _overlap(service, items):
    """The most of these items being uploaded to at one moment."""
    guard = threading.Lock()
    state = {"now": 0, "peak": 0}

    def handle(item):
        claim = service._claim(item)
        try:
            with service._off_turn():
                with guard:
                    state["now"] += 1
                    state["peak"] = max(state["peak"], state["now"])
                time.sleep(0.05)
                with guard:
                    state["now"] -= 1
        finally:
            service._release(claim)

    service._each_asset(items, handle, at_once=True)
    return state["peak"]


def test_two_copies_sharing_a_plex_bundle_are_never_uploaded_to_at_once(test_db):
    """Movies and 4K Movies hold one bundle folder for a title: tidying it after one copy's
    upload must not meet the other copy's upload on its way."""
    service = PlexUploadService(test_db)
    service.UPLOAD_WORKERS = 2
    assert _overlap(service, [_plex_item("1", "plex://movie/heat"), _plex_item("2", "plex://movie/heat")]) == 1
    assert _overlap(service, [_plex_item("1", "plex://movie/heat"), _plex_item("3", "plex://movie/ronin")]) == 2
    assert service._in_flight == set()
    # Seasons are items of their own: a show's seasons go side by side, a season's two copies do not.
    assert _overlap(service, [_plex_item("11", "plex://show/wire", season=1), _plex_item("12", "plex://show/wire", season=2)]) == 2
    assert _overlap(service, [_plex_item("11", "plex://show/wire", season=1), _plex_item("21", "plex://show/wire", season=1)]) == 1
    # Outside a full run there is nothing to wait for.
    assert service._claim(_plex_item("1", "plex://movie/heat")) is None


def test_a_titles_images_reach_the_server_one_at_a_time(test_db, tmp_path, monkeypatch):
    """Plex keeps only one selection when two kinds of image reach one title at the same
    moment: a title's background, logo and square art go up in turn, while titles overlap."""
    service = PlexUploadService(test_db, upload_delay_ms=0)
    assets, index = [], {"movies": {}, "shows": {}, "collections": {}}
    for n in range(4):
        folder = tmp_path / f"Film {n} (2026)"
        folder.mkdir()
        key = f"onetitle{n}"
        index["movies"][key] = [_FakePlexItem("movie", f"Film {n}", 2026, "Movies", section_id=1, server_id="plex-a", rating_key=f"art-{n}")]
        for name, kind in (("background.jpg", "background"), ("logo.png", "logo"), ("square.jpg", "squareart")):
            (folder / name).write_bytes(f"{n} {kind}".encode())
            assets.append({"media_key": key, "path": str(folder / name), "display_name": f"Film {n}", "asset_type": "main",
                           "season_number": None, "folder_year": 2026, "folder_name": folder.name, "artwork_type": kind})
    guard = threading.Lock()
    under_way = {}
    peak = {"title": 0, "all": 0}
    order = {}

    def upload(item, kind, filepath):
        with guard:
            under_way[item.item_id] = under_way.get(item.item_id, 0) + 1
            peak["title"] = max(peak["title"], under_way[item.item_id])
            peak["all"] = max(peak["all"], sum(under_way.values()))
            order.setdefault(item.item_id, []).append(kind)
        time.sleep(0.02)
        with guard:
            under_way[item.item_id] -= 1
        return True

    monkeypatch.setattr(_WRAP_CLIENT, "upload_image", upload)
    stats = service._build_run_stats([], [])
    stats["artwork"]["scanned"] = len(assets)
    service._process_artwork_for_upload(artwork_assets=assets, index=index, stats=stats, dry_run=False, arr_availability={}, at_once=True)
    assert (stats["artwork"]["uploaded"], stats["artwork"]["errors"]) == (12, 0)
    assert peak["title"] == 1 and peak["all"] >= 2
    assert all(kinds == ["background", "logo", "squareart"] for kinds in order.values()) and len(order) == 4


def test_the_pause_between_uploads_is_shared_side_by_side(test_db, monkeypatch):
    slept = []
    monkeypatch.setattr(plex_upload, "time", SimpleNamespace(sleep=slept.append, monotonic=lambda: 100.0))
    service = PlexUploadService(test_db)
    service._pause(0.05)
    service._pause(0)
    assert slept == [0.05]  # on its own: the plain pause

    slept.clear()

    def handle(_asset):
        service._pause(0.05)

    service.UPLOAD_WORKERS = 2
    service._each_asset([1, 2, 3], handle, at_once=True)
    assert [round(s, 2) for s in sorted(slept)] == [0.05, 0.1, 0.15]  # each goes on 50 ms after the one before
