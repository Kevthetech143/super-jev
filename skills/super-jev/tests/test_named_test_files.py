#!/usr/bin/env python3
"""A file its connector names exactly is not dropped by the test-output NAME rule.
Real failure, 2026-09-27: superjev-test-timeline.md (a run log connected with
--name) matched *superjev-test* and was silently never connected or searched, so a
claim it proves came back NOT FOUND. A named file inside a test folder stays out.

    python3 -m pytest skills/super-jev/tests/test_named_test_files.py -q
"""
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
import prepare_bulk  # noqa: E402


def test_a_named_file_is_judged_by_its_folder_only():
    assert prepare_bulk.is_test_material("notes/superjev-test-timeline.md")
    assert not prepare_bulk.is_test_material("notes/superjev-test-timeline.md", named=True)
    assert prepare_bulk.is_test_material("ops/sj-bench-1/report.md", named=True)
    assert prepare_bulk.is_test_material("x/superjev-tests/run/log.md", named=True)
    assert prepare_bulk.is_test_material("tests/fixtures/a.md", named=True)


def test_only_an_exact_name_counts():
    assert prepare_bulk.named_exactly("SuperJev-Test-Timeline.md", ["superjev-test-timeline.md"])
    assert not prepare_bulk.named_exactly("superjev-test-timeline.md", ["superjev-test-*.md"])
    assert not prepare_bulk.named_exactly("superjev-test-timeline.md", ["other.md"])
    assert not prepare_bulk.named_exactly("a.md", None)


def test_connect_admits_a_named_run_log_and_counts_the_rest(tmp_path, capsys):
    (tmp_path / "superjev-test-timeline.md").write_text("# timeline\nv1.0.62 caught a lost case\n")
    (tmp_path / "superjev-test-report-3.md").write_text("# report\nq1 q2 q3\n")
    (tmp_path / "notes.md").write_text("# notes\nreal facts\n")
    files, *_ = prepare_bulk.inventory([tmp_path], no_recurse=True,
                                       names=["superjev-test-timeline.md", "notes.md"])
    assert sorted(p.name for p in files) == ["notes.md", "superjev-test-timeline.md"]
    files, *_ = prepare_bulk.inventory([tmp_path], no_recurse=True)
    assert sorted(p.name for p in files) == ["notes.md"]
    assert "2 test/scratch output file(s)" in capsys.readouterr().out


def _ask():
    import hashlib
    import importlib.util
    spec = importlib.util.spec_from_file_location("ask_named_test", SKILL / "ask.py")
    ask = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ask)
    return ask, hashlib


def test_word_search_finds_a_named_run_log_but_not_an_unnamed_one(tmp_path, monkeypatch):
    import json
    ask, hashlib = _ask()
    log = tmp_path / "superjev-test-timeline.md"
    log.write_text("# Test timeline\nThe scorecard caught a lost case on its first run.\n")
    cache = {str(log): {"sha256": hashlib.sha256(log.read_bytes()).hexdigest(), "pass": True}}
    cdir = tmp_path / "prepare-cache"
    cdir.mkdir()
    monkeypatch.setattr(ask.prepare_bulk, "CACHE_DIR", cdir)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: cache)
    report = {"pointer": "p1", "principals": ["me"], "roots": [str(tmp_path)]}
    (cdir / "p1-report.json").write_text(json.dumps(report))
    assert ask.word_search("scorecard caught lost case first run", ["p1"]) == []
    (cdir / "p1-report.json").write_text(json.dumps({**report, "names": ["superjev-test-timeline.md"]}))
    assert [p for _, p, _ in ask.word_search("scorecard caught lost case first run", ["p1"])] == [str(log)]
