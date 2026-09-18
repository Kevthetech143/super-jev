#!/usr/bin/env python3
"""Offline replay for catch-ledger "catch cases" (see `catch tag
false|miss` in superjev.py, `_write_catch_case`).

WHAT A CATCH CASE IS, AND IS NOT
---------------------------------
A catch case is NOT a bench case in the gate-bench-20260917/-18 sense. A
bench case anchors to one recorded spot in a saved transcript
(`source_offset`/`source_idx`/`transcript_path`/`bot`) and the existing
replay scripts (replay_gate_bench.py, replay_fact_block_sweep.py) both
read exactly that shape from a `cases.json` array. A catch record has no
such anchor — it is one line written after a real, live gate/verify
decision on a real turn, with no saved transcript behind it at all — so a
catch case can never be handed to those scripts, and this script never
pretends it can.

What this script CAN do: for a catch case that carries a `payload_path`
(only present when `SUPERJEV_CATCH_KEEP_PAYLOAD=1` was set at decision
time — see docs/hooks.md, "The catch ledger"), the saved (redacted) hook
payload IS a real payload this same door (`gate` or `verify`) can be
re-run against. This script loads that payload and calls `hook <door>`
again, offline, through whatever SUPERJEV_GATE_CMD/SUPERJEV_VERIFY_CMD the
caller has set (a fake door for a dry run, or a door that replays a
previously-recorded verdict) — this script itself never makes a live
TypeSafe call. It then reports the NEW decision the replay reached.

What this script CANNOT do: a case with no `payload_path` (the common
case — SUPERJEV_CATCH_KEEP_PAYLOAD defaults off) carries nothing but the
catch ledger's own 240-char redacted excerpt. There is no fuller evidence
window to rebuild a payload from, so that case is printed as "no payload —
cannot replay, excerpt-only" and is excluded from the lies-blocked /
truths-blocked summary — counting it either way would be a guess, not a
replay.

Never prints the full draft/payload text — case id, kind, door, and
decision only. Uses an isolated scratch catch ledger for its own replay
calls, never the real one the cases file was read from.

Usage:
    python3 replay_catch_cases.py [cases.json]
    (default: SUPERJEV_BENCH_OUT, else <catch ledger dir>/catch-cases.json)

Exit 0 always — this is a reporting tool, not a pass/fail gate (unlike
replay_fact_block_sweep.py, a catch case's "old decision" was a real human
tag, not a recorded exit code, so there is no old-vs-new regression this
script can respond to a mismatch).
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))
import superjev as sj  # noqa: E402


def _default_cases_path():
    override = os.environ.get("SUPERJEV_BENCH_OUT")
    if override:
        return Path(override).expanduser()
    return sj.CATCH_LEDGER_PATH.parent / "catch-cases.json"


def load_cases(cases_path):
    """Returns a list of case dicts, or None if the file is missing/not a
    JSON array — caller decides what that means (exit code, message)."""
    if not cases_path.exists():
        return None
    try:
        data = json.loads(cases_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


def replay_one(case):
    """Re-run the case's own door (`gate` or `verify`) against its saved
    payload. Returns the new catch-ledger decision string, or None when
    there is no payload to replay against or the payload/replay itself
    failed. Writes into whatever sj.CATCH_LEDGER_PATH currently points at
    — callers that don't want to pollute a real ledger should point that
    at a scratch file first (see main())."""
    payload_path = case.get("payload_path")
    if not payload_path or not Path(payload_path).exists():
        return None
    try:
        payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    door = case.get("door") or "gate"

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                      encoding="utf-8")
    tmp.write(json.dumps(payload))
    tmp.close()

    before = len(sj._catch_lines())
    old_stdin = sys.stdin
    devnull = open(os.devnull, "w", encoding="utf-8")
    old_stdout = sys.stdout
    try:
        sys.stdin = open(tmp.name, encoding="utf-8")
        sys.stdout = devnull
        ns = argparse.Namespace(door=door, from_file=None, worktree=None,
                                test_cmd="", pr=None, explain=False)
        try:
            sj.cmd_hook(ns)
        except Exception:
            return None
    finally:
        sys.stdout = old_stdout
        devnull.close()
        try:
            sys.stdin.close()
        except Exception:
            pass
        sys.stdin = old_stdin
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    new_records = sj._catch_records()
    if len(new_records) <= before:
        return None
    return new_records[-1].get("decision")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cases_file", nargs="?", default=None,
                    help="path to a catch-cases.json array (default: "
                         "SUPERJEV_BENCH_OUT or <catch ledger dir>/catch-cases.json)")
    args = ap.parse_args(argv)

    cases_path = (Path(args.cases_file).expanduser() if args.cases_file
                 else _default_cases_path())
    cases = load_cases(cases_path)
    if cases is None:
        print(f"replay_catch_cases: no readable catch-cases array at {cases_path}")
        return 0
    if not cases:
        print(f"replay_catch_cases: {cases_path} has zero cases")
        return 0

    # An isolated scratch catch ledger for THIS run's own replay calls —
    # never the real one `cases_path` was read from.
    scratch_dir = Path(tempfile.mkdtemp(prefix="replay-catch-cases-"))
    real_catch_ledger_path = sj.CATCH_LEDGER_PATH
    sj.CATCH_LEDGER_PATH = scratch_dir / "catches.jsonl"

    no_payload = 0
    lies_total = lies_blocked = 0
    truths_total = truths_blocked = 0
    try:
        for case in cases:
            cid = case.get("id", "?")
            kind = case.get("kind")
            door = case.get("door") or "gate"
            new_decision = replay_one(case)
            if new_decision is None:
                no_payload += 1
                print(f"  {cid}: no payload — cannot replay, excerpt-only")
                continue
            blocked = new_decision == "block"
            if kind == "lie":
                lies_total += 1
                if blocked:
                    lies_blocked += 1
            elif kind == "truth":
                truths_total += 1
                if blocked:
                    truths_blocked += 1
            print(f"  {cid}  kind={kind or '?':5s}  door={door:6s}  "
                  f"new_decision={new_decision}")
    finally:
        sj.CATCH_LEDGER_PATH = real_catch_ledger_path

    print()
    print(f"cases in file          : {len(cases)}")
    print(f"no payload (excerpt-only, not counted below): {no_payload}")
    print(f"lies blocked           : {lies_blocked}/{lies_total}")
    print(f"truths blocked         : {truths_blocked}/{truths_total} (ideally 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
