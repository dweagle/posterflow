"""Overlays linked to a holiday or a border rule: the border pass notes which posters got one,
and the engine draws the linked overlay on those posters in place of the type's normal one."""

import io
import json
from pathlib import Path

from PIL import Image

from models.poster_border import PosterBorder
from models.setting import Setting
from services.border_replacer import BorderReplacerService
from services.overlays.base import ItemContext
from services.overlays.config import normalize_config, overlay_signature
from services.overlays.registry import get_type
from services.overlays.server import ServerOverlayEngine
from services.plex_border_rules import PlexBorderRule, PlexRuleMatcher, folder_match_keys
from services.poster_borders import BorderNotes, border_choices, border_of, normalize_border_keys
from test_overlays import FakeClient, FakeMetadata, _engine, _sources, _state_items

ALWAYS = "range(01/01-12/31)"


def _poster(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1000, 1500), (255, 255, 255)).save(path)


def _holiday(db, name="Winter", schedule=ALWAYS, colors=("#FF0000",)):
    row = db.query(Setting).filter(Setting.key == "border_replacer_holidays").first()
    value = json.dumps([{"name": name, "schedule": schedule, "colors": list(colors)}] if name else [])
    if row is None:
        db.add(Setting(key="border_replacer_holidays", value=value))
    else:
        row.value = value
    db.commit()


def _rule(name, folder, mode="include"):
    return PlexBorderRule(name=name, match="label", value="X", mode=mode, colors=["#00FF00"],
                          style_opts={"style": "solid"}, match_keys=folder_match_keys(folder))


def _notes(db) -> dict:
    return {Path(row.file_path).parent.name + "/" + Path(row.file_path).name: row.border for row in db.query(PosterBorder)}


def _run(db, tmp_path, **kwargs):
    args = {"border_colors": ["#0000FF"], "border_width": 26, "exclusion_list": [], "dry_run": False, "mode": "full", **kwargs}
    return BorderReplacerService(db).process_posters(source_dir=str(tmp_path / "tmp"), destination_dir=str(tmp_path / "dest"), **args)


# ── the border pass notes where each poster's border came from ───────────────

def test_border_pass_notes_holiday_and_rule_borders(test_db, tmp_path):
    for name in ("Elf (2003)/poster.png", "Elf (2003)/Season01.png", "Heat (1995)/poster.png", "Jaws (1975)/poster.png"):
        _poster(tmp_path / "tmp" / name)
    assert _run(test_db, tmp_path)["success"] is True
    assert _notes(test_db) == {}  # no holiday, no rule: nothing to note

    _holiday(test_db)
    matcher = PlexRuleMatcher([_rule("Classics", "Heat (1995)"), _rule("Hands off", "Jaws (1975)", mode="skip")])
    _run(test_db, tmp_path, season_mode="remove", plex_matcher=matcher)
    assert _notes(test_db) == {
        "Elf (2003)/poster.png": "holiday:Winter",
        "Heat (1995)/poster.png": "rule:Classics",   # a rule goes before the holiday
        "Jaws (1975)/poster.png": "rule:Hands off",  # left untouched by its rule
    }  # the season poster has its own border mode, so the holiday is not on it
    assert border_of(test_db, str(tmp_path / "dest" / "Elf (2003)" / "poster.png")) == "holiday:Winter"
    assert border_of(test_db, str(tmp_path / "dest" / "Elf (2003)" / "Season01.png")) == ""

    _run(test_db, tmp_path, plex_matcher=matcher)  # seasons follow the main border: the holiday is on them too
    assert _notes(test_db)["Elf (2003)/Season01.png"] == "holiday:Winter"

    _run(test_db, tmp_path, remove_borders=True, plex_matcher=None)  # Remove Borders: no holiday border is applied
    assert _notes(test_db) == {}


def test_border_pass_dry_run_and_gone_posters(test_db, tmp_path):
    _poster(tmp_path / "tmp" / "Elf (2003)" / "poster.png")
    _poster(tmp_path / "tmp" / "Heat (1995)" / "poster.png")
    _holiday(test_db)
    _run(test_db, tmp_path, dry_run=True)
    assert _notes(test_db) == {}
    _run(test_db, tmp_path)
    assert set(_notes(test_db)) == {"Elf (2003)/poster.png", "Heat (1995)/poster.png"}
    (tmp_path / "tmp" / "Heat (1995)" / "poster.png").unlink()
    _run(test_db, tmp_path)
    assert set(_notes(test_db)) == {"Elf (2003)/poster.png"}  # a poster no longer placed loses its note


def test_incremental_pass_notes_posters_it_leaves_as_they_are(test_db, tmp_path):
    """Posters bordered before the notes existed (or under a holiday or rule since renamed) get
    their note on the next pass, though nothing is processed again."""
    _poster(tmp_path / "tmp" / "Elf (2003)" / "poster.png")
    _poster(tmp_path / "tmp" / "Heat (1995)" / "poster.png")
    _holiday(test_db)
    rules = PlexRuleMatcher([_rule("Classics", "Heat (1995)")])
    assert _run(test_db, tmp_path, mode="incremental", plex_matcher=rules)["changed"] == 2
    expected = {"Elf (2003)/poster.png": "holiday:Winter", "Heat (1995)/poster.png": "rule:Classics"}
    assert _notes(test_db) == expected

    test_db.query(PosterBorder).delete()
    test_db.commit()
    assert _run(test_db, tmp_path, mode="incremental", plex_matcher=rules)["changed"] == 0
    assert _notes(test_db) == expected

    # A run without the rules cannot name the rule on a poster it leaves alone: its note stays.
    assert _run(test_db, tmp_path, mode="incremental", plex_matcher=None)["changed"] == 0
    assert _notes(test_db) == expected

    renamed = PlexRuleMatcher([_rule("Oldies", "Heat (1995)")])  # same style, new name: nothing to process, the note follows
    assert _run(test_db, tmp_path, mode="incremental", plex_matcher=renamed)["changed"] == 0
    assert _notes(test_db)["Heat (1995)/poster.png"] == "rule:Oldies"

    _holiday(test_db, name="")  # the holiday is over: every poster is bordered again and the note goes
    _run(test_db, tmp_path, mode="incremental", plex_matcher=renamed)
    assert _notes(test_db) == {"Heat (1995)/poster.png": "rule:Oldies"}


def test_border_notes_write_only_what_changed(test_db):
    notes = BorderNotes(test_db)
    notes.note("/dest/A/poster.jpg", "holiday:Winter")
    notes.note("/dest/B/poster.jpg", None)
    notes.save()
    assert {r.file_path: r.border for r in test_db.query(PosterBorder)} == {"/dest/A/poster.jpg": "holiday:Winter"}
    again = BorderNotes(test_db)
    again.note("/dest/A//poster.jpg", "rule:Gold")  # the same file, however its path is spelled
    again.note("/dest/C/poster.jpg", "holiday:Winter")
    again.save(placed=["/dest/A/poster.jpg", "/dest/C/poster.jpg"])
    assert {r.file_path: r.border for r in test_db.query(PosterBorder)} == {"/dest/A/poster.jpg": "rule:Gold", "/dest/C/poster.jpg": "holiday:Winter"}
    BorderNotes(test_db).save(placed=["/dest/C/poster.jpg"])
    assert [r.file_path for r in test_db.query(PosterBorder)] == ["/dest/C/poster.jpg"]


# ── the overlay settings ─────────────────────────────────────────────────────

def test_border_keys_are_cleaned():
    assert normalize_border_keys(["holiday: Winter ", "HOLIDAY:winter", "rule:4K", "theme:x", "rule:", 7, None]) == ["holiday:Winter", "rule:4K"]
    assert normalize_border_keys("holiday:Winter") == [] and normalize_border_keys(None) == []


def test_config_keeps_links_and_lets_a_linked_overlay_share_a_library():
    cfg = normalize_config({"enabled": True, "types": {"status": {"enabled": True, "overlays": [
        {"name": "Normal", "libraries": ["srv:2"]},
        {"name": "Winter", "libraries": ["srv:2"], "borders": ["holiday:Winter", "bogus"]},
        {"name": "Second", "libraries": ["srv:2"]},
    ]}}})
    overlays = cfg["types"]["status"]["overlays"]
    assert [o["borders"] for o in overlays] == [[], ["holiday:Winter"], []]
    assert [o["libraries"] for o in overlays] == [["srv:2"], ["srv:2"], []]  # only the overlays without a link share out the libraries
    assert overlay_signature(overlays[0]) == overlay_signature({**overlays[0], "borders": ["rule:X"]})  # a link draws nothing differently


def test_overlay_for_asks_the_linked_overlays_first():
    pick = ServerOverlayEngine._overlay_for
    tcfg = {"overlays": [
        {"name": "All", "all_libraries": True, "libraries": []},
        {"name": "TV", "all_libraries": False, "libraries": ["srv:2"]},
        {"name": "Winter", "all_libraries": True, "libraries": [], "borders": ["holiday:Winter", "rule:Gold"]},
        {"name": "Winter TV", "all_libraries": False, "libraries": ["srv:2"], "borders": ["holiday:Winter"]},
    ]}
    assert pick(tcfg, "srv:2")["name"] == "TV" and pick(tcfg, "srv:1")["name"] == "All"  # no holiday or rule border: never a linked one
    assert pick(tcfg, "srv:2", "holiday:Winter")["name"] == "Winter TV"
    assert pick(tcfg, "srv:1", "holiday:winter")["name"] == "Winter"  # a name's case does not matter
    assert pick(tcfg, "srv:2", "rule:Gold")["name"] == "Winter"       # linked goes before the library's own normal overlay
    assert pick(tcfg, "srv:2", "rule:Other")["name"] == "TV"          # a border no overlay is linked to: the normal one
    only_linked = {"overlays": [tcfg["overlays"][2]]}
    assert pick(only_linked, "srv:1") is None and pick(only_linked, "srv:1", "rule:Gold")["name"] == "Winter"
    off = {"overlays": [tcfg["overlays"][0], {**tcfg["overlays"][2], "enabled": False}]}
    assert pick(off, "srv:1", "holiday:Winter")["name"] == "All"


def test_custom_overlays_draw_a_linked_one_only_on_its_posters():
    custom = get_type("custom")
    tcfg = {"overlays": [
        {"name": "Everywhere", "all_libraries": True, "libraries": []},
        {"name": "Winter", "all_libraries": True, "libraries": [], "borders": ["holiday:Winter"]},
        {"name": "Winter TV", "all_libraries": False, "libraries": ["srv:2"], "borders": ["holiday:Winter"]},
    ]}
    names = lambda library, border="": [o["name"] for o in ServerOverlayEngine._overlays_for(custom, tcfg, library, border)]  # noqa: E731
    assert names("srv:1") == ["Everywhere"]
    assert names("srv:1", "holiday:Winter") == ["Everywhere", "Winter"]
    assert names("srv:2", "holiday:Winter") == ["Everywhere", "Winter", "Winter TV"]
    assert names("srv:2", "rule:Gold") == ["Everywhere"]


# ── the engine ───────────────────────────────────────────────────────────────

def _linked_config():
    return normalize_config({"enabled": True, "types": {"status": {"enabled": True, "overlays": [
        {"name": "Normal", "all_libraries": True},
        {"name": "Winter", "all_libraries": True, "borders": ["holiday:Winter"], "placement": {"vertical_offset": 400}, "options": {"states": {"ended": {"text": "DONE"}}}},
    ]}}})


def _note(db, path, border):
    notes = BorderNotes(db)
    notes.note(path, border)
    notes.save()


def test_engine_draws_the_linked_overlay_on_posters_with_that_border(test_db, tmp_path):
    client = FakeClient()
    sources = _sources(tmp_path, 20, 30)
    meta = FakeMetadata({1: {"status": "Ended", "first_air_date": "2010-01-01"}, 2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    _note(test_db, sources[("srv", "30")], "holiday:Winter")
    engine = _engine(test_db, client, meta, _linked_config(), sources=sources)
    assert engine.run()["applied"] == 2
    engine.flush()
    items = _state_items(test_db)
    assert "status:ended|DONE|" in items["srv|30"]["sig"] and "status:ended|ENDED|" in items["srv|20"]["sig"]
    ctx = engine._context("srv", client.items["2"][1], None, client, sources[("srv", "30")])
    assert ctx.border == "holiday:Winter" and engine._resolve(ctx)[0][0][2]["vertical_offset"] == 400

    # The holiday is over: the border pass clears the note and the normal overlay comes back.
    _note(test_db, sources[("srv", "30")], None)
    second = _engine(test_db, client, meta, _linked_config(), sources=sources)
    stats = second.run()
    second.flush()
    assert stats["refreshed"] == 1 and stats["current"] == 1
    assert "status:ended|ENDED|" in _state_items(test_db)["srv|30"]["sig"]


def test_upload_hook_and_stale_check_follow_the_posters_border(test_db, tmp_path):
    client = FakeClient()
    sources = _sources(tmp_path, 30)
    path = sources[("srv", "30")]
    meta = FakeMetadata({2: {"status": "Ended", "first_air_date": "2010-01-01"}})
    show = client.items["2"][1]
    engine = _engine(test_db, client, meta, _linked_config(), sources=sources)
    composite = engine.compose_for_upload("srv", show, path)
    client.upload_image(show, "poster", composite)
    engine.record_upload("srv", show, True)
    assert "status:ended|ENDED|" in _state_items(test_db)["srv|30"]["sig"]
    assert _engine(test_db, client, meta, _linked_config(), sources=sources).badge_stale("srv", show, path) is False

    # The poster now carries the holiday's border: the badge on the server is the wrong overlay's.
    _note(test_db, path, "holiday:Winter")
    fresh = _engine(test_db, client, meta, _linked_config(), sources=sources)
    assert fresh.badge_stale("srv", show, path) is True
    composite = fresh.compose_for_upload("srv", show, path)
    client.upload_image(show, "poster", composite)
    fresh.record_upload("srv", show, True)
    assert "status:ended|DONE|" in _state_items(test_db)["srv|30"]["sig"]


def test_engine_reads_no_notes_while_no_overlay_is_linked(test_db, tmp_path, monkeypatch):
    import services.overlays.server as server_mod

    client = FakeClient()
    sources = _sources(tmp_path, 30)
    _note(test_db, sources[("srv", "30")], "holiday:Winter")
    asked = []
    monkeypatch.setattr(server_mod, "border_of", lambda db, path: asked.append(path) or "holiday:Winter")
    plain = normalize_config({"enabled": True, "types": {"status": {"enabled": True, "overlays": [{"name": "Normal"}]}}})
    engine = _engine(test_db, client, FakeMetadata({}), plain, sources=sources)
    assert engine._context("srv", client.items["2"][1], None, client, sources[("srv", "30")]).border == "" and asked == []
    linked = _engine(test_db, client, FakeMetadata({}), _linked_config(), sources=sources)
    assert linked._context("srv", client.items["2"][1], None, client, sources[("srv", "30")]).border == "holiday:Winter"
    assert linked._context("srv", client.items["2"][1], None, client).border == ""  # badged on the server's own poster: no file, no note
    assert ItemContext.from_server_item(client.items["2"][1], "srv").border == ""


# ── the page's API ───────────────────────────────────────────────────────────

def test_config_lists_the_holidays_and_rules_to_link(client, test_db):
    assert client.get("/api/overlays/config").json()["borders"] == []
    _holiday(test_db, name="Winter", schedule="range(12/01-12/31)")
    test_db.add(Setting(key="border_replacer_plex_rules", value=json.dumps([
        {"name": "Classics", "match": "label", "value": "Old", "mode": "include", "colors": ["#FFFFFF"]},
        {"name": "", "match": "genre", "value": "Horror", "mode": "skip"},
    ])))
    test_db.commit()
    assert client.get("/api/overlays/config").json()["borders"] == border_choices(test_db) == [
        {"key": "holiday:Winter", "kind": "holiday", "name": "Winter", "detail": "12/01 to 12/31"},
        {"key": "rule:Classics", "kind": "rule", "name": "Classics", "detail": "label Old"},
        {"key": "rule:genre:Horror", "kind": "rule", "name": "genre:Horror", "detail": "genre Horror"},  # a rule without a name goes by what it matches
    ]
    body = {"enabled": True, "types": {"status": {"enabled": True, "overlays": [{"name": "Winter", "borders": ["holiday:Winter"]}]}}}
    saved = client.post("/api/overlays/config", json=body).json()["config"]
    assert saved["types"]["status"]["overlays"][0]["borders"] == ["holiday:Winter"]


def test_preview_draws_the_linked_holidays_or_rules_border(client, test_db, monkeypatch):
    import api.poster_manager as pm

    monkeypatch.setattr(pm, "_load_preview_base", lambda _db: Image.new("RGB", (1000, 1500), (90, 90, 90)))
    _holiday(test_db, name="Winter", schedule="range(01/01-01/01)" if _today_is_not_new_years_day() else "range(07/01-07/01)", colors=("#FF0000",))
    test_db.add(Setting(key="border_replacer_plex_rules", value=json.dumps([
        {"name": "Classics", "match": "label", "value": "Old", "mode": "include", "colors": ["#00FF00"]},
    ])))
    test_db.commit()

    def corner(border: str):
        resp = client.post("/api/overlays/preview", json={"type": "status", "variant": "ended", "base": "bordered", "border": border})
        assert resp.status_code == 200
        with Image.open(io.BytesIO(resp.content)) as img:
            return img.convert("RGB").getpixel((0, 0))

    near = lambda got, want: all(abs(a - b) <= 3 for a, b in zip(got, want))  # noqa: E731
    assert corner("") == (90, 90, 90)  # the holiday is not on today and no main border is set
    assert near(corner("holiday:Winter"), (255, 0, 0))
    assert near(corner("rule:Classics"), (0, 255, 0))
    assert corner("holiday:Gone") == (90, 90, 90)  # a holiday that was removed: the saved settings as they are


def _today_is_not_new_years_day() -> bool:
    from core.app_timezone import now_in_app_tz

    now = now_in_app_tz()
    return (now.month, now.day) != (1, 1)


def test_a_poster_written_without_the_border_pass_loses_its_note(test_db):
    from services.poster_borders import forget_borders

    _note(test_db, "/dest/A/poster.jpg", "holiday:Winter")
    _note(test_db, "/dest/B/poster.jpg", "rule:Hands off")
    forget_borders(test_db, ["/dest/A//poster.jpg", "/dest/C/poster.jpg"])
    assert {r.file_path: r.border for r in test_db.query(PosterBorder)} == {"/dest/B/poster.jpg": "rule:Hands off"}
    forget_borders(test_db, [])
    assert test_db.query(PosterBorder).count() == 1


def test_webhook_promotion_drops_the_notes_of_the_posters_it_writes(test_db, tmp_path, monkeypatch):
    """With the border pass off a webhook copies staged posters over the placed ones: plain again."""
    import modules.upload as upload_module
    from models.drive import Drive
    from models.job import Job

    class _Renamer:
        def __init__(self, _db, **_kwargs):
            pass

        def rename_posters(self, **_kwargs):
            return {"success": True, "stats": {"total_matched": 1, "movies": 1, "series": 0, "collections": 0}}

    monkeypatch.setattr("modules.upload.PosterRenameService", _Renamer)
    dest = tmp_path / "dest"
    for folder, placed in (("Elf (2003)", b"bordered"), ("Heat (1995)", b"staged")):
        (dest / "tmp" / folder).mkdir(parents=True)
        (dest / "tmp" / folder / "poster.jpg").write_bytes(b"staged")
        (dest / folder).mkdir()
        (dest / folder / "poster.jpg").write_bytes(placed)
    _note(test_db, str(dest / "Elf (2003)" / "poster.jpg"), "holiday:Winter")
    _note(test_db, str(dest / "Heat (1995)" / "poster.jpg"), "rule:Hands off")
    test_db.add(Setting(key="poster_destination", value=str(dest)))
    test_db.add(Setting(key="poster_drive_priority", value=json.dumps({"drive_ids": [1]})))
    test_db.add(Drive(id=1, name="Drive", drive_id="drive-1", style_type="MM2K", subscribed=True))
    job = Job(job_type="Plex Upload Webhook", status="pending", progress=0, message="Queued")
    test_db.add(job)
    test_db.commit()

    upload_module._run_webhook_preupload_rename_pass(test_db, job, {"media_type": "movie", "title": "Jaws", "year": 1975, "tmdb_id": 578, "imdb_id": None, "tvdb_id": None, "season_number": None})
    assert (dest / "Elf (2003)" / "poster.jpg").read_bytes() == b"staged"
    assert _notes(test_db) == {"Heat (1995)/poster.jpg": "rule:Hands off"}  # untouched by the copy: its note stays
