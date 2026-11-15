#!/usr/bin/env bash
# Regenerate the generated API client (cairnml/backend_api/services/<service>.py) from the server's schema.
#
#   scripts/regenerate-api.sh [<server-commit>]
#
# With a commit, it becomes the new pin in scripts/api-schema-ref. Without one the current pin is used.
# Needs: git, python3 with pyhocon and jinja2 (pip install pyhocon jinja2).
set -euo pipefail

cd "$(dirname "$0")/.."
REF_FILE=scripts/api-schema-ref
REF="${1:-$(tr -d '[:space:]' < "$REF_FILE")}"
python3 -c "import pyhocon, jinja2" 2>/dev/null || { echo "needs: pip install pyhocon jinja2" >&2; exit 2; }

SERVER="$(mktemp -d)"
trap 'rm -rf "$SERVER"' EXIT
git -C "$SERVER" init -q
git -C "$SERVER" fetch -q --depth 1 https://github.com/gianlucapargaetzi/cairnml-server.git "$REF"
git -C "$SERVER" checkout -q FETCH_HEAD
REF="$(git -C "$SERVER" rev-parse HEAD)"  # always the full commit, never a branch name or a short sha

PYTHONPATH="$SERVER" python3 -m apiserver.client_generator generate --target sdk --out cairnml/backend_api/services
echo "$REF" > "$REF_FILE"
echo "generated from cairnml-server $REF"
