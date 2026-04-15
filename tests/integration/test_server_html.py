"""Integration tests for the registry's browser-facing HTML routes.

Covers the feature additions:
  * `/` now has a Name / Tag / Description search form
  * `/` renders a Tags column
  * `/packages/<name>` shows a `Published at` column for each version
"""
from __future__ import annotations

import io
import os
import textwrap
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _load_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage = tmp_path / "data"
    (storage / "packages").mkdir(parents=True)
    users_file = storage / "users.toml"
    users_file.write_text(
        textwrap.dedent(
            """
            [users.alice]
            token = "tok_alice_html"
            teams = ["team-doc"]
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("SKILLTOOL_STORAGE_DIR", str(storage))
    monkeypatch.setenv("SKILLTOOL_USERS_FILE", str(users_file))
    monkeypatch.setenv("SKILLTOOL_AUDIT_LOG", str(storage / "publish.log"))

    # Re-import server fresh so the module picks up the env vars.
    import importlib
    import sys

    for mod in [m for m in sys.modules if m == "server" or m.startswith("server.")]:
        del sys.modules[mod]
    return importlib.import_module("server")


def _make_toml_zip(
    name: str,
    version: str,
    *,
    description: str,
    tags: list[str] | None = None,
    author: str = "team-doc",
) -> bytes:
    buf = io.BytesIO()
    toml = [
        "[skill]",
        f'name = "{name}"',
        f'version = "{version}"',
        f'description = "{description}"',
        f'author = "{author}"',
        'entry = "SKILL.md"',
    ]
    if tags is not None:
        toml.append("tags = [" + ", ".join(f'"{t}"' for t in tags) + "]")
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("skill.toml", "\n".join(toml) + "\n")
        zf.writestr("SKILL.md", f"# {name}\n\nbody\n")
    return buf.getvalue()


@pytest.fixture()
def populated_client(tmp_path, monkeypatch):
    """A TestClient against the FastAPI app with three packages published."""
    server = _load_server(tmp_path, monkeypatch)
    client = TestClient(server.app)

    # docx  — tagged office, word
    client.post(
        "/api/publish",
        headers={"Authorization": "Bearer tok_alice_html"},
        files={
            "file": (
                "docx.zip",
                _make_toml_zip(
                    "docx",
                    "1.0.0",
                    description="Author Office documents from Claude",
                    tags=["office", "word"],
                ),
                "application/zip",
            )
        },
    )
    # docx — second version
    client.post(
        "/api/publish",
        headers={"Authorization": "Bearer tok_alice_html"},
        files={
            "file": (
                "docx-2.zip",
                _make_toml_zip(
                    "docx",
                    "1.1.0",
                    description="Author Office documents from Claude",
                    tags=["office", "word"],
                ),
                "application/zip",
            )
        },
    )
    # pdf — tagged office, pdf
    client.post(
        "/api/publish",
        headers={"Authorization": "Bearer tok_alice_html"},
        files={
            "file": (
                "pdf.zip",
                _make_toml_zip(
                    "pdf",
                    "0.3.0",
                    description="Inspect PDF files",
                    tags=["office", "pdf"],
                ),
                "application/zip",
            )
        },
    )
    # forecast — tagged forecasting, causal (no office tag)
    client.post(
        "/api/publish",
        headers={"Authorization": "Bearer tok_alice_html"},
        files={
            "file": (
                "forecast.zip",
                _make_toml_zip(
                    "forecast",
                    "1.0.0",
                    description="Time series utilities",
                    tags=["forecasting", "causal"],
                ),
                "application/zip",
            )
        },
    )
    yield client
    client.close()


# ---------------------------------------------------------------------------
# /  — search form + tags column
# ---------------------------------------------------------------------------
def test_home_renders_search_form_and_tags_column(populated_client):
    r = populated_client.get("/")
    assert r.status_code == 200
    html = r.text

    # Form has the 3 inputs we promised
    assert 'name="name"' in html
    assert 'name="tag"' in html
    assert 'name="description"' in html

    # Tags column header and at least one tag badge link rendered
    assert "<th>Tags</th>" in html
    assert 'class="tag"' in html
    assert "office" in html and "pdf" in html


def test_home_filter_by_name_regex(populated_client):
    r = populated_client.get("/", params={"name": "^doc"})
    html = r.text
    assert "docx" in html
    assert "pdf" not in _body_table(html)
    assert "forecast" not in _body_table(html)


def test_home_filter_by_tag_matches_any_tag(populated_client):
    r = populated_client.get("/", params={"tag": "^office$"})
    body = _body_table(r.text)
    # office tag is on docx and pdf
    assert "docx" in body and "pdf" in body
    assert "forecast" not in body


def test_home_filter_by_description(populated_client):
    r = populated_client.get("/", params={"description": "time series"})
    body = _body_table(r.text)
    assert "forecast" in body
    assert "docx" not in body


def test_home_filters_combine_as_and(populated_client):
    # office tag AND name starting with p → only pdf
    r = populated_client.get(
        "/",
        params={"tag": "^office$", "name": "^p"},
    )
    body = _body_table(r.text)
    assert "pdf" in body
    assert "docx" not in body


def test_home_invalid_regex_renders_error_not_500(populated_client):
    r = populated_client.get("/", params={"name": "["})  # unterminated char class
    assert r.status_code == 200
    assert "invalid regex" in r.text
    # Should still render all packages since the filter didn't compile
    assert "docx" in r.text


def test_home_form_preserves_current_values(populated_client):
    r = populated_client.get("/", params={"name": "docx", "tag": "office"})
    html = r.text
    assert 'value="docx"' in html
    assert 'value="office"' in html


def test_home_tag_link_survives_round_trip(populated_client):
    """Clicking a tag badge links to /?tag=<tag> — reverse round-trip works."""
    r = populated_client.get("/", params={"tag": "pdf"})
    body = _body_table(r.text)
    # Only the pdf package has a "pdf" tag
    assert "pdf" in body
    assert "docx" not in body


# ---------------------------------------------------------------------------
# /packages/{name} — per-version published_at column
# ---------------------------------------------------------------------------
def test_package_page_shows_published_at_column(populated_client):
    r = populated_client.get("/packages/docx")
    assert r.status_code == 200
    html = r.text

    assert "<th>Version</th>" in html
    assert "<th>Published at</th>" in html
    # Both versions present, each with a timestamp
    assert "1.0.0" in html
    assert "1.1.0" in html
    # ISO timestamps end in Z
    assert html.count("Z</td>") >= 2
    # publisher column shows alice for both
    assert html.count("alice") >= 2


def test_package_page_shows_tags(populated_client):
    html = populated_client.get("/packages/docx").text
    assert "<strong>Tags:</strong>" in html
    assert "office" in html
    assert "word" in html


def test_package_page_download_link_uses_urlquoted_version(populated_client):
    """Versions may legitimately contain `+` (build metadata); the download
    link must URL-encode them."""
    # Publish a build-metadata version
    server = populated_client.app
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "skill.toml",
            '[skill]\nname = "plus"\nversion = "1.0.0+build.5"\ndescription = "x"\n',
        )
        zf.writestr("SKILL.md", "body")
    r = populated_client.post(
        "/api/publish",
        headers={"Authorization": "Bearer tok_alice_html"},
        files={"file": ("plus.zip", buf.getvalue(), "application/zip")},
    )
    assert r.status_code == 200, r.text

    r = populated_client.get("/packages/plus")
    assert r.status_code == 200
    # `+` must be URL-encoded to %2B in the href
    assert "1.0.0%2Bbuild.5" in r.text


# ---------------------------------------------------------------------------
# /api/search — per-field parameters
# ---------------------------------------------------------------------------
def test_api_search_by_tag_only(populated_client):
    r = populated_client.get("/api/search", params={"tag": "forecasting"})
    assert r.status_code == 200
    names = [e["name"] for e in r.json()["results"]]
    assert names == ["forecast"]


def test_api_search_by_name_only(populated_client):
    r = populated_client.get("/api/search", params={"name": "^p"})
    names = [e["name"] for e in r.json()["results"]]
    assert names == ["pdf"]


def test_api_search_combines_name_and_tag(populated_client):
    r = populated_client.get(
        "/api/search", params={"name": "^p", "tag": "^office$"}
    )
    names = [e["name"] for e in r.json()["results"]]
    assert names == ["pdf"]


def test_api_search_legacy_q_still_works(populated_client):
    r = populated_client.get("/api/search", params={"q": "PDF"})
    names = [e["name"] for e in r.json()["results"]]
    assert names == ["pdf"]


def test_api_search_rejects_empty_request(populated_client):
    r = populated_client.get("/api/search")
    assert r.status_code == 400
    assert "required" in r.json()["detail"]


def test_api_search_returns_tags_in_each_result(populated_client):
    r = populated_client.get("/api/search", params={"tag": "office"})
    results = r.json()["results"]
    assert results, "expected at least one match for office"
    for row in results:
        assert isinstance(row.get("tags"), list)
        assert "office" in row["tags"]


def test_api_search_invalid_regex_is_400(populated_client):
    r = populated_client.get("/api/search", params={"name": "["})
    assert r.status_code == 400
    assert "invalid" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _body_table(html: str) -> str:
    """Pull out just the <tbody>...</tbody> so header text (e.g. 'Name')
    doesn't accidentally match a test assertion about which rows rendered."""
    start = html.rfind("<tbody>")
    end = html.rfind("</tbody>")
    assert start != -1 and end != -1, html
    return html[start:end]
