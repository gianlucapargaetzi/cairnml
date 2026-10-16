#!/usr/bin/env bash
# Checks that the vendored identity helpers are byte-identical in the server and the agent.
# The other checkouts are found next to this repository unless CLEARML_SERVER_IDP or
# CLEARML_AGENT_IDP are set.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
server="${CLEARML_SERVER_IDP:-$here/../clearml-server/apiserver/idp}"
agent="${CLEARML_AGENT_IDP:-$here/../clearml-agent/clearml_agent/idp}"
status=0

check() {
  local name="$1" dir="$2"
  local ours theirs
  ours=$(sha256sum "$here/clearml/idp/$name" | cut -d' ' -f1)
  if [ ! -f "$dir/$name" ]; then
    echo "MISSING $dir/$name"
    status=1
    return
  fi
  theirs=$(sha256sum "$dir/$name" | cut -d' ' -f1)
  if [ "$ours" = "$theirs" ]; then
    echo "same  $name  ($dir)"
  else
    echo "DIFF  $name  ($dir)"
    status=1
  fi
}

check assertion.py "$server"
check keys.py "$server"
for name in assertion.py keys.py device.py browser_login.py enrol.py; do
  check "$name" "$agent"
done
exit "$status"
