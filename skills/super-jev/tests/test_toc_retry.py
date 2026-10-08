#!/usr/bin/env python3
"""A crashed table-of-contents (TOC) stage is retried once, and a not-found that follows a TOC
that never ran says so instead of reading as a clean "not found". A payment refusal (HTTP 402) is
not retried. The real TOC search runs; only the judge is a stub. No network.

    python3 -m pytest skills/super-jev/tests/test_toc_retry.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_stale_set_answers as base  # noqa: E402

ask = base.ask
REAL_TOC_RUN = ask.zoom.run
pytestmark = pytest.mark.real_toc
Q = "have we already tried the cache warmer?"


def _stub_judge(monkeypatch, failures, error=RuntimeError("judge hiccup")):
    """judges.ask raises `failures` times (None: always), then scores every TOC page as a likely hit."""
    calls = []

    def fake(state, qs, timeout=90):
        calls.append(1)
        if failures is None or len(calls) <= failures:
            raise error
        return {"answers": {k: {"probabilities": {"LIKELY": 0.9}} for k in state["items"]}}

    monkeypatch.setattr(ask.toc_search.judges, "ask", fake)
    return calls


def _lookup(tmp_path, monkeypatch, capsys, confirm_score, word_hit=False):
    tried = base._setup(tmp_path, monkeypatch)
    monkeypatch.setattr(ask.zoom, "run", REAL_TOC_RUN)
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({p: confirm_score for p in ps}, set(), None, {}))
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [(0.9, str(tried[0]), "notes")] if word_hit else [])
    rc = ask.lookup(Q, "primary", tmp_path / "s")
    return rc, capsys.readouterr().out


def test_a_toc_crash_is_retried_once_and_the_retry_answers_normally(tmp_path, monkeypatch, capsys):
    calls = _stub_judge(monkeypatch, failures=1)
    rc, out = _lookup(tmp_path, monkeypatch, capsys, 0.95)
    assert len(calls) == 2 and rc == 0
    assert out.startswith("OUTCOME: found") and "zoom failed" not in out
    assert ask._STAGE["toc"].get("retried") is True and "error" not in ask._STAGE["toc"]


def test_a_toc_that_always_crashes_never_reads_as_a_clean_not_found(tmp_path, monkeypatch, capsys):
    calls = _stub_judge(monkeypatch, failures=None)
    rc, out = _lookup(tmp_path, monkeypatch, capsys, 0.0)
    assert len(calls) == 2  # one try, one retry
    assert "zoom failed: RuntimeError: judge hiccup; read list from word search only" in out and "may be a miss" in out
    assert not out.startswith("OUTCOME: not-found")
    assert rc == ask.OUTCOME_EXIT["error"]  # the same exit the content-check failure already uses


def test_a_failed_toc_with_confirmed_files_answers_as_usual_with_a_trace_note(tmp_path, monkeypatch, capsys):
    _stub_judge(monkeypatch, failures=None)
    rc, out = _lookup(tmp_path, monkeypatch, capsys, 0.95, word_hit=True)
    assert rc == 0 and out.startswith("OUTCOME: found")
    assert "zoom failed: RuntimeError: judge hiccup; read list from word search only" in out.splitlines()[0]
    assert ask._STAGE["toc"]["error"].startswith("RuntimeError") and ask._STAGE["toc"]["note"]


@pytest.mark.parametrize("error", [RuntimeError("judge said HTTP 402: no balance"), ask.judges.TooBig("too big")])
def test_a_payment_or_size_refusal_is_not_retried(tmp_path, monkeypatch, capsys, error):
    calls = _stub_judge(monkeypatch, failures=None, error=error)
    _lookup(tmp_path, monkeypatch, capsys, 0.0)
    assert len(calls) == 1
