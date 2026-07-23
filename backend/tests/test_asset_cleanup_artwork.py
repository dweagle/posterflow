"""Asset cleanup: prune artwork whose drive source is gone but whose media still exists.

Cleanup reconciles the destination against the live media library, so it removes a whole
item folder when the media leaves *arr/Plex — but a still-matched folder kept its artwork
even after the artwork's source was removed from a drive (or the drive was unsubscribed).
This reconciles placed artwork against what the subscribed drives currently provide.

The drive scan / matching is exercised by test_artwork_scan.py + test_sourced_types below;
here the source map is controlled directly so the cleanup logic and its guards are exact.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from models.artwork_drive import ArtworkDrive
from models.setting import upsert_setting
from services.asset_cleanup import AssetCleanupService
from services.artwork_scan import sourced_types_by_media
from util.data.normalization import normalize_titles


def _make_folder(root: Path, name: str, files: List[str]) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    for file_name in files:
        (folder / file_name).write_bytes(b"img")
    return folder


def _movie(title: str, year: int, folder: str, tmdb_id: Optional[int] = None) -> Dict[str, Any]:
    return {
        "type": "movies", "title": title, "year": year, "tmdb_id": tmdb_id, "imdb_id": None,
        "normalized_title": normalize_titles(title), "alternate_titles": [],
        "normalized_alternate_titles": [], "folder": folder,
    }


def _series(title: str, year: int, folder: str, tvdb_id: Optional[int] = None) -> Dict[str, Any]:
    return {
        "type": "series", "title": title, "year": year, "tvdb_id": tvdb_id, "imdb_id": None,
        "normalized_title": normalize_titles(title), "alternate_titles": [],
        "normalized_alternate_titles": [], "folder": folder,
        "seasons": [{"season_number": 1, "season_has_episodes": True}],
    }


def _collection(title: str) -> Dict[str, Any]:
    return {
        "type": "collections", "title": title, "year": None, "tmdb_id": None,
        "normalized_title": normalize_titles(title), "alternate_titles": [],
        "normalized_alternate_titles": [], "folder": title,
    }


def _patch_sourced(monkeypatch, builder):
    """Control what the drives 'currently provide' per media, keyed by id(media)."""
    monkeypatch.setattr(
        "services.artwork_scan.sourced_types_by_media",
        lambda db, media_dict, boxes=None: builder(media_dict),
    )


def _run(test_db, dest: Path, media_dict, **kwargs):
    return AssetCleanupService(test_db).cleanup(str(dest), media_dict=media_dict, **kwargs)


def test_prunes_artwork_whose_source_left_the_drive(test_db, tmp_path, monkeypatch):
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg", "logo.png", "background.jpg"])
    _make_folder(dest, "Inception (2010)", ["poster.jpg", "logo.png"])

    media = {
        "movies": [_movie("The Matrix", 1999, "/m/The Matrix (1999)"), _movie("Inception", 2010, "/m/Inception (2010)")],
        "series": [_series("Living Show", 2015, "/tv/Living Show (2015)", tvdb_id=42)],
        "collections": [_collection("Marvel")],
    }

    # The Matrix lost its logo source (keeps background); Inception still sources its logo.
    def sourced(md):
        by_title = {m["title"]: m for m in md["movies"]}
        return {id(by_title["The Matrix"]): {"background"}, id(by_title["Inception"]): {"logo"}}

    _patch_sourced(monkeypatch, sourced)
    result = _run(test_db, dest, media, dry_run=False, delete_unknown=False)

    assert not (dest / "The Matrix (1999)" / "logo.png").exists()      # orphaned → removed
    assert (dest / "The Matrix (1999)" / "background.jpg").exists()    # still sourced → kept
    assert (dest / "The Matrix (1999)" / "poster.jpg").exists()        # not artwork → untouched
    assert (dest / "Inception (2010)" / "logo.png").exists()           # still sourced → kept
    assert result["counts"]["removed_artwork"] == 1


def test_conservative_when_a_type_has_no_source_anywhere(test_db, tmp_path, monkeypatch):
    """No item sources logos (e.g. unsubscribed the only logo drive) — can't tell that from a
    down source, so logos are never mass-deleted."""
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg", "logo.png"])

    media = {
        "movies": [_movie("The Matrix", 1999, "/m/The Matrix (1999)")],
        "series": [_series("Living Show", 2015, "/tv/Living Show (2015)", tvdb_id=42)],
        "collections": [_collection("Marvel")],
    }

    _patch_sourced(monkeypatch, lambda md: {})  # nothing sourced
    result = _run(test_db, dest, media, dry_run=False)

    assert (dest / "The Matrix (1999)" / "logo.png").exists()
    assert result["counts"]["removed_artwork"] == 0


def test_no_subscribed_artwork_drives_prunes_nothing(test_db, tmp_path, monkeypatch):
    """sourced_types_by_media raises when no artwork drives are subscribed; cleanup treats
    that as 'prune nothing' rather than wiping everything."""
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg", "logo.png", "background.jpg"])

    media = {
        "movies": [_movie("The Matrix", 1999, "/m/The Matrix (1999)")],
        "series": [_series("Living Show", 2015, "/tv/Living Show (2015)", tvdb_id=42)],
        "collections": [_collection("Marvel")],
    }

    def raising(db, media_dict, boxes=None):
        raise ValueError("No subscribed artwork drives")

    monkeypatch.setattr("services.artwork_scan.sourced_types_by_media", raising)
    result = _run(test_db, dest, media, dry_run=False)

    assert (dest / "The Matrix (1999)" / "logo.png").exists()
    assert (dest / "The Matrix (1999)" / "background.jpg").exists()
    assert result["counts"]["removed_artwork"] == 0


def test_unreadable_artwork_drive_prunes_nothing(test_db, tmp_path, monkeypatch):
    """An unmounted/unreadable drive is an EXPECTED failure — degrade to pruning nothing."""
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg", "logo.png"])
    media = {"movies": [_movie("The Matrix", 1999, "/movies/The Matrix (1999)", tmdb_id=603)],
             "series": [], "collections": []}

    def unreadable(db, media_dict, boxes=None):
        raise OSError("artwork drive not mounted")

    monkeypatch.setattr("services.artwork_scan.sourced_types_by_media", unreadable)
    result = _run(test_db, dest, media, dry_run=False)

    assert (dest / "The Matrix (1999)" / "logo.png").exists()
    assert result["counts"]["removed_artwork"] == 0


def test_a_defect_in_the_artwork_scan_is_not_swallowed(test_db, tmp_path, monkeypatch):
    """A programming error must NOT degrade to 'prune nothing' — that is indistinguishable
    from a healthy run with nothing to clean, and hid a real signature mismatch."""
    import pytest as _pytest

    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg", "logo.png"])
    media = {"movies": [_movie("The Matrix", 1999, "/movies/The Matrix (1999)", tmdb_id=603)],
             "series": [], "collections": []}

    def broken(db, media_dict, boxes=None):
        raise TypeError("unexpected keyword argument")

    monkeypatch.setattr("services.artwork_scan.sourced_types_by_media", broken)
    with _pytest.raises(TypeError):
        _run(test_db, dest, media, dry_run=False)


def test_down_media_source_never_wipes_its_artwork(test_db, tmp_path, monkeypatch):
    """Sonarr down → series list empty. The series folder isn't matched, so its artwork is
    never reconciled even while a live movie's orphaned logo is pruned."""
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg", "logo.png"])       # live, still sources logo
    _make_folder(dest, "Inception (2010)", ["poster.jpg", "logo.png"])        # live, logo removed
    _make_folder(dest, "Breaking Bad (2008)", ["poster.jpg", "logo.png", "Season01.jpg"])  # series, source down

    media = {
        "movies": [_movie("The Matrix", 1999, "/m/The Matrix (1999)"), _movie("Inception", 2010, "/m/Inception (2010)")],
        "series": [],  # Sonarr down
        "collections": [_collection("Marvel")],
    }

    def sourced(md):
        by_title = {m["title"]: m for m in md["movies"]}
        return {id(by_title["The Matrix"]): {"logo"}, id(by_title["Inception"]): set()}

    _patch_sourced(monkeypatch, sourced)
    result = _run(test_db, dest, media, dry_run=False)

    assert not (dest / "Inception (2010)" / "logo.png").exists()   # orphaned → pruned
    assert (dest / "The Matrix (1999)" / "logo.png").exists()      # still sourced → kept
    assert (dest / "Breaking Bad (2008)" / "logo.png").exists()    # down source → never touched
    assert result["counts"]["removed_artwork"] == 1


def test_dry_run_reports_but_does_not_remove(test_db, tmp_path, monkeypatch):
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg", "logo.png"])

    media = {
        "movies": [_movie("The Matrix", 1999, "/m/The Matrix (1999)"), _movie("Inception", 2010, "/m/Inception (2010)")],
        "series": [_series("Living Show", 2015, "/tv/Living Show (2015)", tvdb_id=42)],
        "collections": [_collection("Marvel")],
    }
    _make_folder(dest, "Inception (2010)", ["poster.jpg", "logo.png"])

    # logo is globally sourced (Inception), but The Matrix no longer sources it.
    def sourced(md):
        by_title = {m["title"]: m for m in md["movies"]}
        return {id(by_title["The Matrix"]): set(), id(by_title["Inception"]): {"logo"}}

    _patch_sourced(monkeypatch, sourced)
    result = _run(test_db, dest, media, dry_run=True)

    assert (dest / "The Matrix (1999)" / "logo.png").exists()  # dry run removes nothing
    assert result["counts"]["removed_artwork"] == 1


def test_ignore_list_protects_a_folders_artwork(test_db, tmp_path, monkeypatch):
    dest = tmp_path / "assets"
    _make_folder(dest, "The Matrix (1999)", ["poster.jpg", "logo.png"])

    media = {
        "movies": [_movie("The Matrix", 1999, "/m/The Matrix (1999)"), _movie("Inception", 2010, "/m/Inception (2010)")],
        "series": [_series("Living Show", 2015, "/tv/Living Show (2015)", tvdb_id=42)],
        "collections": [_collection("Marvel")],
    }
    _make_folder(dest, "Inception (2010)", ["poster.jpg", "logo.png"])
    upsert_setting(test_db, "asset_cleanup_ignore", '["The Matrix (1999)"]')
    test_db.commit()

    def sourced(md):
        by_title = {m["title"]: m for m in md["movies"]}
        return {id(by_title["The Matrix"]): set(), id(by_title["Inception"]): {"logo"}}

    _patch_sourced(monkeypatch, sourced)
    result = _run(test_db, dest, media, dry_run=False)

    assert (dest / "The Matrix (1999)" / "logo.png").exists()  # ignored → kept
    assert result["counts"]["removed_artwork"] == 0


# ── sourced_types_by_media: the drive-scan half, end to end ─────────────────


def _seed_artwork(root, *, logo=None, background=None):
    for sub, fname in (("logos", logo), ("backgrounds", background)):
        if not fname:
            continue
        d = root / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / fname).write_bytes(b"img")


def test_sourced_types_drops_a_type_when_its_file_is_removed(test_db, tmp_path):
    """Removing an artwork file from the drive drops that type from the item's sourced set —
    which is exactly what tells cleanup the placed copy is now orphaned."""
    drive_root = tmp_path / "makerA"
    _seed_artwork(drive_root, logo="Inception (2010) {tmdb-27205}.png", background="Inception (2010) {tmdb-27205}.jpg")
    drive = ArtworkDrive(name="MakerA", drive_id="makerA", subscribed=True, custom_path=str(drive_root))
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)
    upsert_setting(test_db, "artwork_drive_priority", '{"drive_ids": [%d]}' % drive.id)
    test_db.commit()

    media = {
        "movies": [{"title": "Inception", "year": 2010, "tmdb_id": 27205,
                    "folder": "Inception (2010) {tmdb-27205}", "instance": "plex"}],
        "series": [], "collections": [],
    }
    movie_id = id(media["movies"][0])

    sourced = sourced_types_by_media(test_db, media)
    assert sourced[movie_id] == {"logo", "background"}

    # Remove the logo from the drive, rescan.
    (drive_root / "logos" / "Inception (2010) {tmdb-27205}.png").unlink()
    sourced = sourced_types_by_media(test_db, media)
    assert sourced[movie_id] == {"background"}


def test_sourced_types_raises_without_subscribed_drives(test_db):
    import pytest
    with pytest.raises(ValueError):
        sourced_types_by_media(test_db, {"movies": [], "series": [], "collections": []})


def test_sourced_matches_collection_tmdb_on_ref(test_db, tmp_path):
    """A collection/series carries its tmdb on tmdb_id_ref (off the matcher). The renamer
    ID-matches it via media_source_refs, so cleanup MUST too — else it can't source the
    artwork the renamer just placed and prunes it every run (a place/prune loop). Title
    here deliberately won't fuzzy-match the box; only the surfaced tmdb can."""
    drive_root = tmp_path / "makerC"
    _seed_artwork(drive_root, logo="The Naked Gun Collection {tmdb-37139}.png")
    drive = ArtworkDrive(name="MakerC", drive_id="makerC", subscribed=True, custom_path=str(drive_root))
    test_db.add(drive)
    test_db.commit()
    test_db.refresh(drive)
    upsert_setting(test_db, "artwork_drive_priority", '{"drive_ids": [%d]}' % drive.id)
    test_db.commit()

    media = {
        "movies": [], "series": [],
        "collections": [{"title": "Naked Gun", "tmdb_id_ref": 37139,
                         "folder": "Naked Gun", "instance": "plex"}],
    }
    coll_id = id(media["collections"][0])

    sourced = sourced_types_by_media(test_db, media)
    assert sourced.get(coll_id) == {"logo"}


# ── flat layout (Use Asset Folders off) ─────────────────────────────────────


def _flat(root: Path, name: str) -> None:
    (root / name).write_bytes(b"img")


def test_flat_prunes_artwork_whose_source_left(test_db, tmp_path, monkeypatch):
    """Flat layout: 'Title (Year)-logo.png' at the root, no per-item folder."""
    dest = tmp_path / "assets"
    dest.mkdir()
    _flat(dest, "The Matrix (1999).jpg")
    _flat(dest, "The Matrix (1999)-logo.png")
    _flat(dest, "The Matrix (1999)-background.jpg")
    _flat(dest, "Inception (2010).jpg")
    _flat(dest, "Inception (2010)-logo.png")

    media = {
        "movies": [_movie("The Matrix", 1999, "/m/The Matrix (1999)"), _movie("Inception", 2010, "/m/Inception (2010)")],
        "series": [_series("Living Show", 2015, "/tv/Living Show (2015)", tvdb_id=42)],
        "collections": [_collection("Marvel")],
    }

    def sourced(md):
        by_title = {m["title"]: m for m in md["movies"]}
        return {id(by_title["The Matrix"]): {"background"}, id(by_title["Inception"]): {"logo"}}

    _patch_sourced(monkeypatch, sourced)
    result = _run(test_db, dest, media, dry_run=False)

    assert not (dest / "The Matrix (1999)-logo.png").exists()       # orphaned → removed
    assert (dest / "The Matrix (1999)-background.jpg").exists()     # still sourced → kept
    assert (dest / "The Matrix (1999).jpg").exists()                  # poster → untouched
    assert (dest / "Inception (2010)-logo.png").exists()           # still sourced → kept
    assert result["counts"]["removed_artwork"] == 1


def test_flat_conservative_when_type_unsourced(test_db, tmp_path, monkeypatch):
    dest = tmp_path / "assets"
    dest.mkdir()
    _flat(dest, "The Matrix (1999).jpg")
    _flat(dest, "The Matrix (1999)-logo.png")

    media = {
        "movies": [_movie("The Matrix", 1999, "/m/The Matrix (1999)")],
        "series": [_series("Living Show", 2015, "/tv/Living Show (2015)", tvdb_id=42)],
        "collections": [_collection("Marvel")],
    }
    _patch_sourced(monkeypatch, lambda md: {})
    result = _run(test_db, dest, media, dry_run=False)

    assert (dest / "The Matrix (1999)-logo.png").exists()
    assert result["counts"]["removed_artwork"] == 0


def test_flat_reconciliation_only_touches_matched_items(test_db, tmp_path, monkeypatch):
    """The source-reconciliation pass only prunes flat artwork for a *matched* item that
    stopped sourcing the type. An unmatched flat file (e.g. a down series) isn't its concern
    — it's left to the pre-existing orphan logic, never counted as artwork removal."""
    dest = tmp_path / "assets"
    dest.mkdir()
    _flat(dest, "The Matrix (1999).jpg")
    _flat(dest, "The Matrix (1999)-logo.png")           # matched, still sources logo → kept
    _flat(dest, "Inception (2010).jpg")
    _flat(dest, "Inception (2010)-logo.png")            # matched, logo removed → pruned by us
    _flat(dest, "Ghost Movie (1990)-logo.png")         # unmatched → not our pass

    media = {
        "movies": [_movie("The Matrix", 1999, "/m/The Matrix (1999)"), _movie("Inception", 2010, "/m/Inception (2010)")],
        "series": [_series("Living Show", 2015, "/tv/Living Show (2015)", tvdb_id=42)],
        "collections": [_collection("Marvel")],
    }

    def sourced(md):
        by_title = {m["title"]: m for m in md["movies"]}
        return {id(by_title["The Matrix"]): {"logo"}, id(by_title["Inception"]): set()}

    _patch_sourced(monkeypatch, sourced)
    result = _run(test_db, dest, media, dry_run=False)

    assert not (dest / "Inception (2010)-logo.png").exists()   # matched + unsourced → pruned by us
    assert (dest / "The Matrix (1999)-logo.png").exists()      # matched + sourced → kept
    # Only the reconciled removal is ours; the unmatched Ghost file (if removed) is orphan logic.
    assert result["counts"]["removed_artwork"] == 1
