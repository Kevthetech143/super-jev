#!/usr/bin/env python3
"""A principal who can read a note's original through a raw pointer is answered from the original, not from the
redacted reviewed-view copy of it; where the original is not connected the view still answers. Made-up data.

    python3 -m pytest skills/super-jev/tests/test_raw_over_view.py -q
"""
from test_ask_json import ask, clean, cands, notes, run, world  # noqa: F401  (fixtures: clean is autouse)

Q = "Where does Mia keep the keys?"


def build(tmp_path, monkeypatch, notes, raw_has_a=True):
    a, b = notes / "a.md", notes / "b.md"
    a.write_text("# Keys\n\nMia keeps keys at /Users/x/y\n")
    b.write_text("# Other\n\nNothing about keys here.\n")
    view = tmp_path / "prepared" / "00.txt"  # the reviewed view's own copy of a.md, redacted
    view.parent.mkdir()
    view.write_text("# Keys\n\n[PERSON_REDACTED] keeps keys at [LOCAL_PATH_REDACTED]\n")
    raw_files = [a, b] if raw_has_a else [b]
    cache = {"raw": {str(p): {"pass": True, "sha256": ask.sha256_file(p), "description": "a note"} for p in raw_files},
             "brain-reviewed": {str(view): {"pass": True, "sha256": ask.sha256_file(view), "description": "a note"}}}
    panel = {"pointers": [{"pointer": "raw"}, {"pointer": "brain-reviewed", "viewOriginals": [str(a)]}]}

    def navigate(ptr):
        if ptr == "brain-reviewed":  # the view copy outranks the original
            return {"status": "candidates", "candidates": [{"score": 0.99, "originalPath": str(view), "upstreamPath": str(a)}]}
        return cands(*raw_files)
    src = {"status": "ok", "sources": [{"originalPath": str(view), "upstreamPath": str(a), "contentSHA": ask.sha256_file(view),
                                        "description": "a note"}]}
    scores = {str(a): 0.9, str(b): 0.3, str(view): 0.95}
    world(tmp_path, monkeypatch, pointers=["raw", "brain-reviewed"], panel=panel, navigate=navigate, cache=cache,
          scores=scores, lines={str(a): 3, str(view): 3}, extra={"sources": lambda req: src})
    return a, view


def test_the_raw_original_wins_and_is_listed_once(tmp_path, monkeypatch, capsys, notes):
    a, view = build(tmp_path, monkeypatch, notes)
    rc, out = run(monkeypatch, capsys, Q)
    assert "_REDACTED]" not in out and str(view) not in out
    assert out.count(str(a)) == 1
    assert next(ln for ln in out.splitlines() if str(a) in ln or str(view) in ln).count(str(a)) == 1


def test_the_view_still_answers_when_the_original_is_not_connected(tmp_path, monkeypatch, capsys, notes):
    a, view = build(tmp_path, monkeypatch, notes, raw_has_a=False)
    rc, out = run(monkeypatch, capsys, Q)
    assert str(view) in out
