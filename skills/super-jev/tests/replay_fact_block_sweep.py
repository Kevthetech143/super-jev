#!/usr/bin/env python3
"""Offline, no-judge-call sweep for the CONTRADICTED_BY_FACT deterministic
block change (see gate-bench-20260918-fleet/analysis/SET3-LIVE-GAP.md and
_fact_block_reasons in superjev.py).

For every case in the four recorded benches (gate-bench-20260917 = 40,
gate-bench-20260918 = 29, gate-bench-20260918-fleet = 30,
gate-bench-20260918-blind = 40; 139 total), this rebuilds the SAME wide
evidence window the live Stop-hook gate builds — `_derive_evidence_text_
from_transcript` (receipt shapes included), then (2026-09-18, post-#64 fix)
the receipt-turn extra fact AND the `[cited files]` tail
(`build_cited_file_block`), exactly as `cmd_hook`'s own "hook gate" branch
assembles them, THEN `compose_window_with_facts` — straight from the
recorded payload transcript, with NO subprocess call and NO network
access — pure offline replay. Skipping the cited-file step used to make
this sweep blind to any case whose draft names its own source ("per the
summary log"): t38 (set 2) blocked live on a fact that lived only in a
cited file's tail, and the old sweep, never having read that tail, could
not see the fact at all — not "predicted no block", genuinely couldn't
reproduce the window. See docs/hooks.md. It also used to build the receipt
shapes family's facts but never actually pass them into
`compose_window_with_facts`, so that family was never exercised by this
replay at all (2026-09-18, PR #68 round 2) — `_window_fact_reasons` now
passes `receipt_facts=wmeta.get("receipt_shape_facts")` through, on both
the live and the baseline side.

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

Beyond the block/no-block decision, this also WARNS (never fails the run)
when a recorded case's window — truth OR lie — loses a receipt-worthy line
the baseline window carried. A receipt line dropped by the window-budget
change is a refutation the judge might no longer see even when no
deterministic arm's decision flips on it, and a TRUTH's window is exactly
where that costs the most: the dropped line is what would have kept a true
reply from reading as unsupported. Restricting this to lies alone would
miss that signal entirely. See "receipt lines dropped" in the summary
below, printed for truths and lies separately.

Never prints draft/evidence/transcript text — only case ids, kind
(truth/lie), the fact FAMILY that fired (from the fixed marker set, not
free text) and whether the decision flips. Safe to run and to share output
from; no bench payload content is ever quoted.

Exits 0 iff zero TRUTHS flip to a new block. Exits 1 otherwise.
"""
import importlib.util
import inspect
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
    # Set 4, the blind set (2026-09-18): recorded cases whose drafts were
    # never read while the window code was being written.
    # Added 2026-09-18 with the window-budget change, which is the first
    # change to touch how much of each LAYER survives the cap — the arms
    # have to be replayable over every recorded set, not three of four.
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


def _assembled_window(mod, transcript_path, draft):
    """(window_text, facts) for one case under module `mod` (either the
    live `sj` or a `_load_baseline_module()` result), assembling the
    window the same way `cmd_hook`'s "hook gate" branch does: transcript
    derivation, then the receipt-turn extra fact, then the cited-file
    tail, then compose_window_with_facts. Raises on a bad transcript; the
    caller decides what to do with that.

    `receipt_facts` is only ever passed to `mod.compose_window_with_facts`
    when THAT module's own signature accepts it — the baseline module is a
    real historical `superjev.py` (as of `_resolve_baseline_ref()`), which
    on this branch predates the parameter entirely. Passing it
    unconditionally raised a bare `TypeError` on every single baseline
    call, which this sweep's own "assume not blocked" fallback silently
    swallowed — a baseline call failing on ALL 139 cases is what "assume
    not blocked" exists to survive, but a family that never actually RAN
    on the baseline side made every one of its real, pre-existing fact
    families (WRITTEN FILE, LABELLED VALUE, ...) look unresolved too, and
    produced flips that were never about receipt shapes at all."""
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
    kwargs = {"cap_bytes": 0, "extra_facts": receipt_extra_facts}
    if "receipt_facts" in inspect.signature(mod.compose_window_with_facts).parameters:
        kwargs["receipt_facts"] = (wmeta or {}).get("receipt_shape_facts")
    window_text, facts, _fmeta = mod.compose_window_with_facts(derived or "", draft, **kwargs)
    return window_text, facts


def _window_fact_reasons(mod, transcript_path, draft):
    """One case's fact-block reasons under module `mod`. See
    `_assembled_window`."""
    _window_text, facts = _assembled_window(mod, transcript_path, draft)
    return mod._fact_block_reasons(facts)


def _receipt_worthy_line_count(mod, window_text):
    """How many lines of an assembled window text match `mod`'s own
    receipt-worthy pattern (`_RECEIPT_WORTHY_RE`) — a cheap, deterministic
    proxy for "lines the judge could read as a receipt", used only to
    WARN (never fail) when a recorded case's window — truth or lie —
    loses one of these lines relative to the baseline, reported for
    truths and lies separately (see "How this was measured" in
    docs/hooks.md). Never raises; a module with no such pattern (a very
    old baseline) counts zero."""
    rx = getattr(mod, "_RECEIPT_WORTHY_RE", None)
    if rx is None or not window_text:
        return 0
    return sum(1 for line in window_text.splitlines() if rx.search(line))


def main():
    total_cases = 0
    flips = []
    truths_flipped = []
    fact_family_counts = {}
    baseline_errors = []
    live_errors = []
    receipt_lines_dropped = []   # [(set_name, cid, old_count, new_count)]

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
                window_text, facts = _assembled_window(sj, transcript_path, draft)
                fact_reasons = sj._fact_block_reasons(facts)
            except Exception as e:
                # Mirrors the baseline treatment below: a live-side
                # exception used to print-and-`continue` with no counter
                # and no effect on the exit code, so a live-module crash
                # quietly shrank coverage (fewer cases replayed, no sign
                # anything went wrong) instead of failing the sweep. Now
                # every one is counted, printed under its own "LIVE ERROR"
                # line, and turns the run into a hard FAIL — a live module
                # that cannot run on a case is not a case this sweep
                # silently gets to skip.
                live_errors.append((set_name, cid, repr(e)))
                print(f"  {set_name}/{cid}: LIVE ERROR — window build "
                      f"raised {e!r}")
                continue
            old_blocked = False
            old_window_text = None
            if baseline is not None:
                try:
                    old_window_text, old_facts = _assembled_window(
                        baseline, transcript_path, draft)
                    old_blocked = bool(baseline._fact_block_reasons(old_facts))
                except Exception as e:
                    # A baseline call that raises is NOT "assume not
                    # blocked" and quiet about it — that swallowed a real
                    # TypeError (a kwarg this branch's live side passes
                    # that the pre-change baseline module doesn't accept)
                    # on every single case, which hid the baseline's real
                    # answer for every family, not just receipt shapes, and
                    # produced decision "flips" that were never real (PR
                    # #68 round 2). `old_blocked = False` below still lets
                    # the loop finish and report what it can, but every
                    # such case is counted, printed, and turns the whole
                    # run into a hard FAIL — a baseline that cannot run is
                    # a sweep that cannot tell you anything.
                    baseline_errors.append((set_name, cid, repr(e)))
                    print(f"  {set_name}/{cid}: BASELINE ERROR — window build "
                          f"raised {e!r}")
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
            # Warning-only check (see docs/hooks.md, "How this was
            # measured"): a recorded case whose window lost a
            # receipt-worthy line relative to the baseline is a real
            # signal even when no deterministic arm's decision flips on
            # it — a dropped receipt is a refutation the judge might no
            # longer see. Runs over BOTH truths and lies: a truth's
            # window is exactly where a dropped supporting receipt does
            # the most damage (it is the line that would have kept a
            # true reply from being misread as unsupported), and
            # restricting this to lies alone cannot see that — on the
            # recorded sets, the one case that actually loses a line is
            # a truth, not a lie (2026-09-18, review round 3). Never
            # affects the exit code.
            if old_window_text is not None:
                old_n = _receipt_worthy_line_count(baseline, old_window_text)
                new_n = _receipt_worthy_line_count(sj, window_text)
                if new_n < old_n:
                    receipt_lines_dropped.append((set_name, cid, kind, old_n, new_n))

    print(f"\ncases replayed     : {total_cases}")
    print(f"fact families fired: {fact_family_counts or '(none)'}")
    print(f"live errors        : {len(live_errors)}")
    for set_name, cid, err in live_errors:
        print(f"  LIVE ERROR  {set_name:<20} {cid:<6} {err}")
    print(f"baseline errors    : {len(baseline_errors)}")
    for set_name, cid, err in baseline_errors:
        print(f"  BASELINE ERROR  {set_name:<20} {cid:<6} {err}")
    print(f"decision flips     : {len(flips)}")
    for set_name, cid, kind in flips:
        print(f"  NEW BLOCK  {set_name:<20} {cid:<6} kind={kind}")
    print(f"truths newly blocked: {len(truths_flipped)}")
    for set_name, cid in truths_flipped:
        print(f"  TRUTH BLOCKED  {set_name} {cid}")
    truth_lines_dropped = [r for r in receipt_lines_dropped if r[2] == "truth"]
    lie_lines_dropped = [r for r in receipt_lines_dropped if r[2] == "lie"]
    print(f"truth windows with fewer receipt lines than baseline: "
          f"{len(truth_lines_dropped)}")
    for set_name, cid, _kind, old_n, new_n in truth_lines_dropped:
        print(f"  WARN  {set_name:<20} {cid:<6} receipt lines {old_n} -> {new_n}")
    print(f"lie windows with fewer receipt lines than baseline: "
          f"{len(lie_lines_dropped)}")
    for set_name, cid, _kind, old_n, new_n in lie_lines_dropped:
        print(f"  WARN  {set_name:<20} {cid:<6} receipt lines {old_n} -> {new_n}")

    if live_errors:
        print(f"\nFAIL — {len(live_errors)} live-side call(s) raised instead "
              "of running; that case never had a chance to fire any fact "
              "family, so this sweep's coverage is smaller than it looks. "
              "Fix the live call (or the code under test) before trusting "
              "this sweep.")
        return 1
    if baseline_errors:
        print(f"\nFAIL — {len(baseline_errors)} baseline call(s) raised instead "
              "of running; every decision-flip result above is unreliable "
              "until the baseline module actually runs. Fix the baseline "
              "call (or the code under test) before trusting this sweep.")
        return 1
    if truths_flipped:
        print("\nFAIL — a truth flipped to block; see docs above for the "
              "fact family responsible and gate it out.")
        return 1
    print("\nPASS — zero truths newly blocked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
