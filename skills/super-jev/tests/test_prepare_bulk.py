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
import os
import time
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

# The one argument builder behind auto-heal, refresh_changed.py and the printed `next:` command.
_rc_spec = importlib.util.spec_from_file_location("refresh_changed_pb", SKILL / "refresh_changed.py")
rc = importlib.util.module_from_spec(_rc_spec)
_rc_spec.loader.exec_module(rc)


@pytest.fixture(autouse=True)
def _claude_cli_present(monkeypatch):
    # --writer auto falls back to builtin when no claude CLI is on PATH (as on CI);
    # these tests exercise the model-writer path, so pretend the CLI is installed.
    real_which = pb.shutil.which
    monkeypatch.setattr(pb.shutil, "which",
                        lambda name, *a, **k: "/usr/bin/claude" if name == "claude" else real_which(name, *a, **k))


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


def test_inventory_skips_worktree_copies_below_a_root_but_connects_a_pointed_at_worktree(tmp_path):
    # One agent carried 23 pointers of a Claude worktree's stale copy of its own brain.
    brain = tmp_path / "brain"
    (brain / ".git").mkdir(parents=True)
    (brain / "live.md").write_text("# live\nbody\n")
    claude_wt = brain / ".claude" / "worktrees" / "wf_1"
    claude_wt.mkdir(parents=True)
    (claude_wt / ".git").write_text(f"gitdir: {brain}/.git/worktrees/wf_1\n")
    (claude_wt / "copy.md").write_text("# stale copy\nbody\n")
    # A worktree checkout anywhere (no .claude in the path): its .git is a file into .git/worktrees/.
    other_wt = tmp_path / "site-worktrees" / "migration"
    (other_wt / "docs").mkdir(parents=True)
    (other_wt / ".git").write_text(f"gitdir: {tmp_path}/site/.git/worktrees/migration\n")
    (other_wt / "docs" / "spec.md").write_text("# spec copy\nbody\n")
    # A submodule's .git file points into .git/modules/: that is a real checkout, kept.
    sub = brain / "vendor"
    sub.mkdir()
    (sub / ".git").write_text(f"gitdir: {brain}/.git/modules/vendor\n")
    (sub / "notes.md").write_text("# vendor notes\nbody\n")

    files, _ = pb.inventory([brain])
    assert sorted(p.relative_to(brain).as_posix() for p in files) == ["live.md", "vendor/notes.md"]
    # A --root that is itself a worktree (or inside one) was pointed at on purpose: it connects.
    assert [p.name for p in pb.inventory([claude_wt])[0]] == ["copy.md"]
    assert [p.name for p in pb.inventory([other_wt])[0]] == ["spec.md"]
    assert [p.name for p in pb.inventory([other_wt / "docs"])[0]] == ["spec.md"]


def test_a_connect_shares_the_fleet_shared_list_unless_no_shared(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    (root / "a.md").write_text("# A\nbody\n")
    seen = []
    monkeypatch.setitem(sys.modules, "share_pointers", type(sys)("share_pointers"))
    sys.modules["share_pointers"].share_defaults = lambda principals, memory=None: seen.append(list(principals))
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    (tmp_path / "cache").mkdir()
    monkeypatch.setattr(pb, "writer", lambda items, *a, **k: {i["path"]: {"description": "A note.", "question": "q?"} for i in items})
    monkeypatch.setattr(pb, "gate", lambda path, claim, *a, **k: {"state": "SUPPORTED", "confidence": 0.95, "secs": 0})
    monkeypatch.setattr(pb, "gate_many", lambda path, claims, *a, **k: [{"state": "SUPPORTED", "confidence": 0.95, "secs": 0} for _ in claims], raising=False)
    monkeypatch.setattr(pb, "memory", fake_connect_memory([]))
    monkeypatch.setattr(sys, "argv", base_argv(root, principal="newbot"))
    assert pb.main() == 0
    assert seen == [["newbot"]]
    monkeypatch.setattr(sys, "argv", base_argv(root, principal="newbot", extra=["--no-shared", "--refresh"]))
    pb.main()
    assert seen == [["newbot"]]


def test_inventory_follows_a_symlinked_folder_but_not_into_a_vault(tmp_path):
    # Path.rglob never entered a symlinked folder, so a skill installed as a link was never connected.
    root, elsewhere = tmp_path / "skills", tmp_path / "release"
    (root / "plain").mkdir(parents=True)
    (elsewhere / "linked").mkdir(parents=True)
    (tmp_path / "profile" / "x").mkdir(parents=True)
    (root / "plain" / "SKILL.md").write_text("# plain\nbody\n")
    (elsewhere / "linked" / "SKILL.md").write_text("# linked\nbody\n")
    (tmp_path / "profile" / "x" / "SKILL.md").write_text("# vault\nbody\n")
    (root / "linked").symlink_to(elsewhere / "linked")
    (root / "vault").symlink_to(tmp_path / "profile" / "x")
    (root / "plain" / "loop").symlink_to(root)

    files, held = pb.inventory([root])

    assert sorted(p.relative_to(root).as_posix() for p in files) == ["linked/SKILL.md", "plain/SKILL.md"]
    assert held == []


def test_vault_and_secret_name_checks_ignore_capitals(tmp_path):
    # macOS folders are case-insensitive: PROFILE/ is profile/, so the guard must be too.
    root, outside = tmp_path / "root", tmp_path / "agents" / "global"
    for d in ("PROFILE/x", "Documents/y"):
        (outside / d).mkdir(parents=True)
        (outside / d / "note.md").write_text("# vault\nbody\n")
    (root / "Profile").mkdir(parents=True)
    (root / ".ENV").mkdir()
    (root / "ok.md").write_text("# ok\nbody\n")
    (root / "LOGINS.md").write_text("# logins\nbody\n")
    (root / "X-SECRET.md").write_text("# x\nbody\n")
    (root / "Profile" / "me.md").write_text("# me\nbody\n")
    (root / ".ENV" / "vars.md").write_text("# env\nbody\n")
    (root / "linked-profile").symlink_to(outside / "PROFILE" / "x")
    (root / "linked-docs").symlink_to(outside / "Documents" / "y")

    files, held = pb.inventory([root])

    assert [p.name for p in files] == ["ok.md"]
    assert held == []


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


def test_refresh_repeats_every_principal_so_the_harness_never_sees_scope_change(tmp_path, monkeypatch, capsys):
    """A pointer registered for several principals (e.g. primary + primary-helper) must keep
    all of them on --refresh: naming only one would look like narrowing the pointer's scope,
    which the harness refuses with scope-change."""
    root = tmp_path / "root"
    root.mkdir()
    f = root / "one.md"
    f.write_text("# One\nContent about one.\n")

    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: {
        str(f): {"path": str(f), "description": "Describes one.", "question": "What is one?"}
    })
    monkeypatch.setattr(pb, "gate", lambda desc, path: {"state": "SUPPORTED", "confidence": 0.9, "secs": 0.1})

    calls = []
    monkeypatch.setattr(pb, "memory", fake_connect_memory(calls))

    monkeypatch.setattr(sys, "argv", base_argv(
        root, extra=["--no-findability", "--refresh", "--principal", "primary-helper"]))
    rc = pb.main()

    assert rc == 0
    connect_calls = [c for c in calls if c["action"] == "connect"]
    assert connect_calls and all(c["principals"] == ["agent", "primary-helper"] for c in connect_calls)


def test_refresh_drops_a_removed_file_without_dropping_a_principal(tmp_path, monkeypatch, capsys):
    """--refresh removes cached paths that no longer exist on disk from the cache and connect
    set -- that file-level cleanup must not also drop a principal from the pointer's scope."""
    root = tmp_path / "root"
    root.mkdir()
    kept = root / "kept.md"
    kept.write_text("# Kept\nStill here.\n")

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    gone = root / "gone.md"
    (cache_dir / "my-records.json").write_text(json.dumps(
        {str(gone): {"sha256": "old-hash", "description": "Gone.", "question": "Where is gone?",
                     "kind": "unknown", "status": "unknown", "as_of": "unknown", "subject": "unknown",
                     "pass": True, "labels_ok": True}}))
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: {
        str(kept): {"path": str(kept), "description": "Describes kept.", "question": "What is kept?"}
    })
    monkeypatch.setattr(pb, "gate", lambda desc, path: {"state": "SUPPORTED", "confidence": 0.9, "secs": 0.1})

    calls = []
    monkeypatch.setattr(pb, "memory", fake_connect_memory(calls))

    monkeypatch.setattr(sys, "argv", base_argv(
        root, extra=["--no-findability", "--refresh", "--principal", "primary-helper"]))
    rc = pb.main()

    assert rc == 0
    out = capsys.readouterr().out
    assert "1 cached files removed from disk" in out
    connect_calls = [c for c in calls if c["action"] == "connect"]
    assert connect_calls and all(c["principals"] == ["agent", "primary-helper"] for c in connect_calls)
    assert all(str(gone) not in [s["path"] for s in c["sources"]] for c in connect_calls)


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

    assert rc == 1  # nothing connected is a failure
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

    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--findability"]))
    rc = pb.main()

    assert rc == 0
    report = json.loads((cache_dir / "my-records-report.json").read_text())
    assert report["findability"]["hits"] == 1
    miss_paths = {Path(p).name for p, _why in report["findability"]["misses"]}
    assert miss_paths == {"b.md"}


def test_findability_says_when_the_search_itself_failed(tmp_path, monkeypatch, capsys):
    """A search that errors (no key, an unreachable judge) is not "ranked absent": say it did not run."""
    root = tmp_path / "root"
    root.mkdir()
    fa = root / "a.md"
    fa.write_text("# A\nAbout a.\n")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    (cache_dir / "my-records.json").write_text(json.dumps({
        str(fa): {"sha256": sha256_of(fa), "description": "Describes a.", "question": "What is a?",
                  "verdict": "SUPPORTED", "confidence": 0.9, "pass": True, "checkedAt": "2026-01-01T00:00:00"},
    }))
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: (_ for _ in ()).throw(AssertionError("writer should not run")))
    monkeypatch.setattr(pb, "gate", lambda desc, path: (_ for _ in ()).throw(AssertionError("gate should not run")))
    connect = fake_connect_memory([])

    def memory(req):
        if req.get("action") == "navigate":
            return {"status": "error", "reason": "Navigation failed: TYPESAFE_API_KEY is not set"}
        return connect(req)

    monkeypatch.setattr(pb, "memory", memory)
    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--findability"]))
    assert pb.main() == 0   # the connect itself worked; findability is a report
    out = capsys.readouterr().out
    assert "findability: not completed (Navigation failed: TYPESAFE_API_KEY is not set)" in out, out
    assert "files rank first" not in out and "MISS" not in out and "ranked absent" not in out, out
    report = json.loads((cache_dir / "my-records-report.json").read_text())
    assert report["findability"] == {"notRun": "Navigation failed: TYPESAFE_API_KEY is not set"}


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


def test_labels_drafted_gated_separately_both_pass_cached_and_sent_to_connect(tmp_path, monkeypatch, capsys):
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
    # two gate calls per file that passes stage 1: description alone, then the label sentence alone
    assert gate_calls == [
        "CLOV wheel campaign tracker.",
        "This file is a dashboard about CLOV. Its status is active as of 2026-09-14.",
    ]

    cache = json.loads((pb.CACHE_DIR / "my-records.json").read_text())
    assert cache[str(f)]["kind"] == "dashboard"
    assert cache[str(f)]["status"] == "active"
    assert cache[str(f)]["as_of"] == "2026-09-14"
    assert cache[str(f)]["subject"] == "CLOV"
    assert cache[str(f)]["pass"] is True
    assert cache[str(f)]["labels_ok"] is True
    assert cache[str(f)]["labels_verdict"] == "SUPPORTED"
    assert cache[str(f)]["labels_confidence"] == 0.95

    confirm_call = calls[-1]
    assert confirm_call["sources"][0]["description"] == (
        "CLOV wheel campaign tracker. [kind: dashboard; status: active; as_of: 2026-09-14; subject: CLOV]")
    out = capsys.readouterr().out
    assert "PASS 0.95 | labels ok 0.95" in out


def test_stage1_fail_drops_file_after_one_retry_labels_never_gated(tmp_path, monkeypatch, capsys):
    """A description that never gates still gets exactly one rewrite retry, then the file is an
    exception -- the label gate (stage 2) must never even run for a file that failed stage 1."""
    root = tmp_path / "root"
    root.mkdir()
    f = root / "bad.md"
    f.write_text("# Bad\nContent that never gates.\n")

    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    writer_calls = []

    def fake_writer(items, model, feedback=None):
        writer_calls.append((items, feedback))
        return {str(f): {"path": str(f), "description": "A draft.", "question": "What is bad?",
                          "kind": "dashboard", "status": "active", "as_of": "2026-09-14", "subject": "Bad"}}

    monkeypatch.setattr(pb, "writer", fake_writer)

    gate_calls = []

    def fake_gate(claim, path):
        gate_calls.append(claim)
        return {"state": "NOT_SUPPORTED", "confidence": 0.4, "secs": 0.1}

    monkeypatch.setattr(pb, "gate", fake_gate)

    memory_calls = []
    monkeypatch.setattr(pb, "memory", lambda req: memory_calls.append(req) or {"status": "should-not-be-called"})

    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--no-findability"]))
    rc = pb.main()

    assert rc == 1  # nothing connected is a failure
    assert len(writer_calls) == 2       # initial batch draft + exactly one rewrite retry
    assert len(gate_calls) == 2         # description gate on the draft, then on the retry -- never a labels call
    assert memory_calls == []           # connect_set stayed empty; memory is never invoked

    cache = json.loads((pb.CACHE_DIR / "my-records.json").read_text())
    assert cache[str(f)]["pass"] is False
    assert cache[str(f)]["kind"] == "unknown"
    assert cache[str(f)]["labels_ok"] is False
    assert "labels_verdict" not in cache[str(f)]


def test_stage2_under_line_connects_file_with_labels_unknown(tmp_path, monkeypatch, capsys):
    """Stage 1 (description) passes but stage 2 (labels) lands under the confidence line: the
    file must still connect, with all four labels reset to unknown and the verdict recorded."""
    root = tmp_path / "root"
    root.mkdir()
    f = root / "one.md"
    f.write_text("# One\nCLOV campaign, not yet filed, as of 2026-09-14.\n")

    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: {
        str(f): {"path": str(f), "description": "CLOV wheel campaign tracker.", "question": "What is CLOV status?",
                  "kind": "dashboard", "status": "active", "as_of": "2026-09-14", "subject": "CLOV"}
    })

    def fake_gate(claim, path):
        if claim.startswith("This file is a"):
            return {"state": "SUPPORTED", "confidence": 0.44, "secs": 0.1}  # under the 0.80 line
        return {"state": "SUPPORTED", "confidence": 0.95, "secs": 0.1}

    monkeypatch.setattr(pb, "gate", fake_gate)

    calls = []
    monkeypatch.setattr(pb, "memory", fake_connect_memory(calls))

    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--no-findability"]))
    rc = pb.main()

    assert rc == 0
    cache = json.loads((pb.CACHE_DIR / "my-records.json").read_text())
    entry = cache[str(f)]
    assert entry["pass"] is True
    assert entry["kind"] == "unknown" and entry["status"] == "unknown"
    assert entry["as_of"] == "unknown" and entry["subject"] == "unknown"
    assert entry["labels_ok"] is False
    assert entry["labels_verdict"] == "SUPPORTED"
    assert entry["labels_confidence"] == 0.44

    # the file still connects, on its plain description (no brackets)
    confirm_call = calls[-1]
    assert confirm_call["sources"][0]["description"] == "CLOV wheel campaign tracker."
    out = capsys.readouterr().out
    assert "PASS 0.95 | labels unknown (0.44)" in out


def test_stage2_contradicted_connects_file_with_labels_unknown(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    root.mkdir()
    f = root / "one.md"
    f.write_text("# One\nCLOV campaign.\n")

    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: {
        str(f): {"path": str(f), "description": "CLOV wheel campaign tracker.", "question": "What is CLOV status?",
                  "kind": "dashboard", "status": "closed", "as_of": "2026-09-14", "subject": "CLOV"}
    })

    def fake_gate(claim, path):
        if claim.startswith("This file is a"):
            return {"state": "CONTRADICTED", "confidence": 0.9, "secs": 0.1}
        return {"state": "SUPPORTED", "confidence": 0.9, "secs": 0.1}

    monkeypatch.setattr(pb, "gate", fake_gate)

    calls = []
    monkeypatch.setattr(pb, "memory", fake_connect_memory(calls))

    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--no-findability"]))
    rc = pb.main()

    assert rc == 0
    cache = json.loads((pb.CACHE_DIR / "my-records.json").read_text())
    entry = cache[str(f)]
    assert entry["pass"] is True
    assert entry["status"] == "unknown"
    assert entry["labels_ok"] is False
    assert entry["labels_verdict"] == "CONTRADICTED"

    confirm_call = calls[-1]
    assert confirm_call["sources"][0]["description"] == "CLOV wheel campaign tracker."


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


def _write_manual_record(state_dir, principal, name, kind, status, as_of, subject, body="The answer.\n"):
    manual_dir = state_dir / principal / "manual"
    manual_dir.mkdir(parents=True, exist_ok=True)
    p = manual_dir / f"{name}.md"
    p.write_text(
        f"# some question\n\nkind: {kind}\nstatus: {status}\nas_of: {as_of}\nsubject: {subject}\n"
        f"project: {principal}\n\n{body}\nrecorded: 2026-09-21 00:00 UTC\n"
    )
    return p


def test_manual_label_rows_reads_headers_and_filters_like_list_cmd(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(state_dir))
    today = pb.date.today().isoformat()
    _write_manual_record(state_dir, "agent", "agent-manual-aaa", "record", "active", today, "taxes 2025")
    _write_manual_record(state_dir, "agent", "agent-manual-bbb", "index", "closed", "2020-01-01", "old stuff")
    _write_manual_record(state_dir, "agent", "agent-manual-ccc", "record", "active", "unknown", "no date fact")

    rows, excluded = pb.manual_label_rows("agent")
    paths = {r[4] for r in rows}
    assert paths == {"agent-manual-aaa", "agent-manual-bbb", "agent-manual-ccc"}

    rows2, excluded2 = pb.manual_label_rows("agent", status="active")
    assert {r[4] for r in rows2} == {"agent-manual-aaa", "agent-manual-ccc"}

    rows3, excluded3 = pb.manual_label_rows("agent", within_days=30)
    assert {r[4] for r in rows3} == {"agent-manual-aaa"}
    assert excluded3 == 1  # ccc's as_of is unknown


def test_manual_label_rows_no_manual_dir_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "nowhere"))
    rows, excluded = pb.manual_label_rows("agent")
    assert rows == [] and excluded == 0


def test_list_with_principal_only_shows_manual_records(tmp_path, monkeypatch, capsys):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    state_dir = tmp_path / "state"
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(state_dir))
    _write_manual_record(state_dir, "agent", "agent-manual-xyz", "pointer", "active", "2026-09-01", "super-jev")

    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--list", "--principal", "agent"])
    rc = pb.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "agent-manual-xyz" in out
    assert "super-jev" in out


def test_list_with_pointer_and_principal_merges_both_sources(tmp_path, monkeypatch, capsys):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    (cache_dir / "my-records.json").write_text(json.dumps({
        "/a/one.md": {"pass": True, "kind": "dashboard", "status": "active", "as_of": "2026-01-01", "subject": "One"},
    }))
    state_dir = tmp_path / "state"
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(state_dir))
    _write_manual_record(state_dir, "agent", "agent-manual-abc", "record", "active", "2026-02-01", "Two")

    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--list", "--pointer", "my-records", "--principal", "agent"])
    rc = pb.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "/a/one.md" in out
    assert "agent-manual-abc" in out


def test_list_neither_pointer_nor_principal_is_usage_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--list"])
    rc = pb.main()
    assert rc == 2
    assert "REFUSED" in capsys.readouterr().out


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


def test_inventory_name_keeps_only_skill_entry_files(tmp_path):
    root = tmp_path / "skills"
    (root / "ebay-return-label" / "reference").mkdir(parents=True)
    (root / "ebay-return-label" / "SKILL.md").write_text("# eBay return label\nPrint it.\n")
    (root / "ebay-return-label" / "reference" / "notes.md").write_text("# Notes\nDetail.\n")

    files, held = pb.inventory([root], names=["SKILL.md"])

    assert [p.relative_to(root).as_posix() for p in files] == ["ebay-return-label/SKILL.md"]

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


def _seed_cache(root, cache_dir, n, pointer="my-records"):
    cache_dir.mkdir(exist_ok=True)
    entries = {}
    for i in range(n):
        f = root / f"f{i}.md"
        entries[str(f)] = {"sha256": sha256_of(f), "description": f"Describes f{i}.", "question": f"What is f{i}?",
                           "verdict": "SUPPORTED", "confidence": 0.95, "pass": True, "checkedAt": "2026-01-01T00:00:00"}
    (cache_dir / f"{pointer}.json").write_text(json.dumps(entries))


def _big_root(tmp_path, n):
    root = tmp_path / "root"
    root.mkdir()
    for i in range(n):
        (root / f"f{i}.md").write_text(f"# F{i}\nContent {i}.\n")
    return root


def test_refresh_over_max_files_passes_when_cache_covers_unchanged_files(tmp_path, monkeypatch, capsys):
    root = _big_root(tmp_path, 5)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    _seed_cache(root, cache_dir, 5)
    writer_calls = []
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: writer_calls.append(items) or {})
    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--max-files", "2", "--refresh", "--no-connect"]))
    rc = pb.main()
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "REFUSED" not in out
    assert "refresh: 5 files total (over --max-files 2), but only 0 need a writer call" in out
    assert writer_calls == []


def test_refresh_over_max_files_refuses_when_changed_files_exceed_cap(tmp_path, monkeypatch, capsys):
    root = _big_root(tmp_path, 5)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    _seed_cache(root, cache_dir, 5)
    for i in range(3):  # 3 changed files > cap 2
        (root / f"f{i}.md").write_text(f"# F{i}\nChanged {i}.\n")
    monkeypatch.setattr(pb, "writer", lambda *a, **k: (_ for _ in ()).throw(AssertionError("writer must not run")))
    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--max-files", "2", "--refresh", "--no-connect"]))
    rc = pb.main()
    out = capsys.readouterr().out
    assert rc == 2
    assert "REFUSED: 3 files need drafting" in out


def test_refresh_without_cache_keeps_first_connect_refusal(tmp_path, monkeypatch, capsys):
    root = _big_root(tmp_path, 3)
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--max-files", "2", "--refresh", "--no-connect"]))
    rc = pb.main()
    out = capsys.readouterr().out
    assert rc == 2
    assert "REFUSED: 3 files exceed --max-files 2" in out


def test_refresh_replays_recorded_no_recurse_and_excludes(tmp_path, monkeypatch, capsys):
    # Fleet 2026-09-24: a hand --refresh without --no-recurse grew tools/ to 634 files.
    root = _big_root(tmp_path, 2)
    (root / "sub").mkdir(); (root / "sub" / "deep.md").write_text("# Deep\n")
    (root / "INDEX.md").write_text("# Index\n")
    (root / "pw.md").write_text("# Creds\nthe password for the router is hunter2\n")
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    _seed_cache(root, cache_dir, 2)
    (cache_dir / "my-records-report.json").write_text(json.dumps(
        {"pointer": "my-records", "roots": [str(root)], "principals": ["alice"],
         "excludes": ["INDEX.md"], "noRecurse": True, "limit": 20}))
    monkeypatch.setattr(pb, "writer", lambda *a, **k: (_ for _ in ()).throw(AssertionError("writer must not run")))
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--pointer", "my-records", "--principal", "alice",
                                      "--refresh", "--no-connect", "--no-findability"])
    rc = pb.main()
    out = capsys.readouterr().out
    assert rc == 3, out  # nothing failed; the password file stays held
    assert "inventory: 2 files to prepare, 1 held" in out  # the held file has no override
    report = json.loads((cache_dir / "my-records-report.json").read_text())
    assert report["noRecurse"] is True and report["excludes"] == ["INDEX.md"] and report["limit"] == 20


def test_refresh_of_legacy_report_keeps_recorded_file_set(tmp_path, monkeypatch, capsys):
    # Fleet 2026-09-24 (health-fitness-reuse): a report written before recipes were recorded has
    # no excludes/noRecurse/limit, so the replay printed the default "limit 50" (part size, not a cap)
    # and inventoried the whole grown root -- 505 new drafts, REFUSED over --max-files. The recorded
    # file list is the pointer's scope; refresh must not widen it.
    root = _big_root(tmp_path, 6)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    _seed_cache(root, cache_dir, 2)
    (cache_dir / "my-records-report.json").write_text(json.dumps(
        {"pointer": "my-records", "roots": [str(root)], "principal": "alice",
         "approved": [str(root / "f0.md"), str(root / "f1.md")], "exceptions": [], "held": []}))
    monkeypatch.setattr(pb, "writer", lambda *a, **k: (_ for _ in ()).throw(AssertionError("writer must not run")))
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--pointer", "my-records", "--principal", "alice",
                                      "--refresh", "--max-files", "1", "--no-connect", "--no-findability"])
    rc = pb.main()
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "inventory: 2 files to prepare" in out
    assert "REFUSED" not in out
    # The rewritten report now carries a recipe; the pinned list must survive into the next refresh,
    # including a refresh typed by hand that repeats the recorded root.
    monkeypatch.setattr(sys, "argv", sys.argv + ["--root", str(root)])
    assert pb.main() == 0
    out = capsys.readouterr().out
    assert "inventory: 2 files to prepare" in out and "REFUSED" not in out
    # A hand refresh that repeats the recorded excludes: an unchanged exclude list must not unpin.
    rep = json.loads((cache_dir / "my-records-report.json").read_text())
    rep["excludes"] = ["INDEX.md"]
    (cache_dir / "my-records-report.json").write_text(json.dumps(rep))
    monkeypatch.setattr(sys, "argv", sys.argv + ["--exclude", "INDEX.md"])
    assert pb.main() == 0
    out = capsys.readouterr().out
    assert "inventory: 2 files to prepare" in out and "REFUSED" not in out


def test_refresh_of_legacy_report_admits_new_files_in_its_folders_with_every_check(tmp_path, monkeypatch, capsys):
    # Fleet 2026-09 (amazon-bm-fb, helper): notes written into already connected folders were never
    # found, since a legacy report is pinned to its file list. A new file in a pinned folder joins it,
    # through the same secret scan, writer review and gate; one in a folder the list never had does not.
    root = _big_root(tmp_path, 2)
    (root / "new-note.md").write_text("# New note\nThe supplier is Acme.\n")
    (root / "pw.md").write_text("# Creds\nthe password for the router is hunter2\n")
    (root / "sub").mkdir(); (root / "sub" / "elsewhere.md").write_text("# Elsewhere\n")
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    _seed_cache(root, cache_dir, 2)
    (cache_dir / "my-records-report.json").write_text(json.dumps(
        {"pointer": "my-records", "roots": [str(root)], "principal": "alice",
         "approved": [str(root / "f0.md"), str(root / "f1.md")], "exceptions": [], "held": []}))
    _growth_snapshot(cache_dir)  # the folder held nothing outside the list at the snapshot
    drafted = []
    monkeypatch.setattr(pb, "writer", lambda items, *a, **k: drafted.extend(i["path"] for i in items) or
                        {i["path"]: {"description": "A note.", "question": "q?"} for i in items})
    monkeypatch.setattr(pb, "gate", lambda path, claim, *a, **k: {"state": "SUPPORTED", "confidence": 0.95, "secs": 0})
    monkeypatch.setattr(pb, "gate_many", lambda path, claims, *a, **k: [{"state": "SUPPORTED", "confidence": 0.95, "secs": 0} for _ in claims], raising=False)
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--pointer", "my-records", "--principal", "alice",
                                      "--refresh", "--no-connect", "--no-findability"])
    assert pb.main() == 3  # a held file is still listed as held
    out = capsys.readouterr().out
    assert "refresh: 2 new file(s) in already-connected folders join the pinned list" in out
    assert "inventory: 3 files to prepare, 1 held" in out
    assert drafted == [str(root / "new-note.md")]
    report = json.loads((cache_dir / "my-records-report.json").read_text())
    assert str(root / "new-note.md") in report["approved"]
    assert [h[0] for h in report["held"]] == [str(root / "pw.md")]  # held, never approved
    assert str(root / "sub" / "elsewhere.md") not in report["scopeFiles"]
    assert str(root / "new-note.md") in report["scopeFiles"]  # stays in scope on the next refresh


def test_legacy_refresh_never_grows_into_a_folder_another_pointer_shares(tmp_path, monkeypatch, capsys):
    # Review 2026-09-28: two pointers pinned in one folder ("team" for alice+bob, "private" for alice);
    # growing "team" must not move private.md, nor a new note alice wrote there, to where bob sees it.
    root = _big_root(tmp_path, 2)
    (root / "private.md").write_text("# Private\nalice only\n")
    (root / "new-note.md").write_text("# New note\n")
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    _seed_cache(root, cache_dir, 2, pointer="team")
    (cache_dir / "team-report.json").write_text(json.dumps(
        {"pointer": "team", "roots": [str(root)], "approved": [str(root / "f0.md"), str(root / "f1.md")]}))
    (cache_dir / "private-report.json").write_text(json.dumps(
        {"pointer": "private", "roots": [str(root)], "approved": [str(root / "private.md")]}))
    monkeypatch.setattr(pb, "writer", lambda items, *a, **k: {i["path"]: {"description": "A note.", "question": "q?"} for i in items})
    monkeypatch.setattr(pb, "gate", lambda path, claim, *a, **k: {"state": "SUPPORTED", "confidence": 0.95, "secs": 0})
    monkeypatch.setattr(pb, "gate_many", lambda path, claims, *a, **k: [{"state": "SUPPORTED", "confidence": 0.95, "secs": 0} for _ in claims], raising=False)
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--pointer", "team", "--principal", "bob",
                                      "--refresh", "--no-connect", "--no-findability"])
    assert pb.main() == 0
    assert "join the pinned list" not in capsys.readouterr().out
    report = json.loads((cache_dir / "team-report.json").read_text())
    for f in ("new-note.md", "private.md"):
        assert str(root / f) not in report["approved"] + report["scopeFiles"]


def test_a_new_file_whose_connect_failed_stays_new_until_it_connects(tmp_path, monkeypatch, capsys):
    # Review 2026-09-28: the service never saw the file, so nothing else would mark its pointer stale.
    import refresh_changed as rcm
    root = _big_root(tmp_path, 1)
    (root / "new-note.md").write_text("# New note\n")
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    _seed_cache(root, cache_dir, 1)
    (cache_dir / "my-records-report.json").write_text(json.dumps(
        {"pointer": "my-records", "roots": [str(root)], "approved": [str(root / "f0.md")]}))
    _growth_snapshot(cache_dir)
    monkeypatch.setattr(pb, "writer", lambda items, *a, **k: {i["path"]: {"description": "A note.", "question": "q?"} for i in items})
    monkeypatch.setattr(pb, "gate", lambda path, claim, *a, **k: {"state": "SUPPORTED", "confidence": 0.95, "secs": 0})
    monkeypatch.setattr(pb, "gate_many", lambda path, claims, *a, **k: [{"state": "SUPPORTED", "confidence": 0.95, "secs": 0} for _ in claims], raising=False)
    monkeypatch.setattr(pb, "connect_part", lambda *a, **k: {"connected": False})
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--pointer", "my-records", "--principal", "alice",
                                      "--refresh", "--no-findability", "--no-shared"])
    monkeypatch.setattr(rcm, "CACHE_DIR", cache_dir)
    pb.main()
    capsys.readouterr()
    report = json.loads((cache_dir / "my-records-report.json").read_text())
    assert report["unconnectedNew"] == [str(root / "new-note.md")]
    assert rcm.new_files(report, rcm.known_files(report)) == [str(root / "new-note.md")]
    pb.main()  # fails again: already in the pinned list now, and still kept for the retry
    capsys.readouterr()
    report = json.loads((cache_dir / "my-records-report.json").read_text())
    assert report["unconnectedNew"] == [str(root / "new-note.md")] and report["unconnectedTries"] == 2
    assert rcm.new_files(report, rcm.known_files(report)) == [str(root / "new-note.md")]
    pb.main()  # a connect that fails every time stops being retried, so it cannot eat the heal budget
    capsys.readouterr()
    report = json.loads((cache_dir / "my-records-report.json").read_text())
    assert report["unconnectedTries"] == 3 and rcm.new_files(report, rcm.known_files(report)) == []
    monkeypatch.setattr(pb, "connect_part", lambda *a, **k: {"connected": True})
    pb.main()
    capsys.readouterr()
    report = json.loads((cache_dir / "my-records-report.json").read_text())
    assert "unconnectedNew" not in report and rcm.new_files(report, rcm.known_files(report)) == []


def _growth_snapshot(cache_dir, pointer="my-records", present=(), since=0):
    d = cache_dir / "growth"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{pointer}.json").write_text(json.dumps({"since": since, "present": [str(p) for p in present]}))


def test_legacy_refresh_takes_in_nothing_that_was_on_disk_at_its_first_look(tmp_path, monkeypatch, capsys):
    # Review of PR #239: a legacy report never recorded its --exclude, and neither a connect time nor
    # a birth time (reset by a write-then-rename save) tells a file left out on purpose from a new one.
    # So the first look records every unlisted file ("present"); their folder waits for a person.
    root = _big_root(tmp_path, 2)
    (root / "journal-2026-08.md").write_text("# August\n")  # left out with --exclude 'journal-*'
    (root / "links.md").write_text("# Links\n")  # auto-rebuilt: never grows and never blocks
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    _seed_cache(root, cache_dir, 2)
    (cache_dir / "my-records-report.json").write_text(json.dumps(
        {"pointer": "my-records", "roots": [str(root)], "approved": [str(root / "f0.md"), str(root / "f1.md")]}))
    drafted = []
    monkeypatch.setattr(pb, "writer", lambda items, *a, **k: drafted.extend(i["path"] for i in items) or
                        {i["path"]: {"description": "A note.", "question": "q?"} for i in items})
    monkeypatch.setattr(pb, "gate", lambda path, claim, *a, **k: {"state": "SUPPORTED", "confidence": 0.95, "secs": 0})
    monkeypatch.setattr(pb, "gate_many", lambda path, claims, *a, **k: [{"state": "SUPPORTED", "confidence": 0.95, "secs": 0} for _ in claims], raising=False)
    argv = ["prepare_bulk.py", "--pointer", "my-records", "--principal", "alice", "--refresh", "--no-connect",
            "--no-findability"]
    monkeypatch.setattr(sys, "argv", argv)
    assert pb.main() == 0
    out = capsys.readouterr().out
    assert "join the pinned list" not in out and f"WAITING  {root / 'journal-2026-08.md'}" in out
    snap = json.loads((cache_dir / "growth" / "my-records.json").read_text())
    assert snap["present"] == [str(root / "journal-2026-08.md")]
    time.sleep(0.02)
    (root / "journal-2026-09.md").write_text("# September\n")  # matches the unrecorded exclude
    tmp = root / "f1.tmp"; tmp.write_text("# F1\nsaved again\n"); os.replace(tmp, root / "f1.md")
    assert pb.main() == 0
    assert "join the pinned list" not in capsys.readouterr().out and drafted == [str(root / "f1.md")]
    # A person checks the waiting file and admits it: it joins through the usual review, and from
    # then on the folder takes in new notes on its own.
    monkeypatch.setattr(sys, "argv", argv + ["--admit", str(root / "journal-2026-08.md")])
    assert pb.main() == 0
    assert "1 new file(s) in already-connected folders join" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", argv)
    assert pb.main() == 0
    assert "1 new file(s) in already-connected folders join" in capsys.readouterr().out  # journal-2026-09.md
    report = json.loads((cache_dir / "my-records-report.json").read_text())
    assert {str(root / "journal-2026-08.md"), str(root / "journal-2026-09.md")} <= set(report["approved"])
    assert str(root / "links.md") not in report["scopeFiles"]
    assert "pinnedSince" not in report and "leftOut" not in report


def test_admit_only_takes_a_file_the_inventory_admits(tmp_path, monkeypatch, capsys):
    root = _big_root(tmp_path, 1)
    (root / "pw.md").write_text("# Creds\nthe password for the router is hunter2\n")
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    _seed_cache(root, cache_dir, 1)
    (cache_dir / "my-records-report.json").write_text(json.dumps(
        {"pointer": "my-records", "roots": [str(root)], "approved": [str(root / "f0.md")]}))
    monkeypatch.setattr(pb, "writer", lambda *a, **k: (_ for _ in ()).throw(AssertionError("writer must not run")))
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--pointer", "my-records", "--principal", "alice",
                                      "--refresh", "--no-connect", "--no-findability",
                                      "--admit", str(root / "pw.md"), "--admit", str(tmp_path / "elsewhere.md")])
    assert pb.main() == 3
    out = capsys.readouterr().out
    assert f"--admit {tmp_path / 'elsewhere.md'}: not a WAITING file" in out
    report = json.loads((cache_dir / "my-records-report.json").read_text())
    assert str(root / "pw.md") not in report["approved"]  # admitted to scope, but still held by the scan
    assert [h[0] for h in report["held"]] == [str(root / "pw.md")]


def test_admit_refuses_a_file_that_is_not_waiting(tmp_path, monkeypatch, capsys):
    # Review 6: --admit took any inventoried file, e.g. one another pointer connects.
    root = _big_root(tmp_path, 1)
    (root / "private.md").write_text("# Private\n")
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    _seed_cache(root, cache_dir, 1, pointer="team")
    (cache_dir / "team-report.json").write_text(json.dumps(
        {"pointer": "team", "roots": [str(root)], "approved": [str(root / "f0.md")]}))
    (cache_dir / "private-report.json").write_text(json.dumps(
        {"pointer": "private", "roots": [str(tmp_path / "other")], "approved": [str(root / "private.md")]}))
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--pointer", "team", "--principal", "bob", "--refresh",
                                      "--no-connect", "--no-findability", "--admit", str(root / "private.md")])
    assert pb.main() == 0
    assert "not a WAITING file" in capsys.readouterr().out
    assert str(root / "private.md") not in json.loads((cache_dir / "team-report.json").read_text())["scopeFiles"]


def test_a_note_written_during_the_first_walk_still_counts_as_new(tmp_path, monkeypatch):
    # Review 6: "since" was taken after the walk, so a note written mid-walk was neither waiting nor new.
    root = _big_root(tmp_path, 1)
    snap = pb.take_snapshot("p", {str(root / "f0.md")}, [], set(), tmp_path / "cache", since=time.time() - 5)
    (root / "mid-walk.md").write_text("# Written while the folder was walked\n")
    assert pb.growth({str(root / "f0.md")}, [root / "mid-walk.md"], snap, set()) == [str(root / "mid-walk.md")]


def test_growth_sees_a_folder_shared_through_a_symlink(tmp_path):
    # Review 2026-09-28: "team" pinned the folder through a link, "private" through its real path.
    real = tmp_path / "real"; real.mkdir()
    (tmp_path / "link").symlink_to(real)
    for f in ("a.md", "b.md", "new.md"):
        (real / f).write_text(f"# {f}\n")
    team, snap = {str(tmp_path / "link" / "a.md")}, {"since": 0, "present": []}
    assert pb.growth(team, [tmp_path / "link" / "new.md"], snap, set()) == [str(tmp_path / "link" / "new.md")]
    assert pb.growth(team, [tmp_path / "link" / "new.md"], snap, {str(real / "b.md")}) == []


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


def test_secret_scan_ignores_url_digits_but_still_holds_real_card_number(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "url.md").write_text(
        "# Report\nSee https://example.com/file/1234567890123456 for details.\n"
    )
    (root / "card.md").write_text("# Card\nCard on file: 4000 0566 5566 5556\n")

    files, held = pb.inventory([root])

    assert {p.name for p in files} == {"url.md"}
    held_by_name = {Path(p).name: why for p, why in held}
    assert set(held_by_name) == {"card.md"}
    assert "card/password" in held_by_name["card.md"]


def test_secret_scan_ignores_iso_dates_in_card_check(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "dates.md").write_text(
        "# Log\nEntries: 2026-09-14 2026-09-15 2026-09-16 2026-09-17\n"
    )

    files, held = pb.inventory([root])

    assert {p.name for p in files} == {"dates.md"}
    assert held == []


def test_secret_scan_keyword_rule_unaffected_by_date_or_url_scrub(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "pw.md").write_text(
        "# Notes\nSee https://example.com/setup as of 2026-09-14: the password is hunter2\n"
    )

    files, held = pb.inventory([root])

    assert files == []
    held_by_name = {Path(p).name: why for p, why in held}
    assert "card/password" in held_by_name["pw.md"]


def test_secret_looking_filename_is_held_even_with_clean_content(tmp_path):
    """A file name like password-hunter2xyz-notes.md never lands in key=value/
    key:value/'is' shape, so the content-only WORD_RE keyword scan misses it --
    but the file name itself should still hold the file at connect time."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "password-hunter2xyz-notes.md").write_text("# Notes\nNothing secret in here.\n")

    files, held = pb.inventory([root])

    assert files == []
    held_by_name = {Path(p).name: why for p, why in held}
    assert "secret-keyword-like file name" in held_by_name["password-hunter2xyz-notes.md"]


def test_normal_filename_not_held(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "tokenizer-notes.md").write_text("# Notes\nNothing secret in here.\n")

    files, held = pb.inventory([root])

    assert {p.name for p in files} == {"tokenizer-notes.md"}
    assert held == []


def test_writer_banner_shows_default_and_recommends_cheap_model(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    root.mkdir()
    f = root / "one.md"
    f.write_text("# One\nContent about one.\n")

    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.delenv("SUPERJEV_WRITER_COMMAND", raising=False)
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: {
        str(f): {"path": str(f), "description": "Describes one.", "question": "What is one?"}
    })
    monkeypatch.setattr(pb, "gate", lambda desc, path: {"state": "SUPPORTED", "confidence": 0.9, "secs": 0.1})

    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--no-connect"]))
    rc = pb.main()

    assert rc == 0
    out = capsys.readouterr().out
    assert "writer: claude -p --model haiku" in out
    assert "cheap writer model" in out
    assert "Haiku" in out


def test_writer_banner_shows_explicit_writer_command_no_recommendation(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    root.mkdir()
    (root / "one.md").write_text("# One\nContent about one.\n")

    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None, command=None: {
        str(root / "one.md"): {"path": str(root / "one.md"), "description": "Describes one.", "question": "What is one?"}
    })
    monkeypatch.setattr(pb, "gate", lambda desc, path: {"state": "SUPPORTED", "confidence": 0.9, "secs": 0.1})

    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--writer-command", "my-writer --flag", "--no-connect"]))
    rc = pb.main()

    assert rc == 0
    out = capsys.readouterr().out
    assert "writer: my-writer --flag" in out
    assert "cheap writer model" not in out


def test_writer_banner_reads_env_var_when_flag_omitted(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    root.mkdir()
    (root / "one.md").write_text("# One\nContent about one.\n")

    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("SUPERJEV_WRITER_COMMAND", "env-writer --opt")
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None, command=None: {
        str(root / "one.md"): {"path": str(root / "one.md"), "description": "Describes one.", "question": "What is one?"}
    })
    monkeypatch.setattr(pb, "gate", lambda desc, path: {"state": "SUPPORTED", "confidence": 0.9, "secs": 0.1})

    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--no-connect"]))
    rc = pb.main()

    assert rc == 0
    out = capsys.readouterr().out
    assert "writer: env-writer --opt" in out
    assert "cheap writer model" not in out


def _writer_notes(tmp_path, monkeypatch):
    """Two made-up notes, a tmp cache, the gate and the env var faked; claude counts as installed."""
    root = tmp_path / "notes"
    root.mkdir()
    (root / "parking.md").write_text("# Parking\nVisitors park in lot C.\n")
    (root / "travel.md").write_text("# Travel\nThe nightly hotel limit is 180 dollars.\n")
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.delenv("SUPERJEV_WRITER_COMMAND", raising=False)
    monkeypatch.setattr(pb, "gate", lambda desc, path: {"state": "SUPPORTED", "confidence": 0.9, "secs": 0.1})
    return root


def _fake_model_writer(monkeypatch, fail=False):
    """Records the command each model-writer call got (None = the default claude CLI). With fail=True it
    fails like a claude CLI that is installed but not logged in."""
    calls = []

    def fake(items, model, feedback=None, command=None):
        calls.append(command)
        if fail:
            raise pb.WriterError("writer exited with status 1")
        return {i["path"]: {"path": i["path"], "description": "Describes it.", "question": "What?"} for i in items}

    monkeypatch.setattr(pb, "writer", fake)
    return calls


def _prepare(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", *argv])
    return pb.main()


def _connect_notes(monkeypatch, root, *flags):
    return _prepare(monkeypatch, "--root", str(root), "--pointer", "notes", "--principal", "alice",
                    *flags, "--no-connect")


def _refresh_like_the_callers(monkeypatch, *given):
    """The refresh auto-heal starts, refresh_changed.py runs and ask prints: rc.prepare_args(report)."""
    report = json.loads((pb.CACHE_DIR / "notes-report.json").read_text())
    return _prepare(monkeypatch, *rc.prepare_args(report), *given, "--no-connect")


def test_refresh_replays_the_builtin_writer_it_was_connected_with(tmp_path, monkeypatch, capsys):
    # Trial 2026-09-30: a folder connected with --writer builtin was healed with the claude CLI,
    # and the refresh failed because claude was installed but not logged in.
    root = _writer_notes(tmp_path, monkeypatch)
    calls = _fake_model_writer(monkeypatch, fail=True)
    assert _connect_notes(monkeypatch, root, "--writer", "builtin") == 0
    (root / "parking.md").write_text("# Parking\nVisitors park in lot D.\n")
    capsys.readouterr()
    assert _refresh_like_the_callers(monkeypatch) == 0, capsys.readouterr().out
    assert calls == []  # the model writer was never handed the notes
    assert "writer: builtin" in capsys.readouterr().out


def test_refresh_replays_a_writer_command(tmp_path, monkeypatch):
    root = _writer_notes(tmp_path, monkeypatch)
    calls = _fake_model_writer(monkeypatch)
    assert _connect_notes(monkeypatch, root, "--writer-command", "local-writer --small") == 0
    (root / "parking.md").write_text("# Parking\nVisitors park in lot D.\n")
    assert _refresh_like_the_callers(monkeypatch) == 0
    assert calls == [["local-writer", "--small"]] * 2  # the connect, then the refresh, same command


def test_a_writer_given_on_refresh_replaces_the_recorded_one_as_a_whole(tmp_path, monkeypatch):
    root = _writer_notes(tmp_path, monkeypatch)
    calls = _fake_model_writer(monkeypatch)
    assert _connect_notes(monkeypatch, root, "--writer", "builtin") == 0
    (root / "parking.md").write_text("# Parking\nVisitors park in lot D.\n")
    assert _refresh_like_the_callers(monkeypatch, "--writer-command", "other-writer") == 0
    assert calls == [["other-writer"]]  # the recorded builtin does not override the writer just given
    (root / "parking.md").write_text("# Parking\nVisitors park in lot E.\n")
    assert _refresh_like_the_callers(monkeypatch) == 0
    assert calls == [["other-writer"]] * 2  # and the new choice is what later refreshes replay


def test_a_rescoped_refresh_keeps_the_recorded_writer(tmp_path, monkeypatch, capsys):
    # A rescope (here --exclude) changes the folders and filters; it must not change who writes.
    root = _writer_notes(tmp_path, monkeypatch)
    calls = _fake_model_writer(monkeypatch, fail=True)
    assert _connect_notes(monkeypatch, root, "--writer", "builtin") == 0
    (root / "parking.md").write_text("# Parking\nVisitors park in lot D.\n")
    capsys.readouterr()
    assert _refresh_like_the_callers(monkeypatch, "--exclude", "travel.md") == 0, capsys.readouterr().out
    assert calls == []
    assert "writer: builtin" in capsys.readouterr().out
    report = json.loads((pb.CACHE_DIR / "notes-report.json").read_text())
    assert report["excludes"] == ["travel.md"] and report["writer"] == "builtin"


def test_the_replay_line_names_the_writer_that_runs(tmp_path, monkeypatch, capsys):
    # A --writer-command user who follows the EXCEPTION hint records builtin plus their command; builtin
    # is what runs (main picks it first), so it is what the visibility line must say.
    root = _writer_notes(tmp_path, monkeypatch)
    _fake_model_writer(monkeypatch)
    assert _connect_notes(monkeypatch, root, "--writer-command", "local-writer --small", "--writer", "builtin") == 0
    (root / "parking.md").write_text("# Parking\nVisitors park in lot D.\n")
    capsys.readouterr()
    assert _refresh_like_the_callers(monkeypatch) == 0
    out = capsys.readouterr().out
    assert "writer builtin)" in out and "local-writer" not in out
    assert "writer: builtin" in out


def test_a_refresh_keeps_the_recorded_name_filter_and_allow_target(tmp_path, monkeypatch):
    # The replayed recipe, not the command line, carries --name and --allow-target. A refresh that lost
    # them would widen a SKILL.md pointer to every note in the folder and drop the file its symlink reaches.
    root = _writer_notes(tmp_path, monkeypatch)  # holds parking.md and travel.md: not skill entry files
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "SKILL.md").write_text("# Skill B\nDoes B.\n")
    (root / "a").mkdir()
    (root / "a" / "SKILL.md").write_text("# Skill A\nDoes A.\n")
    (root / "a" / "notes.md").write_text("# Notes\nScratch.\n")
    (root / "b").mkdir()
    (root / "b" / "SKILL.md").symlink_to(elsewhere / "SKILL.md")
    _fake_model_writer(monkeypatch)

    def approved():
        report = json.loads((pb.CACHE_DIR / "notes-report.json").read_text())
        return sorted(Path(p).parent.name for p in report["approved"]), report

    assert _connect_notes(monkeypatch, root, "--name", "SKILL.md", "--allow-target", str(elsewhere)) == 0
    connected, _ = approved()
    assert connected == ["a", "b"]
    (root / "a" / "SKILL.md").write_text("# Skill A\nDoes A better.\n")
    assert _refresh_like_the_callers(monkeypatch) == 0
    refreshed, report = approved()
    assert refreshed == connected
    assert report["names"] == ["SKILL.md"] and report["allowTargets"] == [str(elsewhere.resolve())]


def test_a_report_with_no_recorded_writer_still_uses_the_default(tmp_path, monkeypatch):
    # Guard: a report written before the writer was recorded keeps `auto`; the writer is never inferred.
    root = _writer_notes(tmp_path, monkeypatch)
    calls = _fake_model_writer(monkeypatch)
    assert _connect_notes(monkeypatch, root) == 0
    report_path = pb.CACHE_DIR / "notes-report.json"
    report = json.loads(report_path.read_text())
    for k in [k for k in report if k.lower().startswith("writer")]:
        report.pop(k)
    report_path.write_text(json.dumps(report))
    (root / "parking.md").write_text("# Parking\nVisitors park in lot D.\n")
    assert _refresh_like_the_callers(monkeypatch) == 0
    assert calls[-1] is None  # auto: the claude CLI, as before


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


def test_new_root_drops_cache_entries_from_the_old_root(tmp_path, monkeypatch, capsys):
    # night-log 2026-09-26: after reconnecting a pointer from a /tmp stage to ~/.claude/skills,
    # the cache kept the 298 still-existing /tmp entries and word search returned them.
    old = tmp_path / "stage"
    old.mkdir()
    stale = old / "a.md"
    stale.write_text("# Old copy\n")
    root = tmp_path / "root"
    root.mkdir()
    kept = root / "a.md"
    kept.write_text("# A\nHere.\n")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    entry = lambda f: {"sha256": sha256_of(f), "description": "Describes a.", "question": "What is a?",
                       "verdict": "SUPPORTED", "confidence": 0.9, "pass": True, "checkedAt": "2026-01-01T00:00:00"}
    (cache_dir / "my-records.json").write_text(json.dumps({str(stale): entry(stale), str(kept): entry(kept)}))
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: (_ for _ in ()).throw(AssertionError("writer should not run")))
    monkeypatch.setattr(pb, "gate", lambda desc, path: (_ for _ in ()).throw(AssertionError("gate should not run")))

    monkeypatch.setattr(sys, "argv", base_argv(root, extra=["--refresh", "--no-connect"]))
    assert pb.main() == 0

    saved = json.loads((cache_dir / "my-records.json").read_text())
    assert list(saved) == [str(kept)]
    assert "1 entries outside the current scope dropped" in capsys.readouterr().out


def test_refresh_keeps_the_pointers_registered_principals(tmp_path, monkeypatch):
    """primary-reference stayed STALE after a refresh naming only `primary`: the harness
    refused the narrowed scope. The refresh now reconnects with every registered principal."""
    f = tmp_path / "a.md"
    f.write_text("# A\n")
    cache = {str(f): {"description": "a", "labels_ok": False}}
    sent = []

    def fake_memory(req):
        if req["action"] == "panel":
            return {"pointers": ["ref"]}
        sent.append(list(req["principals"]))
        if not req.get("reviewed"):
            return {"status": "preparation-required", "sources": [{"path": str(f), "sha256": "h"}]}
        if req["principals"] == ["primary"]:
            return {"status": "preparation-required", "reason": "scope-change",
                    "registeredPrincipals": ["primary", "primary-helper"]}
        return {"status": "registered", "pointer": "ref", "sources": [{}]}

    monkeypatch.setattr(pb, "memory", fake_memory)
    principals = ["primary"]
    assert pb.connect_part("ref", principals, [f], cache) == {"connected": True}
    assert sent[-1] == ["primary", "primary-helper"] and principals == ["primary", "primary-helper"]


def test_bench_datasets_are_never_inventoried(tmp_path):
    bench = tmp_path / "super-jev/.local/retrieval-datasets/health-selected-passages"
    bench.mkdir(parents=True)
    (bench / "p1.md").write_text("# Passage\ncopied text\n")
    assert pb.is_bench_dataset(str(bench / "p1.md"))
    assert not pb.inventory([bench])[0]
    assert not pb.is_bench_dataset("/Users/x/agents/health-fitness-brain/INDEX.md")


def test_connect_part_matches_a_symlinked_file_by_realpath(tmp_path, monkeypatch):
    """Night 2026-09-26: the in-place skills reconnect died with KeyError on
    marketplace-listing-pipeline/SKILL.md, a symlink into tools/; the preview echoed its target path."""
    target = tmp_path / "tools" / "SKILL.md"
    target.parent.mkdir()
    target.write_text("# Pipeline\n")
    link = tmp_path / "skills" / "marketplace-listing-pipeline" / "SKILL.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    cache = {str(link): {"description": "a", "labels_ok": False}}
    sent = []

    def fake_memory(req):
        if req["action"] == "panel":
            return {"pointers": []}
        sent.append(req)
        if not req.get("reviewed"):
            return {"status": "preparation-required", "sources": [{"path": str(target.resolve()), "sha256": "h"}]}
        return {"status": "registered", "pointer": "p", "sources": [{}]}

    monkeypatch.setattr(pb, "memory", fake_memory)
    assert pb.connect_part("p", ["primary"], [link], cache) == {"connected": True}
    assert sent[-1]["sources"][0]["sha256"] == "h"


def test_inventory_name_matches_case_insensitively(tmp_path):
    root = tmp_path / "skills"
    for d, n in (("a", "SKILL.md"), ("b", "skill.md"), ("c", "notes.md")):
        (root / d).mkdir(parents=True)
        (root / d / n).write_text(f"# {d}\nBody.\n")

    files, held = pb.inventory([root], names=["SKILL.md"])

    assert sorted(p.relative_to(root).as_posix() for p in files) == ["a/SKILL.md", "b/skill.md"]


def test_refresh_with_a_new_root_drops_the_old_no_recurse(tmp_path, monkeypatch):
    # night-log 2026-09-26: a --refresh onto a new --root kept the old root's noRecurse,
    # so nested skills/<name>/SKILL.md files were never inventoried.
    import argparse
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    (cache_dir / "my-records-report.json").write_text(json.dumps(
        {"pointer": "my-records", "roots": [str(tmp_path / "old")], "principals": ["alice"], "noRecurse": True}))

    def args(roots):
        return argparse.Namespace(pointer="my-records", roots=roots, principals=[], excludes=[], names=[],
                                  no_recurse=False, allow_targets=[], limit=None, extensions=None,
                                  writer=None, writer_model=None, writer_command=None)

    fresh = args([str(tmp_path / "skills")])
    pb.replay_recipe(fresh)
    assert fresh.no_recurse is False
    same = args(None)
    pb.replay_recipe(same)
    assert same.no_recurse is True


@pytest.mark.parametrize("target_rel", ["profile/notes.md", "vault/logins.md", "vault/api-secret.md"])
def test_inventory_skips_a_link_to_a_file_the_roots_would_refuse(tmp_path, target_rel):
    # PR #173 review: guards ran on the link path only, so skills/x/SKILL.md -> profile/... passed.
    home = tmp_path / "home"
    target = home / target_rel
    target.parent.mkdir(parents=True)
    target.write_text("# Private\nPrivate text.\n")
    root = home / "skills"
    (root / "x").mkdir(parents=True)
    (root / "x" / "SKILL.md").symlink_to(target)

    assert pb.inventory([root], names=["SKILL.md"])[0] == []
    # Allowing the target's folder does not lift the name/folder checks on the target.
    assert pb.inventory([root], names=["SKILL.md"], allow_targets=[home])[0] == []


def test_inventory_link_outside_roots_needs_allow_target(tmp_path):
    tools = tmp_path / "tools" / "pipeline"
    tools.mkdir(parents=True)
    (tools / "SKILL.md").write_text("# Pipeline\nSteps.\n")
    root = tmp_path / "skills"
    (root / "pipeline").mkdir(parents=True)
    (root / "pipeline" / "SKILL.md").symlink_to(tools / "SKILL.md")

    assert pb.inventory([root])[0] == []
    files = pb.inventory([root], allow_targets=[tmp_path / "tools"])[0]
    assert [p.relative_to(root).as_posix() for p in files] == ["pipeline/SKILL.md"]


def test_a_part_one_principal_cannot_see_still_refreshes_and_is_widened(tmp_path, monkeypatch):
    """primary-skills-catalog-4 stayed STALE: it was registered for primary only while its
    siblings also served polymarket. The refresh asked only polymarket (the first principal)
    whether the part existed, heard no, connected it fresh and hit already-connected every
    time. Real memory runtime: the part now refreshes in its own scope, then widens."""
    import importlib.util
    exp = Path(pb.__file__).resolve().parent.parent.parent / "experiments" / "verified-pointer-memory"
    spec = importlib.util.spec_from_file_location("cli_partscope_t", exp / "cli.py")
    sys.path.insert(0, str(exp))
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"db": "answers.sqlite", "registry": "registry.json",
                               "retrievalCommand": ["true"], "navigationCommand": ["true"]}))
    config = cli.load_config(cfg)
    (tmp_path / "registry.json").write_text(json.dumps({"version": 1, "datasets": {}}))
    memory = lambda req: cli.run(req, config)
    monkeypatch.setattr(pb, "memory", memory)
    f = tmp_path / "skill.md"
    f.write_text("# Skill\nold text\n")
    cache = {str(f): {"description": "a skill", "labels_ok": False}}
    assert pb.connect_part("cat-4", ["primary"], [f], cache) == {"connected": True}
    f.write_text("# Skill\nnew text\n")
    principals = ["polymarket", "primary"]
    assert pb.connect_part("cat-4", principals, [f], cache) == {"connected": True}
    for who in ("polymarket", "primary"):
        seen = {p["pointer"]: p["status"] for p in memory({"action": "panel", "principal": who})["pointers"]}
        assert seen.get("cat-4") == "available", who
    assert principals == ["polymarket", "primary"]


def test_a_long_notes_file_connects_instead_of_being_held(tmp_path):
    """things-we-have-tried.md (146 KB) was held by the old 90,000-byte limit and so never
    searchable; the gate splits evidence over Jev's ceiling, so it is inventoried now."""
    root = tmp_path / "brain"
    root.mkdir()
    (root / "tried.md").write_text("# Things we tried\n" + ("- tried a thing, it failed because of a reason\n" * 3500))
    assert (root / "tried.md").stat().st_size > 146_000
    files, held = pb.inventory([root])
    assert [p.name for p in files] == ["tried.md"] and held == []


def test_parts_close_early_before_the_connect_byte_limit(tmp_path):
    small = []
    for i in range(6):
        f = tmp_path / f"f{i}.md"
        f.write_text("x" * 100)
        small.append(f)
    assert [len(p) for p in pb.split_parts(small, 4)] == [4, 2]  # count only, as before
    assert [len(p) for p in pb.split_parts(small, 50, part_bytes=250)] == [2, 2, 2]


def test_setup_tools_refuse_a_malformed_agent_name_before_any_path_is_built():
    import argparse
    import pytest
    for bad in ("bad name", "x/../primary", "primary "):
        with pytest.raises(argparse.ArgumentTypeError):
            pb.principal_name(bad)
    assert pb.principal_name("primary-helper") == "primary-helper"
