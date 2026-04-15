"""Typer-based CLI entry point."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from . import __version__, commands, output
from .api import RegistryError
from .config import Config

app = typer.Typer(
    add_completion=False,
    help="PyPI-like registry client for skill.md packages.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        output.info(f"skilltool {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the skilltool version and exit.",
    ),
) -> None:
    """skilltool — publish, search, and install skill.md packages."""


# ----------------------------------------------------------------------
@app.command()
def install(
    name: str = typer.Argument(..., help="Package name"),
    dest: Path = typer.Option(
        Path("."),
        "--dest",
        "-d",
        help="Directory under which to place the package (default: CWD).",
    ),
    version: Optional[str] = typer.Option(
        None, "--version", "-v", help="Specific version (default: latest)."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite an existing destination."
    ),
) -> None:
    """Install a skill package into the current directory."""
    cfg = Config.load()
    try:
        target = commands.cmd_install(
            cfg, name, dest=dest, version=version, force=force
        )
    except FileExistsError as e:
        output.error(str(e))
        raise typer.Exit(1)
    except RegistryError as e:
        output.error(f"registry error: {e}")
        raise typer.Exit(1)
    output.success(f"installed {name} → {target}")


@app.command()
def search(
    query: str = typer.Argument(..., help="Regex matched against name + description."),
) -> None:
    """Search the registry for packages."""
    cfg = Config.load()
    try:
        results = commands.cmd_search(cfg, query)
    except RegistryError as e:
        output.error(f"registry error: {e}")
        raise typer.Exit(1)
    output.package_table(results, title=f"matches for /{query}/")


@app.command()
def show(name: str = typer.Argument(..., help="Package name")) -> None:
    """Show metadata and version history for a package."""
    cfg = Config.load()
    try:
        info = commands.cmd_show(cfg, name)
    except RegistryError as e:
        output.error(f"registry error: {e}")
        raise typer.Exit(1)
    meta = info.get("metadata", {})
    output.kv(
        [
            ("name", info.get("name", name)),
            ("latest", info.get("latest", "-")),
            ("author", meta.get("author", "-")),
            ("description", meta.get("description", "-")),
            ("versions", ", ".join(info.get("versions", []))),
        ]
    )


@app.command("list")
def list_cmd(
    dest: Path = typer.Option(
        Path("."),
        "--dest",
        "-d",
        help="Directory to scan for installed skills (default: CWD).",
    ),
) -> None:
    """List skills installed under a directory."""
    installed = commands.cmd_list(dest)
    rows = [
        {
            "name": meta.name,
            "latest": meta.version,
            "author": meta.author or "",
            "description": meta.description,
        }
        for _, meta in installed
    ]
    output.package_table(rows, title=f"skills installed under {dest}")


@app.command()
def publish(
    path: Path = typer.Argument(
        ..., exists=True, readable=True, help="Skill directory or prebuilt .zip file."
    ),
    token: Optional[str] = typer.Option(
        None, "--token", "-t", help="Override the configured publish token."
    ),
) -> None:
    """Publish a skill package to the registry."""
    cfg = Config.load()
    try:
        result = commands.cmd_publish(cfg, path, token=token)
    except (ValueError, RegistryError) as e:
        output.error(str(e))
        raise typer.Exit(1)
    output.success(
        f"published {result.get('name', '?')} {result.get('version', '?')}"
    )
    published_by = result.get("published_by")
    published_at = result.get("published_at")
    if published_by or published_at:
        output.info(
            f"  by [bold]{published_by or '—'}[/bold]  [dim]{published_at or ''}[/dim]"
        )


@app.command()
def config() -> None:
    """Print the resolved configuration and its source."""
    cfg = Config.load()
    masked_token = "(unset)" if cfg.token is None else f"{cfg.token[:4]}…"

    def _row(value: object, source: str) -> str:
        # Parentheses instead of brackets — rich's markup parser would
        # silently eat "[env]" as a style tag.
        return f"{value}   ({source})"

    rows: list[tuple[str, object]] = [
        ("transport", _row(cfg.transport, cfg.transport_source)),
    ]
    if cfg.transport == "ssh":
        rows.extend(
            [
                (
                    "ssh_host",
                    _row(cfg.ssh_host or "(unset)", cfg.ssh_host_source),
                ),
                ("ssh_user", _row(cfg.ssh_user, cfg.ssh_user_source)),
            ]
        )
    rows.extend(
        [
            ("registry", _row(cfg.registry, cfg.registry_source)),
            ("token", _row(masked_token, cfg.token_source)),
            ("config file", cfg.config_file),
            ("version", __version__),
        ]
    )
    output.kv(rows)


if __name__ == "__main__":  # pragma: no cover
    app()
