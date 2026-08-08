"""Asset cleanup: prune destination posters whose drive source is gone (e.g. unsubscribed).

The poster mirror of test_asset_cleanup_artwork.py — a still-matched item kept its placed
poster even after the only drive sourcing it was unsubscribed, so the item stayed "covered"
(wrong style/unmatched numbers) forever. Cleanup now reconciles placed poster/season files
against the poster slots the subscribed drives currently provide.

The drive scan / matching half is exercised by test_sourced_poster_slots below; the cleanup
tests control the sourced map directly so the prune logic and its guards are exact.
"""

from pathlib import Path
from typing import Any, Dict, List

from models.drive import Drive
from models.setting import upsert_setting
from services.poster_renamer import sourced_poster_slots_by_media
from asset_helpers import _collection, _make_folder, _movie, _run, _series


def _full_media(dest_movies: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Media with every type healthy, so per-type safety gates don't interfere."""
    return {
        "movies": dest_movies,
        "series": [_series("Living Show", 2015, "/tv/Living Show (2015)", tvdb_id=42)],
        "collections": [_collection("Marvel")],
    }


def _patch_sourced(monkeypatch, builder):
    """Control what the drives 'currently provide' per media, keyed by id(media)."""
    monkeypatch.setattr(
        "services.poster_renamer.sourced_poster_slots_by_media",
        lambda db, media_dict: builder(media_dict),
    )
    # Keep the artwork half quiet (no artwork drives in these tests).
    monkeypatch.setattr(
        "services.artwork_scan.sourced_types_by_media",
        lambda db, media_dict, boxes=None: {},
    )


def test_prunes_posters_whose_drive_was_unsubscribed(test_db, tmp_path, monkeypatch):
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg", "logo.png"])
    _make_folder(dest, "Inception (2010)", ["poster.jpg"])

    media = _full_media([
        _movie("The Matrix", 1999, "/m/The Matrix (1999)"),
        _movie("Inception", 2010, "/m/Inception (2010)"),
    ])

    # Inception is still sourced; The Matrix's only drive was unsubscribed.
    def sourced(md):
        by_title = {m["title"]: m for m in md["movies"]}
        return {id(by_title["Inception"]): {"poster": {".jpg"}}}

    _patch_sourced(monkeypatch, sourced)
    result = _run(test_db, dest, media, dry_run=False)

    assert not (dest / "The Matrix (1999)" / "poster.jpg").exists()  # unsourced → removed
    assert (dest / "The Matrix (1999)" / "logo.png").exists()        # artwork → not this pass
    assert (dest / "Inception (2010)" / "poster.jpg").exists()       # still sourced → kept
    assert result["counts"]["removed_posters"] == 1


def test_prunes_only_unsourced_season_files(test_db, tmp_path, monkeypatch):
    dest = tmp_path / "assets"
    _make_folder(dest, "Living Show (2015)", ["poster.jpg", "Season01.jpg", "Season02.jpg"])

    show = _series("Living Show", 2015, "/tv/Living Show (2015)", tvdb_id=42, seasons=[1, 2])
    media = {
        "movies": [_movie("The Matrix", 1999, "/m/The Matrix (1999)")],
        "series": [show],
        "collections": [_collection("Marvel")],
    }

    # Drives still source the main poster and season 1; season 2's source is gone.
    def sourced(md):
        return {id(md["series"][0]): {"poster": {".jpg"}, 1: {".jpg"}}}

    _patch_sourced(monkeypatch, sourced)
    result = _run(test_db, dest, media, dry_run=False)

    assert (dest / "Living Show (2015)" / "poster.jpg").exists()
    assert (dest / "Living Show (2015)" / "Season01.jpg").exists()
    assert not (dest / "Living Show (2015)" / "Season02.jpg").exists()
    assert result["counts"]["removed_posters"] == 1


def test_empty_sourced_map_prunes_nothing(test_db, tmp_path, monkeypatch):
    """An empty map is indistinguishable from a down/broken drive scan — never mass-delete."""
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg"])

    media = _full_media([_movie("The Matrix", 1999, "/m/The Matrix (1999)")])
    _patch_sourced(monkeypatch, lambda md: {})
    result = _run(test_db, dest, media, dry_run=False)

    assert (dest / "The Matrix (1999)" / "poster.jpg").exists()
    assert result["counts"]["removed_posters"] == 0


def test_no_subscribed_poster_drives_prunes_nothing(test_db, tmp_path, monkeypatch):
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg"])
    media = _full_media([_movie("The Matrix", 1999, "/m/The Matrix (1999)")])

    def raising(db, media_dict):
        raise ValueError("No poster drives in the priority list are subscribed")

    monkeypatch.setattr("services.poster_renamer.sourced_poster_slots_by_media", raising)
    monkeypatch.setattr("services.artwork_scan.sourced_types_by_media", lambda db, md, boxes=None: {})
    result = _run(test_db, dest, media, dry_run=False)

    assert (dest / "The Matrix (1999)" / "poster.jpg").exists()
    assert result["counts"]["removed_posters"] == 0


def test_unreadable_poster_drive_prunes_nothing(test_db, tmp_path, monkeypatch):
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg"])
    media = _full_media([_movie("The Matrix", 1999, "/m/The Matrix (1999)")])

    def unreadable(db, media_dict):
        raise OSError("poster drive not mounted")

    monkeypatch.setattr("services.poster_renamer.sourced_poster_slots_by_media", unreadable)
    monkeypatch.setattr("services.artwork_scan.sourced_types_by_media", lambda db, md, boxes=None: {})
    result = _run(test_db, dest, media, dry_run=False)

    assert (dest / "The Matrix (1999)" / "poster.jpg").exists()
    assert result["counts"]["removed_posters"] == 0


def test_a_defect_in_the_poster_scan_is_not_swallowed(test_db, tmp_path, monkeypatch):
    import pytest as _pytest

    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg"])
    media = _full_media([_movie("The Matrix", 1999, "/m/The Matrix (1999)")])

    def broken(db, media_dict):
        raise TypeError("unexpected keyword argument")

    monkeypatch.setattr("services.poster_renamer.sourced_poster_slots_by_media", broken)
    monkeypatch.setattr("services.artwork_scan.sourced_types_by_media", lambda db, md, boxes=None: {})
    with _pytest.raises(TypeError):
        _run(test_db, dest, media, dry_run=False)


def test_down_media_source_never_touches_its_posters(test_db, tmp_path, monkeypatch):
    """Sonarr down → the series folder isn't matched, so it's never reconciled."""
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg"])                     # live, sourced
    _make_folder(dest, "Breaking Bad (2008)", ["poster.jpg", "Season01.jpg"])   # series, source down

    media = {
        "movies": [_movie("The Matrix", 1999, "/m/The Matrix (1999)")],
        "series": [],  # Sonarr down
        "collections": [_collection("Marvel")],
    }

    def sourced(md):
        return {id(md["movies"][0]): {"poster": {".jpg"}}}

    _patch_sourced(monkeypatch, sourced)
    result = _run(test_db, dest, media, dry_run=False)

    assert (dest / "Breaking Bad (2008)" / "poster.jpg").exists()
    assert (dest / "Breaking Bad (2008)" / "Season01.jpg").exists()
    assert result["counts"]["removed_posters"] == 0


def test_ignore_list_protects_a_folders_posters(test_db, tmp_path, monkeypatch):
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg"])
    _make_folder(dest, "Inception (2010)", ["poster.jpg"])

    media = _full_media([
        _movie("The Matrix", 1999, "/m/The Matrix (1999)"),
        _movie("Inception", 2010, "/m/Inception (2010)"),
    ])
    upsert_setting(test_db, "asset_cleanup_ignore", '["The Matrix (1999)"]')
    test_db.commit()

    def sourced(md):
        by_title = {m["title"]: m for m in md["movies"]}
        return {id(by_title["Inception"]): {"poster": {".jpg"}}}

    _patch_sourced(monkeypatch, sourced)
    result = _run(test_db, dest, media, dry_run=False)

    assert (dest / "The Matrix (1999)" / "poster.jpg").exists()  # ignored → kept
    assert result["counts"]["removed_posters"] == 0


def test_dry_run_reports_but_does_not_remove(test_db, tmp_path, monkeypatch):
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg"])
    _make_folder(dest, "Inception (2010)", ["poster.jpg"])

    media = _full_media([
        _movie("The Matrix", 1999, "/m/The Matrix (1999)"),
        _movie("Inception", 2010, "/m/Inception (2010)"),
    ])

    def sourced(md):
        by_title = {m["title"]: m for m in md["movies"]}
        return {id(by_title["Inception"]): {"poster": {".jpg"}}}

    _patch_sourced(monkeypatch, sourced)
    result = _run(test_db, dest, media, dry_run=True)

    assert (dest / "The Matrix (1999)" / "poster.jpg").exists()
    assert result["counts"]["removed_posters"] == 1


def test_prunes_stale_extension_beside_the_live_poster(test_db, tmp_path, monkeypatch):
    """Placement copies the SOURCE extension, so when a drive switches .png → .jpg the old
    poster.png lingers. A slot-only check kept it forever (and kept the item looking covered)."""
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg", "poster.png"])
    _make_folder(dest, "Living Show (2015)", ["Season01.jpg", "Season01.png"])

    show = _series("Living Show", 2015, "/tv/Living Show (2015)", tvdb_id=42, seasons=[1])
    media = {
        "movies": [_movie("The Matrix", 1999, "/m/The Matrix (1999)")],
        "series": [show],
        "collections": [_collection("Marvel")],
    }

    def sourced(md):
        return {
            id(md["movies"][0]): {"poster": {".jpg"}},
            id(md["series"][0]): {1: {".jpg"}},
        }

    _patch_sourced(monkeypatch, sourced)
    result = _run(test_db, dest, media, dry_run=False)

    assert (dest / "The Matrix (1999)" / "poster.jpg").exists()        # live extension → kept
    assert not (dest / "The Matrix (1999)" / "poster.png").exists()    # stale extension → removed
    assert (dest / "Living Show (2015)" / "Season01.jpg").exists()
    assert not (dest / "Living Show (2015)" / "Season01.png").exists()
    assert result["counts"]["removed_posters"] == 2


def test_keeps_an_extension_any_matching_drive_still_sources(test_db, tmp_path, monkeypatch):
    """Two drives sourcing different extensions union, so neither placed copy is pruned."""
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg", "poster.png"])

    media = _full_media([_movie("The Matrix", 1999, "/m/The Matrix (1999)")])
    _patch_sourced(monkeypatch, lambda md: {id(md["movies"][0]): {"poster": {".jpg", ".png"}}})
    result = _run(test_db, dest, media, dry_run=False)

    assert (dest / "The Matrix (1999)" / "poster.jpg").exists()
    assert (dest / "The Matrix (1999)" / "poster.png").exists()
    assert result["counts"]["removed_posters"] == 0


# ── tmp/ staging ────────────────────────────────────────────────────────────


def test_prunes_unsourced_posters_from_tmp_staging(test_db, tmp_path, monkeypatch):
    """tmp/ is preserved across runs as the border replacer's incremental source, so pruning
    only the destination left the staged copy to be copied back (or re-rendered as
    'missing_destination') on the next run — a place/prune loop. Prune both."""
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg"])
    _make_folder(dest, "Inception (2010)", ["poster.jpg"])
    _make_folder(dest / "tmp", "The Matrix (1999)", ["poster.jpg"])
    _make_folder(dest / "tmp", "Inception (2010)", ["poster.jpg"])

    media = _full_media([
        _movie("The Matrix", 1999, "/m/The Matrix (1999)"),
        _movie("Inception", 2010, "/m/Inception (2010)"),
    ])

    def sourced(md):
        by_title = {m["title"]: m for m in md["movies"]}
        return {id(by_title["Inception"]): {"poster": {".jpg"}}}

    _patch_sourced(monkeypatch, sourced)
    result = _run(test_db, dest, media, dry_run=False)

    # Unsourced: gone from BOTH roots, so nothing can copy it forward next run.
    assert not (dest / "The Matrix (1999)" / "poster.jpg").exists()
    assert not (dest / "tmp" / "The Matrix (1999)" / "poster.jpg").exists()
    # Still sourced: untouched in both.
    assert (dest / "Inception (2010)" / "poster.jpg").exists()
    assert (dest / "tmp" / "Inception (2010)" / "poster.jpg").exists()
    assert result["counts"]["removed_posters"] == 2


def test_tmp_staging_respects_the_empty_sourced_guard(test_db, tmp_path, monkeypatch):
    """The 'never mass-delete on a broken drive scan' guard covers tmp/ the same as dest."""
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg"])
    _make_folder(dest / "tmp", "The Matrix (1999)", ["poster.jpg"])

    media = _full_media([_movie("The Matrix", 1999, "/m/The Matrix (1999)")])
    _patch_sourced(monkeypatch, lambda md: {})
    result = _run(test_db, dest, media, dry_run=False)

    assert (dest / "The Matrix (1999)" / "poster.jpg").exists()
    assert (dest / "tmp" / "The Matrix (1999)" / "poster.jpg").exists()
    assert result["counts"]["removed_posters"] == 0


# ── flat layout (Use Asset Folders off) ─────────────────────────────────────


def _flat(root: Path, name: str) -> None:
    (root / name).write_bytes(b"img")


def test_flat_prunes_unsourced_poster(test_db, tmp_path, monkeypatch):
    dest = tmp_path / "assets"
    dest.mkdir()
    _flat(dest, "The Matrix (1999).jpg")
    _flat(dest, "Inception (2010).jpg")

    media = _full_media([
        _movie("The Matrix", 1999, "/m/The Matrix (1999)"),
        _movie("Inception", 2010, "/m/Inception (2010)"),
    ])

    def sourced(md):
        by_title = {m["title"]: m for m in md["movies"]}
        return {id(by_title["Inception"]): {"poster": {".jpg"}}}

    _patch_sourced(monkeypatch, sourced)
    result = _run(test_db, dest, media, dry_run=False)

    assert not (dest / "The Matrix (1999).jpg").exists()  # unsourced → removed
    assert (dest / "Inception (2010).jpg").exists()       # still sourced → kept
    assert result["counts"]["removed_posters"] == 1


# ── sourced_poster_slots_by_media: the drive-scan half, end to end ──────────


def test_sourced_poster_slots_drop_when_drive_file_removed(test_db, tmp_path):
    """Removing a poster from the drive (or unsubscribing it — same scan filter) drops that
    slot from the item's sourced set, which is what tells cleanup the placed copy is stale."""
    drive_root = tmp_path / "driveA"
    drive_root.mkdir()
    (drive_root / "Living Show (2015).jpg").write_bytes(b"img")
    (drive_root / "Living Show (2015) - Season 1.jpg").write_bytes(b"img")
    drive = Drive(name="Drive A", drive_id="drive-a", style_type="MM2K",
                  subscribed=True, custom_path=str(drive_root))
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)
    upsert_setting(test_db, "poster_drive_priority", '{"drive_ids": [%d]}' % drive.id)
    test_db.commit()

    media = {
        "movies": [],
        "series": [_series("Living Show", 2015, "/tv/Living Show (2015)", seasons=[1])],
        "collections": [],
    }
    show_id = id(media["series"][0])

    sourced = sourced_poster_slots_by_media(test_db, media)
    assert sourced[show_id] == {"poster": {".jpg"}, 1: {".jpg"}}

    (drive_root / "Living Show (2015) - Season 1.jpg").unlink()
    sourced = sourced_poster_slots_by_media(test_db, media)
    assert sourced[show_id] == {"poster": {".jpg"}}


def test_sourced_poster_slots_raise_without_subscribed_drives(test_db, tmp_path):
    import pytest

    with pytest.raises(ValueError):
        sourced_poster_slots_by_media(test_db, {"movies": [], "series": [], "collections": []})

    drive = Drive(name="Off Drive", drive_id="drive-off", style_type="MM2K",
                  subscribed=False, custom_path=str(tmp_path))
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)
    upsert_setting(test_db, "poster_drive_priority", '{"drive_ids": [%d]}' % drive.id)
    test_db.commit()

    with pytest.raises(ValueError):
        sourced_poster_slots_by_media(test_db, {"movies": [], "series": [], "collections": []})


def test_sourced_poster_slots_raise_when_drive_folder_missing(test_db, tmp_path):
    import pytest

    drive = Drive(name="Gone Drive", drive_id="drive-gone", style_type="MM2K",
                  subscribed=True, custom_path=str(tmp_path / "not-there"))
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)
    upsert_setting(test_db, "poster_drive_priority", '{"drive_ids": [%d]}' % drive.id)
    test_db.commit()

    with pytest.raises(OSError):
        sourced_poster_slots_by_media(test_db, {"movies": [], "series": [], "collections": []})
