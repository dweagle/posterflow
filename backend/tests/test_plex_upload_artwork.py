"""Artwork Plex upload: isolation from posters, discovery, and subtype upload + dedupe."""

from pathlib import Path

from models.setting import Setting
from services.plex_upload import PlexUploadService


class _FakeArtItem:
    class _FakeServer:
        def __init__(self, mid):
            self.machineIdentifier = mid

    def __init__(self, item_type="movie", title="Inception", year=2010, rating_key="123"):
        self.type = item_type
        self.title = title
        self.year = year
        self.librarySectionTitle = "Movies"
        self.librarySectionID = 1
        self._server = self._FakeServer("srv1")
        self.ratingKey = rating_key
        self.logo_calls: list[str] = []
        self.art_calls: list[str] = []
        self.square_calls: list[str] = []

    def uploadPoster(self, filepath):
        pass

    def uploadLogo(self, filepath):
        self.logo_calls.append(filepath)

    def uploadArt(self, filepath):
        self.art_calls.append(filepath)

    def uploadSquareArt(self, filepath):
        self.square_calls.append(filepath)


def _svc(db):
    return PlexUploadService(db, upload_delay_ms=0)


def _item_folder(tmp_path, *, poster=False, logo=False, background=False, squareart=False):
    d = tmp_path / "Inception (2010) {tmdb-27205}"
    d.mkdir()
    if poster:
        (d / "poster.jpg").write_bytes(b"p")
    if logo:
        (d / "logo.png").write_bytes(b"l")
    if background:
        (d / "background.jpg").write_bytes(b"b")
    if squareart:
        (d / "square.jpg").write_bytes(b"s")
    return d


# ── isolation: poster discovery must ignore artwork ──────────────────────────

def test_discover_local_assets_skips_artwork(test_db, tmp_path):
    _item_folder(tmp_path, poster=True, logo=True, background=True, squareart=True)
    assets = _svc(test_db)._discover_local_assets(tmp_path)
    assert [Path(a["path"]).name for a in assets] == ["poster.jpg"]


def test_discover_local_assets_flat_skips_artwork(test_db, tmp_path):
    (tmp_path / "Inception (2010) {tmdb-27205}.jpg").write_bytes(b"p")
    (tmp_path / "Inception (2010) {tmdb-27205}-logo.png").write_bytes(b"l")
    assets = _svc(test_db)._discover_local_assets(tmp_path)
    assert [Path(a["path"]).name for a in assets] == ["Inception (2010) {tmdb-27205}.jpg"]


# ── artwork discovery ────────────────────────────────────────────────────────

def test_discover_local_artwork_nested(test_db, tmp_path):
    _item_folder(tmp_path, poster=True, logo=True, background=True)
    arts = _svc(test_db)._discover_local_artwork(tmp_path)
    by = {a["artwork_type"]: a for a in arts}
    assert set(by) == {"logo", "background"}
    assert by["logo"]["folder_year"] == 2010
    assert by["logo"]["media_key"]  # normalized, non-empty


def test_discover_local_artwork_flat(test_db, tmp_path):
    (tmp_path / "Inception (2010) {tmdb-27205}.jpg").write_bytes(b"p")
    (tmp_path / "Inception (2010) {tmdb-27205}-logo.png").write_bytes(b"l")
    (tmp_path / "Inception (2010) {tmdb-27205}-square.jpg").write_bytes(b"s")
    arts = _svc(test_db)._discover_local_artwork(tmp_path)
    by = {a["artwork_type"]: a for a in arts}
    assert set(by) == {"logo", "squareart"}
    assert by["logo"]["folder_year"] == 2010


# ── toggle ───────────────────────────────────────────────────────────────────

def test_artwork_upload_toggle(test_db):
    svc = _svc(test_db)
    assert svc._is_artwork_upload_enabled() is False
    test_db.add(Setting(key="plex_upload_artwork", value="true"))
    test_db.commit()
    assert svc._is_artwork_upload_enabled() is True


# ── upload + dedupe ──────────────────────────────────────────────────────────

def _prep_matching(svc, idx, item, monkeypatch):
    def fake_resolve(index_map, media_key, id_keys, folder_year):
        return [item] if index_map is idx["movies"] else []
    monkeypatch.setattr(svc, "_resolve_index_candidates", fake_resolve)
    monkeypatch.setattr(svc, "_dedupe_plex_items", lambda items: list(items))


def test_upload_artwork_logo_and_dedupes(test_db, tmp_path, monkeypatch):
    svc = _svc(test_db)
    item = _FakeArtItem()
    idx = {"movies": {}, "shows": {}, "collections": {}}
    _prep_matching(svc, idx, item, monkeypatch)

    d = _item_folder(tmp_path, logo=True)
    asset = {
        "path": str(d / "logo.png"), "media_key": "inception", "display_name": "Inception",
        "asset_type": "main", "season_number": None, "folder_year": 2010, "artwork_type": "logo",
    }
    uploaded, matched = svc._upload_artwork_asset(asset, idx, dry_run=False)
    assert matched and uploaded == 1
    assert item.logo_calls == [str(d / "logo.png")]
    assert item.art_calls == [] and item.square_calls == []

    # Second run (cache cleared → reads persisted record) must skip.
    svc.invalidate_record_cache()
    uploaded2, _ = svc._upload_artwork_asset(asset, idx, dry_run=False)
    assert uploaded2 == 0
    assert item.logo_calls == [str(d / "logo.png")]  # not re-uploaded


def test_upload_artwork_background_uses_uploadart(test_db, tmp_path, monkeypatch):
    svc = _svc(test_db)
    item = _FakeArtItem()
    idx = {"movies": {}, "shows": {}, "collections": {}}
    _prep_matching(svc, idx, item, monkeypatch)

    d = _item_folder(tmp_path, background=True)
    asset = {
        "path": str(d / "background.jpg"), "media_key": "inception", "display_name": "Inception",
        "asset_type": "main", "season_number": None, "folder_year": 2010, "artwork_type": "background",
    }
    uploaded, matched = svc._upload_artwork_asset(asset, idx, dry_run=False)
    assert uploaded == 1 and matched
    assert item.art_calls == [str(d / "background.jpg")]
    assert item.logo_calls == [] and item.square_calls == []


def _stub_single_upload(svc, tmp_path, monkeypatch, *, artwork):
    monkeypatch.setattr(svc, "_prepare_upload_context", lambda: (None, tmp_path, {"movies": {}, "shows": {}, "collections": {}}, [{"instance": "x"}]))
    monkeypatch.setattr(svc, "_get_local_assets", lambda dest, **k: [{"asset_type": "main", "path": "p.jpg", "media_key": "inception", "folder_year": 2010, "season_number": None}])
    monkeypatch.setattr(svc, "_get_local_artwork", lambda dest, **k: list(artwork))
    monkeypatch.setattr(svc, "_select_local_assets_for_target", lambda assets, **k: assets)
    monkeypatch.setattr(svc, "_get_arr_availability_index", lambda **k: {})
    monkeypatch.setattr(svc, "_process_assets_for_upload", lambda **k: None)
    monkeypatch.setattr(svc, "_persist_upload_cache", lambda: None)


def test_run_single_upload_processes_artwork_when_enabled(test_db, tmp_path, monkeypatch):
    svc = _svc(test_db)
    art = {"path": "logo.png", "media_key": "inception", "artwork_type": "logo", "asset_type": "main", "folder_year": 2010, "season_number": None, "display_name": "Inception"}
    _stub_single_upload(svc, tmp_path, monkeypatch, artwork=[art])
    calls = []
    monkeypatch.setattr(svc, "_process_artwork_for_upload", lambda **k: calls.append(k))

    result = svc.run_single_upload(media_type="movie", title="Inception", year=2010, include_artwork=True)
    assert result["success"]
    assert len(calls) == 1
    assert calls[0]["artwork_assets"] == [art]


def test_run_single_upload_skips_artwork_when_disabled(test_db, tmp_path, monkeypatch):
    svc = _svc(test_db)
    got_artwork = []
    _stub_single_upload(svc, tmp_path, monkeypatch, artwork=[])
    monkeypatch.setattr(svc, "_get_local_artwork", lambda dest, **k: got_artwork.append(True) or [])
    calls = []
    monkeypatch.setattr(svc, "_process_artwork_for_upload", lambda **k: calls.append(k))

    result = svc.run_single_upload(media_type="movie", title="Inception", year=2010, include_artwork=False)
    assert result["success"]
    assert calls == []
    assert got_artwork == []  # artwork discovery skipped entirely when disabled


def _stub_cache_gate(svc, tmp_path, monkeypatch):
    poster = {"asset_type": "main", "path": "p.jpg", "media_key": "inception", "folder_year": 2010, "season_number": None}
    art = {"asset_type": "main", "path": "logo.png", "media_key": "inception", "artwork_type": "logo", "folder_year": 2010, "season_number": None, "display_name": "Inception"}
    monkeypatch.setattr(svc, "_prepare_upload_context", lambda: (None, tmp_path, {"movies": {}, "shows": {}, "collections": {}}, [{"instance": "x"}]))
    monkeypatch.setattr(svc, "_get_local_assets", lambda dest, **k: [poster])
    monkeypatch.setattr(svc, "_get_local_artwork", lambda dest, **k: [art])
    monkeypatch.setattr(svc, "_select_local_assets_for_target", lambda assets, **k: assets)
    monkeypatch.setattr(svc, "_get_arr_availability_index", lambda **k: {})


def test_cache_gate_not_cached_when_artwork_pending(test_db, tmp_path, monkeypatch):
    svc = _svc(test_db)
    _stub_cache_gate(svc, tmp_path, monkeypatch)
    # Poster cached, artwork not.
    monkeypatch.setattr(svc, "_is_asset_fully_cached_for_targets", lambda asset, **k: "artwork_type" not in asset)

    assert svc.is_single_target_fully_cached(media_type="movie", title="Inception", year=2010, include_artwork=False) is True
    assert svc.is_single_target_fully_cached(media_type="movie", title="Inception", year=2010, include_artwork=True) is False


def test_cache_gate_cached_when_artwork_present(test_db, tmp_path, monkeypatch):
    svc = _svc(test_db)
    _stub_cache_gate(svc, tmp_path, monkeypatch)
    # Everything cached.
    monkeypatch.setattr(svc, "_is_asset_fully_cached_for_targets", lambda asset, **k: True)

    assert svc.is_single_target_fully_cached(media_type="movie", title="Inception", year=2010, include_artwork=True) is True


def test_upload_artwork_dry_run_does_not_call_plex(test_db, tmp_path, monkeypatch):
    svc = _svc(test_db)
    item = _FakeArtItem()
    idx = {"movies": {}, "shows": {}, "collections": {}}
    _prep_matching(svc, idx, item, monkeypatch)

    d = _item_folder(tmp_path, squareart=True)
    asset = {
        "path": str(d / "square.jpg"), "media_key": "inception", "display_name": "Inception",
        "asset_type": "main", "season_number": None, "folder_year": 2010, "artwork_type": "squareart",
    }
    uploaded, matched = svc._upload_artwork_asset(asset, idx, dry_run=True)
    assert uploaded == 1 and matched
    assert item.square_calls == []  # dry run: no actual upload


# ---------------------------------------------------------------------------
# _upload_ready: downscale oversized images so Plex doesn't 500 on them
# ---------------------------------------------------------------------------

import os
from PIL import Image


def test_upload_ready_downscales_oversized_logo(test_db, tmp_path):
    """A 16MP logo (the kind that 500s Plex) is downscaled under the cap, keeping alpha."""
    big = tmp_path / "logo.png"
    Image.new("RGBA", (8101, 2062), (10, 20, 30, 128)).save(big)

    svc = _svc(test_db)
    with svc._upload_ready(str(big)) as up:
        assert up != str(big)                       # a temp copy, not the original
        with Image.open(up) as im:
            assert max(im.size) <= svc.MAX_UPLOAD_DIMENSION
            assert im.mode == "RGBA"                # transparency preserved for logos
        tmp = up
    assert not os.path.exists(tmp)                  # temp cleaned up on exit


def test_upload_ready_passes_normal_image_through(test_db, tmp_path):
    ok = tmp_path / "logo.png"
    Image.new("RGBA", (1500, 500), (0, 0, 0, 0)).save(ok)

    svc = _svc(test_db)
    with svc._upload_ready(str(ok)) as up:
        assert up == str(ok)                        # untouched


def test_upload_ready_preserves_jpeg_for_backgrounds(test_db, tmp_path):
    big = tmp_path / "background.jpg"
    Image.new("RGB", (9000, 5000), (40, 40, 40)).save(big)

    svc = _svc(test_db)
    with svc._upload_ready(str(big)) as up:
        assert up != str(big)
        with Image.open(up) as im:
            assert im.format == "JPEG"
            assert max(im.size) <= svc.MAX_UPLOAD_DIMENSION


def test_upload_ready_yields_original_for_unreadable_file(test_db, tmp_path):
    """A non-image / unreadable path isn't second-guessed — Plex gets the original."""
    bad = tmp_path / "logo.png"
    bad.write_bytes(b"not an image")

    svc = _svc(test_db)
    with svc._upload_ready(str(bad)) as up:
        assert up == str(bad)


def test_upload_ready_downscales_over_byte_cap_under_dimension_cap(test_db, tmp_path):
    """A dense PNG under the 4000px dimension cap but over the ~10MB byte cap (Kometa's
    threshold) is still downscaled — we shrink to fit bytes rather than skip like Kometa."""
    dense = tmp_path / "background.png"
    # Random RGB defeats PNG compression, so 2600x2600 lands well over 10MB while under 4000px.
    Image.frombytes("RGB", (2600, 2600), os.urandom(2600 * 2600 * 3)).save(dense)
    assert os.path.getsize(dense) > 10_000_000 and max(Image.open(dense).size) < 4000

    svc = _svc(test_db)
    with svc._upload_ready(str(dense)) as up:
        assert up != str(dense)
        assert os.path.getsize(up) <= svc.MAX_UPLOAD_BYTES   # brought under the byte cap


# ---------------------------------------------------------------------------
# Reporting: artwork must be surfaced wherever posters are (job message, totals
# log, Discord embed, and the single-item path's summary line).
# ---------------------------------------------------------------------------

import pytest


@pytest.fixture
def logged(monkeypatch):
    """Capture what the service logs."""
    lines = []
    monkeypatch.setattr(
        "services.plex_upload.log_info",
        lambda tag, message, **kw: lines.append(message),
    )
    return lines


def _stats(**artwork):
    art = {
        "scanned": 0, "matched": 0, "uploaded": 0, "would_upload": 0, "skipped": 0, "errors": 0,
        "by_type": {"logo": 0, "background": 0, "squareart": 0},
    }
    art.update(artwork)
    return {"artwork": art}


def test_summary_names_each_artwork_type(test_db, logged):
    stats = _stats(
        scanned=6, matched=6, uploaded=6, skipped=0, errors=0,
        by_type={"logo": 3, "background": 2, "squareart": 1},
    )

    PlexUploadService(test_db)._log_artwork_summary(stats, dry_run=False)

    assert len(logged) == 1
    line = logged[0]
    assert "Artwork upload: 6 uploaded" in line
    assert "3 logos" in line and "2 backgrounds" in line and "1 squareart" in line
    assert "6 matched" in line


def test_summary_reports_would_upload_on_a_dry_run(test_db, logged):
    stats = _stats(scanned=2, matched=2, would_upload=2, by_type={"logo": 2, "background": 0, "squareart": 0})

    PlexUploadService(test_db)._log_artwork_summary(stats, dry_run=True)

    assert "Artwork upload: 2 would upload" in logged[0]


def test_summary_still_logs_when_nothing_matched(test_db, logged):
    """The point of the line: a run that placed no artwork must say so, not stay silent."""
    stats = _stats(scanned=4, matched=0, uploaded=0, skipped=4)

    PlexUploadService(test_db)._log_artwork_summary(stats, dry_run=False)

    assert "Artwork upload: 0 uploaded" in logged[0]
    assert "4 skipped" in logged[0]


def test_summary_surfaces_errors(test_db, logged):
    stats = _stats(scanned=3, matched=3, uploaded=2, errors=1, by_type={"logo": 2, "background": 0, "squareart": 0})

    PlexUploadService(test_db)._log_artwork_summary(stats, dry_run=False)

    assert "1 errors" in logged[0]


def test_both_upload_paths_log_the_summary():
    """run_full_upload and run_single_upload must both call it — the single path (webhook
    and Single Poster) previously processed artwork without reporting any of it."""
    import inspect

    full = inspect.getsource(PlexUploadService.run_full_upload)
    single = inspect.getsource(PlexUploadService.run_single_upload)

    assert "_log_artwork_summary" in full
    assert "_log_artwork_summary" in single


# ---------------------------------------------------------------------------
# Job-level reporting
# ---------------------------------------------------------------------------


def _run_upload_job(test_db, monkeypatch, stats):
    """Run the real job with a stubbed service, capturing what it reports."""
    import modules.upload as upload_module
    from models.job import Job

    job = Job(job_type="Plex Upload", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)

    class _FakeService:
        def __init__(self, *a, **k):
            pass

        def run_full_upload(self, *a, **k):
            return {"success": True, "stats": stats, "message": "done"}

    logs = []
    monkeypatch.setattr(upload_module, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None, raising=False)
    monkeypatch.setattr(upload_module, "PlexUploadService", _FakeService)
    monkeypatch.setattr(upload_module, "log_info", lambda tag, message, **kw: logs.append(message))
    monkeypatch.setattr(upload_module, "log_success", lambda tag, message, **kw: logs.append(f"{message} {kw}"))
    monkeypatch.setattr(upload_module, "send_discord_notification", lambda *a, **k: logs.append(str(k.get("description", ""))))

    upload_module.run_plex_upload_background_job(job.id, dry_run=False, skip_discord=False)
    return test_db.query(Job).filter(Job.id == job.id).first(), logs


def _full_stats(**artwork):
    art = {
        "scanned": 0, "matched": 0, "uploaded": 0, "would_upload": 0, "skipped": 0, "errors": 0,
        "by_type": {"logo": 0, "background": 0, "squareart": 0},
    }
    art.update(artwork)
    return {
        "scanned": 10, "matched": 8, "uploaded": 8, "skipped": 2, "errors": 0,
        "movies": 5, "shows": 3, "seasons": 0, "collections": 0,
        "candidates_raw": 10, "candidates_unique": 10,
        "assets_main": 8, "assets_season": 0,
        "artwork": art,
    }


def test_job_message_counts_artwork_alongside_posters(test_db, monkeypatch):
    stats = _full_stats(scanned=6, matched=6, uploaded=6, by_type={"logo": 3, "background": 2, "squareart": 1})

    job, _ = _run_upload_job(test_db, monkeypatch, stats)

    assert "8 poster(s) uploaded" in job.message
    assert "6 artwork" in job.message


def test_job_message_omits_artwork_when_it_was_not_enabled(test_db, monkeypatch):
    """Artwork off means nothing scanned — don't clutter the message with a zero."""
    job, _ = _run_upload_job(test_db, monkeypatch, _full_stats())

    assert "8 poster(s) uploaded" in job.message
    assert "artwork" not in job.message.lower()


def test_job_logs_artwork_totals_by_type(test_db, monkeypatch):
    stats = _full_stats(scanned=6, matched=6, uploaded=6, by_type={"logo": 3, "background": 2, "squareart": 1})

    _, logs = _run_upload_job(test_db, monkeypatch, stats)

    totals = [l for l in logs if "Artwork totals (final)" in l]
    assert len(totals) == 1
    assert "logos=3" in totals[0] and "backgrounds=2" in totals[0] and "squareart=1" in totals[0]


def test_discord_summary_mentions_artwork(test_db, monkeypatch):
    stats = _full_stats(scanned=6, matched=6, uploaded=6, by_type={"logo": 6, "background": 0, "squareart": 0})

    _, logs = _run_upload_job(test_db, monkeypatch, stats)

    assert any("6 artwork file(s)" in l for l in logs)
