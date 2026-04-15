"""SSH transport — executes ``skilltool-server <verb>`` on Server A over SSH.

No HTTP daemon is required on the remote end; only:

* ``sshd`` reachable at ``cfg.ssh_host`` as ``cfg.ssh_user``
* ``skilltool-server`` on the remote user's ``PATH``

Wire format — stdout:

* text verbs emit a single JSON value + trailing newline;
* ``download`` emits the raw zip bytes.

Errors surface on stderr and via a non-zero exit code; both are folded
into :class:`~skilltool.transport.base.RegistryError`.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Sequence

from ..config import Config
from .base import RegistryError, Transport

# ``ssh`` options applied to every invocation:
#   BatchMode=yes             — refuse to prompt (no interactive passwords)
#   ConnectTimeout=10         — fail fast on dead hosts
#   StrictHostKeyChecking=accept-new
#                              — trust on first use; rejects on host-key change
_DEFAULT_SSH_OPTS: tuple[str, ...] = (
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=accept-new",
)

# Override for tests / unusual deployments. When set this replaces the
# leading ``ssh <opts> <user>@<host>`` segment and is executed locally.
# Example: ``SKILLTOOL_SSH_COMMAND="python3 /path/to/server_cli.py"``
_SSH_COMMAND_ENV = "SKILLTOOL_SSH_COMMAND"


class SshTransport(Transport):
    """SSH-based transport for the skilltool registry."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg

    # ------------------------------------------------------------------
    # Command assembly
    # ------------------------------------------------------------------
    def _prefix(self) -> list[str]:
        override = os.environ.get(_SSH_COMMAND_ENV)
        if override:
            # Split with shell-style quoting so tests can pass multi-word commands.
            import shlex

            return shlex.split(override)

        host = self._cfg.ssh_host
        if not host:
            raise RegistryError(
                "SSH transport requires ssh_host. Set SKILLTOOL_SSH_HOST or "
                "add ssh_host to your config.toml."
            )
        return [
            "ssh",
            *_DEFAULT_SSH_OPTS,
            f"{self._cfg.ssh_user}@{host}",
            "skilltool-server",
        ]

    def build_command(self, *verb_args: str) -> list[str]:
        """Expose the assembled argv so tests can assert on it."""
        return [*self._prefix(), *verb_args]

    def _run(
        self,
        verb_args: Sequence[str],
        *,
        input_bytes: bytes | None = None,
    ) -> bytes:
        cmd = self.build_command(*verb_args)
        try:
            completed = subprocess.run(
                cmd,
                input=input_bytes,
                capture_output=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RegistryError(f"ssh error: {exc}") from exc

        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RegistryError(
                f"ssh error (exit {completed.returncode}): "
                f"{stderr or '<no stderr>'}"
            )
        return completed.stdout

    @staticmethod
    def _decode_json(raw: bytes) -> Any:
        try:
            return json.loads(raw.decode("utf-8", errors="replace") or "null")
        except json.JSONDecodeError as exc:
            raise RegistryError(f"server returned non-JSON output: {exc}") from exc

    # ------------------------------------------------------------------
    # Semantic operations
    # ------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        # There is no ``health`` verb on the server side, but a successful
        # ``list`` invocation means the SSH round-trip works.
        self._run(["list"])
        return {"status": "ok", "transport": "ssh"}

    def package(self, name: str) -> dict[str, Any]:
        raw = self._run(["show", name])
        result = self._decode_json(raw)
        if isinstance(result, dict) and "error" in result:
            raise RegistryError(result["error"])
        return result

    def search(self, query: str) -> list[dict[str, Any]]:
        raw = self._run(["search", query])
        result = self._decode_json(raw)
        if isinstance(result, dict) and "error" in result:
            raise RegistryError(result["error"])
        return result or []

    def download(
        self, name: str, dest: Path, version: str | None = None
    ) -> Path:
        args: list[str] = ["download", name]
        if version:
            args.extend(["--version", version])
        payload = self._run(args)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return dest

    def publish(
        self,
        zip_path: Path,
        *,
        token: str | None = None,
    ) -> dict[str, Any]:
        auth_token = token or self._cfg.token
        if not auth_token:
            raise RegistryError(
                "Publish requires a token. Set SKILLTOOL_TOKEN or pass --token."
            )
        zip_bytes = zip_path.read_bytes()
        data_b64 = base64.b64encode(zip_bytes).decode("ascii")
        raw = self._run(
            ["publish", "--token", auth_token, "--data", data_b64]
        )
        result = self._decode_json(raw)
        if isinstance(result, dict) and "error" in result:
            raise RegistryError(result["error"])
        return result

    def audit(self, limit: int = 50) -> dict[str, Any]:
        raw = self._run(["audit", "--limit", str(limit)])
        result = self._decode_json(raw)
        if isinstance(result, dict) and "error" in result:
            raise RegistryError(result["error"])
        return result


__all__ = ["SshTransport"]
