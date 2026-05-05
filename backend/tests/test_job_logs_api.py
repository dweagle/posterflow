from pathlib import Path

from core.config import settings


def _setup_log_dir(tmp_path: Path, job_type: str) -> Path:
    """Create a job log directory under a simulated logs dir and return the logs dir."""
    logs_dir = tmp_path / "logs"
    job_dir = logs_dir / job_type
    job_dir.mkdir(parents=True)
    return logs_dir


# ---------------------------------------------------------------------------
# GET /api/job-logs/  (list)
# already partially covered in test_logs_and_stats.py; these add edge cases
# ---------------------------------------------------------------------------


def test_list_job_logs_returns_all_primary_types(client, tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    prev = settings.log_file
    settings.log_file = str(logs_dir / "posterflow.log")
    try:
        response = client.get("/api/job-logs/")
        assert response.status_code == 200
        data = response.json()
        for expected_type in ["sync_one", "sync_all", "plex_upload", "workflow"]:
            assert expected_type in data
    finally:
        settings.log_file = prev


def test_list_job_logs_returns_files_for_existing_type(client, tmp_path):
    logs_dir = _setup_log_dir(tmp_path, "sync_one")
    (logs_dir / "sync_one" / "sync_one.log").write_text("line1\nline2\n")

    prev = settings.log_file
    settings.log_file = str(logs_dir / "posterflow.log")
    try:
        response = client.get("/api/job-logs/")
        assert response.status_code == 200
        data = response.json()
        assert len(data["sync_one"]) == 1
        assert data["sync_one"][0]["name"] == "sync_one.log"
        assert data["sync_one"][0]["size"] > 0
    finally:
        settings.log_file = prev


# ---------------------------------------------------------------------------
# GET /api/job-logs/{job_type}/{filename}  (read content)
# ---------------------------------------------------------------------------


def test_get_job_log_returns_file_content(client, tmp_path):
    logs_dir = _setup_log_dir(tmp_path, "sync_one")
    log_file = logs_dir / "sync_one" / "sync_one.log"
    log_file.write_text("log line one\nlog line two\n")

    prev = settings.log_file
    settings.log_file = str(logs_dir / "posterflow.log")
    try:
        response = client.get("/api/job-logs/sync_one/sync_one.log")
        assert response.status_code == 200
        data = response.json()
        assert "log line one" in data["content"]
        assert data["filename"] == "sync_one.log"
    finally:
        settings.log_file = prev


def test_get_job_log_returns_404_for_missing_file(client, tmp_path):
    logs_dir = _setup_log_dir(tmp_path, "sync_one")

    prev = settings.log_file
    settings.log_file = str(logs_dir / "posterflow.log")
    try:
        response = client.get("/api/job-logs/sync_one/nonexistent.log")
        assert response.status_code == 404
        assert response.json()["detail"] == "Log file not found"
    finally:
        settings.log_file = prev


def test_get_job_log_returns_400_for_invalid_job_type(client, tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    prev = settings.log_file
    settings.log_file = str(logs_dir / "posterflow.log")
    try:
        response = client.get("/api/job-logs/totally_invalid_type/some.log")
        assert response.status_code == 400
        assert "Invalid job type" in response.json()["detail"]
    finally:
        settings.log_file = prev


# ---------------------------------------------------------------------------
# GET /api/job-logs/{job_type}/{filename}/download
# ---------------------------------------------------------------------------


def test_download_job_log_streams_file(client, tmp_path):
    logs_dir = _setup_log_dir(tmp_path, "workflow")
    log_file = logs_dir / "workflow" / "workflow.log"
    log_file.write_text("workflow log content\n")

    prev = settings.log_file
    settings.log_file = str(logs_dir / "posterflow.log")
    try:
        response = client.get("/api/job-logs/workflow/workflow.log/download")
        assert response.status_code == 200
        assert b"workflow log content" in response.content
    finally:
        settings.log_file = prev


def test_download_job_log_returns_404_for_missing_file(client, tmp_path):
    logs_dir = _setup_log_dir(tmp_path, "workflow")

    prev = settings.log_file
    settings.log_file = str(logs_dir / "posterflow.log")
    try:
        response = client.get("/api/job-logs/workflow/missing.log/download")
        assert response.status_code == 404
    finally:
        settings.log_file = prev


def test_download_job_log_returns_400_for_invalid_job_type(client, tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    prev = settings.log_file
    settings.log_file = str(logs_dir / "posterflow.log")
    try:
        response = client.get("/api/job-logs/bad_type/file.log/download")
        assert response.status_code == 400
    finally:
        settings.log_file = prev
