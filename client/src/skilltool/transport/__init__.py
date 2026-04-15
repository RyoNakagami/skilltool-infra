"""Pluggable transports for the skilltool registry client.

Two implementations today:

- :class:`~skilltool.transport.http.HttpTransport` — reach the registry
  over HTTP on the Tailscale interface (port 8765).
- :class:`~skilltool.transport.ssh.SshTransport` — reach the registry by
  invoking ``skilltool-server`` on Server A via SSH. No HTTP server is
  required on the remote side.

``api.get_transport(cfg)`` picks between them based on ``cfg.transport``.
"""
from .base import Transport
from .http import HttpTransport
from .ssh import SshTransport

__all__ = ["Transport", "HttpTransport", "SshTransport"]
