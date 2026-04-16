"""skilltool registry server.

FastAPI app that backs ``skilltool`` CLI. Storage is filesystem-backed:

    <storage>/packages/<name>/<version>.zip     # payload
    <storage>/packages/<name>/<version>.yaml    # metadata (frontmatter + audit)

Authentication
--------------
Authentication is per-user. The server loads a ``users.toml`` file on each
authenticated request and resolves the caller from their bearer token:

    [users.alice]
    token    = "tok_alice_…"
    teams    = ["team-doc"]
    # disabled = true   # revoke

``POST /api/publish`` requires a valid (non-disabled) user. Reads
(``/api/packages``, ``/api/search``, ``/``) are unauthenticated — the
server is expected to sit behind a Tailscale perimeter.

``GET /api/audit`` accepts the token via ``Authorization: Bearer`` header
or a ``?token=`` query parameter for curl ergonomics.
"""
from __future__ import annotations

import datetime as _dt
import io
import os
import re
import tomllib
import zipfile
from html import escape
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote as urlquote

DEFAULT_ENTRY = "SKILL.md"

import yaml
from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
STORAGE_DIR = Path(os.environ.get("SKILLTOOL_STORAGE_DIR", "/data"))
PACKAGES_DIR = STORAGE_DIR / "packages"
USERS_FILE = Path(
    os.environ.get("SKILLTOOL_USERS_FILE", str(STORAGE_DIR / "users.toml"))
)
AUDIT_LOG = Path(
    os.environ.get("SKILLTOOL_AUDIT_LOG", str(STORAGE_DIR / "publish.log"))
)

PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


app = FastAPI(
    title="skilltool-registry",
    summary="PyPI-like registry for skill.md packages.",
    version="0.2.0",
)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def _load_users() -> dict[str, dict[str, Any]]:
    if not USERS_FILE.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"users.toml not found at {USERS_FILE}",
        )
    try:
        with USERS_FILE.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise HTTPException(500, f"users.toml is not valid TOML: {exc}")
    return data.get("users", {}) or {}


def resolve_user(token: str) -> dict[str, Any]:
    """Return ``{name, teams, ...}`` for the user owning ``token``.

    Raises 401 if no user matches or the user is disabled.
    """
    if not token:
        raise HTTPException(401, "missing token")
    users = _load_users()
    for name, meta in users.items():
        if not isinstance(meta, dict):
            continue
        if meta.get("token") == token and not meta.get("disabled", False):
            return {"name": name, **meta}
    raise HTTPException(401, "invalid or revoked token")


def _authenticate(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None, description="Fallback when no header is set."),
) -> dict[str, Any]:
    """FastAPI dependency: resolve the caller from header or query token."""
    if authorization and authorization.startswith("Bearer "):
        raw = authorization[len("Bearer ") :].strip()
    elif token:
        raw = token
    else:
        raise HTTPException(401, "missing bearer token")
    return resolve_user(raw)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
_AUDIT_RE = re.compile(
    r"^(?P<ts>\S+)\s+(?P<user>\S+)\s+(?P<package>\S+)\s+(?P<detail>.+)$"
)


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_publish(user: str, package: str, *, old_version: str | None, new_version: str) -> str:
    """Append a publish event to the audit log and return the line written."""
    detail = (
        f"{new_version} (new)"
        if old_version is None
        else f"{old_version} → {new_version}"
    )
    ts = _utc_now_iso()
    line = f"{ts}  {user:<16} {package:<20} {detail}\n"
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line)
    return line


def _parse_audit_line(line: str) -> dict[str, Any]:
    match = _AUDIT_RE.match(line)
    if not match:
        return {"raw": line}
    return {"raw": line, **match.groupdict()}


# ---------------------------------------------------------------------------
# Package helpers
# ---------------------------------------------------------------------------
def _version_key(v: str) -> tuple[Any, ...]:
    """Best-effort semver-ish sort key."""
    parts = v.split("-", 1)
    base = parts[0].split(".")
    try:
        numeric = tuple(int(p) for p in base)
    except ValueError:
        numeric = tuple(base)
    prerelease = (0, parts[1]) if len(parts) == 2 else (1,)
    return (numeric, prerelease)


def list_versions(name: str) -> list[str]:
    pkg_dir = PACKAGES_DIR / name
    if not pkg_dir.is_dir():
        return []
    return sorted(
        (p.stem for p in pkg_dir.glob("*.zip")),
        key=_version_key,
    )


def load_manifest(name: str, version: str) -> dict[str, Any]:
    meta_path = PACKAGES_DIR / name / f"{version}.yaml"
    if meta_path.is_file():
        return yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
    return {"name": name, "version": version}


def iter_packages() -> Iterable[tuple[str, list[str]]]:
    if not PACKAGES_DIR.exists():
        return
    for entry in sorted(PACKAGES_DIR.iterdir()):
        if not entry.is_dir():
            continue
        versions = list_versions(entry.name)
        if versions:
            yield entry.name, versions


def _normalize_tags(raw: Any) -> list[str]:
    """Coerce ``tags`` from the manifest into a clean ``list[str]``."""
    if isinstance(raw, list):
        return [str(t) for t in raw if isinstance(t, (str, int, float))]
    if isinstance(raw, str):
        return [raw]
    return []


def all_packages() -> list[dict[str, Any]]:
    """Summary record for every package in the registry.

    Used by ``/api/search`` (when a match-all regex is supplied) and by the
    ``list`` verb of ``server_cli.py``.
    """
    out: list[dict[str, Any]] = []
    for name, versions in iter_packages():
        meta = load_manifest(name, versions[-1])
        out.append(
            {
                "name": name,
                "latest": versions[-1],
                "description": meta.get("description", ""),
                "author": meta.get("author", ""),
                "tags": _normalize_tags(meta.get("tags")),
                "published_at": str(meta.get("published_at", "") or ""),
                "published_by": str(meta.get("published_by", "") or ""),
            }
        )
    return out


def version_entries(name: str) -> list[dict[str, Any]]:
    """Per-version publish info for a package (newest first)."""
    entries: list[dict[str, Any]] = []
    for v in reversed(list_versions(name)):
        vmeta = load_manifest(name, v)
        entries.append(
            {
                "version": v,
                "published_at": str(vmeta.get("published_at", "") or ""),
                "published_by": str(vmeta.get("published_by", "") or ""),
            }
        )
    return entries


def _find_manifest(names: list[str], filename: str) -> str | None:
    """Return the archive entry for ``filename`` at root or one dir deep.

    Exact basename match; skips any deeper paths so e.g. ``scripts/skill.toml``
    inside a larger archive won't be picked up as the manifest.
    """
    for n in names:
        depth = n.count("/")
        if depth > 1:
            continue
        base = n.rsplit("/", 1)[-1]
        if base == filename:
            return n
    return None


def _validate_identity(meta: dict[str, Any]) -> tuple[str, str]:
    for required in ("name", "version", "description"):
        if required not in meta:
            raise HTTPException(
                400, f"manifest missing required field '{required}'"
            )
    name = str(meta["name"])
    version = str(meta["version"])
    if not _NAME_RE.match(name):
        raise HTTPException(400, f"invalid package name: {name!r}")
    if not _VERSION_RE.match(version):
        raise HTTPException(400, f"invalid version: {version!r}")
    return name, version


def _parse_skill_toml(content: str) -> dict[str, Any]:
    try:
        parsed = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise HTTPException(400, f"skill.toml: invalid TOML: {exc}")
    skill = parsed.get("skill")
    if not isinstance(skill, dict):
        raise HTTPException(400, "skill.toml: missing [skill] table")

    name, version = _validate_identity(skill)
    include = skill.get("include")
    if include is not None:
        if not isinstance(include, list) or not all(
            isinstance(x, str) for x in include
        ):
            raise HTTPException(
                400, "skill.toml: skill.include must be a list of strings"
            )

    result: dict[str, Any] = {
        "name": name,
        "version": version,
        "description": str(skill["description"]),
        "author": str(skill["author"]) if "author" in skill else "",
        "entry": str(skill.get("entry", DEFAULT_ENTRY)),
        "manifest_format": "skill.toml",
    }
    # Preserve any extra keys the user put under [skill] for future use —
    # tags, homepage, etc. — but don't let them overwrite the canonical ones.
    for k, v in skill.items():
        if k in {"name", "version", "description", "author", "entry"}:
            continue
        result.setdefault(k, v)
    return result


def _parse_skill_md_frontmatter(content: str) -> dict[str, Any]:
    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise HTTPException(400, "skill.md is missing YAML frontmatter")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(400, f"invalid frontmatter YAML: {exc}")

    name, version = _validate_identity(meta)
    result: dict[str, Any] = {
        "name": name,
        "version": version,
        "description": str(meta.get("description", "")),
        "author": str(meta["author"]) if "author" in meta else "",
        "manifest_format": "skill.md",
    }
    for k, v in meta.items():
        if k in {"name", "version", "description", "author"}:
            continue
        result.setdefault(k, v)
    return result


def extract_skill_metadata(zip_bytes: bytes) -> dict[str, Any]:
    """Parse package metadata from an uploaded zip.

    Prefers ``skill.toml`` (task004) and falls back to the legacy
    ``skill.md`` YAML frontmatter so existing packages keep working.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise HTTPException(400, f"invalid zip: {exc}")

    with zf:
        names = zf.namelist()
        skill_toml = _find_manifest(names, "skill.toml")
        if skill_toml is not None:
            content = zf.read(skill_toml).decode("utf-8", errors="replace")
            return _parse_skill_toml(content)

        for candidate in ("skill.md", "SKILL.md"):
            path = _find_manifest(names, candidate)
            if path is not None:
                content = zf.read(path).decode("utf-8", errors="replace")
                return _parse_skill_md_frontmatter(content)

    raise HTTPException(
        400,
        "no manifest found: expected skill.toml (preferred) or "
        "skill.md at archive root",
    )


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/api/packages/{name}")
def package_info(name: str) -> JSONResponse:
    versions = list_versions(name)
    if not versions:
        raise HTTPException(404, f"package '{name}' not found")
    latest = versions[-1]
    return JSONResponse(
        {
            "name": name,
            "versions": versions,
            "latest": latest,
            "metadata": load_manifest(name, latest),
        }
    )


@app.get("/api/packages/{name}/download")
def download(name: str, version: str | None = None) -> FileResponse:
    versions = list_versions(name)
    if not versions:
        raise HTTPException(404, f"package '{name}' not found")
    v = version or versions[-1]
    path = PACKAGES_DIR / name / f"{v}.zip"
    if not path.is_file():
        raise HTTPException(404, f"version '{v}' not found for '{name}'")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"{name}-{v}.zip",
    )


def _compile_or_400(pattern: str | None, label: str) -> re.Pattern[str] | None:
    if pattern is None or pattern == "":
        return None
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise HTTPException(400, f"invalid {label} regex: {exc}")


def _filter_packages(
    entries: list[dict[str, Any]],
    *,
    q: re.Pattern[str] | None = None,
    name: re.Pattern[str] | None = None,
    tag: re.Pattern[str] | None = None,
    description: re.Pattern[str] | None = None,
) -> list[dict[str, Any]]:
    """AND-combined filter: every provided pattern must match."""
    out: list[dict[str, Any]] = []
    for entry in entries:
        if q and not q.search(f"{entry['name']} {entry.get('description', '')}"):
            continue
        if name and not name.search(entry["name"]):
            continue
        if description and not description.search(
            entry.get("description", "") or ""
        ):
            continue
        if tag:
            tags = entry.get("tags", []) or []
            if not any(tag.search(t) for t in tags):
                continue
        out.append(entry)
    return out


@app.get("/api/search")
def search(
    q: str | None = Query(
        default=None,
        description="Legacy: regex matched against name + description.",
    ),
    name: str | None = Query(
        default=None, description="Regex against package name only."
    ),
    tag: str | None = Query(
        default=None, description="Regex against any one of the package's tags."
    ),
    description: str | None = Query(
        default=None, description="Regex against description only."
    ),
) -> dict[str, Any]:
    patterns = {
        "q": _compile_or_400(q, "q"),
        "name": _compile_or_400(name, "name"),
        "tag": _compile_or_400(tag, "tag"),
        "description": _compile_or_400(description, "description"),
    }
    # No filter at all → keep old 400-on-empty contract for /api/search to
    # avoid accidentally exfiltrating the whole registry.
    if not any(patterns.values()):
        raise HTTPException(
            400, "at least one of q / name / tag / description is required"
        )
    results = _filter_packages(all_packages(), **patterns)
    return {"results": results}


class PublishError(Exception):
    """Raised by ``publish_logic`` for user-visible failures.

    ``status`` is mapped to an HTTP code by the FastAPI handler and is
    printed as part of ``server_cli.py``'s JSON error output.
    """

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def publish_logic(token: str, zip_bytes: bytes) -> dict[str, Any]:
    """Core publish pipeline — shared by the FastAPI handler and server_cli.py.

    Resolves the caller, validates + stores the package, appends to the
    audit log, and returns the JSON response body.
    """
    try:
        user = resolve_user(token)
    except HTTPException as exc:
        raise PublishError(exc.status_code, str(exc.detail)) from None

    try:
        meta = extract_skill_metadata(zip_bytes)
    except HTTPException as exc:
        raise PublishError(exc.status_code, str(exc.detail)) from None

    name, version = meta["name"], meta["version"]

    pkg_dir = PACKAGES_DIR / name
    pkg_dir.mkdir(parents=True, exist_ok=True)
    zip_path = pkg_dir / f"{version}.zip"
    if zip_path.exists():
        raise PublishError(
            409, f"{name} {version} already exists; publish a new version"
        )

    existing_versions = list_versions(name)
    previous_latest = existing_versions[-1] if existing_versions else None

    published_at = _utc_now_iso()
    meta["published_by"] = user["name"]
    meta["published_at"] = published_at
    if user.get("teams"):
        meta.setdefault("published_teams", list(user["teams"]))

    zip_path.write_bytes(zip_bytes)
    (pkg_dir / f"{version}.yaml").write_text(
        yaml.safe_dump(meta, sort_keys=False), encoding="utf-8"
    )

    log_publish(user["name"], name, old_version=previous_latest, new_version=version)

    return {
        "name": name,
        "version": version,
        "status": "published",
        "published_by": user["name"],
        "published_at": published_at,
    }


@app.post("/api/publish")
async def publish(
    file: UploadFile = File(...),
    user: dict = Depends(_authenticate),
) -> dict[str, Any]:
    payload = await file.read()
    try:
        return publish_logic(user["token"], payload)
    except PublishError as exc:
        raise HTTPException(exc.status, exc.detail) from None


@app.get("/api/audit")
def get_audit(
    user: dict = Depends(_authenticate),
    limit: int = Query(default=50, ge=1, le=1000),
) -> dict[str, Any]:
    if not AUDIT_LOG.exists():
        return {"entries": []}
    lines = AUDIT_LOG.read_text(encoding="utf-8").splitlines()
    tail = lines[-limit:]
    entries = [_parse_audit_line(line) for line in reversed(tail)]
    return {
        "entries": entries,
        "total": len(lines),
        "viewer": user["name"],
    }


# ---------------------------------------------------------------------------
# Browser-facing HTML
# ---------------------------------------------------------------------------
_PAGE_CSS = """
body { font: 14px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 960px; padding: 0 1rem; color: #222; }
h1 a { color: inherit; text-decoration: none; }
table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
th, td { border-bottom: 1px solid #e5e5e5; padding: 8px 10px; text-align: left; vertical-align: top; }
th { background: #fafafa; font-weight: 600; }
a { color: #0366d6; }
code, pre { background: #f5f5f5; border-radius: 4px; padding: 2px 6px; font-family: ui-monospace, monospace; }
pre { padding: 12px; overflow-x: auto; }
.muted { color: #888; font-size: 12px; }
form.search { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; margin: 1rem 0 0.5rem; }
form.search input { padding: 5px 9px; border: 1px solid #ccc; border-radius: 4px; min-width: 180px; font-size: 13px; }
form.search button { padding: 5px 14px; background: #0366d6; color: #fff; border: 0; border-radius: 4px; cursor: pointer; font-size: 13px; }
form.search button:hover { background: #0258b6; }
form.search .clear { font-size: 13px; }
a.tag { display: inline-block; padding: 1px 8px; margin: 0 2px 2px 0; background: #e7f3ff; color: #0366d6; border-radius: 10px; font-size: 12px; text-decoration: none; }
a.tag:hover { background: #cfe5fa; text-decoration: none; }
.error { color: #a00; background: #fee; padding: 8px 12px; border-radius: 4px; margin: 0.5rem 0; }
td.ts { font-family: ui-monospace, monospace; font-size: 12px; color: #555; white-space: nowrap; }
.publish label { display: block; margin-bottom: 1rem; font-weight: 600; }
.publish input[type=password] { display: block; margin-top: 4px; width: 100%; max-width: 400px; padding: 5px 9px; border: 1px solid #ccc; border-radius: 4px; font-size: 13px; }
.publish input[type=file] { display: block; margin-top: 4px; }
.publish button { padding: 6px 20px; background: #0366d6; color: #fff; border: 0; border-radius: 4px; cursor: pointer; font-size: 14px; }
.publish button:hover { background: #0258b6; }
.publish button:disabled { background: #999; cursor: wait; }
.result .ok { color: #22863a; }
.result .err { color: #a00; background: #fee; padding: 8px 12px; border-radius: 4px; display: inline-block; }
"""


def _layout(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=en><head>"
        "<meta charset=utf-8>"
        f"<title>{escape(title)}</title>"
        f"<style>{_PAGE_CSS}</style></head><body>"
        f"{body}"
        "</body></html>"
    )


def _tag_badges(tags: list[str]) -> str:
    if not tags:
        return '<span class=muted>—</span>'
    return "".join(
        f'<a class="tag" href="/?tag={urlquote(t)}">{escape(t)}</a>'
        for t in tags
    )


def _search_form(name: str, tag: str, description: str) -> str:
    """Render the top-of-page filter form, preserving current inputs."""
    return (
        '<form method="get" class="search">'
        f'<input type="text" name="name" value="{escape(name)}" placeholder="Name (regex)">'
        f'<input type="text" name="tag" value="{escape(tag)}" placeholder="Tag (regex)">'
        f'<input type="text" name="description" value="{escape(description)}" placeholder="Description (regex)">'
        '<button type="submit">Search</button>'
        '<a class="clear" href="/">clear</a>'
        "</form>"
    )


_PUBLISH_JS = """\
document.getElementById("pf").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = document.getElementById("btn");
  const el = document.getElementById("result");
  btn.disabled = true;
  btn.textContent = "Publishing\u2026";
  el.innerHTML = "";
  const fd = new FormData();
  fd.append("file", document.getElementById("pkg").files[0]);
  try {
    const res = await fetch("/api/publish", {
      method: "POST",
      headers: {"Authorization": "Bearer " + document.getElementById("tok").value},
      body: fd,
    });
    const data = await res.json();
    if (res.ok) {
      el.innerHTML = '<p class="ok">\u2714 Published: <strong>'
        + data.name + "@" + data.version + "</strong></p>";
    } else {
      el.innerHTML = '<p class="err">\u2718 '
        + (data.detail || JSON.stringify(data)) + "</p>";
    }
  } catch (err) {
    el.innerHTML = '<p class="err">\u2718 Network error: '
      + err.message + "</p>";
  } finally {
    btn.disabled = false;
    btn.textContent = "Publish";
  }
});
"""


@app.get("/", response_class=HTMLResponse)
def home(
    name: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    description: str | None = Query(default=None),
) -> HTMLResponse:
    # Compile each filter; collect regex errors so the page renders with a
    # warning instead of a 400 (better UX for browser users).
    errors: list[str] = []

    def _compile(raw: str | None, label: str) -> re.Pattern[str] | None:
        if not raw:
            return None
        try:
            return re.compile(raw, re.IGNORECASE)
        except re.error as exc:
            errors.append(f"{label}: {exc}")
            return None

    patterns = {
        "name": _compile(name, "name"),
        "tag": _compile(tag, "tag"),
        "description": _compile(description, "description"),
    }

    all_pkgs = all_packages()
    visible = (
        _filter_packages(all_pkgs, **patterns)
        if any(patterns.values())
        else all_pkgs
    )

    active_filter = any(patterns.values()) or errors
    status = (
        f"{len(visible)} of {len(all_pkgs)} package(s)"
        if active_filter
        else f"{len(all_pkgs)} package(s)"
    )

    rows: list[str] = []
    for entry in visible:
        rows.append(
            "<tr>"
            f"<td><a href=\"/packages/{escape(entry['name'])}\">{escape(entry['name'])}</a></td>"
            f"<td>{escape(entry['latest'])}</td>"
            f"<td>{_tag_badges(entry.get('tags', []))}</td>"
            f"<td>{escape(str(entry.get('description', '') or ''))}</td>"
            "</tr>"
        )

    err_html = (
        f'<p class="error">invalid regex — {escape("; ".join(errors))}</p>'
        if errors
        else ""
    )

    body = (
        "<h1><a href=\"/\">skilltool registry</a></h1>"
        '<p><a href="/publish">Publish a package</a></p>'
        + _search_form(name or "", tag or "", description or "")
        + err_html
        + f'<p class="muted">{status}</p>'
        + "<table><thead><tr>"
        "<th>Name</th><th>Latest</th><th>Tags</th><th>Description</th>"
        "</tr></thead><tbody>"
        + (
            "".join(rows)
            or "<tr><td colspan=4 class=muted>no matches</td></tr>"
        )
        + "</tbody></table>"
    )
    return HTMLResponse(_layout("skilltool registry", body))


@app.get("/packages/{name}", response_class=HTMLResponse)
def package_page(name: str) -> HTMLResponse:
    versions = list_versions(name)
    if not versions:
        raise HTTPException(404, f"package '{name}' not found")
    latest = versions[-1]
    meta = load_manifest(name, latest)

    entries = version_entries(name)
    version_rows = "".join(
        "<tr>"
        f"<td>{escape(ent['version'])}</td>"
        f"<td class=ts>{escape(ent['published_at'] or '—')}</td>"
        f"<td class=muted>{escape(ent['published_by'] or '—')}</td>"
        "<td>"
        f"<a href=\"/api/packages/{escape(name)}/download?version={urlquote(ent['version'])}\">zip</a>"
        "</td>"
        "</tr>"
        for ent in entries
    )

    published_by = str(meta.get("published_by", "") or "—")
    published_at = str(meta.get("published_at", "") or "—")
    tags = _normalize_tags(meta.get("tags"))
    tags_html = _tag_badges(tags) if tags else ""
    body = (
        "<p><a href=\"/\">← all packages</a></p>"
        f"<h1>{escape(name)}</h1>"
        f"<p><strong>Latest:</strong> {escape(latest)}</p>"
        f"<p><strong>Author:</strong> {escape(str(meta.get('author', '') or '—'))}</p>"
        f"<p><strong>Published by:</strong> {escape(published_by)} "
        f"<span class=muted>({escape(published_at)})</span></p>"
        + (f"<p><strong>Tags:</strong> {tags_html}</p>" if tags else "")
        + f"<p>{escape(str(meta.get('description', '')))}</p>"
        + "<h2>Install</h2>"
        + f"<pre>skilltool install {escape(name)}</pre>"
        + "<h2>Versions</h2>"
        + "<table><thead><tr>"
        "<th>Version</th><th>Published at</th><th>By</th><th>Download</th>"
        "</tr></thead>"
        + f"<tbody>{version_rows}</tbody></table>"
    )
    return HTMLResponse(_layout(f"{name} \u2014 skilltool", body))


@app.get("/publish", response_class=HTMLResponse)
def publish_page() -> HTMLResponse:
    body = (
        '<p><a href="/">\u2190 all packages</a></p>'
        "<h1>Publish</h1>"
        '<form id="pf" class="publish">'
        "<label>Token"
        '<input type="password" id="tok" required '
        'placeholder="tok_\u2026">'
        "</label>"
        "<label>Package zip"
        '<input type="file" id="pkg" accept=".zip" required>'
        "</label>"
        '<button type="submit" id="btn">Publish</button>'
        "</form>"
        '<div id="result" class="result"></div>'
        f"<script>{_PUBLISH_JS}</script>"
    )
    return HTMLResponse(_layout("Publish \u2014 skilltool", body))
