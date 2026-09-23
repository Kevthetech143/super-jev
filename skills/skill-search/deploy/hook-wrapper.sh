#!/bin/sh
# Local deployment hook — REFERENCE implementation of ENV-CONTRACT.md.
# NOT installed by default; the lead adapts it with the existing local
# provider at deployment time. See ENV-CONTRACT.md for the exact contract.
set -u
for arg in "$@"; do
  if [ "$arg" = "--local-only" ]; then
    exec bash "$(dirname "$0")/../launcher.sh" "$@"
  fi
done
PROVIDER="${SKILL_SEARCH_PROVIDER_CMD:-}"
if [ -z "${TYPESAFE_API_KEY:-}" ]; then
  if [ -z "$PROVIDER" ]; then
    printf '%s\n' '{"status":"fallback","source":"local","candidates":[],"error":"No Jev credential provider configured; use the local index"}'
    exit 2
  fi
  KEY="$($PROVIDER 2>/dev/null)" || { printf '%s\n' '{"status":"fallback","source":"local","candidates":[],"error":"Jev credential provider failed; use the local index"}' ; exit 2; }
  [ -n "$KEY" ] || { printf '%s\n' '{"status":"fallback","source":"local","candidates":[],"error":"Jev credential provider returned no key; use the local index"}' ; exit 2; }
  TYPESAFE_API_KEY="$KEY"; export TYPESAFE_API_KEY
fi
exec bash "$(dirname "$0")/../launcher.sh" "$@"
