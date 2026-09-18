#!/usr/bin/env python3
"""Offline replay for catch-ledger "catch cases" (see `catch tag
false|miss` in superjev.py, `_build_catch_case`/`_upsert_catch_case`).

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
previously-recorded verdict). By default this script REFUSES to run at
all (exit 2) unless the door env var a case needs is set, or `--live` is
passed explicitly — see "Live door refusal" below. It then reports the
NEW decision the replay reached.

What this script CANNOT do: a case with no `payload_path` (the common
case — SUPERJEV_CATCH_KEEP_PAYLOAD defaults off) carries nothing but the
catch ledger's own 240-char redacted excerpt. There is no fuller evidence
window to rebuild a payload from, so that case is printed as "no payload —
cannot replay, excerpt-only" and is excluded from the lies-blocked /
truths-blocked summary — counting it either way would be a guess, not a
replay. A case whose saved payload is corrupt/unreadable, or whose payload
carries none of the fields the case's own door can read text out of, is
reported the same way under its own, more specific message (see
replay_one) and is excluded from the summary for the same reason.

Live door refusal: without SUPERJEV_GATE_CMD/SUPERJEV_VERIFY_CMD set for
every door a case in the file needs, this script exits 2 before touching
any case or writing anything, rather than silently falling back to the
real fleet gate/verify door the way `door_cmd()` does for every other
caller — this script is a replay tool meant for a fake/canned door or a
dry run, and a caller who really does want the live door back has to say
so with `--live`. Never sets or reads TYPESAFE_API_KEY itself either way.

Never prints the full draft/payload text — case id, kind, door, and
decision only. Uses an isolated scratch catch ledger for its own replay
calls, never the real one the cases file was read from.

Usage:
    python3 replay_catch_cases.py [cases.json] [--live]
    (default cases file: SUPERJEV_CATCH_CASES, else
    <catch ledger dir>/catch-cases.json)

Exit 0 on a normal run (whatever the individual case decisions turned
out to be), including a missing/unreadable/empty cases file — this is a
reporting tool, not a pass/fail gate (unlike replay_fact_block_sweep.py,
a catch case's "old decision" was a real human tag, not a recorded exit
code, so there is no old-vs-new regression this script can respond to a
mismatch). Exit 2 is reserved for the live-door refusal above — the one
case where running at all could reach a live TypeSafe call this script
never wants to make on its own.
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
    override = os.environ.get("SUPERJEV_CATCH_CASES")
    if override:
        return Path(override).expanduser()
    return sj.CATCH_LEDGER_PATH.parent / "catch-cases.json"


# Which env var a case's own door needs to avoid the live fleet fallback in
# sj.door_cmd() — prompt-verify cases run `verify` internally (see
# cmd_hook_prompt_verify), so they need the verify door, not a gate one.
_DOOR_ENV = {"gate": sj.GATE_CMD_ENV, "verify": sj.VERIFY_CMD_ENV,
            "prompt-verify": sj.VERIFY_CMD_ENV}


def _live_door_refusal(cases, live):
    """None if this run may proceed, else the plain refusal message for
    B1: this script defaults to offline-only and must never fall through
    to the real fleet gate/verify door on its own, so it refuses (rather
    than running and only some cases happening to call a fake door) unless
    EVERY door a case in the file actually could call live has its env var
    set, or the caller passed --live explicitly. Only cases that carry a
    payload_path are considered — a case with none never reaches
    replay_one's door call at all (see replay_one), so it can never call a
    live door and never needs to gate this refusal."""
    if live:
        return None
    needed = sorted({(c.get("door") or "gate") for c in cases
                     if isinstance(c, dict) and c.get("payload_path")})
    missing_doors = [d for d in needed if not os.environ.get(_DOOR_ENV.get(d, sj.GATE_CMD_ENV))]
    if not missing_doors:
        return None
    missing_envs = sorted({_DOOR_ENV.get(d, sj.GATE_CMD_ENV) for d in missing_doors})
    return (f"replay_catch_cases: refusing to run — {', '.join(missing_envs)} not set "
            f"for door(s) {', '.join(missing_doors)} in this cases file, and --live was "
            "not passed. This script never calls the real fleet gate/verify door on its "
            "own (see B1) — set the env var to a fake/canned door for an offline replay, "
            "or pass --live if you really do want the live one.")


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


# Which raw payload fields let cmd_hook derive text for a given door — the
# same field lists cmd_hook's own docstring documents for "gate" and
# "verify" (transcript_path is listed too: a payload carrying only that
# can still reach a real decision, since cmd_hook derives text from the
# transcript itself in that case).
_GATE_TEXT_KEYS = ("last_assistant_message", "draft", "text", "prompt", "transcript_path")
_VERIFY_TEXT_KEYS = ("tool_response", "report", "text", "message", "transcript_path")


def _payload_shape_ok(payload, door):
    """True if `payload` carries at least one field this door's own
    cmd_hook path can pull text out of (see cmd_hook's docstring) — a
    payload missing all of them can never reach a real decision no matter
    what the door does with it, so this is checked before ever calling
    cmd_hook, rather than folding that case into a generic failure."""
    if not isinstance(payload, dict):
        return False
    keys = _VERIFY_TEXT_KEYS if door in ("verify", "prompt-verify") else _GATE_TEXT_KEYS
    return any(payload.get(k) for k in keys)


# replay_one's per-case outcome, printed by main() with its own message —
# see N3: these used to collapse into one "no payload" message that hid
# which of four very different things actually happened.
_STATUS_MESSAGE = {
    "no_payload": "no payload — cannot replay, excerpt-only",
    "unreadable": "payload unreadable (corrupt/non-JSON) — cannot replay",
    "unsupported": "payload shape unsupported for this door (no field cmd_hook can "
                   "read text from) — cannot replay",
    "door_failed": "door ran but produced no new catch-ledger decision — cannot replay",
}


def replay_one(case):
    """Re-run the case's own door (`gate`, `verify`, or `prompt-verify`)
    against its saved payload. Returns (status, decision): status is "ok"
    with the new catch-ledger decision string, or one of "no_payload",
    "unreadable", "unsupported", "door_failed" with decision None — see
    _STATUS_MESSAGE for what each one means. Writes into whatever
    sj.CATCH_LEDGER_PATH currently points at — callers that don't want to
    pollute a real ledger should point that at a scratch file first (see
    main())."""
    payload_path = case.get("payload_path")
    if not payload_path or not Path(payload_path).exists():
        return "no_payload", None
    try:
        payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unreadable", None
    door = case.get("door") or "gate"
    if not _payload_shape_ok(payload, door):
        return "unsupported", None

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
            return "door_failed", None
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
        return "door_failed", None
    return "ok", new_records[-1].get("decision")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cases_file", nargs="?", default=None,
                    help="path to a catch-cases.json array (default: "
                         "SUPERJEV_CATCH_CASES or <catch ledger dir>/catch-cases.json)")
    ap.add_argument("--live", action="store_true",
                    help="allow calling the real fleet gate/verify door for a case "
                         "whose door has no SUPERJEV_GATE_CMD/SUPERJEV_VERIFY_CMD set — "
                         "off by default (see B1); this script itself never sets or "
                         "reads TYPESAFE_API_KEY")
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

    refusal = _live_door_refusal(cases, args.live)
    if refusal:
        print(refusal, file=sys.stderr)
        return 2

    # An isolated scratch catch ledger for THIS run's own replay calls —
    # never the real one `cases_path` was read from.
    scratch_dir = Path(tempfile.mkdtemp(prefix="replay-catch-cases-"))
    real_catch_ledger_path = sj.CATCH_LEDGER_PATH
    sj.CATCH_LEDGER_PATH = scratch_dir / "catches.jsonl"

    not_replayed = 0
    lies_total = lies_blocked = 0
    truths_total = truths_blocked = 0
    try:
        for case in cases:
            cid = case.get("id", "?")
            kind = case.get("kind")
            door = case.get("door") or "gate"
            status, new_decision = replay_one(case)
            if status != "ok":
                not_replayed += 1
                print(f"  {cid}: {_STATUS_MESSAGE.get(status, status)}")
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
    print(f"not replayed (see per-case reason above, not counted below): {not_replayed}")
    print(f"lies blocked           : {lies_blocked}/{lies_total}")
    print(f"truths blocked         : {truths_blocked}/{truths_total} (ideally 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
