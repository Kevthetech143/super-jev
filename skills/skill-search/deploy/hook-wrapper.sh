#!/bin/sh
# Key hook for the skill finder: search.sh runs it for every live search. With TYPESAFE_API_KEY
# in the environment it does nothing; otherwise it asks the provider command for the key.
# See ENV-CONTRACT.md for the exact contract.
set -u
for arg in "$@"; do
  if [ "$arg" = "--local-only" ]; then
    exec bash "$(dirname "$0")/../launcher.sh" "$@"
  fi
done
PROVIDER="${SKILL_SEARCH_PROVIDER_CMD:-}"
if [ -z "${TYPESAFE_API_KEY:-}" ]; then
  if [ -z "$PROVIDER" ]; then
    printf '%s\n' '{"status":"error","candidates":[],"error":"No TYPESAFE_API_KEY in the environment and no credential provider configured (docs/GETTING-STARTED.md, step 2); rerun with --local-only for unverified local guesses"}'
    exit 2
  fi
  KEY="$($PROVIDER 2>/dev/null)" || { printf '%s\n' '{"status":"error","candidates":[],"error":"Jev credential provider failed; rerun with --local-only for unverified local guesses"}' ; exit 2; }
  [ -n "$KEY" ] || { printf '%s\n' '{"status":"error","candidates":[],"error":"Jev credential provider returned no key; rerun with --local-only for unverified local guesses"}' ; exit 2; }
  TYPESAFE_API_KEY="$KEY"; export TYPESAFE_API_KEY
fi
exec bash "$(dirname "$0")/../launcher.sh" "$@"
