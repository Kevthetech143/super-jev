#!/usr/bin/env python3
"""Offline tests for ask.py --followup (the miss follow-up queue): pending misses
are derived from lookups.jsonl (no new state file), a confident re-try is only
ever PROPOSED (never auto-approved), and a miss that stays unconfirmed for
--max-tries retries is dropped. `memory` is monkeypatched at the module level;
no network, no live harness call.

    python3 -m pytest skills/super-jev/tests/test_followup.py -q
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "ask.py"

spec = importlib.util.spec_from_file_location("ask", SCRIPT)
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


@pytest.fixture(autouse=True)
def no_content_check(monkeypatch):
    """Same stub test_ask.py uses: pass every routed file through with its
    routing score so a lookup's outcome only depends on what `navigate` (via
    `memory`) returns, not the live content-check call."""
    monkeypatch.setattr(ask, "confirm", lambda question, paths: ({}, set(paths), None, {}))


def confident_memory(question="where is it?", path="/found.md", pointer="p1"):
    """A memory stub whose one pointer confidently finds `question` on retry."""
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": pointer}]}
        if req["action"] == "navigate":
            return {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": path}]}
        raise AssertionError(f"unexpected action for a followup retry: {req['action']}")
    return fake_memory


def still_missing_memory():
    """A memory stub whose pointers never find anything -- the miss stays a miss."""
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1"}]}
        if req["action"] == "navigate":
            return {"status": "no-candidates", "candidates": []}
        raise AssertionError(f"unexpected action: {req['action']}")
    return fake_memory


def test_pending_misses_is_empty_with_no_log(tmp_path):
    assert ask.pending_misses(tmp_path) == {}


def test_a_miss_is_pending_until_approved(tmp_path):
    ask.log(tmp_path, "miss", question="q", actual="the wiki")
    pending = ask.pending_misses(tmp_path)
    assert "q" in pending
    assert pending["q"]["actual"] == "the wiki"
    assert pending["q"]["tries"] == 0

    ask.log(tmp_path, "approve", question="q", pointer="p1", result="saved")
    assert ask.pending_misses(tmp_path) == {}


def test_a_dropped_miss_is_no_longer_pending(tmp_path):
    ask.log(tmp_path, "miss", question="q", actual="the wiki")
    ask.log(tmp_path, "followup-drop", question="q", tries=5)
    assert ask.pending_misses(tmp_path) == {}


def test_followup_with_no_pending_misses_does_not_touch_memory(tmp_path, monkeypatch, capsys):
    def fail_memory(req):
        raise AssertionError("followup must not call memory when nothing is pending")
    monkeypatch.setattr(ask, "memory", fail_memory)

    rc = ask.followup("alice", tmp_path)

    assert rc == 0
    assert "no pending misses" in capsys.readouterr().out


def test_followup_proposes_a_confident_retry_without_approving(tmp_path, monkeypatch, capsys):
    ask.log(tmp_path, "miss", question="where is it?", actual="the wiki")
    calls = []
    memory = confident_memory()
    monkeypatch.setattr(ask, "memory", lambda req: (calls.append(req["action"]), memory(req))[1])

    rc = ask.followup("alice", tmp_path)

    assert rc == 0
    assert "approve" not in calls  # proposes, never approves on its own
    out = capsys.readouterr().out
    assert "PROPOSED" in out
    assert "/found.md" in out
    assert "--approve" in out

    lines = tmp_path.joinpath("lookups.jsonl").read_text().splitlines()
    recs = [json.loads(l) for l in lines]
    tried = [r for r in recs if r["kind"] == "followup-try"]
    assert len(tried) == 1
    assert tried[0]["result"] == "proposed"
    assert tried[0]["tries"] == 1

    # still pending -- a human has to run --approve; followup never resolves it itself
    assert "where is it?" in ask.pending_misses(tmp_path)


def test_followup_leaves_a_still_missing_question_pending_below_max_tries(tmp_path, monkeypatch, capsys):
    ask.log(tmp_path, "miss", question="q", actual="the wiki")
    monkeypatch.setattr(ask, "memory", still_missing_memory())

    rc = ask.followup("alice", tmp_path, max_tries=5)

    assert rc == 0
    pending = ask.pending_misses(tmp_path)
    assert pending["q"]["tries"] == 1
    out = capsys.readouterr().out
    assert "dropped" not in out


def test_followup_drops_after_max_tries_with_no_confident_file(tmp_path, monkeypatch, capsys):
    ask.log(tmp_path, "miss", question="q", actual="the wiki")
    monkeypatch.setattr(ask, "memory", still_missing_memory())

    rc = ask.followup("alice", tmp_path, max_tries=1)

    assert rc == 0
    assert "dropped after 1 tries" in capsys.readouterr().out
    assert ask.pending_misses(tmp_path) == {}


def test_followup_only_proposes_a_confirmed_not_a_possible_only_hit(tmp_path, monkeypatch, capsys):
    """A possible-only hit (find_pointer refuses it) must never be proposed --
    the same bar --approve already enforces."""
    ask.log(tmp_path, "miss", question="q", actual="the wiki")

    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1"}]}
        if req["action"] == "navigate":
            return {"status": "candidates", "candidates": [{"score": 0.7, "originalPath": "/maybe.md"}]}
        raise AssertionError(req)

    # A routing score in the "possible" band (0.6-0.85) with no real content
    # score classifies as possible, not confirmed, when the question looks
    # like an opinion ask that OPINION_RE matches -- keep this simple by just
    # asserting no PROPOSED line is printed for a sub-CONFIRM_FLOOR-only hit
    # when confirm reports no scores at all and the file isn't kept as partial.
    monkeypatch.setattr(ask, "confirm", lambda question, paths: ({}, set(), None, {}))
    monkeypatch.setattr(ask, "memory", fake_memory)

    rc = ask.followup("alice", tmp_path, max_tries=5)

    assert rc == 0
    out = capsys.readouterr().out
    assert "PROPOSED" not in out


def test_main_routes_followup_through_state_dir(tmp_path, monkeypatch, capsys):
    ask.log(tmp_path, "miss", question="q", actual="the wiki")
    monkeypatch.setattr(ask, "state_dir", lambda principal: tmp_path)
    monkeypatch.setattr(ask, "memory", confident_memory(question="q", path="/found.md"))
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "alice", "--followup"])

    rc = ask.main()

    assert rc == 0
    assert "PROPOSED" in capsys.readouterr().out


def test_main_followup_respects_max_tries_flag(tmp_path, monkeypatch, capsys):
    ask.log(tmp_path, "miss", question="q", actual="the wiki")
    monkeypatch.setattr(ask, "state_dir", lambda principal: tmp_path)
    monkeypatch.setattr(ask, "memory", still_missing_memory())
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "alice", "--followup", "--max-tries", "1"])

    rc = ask.main()

    assert rc == 0
    assert "dropped after 1 tries" in capsys.readouterr().out


def test_followup_never_reproposes_the_file_the_miss_named_as_wrong(tmp_path, monkeypatch, capsys):
    """A fresh retry that lands on the exact same file SJ was already wrong
    about at miss-time must never be proposed again -- it's still the wrong
    file, even though the miss's "actual" (correct answer) is a different
    path entirely."""
    ask.log(tmp_path, "lookup", question="where is it?",
            top=[{"score": 0.9, "path": "/wrong.md", "pointer": "p1", "possible": False}])
    ask.log(tmp_path, "miss", question="where is it?", actual="/the-real-file.md")
    assert ask.pending_misses(tmp_path)["where is it?"]["wrong"] == "/wrong.md"
    monkeypatch.setattr(ask, "memory", confident_memory(question="where is it?", path="/wrong.md"))

    rc = ask.followup("alice", tmp_path, max_tries=5)

    assert rc == 0
    out = capsys.readouterr().out
    assert "PROPOSED" not in out
    assert "where is it?" in ask.pending_misses(tmp_path)


def test_followup_never_falls_back_to_a_stale_prior_hit_on_a_failed_fresh_search(tmp_path, monkeypatch, capsys):
    """A prior lookup (e.g. the original miss's own confident-looking log line)
    must never leak into a followup proposal when the FRESH retry itself
    errors out or finds nothing -- only the just-run lookup counts."""
    ask.log(tmp_path, "lookup", question="where is it?",
            top=[{"score": 0.95, "path": "/old-hit.md", "pointer": "p1", "possible": False}])
    ask.log(tmp_path, "miss", question="where is it?", actual="/old-hit.md")

    def erroring_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1"}]}
        if req["action"] == "navigate":
            return {"status": "error", "reason": "boom"}
        raise AssertionError(req)
    monkeypatch.setattr(ask, "memory", erroring_memory)

    rc = ask.followup("alice", tmp_path, max_tries=5)

    assert rc == 0
    out = capsys.readouterr().out
    assert "PROPOSED" not in out


def test_followup_never_proposes_a_confirmed_file_off_the_question_subject(tmp_path, monkeypatch, capsys):
    """A confirmed-tier, not-the-original-wrong-file hit is still not enough --
    v1.0.6 amazon-bm-fb: a price-ceiling question ('max bid ceiling') proposed a
    same-folder-but-different-date snapshot report that never mentions bids or
    ceilings at all. subject_matches must catch this even when the tier and
    wrong-file checks both pass."""
    off_subject = tmp_path / "unrelated-report.md"
    off_subject.write_text("weekly shipping volume summary, nothing about pricing")
    ask.log(tmp_path, "miss", question="what's our max bid ceiling on a macbook air trade-in",
            actual="bm-price-ceilings.json")
    monkeypatch.setattr(ask, "memory", confident_memory(
        question="what's our max bid ceiling on a macbook air trade-in", path=str(off_subject)))

    rc = ask.followup("alice", tmp_path, max_tries=5)

    assert rc == 0
    out = capsys.readouterr().out
    assert "PROPOSED" not in out
    assert "off-subject" in out
    assert "what's our max bid ceiling on a macbook air trade-in" in ask.pending_misses(tmp_path)


def test_followup_proposes_a_confirmed_file_that_matches_the_question_subject(tmp_path, monkeypatch, capsys):
    """The counterpart to the off-subject test: a confirmed file whose content
    actually shares terms with the question is still proposed."""
    on_subject = tmp_path / "bm-price-ceilings.md"
    on_subject.write_text("max bid ceiling for a macbook air trade-in is $310")
    ask.log(tmp_path, "miss", question="what's our max bid ceiling on a macbook air trade-in",
            actual="bm-price-ceilings.json")
    monkeypatch.setattr(ask, "memory", confident_memory(
        question="what's our max bid ceiling on a macbook air trade-in", path=str(on_subject)))

    rc = ask.followup("alice", tmp_path, max_tries=5)

    assert rc == 0
    out = capsys.readouterr().out
    assert "PROPOSED" in out


def test_main_followup_bad_max_tries_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "alice", "--followup", "--max-tries", "not-a-number"])

    rc = ask.main()

    assert rc == 2
    assert "usage" in capsys.readouterr().out
