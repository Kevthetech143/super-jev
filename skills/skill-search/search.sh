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
# Extra skill folders owned by this user (outside the release, never shipped): a JSON array in
# $SKILL_SEARCH_EXTRA_ROOTS, default ~/.local/state/super-jev/skill-roots.json. They are added after the
# default roots; a missing folder is skipped (named on stderr when SKILL_SEARCH_DEBUG=1). A file that is not
# a JSON array is ignored with one stderr line. Not used when --config or --roots-file is given.
EXTRA="${SKILL_SEARCH_EXTRA_ROOTS:-$HOME/.local/state/super-jev/skill-roots.json}"
BASE="$DIR/roots.json"
if [ "${CLAUDECODE:-}" = 1 ]; then BASE="$DIR/roots-claude.json"; fi
if [ "$HAS_ROOTS" -eq 0 ] && [ -r "$EXTRA" ] && [ -r "$BASE" ]; then
  MERGED="$(mktemp /tmp/skill-search-merged.XXXXXX)"
  trap 'rm -f "$MERGED"' EXIT
  python3 - "$BASE" "$EXTRA" "$MERGED" <<'PYEOF'
import json, os, sys
base, extra, dst = sys.argv[1:4]
debug = os.environ.get("SKILL_SEARCH_DEBUG") == "1"
roots = json.load(open(base))
try:
    more = json.load(open(extra))
    if not isinstance(more, list):
        raise ValueError("not a JSON array")
except Exception as e:
    sys.stderr.write("skill-search: ignoring extra roots %s (%s)\n" % (extra, e))
    more = []
have = {os.path.expanduser(r) for r in roots if isinstance(r, str)}
for entry in more:
    path = os.path.expanduser(entry.strip()) if isinstance(entry, str) else ""
    if not path or not os.path.isabs(path) or not os.path.isdir(path):
        if debug:
            sys.stderr.write("skill-search: extra root skipped: %r\n" % (entry,))
        continue
    if path not in have:
        roots.append(path)
        have.add(path)
json.dump(roots, open(dst, "w"))
PYEOF
  set -- "$@" --config "$MERGED"
  HAS_ROOTS=1
fi
if [ "$HAS_ROOTS" -eq 0 ]; then
  if [ "${CLAUDECODE:-}" = 1 ]; then set -- "$@" --config "$DIR/roots-claude.json"; fi
fi
for arg in "$@"; do
  if [ "$arg" = "--local-only" ]; then bash "$DIR/launcher.sh" "$@"; exit $?; fi
done
# No key in the environment: use this machine's own provider if it has one (gitignored, never shipped).
if [ -z "${TYPESAFE_API_KEY:-}" ] && [ -f "$DIR/deploy/local-key-provider.py" ]; then
  export SKILL_SEARCH_PROVIDER_CMD="${SKILL_SEARCH_PROVIDER_CMD:-$DIR/deploy/local-key-provider.py}"
fi
if [ -z "${NODE_EXTRA_CA_CERTS:-}" ]; then
  NODE_EXTRA_CA_CERTS="$(python3 -m certifi 2>/dev/null || true)"
  if [ -n "$NODE_EXTRA_CA_CERTS" ]; then export NODE_EXTRA_CA_CERTS; fi
fi
bash "$DIR/deploy/hook-wrapper.sh" "$@"
