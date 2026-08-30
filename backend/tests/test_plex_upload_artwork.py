"""Artwork Plex upload: isolation from posters, discovery, and subtype upload + dedupe."""

from pathlib import Path

from models.setting import Setting
from services.plex_upload import PlexUploadService
from plex_upload_fakes import wrap_item


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

def _prep_matching(svc, idx, wrapped, monkeypatch):
    def fake_resolve(index_map, media_key, id_keys, folder_year):
        return [wrapped] if index_map is idx["movies"] else []
    monkeypatch.setattr(svc, "_resolve_index_candidates", fake_resolve)
    monkeypatch.setattr(svc, "_dedupe_plex_items", lambda items: list(items))


def test_upload_artwork_logo_and_dedupes(test_db, tmp_path, monkeypatch):
    svc = _svc(test_db)
    item = _FakeArtItem()
    wrapped = wrap_item(item)
    idx = {"movies": {}, "shows": {}, "collections": {}}
    _prep_matching(svc, idx, wrapped, monkeypatch)

    d = _item_folder(tmp_path, logo=True)
    asset = {
        "path": str(d / "logo.png"), "media_key": "inception", "display_name": "Inception",
        "asset_type": "main", "season_number": None, "folder_year": 2010, "artwork_type": "logo",
    }
    outcome = svc._upload_artwork_asset(asset, idx, dry_run=False)
    assert outcome.matched and outcome.uploaded == 1
    assert item.logo_calls == [str(d / "logo.png")]
    assert item.art_calls == [] and item.square_calls == []

    # Second run (cache cleared → reads persisted record) must skip.
    svc.invalidate_record_cache()
    outcome2 = svc._upload_artwork_asset(asset, idx, dry_run=False)
    assert outcome2.uploaded == 0
    assert item.logo_calls == [str(d / "logo.png")]  # not re-uploaded


def test_upload_artwork_background_uses_uploadart(test_db, tmp_path, monkeypatch):
    svc = _svc(test_db)
    item = _FakeArtItem()
    wrapped = wrap_item(item)
    idx = {"movies": {}, "shows": {}, "collections": {}}
    _prep_matching(svc, idx, wrapped, monkeypatch)

    d = _item_folder(tmp_path, background=True)
    asset = {
        "path": str(d / "background.jpg"), "media_key": "inception", "display_name": "Inception",
        "asset_type": "main", "season_number": None, "folder_year": 2010, "artwork_type": "background",
    }
    outcome = svc._upload_artwork_asset(asset, idx, dry_run=False)
    assert outcome.uploaded == 1 and outcome.matched
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
    assert "arr_availability" in calls[0]   # else undownloaded items read as match failures


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
    wrapped = wrap_item(item)
    idx = {"movies": {}, "shows": {}, "collections": {}}
    _prep_matching(svc, idx, wrapped, monkeypatch)

    d = _item_folder(tmp_path, squareart=True)
    asset = {
        "path": str(d / "square.jpg"), "media_key": "inception", "display_name": "Inception",
        "asset_type": "main", "season_number": None, "folder_year": 2010, "artwork_type": "squareart",
    }
    outcome = svc._upload_artwork_asset(asset, idx, dry_run=True)
    assert outcome.uploaded == 1 and outcome.matched
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
        "uploaded_files": 0, "already_current": 0,
        "by_type": {"logo": 0, "background": 0, "squareart": 0},
    }
    art.update(artwork)
    if "uploaded_files" not in artwork:
        art["uploaded_files"] = art["would_upload"] or art["uploaded"]
    return {"artwork": art}


def test_log_artwork_summary_emits_the_shared_block(test_db, logged):
    """The method is a thin wrapper over the shared formatter; this checks it delegates
    and carries the per-type split. Bucket wording itself is covered where it is built."""
    PlexUploadService(test_db)._log_artwork_summary(
        _stats(scanned=6, uploaded=6, by_type={"logo": 3, "background": 2, "squareart": 1}),
        dry_run=False,
    )
    block = "\n".join(logged)
    assert "Outcome per artwork file (final): 6 scanned" in block
    assert "- 6 uploaded — pushed to your media servers this run" in block
    assert "3 logos, 2 backgrounds, 1 squareart" in block

    logged.clear()
    PlexUploadService(test_db)._log_artwork_summary(
        _stats(scanned=4, matched=0, uploaded=0), dry_run=True,
    )
    quiet = "\n".join(logged)
    assert "- 0 would upload" in quiet          # dry-run verb reaches the wrapper
    assert "- 4 unmatched" in quiet             # a silent run still reports
    assert "logos" not in quiet                 # no per-type row when nothing uploaded


def test_single_upload_path_logs_the_summary_itself():
    """Single runs have no job summary so must log artwork themselves; full runs
    deliberately don't, so the job layer can print it beside the poster block."""
    import inspect

    full = inspect.getsource(PlexUploadService.run_full_upload)
    single = inspect.getsource(PlexUploadService.run_single_upload)

    assert "_log_artwork_summary" in single
    assert "_log_artwork_summary" not in full


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
        "uploaded_files": 0, "already_current": 0,
        "by_type": {"logo": 0, "background": 0, "squareart": 0},
    }
    art.update(artwork)
    if "uploaded_files" not in artwork:
        art["uploaded_files"] = art["uploaded"]
    return {
        "scanned": 10, "matched": 8, "uploaded": 8, "skipped": 2, "errors": 0,
        "uploaded_files": 8, "already_current": 0, "awaiting_plex": 0,
        "movies": 5, "shows": 3, "seasons": 0, "collections": 0,
        "assets_main": 8, "assets_season": 0,
        "artwork": art,
    }


def test_job_message_counts_artwork_alongside_posters(test_db, monkeypatch):
    stats = _full_stats(scanned=6, matched=6, uploaded=6, by_type={"logo": 3, "background": 2, "squareart": 1})

    job, _ = _run_upload_job(test_db, monkeypatch, stats)

    assert "poster 10 file(s): 8 uploaded" in job.message
    assert "artwork 6 file(s): 6 uploaded" in job.message


def test_job_message_omits_artwork_when_it_was_not_enabled(test_db, monkeypatch):
    """Artwork off means nothing scanned — don't clutter the message with a zero."""
    job, _ = _run_upload_job(test_db, monkeypatch, _full_stats())

    assert "poster 10 file(s): 8 uploaded" in job.message
    assert "artwork" not in job.message.lower()


def test_job_logs_artwork_totals_by_type(test_db, monkeypatch):
    stats = _full_stats(scanned=6, matched=6, uploaded=6, by_type={"logo": 3, "background": 2, "squareart": 1})

    _, logs = _run_upload_job(test_db, monkeypatch, stats)

    outcome = [l for l in logs if "Outcome per artwork file (final)" in l]
    assert len(outcome) == 1
    assert "6 scanned" in outcome[0]

    totals = [l for l in logs if "3 logos" in l]
    assert len(totals) == 1
    assert "2 backgrounds" in totals[0] and "1 squareart" in totals[0]


def test_discord_summary_mentions_artwork(test_db, monkeypatch):
    stats = _full_stats(scanned=6, matched=6, uploaded=6, by_type={"logo": 6, "background": 0, "squareart": 0})

    _, logs = _run_upload_job(test_db, monkeypatch, stats)

    assert any("6 artwork file(s)" in l for l in logs)


def test_artwork_type_split_sits_under_the_uploaded_line():
    """The per-type detail is a sub-bullet of 'uploaded' — appending it instead put it
    under whichever bucket happened to be last (e.g. 'unmatched'), which read as a lie."""
    from services.plex_upload import artwork_summary_lines

    lines = artwork_summary_lines(
        {"scanned": 10, "uploaded_files": 6, "already_current": 2, "errors": 0,
         "by_type": {"logo": 3, "background": 2, "squareart": 1},
         "unmatched_reasons": {"no_plex_match": 2}},
        dry_run=False,
    )

    assert "uploaded" in lines[1]
    assert lines[2] == "  - 3 logos, 2 backgrounds, 1 squareart"
    assert "unmatched" in lines[-2] and "no server match" in lines[-1]


def _art_asset(tmp_path, media_key="inception", year=2010):
    d = _item_folder(tmp_path, logo=True)
    return {
        "path": str(d / "logo.png"), "media_key": media_key, "display_name": "Inception",
        "asset_type": "main", "season_number": None, "folder_year": year, "artwork_type": "logo",
    }


def test_artwork_for_undownloaded_movie_reports_not_downloaded(test_db, tmp_path):
    svc = _svc(test_db)
    idx = {"movies": {}, "shows": {}, "collections": {}}
    arr = {"movies": {"inception": {"has_file": False}}}

    outcome = svc._upload_artwork_asset(
        _art_asset(tmp_path), idx, dry_run=True, arr_availability=arr,
    )

    assert outcome.matched is False
    assert outcome.skip_reason == "not_downloaded"


def test_artwork_for_downloaded_movie_missing_from_plex_still_reports_no_match(test_db, tmp_path):
    """*arr has the file, so a Plex miss really is a match problem — keep saying so."""
    svc = _svc(test_db)
    idx = {"movies": {}, "shows": {}, "collections": {}}
    arr = {"movies": {"inception": {"has_file": True}}}

    outcome = svc._upload_artwork_asset(
        _art_asset(tmp_path), idx, dry_run=True, arr_availability=arr,
    )

    assert outcome.matched is False
    assert outcome.skip_reason == "no_plex_match"


def test_artwork_for_movie_absent_from_arr_reports_no_match(test_db, tmp_path):
    """Artwork-only folders for titles *arr doesn't track get no availability opinion —
    don't invent one (this is the 'artwork but no poster' case)."""
    svc = _svc(test_db)
    idx = {"movies": {}, "shows": {}, "collections": {}}

    outcome = svc._upload_artwork_asset(
        _art_asset(tmp_path), idx, dry_run=True, arr_availability={"movies": {}},
    )

    assert outcome.matched is False
    assert outcome.skip_reason == "no_plex_match"


def test_artwork_for_collection_folder_makes_no_arr_claim(test_db, tmp_path):
    """A collection carries neither year nor ids. One sharing a title with an undownloaded
    movie must not inherit that movie's 'not downloaded' label — *arr never said it."""
    svc = _svc(test_db)
    d = tmp_path / "Inception"          # no year, no ids: collection-shaped
    d.mkdir()
    (d / "logo.png").write_bytes(b"l")
    asset = {
        "path": str(d / "logo.png"), "media_key": "inception", "display_name": "Inception",
        "asset_type": "main", "season_number": None, "folder_year": None, "artwork_type": "logo",
    }
    idx = {"movies": {}, "shows": {}, "collections": {}}
    arr = {"movies": {"inception": {"has_file": False}}}

    outcome = svc._upload_artwork_asset(asset, idx, dry_run=True, arr_availability=arr)

    assert outcome.matched is False
    assert outcome.skip_reason == "no_plex_match"


def test_artwork_with_ids_but_no_year_still_asks_arr(test_db, tmp_path):
    """Year-less folders that carry {tmdb-}/{imdb-}/{tvdb-} ids are real titles, not
    collections — 'The Savant (0)' and friends must get the *arr answer."""
    svc = _svc(test_db)
    d = tmp_path / "The Savant (0) {tvdb-432966}"
    d.mkdir()
    (d / "logo.png").write_bytes(b"l")
    asset = {
        "path": str(d / "logo.png"), "media_key": "thesavant", "display_name": "The Savant",
        "asset_type": "main", "season_number": None, "folder_year": None, "artwork_type": "logo",
    }
    idx = {"movies": {}, "shows": {}, "collections": {}}
    arr = {"shows": {"thesavant": {"has_episodes": False, "seasons": {}}}}

    outcome = svc._upload_artwork_asset(asset, idx, dry_run=True, arr_availability=arr)

    assert outcome.skip_reason == "not_downloaded"


def test_ambiguous_artwork_is_logged_not_silently_dropped(test_db, tmp_path, monkeypatch):
    """type-unresolved artwork returned with no log line at all, so the bucket was a
    dead end — you could see the count but never the files."""
    svc = _svc(test_db)
    messages = []
    monkeypatch.setattr("services.plex_upload.log_info", lambda _t, m, **_k: messages.append(m))
    monkeypatch.setattr(svc, "_resolve_target_media_type", lambda *a, **k: (None, "ARR matched both movie and series"))

    asset = _art_asset(tmp_path)
    outcome = svc._upload_artwork_asset(asset, {"movies": {}, "shows": {}, "collections": {}}, dry_run=True)

    assert outcome.skip_reason == "type_unresolved"
    assert any("Skipping ambiguous no-ID logo" in m and "ARR matched both" in m for m in messages)


def test_artwork_uses_arr_to_disambiguate_movie_vs_show_like_posters_do(test_db, tmp_path, monkeypatch):
    """Movie-vs-show ties need *arr; artwork passed None and was dropped while the
    poster beside it uploaded."""
    svc = _svc(test_db)
    movie = wrap_item(_FakeArtItem(item_type="movie", title="Galaxy Quest", year=1999))
    show = wrap_item(_FakeArtItem(item_type="show", title="Galaxy Quest", year=1999, rating_key="999"))
    idx = {"movies": {"galaxyquest": [movie]}, "shows": {"galaxyquest": [show]}, "collections": {}}
    monkeypatch.setattr(
        svc, "_resolve_index_candidates",
        lambda index_map, *a: [movie] if index_map is idx["movies"] else ([show] if index_map is idx["shows"] else []),
    )

    d = tmp_path / "Galaxy Quest (1999) {tmdb-926}"
    d.mkdir()
    (d / "logo.png").write_bytes(b"l")
    asset = {
        "path": str(d / "logo.png"), "media_key": "galaxyquest", "display_name": "Galaxy Quest",
        "asset_type": "main", "season_number": None, "folder_year": 1999, "artwork_type": "logo",
    }

    # No *arr index: genuinely ambiguous, nothing to upload to.
    assert svc._upload_artwork_asset(asset, idx, dry_run=True).skip_reason == "type_unresolved"

    # With *arr saying "movie", it resolves exactly as the sibling poster does.
    arr = {"movies": {"galaxyquest": {"has_file": True}}, "shows": {}}
    outcome = svc._upload_artwork_asset(asset, idx, dry_run=True, arr_availability=arr)
    assert outcome.matched is True
    assert outcome.uploaded == 1


def test_unavailable_artwork_line_matches_the_poster_wording(test_db, tmp_path, monkeypatch):
    """Same event and same *arr answer as the poster path, so say it the same way."""
    svc = _svc(test_db)
    messages = []
    monkeypatch.setattr("services.plex_upload.log_info", lambda _t, m, **_k: messages.append(m))

    idx = {"movies": {}, "shows": {}, "collections": {}}
    arr = {"movies": {"muppet": {"has_file": False}}}
    d = tmp_path / "A Muppet Family Christmas (1987) {tmdb-13247}"
    d.mkdir()
    (d / "background.jpg").write_bytes(b"b")
    asset = {
        "path": str(d / "background.jpg"), "media_key": "muppet",
        "display_name": "A Muppet Family Christmas (1987) {tmdb-13247}",
        "asset_type": "main", "season_number": None, "folder_year": 1987,
        "artwork_type": "background",
    }

    outcome = svc._upload_artwork_asset(asset, idx, dry_run=True, arr_availability=arr)

    assert outcome.skip_reason == "not_downloaded"
    line = next(m for m in messages if "Skipping unavailable" in m)
    assert line.startswith("Skipping unavailable background: ")
    assert "(no Radarr file available)" in line          # *arr's own wording, as posters use
    assert "No Plex match" not in line                   # the old hybrid phrasing is gone


def test_unavailable_artwork_uses_sonarr_wording_for_shows(test_db, tmp_path, monkeypatch):
    svc = _svc(test_db)
    messages = []
    monkeypatch.setattr("services.plex_upload.log_info", lambda _t, m, **_k: messages.append(m))

    d = tmp_path / "Gracepoint (2014) {tvdb-276396}"
    d.mkdir()
    (d / "logo.png").write_bytes(b"l")
    asset = {
        "path": str(d / "logo.png"), "media_key": "gracepoint", "display_name": "Gracepoint (2014)",
        "asset_type": "main", "season_number": None, "folder_year": 2014, "artwork_type": "logo",
    }
    arr = {"shows": {"gracepoint": {"has_episodes": False, "seasons": {}}}}

    svc._upload_artwork_asset(asset, {"movies": {}, "shows": {}, "collections": {}},
                              dry_run=True, arr_availability=arr)

    assert any("Skipping unavailable logo: " in m and "no Sonarr episodes available" in m
               for m in messages)


def test_arr_answer_never_overrides_a_real_plex_match(test_db, tmp_path, monkeypatch):
    """Regression: the question spans both *arr namespaces, so asking before matching
    let a stale Radarr record veto artwork whose Plex item exists."""
    svc = _svc(test_db)
    item = wrap_item(_FakeArtItem(item_type="movie", title="Snorks Bubbles of Fun", year=1987))
    idx = {"movies": {}, "shows": {}, "collections": {}}
    _prep_matching(svc, idx, item, monkeypatch)

    d = tmp_path / "Snorks Bubbles of Fun (1987) {tmdb-731506}"
    d.mkdir()
    (d / "logo.png").write_bytes(b"l")
    asset = {
        "path": str(d / "logo.png"), "media_key": "snorks", "display_name": "Snorks Bubbles of Fun",
        "asset_type": "main", "season_number": None, "folder_year": 1987, "artwork_type": "logo",
    }
    arr = {"movies": {"snorks": {"has_file": False}}}

    outcome = svc._upload_artwork_asset(asset, idx, dry_run=True, arr_availability=arr)

    assert outcome.matched is True, "Plex has the item — *arr must not veto the upload"
    assert outcome.uploaded == 1
    assert outcome.skip_reason is None
