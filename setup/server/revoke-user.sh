#!/usr/bin/env bash
# Disable a user's token by setting ``disabled = true`` in users.toml.
# No server restart required — the next request will reject the token.
#
# Usage:
#   ./revoke-user.sh <username>
#
# Environment overrides:
#   USERS_FILE   Path to users.toml (default: <repo>/registry/data/users.toml)
set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
  echo "[skilltool] python3 is required." >&2
  exit 1
fi

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <username>" >&2
  exit 2
fi

username="$1"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
default_users_file="$(cd "${here}/../../registry" && pwd)/data/users.toml"
users_file="${USERS_FILE:-${default_users_file}}"

if [[ ! -f "${users_file}" ]]; then
  echo "[skilltool] ${users_file} does not exist." >&2
  exit 3
fi

python3 - "${users_file}" "${username}" <<'PY'
import re
import sys
import tomllib
from pathlib import Path

path = Path(sys.argv[1])
username = sys.argv[2]
text = path.read_text(encoding="utf-8")

try:
    data = tomllib.loads(text)
except tomllib.TOMLDecodeError as exc:
    print(f"[skilltool] {path} is not valid TOML: {exc}", file=sys.stderr)
    sys.exit(4)

users = data.get("users") or {}
if username not in users:
    print(f"[skilltool] user '{username}' not found in {path}", file=sys.stderr)
    sys.exit(5)

if users[username].get("disabled"):
    print(f"[skilltool] user '{username}' is already disabled — nothing to do.")
    sys.exit(0)

# Walk line by line to locate the [users.<name>] block. A regex on raw
# text doesn't work reliably because other sections may contain inline
# arrays like `teams = ["a", "b"]` whose brackets confuse naive patterns.
#
# TOML section headers are always on their own line (optionally indented).
header_re = re.compile(r"^\s*\[[^\[\]]+\]\s*$")
target_header_re = re.compile(
    r"^\s*\[users\." + re.escape(username) + r"\]\s*$"
)

lines = text.splitlines(keepends=True)
start = next(
    (i for i, line in enumerate(lines) if target_header_re.match(line)),
    None,
)
if start is None:
    print(
        f"[skilltool] could not locate [users.{username}] block",
        file=sys.stderr,
    )
    sys.exit(6)

# Section runs from `start` up to (but not including) the next header line
# — or EOF if no further section exists.
end = next(
    (j for j in range(start + 1, len(lines)) if header_re.match(lines[j])),
    len(lines),
)

# Insert `disabled = true` at the last non-blank line of the block so we
# don't end up separated from the section by a blank line.
insert_at = end
while insert_at > start + 1 and lines[insert_at - 1].strip() == "":
    insert_at -= 1

new_lines = lines[:insert_at] + ["disabled = true\n"] + lines[insert_at:]
new_text = "".join(new_lines)

# Re-validate before writing.
try:
    tomllib.loads(new_text)
except tomllib.TOMLDecodeError as exc:
    print(
        f"[skilltool] refusing to write: would produce invalid TOML ({exc})",
        file=sys.stderr,
    )
    sys.exit(7)

tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text(new_text, encoding="utf-8")
tmp.replace(path)
print(f"✓ user '{username}' revoked in {path}")
print("  the token is rejected on the next publish request (no restart needed)")
PY
