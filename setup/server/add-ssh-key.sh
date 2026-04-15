#!/usr/bin/env bash
# Authorise a developer's public key for the SSH transport.
#
# Usage:
#   ./add-ssh-key.sh <username> <path-to-pubkey-file>
#
# The username is only used for the trailing comment in authorized_keys
# (so ``sort | uniq`` and audit greps stay readable); the identity that
# actually matters is the key itself.
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <username> <path-to-pubkey-file>" >&2
  exit 2
fi

username="$1"
pubkey_path="$2"

if [[ ! -f "${pubkey_path}" ]]; then
  echo "[skilltool] public-key file not found: ${pubkey_path}" >&2
  exit 3
fi

if ! id -u skilltool >/dev/null 2>&1; then
  echo "[skilltool] user 'skilltool' does not exist — run setup/server/install.sh --with-ssh first." >&2
  exit 4
fi

auth_keys="/home/skilltool/.ssh/authorized_keys"

# Pull the first non-comment, non-empty line from the pubkey file and
# re-emit it with a comment annotation we can grep for on revocation.
pubkey=$(
  awk '!/^[[:space:]]*#/ && NF {print; exit}' "${pubkey_path}"
)
if [[ -z "${pubkey}" ]]; then
  echo "[skilltool] ${pubkey_path}: no usable public key found" >&2
  exit 5
fi

annotated="${pubkey} # skilltool:${username}"

if sudo grep -qF -- "${pubkey%% *} ${pubkey#* }" "${auth_keys}" 2>/dev/null; then
  echo "[skilltool] that key is already authorised — nothing to do."
  exit 0
fi

printf '%s\n' "${annotated}" | sudo tee -a "${auth_keys}" >/dev/null
sudo chown skilltool:skilltool "${auth_keys}"
sudo chmod 600 "${auth_keys}"

cat <<EOF
✓ authorised ${username}'s key in ${auth_keys}
  the client can now reach this host with:
    export SKILLTOOL_TRANSPORT=ssh
    export SKILLTOOL_SSH_USER=skilltool
    export SKILLTOOL_SSH_HOST=<this-host-on-tailscale>
EOF
