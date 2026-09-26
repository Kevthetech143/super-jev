#!/usr/bin/env python3
"""Fixture, sample and evidence-source folders never rank as answers (is_test_material).
Real failure, night-0803 q14: "What decision was made about releasing Super Jev
v1.0.22?" returned docs/evidence/connector-navigation-20260920/sources/Engineering/
Deployment/releases.md, a made-up sample about another company. Nothing here makes a
live provider call.

    python3 -m pytest skills/super-jev/tests/test_fixture_folders.py -q
"""
import importlib.util
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
import prepare_bulk  # noqa: E402

spec = importlib.util.spec_from_file_location("ask_fixture_folders", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

QUESTION = "What decision was made about releasing Super Jev v1.0.22?"


@pytest.mark.parametrize("path", [
    "/r/docs/evidence/connector-navigation-20260920/sources/Engineering/Deployment/releases.md",
    "/r/test/fixtures/notes.md", "/r/tests/fixture/a.md", "/r/src/__fixtures__/x.md",
    "/r/pkg/testdata/a.md", "/r/test-data/a.md", "/r/sample_data/a.md", "fixtures/a.md",
])
def test_fixture_paths_are_test_material(path):
    assert prepare_bulk.is_test_material(path)


@pytest.mark.parametrize("path", [
    "/r/notes/release-decisions.md", "/r/docs/evidence-log.md", "/r/evidence/2026-09.md",
    "/r/sources/reading-list.md", "/r/my-fixtures-plan.md", "/r/samples-of-work.md",
])
def test_real_notes_are_not_test_material(path):
    assert not prepare_bulk.is_test_material(path)


def test_routed_fixture_never_ranks(tmp_path):
    fixture = tmp_path / "docs" / "evidence" / "run" / "sources" / "releases.md"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("# Releases\nDecision: release v1.0.22 to all customers on Friday.\n")
    real = tmp_path / "design.md"
    real.write_text("# Super Jev design\n2026-09-25: v1.0.22 released after review.\n")
    mp = pytest.MonkeyPatch()
    mp.setattr(ask, "load_cache_files", lambda ptr: {})
    mp.setattr(ask, "memory", lambda r: {"status": "miss"} if r["action"] == "cached" else
               {"pointers": ["p1"]} if r["action"] == "panel" else
               {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": str(fixture)},
                                                       {"score": 0.5, "originalPath": str(real)}]})
    mp.setattr(ask, "confirm", lambda q, ps: ({p: 0.9 for p in ps}, set(), None, {}))
    mp.setattr(ask, "judge_listwise", lambda q, pool: (None, None))
    sdir = tmp_path / "s"
    sdir.mkdir()
    try:
        ask.lookup(QUESTION, "me", sdir)
    finally:
        mp.undo()
    import json
    rec = next(json.loads(ln) for ln in reversed((sdir / "lookups.jsonl").read_text().splitlines())
               if json.loads(ln).get("kind") == "lookup")
    paths = [t["path"] for t in rec.get("top") or []]
    assert str(fixture) not in paths and paths[:1] == [str(real)]
