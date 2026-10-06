#!/usr/bin/env python3
"""Asking about a set whose judge-held file was edited starts one background heal; an unchanged held file, or
a held entry with no sha, starts none. Made-up Quillbrook notes, stub judge, no network.

    python3 -m pytest skills/super-jev/tests/test_held_edit_heal.py -q
"""
import json
from pathlib import Path

from test_ask_json import Q, ah, ask, clean, notes, run, world  # noqa: F401  (fixtures: clean is autouse)


def held_world(tmp_path, monkeypatch, notes, edited=True, sha=True, held_sha=False):
    w, held = notes / "warranty.md", notes / "draft-held.md"
    held.write_text("original\n")
    entry = {"pass": False}
    if sha:
        entry["sha256"] = ask.sha256_file(held)
    if edited:
        held.write_text("edited since\n")
    cache = {str(w): {"pass": True, "sha256": ask.sha256_file(w), "description": "a note"}, str(held): entry}
    report = {"pointer": "notes", "approved": [str(w)], "held": [str(held)], "principal": "agent"}
    if held_sha:  # a report written since heldSha: the judged sha of each held file
        report["heldSha"] = {str(held): entry["sha256"]} if sha else {}
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "no-candidates"}, cache={"notes": cache},
          reports={"notes": report}, scores={str(w): 0.95}, lines={str(w): 4})
    cache_dir = tmp_path / "cache"
    (cache_dir / "notes.json").write_text(json.dumps(cache))
    monkeypatch.setattr(ah.rc, "CACHE_DIR", cache_dir)
    calls = []
    monkeypatch.setattr(ah, "maybe_heal", lambda ptr, principal, **k: calls.append(ptr) or "started")
    return calls


def test_an_edited_held_file_starts_one_background_heal(tmp_path, monkeypatch, capsys, notes):
    calls = held_world(tmp_path, monkeypatch, notes)
    run(monkeypatch, capsys, Q)
    assert calls == ["notes"]


def test_an_unchanged_held_file_starts_no_heal(tmp_path, monkeypatch, capsys, notes):
    calls = held_world(tmp_path, monkeypatch, notes, edited=False)
    run(monkeypatch, capsys, Q)
    assert calls == []


def test_a_held_entry_with_no_sha_starts_no_heal(tmp_path, monkeypatch, capsys, notes):
    calls = held_world(tmp_path, monkeypatch, notes, sha=False)
    run(monkeypatch, capsys, Q)
    assert calls == []


def test_the_answer_and_rc_are_the_same_with_or_without_the_edit(tmp_path, monkeypatch, capsys, notes):
    held_world(tmp_path, monkeypatch, notes, edited=False)
    base = run(monkeypatch, capsys, Q)
    held_world(tmp_path, monkeypatch, notes, edited=True)
    assert run(monkeypatch, capsys, Q) == base


def test_changed_files_ignores_a_held_entry_with_no_sha(tmp_path):
    import refresh_changed as rc
    f = tmp_path / "held.md"
    f.write_text("x\n")
    assert rc.changed_files({"approved": []}, {str(f): {"pass": False}}) == []
    assert rc.changed_files({"approved": []}, {str(f): {"pass": False, "sha256": "0" * 64}}) == [str(f)]


def test_held_edited_opens_no_cache_for_a_set_with_nothing_held_or_when_given_the_cache(tmp_path, monkeypatch):
    f = tmp_path / "held.md"
    f.write_text("edited\n")
    cache = {str(f): {"pass": False, "sha256": "0" * 64}}
    monkeypatch.setattr(ah.rc, "CACHE_DIR", tmp_path)
    reads = []
    real = ah.json.loads
    (tmp_path / "a-report.json").write_text(json.dumps({"pointer": "a", "approved": []}))
    (tmp_path / "b-report.json").write_text(json.dumps({"pointer": "b", "approved": [], "held": [[str(f), "low"]]}))
    (tmp_path / "a.json").write_text(json.dumps(cache))
    monkeypatch.setattr(ah.json, "loads", lambda t, *a, **k: reads.append(t) or real(t, *a, **k))
    assert ah.held_edited("a") is False  # nothing held: only its small report was parsed
    assert len(reads) == 1 and "0" * 64 not in reads[0]
    reads.clear()
    assert ah.held_edited("b", cache=cache) is True  # given the cache: no second parse of it
    assert len(reads) == 1 and "0" * 64 not in reads[0]


def parsed_caches(monkeypatch):
    """Every json.loads of a text that holds a prepare cache (it carries a pass flag)."""
    seen = []
    real = ah.json.loads
    monkeypatch.setattr(ah.json, "loads", lambda t, *a, **k: seen.append(t) or real(t, *a, **k))
    return seen


def test_an_ask_over_held_sets_with_recorded_shas_parses_no_cache(tmp_path, monkeypatch, capsys, notes):
    calls = held_world(tmp_path, monkeypatch, notes, held_sha=True)
    seen = parsed_caches(monkeypatch)
    real_open = Path.read_text
    opened = []
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: opened.append(self.name) or real_open(self, *a, **k))
    assert ah.held_edited("notes") is True
    assert calls == [] and "notes.json" not in opened  # only the report was read
    assert not any('"pass"' in t for t in seen)


def test_recorded_shas_start_one_heal_when_edited_and_none_when_not(tmp_path, monkeypatch, capsys, notes):
    calls = held_world(tmp_path, monkeypatch, notes, held_sha=True)
    run(monkeypatch, capsys, Q)
    assert calls == ["notes"]
    calls = held_world(tmp_path, monkeypatch, notes, edited=False, held_sha=True)
    run(monkeypatch, capsys, Q)
    assert calls == []


def test_recorded_shas_never_loop_on_a_held_entry_with_no_sha(tmp_path, monkeypatch, capsys, notes):
    calls = held_world(tmp_path, monkeypatch, notes, sha=False, held_sha=True)
    run(monkeypatch, capsys, Q)
    assert calls == []
