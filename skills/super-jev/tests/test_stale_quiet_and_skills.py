#!/usr/bin/env python3
"""Primary live test, 2026-09-25: a stale pointer nothing could refresh printed its error
(and a prepare_bulk command that could not work) on every answer, and "which skill ..."
questions returned a doc about skill search instead of the skill.

No network, no real key, prepare_bulk.py never runs.

    python3 -m pytest skills/super-jev/tests/test_stale_quiet_and_skills.py -q
"""
import importlib.util
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_quiet", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)
ah = ask.auto_heal


def _stuck(tmp_path, monkeypatch, status="preparation-required"):
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(ask.prepare_bulk, "CACHE_DIR", tmp_path / "cache")

    def fake(req):
        if req["action"] == "cached":
            return {"status": "miss"}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "bench"}, {"pointer": "notes"}]}
        if req["action"] == "recipe":
            return {"status": "no-recipe"}
        if req["pointer"] == "bench":
            return {"status": status}
        return {"status": "no-candidates"}
    monkeypatch.setattr(ask, "memory", fake)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})
    monkeypatch.setattr(ah, "reconnect_now", lambda ptr, principal, timeout=None: "no-report")
    monkeypatch.setattr(ah, "reconnect_recipe", lambda ptr, principal, memory=None: "no-recipe")
    monkeypatch.setattr(ah, "maybe_heal", lambda *a, **k: "no-report")
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({}, set(), None, {}))


def test_a_stuck_stale_pointer_warns_once_a_day_not_on_every_answer(tmp_path, monkeypatch, capsys):
    _stuck(tmp_path, monkeypatch)
    sdir = tmp_path / "s"
    assert ask.lookup("what is pending", "primary", sdir) == 1
    first = capsys.readouterr().out
    assert "[bench] preparation-required" in first and "prepare_bulk.py" not in first
    assert ask.lookup("what is pending", "primary", sdir) == 0
    second = capsys.readouterr().out
    assert "bench" not in second and "errored" not in second
    assert '"stale-quiet"' in (sdir / "lookups.jsonl").read_text()


def test_a_stale_pointer_that_is_healing_still_shows_every_time(tmp_path, monkeypatch, capsys):
    _stuck(tmp_path, monkeypatch)
    monkeypatch.setattr(ah, "maybe_heal", lambda *a, **k: "started")
    for _ in range(2):
        ask.lookup("what is pending", "primary", tmp_path / "s")
        assert "refresh started in background" in capsys.readouterr().out


def test_hint_never_prints_a_prepare_bulk_command_for_a_pointer_it_did_not_build(tmp_path, monkeypatch):
    monkeypatch.setattr(ask.prepare_bulk, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ask, "memory", lambda req: {"status": "error"})
    assert "prepare_bulk.py" not in ask.refresh_hint("bench", "primary", "preparation-required")
    (tmp_path / "bench.json").write_text("{}")  # prepare_bulk built it; only the report is gone
    assert "prepare_bulk.py" in ask.refresh_hint("bench", "primary", "preparation-required")


def test_a_which_skill_question_is_answered_from_the_skill_catalog(tmp_path, monkeypatch, capsys):
    _stuck(tmp_path, monkeypatch, status="no-candidates")
    monkeypatch.setenv("SUPERJEV_SKILLS", "1")
    asked = []
    monkeypatch.setattr(ask, "skill_catalog", lambda q: asked.append(q) or
                        [("ebay-return-label", "/skills/ebay-return-label/SKILL.md")])
    q = "Which skill do I use to buy and print an eBay return shipping label?"
    assert ask.lookup(q, "primary", tmp_path / "s") == 0
    out = capsys.readouterr().out
    assert asked == [q]
    assert out.startswith("skill  /skills/ebay-return-label/SKILL.md  [skills: ebay-return-label]")
    assert ask.VOICE_LINE not in out


def test_only_skill_and_tool_questions_ask_the_catalog(monkeypatch):
    monkeypatch.setenv("SUPERJEV_SKILLS", "1")
    assert ask.skill_question("Which tool or skill checks Kelvin's live Robinhood buying power?")
    assert ask.skill_question("what tool buys a shipping label")
    assert not ask.skill_question("what did my dad's DEXA scan show?")
    assert not ask.skill_question("how much do we still owe Payability")
    monkeypatch.setenv("SUPERJEV_SKILLS", "0")
    assert not ask.skill_question("which skill posts to the forums")
