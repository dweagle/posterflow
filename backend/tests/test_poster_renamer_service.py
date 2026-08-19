import os

from models.setting import Setting, get_setting
from services.poster_renamer import PosterRenameService


# ---------------------------------------------------------------------------
# Placement characterization — pins the EXACT current rename_files output so the
# upcoming "walk slots" migration can be proven byte-identical. Behavior probed,
# not assumed (incl. the no-space "Season01" skip and first-source-wins dedup).
# ---------------------------------------------------------------------------


def _seed(src, names):
    src.mkdir(parents=True, exist_ok=True)
    for n in names:
        (src / n).write_bytes(b"x")
    return {n: str(src / n) for n in names}


def _matched(movies=None, series=None, collections=None):
    return {"collections": collections or [], "movies": movies or [], "series": series or []}


def test_placement_movie_nested_and_flat(test_db, tmp_path):
    f = _seed(tmp_path / "src", ["Inception (2010).jpg"])
    m = _matched(movies=[{"title": "Inception", "year": 2010, "folder": "Inception (2010)", "files": [f["Inception (2010).jpg"]]}])
    svc = PosterRenameService(test_db)

    nested = tmp_path / "nested"; nested.mkdir()
    svc.rename_files(m, str(nested), action_type="copy", asset_folders=True, dry_run=False)
    assert (nested / "Inception (2010)" / "poster.jpg").is_file()

    flat = tmp_path / "flat"; flat.mkdir()
    svc.rename_files(m, str(flat), action_type="copy", asset_folders=False, dry_run=False)
    assert (flat / "Inception (2010).jpg").is_file()


def test_placement_series_seasons_and_specials(test_db, tmp_path):
    f = _seed(tmp_path / "src", [
        "Show - Season 01.jpg",   # -> Season01.jpg
        "Show - Season01.jpg",    # no space -> SKIPPED (number regex needs the space)
        "Show - Specials.jpg",    # -> Season00.jpg
        "Show.jpg",               # -> poster.jpg
    ])
    files = [f["Show - Season 01.jpg"], f["Show - Season01.jpg"], f["Show - Specials.jpg"], f["Show.jpg"]]
    m = _matched(series=[{"title": "Show", "year": 2020, "folder": "Show (2020)", "files": files}])
    svc = PosterRenameService(test_db)

    nested = tmp_path / "nested"; nested.mkdir()
    svc.rename_files(m, str(nested), action_type="copy", asset_folders=True, dry_run=False)
    item = nested / "Show (2020)"
    assert sorted(p.name for p in item.iterdir()) == ["Season00.jpg", "Season01.jpg", "poster.jpg"]

    flat = tmp_path / "flat"; flat.mkdir()
    svc.rename_files(m, str(flat), action_type="copy", asset_folders=False, dry_run=False)
    assert sorted(os.listdir(flat)) == ["Show (2020).jpg", "Show (2020)_Season00.jpg", "Show (2020)_Season01.jpg"]


def test_placement_collection_nested(test_db, tmp_path):
    f = _seed(tmp_path / "src", ["Marvel Collection.jpg"])
    m = _matched(collections=[{"title": "Marvel Collection", "year": None, "folder": "Marvel Collection", "files": [f["Marvel Collection.jpg"]]}])
    svc = PosterRenameService(test_db)
    dest = tmp_path / "dest"; dest.mkdir()
    svc.rename_files(m, str(dest), action_type="copy", asset_folders=True, dry_run=False)
    assert (dest / "Marvel Collection" / "poster.jpg").is_file()


def test_placement_first_source_wins_on_dest_collision(test_db, tmp_path):
    # Two non-season files for one item both target poster.jpg; the FIRST wins, second skipped.
    f = _seed(tmp_path / "src", ["Show first.jpg", "Show second.jpg"])
    (tmp_path / "src" / "Show first.jpg").write_bytes(b"AAA")
    (tmp_path / "src" / "Show second.jpg").write_bytes(b"BBB")
    m = _matched(movies=[{"title": "Show", "year": 2020, "folder": "Show (2020)",
                          "files": [f["Show first.jpg"], f["Show second.jpg"]]}])
    svc = PosterRenameService(test_db)
    dest = tmp_path / "dest"; dest.mkdir()
    svc.rename_files(m, str(dest), action_type="copy", asset_folders=True, dry_run=False)
    assert (dest / "Show (2020)" / "poster.jpg").read_bytes() == b"AAA"


def test_placement_places_artwork_alongside_poster(test_db, tmp_path):
    # The unified proof: one rename_files call places the poster AND the item's logo/background/
    # square through the same loop — they are slots on ONE matched box, not a second lookup.
    from util.data.construct import build_slots
    from util.data.normalization import normalize_titles

    src = tmp_path / "src"; _seed(src, ["Inception (2010).jpg"])
    art = tmp_path / "art"; _seed(art, ["logo.png", "background.jpg", "square.jpg"])
    poster = str(src / "Inception (2010).jpg")
    box = {
        "title": "Inception", "year": 2010, "tmdb_id": 27205, "tvdb_id": None, "imdb_id": None,
        "normalized_title": normalize_titles("Inception"), "type": "movies",
        "slots": build_slots(poster=poster, logo=str(art / "logo.png"),
                             background=str(art / "background.jpg"), square=str(art / "square.jpg")),
    }
    m = _matched(movies=[{"title": "Inception", "year": 2010, "tmdb_id": 27205,
                          "folder": "Inception (2010)", "files": [poster], "asset_ref": box}])
    svc = PosterRenameService(test_db)
    dest = tmp_path / "dest"; dest.mkdir()
    svc.rename_files(m, str(dest), action_type="copy", asset_folders=True, dry_run=False)

    item = dest / "Inception (2010)"
    assert sorted(p.name for p in item.iterdir()) == ["background.jpg", "logo.png", "poster.jpg", "square.jpg"]


def test_placement_places_artwork_without_a_poster(test_db, tmp_path):
    # "Place artwork anyway": an item with artwork but NO poster still gets its logo/square.
    # Now it needs no special case — an artwork-only box simply matches as itself.
    from util.data.construct import build_slots
    from util.data.normalization import normalize_titles

    art = tmp_path / "art"; _seed(art, ["logo.png", "square.jpg"])
    files = [str(art / "logo.png"), str(art / "square.jpg")]
    box = {
        "title": "Loki", "year": 2021, "tmdb_id": None, "tvdb_id": 84958, "imdb_id": None,
        "normalized_title": normalize_titles("Loki"), "type": None,
        "slots": build_slots(logo=str(art / "logo.png"), square=str(art / "square.jpg")),
        "files": files,
    }
    matched = _matched(series=[{"title": "Loki", "year": 2021, "tvdb_id": 84958,
                                "folder": "Loki (2021)", "files": files, "asset_ref": box}])
    svc = PosterRenameService(test_db)
    dest = tmp_path / "dest"; dest.mkdir()
    svc.rename_files(matched, str(dest), action_type="copy", asset_folders=True, dry_run=False)

    item = dest / "Loki (2021)"
    assert sorted(p.name for p in item.iterdir()) == ["logo.png", "square.jpg"]


def test_rename_posters_places_poster_and_artwork_and_counts(test_db, tmp_path, monkeypatch):
    # End-to-end poster pass: one rename_posters run places the poster AND the item's artwork
    # off a single matched box, and reports the artwork tally in stats.
    from util.data.construct import build_slots
    from util.data.normalization import normalize_titles

    posrc = tmp_path / "Movie One.jpg"; posrc.write_bytes(b"p")
    art = tmp_path / "art"; _seed(art, ["logo.png", "square.jpg"])
    box = {"title": "Movie One", "year": 2024, "tmdb_id": 123, "tvdb_id": None, "imdb_id": None,
           "normalized_title": normalize_titles("Movie One"), "type": "movies",
           "files": [str(posrc)],
           "slots": build_slots(poster=str(posrc), logo=str(art / "logo.png"), square=str(art / "square.jpg"))}
    matched = {
        "collections": [], "series": [],
        "movies": [{"title": "Movie One", "year": 2024, "tmdb_id": 123,
                    "folder": "Movie One (2024)", "files": [str(posrc)], "asset_ref": box}],
    }
    media = {"movies": [{"title": "Movie One", "year": 2024, "tmdb_id": 123, "folder": "Movie One (2024)"}],
             "series": [], "collections": []}

    service = PosterRenameService(test_db)
    monkeypatch.setattr("services.poster_renamer.get_assets_files", lambda source_dirs, per_dir_callback=None: ([{"title": "Movie One", "files": [str(posrc)]}], {"m": []}))
    monkeypatch.setattr("services.poster_renamer.match_assets_to_media", lambda *a, **k: matched)

    dest = tmp_path / "dest"; dest.mkdir()
    result = service.rename_posters(source_dirs=["/x"], destination_dir=str(dest), asset_folders=True,
                                    dry_run=False, media_dict=media, artwork_boxes=[box])

    item = dest / "Movie One (2024)"
    assert (item / "poster.jpg").is_file()
    assert (item / "logo.png").is_file()
    assert (item / "square.jpg").is_file()
    assert result["success"] is True
    assert result["stats"]["artwork"] == 2
    # Artwork stays OUT of poster-only stats: one poster matched, and style counts (which feed
    # community-list reconcile) tally only the poster, not the 2 artwork files.
    assert result["stats"]["total_matched"] == 1
    assert sum(result["stats"]["style_counts"].values()) == 1


def test_style_counts_attribute_by_drive_folder_not_index(test_db, tmp_path, monkeypatch):
    """Style attribution comes from which drive folder the winning file sits under — it must
    work with ZERO rows in the poster index. (The old index join binned a whole drive's
    winners as 'Unknown' whenever its rows were missing/stale, and Unknown never renders in
    the style lists, so still-subscribed drives' items vanished from the report.)"""
    from models.drive import Drive
    from util.data.normalization import normalize_titles

    mm_root = tmp_path / "mm"; cl_root = tmp_path / "cl"
    mm = _seed(mm_root, ["Movie One (2024).jpg"])
    cl = _seed(cl_root, ["Movie Two (2023).jpg"])
    test_db.add_all([
        Drive(name="MM Drive", drive_id="mm-1", style_type="MM2K", subscribed=True, custom_path=str(mm_root)),
        Drive(name="CL Drive", drive_id="cl-1", style_type="CL2K", subscribed=True, custom_path=str(cl_root)),
    ])
    test_db.commit()
    # Deliberately NO Poster index rows.

    def _box(title, year, tmdb_id, path):
        return {"title": title, "year": year, "tmdb_id": tmdb_id, "tvdb_id": None, "imdb_id": None,
                "normalized_title": normalize_titles(title), "type": "movies", "files": [path]}

    matched = _matched(movies=[
        {"title": "Movie One", "year": 2024, "tmdb_id": 1, "folder": "Movie One (2024)",
         "files": [mm["Movie One (2024).jpg"]], "asset_ref": _box("Movie One", 2024, 1, mm["Movie One (2024).jpg"])},
        {"title": "Movie Two", "year": 2023, "tmdb_id": 2, "folder": "Movie Two (2023)",
         "files": [cl["Movie Two (2023).jpg"]], "asset_ref": _box("Movie Two", 2023, 2, cl["Movie Two (2023).jpg"])},
    ])
    media = {"movies": [{"title": "Movie One", "year": 2024, "tmdb_id": 1, "folder": "Movie One (2024)"},
                        {"title": "Movie Two", "year": 2023, "tmdb_id": 2, "folder": "Movie Two (2023)"}],
             "series": [], "collections": []}

    monkeypatch.setattr(
        "services.poster_renamer.get_assets_files",
        lambda source_dirs, per_dir_callback=None: (
            [{"title": "Movie One", "files": [mm["Movie One (2024).jpg"]]},
             {"title": "Movie Two", "files": [cl["Movie Two (2023).jpg"]]}],
            {"m": []},
        ),
    )
    monkeypatch.setattr("services.poster_renamer.match_assets_to_media", lambda *a, **k: matched)

    dest = tmp_path / "dest"; dest.mkdir()
    result = PosterRenameService(test_db).rename_posters(
        source_dirs=[str(mm_root), str(cl_root)], destination_dir=str(dest),
        asset_folders=True, dry_run=False, media_dict=media,
    )

    assert result["success"] is True
    assert result["stats"]["style_counts"] == {"MM2K": 1, "CL2K": 1}
    fallback_titles = {item["title"] for item in result["stats"]["style_fallbacks"]["MM2K"]}
    assert fallback_titles == {"Movie One"}
    usage = {u["drive_id"]: u for u in result["stats"]["drive_usage"]}
    assert usage["mm-1"]["count"] == 1 and usage["mm-1"]["name"] == "MM Drive" and usage["mm-1"]["style"] == "MM2K"
    assert usage["cl-1"]["count"] == 1 and usage["cl-1"]["style"] == "CL2K"
    assert result["stats"]["style_fallbacks"]["MM2K"][0]["drive_id"] == "mm-1"


def test_merge_slots_records_poster_and_season_runners():
    """Slot losers land in the box's slot_runners (poster + seasons only), winners in slots."""
    from util.posters.assets import merge_slots

    prio = {"/drives/A": 0, "/drives/B": 1}
    final = {"slots": {"poster": "/drives/B/x/Movie.jpg", "seasons": {1: "/drives/B/x/S1.jpg"}}}
    new = {"slots": {"poster": "/drives/A/x/Movie.jpg", "seasons": {1: "/drives/A/x/S1.jpg", 2: "/drives/A/x/S2.jpg"}}}
    merge_slots(final, new, prio)  # incoming outranks current

    assert final["slots"]["poster"] == "/drives/A/x/Movie.jpg"
    assert final["slots"]["seasons"] == {1: "/drives/A/x/S1.jpg", 2: "/drives/A/x/S2.jpg"}
    assert final["slot_runners"]["poster"] == ["/drives/B/x/Movie.jpg"]
    assert final["slot_runners"]["seasons"] == {1: ["/drives/B/x/S1.jpg"]}

    # current outranks incoming -> the incoming file is the runner
    merge_slots(final, {"slots": {"poster": "/drives/B/y/Movie.jpg", "seasons": {}}}, prio)
    assert final["slots"]["poster"] == "/drives/A/x/Movie.jpg"
    assert final["slot_runners"]["poster"] == ["/drives/B/x/Movie.jpg", "/drives/B/y/Movie.jpg"]


def test_merge_slots_records_artwork_slot_runners():
    """Artwork slot losers are recorded too — they feed the artwork drive-usage report."""
    from util.posters.assets import merge_slots

    prio = {"/drives/A": 0, "/drives/B": 1}
    final = {"slots": {"poster": None, "logo": "/drives/B/x/logos/T.png", "seasons": {}}}
    new = {"slots": {"poster": None, "logo": "/drives/A/x/logos/T.png",
                     "background": "/drives/A/x/backgrounds/T.jpg", "seasons": {}}}
    merge_slots(final, new, prio)

    assert final["slots"]["logo"] == "/drives/A/x/logos/T.png"
    assert final["slots"]["background"] == "/drives/A/x/backgrounds/T.jpg"
    assert final["slot_runners"]["logo"] == ["/drives/B/x/logos/T.png"]
    assert "background" not in final["slot_runners"]  # no competition -> no runner


def test_build_artwork_drive_usage(test_db, tmp_path):
    """Artwork winners/losers aggregate per artwork drive, with slot-tagged item lists."""
    from models.artwork_drive import ArtworkDrive
    from services.poster_renamer import build_artwork_drive_usage

    a_root = tmp_path / "art-a"; b_root = tmp_path / "art-b"
    a_root.mkdir(); b_root.mkdir()
    test_db.add_all([
        ArtworkDrive(name="Art A", drive_id="art-a", subscribed=True, custom_path=str(a_root)),
        ArtworkDrive(name="Art B", drive_id="art-b", subscribed=True, custom_path=str(b_root)),
    ])
    test_db.commit()

    win_logo = str(a_root / "Item" / "logos" / "Movie One (2024).png")
    lost_logo = str(b_root / "Item" / "logos" / "Movie One (2024).png")
    win_bg = str(b_root / "Item" / "backgrounds" / "Movie One (2024).jpg")
    artwork_renamed = {
        "winning_files": {
            "/dest/Movie One (2024)/logo.png": (win_logo, "Movie One", 2024, "movie", "logo", 1, None, None, None, None),
            "/dest/Movie One (2024)/background.jpg": (win_bg, "Movie One", 2024, "movie", "background", 1, None, None, None, None),
        },
        "outranked_files": [("/dest/Movie One (2024)/logo.png", lost_logo)],
    }

    result = build_artwork_drive_usage(test_db, artwork_renamed)
    usage = {u["drive_id"]: u for u in result["artwork_drive_usage"]}
    assert usage["art-a"]["count"] == 1 and usage["art-a"]["outranked"] == 0
    assert usage["art-b"]["count"] == 1 and usage["art-b"]["outranked"] == 1
    assert [i["slot"] for i in result["artwork_drive_items"]["art-a"]] == ["logo"]
    assert [i["slot"] for i in result["artwork_drive_items"]["art-b"]] == ["background"]
    assert [(i["title"], i["slot"]) for i in result["artwork_drive_outranked"]["art-b"]] == [("Movie One", "logo")]


def test_drive_usage_counts_outranked_matches(test_db, tmp_path, monkeypatch):
    """A lower-priority drive whose file matched but lost its slot to a higher-priority
    drive shows as 'outranked' in drive_usage, without winning anything itself."""
    from models.drive import Drive
    from util.data.normalization import normalize_titles

    mm_root = tmp_path / "mm"; cl_root = tmp_path / "cl"
    mm = _seed(mm_root, ["Movie One (2024).jpg"])
    cl = _seed(cl_root, ["Movie One (2024).jpg"])
    test_db.add_all([
        Drive(name="MM Drive", drive_id="mm-1", style_type="MM2K", subscribed=True, custom_path=str(mm_root)),
        Drive(name="CL Drive", drive_id="cl-1", style_type="CL2K", subscribed=True, custom_path=str(cl_root)),
    ])
    test_db.commit()

    win = mm["Movie One (2024).jpg"]; lost = cl["Movie One (2024).jpg"]
    box = {"title": "Movie One", "year": 2024, "tmdb_id": 1, "tvdb_id": None, "imdb_id": None,
           "normalized_title": normalize_titles("Movie One"), "type": "movies", "files": [win],
           "slots": {"poster": win, "seasons": {}},
           "slot_runners": {"poster": [lost]}}
    matched = _matched(movies=[{
        "title": "Movie One", "year": 2024, "tmdb_id": 1, "folder": "Movie One (2024)",
        "files": [win], "asset_ref": box,
    }])
    media = {"movies": [{"title": "Movie One", "year": 2024, "tmdb_id": 1, "folder": "Movie One (2024)"}],
             "series": [], "collections": []}

    monkeypatch.setattr(
        "services.poster_renamer.get_assets_files",
        lambda source_dirs, per_dir_callback=None: ([{"title": "Movie One", "files": [win]}], {"m": []}),
    )
    monkeypatch.setattr("services.poster_renamer.match_assets_to_media", lambda *a, **k: matched)

    dest = tmp_path / "dest"; dest.mkdir()
    result = PosterRenameService(test_db).rename_posters(
        source_dirs=[str(mm_root), str(cl_root)], destination_dir=str(dest),
        asset_folders=True, dry_run=False, media_dict=media,
    )

    assert result["success"] is True
    usage = {u["drive_id"]: u for u in result["stats"]["drive_usage"]}
    assert usage["mm-1"]["count"] == 1 and usage["mm-1"]["outranked"] == 0
    assert usage["cl-1"]["count"] == 0 and usage["cl-1"]["outranked"] == 1
    # Winners sort first even when another drive has more outranked matches.
    assert result["stats"]["drive_usage"][0]["drive_id"] == "mm-1"
    # The outranked item list names the media the loser matched (via the winning entry).
    outranked_items = result["stats"]["drive_outranked"]["cl-1"]
    assert [(i["title"], i["year"], i["type"]) for i in outranked_items] == [("Movie One", 2024, "movie")]
    assert "mm-1" not in result["stats"]["drive_outranked"]


def test_mark_processed_stamps_rows_by_drive_and_name_despite_stale_path(test_db, tmp_path, monkeypatch):
    """Processed-marking joins rows by (drive, file name) — the sync engine's row identity —
    so a row whose STORED path drifted from the scan's spelling (symlinks, moved storage)
    still gets stamped instead of staying 'unprocessed' forever."""
    from models.drive import Drive
    from models.poster import Poster
    from util.data.normalization import normalize_titles

    mm_root = tmp_path / "mm"
    mm = _seed(mm_root, ["Movie One (2024).jpg"])
    drive = Drive(name="MM Drive", drive_id="mm-1", style_type="MM2K", subscribed=True, custom_path=str(mm_root))
    row = Poster(drive_id="mm-1", file_name="Movie One (2024).jpg",
                 file_path="/old/stale/spelling/Movie One (2024).jpg")  # old exact-path lookup would miss
    test_db.add_all([drive, row])
    test_db.commit()

    path = mm["Movie One (2024).jpg"]
    matched = _matched(movies=[{
        "title": "Movie One", "year": 2024, "tmdb_id": 1, "folder": "Movie One (2024)",
        "files": [path],
        "asset_ref": {"title": "Movie One", "year": 2024, "tmdb_id": 1, "tvdb_id": None, "imdb_id": None,
                      "normalized_title": normalize_titles("Movie One"), "type": "movies", "files": [path]},
    }])
    media = {"movies": [{"title": "Movie One", "year": 2024, "tmdb_id": 1, "folder": "Movie One (2024)"}],
             "series": [], "collections": []}

    monkeypatch.setattr(
        "services.poster_renamer.get_assets_files",
        lambda source_dirs, per_dir_callback=None: ([{"title": "Movie One", "files": [path]}], {"m": []}),
    )
    monkeypatch.setattr("services.poster_renamer.match_assets_to_media", lambda *a, **k: matched)

    dest = tmp_path / "dest"; dest.mkdir()
    result = PosterRenameService(test_db).rename_posters(
        source_dirs=[str(mm_root)], destination_dir=str(dest),
        asset_folders=True, dry_run=False, media_dict=media,
    )

    assert result["success"] is True
    test_db.expire_all()
    assert test_db.query(Poster).filter(Poster.drive_id == "mm-1").first().last_processed is not None
    assert test_db.query(Drive).filter(Drive.drive_id == "mm-1").first().last_rename_processed is not None


def test_style_counts_unknown_for_file_outside_any_drive(test_db, tmp_path, monkeypatch):
    """A winner not under any known drive folder still lands in 'Unknown' rather than crashing."""
    from util.data.normalization import normalize_titles

    stray = _seed(tmp_path / "stray", ["Movie One (2024).jpg"])
    path = stray["Movie One (2024).jpg"]
    matched = _matched(movies=[{
        "title": "Movie One", "year": 2024, "tmdb_id": 1, "folder": "Movie One (2024)",
        "files": [path],
        "asset_ref": {"title": "Movie One", "year": 2024, "tmdb_id": 1, "tvdb_id": None, "imdb_id": None,
                      "normalized_title": normalize_titles("Movie One"), "type": "movies", "files": [path]},
    }])
    media = {"movies": [{"title": "Movie One", "year": 2024, "tmdb_id": 1, "folder": "Movie One (2024)"}],
             "series": [], "collections": []}

    monkeypatch.setattr(
        "services.poster_renamer.get_assets_files",
        lambda source_dirs, per_dir_callback=None: ([{"title": "Movie One", "files": [path]}], {"m": []}),
    )
    monkeypatch.setattr("services.poster_renamer.match_assets_to_media", lambda *a, **k: matched)

    dest = tmp_path / "dest"; dest.mkdir()
    result = PosterRenameService(test_db).rename_posters(
        source_dirs=[str(tmp_path / "stray")], destination_dir=str(dest),
        asset_folders=True, dry_run=False, media_dict=media,
    )

    assert result["stats"]["style_counts"] == {"Unknown": 1}
    assert result["stats"]["drive_usage"] == []


def test_artwork_bypasses_tmp_staging(test_db, tmp_path, monkeypatch):
    """With tmp/ staging on, the poster is staged in tmp/ but artwork goes straight to the real
    destination — so the border replacer (which only processes tmp/) never sees the artwork."""
    from util.data.construct import build_slots
    from util.data.normalization import normalize_titles

    posrc = tmp_path / "Movie One.jpg"; posrc.write_bytes(b"p")
    art = tmp_path / "art"; _seed(art, ["logo.png"])
    box = {"title": "Movie One", "year": 2024, "tmdb_id": 123, "tvdb_id": None, "imdb_id": None,
           "normalized_title": normalize_titles("Movie One"), "type": "movies",
           "files": [str(posrc)],
           "slots": build_slots(poster=str(posrc), logo=str(art / "logo.png"))}
    matched = {"collections": [], "series": [],
               "movies": [{"title": "Movie One", "year": 2024, "tmdb_id": 123,
                           "folder": "Movie One (2024)", "files": [str(posrc)], "asset_ref": box}]}
    media = {"movies": [{"title": "Movie One", "year": 2024, "tmdb_id": 123, "folder": "Movie One (2024)"}], "series": [], "collections": []}

    service = PosterRenameService(test_db)
    monkeypatch.setattr("services.poster_renamer.get_assets_files", lambda source_dirs, per_dir_callback=None: ([{"title": "Movie One", "files": [str(posrc)]}], {"m": []}))
    monkeypatch.setattr("services.poster_renamer.match_assets_to_media", lambda *a, **k: matched)

    dest = tmp_path / "dest"; dest.mkdir()
    service.rename_posters(source_dirs=["/x"], destination_dir=str(dest), asset_folders=True,
                           dry_run=False, use_temp_folder=True, media_dict=media, artwork_boxes=[box])

    item = "Movie One (2024)"
    assert (dest / "tmp" / item / "poster.jpg").is_file()   # poster staged in tmp/
    assert (dest / item / "logo.png").is_file()             # artwork in the REAL dest
    assert not (dest / "tmp" / item / "logo.png").exists()  # never staged in tmp/


def test_missing_destination_is_created_with_tmp_staging(test_db, tmp_path, monkeypatch):
    """A first run has no destination yet, and tmp/ staging is always on for the poster pass —
    so it must create the destination, not refuse (Detect tells users to rename first)."""
    posrc = tmp_path / "Movie One.jpg"; posrc.write_bytes(b"p")
    matched = {"collections": [], "series": [],
               "movies": [{"title": "Movie One", "year": 2024, "tmdb_id": 123,
                           "folder": "Movie One (2024)", "files": [str(posrc)]}]}
    media = {"movies": [{"title": "Movie One", "year": 2024, "tmdb_id": 123, "folder": "Movie One (2024)"}],
             "series": [], "collections": []}

    monkeypatch.setattr("services.poster_renamer.get_assets_files", lambda source_dirs, per_dir_callback=None: ([{"title": "Movie One", "files": [str(posrc)]}], {"m": []}))
    monkeypatch.setattr("services.poster_renamer.match_assets_to_media", lambda *a, **k: matched)

    dest = tmp_path / "config" / "posters" / "assets"  # nothing in this chain exists
    result = PosterRenameService(test_db).rename_posters(
        source_dirs=["/x"], destination_dir=str(dest), asset_folders=True,
        dry_run=False, use_temp_folder=True, media_dict=media,
    )

    assert result["success"]
    assert (dest / "tmp" / "Movie One (2024)" / "poster.jpg").is_file()


def test_placement_without_artwork_index_is_poster_only(test_db, tmp_path):
    # No artwork index -> unchanged poster-only behavior (the default path).
    src = tmp_path / "src"; f = _seed(src, ["Inception (2010).jpg"])
    m = _matched(movies=[{"title": "Inception", "year": 2010, "folder": "Inception (2010)", "files": [f["Inception (2010).jpg"]]}])
    svc = PosterRenameService(test_db)
    dest = tmp_path / "dest"; dest.mkdir()
    svc.rename_files(m, str(dest), action_type="copy", asset_folders=True, dry_run=False)
    item = dest / "Inception (2010)"
    assert [p.name for p in item.iterdir()] == ["poster.jpg"]


class _FakeTmdbResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_enrich_collections_with_tmdb_matches_and_skips(test_db, monkeypatch):
    test_db.add(Setting(key="tmdb_api_key", value="fake-key"))
    test_db.commit()

    service = PosterRenameService(test_db)

    calls = []

    def fake_get(url, params=None, timeout=None):
        query = (params or {}).get("query", "")
        calls.append(query)
        if "john wick" in query.lower():
            return _FakeTmdbResponse(
                {"results": [{"id": 404, "name": "John Wick Collection", "poster_path": "/jw.jpg"}]}
            )
        return _FakeTmdbResponse({"results": []})

    monkeypatch.setattr("services.poster_renamer.requests.get", fake_get)

    media_dict = {
        "movies": [],
        "series": [],
        "collections": [
            {"type": "collections", "title": "John Wick", "tmdb_id": None},
            {"type": "collections", "title": "My Favorites", "tmdb_id": None},
            {"type": "collections", "title": "Already Linked", "tmdb_id": 999},
        ],
    }

    service._enrich_collections_with_tmdb(media_dict)

    matched, custom, preset = media_dict["collections"]
    # The id lands on tmdb_id_ref (display-only), NOT tmdb_id, so the poster
    # matcher — which branches on media["tmdb_id"] — is left untouched.
    assert matched["tmdb_id_ref"] == 404
    assert not matched.get("tmdb_id")
    assert matched["poster_url"] == "https://image.tmdb.org/t/p/w185/jw.jpg"
    assert not custom.get("tmdb_id_ref")
    assert not custom.get("tmdb_id")
    assert not custom.get("poster_url")
    # A collection that already had an id is not re-queried.
    assert preset["tmdb_id"] == 999
    assert "Already Linked" not in calls

    # Both the positive and negative result are cached for the next run.
    cache_setting = get_setting(test_db, "poster_collection_tmdb_cache")
    assert cache_setting is not None and cache_setting.value


def test_enrich_collections_with_tmdb_noops_without_api_key(test_db, monkeypatch):
    service = PosterRenameService(test_db)

    def fail_get(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("TMDB should not be queried without an API key")

    monkeypatch.setattr("services.poster_renamer.requests.get", fail_get)

    media_dict = {
        "movies": [],
        "series": [],
        "collections": [{"type": "collections", "title": "John Wick", "tmdb_id": None}],
    }
    service._enrich_collections_with_tmdb(media_dict)
    assert not media_dict["collections"][0].get("tmdb_id")
    assert not media_dict["collections"][0].get("tmdb_id_ref")


def test_rename_posters_fails_when_no_assets_found(test_db, monkeypatch):
    service = PosterRenameService(test_db)

    monkeypatch.setattr("services.poster_renamer.get_assets_files", lambda source_dirs, per_dir_callback=None: ([], {}))

    result = service.rename_posters(
        source_dirs=["/tmp/source"],
        destination_dir="/tmp/dest",
        dry_run=True,
    )

    assert result["success"] is False
    assert result["error"] == "No assets found in the source directories"


def test_rename_posters_fails_when_no_media_found(test_db, monkeypatch):
    service = PosterRenameService(test_db)

    assets = [
        {
            "title": "Movie One",
            "year": 2024,
            "files": ["/tmp/source/Movie One.jpg"],
            "folder": "Movie One (2024)",
        }
    ]

    monkeypatch.setattr("services.poster_renamer.get_assets_files", lambda source_dirs, per_dir_callback=None: (assets, {"m": assets}))
    monkeypatch.setattr(service, "get_media_from_instances", lambda **kwargs: {"movies": [], "series": [], "collections": []})

    result = service.rename_posters(
        source_dirs=["/tmp/source"],
        destination_dir="/tmp/dest",
        dry_run=True,
    )

    assert result["success"] is False
    assert "No media found" in result["error"]


def test_rename_posters_fails_when_no_assets_match_media(test_db, monkeypatch):
    service = PosterRenameService(test_db)

    assets = [
        {
            "title": "Movie One",
            "year": 2024,
            "files": ["/tmp/source/Movie One.jpg"],
            "folder": "Movie One (2024)",
        }
    ]
    media = {
        "movies": [{"title": "Different Movie", "year": 2024, "folder": "Different Movie (2024)", "tmdb_id": 123}],
        "series": [],
        "collections": [],
    }

    monkeypatch.setattr("services.poster_renamer.get_assets_files", lambda source_dirs, per_dir_callback=None: (assets, {"m": assets}))
    monkeypatch.setattr(service, "get_media_from_instances", lambda **kwargs: media)
    monkeypatch.setattr(
        "services.poster_renamer.match_assets_to_media",
        lambda media_dict, prefix_index, strict_folder_match=False, **k: {"movies": [], "series": [], "collections": []},
    )

    result = service.rename_posters(
        source_dirs=["/tmp/source"],
        destination_dir="/tmp/dest",
        dry_run=True,
    )

    assert result["success"] is False
    assert "No assets matched to media" in result["error"]


def test_rename_posters_successful_flow_returns_stats(test_db, monkeypatch):
    service = PosterRenameService(test_db)

    assets = [
        {
            "title": "Movie One",
            "year": 2024,
            "files": ["/tmp/source/Movie One.jpg"],
            "folder": "Movie One (2024)",
        }
    ]
    media = {
        "movies": [{"title": "Movie One", "year": 2024, "folder": "Movie One (2024)", "tmdb_id": 123}],
        "series": [],
        "collections": [],
    }
    matched_assets = {
        "movies": [
            {
                "title": "Movie One",
                "year": 2024,
                "folder": "Movie One (2024)",
                "files": ["/tmp/source/Movie One.jpg"],
            }
        ],
        "series": [],
        "collections": [],
    }

    monkeypatch.setattr("services.poster_renamer.get_assets_files", lambda source_dirs, per_dir_callback=None: (assets, {"m": assets}))
    monkeypatch.setattr(service, "get_media_from_instances", lambda **kwargs: media)
    monkeypatch.setattr(
        "services.poster_renamer.match_assets_to_media",
        lambda media_dict, prefix_index, strict_folder_match=False, **k: matched_assets,
    )
    monkeypatch.setattr(
        service,
        "rename_files",
        lambda matched_assets, destination_dir, action_type, asset_folders, dry_run, progress_callback=None, artwork_index=None, artwork_destination=None, artwork_slot_filter=None: (
            {"movies": [{"title": "Movie One", "year": 2024, "folder": "Movie One (2024)", "messages": ["renamed"]}], "series": [], "collections": []},
            ["/tmp/dest/Movie One (2024)/poster.jpg"],
            ["/tmp/source/Movie One.jpg"],
            {"/tmp/dest/Movie One (2024)/poster.jpg": ("/tmp/source/Movie One.jpg", "Movie One", 2024, "movie", None, 27205, None, "tt0000001", "https://image.tmdb.org/t/p/original/x.jpg", True)},
            [],  # outranked_source_files
            {"total": 0, "by_media": {"movies": 0, "series": 0, "collections": 0},
             "by_type": {"logo": 0, "background": 0, "squareart": 0}},  # artwork written this run
        ),
    )

    result = service.rename_posters(
        source_dirs=["/tmp/source"],
        destination_dir="/tmp/dest",
        dry_run=True,
    )

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["stats"]["total_assets"] == 1
    assert result["stats"]["total_media"] == 1
    assert result["stats"]["total_matched"] == 1
    assert result["stats"]["movies"] == 1


def test_filter_assets_for_target_prefers_matching_ids_and_title(test_db):
    service = PosterRenameService(test_db)

    assets = [
        {
            "type": "movies",
            "title": "Movie One",
            "year": 2024,
            "normalized_title": "movieone2024",
            "tmdb_id": 101,
            "imdb_id": "tt0101",
            "files": ["/tmp/source/Movie One.jpg"],
        },
        {
            "type": "movies",
            "title": "Movie Two",
            "year": 2024,
            "normalized_title": "movietwo2024",
            "tmdb_id": 202,
            "imdb_id": "tt0202",
            "files": ["/tmp/source/Movie Two.jpg"],
        },
    ]

    filtered_assets, _filtered_index = service._filter_assets_for_target(
        assets,
        target_media_type="movie",
        target_title="Movie One",
        target_year=2024,
        target_tmdb_id=101,
        target_tvdb_id=None,
        target_imdb_id="tt0101",
        target_season_number=None,
    )

    assert len(filtered_assets) == 1
    assert filtered_assets[0]["title"] == "Movie One"


def test_filter_assets_for_target_respects_series_season_scope(test_db):
    service = PosterRenameService(test_db)

    assets = [
        {
            "type": "series",
            "title": "The Show",
            "year": 2023,
            "normalized_title": "theshow2023",
            "tvdb_id": 300,
            "season_numbers": [1, 2],
            "files": ["/tmp/source/The Show/Season01.jpg", "/tmp/source/The Show/Season02.jpg"],
        },
        {
            "type": "series",
            "title": "Other Show",
            "year": 2023,
            "normalized_title": "othershow2023",
            "tvdb_id": 301,
            "season_numbers": [1],
            "files": ["/tmp/source/Other Show/Season01.jpg"],
        },
    ]

    filtered_assets, _filtered_index = service._filter_assets_for_target(
        assets,
        target_media_type="series",
        target_title="The Show",
        target_year=2023,
        target_tmdb_id=None,
        target_tvdb_id=300,
        target_imdb_id=None,
        target_season_number=2,
    )

    assert len(filtered_assets) == 1
    assert filtered_assets[0]["title"] == "The Show"


def test_artwork_stat_counts_files_written_not_matched_slots(test_db, tmp_path):
    """A re-run where everything is already in place must report 0 artwork placed — the stat
    used to sum resolved slots, so a no-op run claimed it had placed the whole library."""
    from util.data.construct import build_slots
    from util.data.normalization import normalize_titles

    art = tmp_path / "art"; _seed(art, ["logo.png", "square.jpg"])
    files = [str(art / "logo.png"), str(art / "square.jpg")]
    box = {
        "title": "Loki", "year": 2021, "tmdb_id": None, "tvdb_id": 84958, "imdb_id": None,
        "normalized_title": normalize_titles("Loki"), "type": None, "files": files,
        "slots": build_slots(logo=str(art / "logo.png"), square=str(art / "square.jpg")),
    }
    def _artwork_matched():
        return _matched(series=[{"title": "Loki", "year": 2021, "tvdb_id": 84958,
                                 "folder": "Loki (2021)", "files": files, "asset_ref": box}])

    svc = PosterRenameService(test_db)
    dest = tmp_path / "dest"; dest.mkdir()

    *_, first_written = svc.rename_files(_artwork_matched(), str(dest), action_type="copy", asset_folders=True,
                                         dry_run=False)
    # Second run over the same destination: the files are already in place.
    *_, second_written = svc.rename_files(_artwork_matched(), str(dest), action_type="copy", asset_folders=True,
                                          dry_run=False)

    assert first_written["total"] == 2, "first run actually writes the logo + square"
    assert second_written["total"] == 0, "a no-op re-run must not report artwork as newly placed"

    # Broken down like the poster counts (by media type) plus by artwork type.
    assert first_written["by_media"] == {"movies": 0, "series": 2, "collections": 0}
    assert first_written["by_type"] == {"logo": 1, "background": 0, "squareart": 1}
