#!/usr/bin/env bash
# Remove the skilltool CLI installed via `uv tool`.
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "[skilltool] uv is required." >&2
  exit 1
fi

uv tool uninstall skilltool
