"""Integration tests for SshTransport — subprocess is mocked."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from skilltool.config import Config
from skilltool.transport.base import RegistryError
from skilltool.transport.ssh import SshTransport


def _cfg(**overrides: object) -> Config:
    base = dict(
        registry="http://example:8765",
        token="tok_alice_abc",
        transport="ssh",
        ssh_host="100.64.0.1",
        ssh_user="skilltool",
        registry_source="file",
        token_source="file",
        transport_source="file",
        ssh_host_source="file",
        ssh_user_source="file",
        config_file=Path("/nonexistent"),
    )
    base.update(overrides)
    return Config(**base)  # type: ignore[arg-type]


def _mock_run(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
    class _Result:
        def __init__(self) -> None:
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    return _Result()


def test_search_invokes_ssh_with_search_verb() -> None:
    payload = json.dumps([{"name": "docx", "latest": "1.0.0"}]).encode()
    with patch("subprocess.run", return_value=_mock_run(stdout=payload)) as run:
        results = SshTransport(_cfg()).search("doc")
        cmd = run.call_args.args[0]
        assert cmd[0] == "ssh"
        assert "skilltool-server" in cmd
        assert cmd[-2:] == ["search", "doc"]
        assert results == [{"name": "docx", "latest": "1.0.0"}]


def test_package_invokes_show_and_returns_dict() -> None:
    payload = json.dumps(
        {
            "name": "docx",
            "latest": "1.0.0",
            "versions": ["1.0.0"],
            "metadata": {"name": "docx", "version": "1.0.0"},
        }
    ).encode()
    with patch("subprocess.run", return_value=_mock_run(stdout=payload)) as run:
        out = SshTransport(_cfg()).package("docx")
        assert run.call_args.args[0][-2:] == ["show", "docx"]
        assert out["latest"] == "1.0.0"


def test_download_writes_bytes_to_dest(tmp_path) -> None:
    zip_bytes = b"PK\x03\x04fake-zip"
    with patch("subprocess.run", return_value=_mock_run(stdout=zip_bytes)) as run:
        dest = tmp_path / "docx.zip"
        out = SshTransport(_cfg()).download("docx", dest)
        assert run.call_args.args[0][-2:] == ["download", "docx"]
        assert out == dest
        assert dest.read_bytes() == zip_bytes


def test_download_passes_version_flag(tmp_path) -> None:
    with patch("subprocess.run", return_value=_mock_run(stdout=b"z")) as run:
        SshTransport(_cfg()).download("docx", tmp_path / "a.zip", version="1.2.0")
        cmd = run.call_args.args[0]
        assert cmd[-4:] == ["download", "docx", "--version", "1.2.0"]


def test_publish_sends_base64_payload(tmp_path) -> None:
    zip_path = tmp_path / "t.zip"
    zip_path.write_bytes(b"PK\x03\x04payload")
    response = {
        "name": "t",
        "version": "1.0.0",
        "status": "published",
        "published_by": "alice",
        "published_at": "2026-04-15T00:00:00Z",
    }
    with patch(
        "subprocess.run",
        return_value=_mock_run(stdout=json.dumps(response).encode()),
    ) as run:
        out = SshTransport(_cfg()).publish(zip_path)
    cmd = run.call_args.args[0]
    assert "--token" in cmd and "--data" in cmd
    data_arg = cmd[cmd.index("--data") + 1]
    assert base64.b64decode(data_arg) == b"PK\x03\x04payload"
    token_arg = cmd[cmd.index("--token") + 1]
    assert token_arg == "tok_alice_abc"
    assert out == response


def test_publish_requires_a_token(tmp_path) -> None:
    zip_path = tmp_path / "t.zip"
    zip_path.write_bytes(b"PK")
    with patch("subprocess.run") as run, pytest.raises(RegistryError):
        SshTransport(_cfg(token=None, token_source="none")).publish(zip_path)
    run.assert_not_called()


def test_non_zero_exit_raises_registry_error() -> None:
    with patch(
        "subprocess.run",
        return_value=_mock_run(stderr=b"ssh: connect: host down", returncode=255),
    ):
        with pytest.raises(RegistryError) as excinfo:
            SshTransport(_cfg()).search("x")
    msg = str(excinfo.value)
    assert "255" in msg
    assert "host down" in msg


def test_server_error_json_surfaces_as_registry_error() -> None:
    # server_cli.py prints {"error": "..."} on stderr, but even if it leaks
    # to stdout we must still raise cleanly.
    err_payload = json.dumps({"error": "package 'missing' not found"}).encode()
    with patch("subprocess.run", return_value=_mock_run(stdout=err_payload)):
        with pytest.raises(RegistryError) as excinfo:
            SshTransport(_cfg()).package("missing")
    assert "missing" in str(excinfo.value)


def test_ssh_override_for_local_testing(monkeypatch, tmp_path) -> None:
    # The SKILLTOOL_SSH_COMMAND env var swaps out the `ssh user@host
    # skilltool-server` prefix, enabling loopback E2E tests without a
    # real sshd.
    monkeypatch.setenv("SKILLTOOL_SSH_COMMAND", "python3 /tmp/fake-cli.py")
    with patch("subprocess.run", return_value=_mock_run(stdout=b"[]")) as run:
        SshTransport(_cfg()).search("x")
    cmd = run.call_args.args[0]
    # ssh prefix should be replaced entirely; no `ssh` token remains.
    assert cmd[0] == "python3"
    assert "ssh" not in cmd[0:1]
    assert cmd[1] == "/tmp/fake-cli.py"
