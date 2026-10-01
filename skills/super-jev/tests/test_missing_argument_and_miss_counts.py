#!/usr/bin/env python3
"""A missing argument, or a flag with no value, names itself instead of printing only the
usage or crashing; and the not-found summary's counts each say what they count.

    python3 -m pytest skills/super-jev/tests/test_missing_argument_and_miss_counts.py -q
"""
import importlib.util
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_missing_arg", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)
import connect_checked  # noqa: E402


def _run(monkeypatch, capsys, *argv, env=None):
    monkeypatch.setenv("SUPERJEV_AUTO_CACHE", "1")  # --no-auto sets this in os.environ; let the test undo it
    monkeypatch.delenv("SUPERJEV_PRINCIPAL", raising=False)
    if env:
        monkeypatch.setenv("SUPERJEV_PRINCIPAL", env)
    monkeypatch.setattr(sys, "argv", ["ask.py", *argv])
    rc = ask._main()
    out = capsys.readouterr()
    return rc, (out.out + out.err).splitlines()


def test_a_question_without_principal_names_principal(monkeypatch, capsys):
    rc, lines = _run(monkeypatch, capsys, "what is the hotel limit")
    assert rc == 2
    assert "--principal" in lines[0] and "required" in lines[0], lines[0]


def test_principal_without_a_value_is_named_not_a_traceback(monkeypatch, capsys):
    rc, lines = _run(monkeypatch, capsys, "--principal")
    assert rc == 2
    assert "--principal" in lines[0] and "required" in lines[0], lines[0]
    rc, lines = _run(monkeypatch, capsys, "--principal", env="alice")   # a typed flag is never replaced by the env var
    assert rc == 2
    assert "--principal" in lines[0] and "required" in lines[0], lines[0]


def test_a_missing_question_is_named(monkeypatch, capsys):
    for argv in (("--principal", "bob"), ("--principal", "bob", "--"), ("--principal", "bob", "--no-auto")):
        rc, lines = _run(monkeypatch, capsys, *argv)
        assert rc == 2, argv
        assert "question" in lines[0] and "required" in lines[0], (argv, lines[0])


def test_miss_without_a_question_is_named_not_a_traceback(monkeypatch, capsys):
    rc, lines = _run(monkeypatch, capsys, "--principal", "bob", "--miss")
    assert rc == 2
    assert "--miss" in lines[0] and "question" in lines[0], lines[0]


def test_no_arguments_still_prints_only_the_usage(monkeypatch, capsys):
    rc, lines = _run(monkeypatch, capsys)
    assert rc == 2 and lines and "required" not in lines[0]
    rc, lines = _run(monkeypatch, capsys, "--help")
    assert rc == 2 and lines and "required" not in lines[0]


def test_connect_checked_line_without_a_number_is_named(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["connect_checked.py", "request.json", "--line"])
    assert connect_checked.main() == 2
    out = capsys.readouterr()
    first = (out.out + out.err).splitlines()[0]
    assert "--line" in first and "number" in first, first


def test_miss_summary_counts_say_what_they_count():
    lines = ask.miss_report("bob", 3, {"p1": {"status": "no-candidates"}, "p2": {"status": "no-candidates"}},
                            {"/notes/policies/expense.md": {"score": 0.31}, "/notes/hr/leave.md": {"score": 0.12}})
    sets_line, read_line = lines[1], lines[2]
    assert "had matches" not in sets_line, sets_line
    assert "description" in sets_line, sets_line            # what the set count counts
    assert "2 file(s) read" in read_line and "description" in read_line and "words" in read_line, read_line
