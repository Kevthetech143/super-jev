#!/usr/bin/env bash
set -eu
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HAS_ROOTS=0
for arg in "$@"; do
  case "$arg" in --config|--roots-file) HAS_ROOTS=1;; esac
done
if [ "$HAS_ROOTS" -eq 0 ]; then
  case "${SKILL_SEARCH_AI_MODEL:-}" in claude-*) set -- "$@" --config "$DIR/roots-claude.json";; esac
fi
for arg in "$@"; do
  if [ "$arg" = "--local-only" ]; then exec bash "$DIR/launcher.sh" "$@"; fi
done
if [ -z "${TYPESAFE_API_KEY:-}" ]; then
  export SKILL_SEARCH_PROVIDER_CMD="${SKILL_SEARCH_PROVIDER_CMD:-$DIR/deploy/local-key-provider.py}"
fi
if [ -z "${NODE_EXTRA_CA_CERTS:-}" ]; then
  NODE_EXTRA_CA_CERTS="$(python3 -m certifi 2>/dev/null || true)"
  if [ -n "$NODE_EXTRA_CA_CERTS" ]; then export NODE_EXTRA_CA_CERTS; fi
fi
exec bash "$DIR/deploy/hook-wrapper.sh" "$@"
