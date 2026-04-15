"""Abstract transport contract.

All transports expose the same semantic operations that
``commands.py`` needs; the URL- vs. verb-translation plumbing is an
implementation detail of each concrete transport.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class RegistryError(RuntimeError):
    """Raised when a transport fails to carry out an operation.

    Kept at module scope so ``commands.py`` can import a single exception
    type regardless of which transport is in play.
    """


class Transport(ABC):
    """Minimum operation set required by ``skilltool.commands``."""

    # ------------------------------------------------------------------
    # Context-manager protocol.
    # ------------------------------------------------------------------
    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:  # pragma: no cover - optional override
        """Release any resources. Default: no-op."""
        return None

    # ------------------------------------------------------------------
    # Semantic operations.
    # ------------------------------------------------------------------
    @abstractmethod
    def health(self) -> dict[str, Any]:
        """GET /api/health equivalent."""

    @abstractmethod
    def package(self, name: str) -> dict[str, Any]:
        """Return ``{name, versions, latest, metadata}`` for ``name``."""

    @abstractmethod
    def search(self, query: str) -> list[dict[str, Any]]:
        """Return a list of matching package summaries."""

    @abstractmethod
    def download(
        self, name: str, dest: Path, version: str | None = None
    ) -> Path:
        """Write the package zip to ``dest`` and return the path."""

    @abstractmethod
    def publish(
        self,
        zip_path: Path,
        *,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Upload ``zip_path``. Returns the publish response."""

    def audit(self, limit: int = 50) -> dict[str, Any]:  # pragma: no cover - optional
        """Fetch audit-log entries. Optional operation; not every transport supports it."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support the audit operation"
        )
