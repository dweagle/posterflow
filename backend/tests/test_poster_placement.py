"""How a webhook places its poster: through the border pass when that is what placed the
destination's posters, else by a plain copy that leaves every other placed poster alone."""

import json
from pathlib import Path

from PIL import Image

from models.drive import Drive
from models.job import Job
from models.setting import Setting
from services.border_replacer import BorderReplacerService
from util.poster_settings import note_placement, placed_by_border_pass

ALIEN = {"media_type": "movie", "title": "Alien", "year": 1979, "tmdb_id": 348, "imdb_id": None, "tvdb_id": None, "season_number": None}


def _setup(db, dest: Path) -> Job:
    db.add(Setting(key="poster_destination", value=str(dest)))
    db.add(Setting(key="poster_drive_priority", value=json.dumps({"drive_ids": [1]})))
    db.add(Drive(id=1, name="Drive", drive_id="drive-1", style_type="MM2K", subscribed=True))
    job = Job(job_type="Plex Upload Webhook", status="pending", progress=0, message="Queued")
    db.add(job)
    db.commit()
    return job


def _file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _renamer(staged: dict):
    """A rename pass that stages these files ({path under tmp/: bytes}) and reports them."""

    class _Renamer:
        def __init__(self, _db, **_kwargs):
            pass

        def rename_posters(self, **kwargs):
            tmp = Path(kwargs["destination_dir"]) / "tmp"
            written = [str(_file(tmp / name, data)) for name, data in staged.items()]
            return {"success": True, "renamed_files": written, "stats": {"total_matched": len(written), "movies": len(written), "series": 0, "collections": 0}}

    return _Renamer


def test_webhook_plain_copy_leaves_the_other_placed_posters_alone(test_db, tmp_path, monkeypatch):
    import modules.upload as upload_module

    dest = tmp_path / "dest"
    job = _setup(test_db, dest)
    _file(dest / "tmp" / "Elf (2003)" / "poster.jpg", b"plain")
    _file(dest / "Elf (2003)" / "poster.jpg", b"bordered")        # placed by a border pass earlier
    _file(dest / "tmp" / "Heat (1995)" / "poster.jpg", b"plain")   # staged, never placed
    monkeypatch.setattr("modules.upload.PosterRenameService", _renamer({"Alien (1979)/poster.jpg": b"plain"}))

    summary = upload_module._run_webhook_preupload_rename_pass(test_db, job, ALIEN)
    assert (dest / "Alien (1979)" / "poster.jpg").read_bytes() == b"plain"
    assert (dest / "Heat (1995)" / "poster.jpg").read_bytes() == b"plain"
    assert (dest / "Elf (2003)" / "poster.jpg").read_bytes() == b"bordered"
    assert summary["tmp_promotion"]["copied"] == 2 and summary["border"]["attempted"] is False


def test_webhook_plain_copy_replaces_a_placed_poster_it_staged_again(test_db, tmp_path, monkeypatch):
    import modules.upload as upload_module

    dest = tmp_path / "dest"
    job = _setup(test_db, dest)
    test_db.add(Setting(key="plex_webhook_adopt_existing_processed", value="false"))
    test_db.commit()
    _file(dest / "tmp" / "Alien (1979)" / "poster.jpg", b"old")
    _file(dest / "Alien (1979)" / "poster.jpg", b"old")
    monkeypatch.setattr("modules.upload.PosterRenameService", _renamer({"Alien (1979)/poster.jpg": b"new"}))

    upload_module._run_webhook_preupload_rename_pass(test_db, job, ALIEN)
    assert (dest / "Alien (1979)" / "poster.jpg").read_bytes() == b"new"


def test_webhook_runs_the_border_pass_when_it_placed_the_destinations_posters(test_db, tmp_path, monkeypatch):
    """Auto-Run Border Replacer is off, but the posters in the destination came out of the border pass."""
    import modules.upload as upload_module

    dest = tmp_path / "dest"
    job = _setup(test_db, dest)
    _file(dest / "Elf (2003)" / "poster.jpg", b"bordered")
    note_placement(test_db, by_border_pass=True)
    calls = []

    class _Border:
        def __init__(self, _db, **_kwargs):
            pass

        def process_posters(self, **kwargs):
            calls.append(kwargs)
            return {"success": True, "changed": 1, "skipped": 1}

    monkeypatch.setattr("modules.upload.PosterRenameService", _renamer({"Alien (1979)/poster.jpg": b"plain"}))
    monkeypatch.setattr("modules.upload.BorderReplacerService", _Border)

    summary = upload_module._run_webhook_preupload_rename_pass(test_db, job, ALIEN)
    assert len(calls) == 1 and calls[0]["source_dir"] == str(dest / "tmp") and calls[0]["destination_dir"] == str(dest)
    assert summary["border"]["enabled"] is True and summary["border"]["success"] is True
    assert summary["tmp_promotion"]["attempted"] is False
    assert not (dest / "Alien (1979)").exists()  # nothing is copied plain beside the border pass

    # After a plain placement the webhook copies plain again.
    note_placement(test_db, by_border_pass=False)
    upload_module._run_webhook_preupload_rename_pass(test_db, job, ALIEN)
    assert len(calls) == 1 and (dest / "Alien (1979)" / "poster.jpg").read_bytes() == b"plain"


def test_border_pass_notes_that_it_placed_the_posters(test_db, tmp_path):
    source = tmp_path / "dest" / "tmp" / "Elf (2003)" / "poster.png"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (1000, 1500), (255, 255, 255)).save(source)
    service = BorderReplacerService(test_db)
    run = lambda **kw: service.process_posters(  # noqa: E731
        source_dir=str(tmp_path / "dest" / "tmp"), destination_dir=str(tmp_path / "dest"), border_colors=["#0000FF"], border_width=26, exclusion_list=[], **kw)
    assert placed_by_border_pass(test_db) is False
    assert run(dry_run=True, mode="full")["success"] is True
    assert placed_by_border_pass(test_db) is False  # a dry run places nothing
    assert run(dry_run=False, mode="incremental")["success"] is True
    assert placed_by_border_pass(test_db) is True
    note_placement(test_db, by_border_pass=False)
    assert run(dry_run=False, mode="incremental")["changed"] == 0  # nothing to process: the posters are still its own
    assert placed_by_border_pass(test_db) is True


def test_renamers_plain_copy_notes_a_plain_placement(test_db, tmp_path, monkeypatch):
    import modules.renamer as renamer_module

    dest = tmp_path / "dest"
    job = _setup(test_db, dest)
    _file(dest / "Elf (2003)" / "poster.jpg", b"bordered")
    note_placement(test_db, by_border_pass=True)
    monkeypatch.setattr("modules.renamer.PosterRenameService", _renamer({"Elf (2003)/poster.jpg": b"plain"}))
    monkeypatch.setattr("modules.renamer.reconcile_community_lists", lambda *a, **k: None)

    renamer_module._run_poster_pass(test_db, job, {"destination": str(dest), "auto_run_border": False}, media_dict={})
    assert (dest / "Elf (2003)" / "poster.jpg").read_bytes() == b"plain"
    assert placed_by_border_pass(test_db) is False
