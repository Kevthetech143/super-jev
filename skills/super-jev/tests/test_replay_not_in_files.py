#!/usr/bin/env python3
"""Paid replay grades not-in-files cases: an absent question (nothing in the files answers it) and
an ABSENT claim (no file settles it). Against fake ask builds: no Jev, no network.

    python3 -m pytest skills/super-jev/tests/test_replay_not_in_files.py -q
"""
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
import scorecard as sc  # noqa: E402
from test_paid_replay import TOP, _build, _cases, _log, _run, env  # noqa: E402,F401
from test_scorecard import _build as _sc_build, _w  # noqa: E402

ABSENT_Q = {"question": "Where is the Quillbrook parking policy?", "absent": True, "gold": []}
ABSENT_C = {"kind": "claim", "expected": "ABSENT", "gold": []}


def _rows(capsys):
    return {r["question"]: r for r in json.loads(capsys.readouterr().out)["rows"]}


def test_an_absent_question_is_ok_only_when_the_final_ranking_is_empty(tmp_path, env, capsys):
    q = ABSENT_Q["question"]
    cases = _cases(tmp_path, [ABSENT_Q])
    # Whatever the exit: 1 (nothing matched) and 4 (nothing matched, search incomplete) are both fine.
    for rc in (1, 4):
        old = {q: {"final": [], "rc": rc}}
        new = {q: {"final": [(0.95, "/a.md")], "rc": 0}}
        assert _run(tmp_path, cases, old, new) == 1  # old held, new returned a file: a loss
        row = _rows(capsys)[q]
        assert (row["old"]["ok"], row["new"]["ok"], row["result"]) == (True, False, "lost")
        assert row["old"]["rank"] is None and row["new"]["rank"] is None


def test_an_absent_claim_is_ok_only_when_the_verdict_is_held(tmp_path, env, capsys):
    held = ["NOT FOUND", "PARTIAL (0.5)", "CONFLICT (0.7)", "UNSURE: read these files", None]
    asserted = ["TRUE (0.95)", "FALSE (0.93)"]
    rows = [{**ABSENT_C, "question": f"Quillbrook claim {i}"} for i in range(len(held) + len(asserted))]
    cases = _cases(tmp_path, rows)
    verdicts = held + asserted
    new = {r["question"]: ({"final": TOP, "verdict": v} if v else {"final": TOP}) for r, v in zip(rows, verdicts)}
    _run(tmp_path, cases, {}, new)
    got = [r["new"]["ok"] for r in json.loads(capsys.readouterr().out)["rows"]]
    assert got == [True] * len(held) + [False] * len(asserted)


def test_a_claim_that_errors_or_times_out_is_not_graded_as_asserted(tmp_path, env, capsys):
    cases = _cases(tmp_path, [{**ABSENT_C, "question": "Quillbrook claim"}])
    _run(tmp_path, cases, {}, {"Quillbrook claim": {"sleep": 5}}, "--timeout", "1")
    row = json.loads(capsys.readouterr().out)["rows"][0]
    assert "timeout" in row["new"]["error"] and "ok" not in row["new"]


def test_the_not_in_files_margins_keep_the_existing_sign_convention(tmp_path, env, capsys):
    near = [{**ABSENT_Q, "question": "near"}, {**ABSENT_C, "question": "claim ok"},
            {**ABSENT_C, "question": "claim bad"}, {**ABSENT_Q, "question": "bad"}]
    cases = _cases(tmp_path, near)
    new = {"near": {"final": [], "checked": {"/x.md": 0.45}},   # ok; best checked 0.45 under the 0.6 floor
           "claim ok": {"final": TOP, "verdict": "PARTIAL (0.5)"},
           "claim bad": {"final": TOP, "verdict": "TRUE (0.95)"},
           "bad": {"final": [(0.95, "/a.md")]}}
    _run(tmp_path, cases, {}, new)
    got = {q: (r["new"]["ok"], r["new"]["margin"]) for q, r in _rows(capsys).items()}
    assert got["near"] == (True, pytest.approx(0.15))            # floor minus best content-checked score
    assert got["claim ok"] == (True, pytest.approx(0.4))         # sure line minus its probability
    assert got["claim bad"] == (False, pytest.approx(-0.05))     # over the sure line: negative
    assert got["bad"] == (False, pytest.approx(-0.35))           # a ranked file over the floor: negative
    # An ok margin is never negative, even when a checked file scored above the floor.
    _run(tmp_path, _cases(tmp_path, [{**ABSENT_Q, "question": "high"}]), {}, {"high": {"final": [], "checked": {"/x.md": 0.8}}})
    assert _rows(capsys)["high"]["new"]["margin"] == 0


def test_empty_gold_is_accepted_only_on_an_absent_question_or_an_absent_claim(tmp_path, env, capsys):
    ok = [ABSENT_Q, {**ABSENT_C, "question": "Quillbrook claim"}]
    assert _run(tmp_path, _cases(tmp_path, ok), {}, {}) == 0
    capsys.readouterr()
    refused = [
        {"kind": "claim", "expected": "TRUE", "gold": [], "question": "sam's claim"},
        {"kind": "claim", "expected": "FALSE", "gold": [], "question": "sam's other claim"},
        {"kind": "claim", "expected": "TRUE", "absent": True, "gold": [], "question": "sam's flagged claim"},
        {"question": "an ordinary question with no gold", "gold": []},
        {"question": "absent but names a file", "absent": True, "gold": ["/a.md"]},
    ]
    for row in refused:
        with pytest.raises(SystemExit) as e:
            _run(tmp_path, _cases(tmp_path, [row]), {}, {})
        assert e.value.code == 2, row
        assert "gold" in capsys.readouterr().err


def test_the_scorecard_still_excludes_absent_rows_and_still_refuses_to_freeze_them(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SC_STATE", str(tmp_path / "state"))
    f = tmp_path / "cases.jsonl"
    _w(f, [{**ABSENT_Q, "principal": "me"}, {"question": "q", "gold": ["/held.md"], "principal": "me"}])
    build = _sc_build(tmp_path, "build", ["/held.md"])
    assert sc.main(["--principal", "me", "--no-harvest", "--cases", str(f), "--ask", build, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["cases"] == 1 and report["excluded_no_gold"] == 1
    frozen = tmp_path / "frozen.json"
    held = tmp_path / "held.jsonl"
    _w(held, [{**ABSENT_Q, "principal": "me", "split": "held-out", "group": "g", "source_family": "f"}])
    with pytest.raises(SystemExit) as e:
        sc.main(["--principal", "me", "--cases", str(held), "--freeze-held-out", str(frozen)])
    assert e.value.code == 2 and not frozen.exists()
