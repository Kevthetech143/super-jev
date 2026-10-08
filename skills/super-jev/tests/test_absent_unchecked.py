"""Frozen (2026-10-07): when no read file's content check passed, the ask is never "found".

Contract (superjev-source-contract.md, Division of work): "'Possible' means inspect, not verified
truth." A file whose content check failed or did not finish was not read, so it can be listed as
an unchecked/possible file to inspect, but the outcome is an honest not-found that says files were
not checked. Made-up files, fake judge; no network, no paid calls.

    python3 -m pytest skills/super-jev/tests/test_absent_unchecked.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ask  # noqa: E402


@pytest.fixture(autouse=True)
def clean():
    ask._STAGE.clear()
    ask._RESULT.clear()
    yield
    ask._STAGE.clear()
    ask._RESULT.clear()


def _setup(tmp_path, monkeypatch, routing, scores, notes, check_error):
    paths = {}
    for name in routing:
        p = tmp_path / name
        p.write_text(f"# {name}\nnotes about the Lindqvist bakery opening hours\n")
        paths[name] = str(p)
    monkeypatch.setitem(ask._CLAIM, "text", None)
    monkeypatch.setattr(ask, "load_cache_files", lambda *a: {})
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    monkeypatch.setattr(ask, "pointer_words", lambda *a: ({}, []))
    monkeypatch.setattr(ask, "batch_jev", lambda: False)
    monkeypatch.setattr(ask, "skipped_for_question", lambda *a: [])
    monkeypatch.setattr(ask, "memory", lambda r:
                        {"status": "miss"} if r["action"] == "cached" else
                        {"pointers": ["p1"]} if r["action"] == "panel" else
                        {"status": "candidates",
                         "candidates": [{"score": s, "originalPath": paths[n]} for n, s in routing.items()]})
    monkeypatch.setattr(ask, "confirm", lambda q, ps: (
        {paths[n]: s for n, s in scores.items()}, set(), check_error,
        {paths[n]: v for n, v in notes.items()}))
    return paths


def _ask(tmp_path, capsys):
    rc = ask.lookup("which note says the Lindqvist bakery's Sunday hours", "me", tmp_path / "state")
    return rc, capsys.readouterr().out


@pytest.mark.parametrize("check_error", ["judge unreachable", None])
def test_only_unchecked_file_is_an_honest_not_found(tmp_path, monkeypatch, capsys, check_error):
    paths = _setup(tmp_path, monkeypatch, {"menu.md": 0.9}, {}, {"menu.md": ask.INCONCLUSIVE}, check_error)
    rc, out = _ask(tmp_path, capsys)
    want = "error" if check_error else "not-found"  # a failed check is an execution error, named
    assert ask._RESULT["outcome"] == want and rc == ask.OUTCOME_EXIT[want]
    assert "OUTCOME: found" not in out
    assert ("content check failed" if check_error else "not checked") in ask._RESULT["why"]  # never a complete no-match
    assert "file(s) read" not in out  # the miss report must not call an unread file read
    rows = ask._RESULT.get("files") or []
    assert [r["path"] for r in rows] == [paths["menu.md"]] and rows[0]["tier"] == "unchecked"


def test_a_passing_file_beside_an_unchecked_one_is_still_found(tmp_path, monkeypatch, capsys):
    paths = _setup(tmp_path, monkeypatch, {"menu.md": 0.9, "hours.md": 0.5}, {"hours.md": 0.91},
                   {"menu.md": ask.INCONCLUSIVE}, "judge unreachable")
    rc, out = _ask(tmp_path, capsys)
    assert ask._RESULT["outcome"] == "found" and rc == 0
    assert ask._RESULT["files"][0]["path"] == paths["hours.md"]
