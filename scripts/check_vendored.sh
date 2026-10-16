#!/usr/bin/env bash
# Checks that the vendored identity helpers are byte-identical to the server's copies.
# The server checkout is found next to this repository unless CLEARML_SERVER_IDP is set.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
server="${CLEARML_SERVER_IDP:-$here/../clearml-server/apiserver/idp}"
status=0

for name in assertion.py keys.py; do
  ours=$(sha256sum "$here/clearml/idp/$name" | cut -d' ' -f1)
  theirs=$(sha256sum "$server/$name" | cut -d' ' -f1)
  if [ "$ours" = "$theirs" ]; then
    echo "same  $name"
  else
    echo "DIFF  $name"
    status=1
  fi
done
exit "$status"
