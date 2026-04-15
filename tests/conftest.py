"""Global pytest fixtures for the skilltool test suite.

We add both the client source tree (``client/src``) and the registry
directory to ``sys.path`` so tests can ``import skilltool`` and
``import server`` / ``import server_cli`` without needing either to
be pip-installed.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CLIENT_SRC = _REPO / "client" / "src"
_REGISTRY = _REPO / "registry"

for p in (_CLIENT_SRC, _REGISTRY):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
