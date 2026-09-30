"""Word search runs beside routing and returns its full ranked list; skip, floor and limit are
applied after routing. The final read list must equal the serial path's. Made-up notes, no network.

    python3 -m pytest skills/super-jev/tests/test_word_search_parallel.py -q
"""
import hashlib
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ask

Q = "knee brace size order"
NOTES = {
    "a.md": "# Knee brace\nknee brace size order: medium, ordered friday. " * 3,
    "b.md": "# Brace fitting\nknee brace size is large. order later. " * 2,
    "c.md": "# Orders\norder list: knee brace, size chart, shoes. " * 2,
    "d.md": "# Brace\nbrace size notes only.",
    "e.md": "# Garden\ntomatoes and basil, nothing else.",
    "f.md": "# Sizes\nsize order for the knee: brace type. " * 2,
    "g.md": "# Knee\nknee order brace size " * 4,
    "h.md": "# Knee order\nknee brace size order list. " * 3,
    "i.md": "# Brace order\nbrace knee order size chart. " * 3,
}


def _cache(files):
    return {str(p): {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "pass": True,
                     "description": "", "question": ""} for p in files}


@pytest.fixture
def notes(tmp_path, monkeypatch):
    paths = {}
    for name, text in NOTES.items():
        p = tmp_path / name
        p.write_text(text)
        paths[name] = p
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache(paths.values()))
    return {n: str(p) for n, p in paths.items()}


def test_full_then_pick_equals_serial_for_every_routed_skip(notes):
    full = ask.word_search(Q, ["p1"], full=True)
    assert len(full) > ask.FALLBACK_FILES  # the full list really is longer than the slots
    routed_sets = [(), [notes["a.md"]], [notes["g.md"], notes["a.md"]],
                   [notes[n] for n in ("a.md", "g.md", "b.md", "c.md", "f.md")],
                   list(notes.values())]
    for routed in routed_sets:
        skip = set(routed[:ask.CONFIRM_FILES])
        serial = ask.word_search(Q, ["p1"], skip=skip)
        assert ask.word_pick(full, skip=skip) == serial
        for limit in (1, 3, 100):
            assert ask.word_pick(full, skip=skip, limit=limit) == ask.word_search(Q, ["p1"], limit=limit, skip=skip)


def _lookup_read_list(monkeypatch, tmp_path, notes, routing_delay=0.0, reconnect=None):
    """Run ask.lookup with fake routing; return the paths handed to the content check."""
    cache = {"paths": [notes[n] for n in ("a.md", "b.md", "c.md", "d.md", "e.md")]}
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache([Path(p) for p in cache["paths"]]))
    monkeypatch.setattr(ask, "pointer_words", lambda *a: ({}, []))
    monkeypatch.setattr(ask, "batch_jev", lambda: False)
    monkeypatch.setattr(ask, "skipped_for_question", lambda *a: [])
    monkeypatch.setattr(ask, "apply_near_twin_tiebreak", lambda q, top: top)
    monkeypatch.setattr(ask.auto_heal, "maybe_scan", lambda *a, **k: "off")
    monkeypatch.setattr(ask.auto_heal, "maybe_heal", lambda *a, **k: "cooldown")
    calls = {"nav": 0}

    def memory(r):
        if r["action"] == "panel":
            return {"pointers": ["p1"]}
        if r["action"] == "navigate":
            calls["nav"] += 1
            time.sleep(routing_delay)
            if reconnect and calls["nav"] == 1:
                return {"status": "preparation-required", "reason": "stale"}
            return {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": notes["e.md"]},
                                                           {"score": 0.8, "originalPath": notes["a.md"]}]}
        return {"status": "error"}
    monkeypatch.setattr(ask, "memory", memory)
    if reconnect:
        def reconnect_now(ptr, principal, timeout=0):
            cache["paths"].append(notes["g.md"])  # the reconnect registered a new reviewed file
            return "reconnected"
        monkeypatch.setattr(ask.auto_heal, "reconnect_now", reconnect_now)
    seen = []
    def confirm(question, paths):
        seen.extend(paths)
        return {}, set(), None, {}
    monkeypatch.setattr(ask, "confirm", confirm)
    ask.lookup(Q, "reader", tmp_path / "state")
    return seen


def test_lookup_read_list_is_identical_to_the_serial_path(tmp_path, monkeypatch, notes):
    # Serial reference: word search after routing, skip/floor/limit inside it (the old call).
    routed = [notes["e.md"], notes["a.md"]]
    five = [Path(notes[n]) for n in ("a.md", "b.md", "c.md", "d.md", "e.md")]
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache(five))
    expected = routed[:ask.CONFIRM_FILES] + [p for _, p, _ in ask.word_search(
        Q, ["p1"], skip=set(routed[:ask.CONFIRM_FILES]))]
    got = _lookup_read_list(monkeypatch, tmp_path, notes, routing_delay=0.2)
    assert got == expected
    assert len(got) > len(routed)  # word search added files beyond the routed ones


def test_reconnect_mid_ask_reruns_word_search(tmp_path, monkeypatch, notes):
    runs = []
    real = ask.word_search
    def counting(*a, **k):
        runs.append(k.get("full"))
        return real(*a, **k)
    monkeypatch.setattr(ask, "word_search", counting)
    got = _lookup_read_list(monkeypatch, tmp_path, notes, reconnect=True)
    assert runs == [True, True]  # once beside routing, once again after the reconnect
    assert notes["g.md"] in got  # the file the reconnect added is read, as in the serial path
    # No reconnect: one search only.
    runs.clear()
    _lookup_read_list(monkeypatch, tmp_path, notes)
    assert runs == [True]


def test_trace_show_prints_stage_seconds(tmp_path, monkeypatch, notes, capsys):
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    _lookup_read_list(monkeypatch, tmp_path, notes, reconnect=True)
    capsys.readouterr()
    assert ask.trace_show(tmp_path / "state", "last") == 0
    line = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("stage seconds:"))
    for key in ("routing", "reconnect", "content_check", "total_secs", "routing runs=", "timeout re-checks="):
        assert key in line
    assert "word search re-run after reconnect" in line
