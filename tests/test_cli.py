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

        # After spawn the function loops on _is_alive() until timeout. Keep
        # urlopen failing throughout so we don't actually wait — but cap
        # the timeout so the test stays fast. Patch time.sleep to no-op
        # and time.time to advance fast.
        with patch("urllib.request.urlopen", side_effect=url_fail), \
             patch("subprocess.Popen") as mock_popen, \
             patch("time.sleep"), \
             patch("time.time", side_effect=[0.0, 100.0, 200.0]):
            # Act
            _ensure_proxy_running(timeout=0.01)

            # Assert — Popen was called once with our expected args
            assert mock_popen.call_count == 1
            args, kwargs = mock_popen.call_args
            assert args[0] == [sys.executable, "-m", "circuit_agent.proxy"]

        # The "Starting circuit-proxy" notice should have surfaced
        captured = capsys.readouterr()
        assert "Starting circuit-proxy" in captured.err
