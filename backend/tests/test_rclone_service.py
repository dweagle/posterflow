from types import SimpleNamespace
import io
import pytest

from services.rclone import RcloneService
from core.config import settings


def test_rclone_service_creates_minimal_config(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "config_dir", tmp_path)

    service = RcloneService()

    config_path = tmp_path / "rclone.conf"
    assert service.config_path == config_path
    assert config_path.exists()
    content = config_path.read_text()
    assert "[gdrive]" in content
    assert "type = drive" in content


def test_test_connection_returns_false_when_credentials_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "config_dir", tmp_path)
    service = RcloneService()
    monkeypatch.setattr(service, "_get_credentials", lambda: (None, None, None, None))

    assert service.test_connection() is False


def test_test_connection_returns_true_when_credentials_present(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "config_dir", tmp_path)
    service = RcloneService()
    monkeypatch.setattr(service, "_get_credentials", lambda: ("id", "secret", '{"token":"abc"}', None))

    assert service.test_connection() is True


def test_test_connection_returns_true_when_service_account_present(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "config_dir", tmp_path)
    service = RcloneService()

    sa_file = tmp_path / "service-account.json"
    sa_file.write_text('{"type":"service_account"}')
    monkeypatch.setattr(service, "_get_credentials", lambda: (None, None, None, str(sa_file)))

    assert service.test_connection() is True


def test_list_files_returns_items_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "config_dir", tmp_path)
    monkeypatch.setattr("services.rclone.shutil.which", lambda _binary: "/usr/bin/rclone")
    service = RcloneService()
    monkeypatch.setattr(service, "_get_credentials", lambda: ("id", "secret", '{"token":"abc"}', None))

    fake_result = SimpleNamespace(returncode=0, stdout='[{"Name":"one.jpg"}]', stderr="")
    monkeypatch.setattr("services.rclone.subprocess.run", lambda *args, **kwargs: fake_result)

    files = service.list_files("drive-123")
    assert isinstance(files, list)
    assert files[0]["Name"] == "one.jpg"


def test_download_file_returns_true_or_false_from_rclone_result(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "config_dir", tmp_path)
    monkeypatch.setattr("services.rclone.shutil.which", lambda _binary: "/usr/bin/rclone")
    service = RcloneService()
    monkeypatch.setattr(service, "_get_credentials", lambda: ("id", "secret", '{"token":"abc"}', None))

    local_file = tmp_path / "downloads" / "poster.jpg"

    ok_result = SimpleNamespace(returncode=0, stdout="", stderr="")
    fail_result = SimpleNamespace(returncode=1, stdout="", stderr="boom")

    calls = {"count": 0}

    def _fake_run(*args, **kwargs):
        calls["count"] += 1
        return ok_result if calls["count"] == 1 else fail_result

    monkeypatch.setattr("services.rclone.subprocess.run", _fake_run)

    assert service.download_file("drive-123", "remote/poster.jpg", local_file) is True
    assert service.download_file("drive-123", "remote/poster.jpg", local_file) is False


def test_sync_folder_restricts_to_poster_filetypes_and_size(monkeypatch, tmp_path):
    """sync_folder must constrain the download to poster/PSD file types and a max
    file size so a shared Drive cannot push arbitrary or oversized files to disk."""
    from services.rclone import SYNC_INCLUDE_REGEX, SYNC_MAX_FILE_SIZE

    monkeypatch.setattr(settings, "config_dir", tmp_path)
    monkeypatch.setattr("services.rclone.shutil.which", lambda _binary: "/usr/bin/rclone")
    service = RcloneService()
    monkeypatch.setattr(service, "_get_credentials", lambda: ("id", "secret", '{"token":"abc"}', None))
    monkeypatch.setattr(service, "_sync_refreshed_token_to_db", lambda _original: None)

    captured = {}

    class _FakeProcess:
        stdout = iter([])
        returncode = 0

        def wait(self):
            return None

    def _fake_popen(args, **_kwargs):
        captured["args"] = args
        return _FakeProcess()

    monkeypatch.setattr("services.rclone.subprocess.Popen", _fake_popen)

    result = service.sync_folder("drive-123", tmp_path / "local")
    assert result["success"] is True

    args = captured["args"]
    # --include must immediately precede the regex value, and --max-size the size.
    assert "--include" in args
    assert args[args.index("--include") + 1] == SYNC_INCLUDE_REGEX
    assert "--max-size" in args
    assert args[args.index("--max-size") + 1] == SYNC_MAX_FILE_SIZE
    # The regex is case-insensitive and ends each allowed extension at the path end.
    assert SYNC_INCLUDE_REGEX == r"{{(?i).*\.(jpg|jpeg|png|webp|psd)$}}"
    assert SYNC_MAX_FILE_SIZE == "250M"


# ---------------------------------------------------------------------------
# _build_remote_root_path — injection-prevention validation
# ---------------------------------------------------------------------------

class TestBuildRemoteRootPath:
    def _service(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "config_dir", tmp_path)
        return RcloneService()

    def test_valid_alphanumeric_id(self, monkeypatch, tmp_path):
        svc = self._service(monkeypatch, tmp_path)
        result = svc._build_remote_root_path("AbCdEf123")
        assert result == "gdrive,root_folder_id=AbCdEf123:"

    def test_valid_id_with_hyphens_and_underscores(self, monkeypatch, tmp_path):
        svc = self._service(monkeypatch, tmp_path)
        result = svc._build_remote_root_path("drive_id-123_XYZ")
        assert result == "gdrive,root_folder_id=drive_id-123_XYZ:"

    @pytest.mark.parametrize("bad_id", [
        "",
        "  ",
        "../../../etc",
        "id with spaces",
        "id;rm -rf /",
        "id\x00null",
        "id\nnewline",
        "id$(injection)",
    ])
    def test_rejects_invalid_drive_ids(self, monkeypatch, tmp_path, bad_id):
        svc = self._service(monkeypatch, tmp_path)
        with pytest.raises(ValueError, match="Invalid drive_id format"):
            svc._build_remote_root_path(bad_id)


# ---------------------------------------------------------------------------
# _sanitize_remote_relative_path — traversal and control character rejection
# ---------------------------------------------------------------------------

class TestSanitizeRemoteRelativePath:
    def _service(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "config_dir", tmp_path)
        return RcloneService()

    def test_clean_relative_path_unchanged(self, monkeypatch, tmp_path):
        svc = self._service(monkeypatch, tmp_path)
        assert svc._sanitize_remote_relative_path("Movies/2024") == "Movies/2024"

    def test_leading_slash_stripped(self, monkeypatch, tmp_path):
        svc = self._service(monkeypatch, tmp_path)
        assert svc._sanitize_remote_relative_path("/Movies/2024") == "Movies/2024"

    def test_empty_path_returns_empty(self, monkeypatch, tmp_path):
        svc = self._service(monkeypatch, tmp_path)
        assert svc._sanitize_remote_relative_path("") == ""

    def test_dot_segments_removed(self, monkeypatch, tmp_path):
        svc = self._service(monkeypatch, tmp_path)
        assert svc._sanitize_remote_relative_path("Movies/./2024") == "Movies/2024"

    @pytest.mark.parametrize("traversal", [
        "../etc/passwd",
        "Movies/../../../etc",
        "a/b/../../../c",
        "..",
    ])
    def test_rejects_path_traversal(self, monkeypatch, tmp_path, traversal):
        svc = self._service(monkeypatch, tmp_path)
        with pytest.raises(ValueError, match="Path traversal"):
            svc._sanitize_remote_relative_path(traversal)

    @pytest.mark.parametrize("bad_path", [
        "path\x00null",
        "path\nnewline",
        "path\rcarriage",
    ])
    def test_rejects_control_characters(self, monkeypatch, tmp_path, bad_path):
        svc = self._service(monkeypatch, tmp_path)
        with pytest.raises(ValueError, match="Invalid remote path characters"):
            svc._sanitize_remote_relative_path(bad_path)


# ---------------------------------------------------------------------------
# sync_folder — output parsing and result shape
# ---------------------------------------------------------------------------

class TestSyncFolder:
    def _service(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "config_dir", tmp_path)
        monkeypatch.setattr("services.rclone.shutil.which", lambda _: "/usr/bin/rclone")
        svc = RcloneService()
        monkeypatch.setattr(svc, "_get_credentials", lambda: ("id", "secret", '{"token":"abc"}', None))
        return svc

    def _make_popen(self, monkeypatch, output_lines, returncode=0):
        """Patch subprocess.Popen to yield fake rclone output."""
        _rc = returncode

        def _fake_popen(*args, **kwargs):
            proc = SimpleNamespace(
                stdout=io.StringIO("\n".join(output_lines) + "\n"),
                returncode=_rc,
            )
            proc.wait = lambda: None
            return proc

        monkeypatch.setattr("services.rclone.subprocess.Popen", _fake_popen)

    def test_returns_success_true_on_zero_exit(self, monkeypatch, tmp_path):
        svc = self._service(monkeypatch, tmp_path)
        self._make_popen(monkeypatch, ["There was nothing to transfer"], returncode=0)
        result = svc.sync_folder("drive-123", tmp_path / "dest")
        assert result["success"] is True

    def test_returns_success_false_on_nonzero_exit(self, monkeypatch, tmp_path):
        svc = self._service(monkeypatch, tmp_path)
        self._make_popen(monkeypatch, ["ERROR : something went wrong"], returncode=1)
        result = svc.sync_folder("drive-123", tmp_path / "dest")
        assert result["success"] is False

    def test_files_transferred_counted_from_output(self, monkeypatch, tmp_path):
        svc = self._service(monkeypatch, tmp_path)
        # Simulate rclone INFO lines for two copied files and a stats Transferred line
        output = [
            "2024/01/01 00:00:00 INFO  : poster1.jpg: Copied (new)",
            "2024/01/01 00:00:01 INFO  : poster2.jpg: Copied (new)",
            "Transferred:            2 / 2, 100%",
        ]
        self._make_popen(monkeypatch, output, returncode=0)
        result = svc.sync_folder("drive-123", tmp_path / "dest")
        assert result["success"] is True
        assert result["files_transferred"] >= 2

    def test_returns_error_when_no_rclone_binary(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "config_dir", tmp_path)
        monkeypatch.setattr("services.rclone.shutil.which", lambda _: None)
        svc = RcloneService()
        result = svc.sync_folder("drive-123", tmp_path / "dest")
        assert result["success"] is False
        assert "rclone" in result.get("error", "").lower()

    def test_returns_error_when_credentials_missing(self, monkeypatch, tmp_path):
        svc = self._service(monkeypatch, tmp_path)
        monkeypatch.setattr(svc, "_get_credentials", lambda: (None, None, None, None))
        result = svc.sync_folder("drive-123", tmp_path / "dest")
        assert result["success"] is False
        assert "credentials" in result.get("error", "").lower()

    def test_progress_callback_invoked(self, monkeypatch, tmp_path):
        svc = self._service(monkeypatch, tmp_path)
        output = [
            "2024/01/01 00:00:00 INFO  : poster1.jpg: Copied (new)",
        ]
        self._make_popen(monkeypatch, output, returncode=0)

        calls = []
        svc.sync_folder("drive-123", tmp_path / "dest", progress_callback=lambda *a: calls.append(a))
        assert len(calls) > 0


# ---------------------------------------------------------------------------
# upload_folder — listing-phase progress so the bar moves while rclone lists
# the remote drive (Checks pinned at 0 / total during that slow window)
# ---------------------------------------------------------------------------

class TestUploadFolderProgress:
    def _service(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "config_dir", tmp_path)
        monkeypatch.setattr("services.rclone.shutil.which", lambda _: "/usr/bin/rclone")
        svc = RcloneService()
        monkeypatch.setattr(svc, "_get_credentials", lambda: ("id", "secret", '{"token":"abc"}', None))
        return svc

    def _make_popen(self, monkeypatch, output_lines, returncode=0):
        captured = {}

        def _fake_popen(args, **kwargs):
            captured["args"] = args
            proc = SimpleNamespace(
                stdout=io.StringIO("\n".join(output_lines) + "\n"),
                returncode=returncode,
            )
            proc.wait = lambda: None
            return proc

        monkeypatch.setattr("services.rclone.subprocess.Popen", _fake_popen)
        return captured

    def test_listing_phase_reports_growing_listed_count(self, monkeypatch, tmp_path):
        """While Checks stays 0/total, the growing 'Listed N' count must drive a
        'listing' phase so the progress bar moves instead of freezing."""
        svc = self._service(monkeypatch, tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "poster.jpg").write_text("x")

        output = [
            "Checks:                 0 / 5233, 0%, Listed 1200",
            "Checks:                 0 / 5233, 0%, Listed 4800",
            "Checks:              5233 / 5233, 100%, Listed 5233",
        ]
        self._make_popen(monkeypatch, output, returncode=0)

        calls = []
        result = svc.upload_folder(src, "drive-123", progress_callback=lambda *a: calls.append(a))
        assert result["success"] is True

        listing_calls = [c for c in calls if c[2] == "listing"]
        assert listing_calls, "expected a 'listing' phase while Checks were pinned at 0"
        # current arg (listed count) must increase across the listing window
        listed_counts = [c[0] for c in listing_calls]
        assert listed_counts == sorted(listed_counts)
        assert max(listed_counts) >= 4800
        # Once checks complete, it switches to the 'checking' phase
        assert any(c[2] == "checking" for c in calls)

    def test_upload_uses_check_first(self, monkeypatch, tmp_path):
        svc = self._service(monkeypatch, tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        captured = self._make_popen(monkeypatch, ["There was nothing to transfer"], returncode=0)
        svc.upload_folder(src, "drive-123")
        assert "--check-first" in captured["args"]


# ---------------------------------------------------------------------------
# OAuth credentials written to config file (not argv)
# ---------------------------------------------------------------------------

class TestOAuthWrittenToConfig:
    def test_oauth_creds_not_in_argv(self, monkeypatch, tmp_path):
        """When OAuth creds are configured, build_drive_auth_args returns [] — creds go into the config file."""
        monkeypatch.setattr(settings, "config_dir", tmp_path)
        svc = RcloneService()
        monkeypatch.setattr(svc, "_get_credentials", lambda: ("my-client-id", "my-secret", '{"token":"abc"}', None))

        auth_args = svc._get_drive_auth_args()
        assert auth_args == [], "OAuth args must NOT be passed on the command line"

        config_text = (tmp_path / "rclone.conf").read_text()
        assert "my-client-id" in config_text
        assert "my-secret" in config_text

    def test_service_account_still_passed_as_argv(self, monkeypatch, tmp_path):
        """Service-account-file path is safe to pass as a CLI arg (it's a path, not a secret)."""
        monkeypatch.setattr(settings, "config_dir", tmp_path)
        svc = RcloneService()
        sa_file = tmp_path / "sa.json"
        sa_file.write_text('{"type":"service_account"}')
        monkeypatch.setattr(svc, "_get_credentials", lambda: (None, None, None, str(sa_file)))

        auth_args = svc._get_drive_auth_args()
        assert "--drive-service-account-file" in auth_args

