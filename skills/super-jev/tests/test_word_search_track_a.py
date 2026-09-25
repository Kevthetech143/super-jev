#!/usr/bin/env python3
"""Word search track A (2026-09-24 live asks): "Kelvin knee referral diagnosis code" read
another person's knee notes; a long timeline lost to short files on whole-file counts; "NYP ENT
phone number" missed the line holding the number. Fixtures only, no network.

    python3 -m pytest skills/super-jev/tests/test_word_search_track_a.py -q
"""
import hashlib
import importlib.util
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_track_a", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def _cache(files):
    return {str(p): {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "pass": True,
                     "description": "", "question": ""} for p in files}


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _rank(q, files, monkeypatch):
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache(files))
    return [p for _, p, _ in ask.word_search(q, ["p1"], limit=10)]


# 1. a named person's folder is lifted; no name, no change
def test_named_person_folder_outranks_other_persons(tmp_path, monkeypatch):
    other = _write(tmp_path, "documents/esteban/medical/knee.md",
                   "# Knee\nknee referral diagnosis knee referral diagnosis code knee pain kelvin drove him")
    mine = _write(tmp_path, "documents/kelvin/medical/timeline.md",
                  "# Timeline\n" + "visit notes\n" * 40 + "knee referral diagnosis code M22.2X1\n")
    files = [other, mine]
    assert _rank("Kelvin knee referral diagnosis code", files, monkeypatch)[0] == str(mine)
    assert _rank("knee referral diagnosis code", files, monkeypatch)[0] == str(other)


# 2. a long file is scored by its best passage, not diluted by its length
def test_long_file_scored_by_best_passage(tmp_path, monkeypatch):
    long = _write(tmp_path, "notes/timeline.md",
                  "# Timeline\n" + "unrelated daily entry about groceries\n" * 600
                  + "patellar brace fitting booked; patellar brace fitting paid; patellar brace fitting done\n" + "unrelated daily entry\n" * 300)
    short = _write(tmp_path, "notes/misc.md", "# Misc\npatellar. brace. fitting. "
                   + "other words here " * 150)
    filler = [_write(tmp_path, f"notes/other{i}.md", f"# Other {i}\nshopping list and errands\n") for i in range(4)]
    assert _rank("patellar brace fitting", [long, short] + filler, monkeypatch)[0] == str(long)


# 3. phone/email/code words match their shapes; side-by-side question words score higher
def test_phone_word_matches_phone_number_shape(tmp_path, monkeypatch):
    has_number = _write(tmp_path, "a.md", "# Clinics\nNYP ENT 622 W 168th St, 212-305-6390\n")
    no_number = _write(tmp_path, "b.md", "# Plan\nNYP visit; ENT follow-up; phone them soon\n")
    assert _rank("NYP ENT phone number", [has_number, no_number], monkeypatch)[0] == str(has_number)


def test_code_and_email_words_match_their_shapes():
    assert ask.CODE_RE.findall("diagnosis: M22.2X1 + M22.2X2; R42") == ["M22.2X1", "M22.2X2"]
    assert ask.EMAIL_RE.findall("write to front.desk@clinic.org today") == ["front.desk@clinic.org"]
    assert ask.PHONE_RE.findall("call (212) 305-6390 or 646.697.8479") == ["(212) 305-6390", "646.697.8479"]
