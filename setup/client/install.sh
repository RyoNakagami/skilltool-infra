#!/usr/bin/env bash
# Install the skilltool CLI globally via `uv tool`.
#
# Usage:
#   ./setup/client/install.sh            # install from this checkout
#   ./setup/client/install.sh --editable # develop against this checkout
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
client_dir="$(cd "${here}/../../client" && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  echo "[skilltool] uv is required. Install it from https://docs.astral.sh/uv/" >&2
  exit 1
fi

if [[ "${1:-}" == "--editable" ]]; then
  uv tool install --force --editable "${client_dir}"
else
  uv tool install --force "${client_dir}"
fi

echo
echo "[skilltool] installed. Verify with: skilltool --version"
