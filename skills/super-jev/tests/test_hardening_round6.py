#!/usr/bin/env python3
"""Offline tests for the rc1 hardening round 6 fixes: check scans the draft and every
claim before sending, uninstall deletes only files Super Jev recorded or can derive
from a registered pointer, and the generic secret scan stays fast on long lines.

No network and no real key. Token-shaped strings are built by concatenation so
this file itself never holds one.

    python3 -m pytest skills/super-jev/tests/test_hardening_round6.py -q
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(SKILL))
pb = load("prepare_bulk_r6", SKILL / "prepare_bulk.py")
jc = load("jev_client_r6", SKILL / "lib" / "jev_client.py")
setup = load("setup_r6", SKILL / "setup.py")
AIZA = "AI" + "za" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r"


# 1. the draft, a claims file and --claim strings are scanned too; nothing is sent
@pytest.mark.parametrize("where", ["draft", "claims-file", "claim"])
def test_check_refuses_a_secret_outside_the_evidence(tmp_path, monkeypatch, capsys, where):
    ev = tmp_path / "ev.md"
    ev.write_text("The rate is 4%.\n")
    bad = tmp_path / "bad.txt"
    bad.write_text(f"The rate is 4%. key {AIZA}\n")
    args = {"draft": ["--draft", str(bad)], "claims-file": ["--claims-file", str(bad)],
            "claim": ["--claim", f"The rate is 4%. key {AIZA}"]}[where]
    monkeypatch.setattr(jc, "check", lambda *a, **k: pytest.fail("secret was sent"))
    assert jc.main([str(ev)] + args) == 1
    assert "not sent" in capsys.readouterr().err


def test_check_still_sends_a_clean_draft(tmp_path, monkeypatch):
    ev, draft = tmp_path / "ev.md", tmp_path / "d.md"
    ev.write_text("The rate is 4%.\n")
    draft.write_text("The rate is 4%.\n")
    sent = []
    monkeypatch.setattr(jc, "check", lambda e, c, d: sent.append(d) or ([], {}, 0))
    monkeypatch.setattr(jc, "print_table", lambda *a: None)
    assert jc.main([str(ev), "--draft", str(draft)]) == 0 and sent


# 2. uninstall keeps a user's own files in prepare-cache
def _env(tmp_path, monkeypatch, pointers=()):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    state = tmp_path / "state"
    (state / "_memory").mkdir(parents=True)
    (state / "_memory" / "config.json").write_text("{}")
    (state / "_memory" / "registry.json").write_text(
        json.dumps({"version": 1, "datasets": {p: {} for p in pointers}}))
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(state))
    cache = tmp_path / "prepare-cache"
    cache.mkdir()
    for n in ("my-budget.json", "tax-held.txt", "notes.json", "notes-held.txt",
              "notes-report.json", "notes-2.json", "notes-extra.json"):
        (cache / n).write_text("x")
    monkeypatch.setattr(setup, "IN_REPO_LEFTOVERS", (cache,))
    return cache


def test_uninstall_with_manifest_deletes_only_recorded_files(tmp_path, monkeypatch):
    cache = _env(tmp_path, monkeypatch)
    (cache / setup.WRITTEN_MANIFEST).write_text("notes.json\nnotes-held.txt\n../x\n")
    assert setup.main(["--uninstall"]) == 0
    assert sorted(p.name for p in cache.iterdir()) == [
        "my-budget.json", "notes-2.json", "notes-extra.json", "notes-report.json", "tax-held.txt"]


def test_uninstall_without_manifest_derives_names_from_pointers(tmp_path, monkeypatch, capsys):
    cache = _env(tmp_path, monkeypatch, pointers=["notes"])
    assert setup.main(["--uninstall"]) == 0
    assert sorted(p.name for p in cache.iterdir()) == ["my-budget.json", "notes-extra.json", "tax-held.txt"]
    out = capsys.readouterr().out
    assert "left in place" in out and "my-budget.json" in out


def test_prepare_records_what_it_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path)
    pb.write_held_txt("notes", [("/x/a.md", "over the size ceiling")])
    pb._record_written(tmp_path / "notes.json")
    pb._record_written(tmp_path / "notes.json")
    assert (tmp_path / pb.WRITTEN_MANIFEST).read_text() == "notes-held.txt\nnotes.json\n"
    assert pb.WRITTEN_MANIFEST == setup.WRITTEN_MANIFEST


# 3. repeated-keyword lines scan in linear time; shapes and near-misses unchanged
@pytest.mark.parametrize("line", ["pwd_" * 112500, "key-" * 112500, "token_" * 75000,
                                  "pwd_" * 112500 + "=", "secret_" * 64000, "=" * 450000])
def test_pathological_keyword_lines_scan_under_03s(line):
    t = time.monotonic()
    pb.has_secret(line)
    assert time.monotonic() - t < 0.3


def test_generic_assignment_still_caught_and_prose_not():
    assert pb.has_secret('api_token = "a8f3K9zQ2mX7vB4nL1pR6tY0"')
    assert pb.has_secret("pwd_db=Zq8r2LmX7vKp4TnB9wYc3H")
    assert not pb.has_secret("the key: remember to bring it tomorrow please")
    assert not pb.has_secret("token_count = aaaaaaaaaaaaaaaaaaaaaaaa")
