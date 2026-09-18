#!/usr/bin/env python3
"""Offline, no-judge-call sweep for the CONTRADICTED_BY_FACT deterministic
block change (see gate-bench-20260918-fleet/analysis/SET3-LIVE-GAP.md and
_fact_block_reasons in superjev.py).

For every case in the three recorded benches (gate-bench-20260917 = 40,
gate-bench-20260918 = 29, gate-bench-20260918-fleet = 30; 99 total), this
rebuilds the SAME wide evidence window the live Stop-hook gate builds —
`_derive_evidence_text_from_transcript`, then (2026-09-18, post-#64 fix)
the receipt-turn extra fact AND the `[cited files]` tail
(`build_cited_file_block`), exactly as `cmd_hook`'s own "hook gate" branch
assembles them, THEN `compose_window_with_facts` — straight from the
recorded payload transcript, with NO subprocess call and NO network
access — pure offline replay. Skipping the cited-file step used to make
this sweep blind to any case whose draft names its own source ("per the
summary log"): t38 (set 2) blocked live on a fact that lived only in a
cited file's tail, and the old sweep, never having read that tail, could
not see the fact at all — not "predicted no block", genuinely couldn't
reproduce the window. See docs/hooks.md.

"New" is decided against a REAL pre-change baseline, not a recorded exit
file: this script also imports `superjev.py` AS OF `BASELINE_REF` (default
`git merge-base HEAD origin/main` — the point this branch diverged from
main, not just HEAD's immediate parent, so a multi-commit branch's own
earlier commits are never mistaken for "the baseline"; falls back to
HEAD~1 only when origin/main does not resolve; override with
SUPERJEV_SWEEP_BASELINE_REF) and runs
the identical window-assembly + fact-derivation pipeline through IT. A
recorded `results-v3-shim*/*.exit` file's timing relative to the code
under test is not guaranteed — the set-2 results directory here was
captured minutes AFTER the change under test merged, so treating its exit
codes as the "old" action silently hid exactly the flip this sweep exists
to catch. Comparing two in-process runs of two known commits removes that
ambiguity.

Never prints draft/evidence/transcript text — only case ids, kind
(truth/lie), the fact FAMILY that fired (from the fixed marker set, not
free text) and whether the decision flips. Safe to run and to share output
from; no set-3 payload content is ever quoted.

Exits 0 iff zero TRUTHS flip to a new block. Exits 1 otherwise.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SKILL_DIR.parent.parent
sys.path.insert(0, str(SKILL_DIR))
import superjev as sj  # noqa: E402

SETS = [
    ("set1-20260917", "/Users/admin/super-jev-experiments/gate-bench-20260917",
     "cases.json", "payloads-v3"),
    ("set2-20260918", "/Users/admin/super-jev-experiments/gate-bench-20260918",
     "cases2.json", "payloads-v3-2"),
    ("set3-20260918-fleet", "/Users/admin/super-jev-experiments/gate-bench-20260918-fleet",
     "cases3.json", "payloads-v3-3"),
    ("set4-20260918-blind", "/Users/admin/super-jev-experiments/gate-bench-20260918-blind",
     "cases4.json", "payloads-v3-4"),
]


def _resolve_baseline_ref():
    """The git ref this sweep diffs against. SUPERJEV_SWEEP_BASELINE_REF
    always wins when set. Otherwise `git merge-base HEAD origin/main` —
    the commit this branch actually diverged from, not just HEAD's
    immediate parent (HEAD~1 would be wrong for any branch more than one
    commit ahead of main: it would compare against this branch's OWN
    earlier commit, not against main). Falls back to HEAD~1 only when
    origin/main does not resolve at all (no fetch, detached clone, no
    remote configured)."""
    override = os.environ.get("SUPERJEV_SWEEP_BASELINE_REF")
    if override:
        return override
    try:
        subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--verify", "origin/main"],
            capture_output=True, text=True, timeout=30, check=True)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return "HEAD~1"
    try:
        mb = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "merge-base", "HEAD", "origin/main"],
            capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return "HEAD~1"
    return mb or "HEAD~1"


def _resolve_ref_sha(ref):
    """`ref` resolved to a full commit sha for the header line, or None
    when git cannot resolve it (never raises)."""
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", ref],
            capture_output=True, text=True, timeout=30, check=True).stdout.strip() or None
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return None


def _load_baseline_module():
    """superjev.py as of `_resolve_baseline_ref()` loaded as a SEPARATE
    module `sj_old`, so `old_blocked` below is a real run of the
    pre-change deterministic arm, not a guess from a possibly-stale
    recorded exit file. Returns (None, ref, sha) — sweep falls back to
    "assume not blocked", the conservative direction; it can only
    OVER-report a flip, never hide one — when git or the import fails.

    The exec'd module's own `SKILL_DIR`/`REPO_ROOT` are computed from
    `__file__`, which for this module is the throwaway NamedTemporaryFile
    path below, not this repo — so left alone, `_cited_file_roots()` on
    the baseline side searches the temp directory instead of this repo
    and silently finds nothing, making the baseline blind to any
    cited-file tail the live side sees (2026-09-18, PR #67 round 2, t38).
    Both path constants are overwritten from the live `sj` module
    immediately after exec so both sides assemble the identical window."""
    ref = _resolve_baseline_ref()
    sha = _resolve_ref_sha(ref)
    try:
        src = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", f"{ref}:skills/super-jev/superjev.py"],
            capture_output=True, text=True, timeout=30, check=True).stdout
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as e:
        print(f"  baseline: could not read superjev.py @ {ref} ({e!r}) — "
              f"falling back to 'assume not blocked' for the old side")
        return None, ref, sha
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix="_superjev_baseline.py",
                                      delete=False, encoding="utf-8")
    tmp.write(src)
    tmp.close()
    try:
        spec = importlib.util.spec_from_file_location("superjev_baseline", tmp.name)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # See docstring: without this, mod.SKILL_DIR/mod.REPO_ROOT point at
        # tmp's directory, not this repo.
        mod.SKILL_DIR = sj.SKILL_DIR
        mod.REPO_ROOT = sj.REPO_ROOT
        return mod, ref, sha
    except Exception as e:
        print(f"  baseline: superjev.py @ {ref} failed to import ({e!r}) — "
              f"falling back to 'assume not blocked' for the old side")
        return None, ref, sha


def _window_fact_reasons(mod, transcript_path, draft):
    """One case's fact-block reasons under module `mod` (either the live
    `sj` or a `_load_baseline_module()` result), assembling the window the
    same way `cmd_hook`'s "hook gate" branch does: transcript derivation,
    then the receipt-turn extra fact, then the cited-file tail, then
    compose_window_with_facts. Raises on a bad transcript; the caller
    decides what to do with that."""
    derived, wmeta = mod._derive_evidence_text_from_transcript(
        str(transcript_path), return_meta=True)
    receipt_extra_facts = None
    if wmeta is not None and wmeta.get("current_turn_empty"):
        receipt_idx = mod._receipt_turn_index(wmeta)
        if receipt_idx is not None:
            fact = mod._receipt_turn_extra_fact(receipt_idx)
            receipt_extra_facts = [fact] if fact else None
    cited_block = mod.build_cited_file_block(draft, window_text=derived)
    if cited_block:
        derived = (derived + "\n\n===\n\n" + cited_block) if derived else cited_block
    derived, facts, _fmeta = mod.compose_window_with_facts(
        derived or "", draft, cap_bytes=0, extra_facts=receipt_extra_facts)
    return mod._fact_block_reasons(facts)


def main():
    total_cases = 0
    flips = []
    truths_flipped = []
    fact_family_counts = {}

    baseline, baseline_ref, baseline_sha = _load_baseline_module()
    print(f"baseline ref: {baseline_ref}  sha: {baseline_sha or '(unresolved)'}")

    for set_name, bench_dir, cases_file, payloads_dir in SETS:
        bench_dir = Path(bench_dir)
        cases_path = bench_dir / cases_file
        if not cases_path.exists():
            print(f"  {set_name}: no {cases_file} at {bench_dir} — skipping")
            continue
        cases = json.loads(cases_path.read_text(encoding="utf-8"))

        for case in cases:
            cid, kind = case["id"], case["kind"]
            pdir = bench_dir / payloads_dir / cid
            transcript_path = pdir / "transcript.jsonl"
            if not transcript_path.exists():
                continue
            total_cases += 1
            # Mirror cmd_hook's own "hook gate" branch, which strips
            # machine tags from the draft before it ever reaches the
            # window builder (see superjev.py's `text =
            # _strip_machine_tags(text)` in the Stop-hook gate path). A
            # no-op for today's recorded drafts, but keeps this replay an
            # exact mirror rather than a close one.
            draft = sj._strip_machine_tags(case.get("draft") or "")
            try:
                fact_reasons = _window_fact_reasons(sj, transcript_path, draft)
            except Exception as e:                       # never let one bad
                print(f"  {set_name}/{cid}: window build raised {e!r} — skipped")
                continue
            old_blocked = False
            if baseline is not None:
                try:
                    old_blocked = bool(_window_fact_reasons(baseline, transcript_path, draft))
                except Exception as e:
                    print(f"  {set_name}/{cid}: baseline window build raised "
                          f"{e!r} — assuming not blocked")
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
