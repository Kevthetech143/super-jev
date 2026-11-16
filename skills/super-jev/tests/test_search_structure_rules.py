#!/usr/bin/env python3
"""Structural search rules, on made-up people and notes: whose records a question is about, the resolved
person's name in the search words, dates in a question against dates in a file's name, and singular/plural
folder words. Pure functions, no state, no provider calls.

    python3 -m pytest skills/super-jev/tests/test_search_structure_rules.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ask  # noqa: E402

FOLKS = {"nora": {"mother"}, "gustavo": {"father"}, "ivy": {"daughter"}, "sam": {"self"}}


def test_an_asking_i_adds_no_one():
    assert ask.question_people("I need my father Gustavo's list", FOLKS) == {"gustavo"}
    assert ask.question_people("can you tell me what my mom takes", FOLKS) == {"nora"}


def test_i_joined_by_and_adds_self():
    assert ask.question_people("trips for my mom and I", FOLKS) == {"nora", "sam"}
    assert ask.question_people("me and Gustavo's plans", FOLKS) == {"gustavo", "sam"}


def test_a_name_after_a_determiner_is_a_thing():
    # "my ivy" is a plant, not Ivy: the question is about the asker
    assert ask.question_people("how often do I water my ivy?", FOLKS) == {"sam"}
    assert ask.question_people("an ivy on the wall", FOLKS) == set()
    assert ask.question_people("what is Ivy's next checkup?", FOLKS) == {"ivy"}


def test_relation_only_question_searches_the_name():
    # the relation word that named the person is dropped: their own files rarely say "dad"
    assert ask.with_people("what is my dad taking", {"gustavo"}) == "what is my taking gustavo"
    assert ask.with_people("what is my dad taking", set()) == "what is my dad taking"
    assert ask.with_people("Gustavo's list", {"gustavo"}) == "Gustavo's list"


def test_dates_in_question_and_name():
    assert ask.question_dates("2025 tax tracker") == {(2025, None, None)}
    assert ask.question_dates("the log from september 8") == {(None, 9, 8)}
    assert ask.question_dates("notes from 2026-03-04") == {(2026, 3, 4)}
    assert ask.question_dates("what is my plan") == set()
    assert ask.name_dates("/n/taxes/TRACKER-2025.md") == {(2025, None, None)}
    assert ask.name_dates("/n/log-2026-09-08.md") == {(2026, 9, 8)}
    assert ask.name_dates("/n/plan.md") == set()
    assert ask.dates_agree({(None, 9, 8)}, {(2026, 9, 8)}) is True
    assert ask.dates_agree({(None, 9, 8)}, {(2026, 9, 5)}) is False
    assert ask.dates_agree({(2025, None, None)}, {(2017, None, None)}) is False
    assert ask.dates_agree(set(), {(2017, None, None)}) is None


def test_plural_forms_not_stems():
    assert {"venture", "ventures"} <= ask.plural_forms("venture")
    assert "tax" in ask.plural_forms("taxes")
    assert "medical" not in ask.plural_forms("medications")
