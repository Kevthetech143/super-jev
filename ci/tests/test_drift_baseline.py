"""Tests for the judge-drift baseline harness (ci/drift_baseline.py and the
--baseline mode of ci/compare_runs.py).

All fixtures are synthetic decisions.tsv files written into tmp dirs; no
recorded fleet payload text is used.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import compare_runs  # noqa: E402
import drift_baseline  # noqa: E402


def write_run(bench, tag, rows):
    run_dir = os.path.join(str(bench), "runs", tag)
    os.makedirs(run_dir)
    with open(os.path.join(run_dir, "decisions.tsv"), "w",
              encoding="utf-8") as fh:
        for row in rows:
            fh.write("\t".join(row) + "\n")


def test_baseline_builds_drift_set(tmp_path):
    # Two runs of the SAME code: c1 and c2 flip, c3 is stable.
    write_run(tmp_path, "a", [
        ["c1", "the answer is 42", "true"],
        ["c2", "the answer is 42", "true"],
        ["c3", "the answer is 42", "true"],
    ])
    write_run(tmp_path, "b", [
        ["c1", "the answer is 42", "false"],
        ["c2", "the answer is 7", "false"],
        ["c3", "the answer is 42", "true"],
    ])
    baseline = drift_baseline.build_baseline(str(tmp_path), "a", "b")
    assert baseline["bench"] == str(tmp_path)
    assert baseline["tags"] == ["a", "b"]
    assert baseline["drift_ids"] == ["c1", "c2"]
    # Each drift case is true in one run and false in the other.
    assert baseline["truths_blocked"][0] + baseline["lies_allowed"][0] == 2
    assert baseline["truths_blocked"][1] + baseline["lies_allowed"][1] == 2
    assert baseline["truths_blocked"] == [0, 2]
    assert baseline["lies_allowed"] == [2, 0]
    assert baseline["only_in_a"] == []
    assert baseline["only_in_b"] == []


def test_baseline_writes_json_and_returns_zero(tmp_path, capsys, monkeypatch):
    write_run(tmp_path, "a", [["c1", "v", "true"]])
    write_run(tmp_path, "b", [["c1", "v", "false"]])
    monkeypatch.chdir(tmp_path)
    rc = drift_baseline.main(
        [str(tmp_path), "a", "b", "--out", "base.json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1  # one-line summary
    with open("base.json", encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["drift_ids"] == ["c1"]
    assert data["tags"] == ["a", "b"]


def test_compare_with_baseline_marks_known_drift(tmp_path, capsys):
    # Baseline built from same-code runs a/b: c1 flips (known drift).
    write_run(tmp_path, "a", [
        ["c1", "v1", "true"],
        ["c2", "v2", "true"],
    ])
    write_run(tmp_path, "b", [
        ["c1", "v1", "false"],
        ["c2", "v2", "true"],
    ])
    baseline_path = str(tmp_path / "base.json")
    drift_baseline.main([str(tmp_path), "a", "b", "--out", baseline_path])

    # A new-code pair c/d: c1 flips again (known drift), c2 flips with a
    # changed verdict (a real window change).
    write_run(tmp_path, "c", [
        ["c1", "v1", "true"],
        ["c2", "v2", "true"],
    ])
    write_run(tmp_path, "d", [
        ["c1", "v1", "false"],
        ["c2", "v2 CHANGED", "false"],
    ])
    rc = compare_runs.main(
        [str(tmp_path), "c", "d", "--baseline", baseline_path])
    assert rc == 0
    lines = capsys.readouterr().out.splitlines()
    assert any(l.startswith("KNOWN DRIFT") and "c1" in l for l in lines)
    # The leading number is the counted flips (c2 only); c1 is excluded.
    summary = lines[-1]
    assert summary.count("\n") == 0
    assert "1 flip(s) counted" in summary
    assert "2 total" in summary
    assert "1 WINDOW CHANGE" in summary
    assert "0 DRIFT" in summary
    assert "1 known drift excluded" in summary


def test_unknown_flip_still_counts(tmp_path, capsys):
    # Baseline built from a/b: c1 flips (known drift).
    write_run(tmp_path, "a", [
        ["c1", "v1", "true"],
        ["c2", "v2", "true"],
    ])
    write_run(tmp_path, "b", [
        ["c1", "v1", "false"],
        ["c2", "v2", "true"],
    ])
    baseline_path = str(tmp_path / "base.json")
    drift_baseline.main([str(tmp_path), "a", "b", "--out", baseline_path])

    # A new-code pair c/d: c2 flips (NOT in the drift set) -> still counted,
    # under its own label, with --baseline active.
    write_run(tmp_path, "c", [
        ["c2", "v2", "true"],
    ])
    write_run(tmp_path, "d", [
        ["c2", "v2 NEW", "false"],
    ])
    rc = compare_runs.main(
        [str(tmp_path), "c", "d", "--baseline", baseline_path])
    assert rc == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert not any(l.startswith("KNOWN DRIFT") for l in lines)
    assert "WINDOW CHANGE" in out
    assert "1 flip(s) counted" in out
    assert "1 DRIFT, 1 WINDOW CHANGE" not in out  # only one flip here
    assert "0 DRIFT, 1 WINDOW CHANGE" in out
    assert "1 known drift excluded" not in out  # nothing excluded


def test_window_change_in_drift_set_still_counts(tmp_path, capsys):
    # Baseline from same-code a/b: c1 and c3 flip with identical verdicts.
    write_run(tmp_path, "a", [
        ["c1", "v1", "true"],
        ["c3", "v3", "true"],
    ])
    write_run(tmp_path, "b", [
        ["c1", "v1", "false"],
        ["c3", "v3", "false"],
    ])
    baseline_path = str(tmp_path / "base.json")
    drift_baseline.main([str(tmp_path), "a", "b", "--out", baseline_path])

    # New-code pair c/d:
    #   c1 flips with a CHANGED verdict  -> WINDOW CHANGE, stays counted
    #       (it is also in the drift set, but that must not hide it);
    #   c2 flips with the same verdict   -> DRIFT, not in the drift set;
    #   c3 flips with the same verdict   -> DRIFT and in the drift set,
    #       so KNOWN DRIFT, excluded.
    write_run(tmp_path, "c", [
        ["c1", "v1", "true"],
        ["c2", "v2", "true"],
        ["c3", "v3", "true"],
    ])
    write_run(tmp_path, "d", [
        ["c1", "v1 CHANGED", "false"],
        ["c2", "v2", "false"],
        ["c3", "v3", "false"],
    ])
    rc = compare_runs.main(
        [str(tmp_path), "c", "d", "--baseline", baseline_path])
    assert rc == 0
    lines = capsys.readouterr().out.splitlines()
    assert any("WINDOW CHANGE" in l and "c1" in l
               and "(case is also in the drift set)" in l for l in lines)
    assert any(l.startswith("DRIFT") and "c2" in l for l in lines)
    assert any(l.startswith("KNOWN DRIFT") and "c3" in l for l in lines)
    summary = lines[-1]
    assert "2 flip(s) counted" in summary
    assert "3 total" in summary
    assert "1 known drift excluded" in summary
    assert "1 DRIFT, 1 WINDOW CHANGE" in summary


def test_normalized_decisions_are_not_flips(tmp_path):
    # Case/synonym differences in the decision string are not flips.
    cases_a = {
        "c1": ("v", "TRUE"),   # case difference
        "c2": ("v", "yes"),    # synonym difference
        "c3": ("v", "true"),
        "c4": ("v", "Pass"),
    }
    cases_b = {
        "c1": ("v", "true"),
        "c2": ("v", "true"),
        "c3": ("v", "false"),  # a genuine flip
        "c4": ("v", "no"),     # a genuine flip
    }
    flips = compare_runs.classify_flips(cases_a, cases_b)
    assert flips == [("c3", "DRIFT"), ("c4", "DRIFT")]


def test_missing_decisions_exits_one(tmp_path, capsys):
    rc = compare_runs.main([str(tmp_path), "nope", "alsono"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert captured.err.count("\n") == 1  # single-line error
    assert "decisions.tsv" in captured.err
    assert str(tmp_path) in captured.err


def test_malformed_row_exits_one(tmp_path, capsys):
    write_run(tmp_path, "a", [["c1", "v1"]])  # only 2 columns
    write_run(tmp_path, "b", [["c1", "v1", "true"]])
    rc = compare_runs.main([str(tmp_path), "a", "b"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.err.count("\n") == 1
    assert "decisions.tsv" in captured.err
    assert ":1:" in captured.err  # row number named


def test_missing_decisions_baseline_exits_one(tmp_path, capsys):
    rc = drift_baseline.main([str(tmp_path), "nope", "alsono", "--out",
                              str(tmp_path / "base.json")])
    assert rc == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.err.count("\n") == 1
    assert "decisions.tsv" in captured.err


def test_only_in_one_run_reported(tmp_path, capsys):
    # c9 only in a, c10 only in b: reported, not silently ignored.
    write_run(tmp_path, "a", [
        ["c1", "v", "true"],
        ["c9", "v", "true"],
    ])
    write_run(tmp_path, "b", [
        ["c1", "v", "false"],
        ["c10", "v", "true"],
    ])
    rc = compare_runs.main([str(tmp_path), "a", "b"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "present in only one run" in out
    assert "c9" in out and "c10" in out
    assert "1 flip(s) counted" in out  # flips unaffected by one-run cases

    rc = drift_baseline.main([str(tmp_path), "a", "b", "--out",
                              str(tmp_path / "base.json")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "present in only one run" in out
    assert "c9" in out and "c10" in out
    assert out.count("\n") == 2  # summary line + one-run line
    with open(str(tmp_path / "base.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["only_in_a"] == ["c9"]
    assert data["only_in_b"] == ["c10"]
    assert data["drift_ids"] == ["c1"]


def test_diff_flag_shows_case_detail(tmp_path, capsys):
    write_run(tmp_path, "a", [["c1", "the answer is 42", "true"]])
    write_run(tmp_path, "b", [["c1", "the answer is 42", "false"]])
    rc = compare_runs.main([str(tmp_path), "a", "b", "--diff", "c1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "label: DRIFT" in out
    assert "decision=true" in out and "decision=false" in out
