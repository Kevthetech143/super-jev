#!/usr/bin/env python3
"""Folder pre-filter: only the top-K word-matched pointers go to Jev navigation.

    python3 -m pytest skills/super-jev/tests/test_prefilter.py -q
"""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "ask.py"
spec = importlib.util.spec_from_file_location("ask_prefilter", SCRIPT)
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

PTRS = ["me-brain-root", "a", "b", "c", "d", "e"]


def found(**scores):
    return [(s, f"/x/{p}.md", p) for p, s in scores.items()]


def test_keeps_top_k_in_original_order():
    kept, why = ask.prefilter_pointers(PTRS, "zz", found(d=9, b=5, a=1), 2)
    assert (kept, why) == (["b", "d"], "top-k")


def test_always_keeps_own_brain_root():
    kept, _ = ask.prefilter_pointers(PTRS, "me", found(d=9, b=5), 1)
    assert kept == ["me-brain-root", "d"]


def test_other_principals_root_not_forced():
    kept, _ = ask.prefilter_pointers(PTRS, "you", found(d=9, b=5), 1)
    assert kept == ["d"]


def test_uses_best_file_score_per_pointer():
    f = found(b=5) + [(1, "/x/d1.md", "d"), (8, "/x/d2.md", "d")]
    kept, _ = ask.prefilter_pointers(PTRS, "zz", f, 1)
    assert kept == ["d"]


def test_no_scores_falls_back_to_all():
    assert ask.prefilter_pointers(PTRS, "zz", [], 2) == (PTRS, "no-scores")


def test_flat_scores_fall_back_to_all():
    assert ask.prefilter_pointers(PTRS, "zz", found(a=10, b=9.8, c=9.5), 2) == (PTRS, "flat")


def test_disabled_and_few_pointers():
    assert ask.prefilter_pointers(PTRS, "zz", found(a=5), 0) == (PTRS, "off")
    assert ask.prefilter_pointers(PTRS, "zz", found(a=5), 6) == (PTRS, "few")


def test_env_k(monkeypatch):
    monkeypatch.setenv("SUPERJEV_PREFILTER_K", "0")
    assert ask._prefilter_k() == 0
    monkeypatch.setenv("SUPERJEV_PREFILTER_K", "junk")
    assert ask._prefilter_k() == 6
    monkeypatch.delenv("SUPERJEV_PREFILTER_K")
    assert ask._prefilter_k() == 6
