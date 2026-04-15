"""Server-side integration tests for the skill.toml manifest format.

These hit the real subprocess boundary of ``registry/server_cli.py``
(identical to the SSH transport path minus the ssh hop), so they cover
both metadata extraction in server.py and the publish_logic → audit
roundtrip.
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
def registry_env(tmp_path) -> dict[str, str]:
    storage = tmp_path / "data"
    (storage / "packages").mkdir(parents=True)
    users_file = storage / "users.toml"
    users_file.write_text(
        textwrap.dedent(
            """
            [users.alice]
            token = "tok_alice_toml"
            teams = ["team-doc"]
            """
        ).strip(),
        encoding="utf-8",
    )
    return {
        **os.environ,
        "SKILLTOOL_STORAGE_DIR": str(storage),
        "SKILLTOOL_USERS_FILE": str(users_file),
        "SKILLTOOL_AUDIT_LOG": str(storage / "publish.log"),
    }


def _run(verb: str, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SERVER_CLI), verb, *args],
        capture_output=True,
        env=env,
        check=False,
    )


def _make_toml_zip(
    name: str,
    version: str,
    *,
    description: str = "from toml",
    author: str = "team-doc",
    entry: str = "SKILL.md",
    include: list[str] | None = None,
    extra_files: dict[str, str] | None = None,
) -> bytes:
    """Craft a zip whose manifest is skill.toml (task004 format)."""
    buf = io.BytesIO()
    toml = [
        "[skill]",
        f'name = "{name}"',
        f'version = "{version}"',
        f'description = "{description}"',
        f'author = "{author}"',
        f'entry = "{entry}"',
    ]
    if include is not None:
        toml.append("include = [" + ", ".join(f'"{i}"' for i in include) + "]")
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("skill.toml", "\n".join(toml) + "\n")
        zf.writestr(entry, f"# {name}\n\nbody\n")
        for path, content in (extra_files or {}).items():
            zf.writestr(path, content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
def test_publish_skill_toml_happy_path(registry_env):
    payload = base64.b64encode(
        _make_toml_zip("tomlpkg", "1.0.0", description="hello")
    ).decode()

    r = _run(
        "publish",
        "--token", "tok_alice_toml",
        "--data", payload,
        env=registry_env,
    )
    assert r.returncode == 0, r.stderr
    published = json.loads(r.stdout)
    assert published["name"] == "tomlpkg"
    assert published["version"] == "1.0.0"
    assert published["published_by"] == "alice"

    # metadata sidecar carries the toml-specific fields
    r = _run("show", "tomlpkg", env=registry_env)
    info = json.loads(r.stdout)
    meta = info["metadata"]
    assert meta["manifest_format"] == "skill.toml"
    assert meta["entry"] == "SKILL.md"
    assert meta["description"] == "hello"
    assert meta["author"] == "team-doc"


def test_publish_skill_toml_preserves_extra_keys(registry_env):
    """Unknown keys under [skill] should ride through to the sidecar."""
    # Build zip manually with extras we don't model upfront.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "skill.toml",
            textwrap.dedent(
                """
                [skill]
                name        = "extras"
                version     = "1.0.0"
                description = "with extras"
                tags        = ["pdf", "word"]
                homepage    = "https://example.com"
                """
            ).strip(),
        )
        zf.writestr("SKILL.md", "body")

    r = _run(
        "publish",
        "--token", "tok_alice_toml",
        "--data", base64.b64encode(buf.getvalue()).decode(),
        env=registry_env,
    )
    assert r.returncode == 0, r.stderr

    r = _run("show", "extras", env=registry_env)
    meta = json.loads(r.stdout)["metadata"]
    assert meta["tags"] == ["pdf", "word"]
    assert meta["homepage"] == "https://example.com"


def test_publish_skill_toml_missing_required_field_rejected(registry_env):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "skill.toml",
            '[skill]\nname = "nope"\nversion = "1.0.0"\n',  # description missing
        )
        zf.writestr("SKILL.md", "body")

    r = _run(
        "publish",
        "--token", "tok_alice_toml",
        "--data", base64.b64encode(buf.getvalue()).decode(),
        env=registry_env,
    )
    assert r.returncode != 0
    err = json.loads(r.stderr)
    assert "description" in err["error"]


def test_publish_skill_toml_invalid_name_rejected(registry_env):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "skill.toml",
            textwrap.dedent(
                """
                [skill]
                name        = "HAS UPPER CASE"
                version     = "1.0.0"
                description = "d"
                """
            ).strip(),
        )
        zf.writestr("SKILL.md", "body")

    r = _run(
        "publish",
        "--token", "tok_alice_toml",
        "--data", base64.b64encode(buf.getvalue()).decode(),
        env=registry_env,
    )
    assert r.returncode != 0
    assert "invalid package name" in r.stderr.decode()


def test_publish_skill_toml_takes_precedence_over_skill_md(registry_env):
    """If both manifests ship in the same zip, skill.toml wins."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "skill.toml",
            textwrap.dedent(
                """
                [skill]
                name        = "tomlwin"
                version     = "1.0.0"
                description = "from toml"
                """
            ).strip(),
        )
        zf.writestr(
            "skill.md",
            textwrap.dedent(
                """
                ---
                name: shouldloose
                version: 9.9.9
                description: from md
                ---
                """
            ).lstrip(),
        )

    r = _run(
        "publish",
        "--token", "tok_alice_toml",
        "--data", base64.b64encode(buf.getvalue()).decode(),
        env=registry_env,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["name"] == "tomlwin"


def test_publish_skill_md_still_works_backward_compat(registry_env):
    """Explicit regression for the skill.md frontmatter path."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "skill.md",
            textwrap.dedent(
                """
                ---
                name: legacy
                version: 1.0.0
                description: legacy frontmatter
                author: team-old
                ---

                body
                """
            ).lstrip(),
        )

    r = _run(
        "publish",
        "--token", "tok_alice_toml",
        "--data", base64.b64encode(buf.getvalue()).decode(),
        env=registry_env,
    )
    assert r.returncode == 0, r.stderr

    r = _run("show", "legacy", env=registry_env)
    meta = json.loads(r.stdout)["metadata"]
    assert meta["manifest_format"] == "skill.md"
    assert meta["author"] == "team-old"


def test_publish_no_manifest_at_all_rejected(registry_env):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("random.txt", "nothing here")

    r = _run(
        "publish",
        "--token", "tok_alice_toml",
        "--data", base64.b64encode(buf.getvalue()).decode(),
        env=registry_env,
    )
    assert r.returncode != 0
    err = json.loads(r.stderr)
    assert "manifest" in err["error"].lower()


def test_publish_skill_toml_with_bare_skill_md_no_frontmatter(registry_env):
    """skill.md need not carry frontmatter when skill.toml is present.

    The server must accept the zip and extract metadata from skill.toml
    only — the fact that skill.md is pure narrative markdown is fine.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "skill.toml",
            textwrap.dedent(
                """
                [skill]
                name        = "bare"
                version     = "1.0.0"
                description = "no frontmatter on SKILL.md"
                entry       = "SKILL.md"
                """
            ).strip(),
        )
        zf.writestr(
            "SKILL.md",
            "# bare skill\n\nJust narrative. No YAML frontmatter.\n",
        )

    r = _run(
        "publish",
        "--token", "tok_alice_toml",
        "--data", base64.b64encode(buf.getvalue()).decode(),
        env=registry_env,
    )
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout)
    assert body["name"] == "bare"
    assert body["version"] == "1.0.0"


def test_publish_skill_toml_with_broken_frontmatter_on_skill_md(registry_env):
    """Even if skill.md happens to have malformed YAML frontmatter, the
    server must not trip on it when skill.toml is the source of truth."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "skill.toml",
            textwrap.dedent(
                """
                [skill]
                name        = "resilient"
                version     = "1.0.0"
                description = "toml is the source"
                entry       = "skill.md"
                """
            ).strip(),
        )
        # Deliberately malformed frontmatter — the server should never read it.
        zf.writestr(
            "skill.md",
            "---\nnot: [valid\nyaml: }\n---\n\n# still publishes\n",
        )

    r = _run(
        "publish",
        "--token", "tok_alice_toml",
        "--data", base64.b64encode(buf.getvalue()).decode(),
        env=registry_env,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["name"] == "resilient"


def test_publish_skill_toml_conflicting_fields_all_resolve_to_toml(registry_env):
    """Every field specified in both must resolve to skill.toml's value."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "skill.toml",
            textwrap.dedent(
                """
                [skill]
                name        = "truth"
                version     = "1.0.0"
                description = "from toml"
                author      = "team-toml"
                """
            ).strip(),
        )
        zf.writestr(
            "SKILL.md",
            textwrap.dedent(
                """
                ---
                name: impostor
                version: 9.9.9
                description: from md
                author: team-md
                ---

                body
                """
            ).lstrip(),
        )

    r = _run(
        "publish",
        "--token", "tok_alice_toml",
        "--data", base64.b64encode(buf.getvalue()).decode(),
        env=registry_env,
    )
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout)
    assert body["name"] == "truth"
    assert body["version"] == "1.0.0"

    r = _run("show", "truth", env=registry_env)
    meta = json.loads(r.stdout)["metadata"]
    assert meta["description"] == "from toml"
    assert meta["author"] == "team-toml"
    assert meta["manifest_format"] == "skill.toml"
