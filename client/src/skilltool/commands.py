"""Business logic for each CLI subcommand.

Keeps CLI (``cli.py``) thin — everything testable lives here.
"""
from __future__ import annotations

import io
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from .api import RegistryClient
from .config import Config

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Paths excluded when zipping a skill directory.
_EXCLUDED_DIR_NAMES = {
    "__pycache__",
    ".git",
    ".venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
_EXCLUDED_FILE_SUFFIXES = (".pyc", ".pyo", ".swp")


@dataclass
class SkillMetadata:
    name: str
    version: str
    description: str
    author: str | None = None

    @classmethod
    def from_frontmatter(cls, raw: str, *, source: str) -> "SkillMetadata":
        match = _FRONTMATTER_RE.match(raw)
        if not match:
            raise ValueError(f"{source}: missing YAML frontmatter")
        data = yaml.safe_load(match.group(1)) or {}
        for field in ("name", "version", "description"):
            if field not in data:
                raise ValueError(f"{source}: frontmatter missing '{field}'")
        return cls(
            name=str(data["name"]),
            version=str(data["version"]),
            description=str(data["description"]),
            author=(str(data["author"]) if "author" in data else None),
        )


# ----------------------------------------------------------------------
# Local skill discovery (used by `list` and `publish`)
# ----------------------------------------------------------------------
def read_skill_md(skill_md: Path) -> SkillMetadata:
    return SkillMetadata.from_frontmatter(
        skill_md.read_text(encoding="utf-8"), source=str(skill_md)
    )


def discover_installed(root: Path) -> list[tuple[Path, SkillMetadata]]:
    """Find skills installed under ``root`` (one level deep)."""
    found: list[tuple[Path, SkillMetadata]] = []
    if not root.exists():
        return found
    for entry in sorted(root.iterdir()):
        skill_md = entry / "skill.md"
        if entry.is_dir() and skill_md.is_file():
            try:
                meta = read_skill_md(skill_md)
            except Exception:
                continue
            found.append((entry, meta))
    return found


# ----------------------------------------------------------------------
# Zipping / extraction helpers
# ----------------------------------------------------------------------
def _should_include(path: Path) -> bool:
    if any(part in _EXCLUDED_DIR_NAMES for part in path.parts):
        return False
    if path.suffix in _EXCLUDED_FILE_SUFFIXES:
        return False
    return True


def zip_skill_directory(src: Path) -> bytes:
    """Zip *contents* of ``src`` (not the wrapping dir).

    ``skill.md`` must exist at the archive root.
    """
    if not src.is_dir():
        raise ValueError(f"{src}: not a directory")
    skill_md = src / "skill.md"
    if not skill_md.is_file():
        raise ValueError(f"{src}: skill.md not found at directory root")

    # Validate metadata upfront so we fail fast before building the zip.
    read_skill_md(skill_md)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src.rglob("*")):
            if not path.is_file() or not _should_include(path.relative_to(src)):
                continue
            zf.write(path, arcname=str(path.relative_to(src)))
    return buf.getvalue()


def extract_zip(data_path: Path, dest_dir: Path, *, overwrite: bool) -> Path:
    """Extract ``data_path`` into ``dest_dir``.

    Returns the directory that was written.
    """
    if dest_dir.exists():
        if not overwrite:
            raise FileExistsError(
                f"{dest_dir} already exists; pass --force to overwrite"
            )
        # Remove files we're about to overwrite, but preserve user's other data
        # by only deleting inside an empty wrapper. We actually do a full rmtree
        # since the install target is expected to be skilltool-managed.
        import shutil
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(data_path) as zf:
        zf.extractall(dest_dir)
    return dest_dir


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------
def cmd_install(
    cfg: Config,
    name: str,
    *,
    dest: Path,
    version: str | None,
    force: bool,
) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / name
    with RegistryClient(cfg) as client, tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / f"{name}.zip"
        client.download(name, zip_path, version=version)
        return extract_zip(zip_path, target, overwrite=force)


def cmd_search(cfg: Config, query: str) -> list[dict]:
    with RegistryClient(cfg) as client:
        return client.search(query)


def cmd_show(cfg: Config, name: str) -> dict:
    with RegistryClient(cfg) as client:
        return client.package(name)


def cmd_list(root: Path) -> list[tuple[Path, SkillMetadata]]:
    return discover_installed(root)


def cmd_publish(
    cfg: Config,
    path: Path,
    *,
    token: str | None,
) -> dict:
    if path.is_dir():
        payload = zip_skill_directory(path)
        with tempfile.NamedTemporaryFile(
            suffix=".zip", delete=False
        ) as tmp:
            tmp.write(payload)
            zip_path = Path(tmp.name)
    elif path.is_file() and path.suffix == ".zip":
        zip_path = path
    else:
        raise ValueError(f"{path}: expected a directory or .zip file")

    try:
        with RegistryClient(cfg) as client:
            return client.publish(zip_path, token=token)
    finally:
        if path.is_dir():
            zip_path.unlink(missing_ok=True)


__all__ = [
    "SkillMetadata",
    "cmd_install",
    "cmd_list",
    "cmd_publish",
    "cmd_search",
    "cmd_show",
    "discover_installed",
    "extract_zip",
    "read_skill_md",
    "zip_skill_directory",
]
