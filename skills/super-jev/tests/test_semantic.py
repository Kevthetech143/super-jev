import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ask  # noqa: E402
import semantic  # noqa: E402


def m(p):
    return (1.0, p, "ptr")


def test_word_hits_keep_order_and_last_place_goes_to_meaning(monkeypatch):
    monkeypatch.setattr(ask, "SEM_SLOTS", 1)
    merged = [m(f"w{i}") for i in range(6)]
    top = ask.with_semantic(merged, [(0.0, "s1", "ptr"), (0.0, "s2", "ptr")])
    assert [p for _s, p, _ptr in top] == ["w0", "w1", "w2", "w3", "s1"]


def test_meaning_alone_never_makes_a_found(monkeypatch):
    assert ask.with_semantic([], [(0.0, "s1", "ptr")]) == []


def test_meaning_hit_already_listed_is_not_added():
    merged = [m("a"), m("b")]
    assert ask.with_semantic(merged, [(0.0, "a", "ptr")]) == merged


def test_no_model_means_no_hits(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERJEV_EMBED_MODEL", str(tmp_path / "none"))
    semantic._state.clear()
    assert semantic.ready() is False
    assert semantic.search(tmp_path, "anything") == []
    assert semantic.update(tmp_path, [], lambda p: None) == {"skipped": "no model"}
