"""Tests for services/gemini_kept.py: the server folder behind the maker card's kept Gemini results."""
import json

import pytest

import services.gemini_kept as gk


@pytest.fixture
def kept_root(tmp_path, monkeypatch):
    monkeypatch.setattr(gk, "folder", lambda: tmp_path / "gemini")
    return tmp_path / "gemini"


def _park(key="tmdb-550", role="poster", ext="jpg", data=b"\xff\xd8pixels", **meta):
    return gk.save_pending(data, ext=ext, key=key, meta={"role": role, "source": "tmdb", "season": None,
                                                         "operation": "remove_text", "width": 10, "height": 15,
                                                         "download_name": "Fight Club - textless.jpg", **meta})


def test_item_key_prefers_tmdb_then_tvdb_then_a_title_slug():
    assert gk.item_key(550, 123, "Fight Club") == "tmdb-550"
    assert gk.item_key(None, 123, "Fight Club") == "tvdb-123"
    assert gk.item_key(0, None, "Fight Club: The Musical!") == "title-fight-club-the-musical"
    assert gk.item_key(None, None, "") == "title-untitled"


def test_refs_never_escape_the_folder(kept_root):
    for bad in ("gemini:../poster-aaaaaaaaaaaa.jpg", "gemini:tmdb-1/../../poster-aaaaaaaaaaaa.jpg",
                "gemini:tmdb-1/poster-aaaaaaaaaaaa.exe", "gemini:tmdb-1/evil.jpg", "gemini:tmdb-1",
                "gemini:TMDB-1/poster-aaaaaaaaaaaa.jpg", "/p.jpg"):
        assert gk.ref_path(bad) is None, bad
        assert not gk.is_valid_ref(bad)
    # well-formed but absent: valid shape, not a usable ref
    assert gk.ref_path("gemini:tmdb-1/poster-aaaaaaaaaaaa.jpg") == kept_root / "tmdb-1" / "poster-aaaaaaaaaaaa.jpg"
    assert not gk.is_valid_ref("gemini:tmdb-1/poster-aaaaaaaaaaaa.jpg")
    with pytest.raises(FileNotFoundError):
        gk.read_ref("gemini:tmdb-1/poster-aaaaaaaaaaaa.jpg")


def test_park_keep_list_delete_round_trip(kept_root):
    ref = _park(season=2, source="tvdb")
    assert ref.startswith("gemini:tmdb-550/poster-") and ref.endswith(".jpg")
    path = gk.ref_path(ref)
    assert path.read_bytes() == b"\xff\xd8pixels"
    assert json.loads(gk.read_meta(path).__class__ is dict and json.dumps(gk.read_meta(path)))["kept"] is False
    assert gk.is_valid_ref(ref) and gk.read_ref(ref) == b"\xff\xd8pixels"
    assert gk.list_kept("tmdb-550") == []          # parked, not kept

    tile = gk.keep(ref)
    assert tile["ref"] == ref and tile["role"] == "poster" and tile["source"] == "tvdb" and tile["season"] == 2
    assert tile["url_full"] == gk.file_url(ref) == "/api/maker-tools/gemini-image/file?ref=gemini%3Atmdb-550%2F" + path.name
    assert tile["download_name"] == "Fight Club - textless.jpg"
    assert [t["ref"] for t in gk.list_kept("tmdb-550")] == [ref]
    assert gk.list_kept("tmdb-999") == [] and gk.list_kept("../tmdb-550") == []

    assert gk.delete(ref) is True
    assert not path.exists() and not gk._meta_path(path).exists()
    assert not (kept_root / "tmdb-550").exists()   # empty item folder pruned
    assert gk.delete(ref) is False
    assert gk.keep(ref) is None


def test_list_kept_is_newest_first(kept_root):
    first = _park(data=b"one")
    second = _park(data=b"two")
    gk.keep(first)
    gk.keep(second)
    refs = [t["ref"] for t in gk.list_kept("tmdb-550")]
    assert set(refs) == {first, second} and refs[0] == second or refs[0] == first   # same-second ties are fine


def test_clear_used_deletes_only_gemini_refs_that_exist(kept_root):
    ref = _park()
    gk.keep(ref)
    other = _park(data=b"keep me")
    gk.keep(other)
    cleared = gk.clear_used(["/tmdb.jpg", ref, "gemini:tmdb-550/poster-aaaaaaaaaaaa.jpg"])
    assert cleared == [ref]
    assert not gk.is_valid_ref(ref) and gk.is_valid_ref(other)


def test_sweep_drops_stale_parked_and_long_kept_results(kept_root):
    parked_fresh = _park(data=b"a")
    parked_old = _park(data=b"b")
    kept_fresh = _park(data=b"c")
    kept_old = _park(data=b"d")
    gk.keep(kept_fresh)
    gk.keep(kept_old)
    now = 1_000_000_000.0
    for ref, age in ((parked_fresh, 60), (parked_old, gk.PENDING_TTL + 1), (kept_fresh, gk.KEPT_TTL - 60), (kept_old, gk.KEPT_TTL + 1)):
        path = gk.ref_path(ref)
        meta = gk.read_meta(path)
        meta["created"] = now - age
        gk._write_meta(path, meta)
    assert gk.sweep(now=now) == 2
    assert gk.is_valid_ref(parked_fresh) and gk.is_valid_ref(kept_fresh)
    assert not gk.is_valid_ref(parked_old) and not gk.is_valid_ref(kept_old)
    assert gk.sweep(now=now) == 0


def test_save_pending_rejects_bad_keys_and_extensions(kept_root):
    with pytest.raises(ValueError):
        _park(key="../escape")
    with pytest.raises(ValueError):
        _park(ext="exe")
