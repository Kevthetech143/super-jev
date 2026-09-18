#!/usr/bin/env python3
"""replay_number_pairing.py — OFFLINE sweep for the number-pairing arm
(_number_pairing / _number_pairing_reason, see superjev.py "number-pairing
arm" section, SET3-AUDIT.md Change 1, bench case l48) over all three gate
benches. NO live TypeSafe calls: for every case, rebuilds the exact window
`_derive_evidence_text_from_transcript` would hand the judge — reading each
case's own recorded payload.json (for session_id) and transcript.jsonl
straight off disk under <bench_dir>/payloads-v3[-N]/<id>/ — and runs the
deterministic arm against it, no --explain subprocess, no network.

    python3 skills/super-jev/tests/replay_number_pairing.py

Prints one table per bench (set 1, set 2, set 3) listing every case where
the arm fires, split lie/truth, plus a summary line. Exits 1 iff any TRUTH
fires (a truth blocked by pure arithmetic is a bug, never allowed to ship);
exits 0 otherwise, including when a bench directory is missing.
"""
import json
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))
import superjev as sj  # noqa: E402

BENCHES = [
    ("set 1", "/Users/admin/super-jev-experiments/gate-bench-20260917",
     "cases.json", "payloads-v3"),
    ("set 2", "/Users/admin/super-jev-experiments/gate-bench-20260918",
     "cases2.json", "payloads-v3-2"),
    ("set 3", "/Users/admin/super-jev-experiments/gate-bench-20260918-fleet",
     "cases3.json", "payloads-v3-3"),
]


def sweep_one(name, bench_dir, cases_file, payload_subdir):
    bench_dir = Path(bench_dir)
    cases_path = bench_dir / cases_file
    payload_dir = bench_dir / payload_subdir
    if not cases_path.exists() or not payload_dir.exists():
        print(f"{name}: no bench at {bench_dir} ({cases_file} / {payload_subdir}) — skipping")
        return [], [], 0

    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    fired_lies, fired_truths = [], []
    n_checked = 0
    for case in cases:
        cid, kind, draft = case["id"], case["kind"], case["draft"]
        case_dir = payload_dir / cid
        transcript_path = case_dir / "transcript.jsonl"
        payload_path = case_dir / "payload.json"
        if not transcript_path.exists():
            continue
        session_id = None
        if payload_path.exists():
            try:
                session_id = json.loads(payload_path.read_text(encoding="utf-8")).get("session_id")
            except (json.JSONDecodeError, OSError):
                session_id = None
        try:
            window = sj._derive_evidence_text_from_transcript(
                str(transcript_path), session_id=session_id)
        except Exception as e:                      # never let one bad case kill the sweep
            print(f"  {cid}: window derivation failed ({e!r}) — skipped")
            continue
        n_checked += 1
        reason, detail = sj._number_pairing(draft, window)
        if not reason:
            continue
        row = (cid, detail["missing"], "/".join(detail["matched"]), detail["block_text"][:80])
        (fired_lies if kind == "lie" else fired_truths).append(row)

    print(f"\n{name}: {bench_dir}")
    print(f"  {n_checked} case(s) with a rebuildable window")
    print(f"  {len(fired_lies)} lie(s) fired: "
          + (", ".join(f"{cid} (missing {m}, siblings {s})" for cid, m, s, _b in fired_lies)
             or "none"))
    print(f"  {len(fired_truths)} truth(s) fired (MUST be 0): "
          + (", ".join(f"{cid} (missing {m}, siblings {s})" for cid, m, s, _b in fired_truths)
             or "none"))
    return fired_lies, fired_truths, n_checked


def main():
    all_truths_fired = []
    for name, bench_dir, cases_file, payload_subdir in BENCHES:
        _lies, truths, _n = sweep_one(name, bench_dir, cases_file, payload_subdir)
        all_truths_fired.extend((name, cid) for cid, *_r in truths)

    if all_truths_fired:
        print(f"\nreplay_number_pairing: FAIL — {len(all_truths_fired)} truth(s) fired: "
              + ", ".join(f"{n}/{cid}" for n, cid in all_truths_fired))
        return 1
    print("\nreplay_number_pairing: PASS — 0 truths fired across all benches")
    return 0


if __name__ == "__main__":
    sys.exit(main())
