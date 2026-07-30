"""Artwork scanning: drive scan (Renamer source) + destination scan (Unmatched check).

Mirrors the poster split — the Renamer scans the drives, Unmatched scans the destination.
"""

import os

import pytest

from models.artwork_drive import ArtworkDrive
from models.setting import upsert_setting
from services.artwork_scan import (
    scan_artwork_drive_boxes,
    scan_destination_artwork,
    build_artwork_index,
)


def _make_drive(db, name, drive_id, path, subscribed=True):
    drive = ArtworkDrive(name=name, drive_id=drive_id, subscribed=subscribed, custom_path=str(path))
    db.add(drive)
    db.commit()
    db.refresh(drive)
    return drive


def _set_priority(db, *drive_ids):
    upsert_setting(db, "artwork_drive_priority", '{"drive_ids": [%s]}' % ", ".join(str(i) for i in drive_ids))
    db.commit()


def _seed_artwork(root, *, logo=None, background=None, squareart=None):
    for sub, fname in (("logos", logo), ("backgrounds", background), ("squareart", squareart)):
        if not fname:
            continue
        d = root / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / fname).write_bytes(b"img")


def _movie(title, year, tmdb_id, folder):
    return {"title": title, "year": year, "tmdb_id": tmdb_id, "folder": folder, "instance": "plex"}


# ── drive scan (Renamer source) ──────────────────────────────────────────────


def test_scan_boxes_groups_an_items_artwork_into_one_box(test_db, tmp_path):
    # An item's logo/background/square (same stem, different subfolders) become ONE box
    # with those slots filled — the same box shape a poster produces, ready for one match.
    drive_root = tmp_path / "makerA"
    _seed_artwork(
        drive_root,
        logo="Inception (2010) {tmdb-27205}.png",
        background="Inception (2010) {tmdb-27205}.jpg",
        squareart="Inception (2010) {tmdb-27205}.jpg",
    )
    d = _make_drive(test_db, "MakerA", "makerA", drive_root)
    _set_priority(test_db, d.id)

    boxes = scan_artwork_drive_boxes(test_db)
    assert len(boxes) == 1
    box = boxes[0]
    assert box["tmdb_id"] == 27205 and box["title"] == "Inception"
    assert box["_priority"] == 0
    slots = box["slots"]
    assert slots["logo"].endswith(os.path.join("logos", "Inception (2010) {tmdb-27205}.png"))
    assert slots["background"].endswith(os.path.join("backgrounds", "Inception (2010) {tmdb-27205}.jpg"))
    assert slots["square"].endswith(os.path.join("squareart", "Inception (2010) {tmdb-27205}.jpg"))
    # Artwork fills only the artwork slots; poster/seasons stay empty.
    assert slots["poster"] is None and slots["seasons"] == {}


def test_scan_boxes_one_box_per_item(test_db, tmp_path):
    drive_root = tmp_path / "makerA"
    _seed_artwork(drive_root, logo="Inception (2010) {tmdb-27205}.png")
    _seed_artwork(drive_root, background="The Matrix (1999) {tmdb-603}.jpg")
    d = _make_drive(test_db, "MakerA", "makerA", drive_root)
    _set_priority(test_db, d.id)

    boxes = scan_artwork_drive_boxes(test_db)
    assert len(boxes) == 2
    titles = sorted(b["title"] for b in boxes)
    assert titles == ["Inception", "The Matrix"]


def test_scan_merges_slots_across_drives_into_one_box(test_db, tmp_path):
    # Cross-drive per-slot priority now happens in the SCAN, via the shared merge_assets —
    # so matching gets one box per item and doesn't need its own merge step.
    a = tmp_path / "makerA"
    b = tmp_path / "makerB"
    _seed_artwork(a, logo="Inception (2010) {tmdb-27205}.png", background="Inception (2010) {tmdb-27205}.jpg")
    _seed_artwork(b, logo="Inception (2010) {tmdb-27205}.png", squareart="Inception (2010) {tmdb-27205}.jpg")
    da = _make_drive(test_db, "MakerA", "makerA", a)
    db_ = _make_drive(test_db, "MakerB", "makerB", b)
    _set_priority(test_db, da.id, db_.id)

    boxes = scan_artwork_drive_boxes(test_db)
    assert len(boxes) == 1
    slots = boxes[0]["slots"]
    assert slots["logo"].startswith(str(a))        # A wins the contested slot
    assert slots["background"].startswith(str(a))  # only A has it
    assert slots["square"].startswith(str(b))      # only B has it
    # files stays consistent with slots (the filename dedup would otherwise collapse them).
    assert sorted(boxes[0]["files"]) == sorted(v for v in slots.values() if isinstance(v, str))
    # Type-less so the matcher accepts it for movies, series, or collections alike.
    assert boxes[0]["type"] is None


def test_artwork_matching_goes_through_the_poster_matcher_and_logs(test_db, tmp_path):
    # The point of the merge: artwork matches via match_assets_to_media, so it produces the
    # same [MATCH] evidence posters do. Previously artwork matched silently.
    from loguru import logger
    from util.posters.index import build_search_index, create_new_empty_index
    from util.posters.match import match_assets_to_media

    drive_root = tmp_path / "makerA"
    _seed_artwork(drive_root, logo="Inception (2010) {tmdb-27205}.png")
    d = _make_drive(test_db, "MakerA", "makerA", drive_root)
    _set_priority(test_db, d.id)

    boxes = scan_artwork_drive_boxes(test_db)
    index = create_new_empty_index()
    for box in boxes:
        build_search_index(index, box["title"], box)
    media = {"movies": [_movie("Inception", 2010, 27205, "Inception (2010)")],
             "series": [], "collections": []}

    messages: list = []
    sink_id = logger.add(messages.append, level="DEBUG")
    try:
        matched = match_assets_to_media(media, index)
    finally:
        logger.remove(sink_id)
    logged = "".join(messages)

    assert len(matched["movies"]) == 1
    assert matched["movies"][0]["asset_ref"]["slots"]["logo"].endswith("Inception (2010) {tmdb-27205}.png")
    assert "MATCH" in logged and "Matched by tmdb_id" in logged


def test_scan_groups_real_drive_naming_with_type_suffix(test_db, tmp_path):
    """Drives name artwork "Title (Year) {ids} - logo.png" / "... - background.jpg".

    The grouping stem must drop that suffix, or each file becomes its own box and the whole
    library goes through merge_assets to be reunited by title (18,930 boxes instead of 7,258
    on the real drive, and ~50k log lines of merge churn). The older tests seeded names
    WITHOUT the suffix, so they never exercised this.
    """
    drive_root = tmp_path / "makerA"
    base = "Inception (2010) {tmdb-27205}"
    _seed_artwork(
        drive_root,
        logo=f"{base} - logo.png",
        background=f"{base} - background.jpg",
        squareart=f"{base} - squareart.jpg",
    )
    d = _make_drive(test_db, "MakerA", "makerA", drive_root)
    _set_priority(test_db, d.id)

    boxes = scan_artwork_drive_boxes(test_db)
    assert len(boxes) == 1, "one item's three artwork files must group into ONE box"
    slots = boxes[0]["slots"]
    assert slots["logo"] and slots["background"] and slots["square"]
    assert boxes[0]["title"] == "Inception" and boxes[0]["tmdb_id"] == 27205


def test_sourced_types_reuses_a_supplied_scan(test_db, tmp_path, monkeypatch):
    # A rename+cleanup run walked every artwork drive TWICE; cleanup now reuses the scan.
    import services.artwork_scan as ascan

    drive_root = tmp_path / "makerA"
    _seed_artwork(drive_root, logo="Inception (2010) {tmdb-27205}.png")
    d = _make_drive(test_db, "MakerA", "makerA", drive_root)
    _set_priority(test_db, d.id)

    boxes = scan_artwork_drive_boxes(test_db)
    monkeypatch.setattr(ascan, "scan_artwork_drive_boxes",
                        lambda db: pytest.fail("re-scanned the drives instead of reusing the scan"))

    media = {"movies": [_movie("Inception", 2010, 27205, "Inception (2010)")], "series": [], "collections": []}
    sourced = ascan.sourced_types_by_media(test_db, media, boxes)
    assert sourced[id(media["movies"][0])] == {"logo": {".png"}}


def test_include_filter_is_placement_time_slot_names(test_db):
    # The Include selection no longer touches the scan at all — matching sees the full slot
    # set (so cleanup learns everything the drives source) and the filter applies at
    # placement as a set of slot names (squareart's on-disk slot is 'square').
    from modules.renamer import _artwork_slot_filter

    assert _artwork_slot_filter(["logo", "squareart"]) == {"logo", "square"}
    assert _artwork_slot_filter([]) == set()


def test_per_slot_drive_priority_survives_matching(test_db, tmp_path):
    # End-to-end: an item's logo/background/square can come from DIFFERENT drives, and the
    # per-slot winner still reaches the caller through the shared matcher.
    from util.posters.match import match_assets_to_media

    a = tmp_path / "makerA"
    b = tmp_path / "makerB"
    _seed_artwork(a, logo="Inception (2010) {tmdb-27205}.png", background="Inception (2010) {tmdb-27205}.jpg")
    _seed_artwork(b, logo="Inception (2010) {tmdb-27205}.png", squareart="Inception (2010) {tmdb-27205}.jpg")
    da = _make_drive(test_db, "MakerA", "makerA", a)
    db_ = _make_drive(test_db, "MakerB", "makerB", b)
    _set_priority(test_db, da.id, db_.id)  # A is priority 0, B is priority 1

    index = build_artwork_index(scan_artwork_drive_boxes(test_db))
    media = _movie("Inception", 2010, 27205, "Inception (2010) {tmdb-27205}")
    matched = match_assets_to_media({"movies": [media], "series": [], "collections": []}, index)

    assert len(matched["movies"]) == 1
    slots = matched["movies"][0]["asset_ref"]["slots"]
    assert slots["logo"].startswith(str(a))          # A wins logo (higher priority)
    assert slots["background"].startswith(str(a))    # only A has background
    assert slots["square"].startswith(str(b))        # only B has square


def test_scan_raises_without_priority(test_db, tmp_path):
    # Like poster renamer: no drive priority configured is an error, not a silent scan.
    drive_root = tmp_path / "makerA"
    _seed_artwork(drive_root, logo="Inception (2010) {tmdb-27205}.png")
    _make_drive(test_db, "MakerA", "makerA", drive_root)
    with pytest.raises(ValueError, match="priority"):
        scan_artwork_drive_boxes(test_db)


def test_scan_raises_when_no_priority_drive_subscribed(test_db, tmp_path):
    drive_root = tmp_path / "off"
    _seed_artwork(drive_root, logo="Inception (2010) {tmdb-27205}.png")
    d = _make_drive(test_db, "Off", "off", drive_root, subscribed=False)
    _set_priority(test_db, d.id)
    with pytest.raises(ValueError, match="subscribed"):
        scan_artwork_drive_boxes(test_db)


# ── destination scan (Unmatched check) ───────────────────────────────────────


def test_destination_scan_nested(tmp_path):
    item = tmp_path / "Inception (2010) {tmdb-27205}"
    item.mkdir()
    (item / "poster.jpg").write_bytes(b"img")
    (item / "logo.png").write_bytes(b"img")
    (item / "background.jpg").write_bytes(b"img")
    # No squareart placed for this item.

    by_type = scan_destination_artwork(str(tmp_path), asset_folders=True)
    assert len(by_type["logo"]) == 1
    assert len(by_type["background"]) == 1
    assert by_type["squareart"] == []
    assert by_type["logo"][0]["tmdb_id"] == 27205
    assert by_type["logo"][0]["title"] == "Inception"


def test_destination_scan_flat(tmp_path):
    (tmp_path / "Inception (2010) {tmdb-27205}.jpg").write_bytes(b"img")
    (tmp_path / "Inception (2010) {tmdb-27205}-logo.png").write_bytes(b"img")
    (tmp_path / "Inception (2010) {tmdb-27205}-square.jpg").write_bytes(b"img")

    by_type = scan_destination_artwork(str(tmp_path), asset_folders=False)
    assert len(by_type["logo"]) == 1
    assert by_type["background"] == []
    assert len(by_type["squareart"]) == 1
    assert by_type["logo"][0]["tmdb_id"] == 27205


def test_destination_scan_missing_dir():
    by_type = scan_destination_artwork("/no/such/dir", asset_folders=True)
    assert by_type == {"logo": [], "background": [], "squareart": []}
