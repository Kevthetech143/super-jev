#!/usr/bin/env bash
set -eu
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The only place skill roots are chosen: the finder searches the asking agent's own skill folders.
# Inside Claude Code (it sets CLAUDECODE=1 for its shells and hooks) that is ~/.claude/skills;
# any other agent uses its own roots.json (copy roots.example.json). --config / --roots-file win.
HAS_ROOTS=0
for arg in "$@"; do
  case "$arg" in --config|--roots-file) HAS_ROOTS=1;; esac
done
if [ "$HAS_ROOTS" -eq 0 ]; then
  if [ "${CLAUDECODE:-}" = 1 ]; then set -- "$@" --config "$DIR/roots-claude.json"; fi
fi
for arg in "$@"; do
  if [ "$arg" = "--local-only" ]; then exec bash "$DIR/launcher.sh" "$@"; fi
done
# No key in the environment: use this machine's own provider if it has one (gitignored, never shipped).
if [ -z "${TYPESAFE_API_KEY:-}" ] && [ -f "$DIR/deploy/local-key-provider.py" ]; then
  export SKILL_SEARCH_PROVIDER_CMD="${SKILL_SEARCH_PROVIDER_CMD:-$DIR/deploy/local-key-provider.py}"
fi
if [ -z "${NODE_EXTRA_CA_CERTS:-}" ]; then
  NODE_EXTRA_CA_CERTS="$(python3 -m certifi 2>/dev/null || true)"
  if [ -n "$NODE_EXTRA_CA_CERTS" ]; then export NODE_EXTRA_CA_CERTS; fi
fi
exec bash "$DIR/deploy/hook-wrapper.sh" "$@"
