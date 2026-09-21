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
import shlex
import sys
from datetime import timedelta
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "prepare_bulk.py"

spec = importlib.util.spec_from_file_location("prepare_bulk", SCRIPT)
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def base_argv(root, pointer="my-records", principal="agent", extra=None, roots=None):
    argv = ["prepare_bulk.py"]
    for r in (roots if roots is not None else [root]):
        argv += ["--root", str(r)]
    argv += ["--pointer", pointer, "--principal", principal]
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

    files, held = pb.inventory([root])

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


def test_writer_uses_claude_by_default_and_never_uses_a_shell(monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stdout = '[{"path": "/tmp/one.md", "description": "One.", "question": "What is one?"}]'

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Result()

    monkeypatch.setattr(pb.subprocess, "run", fake_run)
    got = pb.writer([{"path": "/tmp/one.md"}], "haiku")

    assert got["/tmp/one.md"]["description"] == "One."
    assert calls[0][0] == ["claude", "-p", "--model", "haiku"]
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["text"] is True
    assert "shell" not in calls[0][1]


def test_custom_writer_command_runs_adapter_end_to_end(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    f = root / "one.md"
    f.write_text("# One\nContent about one.\n")
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "gate", lambda desc, path: {"state": "SUPPORTED", "confidence": 0.9})
    adapter = (
        "import json,sys; prompt=sys.stdin.read(); "
        "files=json.loads(prompt.split('FILES:\\n', 1)[1]); "
        "print(json.dumps([{'path': x['path'], 'description': 'Describes one.', "
        "'question': 'What is one?'} for x in files]))"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(adapter)}"
    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--writer-command", command, "--no-connect"]))

    assert pb.main() == 0
    cache = json.loads((pb.CACHE_DIR / "my-records.json").read_text())
    assert cache[str(f)]["description"] == "Describes one."


def test_writer_failure_stops_run_without_echoing_writer_output(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    root.mkdir()
    (root / "one.md").write_text("# One\nContent about one.\n")
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")

    def broken_writer(items, model, feedback=None, command=None):
        raise pb.WriterError("writer exited with status 7")

    monkeypatch.setattr(pb, "writer", broken_writer)
    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--no-connect"]))

    assert pb.main() == 1
    out = capsys.readouterr().out
    assert "ERROR: description writer failed: writer exited with status 7" in out


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


def test_validate_labels_coerces_unknown_enum_and_bad_date():
    labels = pb.validate_labels({"kind": "spreadsheet", "status": "kinda-active", "as_of": "not-a-date",
                                  "subject": "  CLOV  "})
    assert labels == {"kind": "unknown", "status": "unknown", "as_of": "unknown", "subject": "CLOV"}


def test_validate_labels_passes_through_valid_values():
    labels = pb.validate_labels({"kind": "dashboard", "status": "active", "as_of": "2026-09-14", "subject": "CLOV"})
    assert labels == {"kind": "dashboard", "status": "active", "as_of": "2026-09-14", "subject": "CLOV"}


def test_claim_sentence_includes_as_of_when_known():
    labels = {"kind": "dashboard", "status": "active", "as_of": "2026-09-14", "subject": "CLOV"}
    assert pb.claim_sentence(labels) == "This file is a dashboard about CLOV. Its status is active as of 2026-09-14."


def test_claim_sentence_omits_as_of_when_unknown():
    labels = {"kind": "note", "status": "unknown", "as_of": "unknown", "subject": "CLOV"}
    assert pb.claim_sentence(labels) == "This file is a note about CLOV. Its status is unknown."


def test_labeled_description_carries_brackets():
    labels = {"kind": "dashboard", "status": "active", "as_of": "2026-09-14", "subject": "CLOV"}
    out = pb.labeled_description("CLOV campaign tracker.", labels)
    assert out == ("CLOV campaign tracker. [kind: dashboard; status: active; as_of: 2026-09-14; subject: CLOV]")


def test_labels_drafted_gated_once_cached_and_sent_to_connect(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    root.mkdir()
    f = root / "one.md"
    f.write_text("# One\nCLOV campaign, not yet filed, as of 2026-09-14.\n")

    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: {
        str(f): {"path": str(f), "description": "CLOV wheel campaign tracker.", "question": "What is CLOV status?",
                  "kind": "dashboard", "status": "active", "as_of": "2026-09-14", "subject": "CLOV"}
    })

    gate_calls = []

    def fake_gate(claim, path):
        gate_calls.append(claim)
        return {"state": "SUPPORTED", "confidence": 0.95, "secs": 0.1}

    monkeypatch.setattr(pb, "gate", fake_gate)

    calls = []
    monkeypatch.setattr(pb, "memory", fake_connect_memory(calls))

    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--no-findability"]))
    rc = pb.main()

    assert rc == 0
    # exactly one gate call per file (no separate description-only call)
    assert len(gate_calls) == 1
    assert gate_calls[0] == ("CLOV wheel campaign tracker. This file is a dashboard about CLOV. "
                              "Its status is active as of 2026-09-14.")

    cache = json.loads((pb.CACHE_DIR / "my-records.json").read_text())
    assert cache[str(f)]["kind"] == "dashboard"
    assert cache[str(f)]["status"] == "active"
    assert cache[str(f)]["as_of"] == "2026-09-14"
    assert cache[str(f)]["subject"] == "CLOV"

    confirm_call = calls[-1]
    assert confirm_call["sources"][0]["description"] == (
        "CLOV wheel campaign tracker. [kind: dashboard; status: active; as_of: 2026-09-14; subject: CLOV]")


def test_bad_writer_label_enum_coerced_to_unknown_never_crashes(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    f = root / "one.md"
    f.write_text("# One\nSome content.\n")

    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: {
        str(f): {"path": str(f), "description": "Describes one.", "question": "What is one?",
                  "kind": "spreadsheet", "status": "sort-of-active", "as_of": "soon", "subject": "One"}
    })
    monkeypatch.setattr(pb, "gate", lambda claim, path: {"state": "SUPPORTED", "confidence": 0.9, "secs": 0.1})
    monkeypatch.setattr(pb, "memory", lambda req: {"status": "should-not-matter"})

    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--no-connect"]))
    rc = pb.main()

    assert rc == 0
    cache = json.loads((pb.CACHE_DIR / "my-records.json").read_text())
    assert cache[str(f)]["kind"] == "unknown"
    assert cache[str(f)]["status"] == "unknown"
    assert cache[str(f)]["as_of"] == "unknown"


def test_list_filters_by_status_kind_subject_and_within_days_never_calls_writer_gate_memory(tmp_path, monkeypatch, capsys):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)

    today = pb.date.today()
    recent = (today - timedelta(days=5)).isoformat()
    stale = "2020-01-01"
    cache = {
        "/a/clov.md": {"pass": True, "kind": "dashboard", "status": "active", "as_of": recent, "subject": "CLOV"},
        "/a/bbai.md": {"pass": True, "kind": "dashboard", "status": "closed", "as_of": stale, "subject": "BBAI"},
        "/a/unk.md": {"pass": True, "kind": "note", "status": "active", "as_of": "unknown", "subject": "CLOV notes"},
        "/a/failed.md": {"pass": False, "kind": "dashboard", "status": "active", "as_of": recent, "subject": "CLOV"},
    }
    (cache_dir / "my-records.json").write_text(json.dumps(cache))

    monkeypatch.setattr(pb, "writer", lambda *a, **k: (_ for _ in ()).throw(AssertionError("writer should not run")))
    monkeypatch.setattr(pb, "gate", lambda *a, **k: (_ for _ in ()).throw(AssertionError("gate should not run")))
    monkeypatch.setattr(pb, "memory", lambda *a, **k: (_ for _ in ()).throw(AssertionError("memory should not run")))

    rows, excluded = pb.list_cmd("my-records", status="active", subject="CLOV")
    paths = {r[4] for r in rows}
    assert paths == {"/a/clov.md", "/a/unk.md"}  # failed.md excluded by pass=False

    rows2, excluded2 = pb.list_cmd("my-records", within_days=30)
    assert {r[4] for r in rows2} == {"/a/clov.md"}
    assert excluded2 == 1  # unk.md's as_of is unknown

    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--list", "--pointer", "my-records", "--status", "active"])
    rc = pb.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "CLOV" in out and "clov.md" in out


def test_list_merges_part_pointer_caches(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)

    (cache_dir / "my-records.json").write_text(json.dumps({
        "/a/one.md": {"pass": True, "kind": "dashboard", "status": "active", "as_of": "2026-01-01", "subject": "One"},
    }))
    (cache_dir / "my-records-2.json").write_text(json.dumps({
        "/a/two.md": {"pass": True, "kind": "dashboard", "status": "active", "as_of": "2026-02-01", "subject": "Two"},
    }))
    # a differently-named pointer that merely starts with the same prefix must not be pulled in
    (cache_dir / "my-records-extra.json").write_text(json.dumps({
        "/a/three.md": {"pass": True, "kind": "note", "status": "active", "as_of": "2026-03-01", "subject": "Three"},
    }))

    rows, _ = pb.list_cmd("my-records")
    paths = {r[4] for r in rows}
    assert paths == {"/a/one.md", "/a/two.md"}


def test_inventory_multiple_roots_union_in_order(tmp_path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir(); root_b.mkdir()
    (root_a / "z.md").write_text("# Z\nIn root a.\n")
    (root_a / "m.md").write_text("# M\nIn root a.\n")
    (root_b / "y.md").write_text("# Y\nIn root b.\n")

    files, held = pb.inventory([root_a, root_b])

    assert held == []
    # root a (sorted) fully before root b (sorted): union in the order the roots were given
    assert [p.name for p in files] == ["m.md", "z.md", "y.md"]


def test_inventory_dedupes_file_reachable_via_two_roots(tmp_path):
    root_a = tmp_path / "a"
    root_a.mkdir()
    (root_a / "one.md").write_text("# One\nShared file.\n")
    nested = root_a / "sub"
    nested.mkdir()
    (nested / "two.md").write_text("# Two\nNested file.\n")

    # root_a and its own subdirectory both passed as roots -> two.md reachable twice
    files, held = pb.inventory([root_a, nested])

    assert [p.name for p in files] == ["one.md", "two.md"]


def test_inventory_exclude_skips_subpath(tmp_path):
    root = tmp_path / "root"
    (root / "campaigns").mkdir(parents=True)
    (root / "companies").mkdir()
    (root / "campaigns" / "one.md").write_text("# One\nCampaign file.\n")
    (root / "companies" / "two.md").write_text("# Two\nCompany file.\n")
    (root / "keep.md").write_text("# Keep\nStays.\n")

    files, held = pb.inventory([root], excludes=["campaigns", "companies"])

    assert {p.name for p in files} == {"keep.md"}


def test_inventory_no_recurse_only_direct_children(tmp_path):
    root = tmp_path / "root"
    nested = root / "sub"
    nested.mkdir(parents=True)
    (root / "top.md").write_text("# Top\nDirect child.\n")
    (nested / "deep.md").write_text("# Deep\nNested child.\n")

    files, held = pb.inventory([root], no_recurse=True)

    assert {p.name for p in files} == {"top.md"}


def test_max_files_guard_refuses(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    root.mkdir()
    for i in range(3):
        (root / f"f{i}.md").write_text(f"# F{i}\nContent {i}.\n")

    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--max-files", "2"]))
    rc = pb.main()

    assert rc == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out and "max-files" in out


def test_held_txt_written_with_masked_line_and_pattern_type(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "pw.md").write_text("# Creds\nthe password for the router is hunter2\n")

    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    cache_dir.mkdir()

    files, held = pb.inventory([root])
    assert files == []
    pb.write_held_txt("my-records", held)

    held_txt = (cache_dir / "my-records-held.txt").read_text()
    assert "pw.md" in held_txt
    assert "card/password-like text" in held_txt
    assert "pattern=password/api-key keyword" in held_txt
    assert "line=2" in held_txt
    assert "hunter2" not in held_txt   # the digit is masked
    assert "hunter#" in held_txt


def test_split_120_approved_files_into_3_parts_one_connect_per_part(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    root.mkdir()
    paths = []
    for i in range(120):
        p = root / f"file{i:03}.md"
        p.write_text(f"# File {i}\nContent {i}.\n")
        paths.append(p)

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    cache = {
        str(p): {"sha256": sha256_of(p), "description": f"Describes file {i}.", "question": f"What is file {i}?",
                  "verdict": "SUPPORTED", "confidence": 0.9, "pass": True, "checkedAt": "2026-01-01T00:00:00"}
        for i, p in enumerate(paths)
    }
    (cache_dir / "my-records.json").write_text(json.dumps(cache))

    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: (_ for _ in ()).throw(AssertionError("writer should not run")))
    monkeypatch.setattr(pb, "gate", lambda desc, path: (_ for _ in ()).throw(AssertionError("gate should not run")))

    calls = []
    monkeypatch.setattr(pb, "memory", fake_connect_memory(calls))

    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--no-findability"]))
    rc = pb.main()

    assert rc == 0
    # panel, connect(preview), connect(confirm) per part = 3 parts * 3 calls
    pointers_used = [c["pointer"] for c in calls if c["action"] == "connect" and "reviewed" not in c]
    assert pointers_used == ["my-records", "my-records-2", "my-records-3"]
    confirm_calls = [c for c in calls if c.get("reviewed") is True]
    assert [len(c["sources"]) for c in confirm_calls] == [50, 50, 20]

    report = json.loads((cache_dir / "my-records-report.json").read_text())
    assert report["connected"] is True
    assert [(part["pointer"], part["count"]) for part in report["parts"]] == \
        [("my-records", 50), ("my-records-2", 50), ("my-records-3", 20)]
    out = capsys.readouterr().out
    assert "splitting 120 approved files into 3 parts" in out


def test_refresh_drops_removed_file_from_cache_and_reports_it(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    root.mkdir()
    kept = root / "kept.md"
    kept.write_text("# Kept\nStill here.\n")
    gone_path = str(root / "gone.md")  # never created on disk this run: simulates a prior file since deleted

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    cache = {
        str(kept): {"sha256": sha256_of(kept), "description": "Describes kept.", "question": "What is kept?",
                    "verdict": "SUPPORTED", "confidence": 0.9, "pass": True, "checkedAt": "2026-01-01T00:00:00"},
        gone_path: {"sha256": "deadbeef", "description": "Describes gone.", "question": "What is gone?",
                    "verdict": "SUPPORTED", "confidence": 0.9, "pass": True, "checkedAt": "2026-01-01T00:00:00"},
    }
    (cache_dir / "my-records.json").write_text(json.dumps(cache))

    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: (_ for _ in ()).throw(AssertionError("writer should not run")))
    monkeypatch.setattr(pb, "gate", lambda desc, path: (_ for _ in ()).throw(AssertionError("gate should not run")))

    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--refresh", "--no-connect"]))
    rc = pb.main()

    assert rc == 0
    out = capsys.readouterr().out
    assert "1 cached files removed from disk" in out
    assert "gone.md" in out

    report = json.loads((cache_dir / "my-records-report.json").read_text())
    assert report["removed"] == [gone_path]
    assert report["approved"] == [str(kept)]

    saved_cache = json.loads((cache_dir / "my-records.json").read_text())
    assert gone_path not in saved_cache
    assert str(kept) in saved_cache
