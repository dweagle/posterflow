from pathlib import Path
from types import SimpleNamespace
import io
import pytest

from services.rclone import RcloneService
from core.config import Settings, settings


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

    def test_upload_tuning_flags_come_from_settings(self, monkeypatch, tmp_path):
        """Upload throughput is latency-bound (~1s round-trip per small file), so it tracks
        --transfers almost linearly. That makes it a knob rather than a constant: Google
        documents ~3 sustained writes/sec but does not appear to enforce it, so the safe
        move is a conservative default that can be raised by env and measured."""
        svc = self._service(monkeypatch, tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        captured = self._make_popen(monkeypatch, ["There was nothing to transfer"], returncode=0)
        svc.upload_folder(src, "drive-123")
        args = captured["args"]

        assert f"--drive-chunk-size={settings.rclone_upload_chunk_size}" in args
        assert "--drive-stop-on-upload-limit" in args
        assert f"--transfers={settings.rclone_upload_transfers}" in args

    def test_upload_transfers_default_stays_conservative(self):
        """The default ships to every user, on quotas we cannot see. Raising it is an opt-in
        experiment via env, not a new baseline for everyone."""
        assert Settings().rclone_upload_transfers == 4

    def test_upload_rate_knobs_default_to_the_shared_download_values(self, monkeypatch, tmp_path):
        """Unset upload knobs must not silently slow uploads below the shared setting."""
        svc = self._service(monkeypatch, tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        monkeypatch.setattr(settings, "rclone_tps_limit", 12)
        monkeypatch.setattr(settings, "rclone_pacer_min_sleep", "60ms")
        monkeypatch.setattr(settings, "rclone_upload_tps_limit", None)
        monkeypatch.setattr(settings, "rclone_upload_pacer_min_sleep", None)
        captured = self._make_popen(monkeypatch, ["There was nothing to transfer"], returncode=0)
        svc.upload_folder(src, "drive-123")

        assert "--tpslimit=12" in captured["args"]
        assert "--drive-pacer-min-sleep=60ms" in captured["args"]

    def test_upload_rate_knobs_override_without_touching_downloads(self, monkeypatch, tmp_path):
        """Uploads cost 50 quota units vs 200 for a download, so they can run hotter. Raising
        the upload knobs must not raise the download path along with them."""
        svc = self._service(monkeypatch, tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        monkeypatch.setattr(settings, "rclone_tps_limit", 12)
        monkeypatch.setattr(settings, "rclone_pacer_min_sleep", "60ms")
        monkeypatch.setattr(settings, "rclone_upload_tps_limit", 30)
        monkeypatch.setattr(settings, "rclone_upload_pacer_min_sleep", "30ms")

        captured = self._make_popen(monkeypatch, ["There was nothing to transfer"], returncode=0)
        svc.upload_folder(src, "drive-123")
        assert "--tpslimit=30" in captured["args"]
        assert "--drive-pacer-min-sleep=30ms" in captured["args"]

        # The download path must still be on the conservative shared numbers.
        captured = self._make_popen(monkeypatch, ["There was nothing to transfer"], returncode=0)
        svc.sync_folder("drive-123", tmp_path / "dst")
        assert "--tpslimit=12" in captured["args"]
        assert "--drive-pacer-min-sleep=60ms" in captured["args"]

    def test_check_first_stats_do_not_report_uploading(self, monkeypatch, tmp_path):
        """--check-first makes rclone print 'Transferred: 0 / 0 Bytes' every second while
        it is still listing/checking. Reading that as a file count emits an uploading
        phase at ratio 0, which pins the caller's monotonic progress bar at the uploading
        floor for the whole run (the 'stuck on checking' bug)."""
        svc = self._service(monkeypatch, tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "poster.jpg").write_text("x")

        output = [
            "Transferred:   \t         0 / 0 Bytes, -, 0 Bytes/s, ETA -",
            "Checks:                 0 / 0, -, Listed 1200",
            "Transferred:   \t         0 / 0 Bytes, -, 0 Bytes/s, ETA -",
            "Checks:              2142 / 9963, 21%, Listed 19926",
        ]
        self._make_popen(monkeypatch, output, returncode=0)

        calls = []
        svc.upload_folder(src, "drive-123", progress_callback=lambda *a: calls.append(a))

        phases = [c[2] for c in calls]
        assert "uploading_stats" not in phases, (
            "nothing has transferred yet; an uploading phase here freezes the progress bar"
        )
        assert "listing" in phases and "checking" in phases

    def test_stale_checks_line_stops_reporting_once_transfers_start(self, monkeypatch, tmp_path):
        """After --check-first finishes, rclone keeps reprinting the final 'Checks:' line in
        every stats block. Reporting it as the checking phase overwrites the live upload
        message once a second, so the bar reads 'Checked 5,712/18,618' forever while files
        are actually uploading."""
        svc = self._service(monkeypatch, tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "poster.jpg").write_text("x")

        output = [
            # still checking -- this one must report
            "Checks:              5712 / 18618, 31%, Listed 19926",
            "INFO  : Local file system at remote: Checks finished, now starting transfers",
            "INFO  : squareart/Cats (1998) - squareart.jpg: Copied (new)",
            # rclone keeps reprinting the finished Checks line alongside real transfers
            "Transferred:            479 / 12906, 3%",
            "Checks:              5712 / 18618, 31%, Listed 19926",
            "Transferred:            960 / 12906, 7%",
            "Checks:              5712 / 18618, 31%, Listed 19926",
        ]
        self._make_popen(monkeypatch, output, returncode=0)

        calls = []
        svc.upload_folder(src, "drive-123", progress_callback=lambda *a: calls.append(a))

        phases = [c[2] for c in calls]
        # The one real checking report (before transfers began) is fine.
        assert phases.count("checking") == 1, f"stale Checks lines re-reported: {phases}"
        # Nothing may report checking again once uploading has started.
        first_upload = next(i for i, p in enumerate(phases) if p.startswith("uploading"))
        assert "checking" not in phases[first_upload:], (
            f"a stale Checks line clobbered the upload message: {phases}"
        )

    def test_byte_totals_are_not_mistaken_for_file_counts(self, monkeypatch, tmp_path):
        """The byte line ('1.2 GiB / 5.6 GiB') and the count line ('123 / 456') both start
        with 'Transferred:'. Only the count may drive progress."""
        svc = self._service(monkeypatch, tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "poster.jpg").write_text("x")

        output = [
            "Transferred:   \t  1.2 GiB / 5.6 GiB, 21%, 10.5 MiB/s, ETA 7m",
            "Transferred:            123 / 456, 27%",
            "Transferred:   \t        512 / 1024 Bytes, 50%, 128 Bytes/s, ETA 4s",
        ]
        self._make_popen(monkeypatch, output, returncode=0)

        calls = []
        svc.upload_folder(src, "drive-123", progress_callback=lambda *a: calls.append(a))

        stats = [c for c in calls if c[2] == "uploading_stats"]
        assert stats, "a real file count must still report progress"
        # Only the 123/456 count line qualifies -- never 1/5 (GiB) or 512/1024 (Bytes).
        assert all((c[0], c[1]) == (123, 456) for c in stats)


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



# ---------------------------------------------------------------------------
# upload_files — one rclone process for a named set of files
# ---------------------------------------------------------------------------

class TestUploadFiles:
    """Single-item drops name their files instead of mirroring the folder, so the transfer
    never lists a scope holding thousands of others -- and one process covers the whole drop,
    because paying startup/auth per file cost ~2s each and gave up parallel transfers."""

    def _service(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "config_dir", tmp_path)
        monkeypatch.setattr("services.rclone.shutil.which", lambda _: "/usr/bin/rclone")
        svc = RcloneService()
        monkeypatch.setattr(svc, "_get_credentials", lambda: ("id", "secret", '{"token":"abc"}', None))
        return svc

    def _popen(self, monkeypatch, output_lines, returncode=0):
        captured = {}

        def _fake_popen(args, **_kwargs):
            captured["args"] = args
            captured["list_file"] = Path(args[args.index("--files-from-raw") + 1]).read_text()
            proc = SimpleNamespace(
                stdout=io.StringIO("\n".join(output_lines) + "\n"),
                returncode=returncode,
            )
            proc.wait = lambda: None
            return proc

        monkeypatch.setattr("services.rclone.subprocess.Popen", _fake_popen)
        return captured

    def _root(self, tmp_path, *names):
        root = tmp_path / "scope"
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
        return root

    def test_one_process_covers_every_file(self, monkeypatch, tmp_path):
        svc = self._service(monkeypatch, tmp_path)
        root = self._root(tmp_path, "a.jpg", "b.jpg", "logos/c.png")
        captured = self._popen(monkeypatch, [
            "INFO  : a.jpg: Copied (new)",
            "INFO  : b.jpg: Copied (new)",
            "INFO  : logos/c.png: Copied (new)",
        ])

        result = svc.upload_files(root, "drive-123", ["a.jpg", "b.jpg", "logos/c.png"])

        assert result["success"] is True
        assert result["uploaded"] == ["a.jpg", "b.jpg", "logos/c.png"]
        assert captured["list_file"].splitlines() == ["a.jpg", "b.jpg", "logos/c.png"]

    def test_never_traverses_the_destination(self, monkeypatch, tmp_path):
        """--no-traverse is what keeps this cheap against a scope with thousands of files."""
        svc = self._service(monkeypatch, tmp_path)
        root = self._root(tmp_path, "a.jpg")
        captured = self._popen(monkeypatch, ["INFO  : a.jpg: Copied (new)"])

        svc.upload_files(root, "drive-123", ["a.jpg"])

        assert "--no-traverse" in captured["args"]
        assert "--fast-list" not in captured["args"]
        assert captured["args"][1] == "copy", "copy, never sync -- this must not delete anything remote"

    def test_uses_files_from_raw_so_hash_names_are_not_comments(self, monkeypatch, tmp_path):
        """--files-from would treat '#1 (2020).jpg' as a comment and silently skip it."""
        svc = self._service(monkeypatch, tmp_path)
        root = self._root(tmp_path, "#1 (2020).jpg")
        captured = self._popen(monkeypatch, ["INFO  : #1 (2020).jpg: Copied (new)"])

        result = svc.upload_files(root, "drive-123", ["#1 (2020).jpg"])

        assert "--files-from-raw" in captured["args"]
        assert "--files-from" not in captured["args"]
        assert result["uploaded"] == ["#1 (2020).jpg"]

    def test_unchanged_file_counts_as_landed(self, monkeypatch, tmp_path):
        """A re-drop of identical bytes is skipped by rclone -- it's still on the drive."""
        svc = self._service(monkeypatch, tmp_path)
        root = self._root(tmp_path, "a.jpg")
        self._popen(monkeypatch, ["INFO  : a.jpg: Unchanged skipping"])

        result = svc.upload_files(root, "drive-123", ["a.jpg"])

        assert result["success"] is True
        assert result["failed"] == {}

    def test_partial_failure_blames_only_the_file_that_did_not_transfer(self, monkeypatch, tmp_path):
        svc = self._service(monkeypatch, tmp_path)
        root = self._root(tmp_path, "good.jpg", "bad.jpg")
        self._popen(
            monkeypatch,
            ["INFO  : good.jpg: Copied (new)", "ERROR : bad.jpg: quota exceeded"],
            returncode=1,
        )

        result = svc.upload_files(root, "drive-123", ["good.jpg", "bad.jpg"])

        assert result["success"] is False
        assert result["uploaded"] == ["good.jpg"]
        assert "bad.jpg" in result["failed"]
        assert "good.jpg" not in result["failed"]

    def test_colon_in_filename_parses_correctly(self, monkeypatch, tmp_path):
        """Titles carry colons; the action is the LAST ': ' segment, not the first."""
        svc = self._service(monkeypatch, tmp_path)
        root = self._root(tmp_path, "SI-VIS: The Sound of Heroes (2025).jpg")
        self._popen(monkeypatch, ["INFO  : SI-VIS: The Sound of Heroes (2025).jpg: Copied (new)"])

        seen = []
        result = svc.upload_files(
            root, "drive-123", ["SI-VIS: The Sound of Heroes (2025).jpg"],
            file_done_callback=lambda path, action: seen.append((path, action)),
        )

        assert result["success"] is True
        assert seen == [("SI-VIS: The Sound of Heroes (2025).jpg", "Copied (new)")]

    def test_missing_local_file_is_reported_without_running_rclone(self, monkeypatch, tmp_path):
        svc = self._service(monkeypatch, tmp_path)
        root = self._root(tmp_path, "here.jpg")

        def _must_not_run(*_args, **_kwargs):  # pragma: no cover - guard
            raise AssertionError("rclone must not run when nothing is uploadable")

        monkeypatch.setattr("services.rclone.subprocess.Popen", _must_not_run)

        result = svc.upload_files(root, "drive-123", ["gone.jpg"])

        assert result["success"] is False
        assert "Local file not found" in result["failed"]["gone.jpg"]

    def test_list_file_is_cleaned_up(self, monkeypatch, tmp_path):
        svc = self._service(monkeypatch, tmp_path)
        root = self._root(tmp_path, "a.jpg")
        captured = self._popen(monkeypatch, ["INFO  : a.jpg: Copied (new)"])

        svc.upload_files(root, "drive-123", ["a.jpg"])

        list_path = Path(captured["args"][captured["args"].index("--files-from-raw") + 1])
        assert not list_path.exists()
