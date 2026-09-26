#!/usr/bin/env python3
"""Word matching is Unicode- and accent-aware. Real failure (PR #175 review): the
ASCII-only WORD_RE split "¿Cuántas medicinas toma mi papá?" into cu/ntas/pap, so
the right Spanish file got word coverage 0.04 and a confirm would be capped to
possible by cover_gate. Nothing here makes a live provider call.

    python3 -m pytest skills/super-jev/tests/test_unicode_words.py -q
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ask_unicode_words", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

QUESTION = "¿Cuántas medicinas toma mi papá?"


def _search(tmp_path, question):
    """Two notes on a "cita": only the right one is about the boy (niño). The old
    split turned niño into ni + o (both too short to count) and día into d, so both
    notes held the same words, tied, and the wrong one ranked first by path."""
    right = tmp_path / "z-cita-nino.md"
    right.write_text("# Cita del niño\nLa cita del niño con el pediatra es el día 5 a las 3.\n", encoding="utf-8")
    wrong = tmp_path / "a-cita-perro.md"
    wrong.write_text("# Cita del perro\nLa cita del perro con el veterinario es el lunes.\n", encoding="utf-8")
    others = [tmp_path / f"n{i}.md" for i in range(8)]
    for i, f in enumerate(others):
        f.write_text(f"# Nota {i}\nLa compra del mercado, el alquiler y la luz del mes.\n", encoding="utf-8")
    files = [right, wrong, *others]
    mp = pytest.MonkeyPatch()
    mp.setattr(ask, "load_cache_files", lambda ptr: {
        str(f): {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True} for f in files})
    ask._STAGE.clear()
    try:
        ranked = ask.word_search(question, ["p1"])
    finally:
        mp.undo()
    return str(right), str(wrong), ranked


def test_accented_words_stay_whole():
    assert ask.query_terms(QUESTION) == ["medicinas", "toma", "papa"]  # "cuántas" is a stopword
    assert ask.words("Ñandú CAFÉ naïve Straße") == ["nandu", "cafe", "naive", "strasse"]
    assert ask.words("cafe\u0301 pap\u00e1") == ["cafe", "papa"]  # decomposed accents (macOS paths)


def test_english_words_unchanged():
    q = "What's the NYP ENT phone number for Kelvin's v1.0.22 release?"
    old = [w for w in __import__("re").findall(r"[a-z0-9]+", q.lower().replace("'", ""))
           if len(w) > 2 and w not in ask.QUERY_STOPWORDS]
    assert ask.query_terms(q) == list(dict.fromkeys(old))


@pytest.mark.parametrize("question", ["¿Qué día es la cita del niño?", "que dia es la cita del nino"])
def test_spanish_question_ranks_and_keeps_its_file(tmp_path, question):
    right, wrong, ranked = _search(tmp_path, question)
    assert ranked[0][1] == right and ranked[0][0] > dict((p, s) for s, p, _ in ranked).get(wrong, 0)
    scores = {right: 0.9}
    assert ask.cover_gate(scores) == [] and scores[right] == 0.9


def test_term_hits_folds_the_text():
    assert ask.term_hits(ask.query_terms(QUESTION), "Papá toma sus MEDICINAS") == 3


def test_pointer_words_from_old_tokenizer_rebuild(tmp_path):
    (tmp_path / ask.POINTER_WORDS_FILE).write_text(json.dumps({
        "old": {"generation": "g1", "words": "medicinas pap"},
        "new": {"generation": "g1", "words": "medicinas papa", "version": ask.WORDS_VERSION}}))
    known, missing = ask.pointer_words(tmp_path, {"old": "g1", "new": "g1"})
    assert missing == ["old"] and known == {"new": "medicinas papa"}


def test_saved_pointer_words_carry_the_version(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("Papá toma medicinas", encoding="utf-8")
    mp = pytest.MonkeyPatch()
    mp.setattr(ask, "memory", lambda r: {"status": "ok", "sources": [{"path": str(f), "originalPath": str(f)}]})
    try:
        ask.save_pointer_words(tmp_path, "me", {"p": "g1"}, ["p"])
    finally:
        mp.undo()
    entry = json.loads((tmp_path / ask.POINTER_WORDS_FILE).read_text())["p"]
    assert entry["version"] == ask.WORDS_VERSION and "papa" in entry["words"].split()
