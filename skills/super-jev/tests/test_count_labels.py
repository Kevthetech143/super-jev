#!/usr/bin/env python3
"""Count-arm false-positive guards (2026-09-18).

Four live false blocks from tonight, each a truthful reply the
deterministic arms misread as a count or a contradiction:

1. "PR #63, #68" read as claimed test counts 63/68 against the real test
   totals.
2. "round 5 tests" read as a claimed count of 5 tests.
3. "round 3" vs "round 4" (two different PRs) called a contradiction in
   the LABELLED VALUE arm.
4. "#69 vs judge drift" read as a claimed count of 69.

Plus a positive control: a real mismatch ("12 tests passed" against an
evidence total of 11) must still block, and a qualified generic label
("round 3 of PR #63") may still contradict.
"""
import importlib.util
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "superjev_count_labels", SKILL / "superjev.py")
sj = importlib.util.module_from_spec(spec)
sys.modules["superjev_count_labels"] = sj
spec.loader.exec_module(sj)


def test_count_arm_ignores_pr_and_issue_reference_numbers():
    # Tonight's false block: "#63, #68" sat within the token window of a
    # "tests" keyword and was read as claimed test counts.
    draft = "Fixed #63, #68, all tests green, Sir."
    assert sj._extract_labelled_draft_counts(draft).get("tests") in (None, set())
    assert sj.deterministic_block_reasons(draft, "34 passed in 6.94s\n") == []


def test_count_arm_ignores_round_step_enumerators():
    # "round 5 tests" is round 5, not a claim of 5 tests.
    draft = "Round 5 tests are green on the rerun, Sir."
    assert sj._extract_labelled_draft_counts(draft).get("tests") in (None, set())
    assert sj.deterministic_block_reasons(draft, "34 passed in 6.94s\n") == []


def test_count_arm_ignores_a_bare_hash_reference():
    # Tonight's false block: "#69" read as a claimed count of 69.
    draft = "#69 vs judge drift: tests green, Sir."
    assert sj._extract_labelled_draft_counts(draft).get("tests") in (None, set())
    assert sj.deterministic_block_reasons(draft, "34 passed in 6.94s\n") == []


def test_labelled_value_arm_needs_a_subject_qualifier_for_generic_labels():
    # Tonight's false block: "round 3" (one PR) against the window's
    # "round 4" (a different PR) was called a contradiction. A bare
    # generic label names no subject, so it never blocks.
    window = (
        "[current turn]\n"
        "[from: Bash cat rounds.md @ /Users/example/x]\n"
        "round: 4\n"
    )
    facts = sj.derive_window_facts(window, "Round 3 of the fix is green, Sir.")
    assert not any("CONTRADICTED_BY_FACT" in f for f in facts)


def test_labelled_value_arm_still_contradicts_a_qualified_generic_label():
    # The same shape WITH a subject qualifier may still contradict: the
    # draft anchors "round 3" to PR #63.
    window = (
        "[current turn]\n"
        "[from: Bash cat rounds.md @ /Users/example/x]\n"
        "round: 4\n"
    )
    facts = sj.derive_window_facts(window, "Round 3 of PR #63 is green, Sir.")
    assert any("LABELLED VALUE" in f and "CONTRADICTED_BY_FACT" in f
               and "'round'" in f for f in facts)


def test_count_arm_still_blocks_a_real_test_count_mismatch():
    # Positive control: the fixes must not weaken real count checks.
    reasons = sj.deterministic_block_reasons(
        "12 tests passed, Sir.", "11 passed in 6.94s\n")
    assert any("count mismatch (tests)" in r for r in reasons)


def test_count_arm_ignores_plural_pr_hash_references():
    # v1 review hole: "Fixed PRs #63, #68" tokenizes as PRs, #, 63 — the
    # "#" branch matched first so the ref-word branch never ran, and
    # "PRs" (a real label) kept 63/68 as claimed counts. A ref word
    # before the "#" is a reference, never a claimed count.
    draft = "Fixed PRs #63, #68; tests pass"
    assert sj._extract_labelled_draft_counts(draft).get("tests") in (None, set())
    assert sj.deterministic_block_reasons(draft, "34 passed in 6.94s\n") == []


def test_count_arm_ignores_singular_ref_word_before_hash():
    # Same hole, singular form: "round 4 and PR #63" claims nothing.
    draft = "round 4 and PR #63"
    assert sj._extract_labelled_draft_counts(draft) == {}
    assert sj.deterministic_block_reasons(draft, "34 passed in 6.94s\n") == []


def test_count_arm_still_claims_real_label_before_hash():
    # Carve-out stays green: "Tests #52 passed" really does claim 52 tests,
    # and a lie there still blocks.
    assert sj._extract_labelled_draft_counts("Tests #52 passed") == {"tests": {52}}
    reasons = sj.deterministic_block_reasons(
        "Tests #52 passed, Sir.", "51 passed in 1.2s\n")
    assert any("count mismatch (tests)" in r for r in reasons)
