#!/usr/bin/env python3
"""Hand test 6 (2026-09-25) misses: a long file whose answer sits in one passage never
reached word search's read slots, and a stale pointer whose files were all already
reviewed stayed hidden until someone refreshed it by hand.

No network, no real key, prepare_bulk.py never runs.

    python3 -m pytest skills/super-jev/tests/test_stale_and_passages.py -q
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_passages", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)
ah = ask.auto_heal


def _cache(files):
    return {str(f): {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True} for f in files}


def test_a_long_file_is_not_sunk_by_its_unrelated_passages(tmp_path, monkeypatch):
    # q01: the 36 KB timeline held the knee referral code in one line and sat at #5 behind
    # short knee notes: whole-file BM25 divided its one matching line by 36 KB of other visits.
    timeline, knee = tmp_path / "timeline.md", tmp_path / "knee.md"
    filler = "".join(f"- 2026-08-{i % 28 + 1:02d} visit notes about sleep, labs and diet.\n" for i in range(300))
    timeline.write_text(filler + "- Knee referral diagnosis code M22.2X1.\n" + filler)
    knee.write_text("Knee pain notes: knee brace, knee ice. Referral pending. Diagnosis unclear.\n")
    others = [tmp_path / f"u{i}.md" for i in range(20)]
    for i, f in enumerate(others):
        f.write_text(f"Unrelated note {i} about groceries and rent.\n" * 60)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache([timeline, knee, *others]))
    found = {p: s for s, p, _ in ask.word_search("knee referral diagnosis code", ["p1"])}
    assert found[str(timeline)] > 0.6 * found[str(knee)]


def test_a_written_phone_number_counts_as_phone_number():
    # q02 "NYP ENT phone number": the answer line lists "NYP ENT ..., 212-305-6390" and never
    # says "phone", so the file could not cover the question's words.
    words = ask.passage_words("NYP ENT 622 W 168th St 10th fl, 212-305-6390; (212) 979-4340.")
    assert words["phone"] == 2 and words["number"] == 2
    assert ask.passage_words("Labs 2026-09-11, A1c 5.2%, visit 03/19/2026")["phone"] == 0


def test_weak_matches_far_behind_the_best_take_no_read_slot(tmp_path, monkeypatch):
    # q28: a passing mention in a long timeline took a read slot and confirmed over the handout.
    handout, other = tmp_path / "epley-home-handout.md", tmp_path / "notes.md"
    handout.write_text("# Epley maneuver at home\nEpley maneuver steps at home: ... Epley again.\n")
    other.write_text("Lots about the day. " * 150 + "Did the Epley maneuver once at home.\n")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache([handout, other]))
    found = ask.word_search("how do I do the Epley maneuver at home?", ["p1"])
    assert [p for _, p, _ in found] == [str(handout)]


def _heal_setup(tmp_path, monkeypatch, changed=False, parts=None):
    root = tmp_path / "brain"; root.mkdir()
    cache_dir = tmp_path / "cache"; cache_dir.mkdir()
    monkeypatch.setattr(ah, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(ah, "LOG_PATH", tmp_path / "state" / "autoheal.log")
    f = root / "pending.md"
    f.write_text("# pending\n")
    sha = hashlib.sha256(f.read_bytes()).hexdigest()
    (cache_dir / "brain.json").write_text(json.dumps({str(f): {"sha256": "old" if changed else sha, "pass": True}}))
    (cache_dir / "brain-report.json").write_text(json.dumps(
        {"pointer": "brain", "roots": [str(root)], "approved": [str(f)], "principals": ["hf"],
         "parts": parts or []}))
    calls = []

    class Proc:
        def __init__(self, cmd, **kw):
            calls.append(cmd)

        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(ah.subprocess, "Popen", Proc)
    return cache_dir, calls


def test_stale_pointer_with_nothing_to_redraft_reconnects_inline(tmp_path, monkeypatch):
    # primary-reference sat stale for hours: maybe_heal said "no-change" because the prepare
    # cache already matched every file, so nothing ever reconnected it.
    cache_dir, calls = _heal_setup(tmp_path, monkeypatch)
    assert ah.maybe_heal("brain", "hf", cache_dir=cache_dir) == "no-change"
    assert ah.reconnect_now("brain", "hf", cache_dir=cache_dir) == "reconnected"
    assert len(calls) == 1 and "--no-findability" in calls[0][2] and "--refresh" in calls[0][2]


def test_a_real_change_is_left_to_the_background_refresh(tmp_path, monkeypatch):
    cache_dir, calls = _heal_setup(tmp_path, monkeypatch, changed=True)
    assert ah.reconnect_now("brain", "hf", cache_dir=cache_dir) == "changed"
    assert calls == []


def test_a_split_part_heals_through_its_parent_report(tmp_path, monkeypatch):
    # health-fitness-brain-2 has no report of its own; it used to skip as "no-report".
    cache_dir, calls = _heal_setup(tmp_path, monkeypatch, parts=[{"pointer": "brain"}, {"pointer": "brain-2"}])
    assert ah.reconnect_now("brain-2", "hf", cache_dir=cache_dir) == "reconnected"
    assert "--pointer brain " in calls[0][2]
    assert ah.reconnect_now("brain-3", "hf", cache_dir=cache_dir) == "no-report"


def test_lookup_reads_a_pointer_reconnected_before_the_ask(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    pending = tmp_path / "pending.md"
    pending.write_text("to-do list")
    healed = []

    def fake(req):
        if req["action"] == "cached":
            return {"status": "miss"}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1"}]}
        if not healed:
            return {"status": "preparation-required"}
        return {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": str(pending)}]}
    monkeypatch.setattr(ask, "memory", fake)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})
    monkeypatch.setattr(ah, "reconnect_now", lambda ptr, principal: healed.append(ptr) or "reconnected")
    monkeypatch.setattr(ah, "maybe_heal", lambda *a, **k: pytest.fail("background heal after a reconnect"))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({str(pending): 0.95}, set(), None, {}))
    ask.lookup("what is on the pending to-do list", "hf", tmp_path / "s")
    out = capsys.readouterr().out
    assert healed == ["p1"] and str(pending) in out and "preparation-required" not in out
