#!/usr/bin/env python3
"""Offline tests for the auto-catch feature (PLAN.md "Tests (must ship in the PR)").

No network, no live harness, no live Jev call: the check/approve/add doors are
injected stubs, the state dir is an isolated tmp_path, and ask.memory is
monkeypatched where ask.py itself is exercised.

    python3 -m pytest skills/super-jev/tests/test_auto_catch.py -q
"""
import importlib.util
import json
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SKILL / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


auto_catch = _load("auto_catch")
ask = _load("ask")

GATE_PASS = """  c1   SUPPORTED   0.95  the sky is blue
  overclaim         HONEST           0.99
  time_sensitive    NOT_TIME_SENSITIVE  0.95
  leaked_internal   CLEAN            1.00
"""
GATE_OVERCLAIM = """  c1   SUPPORTED   0.92  the sky is blue
  overclaim         OVERCLAIMS       1.00   -> SOFTEN IT
  time_sensitive    NOT_TIME_SENSITIVE  0.95
  leaked_internal   CLEAN            1.00
"""

FILE_TEXT = "The sky appears blue at noon.\nThis is a reviewed fact about color.\nNothing else is claimed here.\n"
GOOD_ANSWER = 'The answer is "The sky appears blue at noon." according to the file, obviously.'
QUESTION = "what color is the sky at noon?"


class Doors:
    """Injected stand-ins for the check/approve/add doors; every call is counted."""

    def __init__(self, gate_text):
        self.gate_text = gate_text
        self.check_calls = []
        self.approve_calls = []
        self.add_calls = []

    def check(self, claim, path):
        self.check_calls.append((claim, path))
        return auto_catch.parse_flags(self.gate_text)

    def approve(self, question, quotes):
        self.approve_calls.append((question, quotes))
        return 0

    def add(self, question, quotes, source):
        self.add_calls.append((question, quotes, source))
        return 0


def _receipt(tmp_path, principal="w1", cands=(("/ev/top.txt", "p1", 0.90), ("/ev/other.txt", "p2", 0.50))):
    auto_catch.set_enabled(tmp_path, True)
    auto_catch.write_receipt(tmp_path, principal, QUESTION,
                             [{"path": p, "pointer": ptr, "score": s} for p, ptr, s in cands])
    return principal


def _evidence(tmp_path, name="top.txt", text=FILE_TEXT):
    d = tmp_path / "ev"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_text(text)
    return str(f)


def _log_decisions(tmp_path):
    p = tmp_path / "auto-catch.log"
    if not p.is_file():
        return []
    return [json.loads(line)["decision"] for line in p.read_text().splitlines()]


def test_known_good_approves_both_lanes_with_file_quotes(tmp_path):
    ev = _evidence(tmp_path)
    principal = _receipt(tmp_path, cands=((ev, "p1", 0.90), ("/ev/other.txt", "p2", 0.50)))
    auto_catch.mark_opened(tmp_path, principal, ev)
    doors = Doors(GATE_PASS)
    rc = auto_catch.do_done(GOOD_ANSWER, principal, tmp_path,
                            check_fn=doors.check, approve_fn=doors.approve, add_fn=doors.add)
    assert rc == 0
    assert len(doors.check_calls) == 1      # exactly one Jev call per --done
    assert len(doors.approve_calls) == 1    # --approve lane
    assert len(doors.add_calls) == 1        # --add lane
    q, quotes = doors.approve_calls[0]
    assert q == QUESTION
    for line in quotes.splitlines():       # cached content is verbatim file lines...
        assert line in FILE_TEXT
    assert GOOD_ANSWER not in quotes        # ...never the agent's prose
    q2, quotes2, source = doors.add_calls[0]
    assert source == ev                     # --source <file>: hash freshness applies
    assert _log_decisions(tmp_path) == ["approved"]
    assert auto_catch.load_receipt(tmp_path, principal)["done"] == "approved"


def test_known_bad_1_close_call_gap_caches_nothing(tmp_path):
    ev = _evidence(tmp_path)
    principal = _receipt(tmp_path, cands=((ev, "p1", 0.75), ("/ev/other.txt", "p2", 0.70)))
    doors = Doors(GATE_PASS)
    rc = auto_catch.do_done(GOOD_ANSWER, principal, tmp_path,
                            check_fn=doors.check, approve_fn=doors.approve, add_fn=doors.add)
    assert rc == 0
    assert doors.check_calls == [] and doors.approve_calls == [] and doors.add_calls == []
    assert _log_decisions(tmp_path) == ["close-call"]


def test_known_bad_2_overclaim_goes_to_pending_catch(tmp_path):
    ev = _evidence(tmp_path)
    principal = _receipt(tmp_path, cands=((ev, "p1", 0.90), ("/ev/other.txt", "p2", 0.10)))
    doors = Doors(GATE_OVERCLAIM)
    rc = auto_catch.do_done(GOOD_ANSWER, principal, tmp_path,
                            check_fn=doors.check, approve_fn=doors.approve, add_fn=doors.add)
    assert rc == 0
    assert len(doors.check_calls) == 1     # the check still ran once
    assert doors.approve_calls == [] and doors.add_calls == []   # nothing cached
    fb = tmp_path / "auto-catch-feedback.jsonl"
    rows = [json.loads(line) for line in fb.read_text().splitlines()]
    assert rows and rows[-1]["kind"] == "pending-catch" and "overclaim" in rows[-1]["reason"]
    assert _log_decisions(tmp_path) == ["pending-catch"]


def test_known_bad_3_stale_source_withheld_no_reuse(tmp_path):
    # the --add lane records source_path+source_sha256 (asserted in the good case);
    # print_hit withholds via changed_source when the file moved under it.
    ev = _evidence(tmp_path)
    doors = Doors(GATE_PASS)
    principal = _receipt(tmp_path, cands=((ev, "p1", 0.90), ("/ev/other.txt", "p2", 0.10)))
    auto_catch.do_done(GOOD_ANSWER, principal, tmp_path,
                       check_fn=doors.check, approve_fn=doors.approve, add_fn=doors.add)
    _, _, source = doors.add_calls[0]
    assert source == ev
    record = tmp_path / "manual" / "r.md"
    record.parent.mkdir(exist_ok=True)
    record.write_text("source_path: %s\nsource_sha256: %s\n" % (ev, ask.sha256_file(Path(ev))))
    Path(ev).write_text(FILE_TEXT + "Edited after approve.\n")   # source edited after approve
    assert ask.changed_source(record) == ev                       # next ask withholds
    assert _log_decisions(tmp_path) == ["approved"]               # no new reuse logged


def test_switch_off_no_receipt_no_check_calls(tmp_path, monkeypatch, capsys):
    # switch untouched (default off): --done is a quiet no-op...
    doors = Doors(GATE_PASS)
    rc = auto_catch.do_done(GOOD_ANSWER, "w1", tmp_path,
                            check_fn=doors.check, approve_fn=doors.approve, add_fn=doors.add)
    assert rc == 0
    assert doors.check_calls == [] and doors.approve_calls == [] and doors.add_calls == []
    assert not (tmp_path / "receipts").exists() and not (tmp_path / "auto-catch.log").exists()
    assert capsys.readouterr().out == ""
    # ...and ask() writes no receipt and prints nothing extra.

    def fake_memory(req):
        assert req["action"] in ("cached", "panel")
        return {"status": "cache-miss"} if req["action"] == "cached" else {"pointers": []}

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup("some question", "w1", tmp_path)
    assert rc == 0
    assert not (tmp_path / "receipts").exists()
    assert "auto-catch" not in capsys.readouterr().out.lower()


def test_missing_or_stale_receipt_logs_not_caught(tmp_path):
    auto_catch.set_enabled(tmp_path, True)
    doors = Doors(GATE_PASS)
    assert auto_catch.do_done(GOOD_ANSWER, "w1", tmp_path,
                              check_fn=doors.check, approve_fn=doors.approve, add_fn=doors.add) == 0
    assert doors.check_calls == []
    stale = {"question": QUESTION, "attemptId": None, "ts": time.time() - 3600,
             "candidates": [{"path": "/ev/x.txt", "pointer": "p", "score": 0.9}], "opened": []}
    rdir = tmp_path / "receipts"
    rdir.mkdir(exist_ok=True)
    (rdir / "w1.json").write_text(json.dumps(stale))
    assert auto_catch.do_done(GOOD_ANSWER, "w1", tmp_path,
                              check_fn=doors.check, approve_fn=doors.approve, add_fn=doors.add) == 0
    assert doors.check_calls == []
    assert _log_decisions(tmp_path) == ["not-caught", "not-caught"]
