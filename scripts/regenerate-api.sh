#!/usr/bin/env bash
# Regenerate the generated API client (cairnml/backend_api/services/<service>.py) from the server's schema.
#
#   scripts/regenerate-api.sh [<server-ref>]
#
# Without an argument the server's main is used (what the api contract CI job compares with).
# Needs: git, python3 with pyhocon and jinja2 (pip install pyhocon jinja2).
set -euo pipefail

cd "$(dirname "$0")/.."
REF="${1:-main}"
python3 -c "import pyhocon, jinja2" 2>/dev/null || { echo "needs: pip install pyhocon jinja2" >&2; exit 2; }

SERVER="$(mktemp -d)"
trap 'rm -rf "$SERVER"' EXIT
git -C "$SERVER" init -q
git -C "$SERVER" fetch -q --depth 1 https://github.com/gianlucapargaetzi/cairnml-server.git "$REF"
git -C "$SERVER" checkout -q FETCH_HEAD
REF="$(git -C "$SERVER" rev-parse HEAD)"

PYTHONPATH="$SERVER" python3 -m apiserver.client_generator generate --target sdk --out cairnml/backend_api/services
echo "generated from cairnml-server $REF"
