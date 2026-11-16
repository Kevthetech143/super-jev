#!/usr/bin/env python3
"""A question naming someone else's person folder, asked by an agent with none of their files, ends "not found for
<name>" and never offers the asker's own files; a job's numbered step folder ranks below the note it produced.
Made-up people and notes in a tmp dir, stub judge, no provider calls.

    python3 -m pytest skills/super-jev/tests/test_stranger_and_run_folder.py -q
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_index_read_path import Rig, ask, ask_it, build  # noqa: E402

pytestmark = pytest.mark.real_toc

MINE = {"brokerage/options-level.md": "# Options level\nThe brokerage account options level is approved at level two.\n"}
OTHERS = {  # another agent's set: a person folder the asker never sees
    "people/ottoline/PROFILE.md": "# Ottoline\n- Relation: sister\n",
    "people/ottoline/notes.md": "# Notes\nOttoline keeps a garden.\n",
    "people/quillan/PROFILE.md": "# Quillan\n- Relation: self\n",  # the owner is nobody else's person
}


def put(cdir, notes, cache_name, files):
    path = cdir / f"{cache_name}.json"
    cache = json.loads(path.read_text()) if path.exists() else {}
    for rel, text in files.items():
        f = notes / cache_name / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        cache[str(f)] = {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True, "description": f.name, "question": ""}
    path.write_text(json.dumps(cache))


@pytest.fixture
def rig(tmp_path, monkeypatch):
    notes, names, sdir = build(tmp_path, monkeypatch, 30)
    cdir = ask.prepare_bulk.CACHE_DIR
    put(cdir, notes, "p0", MINE)
    put(cdir, notes, "elsewhere", OTHERS)  # not in the asker's pointer list
    Rig(monkeypatch, notes, names)
    return sdir


def test_fleet_people_skips_the_owner(rig):
    assert ask.fleet_people() == {"ottoline": {"sister"}}


def test_named_stranger_is_not_found_and_own_files_never_offered(rig, capsys):
    rc, out = ask_it("what options level is Ottoline's brokerage account approved for", rig, capsys)
    assert ask._RESULT["outcome"] == "not-found"
    assert "not found for ottoline" in ask._RESULT["why"]
    assert "options-level.md" not in out


def test_same_question_without_a_name_still_finds_own_file(rig, capsys):
    rc, out = ask_it("what options level is the brokerage account approved for", rig, capsys)
    assert "options-level.md" in out


def test_owner_name_is_not_a_stranger(rig, capsys):
    rc, out = ask_it("what options level is Quillan's brokerage account approved for", rig, capsys)
    assert "not found for" not in ask._RESULT.get("why", "")


def test_determiner_makes_it_a_thing(rig, capsys):
    ask_it("what options level is my ottoline brokerage account approved for", rig, capsys)
    assert "not found for" not in ask._RESULT.get("why", "")


def test_run_folder_ranks_below_its_note(tmp_path):
    run = tmp_path / "jobs" / "2026-01-02-glimmer"
    run.mkdir(parents=True)
    for n in ("01-preflight.md", "02-start.md", "10-learn.md", "summary.md"):
        (run / n).write_text("x")
    note = tmp_path / "notes" / "glimmer.md"
    note.parent.mkdir()
    note.write_text("x")
    guide = tmp_path / "docs"  # a numbered guide is no run folder: its names are not the cycle's receipts
    guide.mkdir()
    for n in ("01-intro.md", "02-setup.md", "03-usage.md", "summary.md"):
        (guide / n).write_text("x")
    assert ask.is_run_folder(str(run)) and not ask.is_run_folder(str(note.parent))
    assert not ask.is_run_folder(str(guide))


def test_run_folder_files_take_slots_after_the_note(tmp_path, monkeypatch):
    notes, names, _sdir = build(tmp_path, monkeypatch, 30)
    loud = "zibber flange setting zibber flange setting checked zibber flange setting again\n"
    put(ask.prepare_bulk.CACHE_DIR, notes, "p0", {
        "jobs/run-7/01-preflight.md": "# Preflight\n" + loud,
        "jobs/run-7/02-start.md": "# Start\n" + loud,
        "jobs/run-7/10-learn.md": "# Learn\n" + loud,
        "jobs/run-7/summary.md": "# Summary\n" + loud,
        "kept/answer.md": "# Answer\nThe zibber flange setting is nine, per the vendor sheet and the bench log of the week.\n",
    })
    Rig(monkeypatch, notes, names)
    got = [Path(p).name for _s, p, _ptr in ask.word_search("what is the zibber flange setting", names)]
    assert got[0] == "answer.md"
    assert len(got) == 5  # the step files still fill the slots left over


FOLKS = {"ottoline": {"sister"}, "mark": {"friend"}, "ivy": {"sister"}, "rose": {"aunt"}, "brannoc-vey": {"uncle"}}


@pytest.mark.parametrize("question", [
    "How do I mark an item sold?", "Mark sold 673", "water the ivy", "our Ivy plant", "rose gold iPhone price",
    "is Rose Gold in stock", "when is the Ivy appointment", "what did Vey say"])
def test_words_that_are_not_people(question):
    assert ask.named_people(question, FOLKS) == set()


@pytest.mark.parametrize("question,who", [
    ("what is Ottoline's account level", {"ottoline"}),
    ("when does Ottoline\u2019s policy renew", {"ottoline"}),
    ("ask Ottoline about the plan", {"ottoline"}),
    ("did Brannoc Vey file the form", {"brannoc-vey"}),
    ("what is Brannoc's address", {"brannoc-vey"}),
    ("where are OTTOLINE\u2019S notes", {"ottoline"}),
    ("who is OTTOLINE'S doctor", {"ottoline"})])
def test_names_used_as_people(question, who):
    assert ask.named_people(question, FOLKS) == who


def test_numbered_guide_is_not_demoted(tmp_path, monkeypatch):
    notes, names, _sdir = build(tmp_path, monkeypatch, 30)
    put(ask.prepare_bulk.CACHE_DIR, notes, "p0", {
        "guide/01-intro.md": "# Intro\nThe quorlap dial setting is seven on the quorlap dial setting page.\n",
        "guide/02-setup.md": "# Setup\nquorlap dial setting quorlap dial setting.\n",
        "kept/other.md": "# Other\nA quorlap dial was mentioned once with no setting.\n",
    })
    Rig(monkeypatch, notes, names)
    got = [Path(p).name for _s, p, _ptr in ask.word_search("what is the quorlap dial setting", names)]
    assert set(got[:2]) == {"01-intro.md", "02-setup.md"}  # not pushed below the weaker file
