#!/usr/bin/env bash
#
# test_merge_gated.sh -- scenario tests for ci/merge_gated.sh.
#
# Runs merge_gated.sh with a fake `gh` on PATH. The fake gh is generated
# into a per-scenario temp dir and returns canned JSON/text per argv,
# driven by MERGE_GATED_SCENARIO:
#   pending-then-pass    first `gh pr checks` reports pending, then all pass
#   failed               checks report a failed bucket
#   timeout              checks stay pending forever (deadline forced to 0)
#   scrub-hit            checks pass but the PR body contains a perf number
#   merge-not-confirmed  checks pass, merge runs, but PR state stays OPEN
#   legacy-text-failed   fake gh has no --json support; text table shows fail
#
# For each scenario the test asserts the single final line printed by
# merge_gated.sh (and that the exit code matches). No real gh call is made.

set -u

TDIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MERGE_GATED="$TDIR/../merge_gated.sh"

write_fake_gh() { # $1 = dir; writes $1/gh
  cat > "$1/gh" <<'FAKEGH'
#!/usr/bin/env bash
# Fake gh: canned responses per argv, driven by MERGE_GATED_SCENARIO.
set -u
dir="${MERGE_GATED_SHIM_DIR:?}"
scenario="${MERGE_GATED_SCENARIO:?}"

if [[ "${1:-}" == "pr" && "${2:-}" == "checks" ]]; then
  if [[ " $* " == *" --json "* ]]; then
    case "$scenario" in
      legacy-text-*)
        echo "unknown flag: --json" >&2
        exit 2 ;;
    esac
    case "$scenario" in
      pending-then-pass)
        n="$([ -f "$dir/checks_n" ] && cat "$dir/checks_n" || echo 0)"
        n=$(( n + 1 )); printf '%s' "$n" > "$dir/checks_n"
        if [[ "$n" -eq 1 ]]; then
          printf '%s\n' '[{"name":"build","state":"IN_PROGRESS","bucket":"pending"}]'
        else
          printf '%s\n' '[{"name":"build","state":"SUCCESS","bucket":"pass"},{"name":"lint","state":"SUCCESS","bucket":"pass"}]'
        fi ;;
      failed)
        printf '%s\n' '[{"name":"build","state":"FAILURE","bucket":"fail"}]' ;;
      timeout)
        printf '%s\n' '[{"name":"build","state":"QUEUED","bucket":"pending"}]' ;;
      scrub-hit|merge-not-confirmed)
        printf '%s\n' '[{"name":"build","state":"SUCCESS","bucket":"pass"}]' ;;
      *)
        echo "fake-gh: unknown scenario $scenario" >&2; exit 3 ;;
    esac
    exit 0
  fi
  # text-table fallback (no --json in argv)
  if [[ "$scenario" == legacy-text-failed ]]; then
    printf 'NAME\tSTATE\tWORKFLOW\nbuild\tfail\tCI\n'
  else
    printf 'NAME\tSTATE\tWORKFLOW\nbuild\tpass\tCI\n'
  fi
  exit 0
fi

if [[ "${1:-}" == "pr" && "${2:-}" == "view" ]]; then
  json=""; prev=""
  for a in "$@"; do
    if [[ "$prev" == "--json" ]]; then json="$a"; fi
    prev="$a"
  done
  case "$json" in
    title,body)
      if [[ "$scenario" == "scrub-hit" ]]; then
        printf 'Speed up the thing\n\nCuts p99 from 12 ms to 4 ms.\n'
      else
        printf 'Add the thing\n\nNo numbers in this text.\n'
      fi ;;
    state)
      if [[ "$scenario" == "merge-not-confirmed" ]]; then printf 'OPEN\n'; else printf 'MERGED\n'; fi ;;
    mergeCommit)
      printf 'abc123def4567890\n' ;;
    headRefName)
      printf '%s\n' "${MERGE_GATED_HEAD_BRANCH:-feature-branch}" ;;
    baseRefName)
      printf '%s\n' "${MERGE_GATED_BASE_BRANCH:-main}" ;;
    *)
      echo "fake-gh: unhandled --json $json" >&2; exit 3 ;;
  esac
  exit 0
fi

if [[ "${1:-}" == "pr" && "${2:-}" == "merge" ]]; then
  exit 0
fi

echo "fake-gh: unhandled argv: $*" >&2
exit 3
FAKEGH
  chmod +x "$1/gh"
}

write_fake_git() { # $1 = dir; writes $1/git -- intercepts push/pull (recorded
                    # to files, never touches a real remote), forwards every
                    # other subcommand (rev-parse, worktree setup, ...) to the
                    # real git so worktree/branch behavior stays genuine.
  local real_git; real_git="$(command -v git)"
  cat > "$1/git" <<SHIM
#!/usr/bin/env bash
set -u
REAL_GIT="$real_git"
dir="\${MERGE_GATED_SHIM_DIR:?}"
sub="\${1:-}"
if [[ "\$sub" == "-C" ]]; then
  sub="\${3:-}"
fi
case "\$sub" in
  push)
    printf '%s\n' "\$*" >> "\$dir/git_push_calls"
    exit 0 ;;
  pull)
    printf '%s\n' "\$*" >> "\$dir/git_pull_calls"
    exit 0 ;;
esac
exec "\$REAL_GIT" "\$@"
SHIM
  chmod +x "$1/git"
}

run_scenario() { # $1=scenario $2=expected last line $3=deadline seconds
  local scenario="$1" expected="$2" deadline_secs="$3"
  local tmp; tmp="$(mktemp -d)"
  write_fake_gh "$tmp"
  write_fake_git "$tmp"   # scenarios that reach the merge step now also push
                          # --delete; fake git keeps that off the real remote.
  local out rc last want_rc
  out="$(
    export PATH="$tmp:$PATH"
    export MERGE_GATED_SHIM_DIR="$tmp"
    export MERGE_GATED_SCENARIO="$scenario"
    export MERGE_GATED_POLL_SECS=0
    export MERGE_GATED_DEADLINE_SECS="$deadline_secs"
    bash "$MERGE_GATED" 42 2>&1
  )"
  rc=$?
  last="$(printf '%s\n' "$out" | tail -n 1)"
  case "$expected" in
    PASS*) want_rc=0 ;;
    *)     want_rc=1 ;;
  esac
  if [[ "$last" == "$expected" && "$rc" -eq "$want_rc" ]]; then
    echo "ok: $scenario -> $last"
    rm -rf "$tmp"
    return 0
  fi
  echo "FAIL: $scenario: expected [$expected] rc=$want_rc; got [$last] rc=$rc"
  printf '%s\n' "$out" | sed 's/^/    /'
  rm -rf "$tmp"
  return 1
}

# --repo-dir worktree scenarios -------------------------------------------
#
# Reproduces the two bugs two workers hit merging from a git worktree:
#   1. `[[ -d "$repo_dir/.git" ]]` used to reject a worktree outright,
#      because a linked worktree's .git is a FILE, not a directory.
#   2. `gh pr merge --delete-branch` used to try to check out the base
#      branch locally, which fails when that branch is checked out
#      elsewhere -- even though GitHub had already merged.
# These use a REAL temporary git repo + a REAL `git worktree add` (not a
# fake) for the repo-dir precondition and branch checks, so a regression
# back to `-d "$repo_dir/.git"` would actually fail this test. `git push`
# and `git pull` are still intercepted by write_fake_git so no real remote
# is ever touched.
run_worktree_scenario() { # $1=worktree branch $2=base branch $3=expect pull called (yes/no)
  local wt_branch="$1" base_branch="$2" expect_pull="$3"
  local src; src="$(mktemp -d)"
  local wt; wt="$(mktemp -d)"
  rmdir "$wt"   # `git worktree add` requires the target not exist yet

  # "scratch" stays checked out in $src throughout, so $base_branch (and, for
  # the same-branch case, $wt_branch) is always free for `worktree add` to
  # check out -- git refuses to check out a branch that's checked out
  # elsewhere already, which is the bug this whole scenario is guarding.
  git init -q -b scratch "$src"
  git -C "$src" config user.email test@example.com
  git -C "$src" config user.name "Test"
  echo one > "$src/f.txt"
  git -C "$src" add f.txt
  git -C "$src" commit -q -m init
  git -C "$src" branch "$base_branch"

  if [[ "$wt_branch" == "$base_branch" ]]; then
    git -C "$src" worktree add -q "$wt" "$base_branch"
  else
    git -C "$src" worktree add -q -b "$wt_branch" "$wt" "$base_branch"
  fi

  local tmp; tmp="$(mktemp -d)"
  write_fake_gh "$tmp"
  write_fake_git "$tmp"

  local label="repo-dir-worktree(branch=$wt_branch,base=$base_branch)"
  local out rc last
  out="$(
    export PATH="$tmp:$PATH"
    export MERGE_GATED_SHIM_DIR="$tmp"
    export MERGE_GATED_SCENARIO="pending-then-pass"
    export MERGE_GATED_POLL_SECS=0
    export MERGE_GATED_DEADLINE_SECS=120
    export MERGE_GATED_BASE_BRANCH="$base_branch"
    export MERGE_GATED_HEAD_BRANCH="some-pr-branch"
    bash "$MERGE_GATED" 42 --repo-dir "$wt" 2>&1
  )"
  rc=$?
  last="$(printf '%s\n' "$out" | tail -n 1)"

  local ok=1
  if [[ "$last" != "PASS abc123def4567890" || "$rc" -ne 0 ]]; then
    ok=0
  fi
  local pulled=no
  [[ -f "$tmp/git_pull_calls" ]] && pulled=yes
  if [[ "$pulled" != "$expect_pull" ]]; then
    ok=0
  fi
  # the remote-branch cleanup push must always be attempted once merged
  if [[ ! -f "$tmp/git_push_calls" ]]; then
    ok=0
  fi

  if [[ "$ok" -eq 1 ]]; then
    echo "ok: $label -> $last (pull-called=$pulled)"
  else
    echo "FAIL: $label: got [$last] rc=$rc pull-called=$pulled (expected pull-called=$expect_pull)"
    printf '%s\n' "$out" | sed 's/^/    /'
  fi
  git -C "$src" worktree remove --force "$wt" >/dev/null 2>&1 || rm -rf "$wt"
  rm -rf "$src" "$tmp"
  return $(( 1 - ok ))
}

fails=0
run_scenario pending-then-pass    "PASS abc123def4567890" 120 || fails=$(( fails + 1 ))
run_scenario failed               "FAIL: ci-failed"       120 || fails=$(( fails + 1 ))
run_scenario timeout              "FAIL: ci-timeout"       0 || fails=$(( fails + 1 ))
run_scenario scrub-hit            "FAIL: scrub"           120 || fails=$(( fails + 1 ))
run_scenario merge-not-confirmed  "FAIL: confirm"         120 || fails=$(( fails + 1 ))
run_scenario legacy-text-failed   "FAIL: ci-failed"       120 || fails=$(( fails + 1 ))
run_worktree_scenario feature-x main no  || fails=$(( fails + 1 ))
run_worktree_scenario main       main yes || fails=$(( fails + 1 ))

if [[ "$fails" -eq 0 ]]; then
  echo "ALL SCENARIOS PASSED"
  exit 0
else
  echo "SCENARIO FAILURES: $fails"
  exit 1
fi
