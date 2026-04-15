"""Business logic for each CLI subcommand.

Keeps CLI (``cli.py``) thin — everything testable lives here.
"""
from __future__ import annotations

import io
import re
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

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

DEFAULT_ENTRY = "SKILL.md"


@dataclass
class SkillMetadata:
    name: str
    version: str
    description: str
    author: str | None = None
    # skill.toml-only fields. For legacy skill.md packages these stay at
    # their defaults so callers can branch on ``manifest_format``.
    entry: str | None = None
    include: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    manifest_format: str = "skill.md"

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------
    @classmethod
    def from_frontmatter(cls, raw: str, *, source: str) -> "SkillMetadata":
        match = _FRONTMATTER_RE.match(raw)
        if not match:
            raise ValueError(f"{source}: missing YAML frontmatter")
        data = yaml.safe_load(match.group(1)) or {}
        for required in ("name", "version", "description"):
            if required not in data:
                raise ValueError(
                    f"{source}: frontmatter missing '{required}'"
                )
        return cls(
            name=str(data["name"]),
            version=str(data["version"]),
            description=str(data["description"]),
            author=(str(data["author"]) if "author" in data else None),
            manifest_format="skill.md",
        )

    @classmethod
    def from_skill_toml(cls, raw: str, *, source: str) -> "SkillMetadata":
        try:
            data = tomllib.loads(raw)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"{source}: invalid TOML: {exc}")
        skill = data.get("skill")
        if not isinstance(skill, dict):
            raise ValueError(f"{source}: missing [skill] table")
        for required in ("name", "version", "description"):
            if required not in skill:
                raise ValueError(
                    f"{source}: skill.{required} is required"
                )
        entry = str(skill.get("entry", DEFAULT_ENTRY))
        include_raw = skill.get("include")
        if include_raw is None:
            include = [entry]
        elif isinstance(include_raw, list) and all(
            isinstance(x, str) for x in include_raw
        ):
            include = list(include_raw)
        else:
            raise ValueError(
                f"{source}: skill.include must be a list of strings"
            )
        tags_raw = skill.get("tags") or []
        if isinstance(tags_raw, list):
            tags = [str(t) for t in tags_raw]
        elif isinstance(tags_raw, str):
            tags = [tags_raw]
        else:
            raise ValueError(
                f"{source}: skill.tags must be a list of strings"
            )
        return cls(
            name=str(skill["name"]),
            version=str(skill["version"]),
            description=str(skill["description"]),
            author=(str(skill["author"]) if "author" in skill else None),
            entry=entry,
            include=include,
            tags=tags,
            manifest_format="skill.toml",
        )


# ----------------------------------------------------------------------
# Manifest reading (file-level)
# ----------------------------------------------------------------------
def read_skill_md(skill_md: Path) -> SkillMetadata:
    return SkillMetadata.from_frontmatter(
        skill_md.read_text(encoding="utf-8"), source=str(skill_md)
    )


def read_skill_toml(skill_toml: Path) -> SkillMetadata:
    return SkillMetadata.from_skill_toml(
        skill_toml.read_text(encoding="utf-8"), source=str(skill_toml)
    )


def read_skill_manifest(src: Path) -> SkillMetadata:
    """Return the manifest in ``src``, preferring ``skill.toml``.

    Accepts either ``skill.toml`` (new format) or ``skill.md``/``SKILL.md``
    (legacy YAML frontmatter). skill.toml wins if both exist.
    """
    skill_toml = src / "skill.toml"
    if skill_toml.is_file():
        return read_skill_toml(skill_toml)
    for candidate in (src / "skill.md", src / "SKILL.md"):
        if candidate.is_file():
            return read_skill_md(candidate)
    raise ValueError(
        f"{src}: no manifest found "
        "(expected skill.toml or skill.md at directory root)"
    )


# ----------------------------------------------------------------------
# Local skill discovery (used by `list` and `publish`)
# ----------------------------------------------------------------------
def discover_installed(root: Path) -> list[tuple[Path, SkillMetadata]]:
    """Find skills installed under ``root`` (one level deep)."""
    found: list[tuple[Path, SkillMetadata]] = []
    if not root.exists():
        return found
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        try:
            meta = read_skill_manifest(entry)
        except (ValueError, OSError):
            continue
        found.append((entry, meta))
    return found


# ----------------------------------------------------------------------
# Zipping / extraction helpers
# ----------------------------------------------------------------------
def _should_include(path: Path) -> bool:
    """Reject paths that hit our hard-excluded dirs/suffixes regardless
    of what ``include`` says — keeps ``**`` patterns from accidentally
    shipping ``.git`` or ``__pycache__``.
    """
    if any(part in _EXCLUDED_DIR_NAMES for part in path.parts):
        return False
    if path.suffix in _EXCLUDED_FILE_SUFFIXES:
        return False
    return True


def expand_include(src: Path, patterns: list[str]) -> set[Path]:
    """Resolve ``include`` glob patterns against ``src``.

    Returns files only (directories that match are skipped; their
    children match on their own if the pattern recurses). Hard-excluded
    paths are dropped even if the pattern explicitly matched them.
    """
    matched: set[Path] = set()
    for pattern in patterns:
        for p in src.glob(pattern):
            if not p.is_file():
                continue
            if not _should_include(p.relative_to(src)):
                continue
            matched.add(p)
    return matched


def _zip_with_manifest(src: Path, meta: SkillMetadata) -> bytes:
    """Zip only the files selected by ``meta.include``.

    ``skill.toml`` and the entry file are implicitly added — the user
    can omit them from ``include`` and the resulting archive still
    contains them.
    """
    matched: set[Path] = set()

    # skill.toml is ALWAYS included (task004 contract).
    skill_toml = src / "skill.toml"
    if skill_toml.is_file():
        matched.add(skill_toml)

    # Entry is implicitly included so it's always reachable at install time.
    if meta.entry:
        entry_path = src / meta.entry
        if not entry_path.is_file():
            raise ValueError(
                f"{src}: entry '{meta.entry}' declared in skill.toml "
                "does not exist"
            )
        matched.add(entry_path)

    matched.update(expand_include(src, meta.include))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(matched):
            if not path.is_file():
                continue
            zf.write(path, arcname=str(path.relative_to(src)))
    return buf.getvalue()


def _zip_legacy(src: Path) -> bytes:
    """Zip every file under ``src`` minus hard excludes.

    Used for packages that still use the ``skill.md`` YAML-frontmatter
    manifest format.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src.rglob("*")):
            rel = path.relative_to(src)
            if not path.is_file() or not _should_include(rel):
                continue
            zf.write(path, arcname=str(rel))
    return buf.getvalue()


def zip_skill_directory(src: Path) -> bytes:
    """Zip a skill directory for publishing.

    * ``skill.toml`` present → ``include`` globs drive the contents.
    * ``skill.md`` present   → legacy behaviour (zip everything minus excludes).

    Metadata is validated upfront so we fail fast before building the zip.
    """
    if not src.is_dir():
        raise ValueError(f"{src}: not a directory")

    meta = read_skill_manifest(src)
    if meta.manifest_format == "skill.toml":
        return _zip_with_manifest(src, meta)
    return _zip_legacy(src)


def extract_zip(data_path: Path, dest_dir: Path, *, overwrite: bool) -> Path:
    """Extract ``data_path`` into ``dest_dir``.

    Returns the directory that was written.
    """
    if dest_dir.exists():
        if not overwrite:
            raise FileExistsError(
                f"{dest_dir} already exists; pass --force to overwrite"
            )
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
    "DEFAULT_ENTRY",
    "SkillMetadata",
    "cmd_install",
    "cmd_list",
    "cmd_publish",
    "cmd_search",
    "cmd_show",
    "discover_installed",
    "expand_include",
    "extract_zip",
    "read_skill_manifest",
    "read_skill_md",
    "read_skill_toml",
    "zip_skill_directory",
]
