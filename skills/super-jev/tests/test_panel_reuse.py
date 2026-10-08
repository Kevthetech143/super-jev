#!/usr/bin/env python3
"""A miss fetches the registry panel once, and the stale flag routing reads from it is unchanged.

    python3 -m pytest skills/super-jev/tests/test_panel_reuse.py -q
"""
import importlib.util
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_panel_reuse", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def _run(tmp_path, monkeypatch, panel):
    calls = []

    def fake_memory(req):
        calls.append(req["action"])
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return panel
        raise AssertionError(req)

    note = tmp_path / "notes" / "a.md"
    note.parent.mkdir(parents=True)
    note.write_text("the answer is here\n")
    sha = ask.sha256_file(note)
    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "candidate_files", lambda *a, **k: [("p1", str(note), {"sha256": sha})])
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {str(note): {"pass": True, "sha256": sha}})
    monkeypatch.setattr(ask.zoom, "run", lambda *a, **k: ([str(note)], [], {}))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({str(note): 0.9}, set(), None, {}))
    monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None: ("CLEAN", 0.93))
    ask.lookup("q", "alice", tmp_path / "state")
    return calls


def test_panel_is_fetched_once_per_ask(tmp_path, monkeypatch):
    calls = _run(tmp_path, monkeypatch, {"pointers": [{"pointer": "p1"}]})
    assert calls.count("panel") == 1


def test_stale_flag_still_comes_from_the_panel(tmp_path, monkeypatch, capsys):
    calls = _run(tmp_path, monkeypatch,
                       {"pointers": [{"pointer": "p1", "snapshotStatus": "refresh-required"}]})
    out = capsys.readouterr().out
    assert calls.count("panel") == 1
    assert "[p1] stale: searched as of its last refresh" in out


def test_fresh_pointer_is_not_flagged_stale(tmp_path, monkeypatch, capsys):
    _run(tmp_path, monkeypatch, {"pointers": [{"pointer": "p1", "snapshotStatus": "ready"}]})
    assert "stale" not in capsys.readouterr().out
