"""HTTP transport — the default. Wraps the FastAPI registry over httpx."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from ..config import Config
from .base import RegistryError, Transport

DEFAULT_TIMEOUT = 60.0


class HttpTransport(Transport):
    def __init__(self, cfg: Config, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._cfg = cfg
        self._client = httpx.Client(base_url=cfg.registry, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        r = self._client.get("/api/health")
        self._raise(r)
        return r.json()

    def package(self, name: str) -> dict[str, Any]:
        r = self._client.get(f"/api/packages/{name}")
        self._raise(r)
        return r.json()

    def search(self, query: str) -> list[dict[str, Any]]:
        r = self._client.get("/api/search", params={"q": query})
        self._raise(r)
        return r.json().get("results", [])

    def download(
        self, name: str, dest: Path, version: str | None = None
    ) -> Path:
        params = {"version": version} if version else None
        with self._client.stream(
            "GET", f"/api/packages/{name}/download", params=params
        ) as r:
            self._raise(r)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
        return dest

    def audit(self, limit: int = 50) -> dict[str, Any]:
        auth_token = self._cfg.token
        if not auth_token:
            raise RegistryError(
                "Audit requires a token. Set SKILLTOOL_TOKEN."
            )
        headers = {"Authorization": f"Bearer {auth_token}"}
        r = self._client.get(
            "/api/audit", headers=headers, params={"limit": limit}
        )
        self._raise(r)
        return r.json()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
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
        headers = {"Authorization": f"Bearer {auth_token}"}
        with zip_path.open("rb") as fh:
            files = {"file": (zip_path.name, fh, "application/zip")}
            r = self._client.post("/api/publish", headers=headers, files=files)
        self._raise(r)
        return r.json()

    # ------------------------------------------------------------------
    @staticmethod
    def _raise(resp: httpx.Response) -> None:
        if resp.is_success:
            return
        try:
            body = resp.json()
            detail = body.get("detail") or body
        except ValueError:
            detail = resp.text or f"HTTP {resp.status_code}"
        raise RegistryError(f"{resp.status_code}: {detail}")


__all__ = ["HttpTransport"]
