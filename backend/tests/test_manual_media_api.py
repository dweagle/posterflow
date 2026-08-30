"""Tests for /api/posterflow/manual-media endpoints."""

import json
import pytest

from models.manual_media import ManualMediaEntry


# ---------------------------------------------------------------------------
# GET /manual-media
# ---------------------------------------------------------------------------


def test_list_manual_media_empty(client):
    """Returns an empty list when no entries exist."""
    response = client.get("/api/posterflow/manual-media")
    assert response.status_code == 200
    data = response.json()
    assert data["entries"] == []


def test_list_manual_media_returns_existing(client, test_db):
    """Returns all stored entries in insertion order."""
    test_db.add(ManualMediaEntry(title="Alien", year=1979, media_type="movie"))
    test_db.add(ManualMediaEntry(title="The Wire", year=2002, media_type="series", seasons_json="[1,2,3,4,5]"))
    test_db.commit()

    response = client.get("/api/posterflow/manual-media")
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert len(entries) == 2
    assert entries[0]["title"] == "Alien"
    assert entries[0]["media_type"] == "movie"
    assert entries[0]["seasons"] == []
    assert entries[1]["title"] == "The Wire"
    assert entries[1]["seasons"] == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# POST /manual-media
# ---------------------------------------------------------------------------


def test_add_movie(client):
    """Adding a movie creates an entry with no seasons."""
    response = client.post(
        "/api/posterflow/manual-media",
        json={"title": "Alien", "year": 1979, "media_type": "movie"},
    )
    assert response.status_code == 200
    entry = response.json()["entry"]
    assert entry["title"] == "Alien"
    assert entry["year"] == 1979
    assert entry["media_type"] == "movie"
    assert entry["seasons"] == []
    assert entry["id"] is not None


def test_add_series_with_seasons(client):
    """Adding a series generates sequential season numbers."""
    response = client.post(
        "/api/posterflow/manual-media",
        json={"title": "Breaking Bad", "year": 2008, "media_type": "series", "seasons_count": 5},
    )
    assert response.status_code == 200
    entry = response.json()["entry"]
    assert entry["title"] == "Breaking Bad"
    assert entry["seasons"] == [1, 2, 3, 4, 5]


def test_add_series_with_specials(client):
    """Adding a series with include_specials prepends season 0."""
    response = client.post(
        "/api/posterflow/manual-media",
        json={
            "title": "The Simpsons",
            "year": 1989,
            "media_type": "series",
            "seasons_count": 3,
            "include_specials": True,
        },
    )
    assert response.status_code == 200
    entry = response.json()["entry"]
    assert entry["seasons"] == [0, 1, 2, 3]


def test_add_series_specials_only(client):
    """A series with include_specials but no seasons_count stores [0]."""
    response = client.post(
        "/api/posterflow/manual-media",
        json={
            "title": "Doctor Who Specials",
            "year": 2023,
            "media_type": "series",
            "include_specials": True,
        },
    )
    assert response.status_code == 200
    entry = response.json()["entry"]
    assert entry["seasons"] == [0]


def test_add_entry_with_external_ids(client):
    """TMDB/TVDB/IMDB IDs are stored and returned correctly."""
    response = client.post(
        "/api/posterflow/manual-media",
        json={
            "title": "Inception",
            "year": 2010,
            "media_type": "movie",
            "tmdb_id": 27205,
            "tvdb_id": None,
            "imdb_id": "tt1375666",
        },
    )
    assert response.status_code == 200
    entry = response.json()["entry"]
    assert entry["tmdb_id"] == 27205
    assert entry["tvdb_id"] is None
    assert entry["imdb_id"] == "tt1375666"


def test_add_entry_strips_whitespace_from_title(client):
    """Leading/trailing whitespace is stripped from title."""
    response = client.post(
        "/api/posterflow/manual-media",
        json={"title": "  Alien  ", "year": 1979, "media_type": "movie"},
    )
    assert response.status_code == 200
    assert response.json()["entry"]["title"] == "Alien"


def test_add_entry_rejects_empty_title(client):
    """An empty or whitespace-only title returns 400."""
    response = client.post(
        "/api/posterflow/manual-media",
        json={"title": "   ", "media_type": "movie"},
    )
    assert response.status_code == 400
    assert "title is required" in response.json()["detail"]


def test_add_entry_rejects_missing_title(client):
    """A missing title field returns 422 (validation error)."""
    response = client.post(
        "/api/posterflow/manual-media",
        json={"media_type": "movie"},
    )
    assert response.status_code == 422


def test_add_entry_rejects_invalid_media_type(client):
    """An unrecognised media_type returns 400."""
    response = client.post(
        "/api/posterflow/manual-media",
        json={"title": "Alien", "year": 1979, "media_type": "episode"},
    )
    assert response.status_code == 400
    assert "media_type" in response.json()["detail"]


# ---------------------------------------------------------------------------
# DELETE /manual-media/{id}
# ---------------------------------------------------------------------------


def test_delete_entry(client, test_db):
    """Deleting an existing entry succeeds and removes it from the list."""
    entry = ManualMediaEntry(title="Alien", year=1979, media_type="movie")
    test_db.add(entry)
    test_db.commit()
    test_db.refresh(entry)
    entry_id = entry.id

    response = client.delete(f"/api/posterflow/manual-media/{entry_id}")
    assert response.status_code == 200
    assert response.json()["success"] is True

    list_response = client.get("/api/posterflow/manual-media")
    assert all(e["id"] != entry_id for e in list_response.json()["entries"])


def test_delete_nonexistent_entry_returns_404(client):
    """Deleting an ID that doesn't exist returns 404."""
    response = client.delete("/api/posterflow/manual-media/99999")
    assert response.status_code == 404


def test_inject_manual_media_skips_entries_covered_by_other_sources(test_db):
    """A manual entry matching an existing sourced entry (shared id, or same
    title+year when id-less) must not inject — with the media-server source it
    would double-place posters under a second folder name."""
    from models.manual_media import ManualMediaEntry
    from services.poster_renamer import PosterRenameService

    test_db.add(ManualMediaEntry(title="Heat", year=1995, media_type="movie", tmdb_id=949))
    test_db.add(ManualMediaEntry(title="Solo Show", year=2020, media_type="series"))
    test_db.add(ManualMediaEntry(title="Uncovered", year=2001, media_type="movie", tmdb_id=777))
    test_db.commit()

    service = PosterRenameService(test_db)
    media_dict = {
        "movies": [{"title": "Heat", "year": 1995, "tmdb_id": 949,
                    "normalized_title": "heat", "instance": "jf (Movies)"}],
        "series": [{"title": "Solo Show", "year": 2020, "tmdb_id": None, "tvdb_id": None, "imdb_id": None,
                    "normalized_title": "soloshow", "instance": "jf (TV)"}],
        "collections": [],
    }
    service._inject_manual_media(media_dict)

    assert [m["title"] for m in media_dict["movies"]] == ["Heat", "Uncovered"]
    assert [s["title"] for s in media_dict["series"]] == ["Solo Show"]
    manual = [m for m in media_dict["movies"] if m.get("source") == "manual"]
    assert [m["title"] for m in manual] == ["Uncovered"]
