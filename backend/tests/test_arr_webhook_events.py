"""The arr webhook routes ignore the Sonarr/Radarr events that never mean new media landed
(Rename, Grab, Health, deletes) and still queue imports, adds and test pings as before."""


def _submitted(monkeypatch):
    submitted = []
    monkeypatch.setattr("api.plex_upload.job_queue.submit", lambda *a, **k: submitted.append(a))
    return submitted


def _stats(client):
    return client.get("/api/posterflow/plex-upload/webhook-stats").json()


def test_arr_routes_ignore_rename_and_other_non_import_events(client, test_db, monkeypatch):
    submitted = _submitted(monkeypatch)
    series = {"series": {"title": "Peaky Blinders", "year": 2013, "tvdbId": 270915}, "renamedEpisodeFiles": []}
    resp = client.post("/api/posterflow/plex-upload/webhook", json={"eventType": "Rename", **series})
    assert resp.status_code == 200 and resp.json()["queued"] is False and resp.json()["message"] == "Ignored Sonarr event 'Rename' for Peaky Blinders"
    for event in ("Grab", "Health", "ApplicationUpdate", "SeriesDelete", "EpisodeFileDelete"):
        assert client.post("/api/posterflow/plex-upload/webhook", json={"eventType": event, **series}).json()["queued"] is False
    assert client.post("/api/posterflow/plex-upload/webhook", json={"eventType": "MovieFileDelete", "movie": {"title": "Heat", "year": 1995, "tmdbId": 949}}).json()["message"] == "Ignored Radarr event 'MovieFileDelete' for Heat"
    assert submitted == []
    stats = _stats(client)
    assert stats["skipped_ignored"] == 7 and stats["skipped_test"] == 0

    # Imports and adds still queue, and a test ping still counts as a test.
    assert client.post("/api/posterflow/plex-upload/webhook", json={"eventType": "Download", **series}).json()["queued"] is True
    assert client.post("/api/posterflow/plex-upload/webhook", json={"eventType": "SeriesAdd", **series}).json()["queued"] is True
    assert client.post("/api/posterflow/plex-upload/webhook", json={"eventType": "Test"}).json()["queued"] is False
    assert len(submitted) == 2 and _stats(client)["skipped_test"] == 1
