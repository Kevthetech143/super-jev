#!/usr/bin/env python3
"""ask.py --claim: per-file verdicts ride the listwise Jev call and plain code combines them.

    python3 -m pytest skills/super-jev/tests/test_claim_verdict.py -q
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ask  # noqa: E402


def _f(tmp_path, name, text, age_days=0):
    p = tmp_path / name
    p.write_text(text)
    t = 1_780_000_000 - age_days * 86400
    os.utime(p, (t, t))
    return str(p)


def rec(verdict, prob, line=None, line_no=None):
    return {"verdict": verdict, "prob": prob, "line": line, "line_no": line_no}


def test_sure_true_quotes_the_proof_line(tmp_path):
    a = _f(tmp_path, "a.md", "x")
    b = _f(tmp_path, "b.md", "y")
    word, lines = ask.claim_verdict({a: rec("supported", 0.98, "limit is 255", 7), b: rec("not_stated", 0.99)}, [a, b])
    assert word == "TRUE" and lines[0] == "TRUE (0.98)" and a in lines[1]
    assert 'line 7: "limit is 255"' in lines[2] and "Other files read: b.md" in lines[-1]


def test_files_that_disagree_are_a_conflict_newest_first(tmp_path):
    old = _f(tmp_path, "pr-5.md", "x", age_days=300)
    new = _f(tmp_path, "protocol.md", "y", age_days=1)
    word, lines = ask.claim_verdict({old: rec("contradicted", 0.95), new: rec("supported", 0.99)}, [old, new])
    assert word == "CONFLICT" and "newest says TRUE" in lines[0]
    assert new in lines[1] and old in lines[2]


def test_under_the_line_is_unsure_never_a_verdict(tmp_path):
    a = _f(tmp_path, "a.md", "x")
    word, lines = ask.claim_verdict({a: rec("contradicted", 0.84)}, [a])
    assert word == "UNSURE" and "0.84" in lines[0] and a in lines[1]


def test_all_not_stated_is_not_found_in_the_files_read(tmp_path):
    a = _f(tmp_path, "a.md", "x")
    word, lines = ask.claim_verdict({a: rec("not_stated", 1.0)}, [a])
    assert word == "NOT FOUND" and "1 file(s) I read" in lines[0]
    assert ask.claim_verdict({}, [])[0] == "NOT FOUND"


def test_partial(tmp_path):
    a = _f(tmp_path, "a.md", "x")
    b = _f(tmp_path, "b.md", "y")
    word, lines = ask.claim_verdict({a: rec("partly", 0.93), b: rec("partly", 0.95)}, [a, b])
    assert word == "PARTIAL" and len(lines) == 3


def test_answers_are_read_per_file_with_the_picked_line(tmp_path):
    a = _f(tmp_path, "a.md", "# Title\nThe choice limit is 255 options.\n")
    files = {a: open(a).read()}
    qs, lines = ask.claim_questions("A choice can have 255 options.", [a], files)
    assert set(qs) == {"verdict_1", "line_1"} and "`file_1.text`" in qs["verdict_1"]["instructions"]
    answers = {"verdict_1": {"choice": "supported", "probabilities": {"supported": 0.97}},
               "line_1": {"choice": "L1", "probabilities": {"L1": 0.9}}}
    got = ask.read_claim_answers(answers, [a], lines)[a]
    assert got == {"verdict": "supported", "prob": 0.97, "line": "The choice limit is 255 options.", "line_no": 2}


def test_listwise_carries_the_claim_questions_in_the_same_call(tmp_path, monkeypatch):
    a = _f(tmp_path, "a.md", "The choice limit is 255 options.\n")
    calls = []

    def fake(state, questions):
        calls.append(set(questions))
        return {"answers": {"pick": {"choice": "file_1", "probabilities": {"file_1": 0.95}},
                            "verdict_1": {"choice": "supported", "probabilities": {"supported": 0.96}},
                            "line_1": {"choice": "L1", "probabilities": {"L1": 0.9}}}}
    monkeypatch.setattr(ask, "jev_choice", fake)
    monkeypatch.setitem(ask._CLAIM, "text", "A choice can have 255 options.")
    ask._STAGE.clear()
    assert ask.judge_listwise("A choice can have 255 options.", [a]) == (a, 0.95)
    assert len(calls) == 1 and {"pick", "verdict_1", "line_1"} <= calls[0]
    assert ask._STAGE["claim_files"][a]["verdict"] == "supported"
    monkeypatch.setitem(ask._CLAIM, "text", None)
    ask.judge_listwise("q", [a])
    assert calls[-1] == {"pick"}  # a normal ask is unchanged


def test_a_saved_verdict_drops_when_its_proof_file_changes(tmp_path):
    a = _f(tmp_path, "a.md", "The choice limit is 255 options.\n")
    sdir = tmp_path / "state"
    ask.claim_cache_put(sdir, "A choice can have 255 options.", "TRUE", a, rec("supported", 0.97, "x", 1))
    assert ask.claim_cache_get(sdir, "a choice can have 255 options")["verdict"] == "TRUE"
    Path(a).write_text("changed\n")
    assert ask.claim_cache_get(sdir, "A choice can have 255 options.") is None
    assert ask.claim_cache_get(sdir, "A choice can have 255 options.") is None
