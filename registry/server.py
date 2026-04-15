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
            }
        )
    return out


def extract_skill_metadata(zip_bytes: bytes) -> dict[str, Any]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise HTTPException(400, f"invalid zip: {exc}")

    with zf:
        skill_md = next(
            (
                n
                for n in zf.namelist()
                if n.endswith("skill.md") and n.count("/") <= 1
            ),
            None,
        )
        if skill_md is None:
            raise HTTPException(400, "skill.md not found at archive root")
        content = zf.read(skill_md).decode("utf-8", errors="replace")

    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise HTTPException(400, "skill.md is missing YAML frontmatter")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(400, f"invalid frontmatter YAML: {exc}")

    for field in ("name", "version", "description"):
        if field not in meta:
            raise HTTPException(400, f"frontmatter missing '{field}'")

    name = str(meta["name"])
    version = str(meta["version"])
    if not _NAME_RE.match(name):
        raise HTTPException(400, f"invalid package name: {name!r}")
    if not _VERSION_RE.match(version):
        raise HTTPException(400, f"invalid version: {version!r}")

    return {
        "name": name,
        "version": version,
        "description": str(meta.get("description", "")),
        "author": str(meta["author"]) if "author" in meta else "",
        **{
            k: v
            for k, v in meta.items()
            if k not in {"name", "version", "description", "author"}
        },
    }


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


@app.get("/api/search")
def search(q: str) -> dict[str, Any]:
    try:
        pattern = re.compile(q, re.IGNORECASE)
    except re.error as exc:
        raise HTTPException(400, f"invalid regex: {exc}")

    results: list[dict[str, Any]] = []
    for name, versions in iter_packages():
        meta = load_manifest(name, versions[-1])
        haystack = f"{name} {meta.get('description', '')}"
        if pattern.search(haystack):
            results.append(
                {
                    "name": name,
                    "latest": versions[-1],
                    "description": meta.get("description", ""),
                    "author": meta.get("author", ""),
                }
            )
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
.muted { color: #888; }
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


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    rows: list[str] = []
    total = 0
    for name, versions in iter_packages():
        total += 1
        meta = load_manifest(name, versions[-1])
        rows.append(
            "<tr>"
            f"<td><a href=\"/packages/{escape(name)}\">{escape(name)}</a></td>"
            f"<td>{escape(versions[-1])}</td>"
            f"<td>{escape(str(meta.get('description', '')))}</td>"
            "</tr>"
        )
    body = (
        "<h1><a href=\"/\">skilltool registry</a></h1>"
        f"<p class=muted>{total} package(s).</p>"
        "<table><thead><tr><th>Name</th><th>Latest</th><th>Description</th></tr></thead>"
        "<tbody>"
        + ("".join(rows) or "<tr><td colspan=3 class=muted>empty</td></tr>")
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

    version_rows = "".join(
        "<tr>"
        f"<td>{escape(v)}</td>"
        f"<td><a href=\"/api/packages/{escape(name)}/download?version={escape(v)}\">zip</a></td>"
        "</tr>"
        for v in reversed(versions)
    )
    published_by = str(meta.get("published_by", "") or "—")
    published_at = str(meta.get("published_at", "") or "—")
    body = (
        "<p><a href=\"/\">← all packages</a></p>"
        f"<h1>{escape(name)}</h1>"
        f"<p><strong>Latest:</strong> {escape(latest)}</p>"
        f"<p><strong>Author:</strong> {escape(str(meta.get('author', '') or '—'))}</p>"
        f"<p><strong>Published by:</strong> {escape(published_by)} "
        f"<span class=muted>({escape(published_at)})</span></p>"
        f"<p>{escape(str(meta.get('description', '')))}</p>"
        "<h2>Install</h2>"
        f"<pre>skilltool install {escape(name)}</pre>"
        "<h2>Versions</h2>"
        "<table><thead><tr><th>Version</th><th>Download</th></tr></thead>"
        f"<tbody>{version_rows}</tbody></table>"
    )
    return HTMLResponse(_layout(f"{name} — skilltool", body))
