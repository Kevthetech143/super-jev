#!/usr/bin/env python3
"""Word search: a passage that states two question words side by side outranks one
that only scatters them. No network.

    python3 -m pytest skills/super-jev/tests/test_phrase_rank.py -q
"""
import hashlib
import importlib.util
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_phrase", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def _cache(files):
    return {str(f): {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True} for f in files}


def test_word_pairs_skip_stopwords_and_match_by_stem():
    assert ("read", "sour") in ask.word_pairs("The row said READ THE SOURCE.")
    assert ("pare", "fold") in ask.word_pairs("labelled with its parent's folder")


def test_a_passage_stating_the_phrase_outranks_scattered_words(tmp_path, monkeypatch):
    phrase, scattered = tmp_path / "quirks.md", tmp_path / "notes.md"
    phrase.write_text("# Quirks\nThe overclaim row said read the source on a clean report.\n")
    scattered.write_text("# Notes\nRead this later. The row count moved. Source: a report. "
                         "Overclaim checks exist. Clean up the report. Said twice: row, source, "
                         "report, clean, read, overclaim.\n")
    others = [tmp_path / f"o{i}.md" for i in range(8)]
    for i, f in enumerate(others):
        f.write_text(f"Unrelated note {i} about groceries.\n")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache([phrase, scattered, *others]))
    found = [p for _, p, _ in ask.word_search("overclaim row said read the source on a clean report", ["p1"])]
    assert found[0] == str(phrase)


def test_a_repeated_word_is_no_phrase(tmp_path, monkeypatch):
    seen = []
    real = ask.word_pairs
    monkeypatch.setattr(ask, "word_pairs", lambda text: seen.append(1) or real(text))
    f = tmp_path / "a.md"
    f.write_text("step by step guide\n")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache([f]))
    ask.word_search("step by step", ["p1"])
    assert seen == []  # no question pair, so no passage pair pass at all
