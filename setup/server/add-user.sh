#!/usr/bin/env bash
# Create a new per-user publish token in users.toml.
#
# Usage:
#   ./add-user.sh <username> [team1,team2,...]
#
# Environment overrides:
#   USERS_FILE   Path to users.toml (default: <repo>/registry/data/users.toml)
#
# On success the generated token is printed once to stdout. Share it with
# the user via Slack DM or 1Password — never via email or a public channel.
set -euo pipefail

if ! command -v openssl >/dev/null 2>&1; then
  echo "[skilltool] openssl is required." >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "[skilltool] python3 is required." >&2
  exit 1
fi

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <username> [team1,team2,...]" >&2
  exit 2
fi

username="$1"
teams_csv="${2:-}"

if [[ ! "${username}" =~ ^[a-zA-Z][a-zA-Z0-9_-]*$ ]]; then
  echo "[skilltool] invalid username: ${username}" >&2
  echo "  must match ^[a-zA-Z][a-zA-Z0-9_-]*\$" >&2
  exit 2
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
default_users_file="$(cd "${here}/../../registry" && pwd)/data/users.toml"
users_file="${USERS_FILE:-${default_users_file}}"

mkdir -p "$(dirname "${users_file}")"
touch "${users_file}"

secret="$(openssl rand -hex 32)"
token="tok_${username}_${secret}"

# Abort cleanly if the user already exists. Appending a duplicate
# [users.<name>] block would make users.toml fail to parse (TOML forbids
# duplicate table headers) and the server would refuse all auth.
python3 - "$users_file" "$username" <<'PY'
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        sys.stderr.write(
            "[skilltool] TOML parser not available. Use Python 3.11+ or\n"
            "install the backport: pip install --user tomli\n"
        )
        sys.exit(10)

path = Path(sys.argv[1])
username = sys.argv[2]
data = tomllib.loads(path.read_text(encoding="utf-8") or "")
if username in (data.get("users") or {}):
    print(f"[skilltool] user '{username}' already exists in {path}", file=sys.stderr)
    sys.exit(3)
PY

# Build `teams = ["a", "b", ...]` safely.
teams_toml="[]"
if [[ -n "${teams_csv}" ]]; then
  teams_toml="[$(python3 -c "
import json, sys
parts = [p.strip() for p in sys.argv[1].split(',') if p.strip()]
print(', '.join(json.dumps(p) for p in parts))
" "${teams_csv}")]"
fi

tmp="$(mktemp "${users_file}.XXXXXX")"
trap 'rm -f "${tmp}"' EXIT
cat "${users_file}" >"${tmp}"
# Ensure the existing file ends with a newline so we don't glue blocks
# onto the previous one.
if [[ -s "${tmp}" ]] && [[ "$(tail -c1 "${tmp}" | od -An -c | tr -d ' ')" != "\\n" ]]; then
  printf '\n' >>"${tmp}"
fi

cat >>"${tmp}" <<EOF

[users.${username}]
token = "${token}"
teams = ${teams_toml}
EOF

# Sanity-check the result before replacing the live file.
python3 - "${tmp}" <<'PY'
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        sys.stderr.write(
            "[skilltool] TOML parser not available. Use Python 3.11+ or\n"
            "install the backport: pip install --user tomli\n"
        )
        sys.exit(10)

try:
    tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except tomllib.TOMLDecodeError as exc:
    print(f"[skilltool] generated users.toml is invalid: {exc}", file=sys.stderr)
    sys.exit(4)
PY

mv "${tmp}" "${users_file}"
trap - EXIT
chmod 600 "${users_file}" 2>/dev/null || true

cat <<EOF
✓ user '${username}' added to ${users_file}
  token: ${token}

Share this token with ${username} via Slack DM or 1Password.
The user should set it on their machine with one of:

  export SKILLTOOL_TOKEN=${token}

  # or in ~/.config/skilltool/config.toml
  token = "${token}"
EOF
