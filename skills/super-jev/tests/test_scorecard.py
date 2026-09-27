#!/usr/bin/env python3
"""Scorecard: harvest cases from an agent's own records, keep them, and flag a build that
drops a case the baseline read. No network, no Jev.

    python3 -m pytest skills/super-jev/tests/test_scorecard.py -q
"""
import json
import pytest
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
import scorecard as sc  # noqa: E402


def _w(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_harvest_keeps_standing_approvals_and_outcomes_with_real_files(tmp_path):
    a, b, c = tmp_path / "a.md", tmp_path / "b.md", tmp_path / "c.md"
    for f in (a, b, c):
        f.write_text("x\n")
    _w(tmp_path / "approvals.jsonl", [
        {"question": "q1", "approved_by": "human", "file": str(a)},
        {"question": "q2", "approved_by": "human", "file": str(b)},
        {"question": "q2", "approved_by": None, "removed_by": "miss"},
        {"question": "q3", "approved_by": "human"}])  # no file: nothing to grade against
    _w(tmp_path / "traces.jsonl", [
        {"kind": "outcome", "question": "q4", "result": "wrong", "file": str(c)},
        {"kind": "outcome", "question": "q5", "result": "wrong", "file": "notes/rel.md Rule 2"},
        {"kind": "trace", "question": "q6"}])
    got = {c["question"]: c["gold"] for c in sc.harvest(tmp_path, "me")}
    assert got == {"q1": [str(a)], "q4": [str(c)]}


def test_saved_cases_outlive_the_records_they_came_from(tmp_path):
    first = sc.merge_saved(tmp_path, [{"question": "q1", "gold": ["/a"]}])
    again = sc.merge_saved(tmp_path, [{"question": "q1", "gold": ["/b"]}, {"question": "q2", "gold": ["/c"]}])
    assert [c["question"] for c in first] == ["q1"]
    # A later record for the same question (a --miss naming the right file) replaces the old gold.
    assert [(c["question"], c["gold"]) for c in again] == [("q1", ["/b"]), ("q2", ["/c"])]
    # A question the harvest no longer covers (its trace rotated away) is kept.
    later = sc.merge_saved(tmp_path, [{"question": "q2", "gold": ["/c"]}])
    assert [(c["question"], c["gold"]) for c in later] == [("q1", ["/b"]), ("q2", ["/c"])]
    assert [(c["question"], c["gold"]) for c in sc._jsonl(tmp_path / sc.CASES_FILE)] == \
        [("q1", ["/b"]), ("q2", ["/c"])]


FAKE = '''
import os
from pathlib import Path
from types import SimpleNamespace
FALLBACK_FILES = 1
_STAGE = {}
STAGE_LIST_CAP = 10
prepare_bulk = SimpleNamespace(CACHE_DIR=Path("."))
ORDER = %r
def state_dir(principal):
    return Path(os.environ["SC_STATE"]) / principal
def my_pointers(principal):
    return ["p1"]
def word_search(question, pointers, limit=5):
    _STAGE["word"] = {"ranked": [(1.0, p, "p1") for p in ORDER]}
    return []
'''


def _build(tmp_path, name, order):
    d = tmp_path / name
    d.mkdir()
    (d / "ask.py").write_text(FAKE % (order,))
    return str(d / "ask.py")


def test_a_build_that_drops_a_read_case_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SC_STATE", str(tmp_path / "state"))
    cases = tmp_path / "cases.jsonl"
    _w(cases, [{"question": "q", "gold": ["/gold.md"], "principal": "me"}])
    old = _build(tmp_path, "old", ["/gold.md", "/other.md"])
    new = _build(tmp_path, "new", ["/other.md", "/gold.md"])
    rc = sc.main(["--principal", "me", "--no-harvest", "--cases", str(cases), "--ask", old, "--ask", new])
    out = capsys.readouterr().out
    assert rc == 1 and "1 lost" in out and "LOST  rank 1 -> 2" in out
    assert sc.main(["--principal", "me", "--no-harvest", "--cases", str(cases), "--ask", new, "--ask", old]) == 0


def test_a_question_with_two_recorded_files_counts_either(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SC_STATE", str(tmp_path / "state"))
    cases = tmp_path / "cases.jsonl"
    _w(cases, [{"question": "q", "gold": ["/a.md"], "principal": "me"},
               {"question": "q", "gold": ["/b.md"], "principal": "me"}])
    build = _build(tmp_path, "b", ["/b.md", "/a.md"])
    sc.main(["--principal", "me", "--no-harvest", "--cases", str(cases), "--ask", build])
    assert "1/1" in capsys.readouterr().out


def test_a_launcher_ask_py_grades_the_real_code_beside_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SC_STATE", str(tmp_path / "state"))
    cases = tmp_path / "cases.jsonl"
    _w(cases, [{"question": "q", "gold": ["/gold.md"], "principal": "me"}])
    d = Path(_build(tmp_path, "rel", ["/gold.md"])).parent
    (d / "ask.py").rename(d / "ask_impl.py")
    (d / "ask.py").write_text("raise SystemExit('the launcher must not run')\n")
    assert sc.main(["--principal", "me", "--no-harvest", "--cases", str(cases), "--ask", str(d / "ask.py")]) == 0
    assert "1/1" in capsys.readouterr().out


def test_a_file_that_is_not_an_ask_module_is_refused_by_name(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SC_STATE", str(tmp_path / "state"))
    other = tmp_path / "x" / "tool.py"
    other.parent.mkdir()
    other.write_text("X = 1\n")
    with pytest.raises(SystemExit) as e:
        sc.main(["--principal", "me", "--no-harvest", "--ask", str(other)])
    assert e.value.code == 2 and "no word_search" in capsys.readouterr().err
