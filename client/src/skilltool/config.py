"""Configuration loading.

Resolution precedence (highest first):

    1. Environment variables — ``SKILLTOOL_{REGISTRY,TOKEN,TRANSPORT,
       SSH_HOST,SSH_USER}``.
    2. ``~/.config/skilltool/config.toml`` (respects ``$XDG_CONFIG_HOME``).
    3. Localhost auto-detect — if nothing above is set and
       ``http://localhost:8765/api/health`` answers within 1 s, the
       registry URL is auto-pinned to ``http://localhost:8765``. This
       makes the CLI "just work" when run on Server A itself.
    4. Built-in defaults (``DEFAULT_REGISTRY`` / ``DEFAULT_TRANSPORT`` /
       ``DEFAULT_SSH_USER``).
"""
from __future__ import annotations

import os
import socket
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REGISTRY = "http://localhost:8765"
DEFAULT_TRANSPORT = "http"
DEFAULT_SSH_USER = "skilltool"
_LOCALHOST_PROBE = "http://localhost:8765/api/health"
_LOCALHOST_PROBE_TIMEOUT = 1.0


def config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "skilltool" / "config.toml"


def _localhost_registry_running() -> bool:
    """Best-effort probe of ``http://localhost:8765/api/health``.

    Returns True within a hard 1 s budget. Any error — DNS, connection,
    timeout, HTTP != 2xx — is treated as "no local registry".
    """
    try:
        with urllib.request.urlopen(
            _LOCALHOST_PROBE, timeout=_LOCALHOST_PROBE_TIMEOUT
        ) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, socket.timeout, OSError, ValueError):
        return False


@dataclass(frozen=True)
class Config:
    registry: str
    token: str | None
    transport: str
    ssh_host: str | None
    ssh_user: str
    registry_source: str  # "env" | "file" | "auto" | "default"
    token_source: str     # "env" | "file" | "none"
    transport_source: str
    ssh_host_source: str
    ssh_user_source: str
    config_file: Path

    @classmethod
    def load(cls) -> "Config":
        path = config_path()
        file_data: dict[str, object] = {}
        if path.exists():
            with path.open("rb") as fh:
                file_data = tomllib.load(fh)

        def resolved(
            env_name: str,
            file_key: str,
            default: str | None,
            *,
            allow_auto: bool = False,
        ) -> tuple[str | None, str]:
            env_val = os.environ.get(env_name)
            if env_val:
                return env_val, "env"
            file_val = file_data.get(file_key)
            if isinstance(file_val, str) and file_val:
                return file_val, "file"
            if allow_auto and _localhost_registry_running():
                return DEFAULT_REGISTRY, "auto"
            if default is None:
                return None, "none"
            return default, "default"

        registry, registry_src = resolved(
            "SKILLTOOL_REGISTRY", "registry", DEFAULT_REGISTRY, allow_auto=True
        )
        token, token_src = resolved("SKILLTOOL_TOKEN", "token", None)
        transport, transport_src = resolved(
            "SKILLTOOL_TRANSPORT", "transport", DEFAULT_TRANSPORT
        )
        ssh_host, ssh_host_src = resolved(
            "SKILLTOOL_SSH_HOST", "ssh_host", None
        )
        ssh_user, ssh_user_src = resolved(
            "SKILLTOOL_SSH_USER", "ssh_user", DEFAULT_SSH_USER
        )

        # mypy-friendly narrowing: resolved() can only return None when
        # default is None. registry/transport/ssh_user always have defaults.
        assert registry is not None
        assert transport is not None
        assert ssh_user is not None

        return cls(
            registry=registry.rstrip("/"),
            token=token,
            transport=transport.lower(),
            ssh_host=ssh_host,
            ssh_user=ssh_user,
            registry_source=registry_src,
            token_source=token_src,
            transport_source=transport_src,
            ssh_host_source=ssh_host_src,
            ssh_user_source=ssh_user_src,
            config_file=path,
        )
