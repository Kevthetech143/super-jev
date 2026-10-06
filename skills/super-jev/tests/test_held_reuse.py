"""A refresh does not pay again for a held file that has not changed: its earlier verdict stands, with the same
report rows. An edited one is judged once more, and --rejudge (the manual retry) judges it again. Made-up pear
notes, stub judge and writer, no network.

    python3 -m pytest skills/super-jev/tests/test_held_reuse.py -q
"""
import json
import sys

import pytest

from test_partial_ok import _claude_cli_present, _set, pb, base_argv, fake_connect_memory  # noqa: F401


def _refresh(tmp_path, monkeypatch, low="orchard1", extra=()):
    judged, wrote = [], []
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")

    def gate(claim, path):
        judged.append(path)
        return {"state": "SUPPORTED", "confidence": 0.35 if low in path else 0.95}
    inner = pb.writer
    monkeypatch.setattr(pb, "gate", gate)
    monkeypatch.setattr(pb, "gate_many", lambda claims, path: None)
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: wrote.extend(items) or inner(items, model, feedback=feedback))
    monkeypatch.setattr(pb, "memory", fake_connect_memory([]))
    monkeypatch.setattr(sys, "argv", base_argv(tmp_path / "root", extra=["--no-findability", "--refresh", *extra]))
    return pb.main(), judged, wrote


def _first_run(tmp_path, monkeypatch):
    fs = _set(tmp_path, monkeypatch)
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")
    monkeypatch.setattr(pb, "gate", lambda c, p: {"state": "SUPPORTED", "confidence": 0.35 if "orchard1" in p else 0.95})
    monkeypatch.setattr(pb, "gate_many", lambda claims, path: None)
    monkeypatch.setattr(pb, "memory", fake_connect_memory([]))
    monkeypatch.setattr(sys, "argv", base_argv(tmp_path / "root", extra=["--no-findability"]))
    assert pb.main() == 3
    return fs


def _held_rows(tmp_path):
    rep = json.loads((pb.CACHE_DIR / "my-records-report.json").read_text())
    return rep, rep["exceptions"]


def test_an_unchanged_held_file_costs_no_call_and_stays_held(tmp_path, monkeypatch, capsys):
    _first_run(tmp_path, monkeypatch)
    before = _held_rows(tmp_path)[1]
    rc, judged, wrote = _refresh(tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert rc == 3 and "CONNECTED 3, HELD 2, FAILED 0" in out
    assert not any("orchard1" in p for p in judged) and not any("orchard1" in str(w) for w in wrote)
    rep, rows = _held_rows(tmp_path)
    assert rows == before and len(rep["approved"]) == 3
    assert list(rep["heldSha"]) == [r[0] for r in rows]


def test_an_edited_held_file_is_judged_once(tmp_path, monkeypatch):
    fs = _first_run(tmp_path, monkeypatch)
    fs[1].write_text("# Orchard 1\nNew facts about plums.\n")
    rc, judged, _ = _refresh(tmp_path, monkeypatch)
    assert rc == 3 and {p for p in judged if "orchard1" in p} == {str(fs[1])}  # judged (a low verdict gets one rewrite check)
    assert {p for p in judged} == {str(fs[1])}  # and nothing else was


def test_rejudge_judges_an_unchanged_held_file_again(tmp_path, monkeypatch):
    fs = _first_run(tmp_path, monkeypatch)
    rc, judged, _ = _refresh(tmp_path, monkeypatch, extra=["--rejudge"])
    assert rc == 3 and {p for p in judged} == {str(fs[1])}


def test_the_rerun_hint_of_a_refresh_carries_rejudge(tmp_path, monkeypatch, capsys):
    _first_run(tmp_path, monkeypatch)
    capsys.readouterr()
    _refresh(tmp_path, monkeypatch)
    assert "--refresh --rejudge" in capsys.readouterr().out
