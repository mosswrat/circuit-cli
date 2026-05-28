"""Tests for the helper functions in circuit_agent.cli.

Covers:
- _load_env_file_silently()
- write_env_file()
- _ensure_proxy_running()

These tests do NOT spawn real subprocesses or make real HTTP requests.
"""

from __future__ import annotations

import os
import stat
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from circuit_agent.cli import (
    _ensure_proxy_running,
    _env_file_path,
    _load_env_file_silently,
    write_env_file,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def circuit_home(tmp_path, monkeypatch):
    """Point CIRCUIT_AGENT_HOME at a fresh tmp dir so each test is isolated."""
    home = tmp_path / "circuit-agent"
    monkeypatch.setenv("CIRCUIT_AGENT_HOME", str(home))
    return home


@pytest.fixture
def clean_circuit_env(monkeypatch):
    """Strip any CIRCUIT_* vars from os.environ so tests start clean.

    monkeypatch.delenv with raising=False makes the teardown restore the
    original value automatically — no bleed between tests.
    """
    for key in (
        "CIRCUIT_CLIENT_ID",
        "CIRCUIT_CLIENT_SECRET",
        "CIRCUIT_APP_KEY",
        "CIRCUIT_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# _load_env_file_silently
# ---------------------------------------------------------------------------


class TestLoadEnvFileSilently:
    def test_missing_file_is_silent_noop(self, circuit_home, clean_circuit_env, capsys):
        # Arrange — directory doesn't exist at all
        assert not circuit_home.exists()

        # Act
        _load_env_file_silently()

        # Assert — no output, no env mutation
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""
        assert "CIRCUIT_CLIENT_ID" not in os.environ

    def test_populates_environ_from_valid_file(self, circuit_home, clean_circuit_env):
        # Arrange
        circuit_home.mkdir(parents=True)
        env_file = circuit_home / ".env"
        env_file.write_text(
            "CIRCUIT_CLIENT_ID=cid-123\n"
            "CIRCUIT_CLIENT_SECRET=secret-456\n"
            "CIRCUIT_APP_KEY=appkey-789\n"
            "CIRCUIT_MODEL=gpt-5-nano\n"
        )

        # Act
        _load_env_file_silently()

        # Assert
        assert os.environ["CIRCUIT_CLIENT_ID"] == "cid-123"
        assert os.environ["CIRCUIT_CLIENT_SECRET"] == "secret-456"
        assert os.environ["CIRCUIT_APP_KEY"] == "appkey-789"
        assert os.environ["CIRCUIT_MODEL"] == "gpt-5-nano"

    def test_ignores_comments_blanks_and_malformed_lines(
        self, circuit_home, clean_circuit_env
    ):
        # Arrange
        circuit_home.mkdir(parents=True)
        env_file = circuit_home / ".env"
        env_file.write_text(
            "# this is a comment\n"
            "\n"
            "   \n"
            "no_equals_here\n"
            "CIRCUIT_CLIENT_ID=only-real-one\n"
            "# CIRCUIT_CLIENT_SECRET=should-be-skipped\n"
        )

        # Act
        _load_env_file_silently()

        # Assert — only the well-formed line landed
        assert os.environ["CIRCUIT_CLIENT_ID"] == "only-real-one"
        assert "CIRCUIT_CLIENT_SECRET" not in os.environ

    def test_does_not_overwrite_existing_env_var(
        self, circuit_home, clean_circuit_env, monkeypatch
    ):
        # Arrange — shell wins
        monkeypatch.setenv("CIRCUIT_CLIENT_ID", "shell-wins")
        circuit_home.mkdir(parents=True)
        env_file = circuit_home / ".env"
        env_file.write_text("CIRCUIT_CLIENT_ID=file-loses\n")

        # Act
        _load_env_file_silently()

        # Assert
        assert os.environ["CIRCUIT_CLIENT_ID"] == "shell-wins"

    def test_unreadable_file_prints_warning_to_stderr(
        self, circuit_home, clean_circuit_env, capsys, monkeypatch
    ):
        # Arrange — file exists but read_text raises (simulate I/O error
        # since root bypasses POSIX perms).
        circuit_home.mkdir(parents=True)
        env_file = circuit_home / ".env"
        env_file.write_text("CIRCUIT_CLIENT_ID=irrelevant\n")

        def boom(self, *args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "read_text", boom)

        # Act — must not raise
        _load_env_file_silently()

        # Assert — warning landed on stderr, env untouched, no stdout noise
        captured = capsys.readouterr()
        assert "could not read" in captured.err.lower()
        assert str(env_file) in captured.err
        assert captured.out == ""
        assert "CIRCUIT_CLIENT_ID" not in os.environ


# ---------------------------------------------------------------------------
# write_env_file
# ---------------------------------------------------------------------------


class TestWriteEnvFile:
    def test_returns_path_it_wrote_to(self, circuit_home, clean_circuit_env):
        # Act
        path = write_env_file("cid", "secret", "appkey")

        # Assert
        assert isinstance(path, Path)
        assert path == _env_file_path()
        assert path.exists()

    def test_file_contains_all_four_key_value_lines(
        self, circuit_home, clean_circuit_env
    ):
        # Act
        path = write_env_file("cid-abc", "secret-xyz", "appkey-123", model="gpt-5-nano")

        # Assert
        content = path.read_text()
        assert "CIRCUIT_CLIENT_ID=cid-abc" in content
        assert "CIRCUIT_CLIENT_SECRET=secret-xyz" in content
        assert "CIRCUIT_APP_KEY=appkey-123" in content
        assert "CIRCUIT_MODEL=gpt-5-nano" in content

    def test_uses_default_model_when_omitted(self, circuit_home, clean_circuit_env):
        # Act
        path = write_env_file("cid", "secret", "appkey")

        # Assert
        assert "CIRCUIT_MODEL=gpt-5-nano" in path.read_text()

    @pytest.mark.skipif(os.name != "posix", reason="POSIX-only file mode check")
    def test_file_mode_is_0600_on_posix(self, circuit_home, clean_circuit_env):
        # Act
        path = write_env_file("cid", "secret", "appkey")

        # Assert — strip out the file-type bits, compare just the permission bits
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    @pytest.mark.skipif(os.name != "posix", reason="POSIX-only dir mode check")
    def test_parent_dir_mode_is_0700_on_posix(self, circuit_home, clean_circuit_env):
        # Act
        path = write_env_file("cid", "secret", "appkey")

        # Assert
        mode = stat.S_IMODE(path.parent.stat().st_mode)
        assert mode == 0o700

    def test_creates_parent_dir_if_missing(self, circuit_home, clean_circuit_env):
        # Arrange — directory doesn't exist yet
        assert not circuit_home.exists()

        # Act
        path = write_env_file("cid", "secret", "appkey")

        # Assert
        assert path.parent.exists()
        assert path.parent.is_dir()

    def test_overwrites_existing_file_content(self, circuit_home, clean_circuit_env):
        # Arrange — pre-existing file with stale content
        circuit_home.mkdir(parents=True)
        env_file = circuit_home / ".env"
        env_file.write_text("STALE=garbage\nCIRCUIT_CLIENT_ID=old-id\n")

        # Act
        path = write_env_file("new-cid", "new-secret", "new-appkey")

        # Assert
        content = path.read_text()
        assert "STALE=garbage" not in content
        assert "CIRCUIT_CLIENT_ID=old-id" not in content
        assert "CIRCUIT_CLIENT_ID=new-cid" in content


# ---------------------------------------------------------------------------
# _ensure_proxy_running
# ---------------------------------------------------------------------------


class TestEnsureProxyRunning:
    def test_opt_out_via_env_var_returns_immediately(
        self, monkeypatch, capsys
    ):
        # Arrange
        monkeypatch.setenv("CIRCUIT_AGENT_AUTO_PROXY", "0")

        # If anything actually tried to spawn we'd hear about it — patch Popen
        # to a failing mock to make any spawn attempt loud.
        with patch("subprocess.Popen") as mock_popen:
            # Act
            _ensure_proxy_running()

            # Assert
            mock_popen.assert_not_called()

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_short_circuits_when_health_returns_200(
        self, monkeypatch, circuit_home, capsys
    ):
        # Arrange — make sure auto-proxy is on
        monkeypatch.delenv("CIRCUIT_AGENT_AUTO_PROXY", raising=False)

        fake_response = MagicMock()
        fake_response.status = 200
        fake_response.__enter__ = MagicMock(return_value=fake_response)
        fake_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_response) as mock_urlopen, \
             patch("subprocess.Popen") as mock_popen:
            # Act
            _ensure_proxy_running()

            # Assert — health check ran, no spawn happened
            assert mock_urlopen.called
            mock_popen.assert_not_called()

        captured = capsys.readouterr()
        # No "Starting circuit-proxy..." message
        assert "Starting circuit-proxy" not in captured.err

    def test_spawns_proxy_when_health_unreachable(
        self, monkeypatch, circuit_home, capsys
    ):
        # Arrange — auto-proxy on, health check fails
        monkeypatch.delenv("CIRCUIT_AGENT_AUTO_PROXY", raising=False)

        def url_fail(*args, **kwargs):
            raise urllib.error.URLError("connection refused")

        # No polling loop in fire-and-forget mode, so no need to patch time.
        with patch("urllib.request.urlopen", side_effect=url_fail), \
             patch("subprocess.Popen") as mock_popen:
            # Act
            _ensure_proxy_running()

            # Assert — Popen was called once with our expected args
            assert mock_popen.call_count == 1
            args, _ = mock_popen.call_args
            assert args[0] == [sys.executable, "-m", "circuit_agent.proxy"]

        # The "Starting circuit-proxy" notice should have surfaced
        captured = capsys.readouterr()
        assert "Starting circuit-proxy" in captured.err

        # And the spawn marker should be gone after a successful spawn
        assert not (circuit_home / "proxy.spawning").exists()

    def test_spawn_marker_blocks_concurrent_spawn(
        self, monkeypatch, circuit_home, capsys
    ):
        # Arrange — health unreachable, but a fresh marker already exists
        # (simulating another agent mid-spawn)
        monkeypatch.delenv("CIRCUIT_AGENT_AUTO_PROXY", raising=False)
        circuit_home.mkdir(parents=True, exist_ok=True)
        marker = circuit_home / "proxy.spawning"
        marker.touch()

        def url_fail(*args, **kwargs):
            raise urllib.error.URLError("connection refused")

        with patch("urllib.request.urlopen", side_effect=url_fail), \
             patch("subprocess.Popen") as mock_popen:
            # Act
            _ensure_proxy_running()

            # Assert — no spawn because marker exists and is fresh
            mock_popen.assert_not_called()

        # Marker should still be there (we deferred to the other process)
        assert marker.exists()

    def test_stale_spawn_marker_is_reclaimed(
        self, monkeypatch, circuit_home, capsys
    ):
        # Arrange — health unreachable, stale marker from a crashed spawner
        monkeypatch.delenv("CIRCUIT_AGENT_AUTO_PROXY", raising=False)
        circuit_home.mkdir(parents=True, exist_ok=True)
        marker = circuit_home / "proxy.spawning"
        marker.touch()
        old_mtime = 1.0  # epoch — definitely > 30s ago
        os.utime(marker, (old_mtime, old_mtime))

        def url_fail(*args, **kwargs):
            raise urllib.error.URLError("connection refused")

        with patch("urllib.request.urlopen", side_effect=url_fail), \
             patch("subprocess.Popen") as mock_popen:
            # Act
            _ensure_proxy_running()

            # Assert — stale marker was reclaimed and the spawn proceeded
            assert mock_popen.call_count == 1

        # Cleaned up after successful spawn
        assert not marker.exists()


# ---------------------------------------------------------------------------
# _upgrade_in_place
# ---------------------------------------------------------------------------


class TestUpgradeInPlace:
    def test_returns_1_when_pip_missing(self, monkeypatch, tmp_path, capsys):
        from circuit_agent.cli import _upgrade_in_place

        # Arrange — point sys.executable somewhere that has no sibling pip
        fake_python = tmp_path / "bin" / "python"
        fake_python.parent.mkdir(parents=True)
        fake_python.write_text("")
        monkeypatch.setattr(sys, "executable", str(fake_python))

        # Act
        rc = _upgrade_in_place()

        # Assert
        assert rc == 1
        captured = capsys.readouterr()
        assert "cannot find pip" in captured.err

    def test_runs_pip_with_expected_command(self, monkeypatch, tmp_path):
        from circuit_agent.cli import REPO_GIT_URL, _upgrade_in_place

        # Arrange — fake venv layout
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        fake_python = venv_bin / "python"
        fake_pip = venv_bin / "pip"
        fake_agent = venv_bin / "circuit-agent"
        for p in (fake_python, fake_pip, fake_agent):
            p.write_text("")
        monkeypatch.setattr(sys, "executable", str(fake_python))

        fake_run = MagicMock(return_value=MagicMock(returncode=0))
        fake_check_output = MagicMock(return_value="Circuit Agent v5.99.0\n")

        with patch("subprocess.run", fake_run), \
             patch("subprocess.check_output", fake_check_output):
            # Act
            rc = _upgrade_in_place()

        # Assert
        assert rc == 0
        cmd = fake_run.call_args[0][0]
        assert cmd[0] == str(fake_pip)
        assert "install" in cmd
        assert "--upgrade" in cmd
        assert "--force-reinstall" in cmd
        assert "--no-cache-dir" in cmd  # load-bearing — pip caches git+ aggressively
        assert REPO_GIT_URL in cmd

    def test_propagates_nonzero_pip_exit(self, monkeypatch, tmp_path, capsys):
        from circuit_agent.cli import _upgrade_in_place

        # Arrange — fake venv with pip present
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").write_text("")
        (venv_bin / "pip").write_text("")
        monkeypatch.setattr(sys, "executable", str(venv_bin / "python"))

        with patch("subprocess.run", return_value=MagicMock(returncode=42)):
            # Act
            rc = _upgrade_in_place()

        # Assert
        assert rc == 42
        captured = capsys.readouterr()
        assert "Upgrade failed" in captured.err
