"""Terminal output helpers built on rich."""
from __future__ import annotations

from typing import Any, Iterable

from rich.console import Console
from rich.table import Table

_console = Console()
_err = Console(stderr=True, style="bold red")


def info(msg: str) -> None:
    _console.print(msg)


def success(msg: str) -> None:
    _console.print(f"[bold green]✓[/bold green] {msg}")


def warn(msg: str) -> None:
    _console.print(f"[yellow]![/yellow] {msg}")


def error(msg: str) -> None:
    _err.print(msg)


def package_table(rows: Iterable[dict[str, Any]], *, title: str | None = None) -> None:
    table = Table(title=title, show_lines=False, header_style="bold")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Latest", style="magenta")
    table.add_column("Author")
    table.add_column("Tags", style="blue")
    table.add_column("Description")
    count = 0
    for row in rows:
        tags = row.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        tag_str = ", ".join(str(t) for t in tags) if tags else ""
        table.add_row(
            str(row.get("name", "")),
            str(row.get("latest", row.get("version", ""))),
            str(row.get("author", "")),
            tag_str,
            str(row.get("description", "")),
        )
        count += 1
    if count == 0:
        _console.print("[dim]no matches[/dim]")
        return
    _console.print(table)


def kv(pairs: Iterable[tuple[str, Any]]) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    for k, v in pairs:
        table.add_row(str(k), str(v))
    _console.print(table)
