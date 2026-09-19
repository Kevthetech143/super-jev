#!/usr/bin/env bash
#
# merge_gated.sh -- wait for CI, scrub the PR text for performance numbers,
# then squash-merge a pull request.
#
# Usage: merge_gated.sh <pr-number> [--repo-dir <path>]
#
#   1. polls `gh pr checks <n>` every MERGE_GATED_POLL_SECS (default 30 s)
#      for up to MERGE_GATED_DEADLINE_SECS (default 1500 s = 25 min).
#      The gate is POSITIVE: the merge proceeds only when every reported
#      check is in a passing bucket. Prefers
#      `gh pr checks <n> --json name,state,bucket` when this gh supports it
#      and falls back to parsing the text table otherwise. A failed or
#      cancelled check fails at ci-failed; a deadline overrun fails at
#      ci-timeout; zero configured checks prints a NOTE and is treated as
#      pass. gh exits non-zero while checks are pending (and when any check
#      failed), so output AND exit code are captured without aborting and
#      every decision is made from the parsed output, never from the exit
#      code alone.
#   2. scrubs the PR title+body for performance numbers with
#      ci/scrub_numbers.py; a hit prints the offending line and aborts
#      BEFORE any merge happens
#   3. runs `gh pr merge <n> --squash --delete-branch`
#   4. confirms `gh pr view <n> --json state` == MERGED (never trusts the
#      merge command exit code alone)
#   5. `git pull --ff-only` in --repo-dir when given
#   6. prints exactly one final line: PASS <sha>  or  FAIL: <step>
#      (a NOTE line about zero configured checks may precede it)

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRUB="$SCRIPT_DIR/scrub_numbers.py"

fail() { echo "FAIL: $1"; exit 1; }

usage() {
  cat <<EOF
Usage: merge_gated.sh <pr-number> [--repo-dir <path>]

  1. waits for CI (gh pr checks) up to 25 min
  2. scrubs the PR title+body for performance numbers; aborts on a hit
  3. squash-merges the PR and deletes the branch
  4. confirms the PR state is MERGED
  5. git pull --ff-only in --repo-dir when given
EOF
}

pr_number=""
repo_dir=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --repo-dir)
      [[ $# -ge 2 ]] || fail "usage"
      repo_dir="$2"; shift 2 ;;
    --repo-dir=*)
      repo_dir="${1#*=}"; shift ;;
    -*)
      fail "usage" ;;
    *)
      [[ -z "$pr_number" ]] || fail "usage"
      pr_number="$1"; shift ;;
  esac
done
[[ "$pr_number" =~ ^[0-9]+$ ]] || fail "usage"

command -v gh >/dev/null 2>&1 || fail "gh-missing"
command -v python3 >/dev/null 2>&1 || fail "python3-missing"
[[ -f "$SCRUB" ]] || fail "scrub-missing"
if [[ -n "$repo_dir" ]]; then
  [[ -d "$repo_dir/.git" ]] || fail "repo-dir"
fi

# 1. wait for CI ----------------------------------------------------------
poll_secs="${MERGE_GATED_POLL_SECS:-30}"
deadline_secs="${MERGE_GATED_DEADLINE_SECS:-1500}"
deadline=$(( $(date +%s) + deadline_secs ))

json_mode=0        # set to 1 once --json output has been seen to parse
poll_out=""
poll_rc=0

poll() {
  set +e
  if [[ "$json_mode" -eq 1 ]]; then
    poll_out="$(gh pr checks "$pr_number" --json name,state,bucket 2>&1)"
  else
    poll_out="$(gh pr checks "$pr_number" 2>&1)"
  fi
  poll_rc=$?
  set -e
}

is_json_list() { # $1 = candidate text; exit 0 when it parses as a JSON list
  printf '%s' "$1" | python3 -c \
    'import json,sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
sys.exit(0 if isinstance(data, list) else 1)' 2>/dev/null
}

classify_json() { # stdin: gh --json list; stdout: one verdict word
  python3 -c '
import json, sys
try:
    checks = json.load(sys.stdin)
except Exception:
    print("UNKNOWN"); sys.exit(0)
if not isinstance(checks, list):
    print("UNKNOWN"); sys.exit(0)
if not checks:
    print("EMPTY"); sys.exit(0)
state_to_bucket = {
    "success": "pass", "failure": "fail", "cancelled": "cancel",
    "skipped": "skipping", "neutral": "skipping",
    "pending": "pending", "queued": "pending", "in_progress": "pending",
    "waiting": "pending", "requested": "pending",
}
buckets = set()
for c in checks:
    b = str(c.get("bucket") or "").lower()
    if not b:
        b = state_to_bucket.get(str(c.get("state") or "").lower(), "")
    buckets.add(b)
if buckets & {"fail", "cancel"}:
    print("FAILED")
elif buckets & {"pending"}:
    print("PENDING")
elif buckets <= {"pass", "skipping"}:
    print("PASSED")
else:
    print("UNKNOWN")'
}

classify_text() { # $1 = gh pr checks text table; stdout: one verdict word
  if printf '%s\n' "$1" | grep -qiE 'no checks (reported|found)'; then
    echo "EMPTY"; return
  fi
  printf '%s\n' "$1" | tail -n +2 | awk '
    BEGIN { found = 0; bad = 0; waiting = 0 }
    NF == 0 { next }
    {
      for (i = 1; i <= NF; i++) {
        t = tolower($i)
        if (t == "pass" || t == "success" || t == "skipping" || t == "skipped" || t == "neutral") found = 1
        else if (t == "fail" || t == "failed" || t == "failure" || t == "cancel" || t == "cancelled" || t == "canceled" || t == "timed_out" || t == "timed-out" || t == "timeout" || t == "action_required") { found = 1; bad = 1 }
        else if (t == "pending" || t == "queued" || t == "queue" || t == "waiting" || t == "expected" || t == "requested" || t == "running" || t == "in_progress" || t == "in-progress") { found = 1; waiting = 1 }
      }
    }
    END {
      if (bad) print "FAILED"
      else if (waiting) print "PENDING"
      else if (found) print "PASSED"
      else print "UNKNOWN"
    }'
}

# Probe once with --json: when this gh supports it the probe output parses
# as a JSON list and doubles as the first poll. Otherwise fall back to the
# text table for the whole run.
set +e
poll_out="$(gh pr checks "$pr_number" --json name,state,bucket 2>&1)"
poll_rc=$?
set -e
if is_json_list "$poll_out"; then
  json_mode=1
else
  poll   # json_mode is 0 here, so this is a plain text-table call
fi

while true; do
  if [[ "$json_mode" -eq 1 ]]; then
    verdict="$(printf '%s' "$poll_out" | classify_json)"
  else
    verdict="$(classify_text "$poll_out")"
  fi
  case "$verdict" in
    PASSED)
      break ;;
    EMPTY)
      echo "NOTE: no CI checks configured for PR $pr_number; treating as pass"
      break ;;
    FAILED)
      printf '%s\n' "$poll_out" >&2
      fail "ci-failed" ;;
    PENDING)
      [[ $(date +%s) -ge "$deadline" ]] && fail "ci-timeout"
      sleep "$poll_secs"
      poll ;;
    *)
      printf '%s\n' "$poll_out" >&2
      fail "ci-wait" ;;
  esac
done

# 2. scrub title+body BEFORE any merge ------------------------------------
pr_text="$(gh pr view "$pr_number" --json title,body \
  --jq '[.title, (.body // "")] | join("\n")')" || fail "pr-view"
if ! printf '%s' "$pr_text" | python3 "$SCRUB"; then
  fail "scrub"
fi

# 3. merge ----------------------------------------------------------------
gh pr merge "$pr_number" --squash --delete-branch >/dev/null || fail "merge"

# 4. confirm merged (never trust the merge exit code alone) ---------------
state="$(gh pr view "$pr_number" --json state --jq '.state')" || fail "pr-view"
[[ "$state" == "MERGED" ]] || fail "confirm"

# 5. fast-forward the local checkout when asked ---------------------------
if [[ -n "$repo_dir" ]]; then
  ( cd "$repo_dir" && git pull --ff-only >/dev/null ) || fail "pull"
fi

# 6. final line ------------------------------------------------------------
sha="$(gh pr view "$pr_number" --json mergeCommit \
  --jq '.mergeCommit.oid // empty')" || fail "pr-view"
[[ -n "$sha" ]] || fail "sha"
echo "PASS $sha"
