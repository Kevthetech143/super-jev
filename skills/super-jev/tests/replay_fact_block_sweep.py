#!/usr/bin/env python3
"""Offline, no-judge-call sweep for the CONTRADICTED_BY_FACT deterministic
block change (see gate-bench-20260918-fleet/analysis/SET3-LIVE-GAP.md and
_fact_block_reasons in superjev.py).

For every case in the three recorded benches (gate-bench-20260917 = 40,
gate-bench-20260918 = 29, gate-bench-20260918-fleet = 30; 99 total), this
rebuilds the SAME wide evidence window the live Stop-hook gate builds
(`_derive_evidence_text_from_transcript` + `compose_window_with_facts`)
straight from the recorded payload transcript, with NO subprocess call and
NO network access — pure offline replay. It then asks only: does this
window carry a CONTRADICTED_BY_FACT sentence? That is the ONLY thing the
new deterministic arm adds; the count/PR arms and the judge's own verdict
are unchanged by this change, so a case's decision only flips here if it
was NOT already blocking (recorded exit 2) AND now carries a
CONTRADICTED_BY_FACT fact.

Never prints draft/evidence/transcript text — only case ids, kind
(truth/lie), the fact FAMILY that fired (from the fixed marker set, not
free text) and whether the decision flips. Safe to run and to share output
from; no set-3 payload content is ever quoted.

Exits 0 iff zero TRUTHS flip to a new block. Exits 1 otherwise.
"""
import json
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))
import superjev as sj  # noqa: E402

SETS = [
    ("set1-20260917", "/Users/admin/super-jev-experiments/gate-bench-20260917",
     "cases.json", "payloads-v3"),
    ("set2-20260918", "/Users/admin/super-jev-experiments/gate-bench-20260918",
     "cases2.json", "payloads-v3-2"),
    ("set3-20260918-fleet", "/Users/admin/super-jev-experiments/gate-bench-20260918-fleet",
     "cases3.json", "payloads-v3-3"),
]

# Recorded exit codes for set 3, straight off the live run's own .exit
# files (results-v3-shim-3) — the "old" (pre-change) action, so the sweep
# reports a real decision flip rather than just fact presence. Sets 1 and
# 2 have no recorded exit files in this shape; for those the sweep reports
# fact presence only (old action assumed non-block, which is the
# conservative direction — it can only OVER-report a flip, never hide one).
def _recorded_exit(results_dir, cid):
    p = Path(results_dir) / f"{cid}.exit"
    if p.exists():
        try:
            return int(p.read_text().strip())
        except ValueError:
            return None
    return None


def main():
    total_cases = 0
    flips = []
    truths_flipped = []
    fact_family_counts = {}

    for set_name, bench_dir, cases_file, payloads_dir in SETS:
        bench_dir = Path(bench_dir)
        cases_path = bench_dir / cases_file
        if not cases_path.exists():
            print(f"  {set_name}: no {cases_file} at {bench_dir} — skipping")
            continue
        cases = json.loads(cases_path.read_text(encoding="utf-8"))
        results_dir = None
        for cand in ("results-v3-shim-3", "results-v3-shim-2", "results-v3-shim"):
            if (bench_dir / cand).exists():
                results_dir = bench_dir / cand
                break

        for case in cases:
            cid, kind = case["id"], case["kind"]
            pdir = bench_dir / payloads_dir / cid
            transcript_path = pdir / "transcript.jsonl"
            if not transcript_path.exists():
                continue
            total_cases += 1
            draft = case.get("draft") or ""
            try:
                derived, _wmeta = sj._derive_evidence_text_from_transcript(
                    str(transcript_path), return_meta=True)
            except Exception as e:                       # never let one bad
                print(f"  {set_name}/{cid}: window build raised {e!r} — skipped")
                continue
            derived, facts, _fmeta = sj.compose_window_with_facts(
                derived or "", draft, cap_bytes=0)
            fact_reasons = sj._fact_block_reasons(facts)
            old_exit = _recorded_exit(results_dir, cid) if results_dir else None
            old_blocked = old_exit == 2
            new_blocked = old_blocked or bool(fact_reasons)
            if fact_reasons:
                # Family name only (the fixed prefix before the first
                # colon), never the identity detail after it.
                fam = fact_reasons[0].split(":", 1)[0].strip()
                fact_family_counts[fam] = fact_family_counts.get(fam, 0) + 1
            if new_blocked and not old_blocked:
                flips.append((set_name, cid, kind))
                if kind == "truth":
                    truths_flipped.append((set_name, cid))

    print(f"\ncases replayed     : {total_cases}")
    print(f"fact families fired: {fact_family_counts or '(none)'}")
    print(f"decision flips     : {len(flips)}")
    for set_name, cid, kind in flips:
        print(f"  NEW BLOCK  {set_name:<20} {cid:<6} kind={kind}")
    print(f"truths newly blocked: {len(truths_flipped)}")
    for set_name, cid in truths_flipped:
        print(f"  TRUTH BLOCKED  {set_name} {cid}")

    if truths_flipped:
        print("\nFAIL — a truth flipped to block; see docs above for the "
              "fact family responsible and gate it out.")
        return 1
    print("\nPASS — zero truths newly blocked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
