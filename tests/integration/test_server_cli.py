"""Integration tests for registry/server_cli.py.

Exercises each verb end-to-end through a real subprocess boundary
(``python server_cli.py <verb>``). This is the same transport boundary
that ``ssh user@host skilltool-server <verb>`` crosses in production,
minus the SSH hop.
"""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SERVER_CLI = _REPO / "registry" / "server_cli.py"


@pytest.fixture()
def registry_env(tmp_path, monkeypatch) -> dict[str, str]:
    """Isolated storage + users.toml for each test, returned as env vars."""
    storage = tmp_path / "data"
    (storage / "packages").mkdir(parents=True)
    users_file = storage / "users.toml"
    users_file.write_text(
        textwrap.dedent(
            """
            [users.alice]
            token = "tok_alice_deadbeef"
            teams = ["team-doc"]

            [users.bob]
            token = "tok_bob_cafebabe"
            teams = ["team-other"]
            disabled = true
            """
        ).strip(),
        encoding="utf-8",
    )
    audit_log = storage / "publish.log"
    env = {
        **os.environ,
        "SKILLTOOL_STORAGE_DIR": str(storage),
        "SKILLTOOL_USERS_FILE": str(users_file),
        "SKILLTOOL_AUDIT_LOG": str(audit_log),
    }
    return env


def _run(
    verb: str,
    *args: str,
    env: dict[str, str],
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SERVER_CLI), verb, *args],
        input=input_bytes,
        capture_output=True,
        env=env,
        check=False,
    )


def _make_skill_zip(name: str, version: str, description: str = "desc") -> bytes:
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
def test_list_on_empty_registry(registry_env) -> None:
    r = _run("list", env=registry_env)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == []


def test_publish_then_list_show_and_search(registry_env) -> None:
    payload_b64 = base64.b64encode(_make_skill_zip("docx", "1.0.0", "write docx")).decode()

    r = _run(
        "publish",
        "--token", "tok_alice_deadbeef",
        "--data", payload_b64,
        env=registry_env,
    )
    assert r.returncode == 0, r.stderr
    published = json.loads(r.stdout)
    assert published["published_by"] == "alice"
    assert published["version"] == "1.0.0"
    assert published["published_at"].endswith("Z")

    # list
    r = _run("list", env=registry_env)
    entries = json.loads(r.stdout)
    assert [e["name"] for e in entries] == ["docx"]
    assert entries[0]["author"] == "test-author"

    # search
    r = _run("search", "do(c|x)", env=registry_env)
    results = json.loads(r.stdout)
    assert [e["name"] for e in results] == ["docx"]

    r = _run("search", "nothingmatches", env=registry_env)
    assert json.loads(r.stdout) == []

    # show
    r = _run("show", "docx", env=registry_env)
    info = json.loads(r.stdout)
    assert info["latest"] == "1.0.0"
    assert info["metadata"]["published_by"] == "alice"

    # audit
    r = _run("audit", env=registry_env)
    audit = json.loads(r.stdout)
    assert audit["total"] == 1
    assert audit["entries"][0]["user"] == "alice"
    assert audit["entries"][0]["package"] == "docx"


def test_publish_via_stdin_matches_base64_path(registry_env) -> None:
    payload = _make_skill_zip("from-stdin", "0.1.0")

    r = _run(
        "publish",
        "--token", "tok_alice_deadbeef",
        env=registry_env,
        input_bytes=payload,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["name"] == "from-stdin"


def test_download_returns_raw_bytes(registry_env) -> None:
    zip_bytes = _make_skill_zip("docx", "1.0.0")
    # publish first
    _run(
        "publish",
        "--token", "tok_alice_deadbeef",
        "--data", base64.b64encode(zip_bytes).decode(),
        env=registry_env,
    )

    r = _run("download", "docx", env=registry_env)
    assert r.returncode == 0, r.stderr
    # server returns the stored zip bytes verbatim
    with zipfile.ZipFile(io.BytesIO(r.stdout)) as zf:
        assert "skill.md" in zf.namelist()


def test_publish_rejects_invalid_token(registry_env) -> None:
    payload_b64 = base64.b64encode(_make_skill_zip("x", "1.0.0")).decode()
    r = _run(
        "publish",
        "--token", "tok_nobody_ffff",
        "--data", payload_b64,
        env=registry_env,
    )
    assert r.returncode != 0
    err = json.loads(r.stderr)
    assert "invalid" in err["error"].lower() or "revoked" in err["error"].lower()


def test_publish_rejects_disabled_user(registry_env) -> None:
    payload_b64 = base64.b64encode(_make_skill_zip("x", "1.0.0")).decode()
    r = _run(
        "publish",
        "--token", "tok_bob_cafebabe",
        "--data", payload_b64,
        env=registry_env,
    )
    assert r.returncode != 0
    assert "revoked" in r.stderr.decode().lower() or "invalid" in r.stderr.decode().lower()


def test_duplicate_version_is_rejected(registry_env) -> None:
    payload_b64 = base64.b64encode(_make_skill_zip("docx", "1.0.0")).decode()
    r1 = _run(
        "publish",
        "--token", "tok_alice_deadbeef",
        "--data", payload_b64,
        env=registry_env,
    )
    assert r1.returncode == 0, r1.stderr
    r2 = _run(
        "publish",
        "--token", "tok_alice_deadbeef",
        "--data", payload_b64,
        env=registry_env,
    )
    assert r2.returncode != 0
    err_text = r2.stderr.decode()
    assert "already exists" in err_text


def test_unknown_verb_errors_cleanly(registry_env) -> None:
    r = _run("nope", env=registry_env)
    assert r.returncode != 0
    err = json.loads(r.stderr)
    assert "unknown verb" in err["error"]


def test_stdout_is_only_json_for_text_verbs(registry_env) -> None:
    # Task003 §8: stdout must not contain logs / warnings for text verbs.
    r = _run("list", env=registry_env)
    assert r.returncode == 0
    # exactly one JSON document + trailing newline
    assert r.stdout.endswith(b"\n")
    assert json.loads(r.stdout) == []
    # Any chatter should appear on stderr (empty for a clean list call).
    assert r.stderr == b""
