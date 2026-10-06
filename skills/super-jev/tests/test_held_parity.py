#!/usr/bin/env python3
"""A held file (it still matches its review, but its text now scans as a secret) is searched by neither path: the
index and today's path give the same list, both name the file as held, and a held file never makes a set stale.
Made-up files, fake secret-shaped strings and a stub judge, no network, no spend.

    python3 -m pytest skills/super-jev/tests/test_held_parity.py -q
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_index_read_path import PLANTED, PRINCIPAL, Rig, ask, ask_it, build, flag, sync, top5  # noqa: E402
from test_file_index import _entry  # noqa: E402
from file_index import FileIndex  # noqa: E402

FAKE = "api_key = " + "sk" + "-live-9fQ2xZ7pL0aBcD3eF4"  # secret-shaped, made up


def _held_file(notes, cdir, ptr="p0", name="zorblax-held.md"):
    """A reviewed file whose reviewed sha matches its bytes, holding the planted words and a fake secret."""
    f = notes / ptr / name
    f.write_text(f"# zorblax held\n{PLANTED[0][2]}, per the held copy.\n{FAKE}\n")
    cache = cdir / f"{ptr}.json"
    rows = json.loads(cache.read_text())
    rows[str(f)] = {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True,
                    "description": name, "question": ""}
    cache.write_text(json.dumps(rows))
    return f


def _cdir():
    return ask.prepare_bulk.CACHE_DIR


def test_held_rows_never_make_a_set_stale(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    files = []
    for i in range(4):
        f = d / f"note{i}.md"
        f.write_text(f"made up note number {i}\n")
        files.append(f)
    files[3].write_text("harmless\n" + FAKE + "\n")
    entries = {str(f): _entry(f) for f in files}
    idx = FileIndex("a", db_path=tmp_path / "a.sqlite")
    idx.update("p", entries, [str(d)])  # the seeding pass
    assert idx.db.execute("SELECT reason FROM seen WHERE path=?", (str(files[3]),)).fetchone() == ("held",)
    r = idx.update("p", entries)  # a later pass with the held row still there
    assert not r["stale"] and not idx.is_stale("p")
    new = d / "late.md"  # a reviewed file that arrives already held
    new.write_text("late\n" + FAKE + "\n")
    entries[str(new)] = _entry(new)
    assert not idx.update("p", entries)["stale"] and not idx.is_stale("p")
    idx.db.execute("UPDATE pointers SET stale=1 WHERE pointer='p'")  # left by an older pass that counted a held row
    idx.db.commit()
    idx.update("p", entries)
    assert not idx.is_stale("p")  # only held rows left: it clears
    files[1].write_text("now\n" + FAKE + "\n")  # an indexed file turns held: it left `files`, a real change
    entries[str(files[1])] = _entry(files[1])
    assert idx.update("p", entries)["stale"]
    idx.update("p", entries)
    assert not idx.is_stale("p")  # handled: the index no longer serves it, nor does today's path
    idx.mark_stale("p")  # a read-side mismatch: only a refresh clears it, held rows or not
    idx.update("p", entries)
    assert idx.db.execute("SELECT stale FROM pointers WHERE pointer='p'").fetchone()[0] == 2  # stale=2 unchanged


def test_both_paths_give_the_same_list_and_name_the_held_file(tmp_path, monkeypatch, capsys):
    notes, names, sdir = build(tmp_path, monkeypatch, 40)
    held = _held_file(notes, _cdir())
    Rig(monkeypatch, notes, names)
    sync(sdir)
    i = FileIndex(PRINCIPAL, sdir / "index.sqlite")
    assert i.db.execute("SELECT reason FROM seen WHERE path=?", (str(held),)).fetchone() == ("held",)
    assert not i.is_stale("p0")
    i.close()
    sync(sdir)  # a later pass: still not stale, so the index keeps serving the set
    i = FileIndex(PRINCIPAL, sdir / "index.sqlite")
    assert not i.is_stale("p0")
    i.close()
    q = PLANTED[0][0]
    flag(monkeypatch, False)
    off = ask_it(q, sdir, capsys)[1]
    flag(monkeypatch, True)
    on = ask_it(q, sdir, capsys)[1]
    for out in (off, on):
        files = [ln for ln in out.splitlines() if not ln.startswith("HELD")]
        assert not any("zorblax-held.md" in ln for ln in files), out  # never searched, never served
        assert f"HELD  {held}" in out, out  # the user is told it was held
        assert any("zorblax.md" in p for p in top5(out)), out
    assert top5(on) == top5(off)


def test_word_index_flags_a_secret_once_per_sha(tmp_path, monkeypatch):
    notes, names, sdir = build(tmp_path, monkeypatch, 10)
    held = _held_file(notes, _cdir())
    text = held.read_text()
    calls = []
    real = ask.has_secret
    monkeypatch.setattr(ask, "has_secret", lambda t: (calls.append(1) if t == text else None) or real(t))
    wpath = tmp_path / "word-index.json"
    for _ in range(3):
        found = ask.word_search(PLANTED[0][0], names, index_path=wpath)
        assert str(held) not in [p for _s, p, _ptr in found]
    assert len(calls) == 1  # worked out when the word item was built, then read from the saved index
    assert json.loads(wpath.read_text())["files"][str(held)]["secret"] is True
