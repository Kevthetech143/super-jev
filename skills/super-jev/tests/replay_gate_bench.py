#!/usr/bin/env python3
"""Replay proof for gate v2's DETERMINISTIC arms — the count/PR cross-check
(deterministic_block_reasons) — over the 40-case gate bench, with NO judge
call, no network, no TYPESAFE_API_KEY. This is not a pytest file (no
test_ prefix, not collected by `npm run test:skill`); it is the proof
artifact the gate-v2 brief asks for: "which lies the deterministic arm
catches and which truths it would block (must be 0 truths blocked by the
deterministic arm on the recorded cases)".

Reads the bench's own drafts/<id>.md and evidence/<id>.md straight off
disk (bench dir default: /Users/admin/super-jev-experiments/
gate-bench-20260917 — the same 40-case bench the 2026-09-17 REPORT.md
analysis was built from; override with GATE_BENCH_DIR if it lives
elsewhere) and cases.json for each case's id/kind. It does NOT run
presplit_claims through a judge — pre-split only changes what a live
`--claims-file` run hands to jev.py, and there is no offline way to
replay a judge's score; this script tests only the two deterministic,
no-model-call arms that CAN be replayed byte-for-byte against the
recorded evidence.

    python3 skills/super-jev/tests/replay_gate_bench.py

Exits 0 iff zero truths are blocked by the deterministic arm; exits 1
otherwise (a truth blocked by pure arithmetic is a real gate-v2 bug, not
a judge-calibration question, and must never ship).
"""
import json
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))
import superjev as sj  # noqa: E402

DEFAULT_BENCH_DIR = "/Users/admin/super-jev-experiments/gate-bench-20260917"


def main():
    bench_dir = Path(os.environ.get("GATE_BENCH_DIR", DEFAULT_BENCH_DIR))
    cases_path = bench_dir / "cases.json"
    if not cases_path.exists():
        print(f"replay_gate_bench: no bench at {bench_dir} "
              f"(set GATE_BENCH_DIR) — nothing to replay, skipping")
        return 0

    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    caught, missed, truths_blocked, truths_clean = [], [], [], []
    presplit_claim_counts = {}

    for case in cases:
        cid, kind = case["id"], case["kind"]
        draft_path = bench_dir / "drafts" / f"{cid}.md"
        evidence_path = bench_dir / "evidence" / f"{cid}.md"
        if not draft_path.exists() or not evidence_path.exists():
            continue
        draft = draft_path.read_text(encoding="utf-8")
        evidence = evidence_path.read_text(encoding="utf-8")

        presplit_claim_counts[cid] = len(sj.presplit_claims(draft))
        reasons = sj.deterministic_block_reasons(draft, evidence)

        if kind == "lie":
            (caught if reasons else missed).append((cid, reasons))
        elif kind == "truth":
            (truths_blocked if reasons else truths_clean).append((cid, reasons))

    print(f"replay_gate_bench: {bench_dir}")
    print(f"  {len(caught)} lie(s) caught by the deterministic arm: "
          + ", ".join(f"{cid} ({'; '.join(r)})" for cid, r in caught))
    print(f"  {len(missed)} lie(s) the deterministic arm does not catch (left to the "
          "judge): " + ", ".join(cid for cid, _ in missed))
    print(f"  {len(truths_blocked)} truth(s) blocked by the deterministic arm "
          "(MUST be 0): "
          + ", ".join(f"{cid} ({'; '.join(r)})" for cid, r in truths_blocked))
    print(f"  presplit_claims: avg {sum(presplit_claim_counts.values()) / max(1, len(presplit_claim_counts)):.1f} "
          f"claim(s)/draft across {len(presplit_claim_counts)} case(s)")

    if truths_blocked:
        print("\nreplay_gate_bench: FAIL — the deterministic arm blocked a truth")
        return 1
    print("\nreplay_gate_bench: PASS — 0 truths blocked by the deterministic arm")
    return 0


if __name__ == "__main__":
    sys.exit(main())
