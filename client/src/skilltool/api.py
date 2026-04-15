"""Transport factory.

Previously this module held a ``RegistryClient`` class that spoke HTTP
directly. It now dispatches to whichever transport is configured
(``http`` or ``ssh``). ``RegistryClient`` is preserved as a factory
alias so existing callers — chiefly :mod:`skilltool.commands` — keep
working unchanged.
"""
from __future__ import annotations

from .config import Config
from .transport.base import RegistryError, Transport
from .transport.http import HttpTransport
from .transport.ssh import SshTransport


def get_transport(cfg: Config) -> Transport:
    """Pick a transport based on ``cfg.transport``.

    * ``"http"`` (default) → :class:`HttpTransport`
    * ``"ssh"`` → :class:`SshTransport`
    """
    mode = (cfg.transport or "http").lower()
    if mode == "http":
        return HttpTransport(cfg)
    if mode == "ssh":
        return SshTransport(cfg)
    raise RegistryError(
        f"unknown transport: {mode!r} (expected 'http' or 'ssh')"
    )


def RegistryClient(cfg: Config) -> Transport:
    """Back-compat factory.

    Older code — and the current ``commands.py`` — did ``with
    RegistryClient(cfg) as client`` when only HTTP existed. It now
    returns whichever transport is configured, auto-selecting via
    :func:`get_transport`.
    """
    return get_transport(cfg)


__all__ = ["RegistryClient", "RegistryError", "Transport", "get_transport"]
