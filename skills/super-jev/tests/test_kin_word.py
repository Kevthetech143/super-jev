#!/usr/bin/env python3
"""Person folders found by their PROFILE's Relation line, in any layout (not only the fleet's documents/<name>/).
Made-up family in a tmp dir, stub judge, no provider calls.

    python3 -m pytest skills/super-jev/tests/test_kin_word.py -q
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_index_read_path import PRINCIPAL, Rig, ask, ask_it, build, flag, sync  # noqa: E402
from file_index import FileIndex  # noqa: E402

pytestmark = pytest.mark.real_toc

FAMILY = {  # a non-fleet layout: family/<name>/, no agents/global/documents anywhere
    "family/nora/PROFILE.md": "# Nora\n- Relation: mother\n",
    "family/nora/medical/OPEN-QUESTIONS.md": "# Questions\nNora asked about the open referral. Nora's follow-up is open.\n",
    "family/gustavo/PROFILE.md": "# Gustavo\n- Relation: father\n",
    "family/gustavo/medical/OPEN-QUESTIONS.md": "# Questions\nGustavo has an open claim.\n",
    "calls/kitchen-call.md": "# Call\nmom said the pharmacy is open late on friday.\n",  # says "mom", is no one's folder
    **{f"hours/hours-{i}.md": f"# Hours {i}\nthe library is open on weekends.\n" for i in range(6)},
}
NORA = "family/nora/medical/OPEN-QUESTIONS.md"
DADS = "family/gustavo/medical/OPEN-QUESTIONS.md"
DECOY = "calls/kitchen-call.md"
ASK = "what's still open for my mom?"


def family(tmp_path, monkeypatch):
    notes, names, sdir = build(tmp_path, monkeypatch, 40)
    cdir = ask.prepare_bulk.CACHE_DIR
    cache = json.loads((cdir / "p0.json").read_text())
    for rel, text in FAMILY.items():
        f = notes / "p0" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        cache[str(f)] = {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True, "description": f.name, "question": ""}
    (cdir / "p0.json").write_text(json.dumps(cache))
    Rig(monkeypatch, notes, names)
    return notes / "p0", names, sdir


def found(question, sdir, capsys, monkeypatch):
    """The word search's top 5 paths in a real ask (the files that passed its coverage test)."""
    got, real = [], ask.word_search
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: got.append(real(*a, **k)) or got[-1])
    ask_it(question, sdir, capsys)
    monkeypatch.setattr(ask, "word_search", real)
    return [p for _s, p, _ptr in got[-1]]


def test_person_folder_outside_global_documents(tmp_path, monkeypatch):
    root, names, _sdir = family(tmp_path, monkeypatch)
    assert ask.people(names) == {"nora": {"mother"}, "gustavo": {"father"}}
    homes = ask.person_homes(names)
    assert ask.person_of(str(root / NORA), homes) == "nora" and ask.person_of(str(root / DECOY), homes) is None


def test_my_dad_leaves_the_kin_folder_out_in_any_layout(tmp_path, monkeypatch, capsys):
    root, _names, sdir = family(tmp_path, monkeypatch)
    flag(monkeypatch, False)
    ask_it("what is still open for my dad?", sdir, capsys)
    trace = json.loads((sdir / "traces.jsonl").read_text().splitlines()[-1])["stages"]
    assert trace["person"]["who"] == ["gustavo"]
    out = found("what is still open for my dad?", sdir, capsys, monkeypatch)
    assert str(root / DADS) in out and str(root / NORA) not in out


def test_index_moves_the_person_without_a_version_bump(tmp_path, monkeypatch):
    root, _names, sdir = family(tmp_path, monkeypatch)
    sync(sdir)
    idx = FileIndex(PRINCIPAL, sdir / "index.sqlite")
    person = lambda: dict(idx.db.execute("SELECT path,person FROM fts_map"))  # noqa: E731
    assert person()[str(root / NORA)] == "nora" and person()[str(root / DECOY)] == ""
    idx.db.execute("UPDATE fts_map SET person=''")  # as an index built under the old path rule holds them
    idx.db.commit()
    idx.close()
    real = ask.person_homes  # a path-connected set lists no files in a prepare-cache: the PROFILEs come from the index
    monkeypatch.setattr(ask, "person_homes", lambda pointers, paths=None: {} if paths is None else real(pointers, paths))
    sync(sdir)
    idx = FileIndex(PRINCIPAL, sdir / "index.sqlite")
    assert person()[str(root / NORA)] == "nora" and person()[str(root / DADS)] == "gustavo"
    assert idx.fts_usable(ask.FTS_VERSION)[0]


@pytest.mark.parametrize("fts", [False, True])
def test_a_profile_on_disk_but_not_connected_still_makes_a_person(tmp_path, monkeypatch, capsys, fts):
    root, names, sdir = family(tmp_path, monkeypatch)
    (root / "family/sam").mkdir()
    (root / "family/sam/PROFILE.md").write_text("# Sam\n- Relation: brother\n")  # on disk, not in the prepare-cache
    note = root / "family/sam/visits.md"
    note.write_text("# Visits\nSam has an open referral.\n")
    cdir = ask.prepare_bulk.CACHE_DIR
    cache = json.loads((cdir / "p0.json").read_text())
    cache[str(note)] = {"sha256": hashlib.sha256(note.read_bytes()).hexdigest(), "pass": True, "description": "visits", "question": ""}
    (cdir / "p0.json").write_text(json.dumps(cache))
    assert ask.people(names)["sam"] == {"brother"}
    if fts:
        sync(sdir)
        idx = FileIndex(PRINCIPAL, sdir / "index.sqlite")
        assert str(root / "family/sam/PROFILE.md") not in dict(idx.db.execute("SELECT path,person FROM fts_map"))
        assert dict(idx.db.execute("SELECT path,person FROM fts_map"))[str(note)] == "sam"
        idx.close()
    flag(monkeypatch, fts)
    ask_it("what is still open for my brother?", sdir, capsys)
    stages = json.loads((sdir / "traces.jsonl").read_text().splitlines()[-1])["stages"]
    assert stages["person"]["who"] == ["sam"]
    assert (stages.get("index") or {}).get("fts", {}).get("used", False) is fts


def test_heading_naming_the_folder_above_keeps_the_own_folder(tmp_path, monkeypatch, capsys):
    root, names, sdir = family(tmp_path, monkeypatch)
    cdir = ask.prepare_bulk.CACHE_DIR
    cache = json.loads((cdir / "p0.json").read_text())
    files = {"people/lena/PROFILE.md": "# Lena, people I know\n- Relation: sister\n",  # names her folder and the one above
             "people/lena/visits.md": "# Visits\nLena has an open referral.\n",
             "people/otto/notes.md": "# Notes\nOtto has an open bill.\n"}  # no PROFILE: no one's folder
    for rel, text in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        cache[str(f)] = {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True, "description": f.name, "question": ""}
    (cdir / "p0.json").write_text(json.dumps(cache))
    homes = ask.person_homes(names)
    assert homes[str(root / "people/lena")] == ("lena", {"sister"}) and str(root / "people") not in homes
    assert ask.person_of(str(root / "people/otto/notes.md"), homes) is None
    sync(sdir)  # the index pass
    idx = FileIndex(PRINCIPAL, sdir / "index.sqlite")
    person = dict(idx.db.execute("SELECT path,person FROM fts_map"))
    idx.close()
    assert person[str(root / "people/lena/visits.md")] == "lena" and person[str(root / "people/otto/notes.md")] == ""
    flag(monkeypatch, True)  # the ask
    ask_it("what is still open for my sister?", sdir, capsys)
    assert json.loads((sdir / "traces.jsonl").read_text().splitlines()[-1])["stages"]["person"]["who"] == ["lena"]
