#!/usr/bin/env python3
"""Family-6 wording: the WRITTEN FILE contradiction fact is a positive
open list — singular for one receipt, plural for several — and never
carries a closed-inventory word like "only".
"""
import importlib.util
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("superjev_fam6", SKILL / "superjev.py")
_sj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sj)


def _window(*receipts):
    return "[current turn]\n" + "".join(
        "[from: Write %s @ /Users/admin/x]\n" % r for r in receipts
    )


def test_written_file_fact_one_file_singular_open_list():
    facts = _sj._facts_written_file_claims(
        _window("/tmp/report.md"), "I updated other.md just now.")
    assert facts == [
        "WRITTEN FILE: the draft names other.md; file written "
        "in this window: report.md \u2014 CONTRADICTED_BY_FACT."
    ]
    assert "only" not in facts[0].lower()


def test_written_file_fact_three_files_plural_open_list():
    facts = _sj._facts_written_file_claims(
        _window("/tmp/a.txt", "/tmp/b.txt", "/tmp/c.txt"),
        "Saved other.md, Sir.")
    assert facts == [
        "WRITTEN FILE: the draft names other.md; files written "
        "in this window: a.txt, b.txt, c.txt \u2014 CONTRADICTED_BY_FACT."
    ]
    assert "only" not in facts[0].lower()
