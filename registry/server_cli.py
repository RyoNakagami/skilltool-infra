#!/usr/bin/env python3
"""Server-side CLI invoked by the SSH transport.

Usage (from an authorised SSH client):

    ssh <user>@<host> skilltool-server list
    ssh <user>@<host> skilltool-server search <regex>
    ssh <user>@<host> skilltool-server show <name>
    ssh <user>@<host> skilltool-server download <name> [--version <v>]
    ssh <user>@<host> skilltool-server publish --token <tok> --data <b64>
    ssh <user>@<host> skilltool-server audit [--limit <n>]

Contract (per task003 §8):
  * stdout emits ONLY JSON (text verbs) or raw bytes (``download``).
  * logs, warnings, and errors go to stderr.
  * non-zero exit indicates failure.

This module imports ``server.py`` for its shared logic — ``resolve_user``,
``publish_logic``, ``extract_skill_metadata``, etc. — so HTTP and SSH
transports share a single code path for package storage and the audit
log.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, NoReturn

# Allow ``python server_cli.py`` and ``skilltool-server`` symlink invocations
# to both find the sibling ``server`` module regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import server as _server  # noqa: E402  (path setup must precede import)


def _fail(detail: str, *, code: int = 1) -> NoReturn:
    print(json.dumps({"error": detail}), file=sys.stderr)
    sys.exit(code)


def _emit(obj: Any) -> None:
    """Write a JSON value to stdout exactly once, followed by a newline."""
    sys.stdout.write(json.dumps(obj))
    sys.stdout.write("\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Verb implementations
# ---------------------------------------------------------------------------
def _verb_list(_args: list[str]) -> None:
    _emit(_server.all_packages())


def _verb_search(args: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="skilltool-server search")
    parser.add_argument("query")
    ns = parser.parse_args(args)
    try:
        pattern = re.compile(ns.query, re.IGNORECASE)
    except re.error as exc:
        _fail(f"invalid regex: {exc}", code=2)

    results: list[dict[str, Any]] = []
    for entry in _server.all_packages():
        haystack = f"{entry['name']} {entry.get('description', '')}"
        if pattern.search(haystack):
            results.append(entry)
    _emit(results)


def _verb_show(args: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="skilltool-server show")
    parser.add_argument("name")
    ns = parser.parse_args(args)

    versions = _server.list_versions(ns.name)
    if not versions:
        _fail(f"package '{ns.name}' not found", code=4)
    latest = versions[-1]
    _emit(
        {
            "name": ns.name,
            "versions": versions,
            "latest": latest,
            "metadata": _server.load_manifest(ns.name, latest),
        }
    )


def _verb_download(args: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="skilltool-server download")
    parser.add_argument("name")
    parser.add_argument("--version", dest="version", default=None)
    ns = parser.parse_args(args)

    versions = _server.list_versions(ns.name)
    if not versions:
        _fail(f"package '{ns.name}' not found", code=4)
    v = ns.version or versions[-1]
    zip_path = _server.PACKAGES_DIR / ns.name / f"{v}.zip"
    if not zip_path.is_file():
        _fail(f"version '{v}' not found for '{ns.name}'", code=4)

    # Binary verb — raw bytes to stdout, no trailing newline.
    sys.stdout.buffer.write(zip_path.read_bytes())
    sys.stdout.buffer.flush()


def _verb_publish(args: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="skilltool-server publish")
    parser.add_argument("--token", required=True)
    parser.add_argument(
        "--data",
        required=False,
        default=None,
        help="Base64-encoded zip payload. Omit to read raw bytes from stdin.",
    )
    ns = parser.parse_args(args)

    if ns.data is not None:
        try:
            payload = base64.b64decode(ns.data, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            _fail(f"invalid base64 payload: {exc}", code=2)
    else:
        payload = sys.stdin.buffer.read()
        if not payload:
            _fail("no payload on stdin and --data not supplied", code=2)

    try:
        result = _server.publish_logic(ns.token, payload)
    except _server.PublishError as exc:
        _fail(exc.detail, code=3)
    _emit(result)


def _verb_audit(args: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="skilltool-server audit")
    parser.add_argument("--limit", type=int, default=50)
    ns = parser.parse_args(args)

    if not _server.AUDIT_LOG.exists():
        _emit({"entries": [], "total": 0})
        return
    lines = _server.AUDIT_LOG.read_text(encoding="utf-8").splitlines()
    tail = lines[-max(ns.limit, 0):] if ns.limit else lines
    entries = [_server._parse_audit_line(line) for line in reversed(tail)]
    _emit({"entries": entries, "total": len(lines)})


_VERBS = {
    "list": _verb_list,
    "search": _verb_search,
    "show": _verb_show,
    "download": _verb_download,
    "publish": _verb_publish,
    "audit": _verb_audit,
}


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        _fail("no verb; expected one of: " + ", ".join(_VERBS), code=2)
    verb, *rest = args
    handler = _VERBS.get(verb)
    if handler is None:
        _fail(f"unknown verb: {verb!r}", code=2)
    handler(rest)


if __name__ == "__main__":
    main()
