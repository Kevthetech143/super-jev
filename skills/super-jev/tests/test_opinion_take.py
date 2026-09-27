#!/usr/bin/env python3
"""Stance asks ("our take on X", "what do we think of X") are opinion asks: they get
the "does it answer" wording and a strongly routed file that was read stays possible.
A fact question that only mentions a take, view or verdict keeps the exact-value
wording and gets no route-keep. No network and no real key.

    python3 -m pytest skills/super-jev/tests/test_opinion_take.py -q
"""
import importlib.util
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_opinion_take", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


@pytest.fixture(autouse=True)
def no_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})


def _memory(cands):
    def fake(req):
        if req["action"] == "cached":
            return {"status": "miss"}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1"}]}
        return {"status": "candidates", "candidates": cands} if cands else {"status": "no-candidates"}
    return fake


STANCE = ["our take on Acme stock", "what's our view on the lease renewal",
          "what do we think of the new supplier", "my opinion about the Q3 plan"]
NOT_STANCE = ["how long does the course take", "take on more debt next month",
              "when is the PLUG meeting", "what car do I have"]
# Fact questions that merely contain a stance noun (review of 9521697).
FACT_WITH_STANCE_NOUN = ["What date did we publish our take on Acme stock?",
                         "What percentage is our take of ticket sales?",
                         "Describe the view of the river from room 402",
                         "What is the verdict on case 123?"]


@pytest.mark.parametrize("q", STANCE)
def test_stance_asks_get_answer_wording(q):
    assert ask.confirm_label(q) == ask.ANSWER_LABEL


def test_value_stance_ask_keeps_exact_value_wording():
    # a value question still wants the exact figure
    q = "our take on the price of ACME"
    assert ask.is_value_question(q) and ask.confirm_label(q) == ask.CONFIRM_LABEL


@pytest.mark.parametrize("q", NOT_STANCE)
def test_non_stance_asks_are_not_opinion(q):
    assert not ask.is_stance_question(q)


@pytest.mark.parametrize("q", FACT_WITH_STANCE_NOUN)
def test_fact_questions_with_stance_nouns_keep_exact_wording(q):
    assert not ask.is_stance_question(q)
    assert ask.confirm_label(q) == ask.CONFIRM_LABEL


@pytest.mark.parametrize("q", FACT_WITH_STANCE_NOUN)
def test_fact_questions_with_stance_nouns_get_no_route_keep(tmp_path, monkeypatch, capsys, q):
    a = tmp_path / "note.md"
    a.write_text("file")
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.99, "originalPath": str(a)}]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({}, set(), None, {}))
    ask.lookup(q, "me", tmp_path / "s")
    assert "(possible:" not in capsys.readouterr().out


@pytest.mark.parametrize("q,route,kept", [("our take on Acme stock", 0.99, True),
                                          ("what do we think of Acme", 0.99, True),
                                          ("our take on Acme stock", 0.7, False),
                                          ("Acme stock price right now", 0.99, False)])
def test_strong_route_kept_as_possible_on_stance_asks(tmp_path, monkeypatch, capsys, q, route, kept):
    a = tmp_path / "company.md"
    a.write_text("file")
    monkeypatch.setattr(ask, "memory", _memory([{"score": route, "originalPath": str(a)}]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({}, set(), None, {}))
    ask.lookup(q, "me", tmp_path / "s")
    out = capsys.readouterr().out
    assert (f"{ask.POSSIBLE_FLOOR:5.2f}  {a}  [p1]  (possible:" in out) is kept
