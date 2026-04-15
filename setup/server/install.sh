#!/usr/bin/env bash
# Bootstrap Server A for the skilltool registry.
#
# Prereqs on Server A:
#   - docker (with the compose plugin)
#   - tailscale connected so the box is reachable at its tailnet IP
#
# Usage:
#   ./setup/server/install.sh                     # HTTP transport only
#   ./setup/server/install.sh --with-ssh          # also enable SSH transport
#
# --with-ssh does three extra things (needs sudo):
#   1. Creates a `skilltool` system user + ~/.ssh/authorized_keys stub
#   2. Installs server_cli.py to /usr/local/bin/skilltool-server
#   3. Ensures the user can reach registry/data (packages, users.toml,
#      publish.log) by adding them to the data dir's group.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "${here}/../.." && pwd)"
registry_dir="${repo_dir}/registry"

with_ssh=0
for arg in "$@"; do
  case "${arg}" in
    --with-ssh) with_ssh=1 ;;
    -h|--help)
      sed -n '2,18p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "[skilltool] unknown argument: ${arg}" >&2
      exit 2
      ;;
  esac
done

cd "${registry_dir}"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "[skilltool] created registry/.env from the example."
  echo "[skilltool] edit SKILLTOOL_PUBLISH_TOKEN-related vars if needed."
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "[skilltool] docker is not installed on this host." >&2
  exit 1
fi

docker compose up -d --build
docker compose ps

if [[ "${with_ssh}" -eq 1 ]]; then
  echo
  echo "[skilltool] enabling SSH transport (needs sudo)..."

  if ! command -v sudo >/dev/null 2>&1; then
    echo "[skilltool] --with-ssh requires sudo; aborting." >&2
    exit 1
  fi

  # 1. Create the skilltool user (idempotent).
  if ! id -u skilltool >/dev/null 2>&1; then
    sudo useradd --create-home --shell /bin/bash skilltool
    echo "[skilltool] created user 'skilltool'"
  else
    echo "[skilltool] user 'skilltool' already exists — leaving it alone"
  fi
  sudo install -d -o skilltool -g skilltool -m 700 /home/skilltool/.ssh
  sudo touch /home/skilltool/.ssh/authorized_keys
  sudo chown skilltool:skilltool /home/skilltool/.ssh/authorized_keys
  sudo chmod 600 /home/skilltool/.ssh/authorized_keys

  # 2. Install skilltool-server command.
  sudo ln -sf "${registry_dir}/server_cli.py" /usr/local/bin/skilltool-server
  sudo chmod +x "${registry_dir}/server_cli.py"
  echo "[skilltool] installed /usr/local/bin/skilltool-server -> ${registry_dir}/server_cli.py"

  # 3. Make the data dir reachable to the skilltool user.
  if [[ -d "${registry_dir}/data" ]]; then
    sudo chgrp -R skilltool "${registry_dir}/data"
    sudo chmod -R g+rX "${registry_dir}/data"
    # users.toml stays 0640 (owner rw, skilltool group r).
    if [[ -f "${registry_dir}/data/users.toml" ]]; then
      sudo chmod 640 "${registry_dir}/data/users.toml"
    fi
    echo "[skilltool] granted skilltool group access to ${registry_dir}/data"
  fi

  cat <<EOF

✓ SSH transport ready on the server.

Register a developer's public key next:
  ./setup/server/add-ssh-key.sh <username> path/to/id_ed25519.pub

The developer should then point their client at this host:
  export SKILLTOOL_TRANSPORT=ssh
  export SKILLTOOL_SSH_HOST=<this-host-on-tailscale>
  export SKILLTOOL_SSH_USER=skilltool
EOF
fi
