#!/usr/bin/env bash
# skill-search launcher — thin wrapper around the shared skill-search runtime.
#
# The runtime lives in a super-jev checkout:
#   node <repo>/src/skill-search-cli.ts --roots-file ROOTS.json --request-file REQUEST.json [--local-only]
#
# This launcher:
#   - expands ~ in the generic roots config and writes a temp ROOTS.json of
#     absolute paths (the runtime only accepts absolute paths); entries are
#     validated for type and absoluteness AFTER expansion. ANY invalid entry
#     (non-string, empty, or not absolute after ~ expansion) makes the launcher
#     fail explicitly — it never warns-and-continues over a filtered set.
#     Unreadable-but-valid absolute roots are PRESERVED so the runtime can
#     report the search incomplete (never silently narrowing the search),
#   - forwards --request-file and --local-only untouched,
#   - prints exactly one compact JSON object on stdout (the runtime's reply,
#     validated against the contract, or an error object in the
#     same shape),
#   - never prints secrets, full catalogs, or raw provider payloads; malformed
#     runtime output is replaced by a sanitized diagnostic, never forwarded,
#   - never pretends success: a missing node binary, a missing runtime entry,
#     or a failed runtime call yields {"status":"error","error":"<real cause>"}
#     and a nonzero exit. The runtime's own one-line failure text is passed through.
#
# Usage:
#   launcher.sh --request-file REQUEST.json [--roots-file ROOTS.json]
#               [--config roots.json] [--repo /path/to/super-jev]
#               [--node-bin /path/to/node] [--local-only]
#
# Exit codes: 0 = search completed (stdout holds the runtime JSON; a status of
#                 fallback means local guesses, not a match);
#             2 = launcher-level failure (stdout holds an {"status":"error"} object).
#
# Test hook: SKILL_SEARCH_RUN_TIMEOUT_SECS overrides the runtime wait deadline.

set -u

LAUNCHER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd -P)"   # real path, so a symlinked skill folder still finds its release
DEFAULT_REPO="$(cd "$LAUNCHER_DIR/../.." && pwd -P)"   # the release/checkout this launcher ships in
DEFAULT_CONFIG="$LAUNCHER_DIR/roots.json"
RUNTIME_ENTRY="src/skill-search-cli.ts"
RUN_TIMEOUT_SECS="${SKILL_SEARCH_RUN_TIMEOUT_SECS:-120}"

REPO="$DEFAULT_REPO"
CONFIG="$DEFAULT_CONFIG"
CONFIG_GIVEN=0
ROOTS_FILE=""
REQUEST_FILE=""
NODE_BIN="${SKILL_SEARCH_NODE_BIN:-node}"
LOCAL_ONLY=0

usage() {
  cat <<'EOF'
Usage: launcher.sh --request-file REQUEST.json [--roots-file ROOTS.json]
                   [--config roots.json] [--repo /path/to/super-jev]
                   [--node-bin /path/to/node] [--local-only]

  --request-file  JSON file: {"request": "<original user wording>",
                              "context": ["<needed recent turn>", ...]}
  --roots-file    JSON array of trusted absolute skill-directory paths
                  (caller-owned; bypasses --config)
  --config        generic roots config with ~ entries (default: roots.json
                  next to this launcher); ~ is expanded by the launcher
  --repo          super-jev checkout holding src/skill-search-cli.ts
                  (default: the release this launcher ships in)
  --node-bin      node binary override, for isolated tests (default: node)
  --local-only    forwarded to the runtime: local index only, no model call

Prints one compact JSON object on stdout. Exit 0 on a completed search,
exit 2 with a {"status":"error","error":"<cause>"} object when it could not run.
EOF
}

# error_json <error-message> — one compact {"status":"error"} object with the real cause on stdout, exit 2.
error_json() {
  python3 -c '
import json, sys
print(json.dumps({"status": "error", "candidates": [], "error": sys.argv[1]},
                 separators=(",", ":")))
' "$1"
  exit 2
}

# need_value <option> <remaining-argc> — a value-taking option must be followed
# by its value; reject promptly instead of mis-shifting and looping.
need_value() {
  if [ "$2" -lt 2 ]; then
    error_json "option $1 requires a value"
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --request-file) need_value "$1" "$#"; REQUEST_FILE="$2"; shift 2 ;;
    --roots-file)   need_value "$1" "$#"; ROOTS_FILE="$2";   shift 2 ;;
    --config)       need_value "$1" "$#"; CONFIG="$2"; CONFIG_GIVEN=1; shift 2 ;;
    --repo)         need_value "$1" "$#"; REPO="$2";         shift 2 ;;
    --node-bin)     need_value "$1" "$#"; NODE_BIN="$2";     shift 2 ;;
    --local-only)   LOCAL_ONLY=1;          shift ;;
    -h|--help)      usage; exit 0 ;;
    *) echo "skill-search launcher: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# --- validate request file -------------------------------------------------
if [ -z "$REQUEST_FILE" ]; then
  error_json "--request-file is required"
fi
if [ ! -r "$REQUEST_FILE" ]; then
  error_json "request file not readable: $REQUEST_FILE"
fi
if ! python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
assert isinstance(d, dict) and isinstance(d.get("request"), str) and d["request"].strip(), "bad request"
' "$REQUEST_FILE" 2>/dev/null; then
  error_json "request file is not a JSON object with a non-empty string \"request\": $REQUEST_FILE"
fi

# --- resolve roots ----------------------------------------------------------
# ROOTS_SRC is the config to expand; ROOTS_FILE (explicit) wins over --config.
# Validation only: entries must be non-empty strings and absolute AFTER ~
# expansion. ANY invalid entry fails the launcher explicitly (error JSON,
# exit 2) — never warn-and-continue, never claim complete over a filtered
# set. Unreadable-but-valid absolute roots are PRESERVED so the runtime can
# report the search incomplete.
ROOTS_SRC="${ROOTS_FILE:-$CONFIG}"
if [ -z "$ROOTS_SRC" ] || [ ! -r "$ROOTS_SRC" ]; then
  # Only the default file can be missing for a reason a new user can fix: it is never shipped.
  if [ -z "$ROOTS_FILE" ] && [ "$CONFIG_GIVEN" -eq 0 ]; then
    error_json "no skill folders are set for this agent: in $LAUNCHER_DIR, copy roots.example.json to roots.json and list your skill folders (Claude Code needs none: it searches ~/.claude/skills)"
  fi
  error_json "roots source not readable: ${ROOTS_SRC:-<none>}"
fi

TMP_ROOTS="$(mktemp /tmp/skill-search-roots.XXXXXX)"
trap 'rm -f "$TMP_ROOTS"' EXIT

INVALID="$(python3 - "$ROOTS_SRC" "$TMP_ROOTS" <<'PYEOF'
import json, os, sys
src, dst = sys.argv[1], sys.argv[2]
try:
    raw = json.load(open(src))
except Exception as e:
    sys.stderr.write("roots source is not valid JSON: %s\n" % e)
    sys.exit(1)
if not isinstance(raw, list):
    sys.stderr.write("roots source is not a JSON array\n")
    sys.exit(1)
kept, invalid = [], 0
for entry in raw:
    if not isinstance(entry, str) or not entry.strip():
        invalid += 1
        continue
    path = os.path.expanduser(entry.strip())
    if not os.path.isabs(path):
        invalid += 1
        continue
    kept.append(path)
json.dump(kept, open(dst, "w"))
print(invalid)
PYEOF
)" || error_json "roots config malformed: $ROOTS_SRC"

if [ "$INVALID" -gt 0 ] 2>/dev/null; then
  error_json "$INVALID invalid root entr(ies) in config $ROOTS_SRC (each entry must be a non-empty string and absolute after ~ expansion); refusing an incomplete search"
fi
if [ "$(cat "$TMP_ROOTS")" = "[]" ]; then
  error_json "no valid skill roots in config; refusing an incomplete search"
fi

# --- runtime availability ----------------------------------------------------
ENTRY="$REPO/$RUNTIME_ENTRY"
if [ ! -f "$ENTRY" ]; then
  error_json "runtime entry not installed: $ENTRY (repo: $REPO)"
fi
if ! command -v "$NODE_BIN" >/dev/null 2>&1 && [ ! -x "$NODE_BIN" ]; then
  error_json "node binary not found: $NODE_BIN; skill-search runtime unavailable"
fi

# --- invoke with a bounded wait ------------------------------------------------
ARGS=(--roots-file "$TMP_ROOTS" --request-file "$REQUEST_FILE")
if [ "$LOCAL_ONLY" -eq 1 ]; then
  ARGS+=(--local-only)
fi

OUT="$(mktemp /tmp/skill-search-out.XXXXXX)"
trap 'rm -f "$TMP_ROOTS" "$OUT" "$OUT.err"' EXIT

# One blocking wait with an absolute deadline (50ms poll, no 1s floor).
# stdout goes byte-identical to $OUT; on failure only the runtime's first
# stderr line (already sanitized by the runtime) is passed through, capped.
INVOKE_RESULT="$(python3 - "$NODE_BIN" "$ENTRY" "$OUT" "$RUN_TIMEOUT_SECS" "${ARGS[@]}" <<'PYEOF'
import subprocess, sys, time
node, entry, out, timeout = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
err = out + ".err"
p = subprocess.Popen([node, entry] + sys.argv[5:],
                     stdout=open(out, "wb"), stderr=open(err, "wb"))
deadline = time.monotonic() + timeout
while True:
    rc = p.poll()
    if rc is not None:
        msg = ""
        if rc != 0:
            lines = open(err, encoding="utf-8", errors="replace").read().splitlines()
            msg = next((l.strip() for l in lines if l.strip()), "")
            msg = "".join(c for c in msg if c.isprintable())[:300]
        print("rc=%d|%s" % (rc, msg))
        break
    if time.monotonic() >= deadline:
        p.kill()
        try:
            p.wait(timeout=5)
        except Exception:
            pass
        print("timeout")
        break
    time.sleep(0.05)
PYEOF
)"

case "$INVOKE_RESULT" in
  timeout) error_json "skill-search runtime timed out after ${RUN_TIMEOUT_SECS}s" ;;
  rc=0\|*) ;;
  rc=*)   RC_CODE="${INVOKE_RESULT%%|*}"; error_json "skill-search runtime failed (exit ${RC_CODE#rc=}): ${INVOKE_RESULT#*|}" ;;
  *)      error_json "skill-search runtime wait failed" ;;
esac

# --- validate the runtime reply against the contract ---------------------------
# status must be a known enum; candidates at most 3, each with the required
# string fields. Anything else is replaced by a sanitized diagnostic — raw
# provider/debug payloads are NEVER forwarded.
if ! python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
assert isinstance(d, dict), "not an object"
assert d.get("status") in ("exact", "suggestions", "no_match", "fallback",
                           "clarify"), "bad status"
cands = d.get("candidates", [])
assert isinstance(cands, list) and len(cands) <= 3, "bad candidates"
for cand in cands:
    assert isinstance(cand, dict), "bad candidate"
    for k in ("id", "name", "path", "description"):
        v = cand.get(k)
        assert isinstance(v, str) and v.strip(), "bad candidate field %s" % k
' "$OUT" 2>/dev/null; then
  error_json "skill-search runtime returned unusable output"
fi

cat "$OUT"
exit 0
