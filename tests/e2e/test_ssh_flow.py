"""End-to-end tests for the SSH transport.

We don't assume a real ``sshd`` is reachable. Instead we lean on
``SKILLTOOL_SSH_COMMAND`` to replace the ``ssh user@host
skilltool-server`` prefix with a direct ``python server_cli.py``
invocation. That crosses the same subprocess / JSON boundary the real
transport crosses — it only skips the SSH hop.

Covers task003 E20–E25:
  E20: SSH transport で list            → 登録パッケージが列挙される
  E21: SSH transport で install         → ファイルが展開される
  E22: SSH transport で publish         → published_by が返る
  E23: SSH transport で search          → マッチが返る
  E24: SSH 接続失敗時のエラー            → RegistryError が raise される
  E25: トランスポート切り替え           → HTTP ↔ SSH で同一結果
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

from skilltool.config import Config
from skilltool.transport.base import RegistryError
from skilltool.transport.ssh import SshTransport

_REPO = Path(__file__).resolve().parents[2]
_SERVER_CLI = _REPO / "registry" / "server_cli.py"


@pytest.fixture()
def ssh_env(tmp_path, monkeypatch) -> tuple[Path, Path]:
    """Set up an isolated registry on disk + the SSH override env var."""
    storage = tmp_path / "data"
    (storage / "packages").mkdir(parents=True)
    users_file = storage / "users.toml"
    users_file.write_text(
        textwrap.dedent(
            """
            [users.alice]
            token = "tok_alice_aaaa"
            teams = ["team-doc"]
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("SKILLTOOL_STORAGE_DIR", str(storage))
    monkeypatch.setenv("SKILLTOOL_USERS_FILE", str(users_file))
    monkeypatch.setenv("SKILLTOOL_AUDIT_LOG", str(storage / "publish.log"))
    monkeypatch.setenv(
        "SKILLTOOL_SSH_COMMAND",
        f"{sys.executable} {_SERVER_CLI}",
    )
    return storage, users_file


def _cfg(token: str | None = "tok_alice_aaaa") -> Config:
    return Config(
        registry="http://example:8765",
        token=token,
        transport="ssh",
        ssh_host="ignored-via-override",
        ssh_user="skilltool",
        registry_source="file",
        token_source="file",
        transport_source="file",
        ssh_host_source="file",
        ssh_user_source="file",
        config_file=Path("/nonexistent"),
    )


def _make_zip(name: str, version: str, description: str = "desc") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "skill.md",
            textwrap.dedent(
                f"""
                ---
                name: {name}
                version: {version}
                description: {description}
                author: test-author
                ---

                body
                """
            ).lstrip(),
        )
    return buf.getvalue()


# ---------------------------------------------------------------------------
def test_e22_publish_returns_published_by(ssh_env, tmp_path):
    zip_path = tmp_path / "docx.zip"
    zip_path.write_bytes(_make_zip("docx", "1.0.0", "write documents"))

    with SshTransport(_cfg()) as t:
        out = t.publish(zip_path)
    assert out["published_by"] == "alice"
    assert out["version"] == "1.0.0"


def test_e23_search_matches(ssh_env, tmp_path):
    zip_path = tmp_path / "docx.zip"
    zip_path.write_bytes(_make_zip("docx", "1.0.0", "write documents"))
    with SshTransport(_cfg()) as t:
        t.publish(zip_path)

    with SshTransport(_cfg()) as t:
        results = t.search("doc")
    names = [r["name"] for r in results]
    assert "docx" in names


def test_e20_list_via_search_all(ssh_env, tmp_path):
    with SshTransport(_cfg()) as t:
        t.publish(_write_zip(tmp_path, _make_zip("docx", "1.0.0")))
        t.publish(_write_zip(tmp_path, _make_zip("pdf", "2.0.0", "pdf tools")))
        # "list all" ≡ match-all regex
        all_pkgs = t.search(".*")
    names = sorted(r["name"] for r in all_pkgs)
    assert names == ["docx", "pdf"]


def test_e21_install_extracts_files(ssh_env, tmp_path):
    zip_path = _write_zip(tmp_path, _make_zip("docx", "1.0.0"))
    with SshTransport(_cfg()) as t:
        t.publish(zip_path)

    # download + extract = install
    with SshTransport(_cfg()) as t:
        dest_zip = tmp_path / "downloaded.zip"
        t.download("docx", dest_zip)

    with zipfile.ZipFile(dest_zip) as zf:
        out_dir = tmp_path / "installed"
        zf.extractall(out_dir)

    skill_md = out_dir / "skill.md"
    assert skill_md.is_file()
    assert "name: docx" in skill_md.read_text()


def test_e24_ssh_failure_surfaces_as_registry_error(monkeypatch, ssh_env, tmp_path):
    # Force the SSH command to something guaranteed to fail.
    monkeypatch.setenv("SKILLTOOL_SSH_COMMAND", "/bin/false")
    with pytest.raises(RegistryError) as excinfo:
        with SshTransport(_cfg()) as t:
            t.search("x")
    assert "exit" in str(excinfo.value)


def test_e24_missing_ssh_binary_also_errors(monkeypatch, ssh_env):
    monkeypatch.setenv("SKILLTOOL_SSH_COMMAND", "/nonexistent-ssh-binary")
    with pytest.raises(RegistryError):
        with SshTransport(_cfg()) as t:
            t.search("x")


def test_e25_http_and_ssh_agree_on_reads(ssh_env, tmp_path):
    # Publish via SSH
    zip_path = _write_zip(tmp_path, _make_zip("docx", "1.0.0", "describe docs"))
    with SshTransport(_cfg()) as t:
        t.publish(zip_path)

    # Read via SSH
    with SshTransport(_cfg()) as t:
        ssh_pkg = t.package("docx")

    # Read via HTTP using the same FastAPI app, in-process.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "server", _REPO / "registry" / "server.py"
    )
    server = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(server)

    from fastapi.testclient import TestClient

    with TestClient(server.app) as client:
        http_pkg = client.get("/api/packages/docx").json()

    # Both transports must see the same metadata shape.
    assert http_pkg["name"] == ssh_pkg["name"] == "docx"
    assert http_pkg["latest"] == ssh_pkg["latest"] == "1.0.0"
    assert (
        http_pkg["metadata"]["published_by"]
        == ssh_pkg["metadata"]["published_by"]
        == "alice"
    )


# ---------------------------------------------------------------------------
def _write_zip(tmp_path: Path, data: bytes) -> Path:
    p = tmp_path / f"pkg-{abs(hash(data)) & 0xFFFFFFFF:08x}.zip"
    p.write_bytes(data)
    return p
