"""Unit tests for the skill.toml manifest format (task004)."""
from __future__ import annotations

import io
import textwrap
import zipfile
from pathlib import Path

import pytest

from skilltool.commands import (
    DEFAULT_ENTRY,
    SkillMetadata,
    expand_include,
    read_skill_manifest,
    zip_skill_directory,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def test_from_skill_toml_minimal() -> None:
    meta = SkillMetadata.from_skill_toml(
        textwrap.dedent(
            """
            [skill]
            name        = "my-skill"
            version     = "1.0.0"
            description = "demo"
            """
        ).strip(),
        source="<test>",
    )
    assert meta.name == "my-skill"
    assert meta.version == "1.0.0"
    assert meta.description == "demo"
    assert meta.entry == DEFAULT_ENTRY
    # Default include = [entry]
    assert meta.include == [DEFAULT_ENTRY]
    assert meta.manifest_format == "skill.toml"
    assert meta.author is None


def test_from_skill_toml_full() -> None:
    meta = SkillMetadata.from_skill_toml(
        textwrap.dedent(
            """
            [skill]
            name        = "my-skill"
            version     = "1.2.0"
            description = "rich"
            author      = "team-doc"
            entry       = "SKILL.md"
            include     = ["SKILL.md", "scripts/**"]
            """
        ).strip(),
        source="<test>",
    )
    assert meta.author == "team-doc"
    assert meta.entry == "SKILL.md"
    assert meta.include == ["SKILL.md", "scripts/**"]


def test_from_skill_toml_rejects_missing_table() -> None:
    with pytest.raises(ValueError, match=r"\[skill\] table"):
        SkillMetadata.from_skill_toml(
            "[other]\nname = 'x'\n", source="<test>"
        )


def test_from_skill_toml_requires_name_version_description() -> None:
    for missing in ("name", "version", "description"):
        fields = {
            "name": '"x"',
            "version": '"1.0.0"',
            "description": '"d"',
        }
        del fields[missing]
        raw = "[skill]\n" + "\n".join(f"{k} = {v}" for k, v in fields.items())
        with pytest.raises(ValueError, match=rf"skill\.{missing}"):
            SkillMetadata.from_skill_toml(raw, source="<test>")


def test_from_skill_toml_rejects_bad_include_type() -> None:
    with pytest.raises(ValueError, match=r"skill\.include must be"):
        SkillMetadata.from_skill_toml(
            textwrap.dedent(
                """
                [skill]
                name = "x"
                version = "1.0.0"
                description = "d"
                include = "not-a-list"
                """
            ).strip(),
            source="<test>",
        )


def test_from_skill_toml_rejects_bad_toml() -> None:
    with pytest.raises(ValueError, match=r"invalid TOML"):
        SkillMetadata.from_skill_toml("this is not [toml", source="<test>")


# ---------------------------------------------------------------------------
# Directory-level manifest resolution
# ---------------------------------------------------------------------------
def test_read_skill_manifest_prefers_skill_toml(tmp_path: Path) -> None:
    # Both formats present — skill.toml wins.
    _write(
        tmp_path / "skill.toml",
        """
        [skill]
        name        = "twotone"
        version     = "1.0.0"
        description = "from toml"
        """,
    )
    _write(
        tmp_path / "SKILL.md",
        """
        ---
        name: twotone
        version: 1.0.0
        description: from md
        ---

        body
        """,
    )
    meta = read_skill_manifest(tmp_path)
    assert meta.manifest_format == "skill.toml"
    assert meta.description == "from toml"


def test_read_skill_manifest_falls_back_to_skill_md(tmp_path: Path) -> None:
    _write(
        tmp_path / "skill.md",
        """
        ---
        name: legacy
        version: 0.1.0
        description: from md
        ---

        body
        """,
    )
    meta = read_skill_manifest(tmp_path)
    assert meta.manifest_format == "skill.md"


def test_read_skill_manifest_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"no manifest found"):
        read_skill_manifest(tmp_path)


# ---------------------------------------------------------------------------
# include glob expansion
# ---------------------------------------------------------------------------
def test_expand_include_matches_file_at_root(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("body")
    matched = expand_include(tmp_path, ["SKILL.md"])
    assert matched == {tmp_path / "SKILL.md"}


def test_expand_include_recurses_with_double_star(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("top")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "a.py").write_text("a")
    (tmp_path / "scripts" / "nested").mkdir()
    (tmp_path / "scripts" / "nested" / "b.py").write_text("b")

    matched = expand_include(tmp_path, ["scripts/**"])
    rels = sorted(p.relative_to(tmp_path).as_posix() for p in matched)
    assert rels == ["scripts/a.py", "scripts/nested/b.py"]


def test_expand_include_drops_hard_excluded_paths(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "helper.py").write_text("h")
    (tmp_path / "scripts" / "__pycache__").mkdir()
    (tmp_path / "scripts" / "__pycache__" / "helper.cpython-312.pyc").write_text("")

    matched = expand_include(tmp_path, ["scripts/**"])
    rels = sorted(p.relative_to(tmp_path).as_posix() for p in matched)
    assert rels == ["scripts/helper.py"]


def test_expand_include_multiple_patterns_union(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("a")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.md").write_text("b")
    (tmp_path / "other.txt").write_text("c")

    matched = expand_include(tmp_path, ["SKILL.md", "docs/**"])
    rels = sorted(p.relative_to(tmp_path).as_posix() for p in matched)
    assert rels == ["SKILL.md", "docs/README.md"]


# ---------------------------------------------------------------------------
# Full zip_skill_directory end-to-end
# ---------------------------------------------------------------------------
def _entries(zip_bytes: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return sorted(zf.namelist())


def test_zip_directory_with_skill_toml_default_include(tmp_path: Path) -> None:
    # No include specified → default = [entry]. Other files are excluded.
    _write(
        tmp_path / "skill.toml",
        """
        [skill]
        name        = "my-skill"
        version     = "1.0.0"
        description = "demo"
        """,
    )
    (tmp_path / "SKILL.md").write_text("body")
    (tmp_path / "README_dev.md").write_text("not shipped")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text("t")

    entries = _entries(zip_skill_directory(tmp_path))
    assert entries == ["SKILL.md", "skill.toml"]


def test_zip_directory_with_skill_toml_explicit_include(tmp_path: Path) -> None:
    _write(
        tmp_path / "skill.toml",
        """
        [skill]
        name        = "my-skill"
        version     = "1.2.0"
        description = "demo"
        entry       = "SKILL.md"
        include     = ["SKILL.md", "scripts/**"]
        """,
    )
    (tmp_path / "SKILL.md").write_text("body")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "helper.py").write_text("h")
    (tmp_path / "scripts" / "__pycache__").mkdir()
    (tmp_path / "scripts" / "__pycache__" / "helper.cpython-312.pyc").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text("t")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref")
    (tmp_path / "README_dev.md").write_text("skip")

    entries = _entries(zip_skill_directory(tmp_path))
    # Exactly the whitelisted files, skill.toml always, __pycache__/.git purged.
    assert entries == [
        "SKILL.md",
        "scripts/helper.py",
        "skill.toml",
    ]


def test_zip_directory_fails_when_entry_missing(tmp_path: Path) -> None:
    _write(
        tmp_path / "skill.toml",
        """
        [skill]
        name        = "my-skill"
        version     = "1.0.0"
        description = "demo"
        entry       = "SKILL.md"
        """,
    )
    # No SKILL.md file exists.
    with pytest.raises(ValueError, match=r"entry 'SKILL\.md' .*does not exist"):
        zip_skill_directory(tmp_path)


def test_zip_directory_skill_md_legacy_still_works(tmp_path: Path) -> None:
    # No skill.toml — exercise the backward-compat path.
    _write(
        tmp_path / "skill.md",
        """
        ---
        name: legacy
        version: 0.1.0
        description: legacy
        ---

        body
        """,
    )
    (tmp_path / "extras.txt").write_text("ok")

    entries = _entries(zip_skill_directory(tmp_path))
    # Legacy behaviour: zip everything minus hard excludes.
    assert entries == ["extras.txt", "skill.md"]


def test_zip_directory_skill_toml_always_included_even_if_not_in_include(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "skill.toml",
        """
        [skill]
        name        = "my-skill"
        version     = "1.0.0"
        description = "demo"
        include     = ["SKILL.md"]
        """,
    )
    (tmp_path / "SKILL.md").write_text("body")

    entries = _entries(zip_skill_directory(tmp_path))
    assert "skill.toml" in entries
    assert "SKILL.md" in entries


# ---------------------------------------------------------------------------
# Precedence & frontmatter-optional invariants
# ---------------------------------------------------------------------------
def test_skill_toml_present_means_no_frontmatter_needed(tmp_path: Path) -> None:
    """With skill.toml, SKILL.md can be arbitrary markdown with no frontmatter.

    The client must neither parse nor require the frontmatter — the whole
    point of skill.toml is that the narrative file stays plain.
    """
    _write(
        tmp_path / "skill.toml",
        """
        [skill]
        name        = "plain"
        version     = "1.0.0"
        description = "no frontmatter on SKILL.md"
        """,
    )
    # SKILL.md is pure narrative — no YAML frontmatter at all.
    (tmp_path / "SKILL.md").write_text(
        "# Plain skill\n\nJust narrative, no frontmatter.\n"
    )

    meta = read_skill_manifest(tmp_path)
    assert meta.manifest_format == "skill.toml"
    assert meta.name == "plain"

    # Zip must still build cleanly.
    entries = _entries(zip_skill_directory(tmp_path))
    assert entries == ["SKILL.md", "skill.toml"]


def test_skill_toml_present_with_broken_frontmatter_on_skill_md(tmp_path: Path) -> None:
    """Even malformed frontmatter on skill.md is ignored when skill.toml wins.

    This nails down that read_skill_manifest / _zip_with_manifest never
    parses skill.md at all when skill.toml is the source of truth.
    """
    _write(
        tmp_path / "skill.toml",
        """
        [skill]
        name        = "winner"
        version     = "1.0.0"
        description = "toml wins"
        entry       = "skill.md"
        include     = ["skill.md"]
        """,
    )
    # Deliberately broken YAML. Would blow up `read_skill_md`, but we never
    # call it when skill.toml is present.
    (tmp_path / "skill.md").write_text(
        "---\nnot: [valid\nyaml: }\n---\n\n# still publishes\n"
    )

    meta = read_skill_manifest(tmp_path)
    assert meta.name == "winner"
    assert meta.manifest_format == "skill.toml"
    # The zip step must not try to parse skill.md's frontmatter.
    entries = _entries(zip_skill_directory(tmp_path))
    assert "skill.toml" in entries
    assert "skill.md" in entries


def test_skill_toml_overrides_all_conflicting_skill_md_fields(tmp_path: Path) -> None:
    """If skill.md's frontmatter conflicts with skill.toml on every field,
    the resolved metadata matches skill.toml exactly — skill.md never wins."""
    _write(
        tmp_path / "skill.toml",
        """
        [skill]
        name        = "truth"
        version     = "1.0.0"
        description = "from toml"
        author      = "team-toml"
        """,
    )
    _write(
        tmp_path / "SKILL.md",
        """
        ---
        name: impostor
        version: 9.9.9
        description: from md
        author: team-md
        ---

        body
        """,
    )

    meta = read_skill_manifest(tmp_path)
    assert meta.name == "truth"
    assert meta.version == "1.0.0"
    assert meta.description == "from toml"
    assert meta.author == "team-toml"
    assert meta.manifest_format == "skill.toml"
