"""iter_file_entries: the listing supplies file types, so callers never pay a stat per file."""
import os

import pytest

from util.fs_scan import iter_file_entries


def _names(entries):
    return sorted(e.name for e in entries)


def test_filters_by_suffix_and_recurses(tmp_path):
    (tmp_path / "sub" / "deeper").mkdir(parents=True)
    (tmp_path / "a.JPG").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    (tmp_path / "sub" / "b.png").write_bytes(b"x")
    (tmp_path / "sub" / "deeper" / "c.webp").write_bytes(b"x")

    assert _names(iter_file_entries(tmp_path, {".jpg", ".png", ".webp"})) == ["a.JPG", "b.png", "c.webp"]
    assert _names(iter_file_entries(tmp_path, {".jpg", ".png"}, recursive=False)) == ["a.JPG"]
    assert _names(iter_file_entries(tmp_path)) == ["a.JPG", "b.png", "c.webp", "notes.txt"]


def test_skip_dirs_prunes_at_any_depth_case_insensitively(tmp_path):
    (tmp_path / "Item" / "TMP" / "x").mkdir(parents=True)
    (tmp_path / "tmp").mkdir()
    (tmp_path / "Item" / "poster.jpg").write_bytes(b"x")
    (tmp_path / "Item" / "TMP" / "x" / "poster.jpg").write_bytes(b"x")
    (tmp_path / "tmp" / "poster.jpg").write_bytes(b"x")

    found = [os.path.relpath(e.path, tmp_path) for e in iter_file_entries(tmp_path, {".jpg"}, skip_dirs={"tmp"})]
    assert found == [os.path.join("Item", "poster.jpg")]


def test_symlinked_folders_are_not_followed_but_file_links_count(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "inside.jpg").write_bytes(b"x")
    (tmp_path / "top.jpg").write_bytes(b"x")
    os.symlink(real, tmp_path / "linked_dir")
    os.symlink(tmp_path / "top.jpg", tmp_path / "linked_file.jpg")
    os.symlink(tmp_path / "missing.jpg", tmp_path / "broken.jpg")

    assert _names(iter_file_entries(tmp_path, {".jpg"})) == ["inside.jpg", "linked_file.jpg", "top.jpg"]


def test_unreadable_folder_is_skipped_unless_strict(tmp_path, monkeypatch):
    (tmp_path / "ok").mkdir()
    (tmp_path / "bad").mkdir()
    (tmp_path / "ok" / "a.jpg").write_bytes(b"x")
    real_scandir = os.scandir

    def _scandir(path):
        if str(path).endswith("bad"):
            raise PermissionError(13, "Permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr("util.fs_scan.os.scandir", _scandir)

    assert _names(iter_file_entries(tmp_path, {".jpg"})) == ["a.jpg"]
    with pytest.raises(PermissionError):
        list(iter_file_entries(tmp_path, {".jpg"}, strict=True))


def test_missing_root_is_empty_unless_strict(tmp_path):
    assert list(iter_file_entries(tmp_path / "nope")) == []
    with pytest.raises(FileNotFoundError):
        list(iter_file_entries(tmp_path / "nope", strict=True))
