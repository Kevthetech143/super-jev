#!/usr/bin/env python3
"""Offline tests for prepare_bulk.py, the folder-scale draft/gate/connect pipeline.

No network, no live Jev call, no live writer subprocess, no live memory backend:
`prepare_bulk.writer`, `prepare_bulk.gate`, `prepare_bulk.memory` and
`prepare_bulk.navigate` are monkeypatched at the module level for every test, and
`prepare_bulk.CACHE_DIR` is monkeypatched to a tmp_path so no cache or report lands
in the repo.

    python3 -m pytest skills/super-jev/tests/test_prepare_bulk.py -q
"""
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "prepare_bulk.py"

spec = importlib.util.spec_from_file_location("prepare_bulk", SCRIPT)
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def base_argv(root, pointer="my-records", principal="agent", extra=None):
    argv = ["prepare_bulk.py", "--root", str(root), "--pointer", pointer, "--principal", principal]
    if extra:
        argv += extra
    return argv


def fake_connect_memory(calls):
    """Return a memory() stub that answers panel -> preview -> confirm in order,
    recording a deep copy of each request (the caller mutates the same dict object
    between the preview and confirm calls)."""

    def fake(req):
        calls.append(copy.deepcopy(req))
        if req.get("action") == "panel":
            return {"pointers": []}
        if "reviewed" not in req:
            return {"status": "preparation-required",
                    "sources": [{"path": s["path"], "sha256": sha256_of(Path(s["path"]))} for s in req["sources"]]}
        return {"status": "registered", "pointer": req["pointer"], "sources": req["sources"]}

    return fake


def test_inventory_skips_hidden_backup_and_vault_dirs_and_holds_password_and_oversize(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / ".hidden").mkdir(parents=True)
    (root / "profile").mkdir()
    (root / "documents").mkdir()

    (root / "normal.md").write_text("# Normal\nSome ordinary content.\n")
    (root / "readme.bak.md").write_text("stale backup, should be skipped\n")
    (root / ".hidden" / "inside.md").write_text("hidden dir content\n")
    (root / "profile" / "secret.md").write_text("profile vault content\n")
    (root / "documents" / "note.md").write_text("documents vault content\n")
    (root / "pw.md").write_text("the password for the router is hunter2\n")
    (root / "big.md").write_text("x" * 100)

    monkeypatch.setattr(pb, "CEILING_BYTES", 50)

    files, held = pb.inventory(root, limit=50)

    assert {p.name for p in files} == {"normal.md"}
    held_by_name = {Path(p).name: why for p, why in held}
    assert set(held_by_name) == {"big.md", "pw.md"}
    assert "size ceiling" in held_by_name["big.md"]
    assert "password" in held_by_name["pw.md"]


def test_limit_over_50_refuses(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", base_argv(tmp_path, extra=["--limit", "51"]))
    rc = pb.main()
    assert rc == 2
    assert "REFUSED" in capsys.readouterr().out


def test_passing_draft_is_gated_connected_and_cached(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    root.mkdir()
    f = root / "one.md"
    f.write_text("# One\nContent about one.\n")

    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: {
        str(root / "one.md"): {"path": str(root / "one.md"), "description": "Describes one.", "question": "What is one?"}
    })
    monkeypatch.setattr(pb, "gate", lambda desc, path: {"state": "SUPPORTED", "confidence": 0.9, "secs": 0.1})

    calls = []
    monkeypatch.setattr(pb, "memory", fake_connect_memory(calls))

    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--no-findability"]))
    rc = pb.main()

    assert rc == 0
    assert [c["action"] for c in calls] == ["panel", "connect", "connect"]
    preview_call, confirm_call = calls[1], calls[2]
    assert "reviewed" not in preview_call or preview_call.get("reviewed") is not True
    assert confirm_call["reviewed"] is True
    assert confirm_call["sources"][0]["sha256"] == sha256_of(f)

    cache = json.loads((pb.CACHE_DIR / "my-records.json").read_text())
    assert cache[str(f)]["pass"] is True
    assert cache[str(f)]["sha256"] == sha256_of(f)
    assert "registered" in capsys.readouterr().out


def test_failing_draft_gets_one_retry_then_lands_in_exceptions_not_connected(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    root.mkdir()
    f = root / "bad.md"
    f.write_text("# Bad\nContent that never gates.\n")

    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    writer_calls = []

    def fake_writer(items, model, feedback=None):
        writer_calls.append((items, feedback))
        return {str(f): {"path": str(f), "description": "A draft.", "question": "What is bad?"}}

    monkeypatch.setattr(pb, "writer", fake_writer)
    monkeypatch.setattr(pb, "gate", lambda desc, path: {"state": "NOT_SUPPORTED", "confidence": 0.4, "secs": 0.1})

    memory_calls = []
    monkeypatch.setattr(pb, "memory", lambda req: memory_calls.append(req) or {"status": "should-not-be-called"})

    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--no-findability"]))
    rc = pb.main()

    assert rc == 0
    assert len(writer_calls) == 2  # initial batch draft + exactly one rewrite retry
    assert memory_calls == []      # connect_set stayed empty; memory is never invoked
    out = capsys.readouterr().out
    assert "EXCEPTION" in out

    cache = json.loads((pb.CACHE_DIR / "my-records.json").read_text())
    assert cache[str(f)]["pass"] is False


def test_cache_hit_skips_writer(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    root.mkdir()
    f = root / "known.md"
    f.write_text("# Known\nAlready reviewed content.\n")

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    (cache_dir / "my-records.json").write_text(json.dumps({
        str(f): {"sha256": sha256_of(f), "description": "Known description.", "question": "What is known?",
                 "verdict": "SUPPORTED", "confidence": 0.95, "pass": True, "checkedAt": "2026-01-01T00:00:00"}
    }))

    writer_calls = []
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: writer_calls.append(items) or {})
    monkeypatch.setattr(pb, "gate", lambda desc, path: (_ for _ in ()).throw(AssertionError("gate should not run")))

    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--no-connect"]))
    rc = pb.main()

    assert rc == 0
    assert writer_calls == []
    out = capsys.readouterr().out
    assert "1 unchanged and already passing, 0 to draft" in out


def test_findability_hit_and_miss(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    root.mkdir()
    fa = root / "a.md"
    fb = root / "b.md"
    fa.write_text("# A\nAbout a.\n")
    fb.write_text("# B\nAbout b.\n")
    other = tmp_path / "elsewhere" / "c.md"  # outside root; wrong top hit for b's question
    other.parent.mkdir()
    other.write_text("# C\nUnrelated.\n")

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    (cache_dir / "my-records.json").write_text(json.dumps({
        str(fa): {"sha256": sha256_of(fa), "description": "Describes a.", "question": "What is a?",
                  "verdict": "SUPPORTED", "confidence": 0.9, "pass": True, "checkedAt": "2026-01-01T00:00:00"},
        str(fb): {"sha256": sha256_of(fb), "description": "Describes b.", "question": "What is b?",
                  "verdict": "SUPPORTED", "confidence": 0.9, "pass": True, "checkedAt": "2026-01-01T00:00:00"},
    }))

    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: (_ for _ in ()).throw(AssertionError("writer should not run")))
    monkeypatch.setattr(pb, "gate", lambda desc, path: (_ for _ in ()).throw(AssertionError("gate should not run")))

    calls = []
    monkeypatch.setattr(pb, "memory", fake_connect_memory(calls))

    def fake_navigate(pointer, principal, question):
        if question == "What is a?":
            return [str(fa)]
        return [str(other)]

    monkeypatch.setattr(pb, "navigate", fake_navigate)

    monkeypatch.setattr(sys, "argv", base_argv(root))
    rc = pb.main()

    assert rc == 0
    report = json.loads((cache_dir / "my-records-report.json").read_text())
    assert report["findability"]["hits"] == 1
    miss_paths = {Path(p).name for p, _why in report["findability"]["misses"]}
    assert miss_paths == {"b.md"}


def test_no_connect_writes_report_and_never_calls_memory(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    root.mkdir()
    f = root / "one.md"
    f.write_text("# One\nContent about one.\n")

    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: {
        str(f): {"path": str(f), "description": "Describes one.", "question": "What is one?"}
    })
    monkeypatch.setattr(pb, "gate", lambda desc, path: {"state": "SUPPORTED", "confidence": 0.9, "secs": 0.1})
    monkeypatch.setattr(pb, "memory", lambda req: (_ for _ in ()).throw(AssertionError("memory should not be called")))

    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--no-connect"]))
    rc = pb.main()

    assert rc == 0
    report = json.loads((pb.CACHE_DIR / "my-records-report.json").read_text())
    assert report["connected"] is False
    assert report["approved"] == [str(f)]
    out = capsys.readouterr().out
    assert "no connect (--no-connect)" in out
