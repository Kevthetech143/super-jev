#!/usr/bin/env python3
"""refresh_changed.py re-prepares only pointers whose connected files changed. No network:
subprocess.run is faked, so prepare_bulk never runs.

    python3 -m pytest skills/super-jev/tests/test_refresh_changed.py -q
"""
import hashlib
import importlib.util
import json
import os
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("refresh_changed_t", SKILL / "refresh_changed.py")
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)


def _setup(tmp_path, monkeypatch, principal="agent"):
    root = tmp_path / "brain"; root.mkdir()
    cache_dir = tmp_path / "cache"; cache_dir.mkdir()
    monkeypatch.setattr(rc, "CACHE_DIR", cache_dir)
    for name in ("steady", "moving"):
        f = root / f"{name}.md"
        f.write_text(f"# {name}\n")
        (cache_dir / f"{name}.json").write_text(json.dumps(
            {str(f): {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True,
                      "checkedAt": "2026-01-01T00:00:00"}}))
        report = {"pointer": name, "roots": [str(root)], "approved": [str(f)],
                  "excludes": ["links.md"], "noRecurse": True}
        if principal:
            report["principal"] = principal
        (cache_dir / f"{name}-report.json").write_text(json.dumps(report))
    (root / "moving.md").write_text("# moving\nchanged tonight\n")
    calls = []
    monkeypatch.setattr(rc.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": 0})())
    return calls


def test_only_the_changed_pointer_is_refreshed_with_its_recorded_args(tmp_path, monkeypatch, capsys):
    calls = _setup(tmp_path, monkeypatch)
    assert rc.main([]) == 0
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[cmd.index("--pointer") + 1] == "moving"
    assert cmd[cmd.index("--principal") + 1] == "agent"
    # One rule: the refresh names the pointer and its principals; prepare_bulk --refresh replays the rest
    # (excludes and --no-recurse: test_prepare_bulk.py::test_refresh_replays_recorded_no_recurse_and_excludes).
    assert cmd[2:] == ["--pointer", "moving", "--principal", "agent", "--refresh"]  # after python and the script
    assert "steady" not in capsys.readouterr().out


def test_dry_run_and_skip_never_run_prepare(tmp_path, monkeypatch, capsys):
    calls = _setup(tmp_path, monkeypatch)
    assert rc.main(["--dry-run"]) == 3  # verdict: stale
    assert calls == [] and "STALE moving" in capsys.readouterr().out
    assert rc.main(["--skip", "moving"]) == 0
    assert calls == []


def test_report_without_principal_needs_setup_not_guessed(tmp_path, monkeypatch, capsys):
    calls = _setup(tmp_path, monkeypatch, principal=None)
    assert rc.main([]) == 2
    out = capsys.readouterr().out
    assert calls == [] and "NEEDS-SETUP moving" in out
    # The printed command has the one refresh shape: --refresh replays the roots, so none is rebuilt here.
    assert "prepare_bulk.py --pointer moving --principal AGENT --refresh" in out and "--root" not in out


def test_multi_principal_pointer_repeats_every_principal_on_refresh(tmp_path, monkeypatch):
    """A pointer registered for several principals (e.g. primary + primary-helper) must repeat
    --principal for each one on refresh, or the harness would see the request as narrowing the
    pointer's scope and refuse it with scope-change."""
    root = tmp_path / "brain"; root.mkdir()
    cache_dir = tmp_path / "cache"; cache_dir.mkdir()
    monkeypatch.setattr(rc, "CACHE_DIR", cache_dir)
    f = root / "shared.md"; f.write_text("# shared\n")
    (cache_dir / "shared.json").write_text(json.dumps(
        {str(f): {"sha256": "stale-hash", "pass": True}}))
    report = {"pointer": "shared", "roots": [str(root)], "approved": [str(f)],
              "principals": ["primary", "primary-helper"]}
    (cache_dir / "shared-report.json").write_text(json.dumps(report))
    calls = []
    monkeypatch.setattr(rc.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": 0})())
    assert rc.main([]) == 0
    assert len(calls) == 1
    cmd = calls[0]
    principal_values = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "--principal"]
    assert principal_values == ["primary", "primary-helper"]


def test_cache_with_no_report_is_listed_needs_manual_prepare_not_skipped_silently(tmp_path, monkeypatch, capsys):
    cache_dir = tmp_path / "cache"; cache_dir.mkdir()
    monkeypatch.setattr(rc, "CACHE_DIR", cache_dir)
    (cache_dir / "orphan.json").write_text(json.dumps({"/some/file.md": {"sha256": "x", "pass": True}}))
    calls = []
    monkeypatch.setattr(rc.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": 0})())
    assert rc.main([]) == 2
    assert calls == []
    out = capsys.readouterr().out
    assert "NEEDS-SETUP orphan" in out


def test_a_file_added_to_a_connected_folder_refreshes_its_pointer(tmp_path, monkeypatch, capsys):
    calls = _setup(tmp_path, monkeypatch)
    (tmp_path / "brain" / "moving.md").write_text("# moving\n")  # undo the change: only the new file is left
    (tmp_path / "brain" / "added.md").write_text("# added after connect\n")
    assert rc.main(["--dry-run"]) == 3
    out = capsys.readouterr().out
    # Both pointers share the root, so both pick up the new in-scope file; neither re-claims the other's.
    assert "STALE moving: 0 changed, 1 new (added.md)" in out and "STALE steady: 0 changed, 1 new (added.md)" in out
    assert calls == []


def _unpin(names=("steady", "moving")):
    for name in names:
        rp = rc.CACHE_DIR / f"{name}-report.json"
        rep = json.loads(rp.read_text()); rep.pop("noRecurse"); rp.write_text(json.dumps(rep))


def _only_moving(tmp_path):
    """One legacy pointer ("moving") alone in brain/: steady's files and report removed."""
    for f in ("steady-report.json", "steady.json"):
        (rc.CACHE_DIR / f).unlink()
    (tmp_path / "brain" / "steady.md").unlink()


def _snap(name="moving", present=(), since=0):
    d = rc.CACHE_DIR / "growth"; d.mkdir(exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps({"since": since, "present": [str(p) for p in present]}))


def test_legacy_report_picks_up_a_file_created_after_its_growth_snapshot(tmp_path, monkeypatch, capsys):
    # Fleet 2026-09 (amazon-bm-fb, helper 10-question tests): notes written into already connected
    # folders were never found, because 208 of 233 reports are pinned to their file list.
    calls = _setup(tmp_path, monkeypatch)
    (tmp_path / "brain" / "moving.md").write_text("# moving\n")
    _unpin(); _only_moving(tmp_path)
    assert rc.main([]) == 0  # first look: the snapshot is taken, nothing is new yet
    assert "STALE" not in capsys.readouterr().out
    snap = json.loads((rc.CACHE_DIR / "growth" / "moving.json").read_text())
    assert snap["present"] == []
    time.sleep(0.02)
    (tmp_path / "brain" / "added.md").write_text("# added\n")
    assert rc.main(["--dry-run"]) == 3
    assert "STALE moving: 0 changed, 1 new (added.md)" in capsys.readouterr().out and calls == []


def test_a_folder_that_held_unlisted_files_at_the_snapshot_never_grows_on_its_own(tmp_path, monkeypatch, capsys):
    # Review of PR #239: a legacy report never recorded its --exclude, and a write-then-rename save
    # gives a left-out file a new birth time. With --exclude 'journal-*' at connect, diary.md
    # (rewritten since) and a new journal-2026-09.md must both stay out.
    calls = _setup(tmp_path, monkeypatch)
    brain = tmp_path / "brain"
    (brain / "moving.md").write_text("# moving\n")
    (brain / "journal-2026-08.md").write_text("# August\n")
    _unpin(); _only_moving(tmp_path)
    assert rc.main(["--dry-run"]) == 0  # a dry run records nothing
    assert not (rc.CACHE_DIR / "growth" / "moving.json").exists()
    assert rc.main([]) == 0
    out = capsys.readouterr().out
    assert "WAITING moving: 1 file(s)" in out and "STALE" not in out
    time.sleep(0.02)
    tmp = brain / "diary.tmp"; tmp.write_text("# Diary\nsaved again\n"); os.replace(tmp, brain / "diary.md")
    (brain / "journal-2026-09.md").write_text("# September\n")
    assert rc.main([]) == 0
    assert "STALE" not in capsys.readouterr().out and calls == []
    rep = json.loads((rc.CACHE_DIR / "moving-report.json").read_text())
    assert rc.new_files(rep, rc.known_files(rep)) == []


def test_legacy_report_does_not_grow_into_a_folder_it_never_connected(tmp_path, monkeypatch, capsys):
    calls = _setup(tmp_path, monkeypatch)
    (tmp_path / "brain" / "moving.md").write_text("# moving\n")
    _unpin(); _only_moving(tmp_path); _snap()
    (tmp_path / "brain" / "sub").mkdir()
    (tmp_path / "brain" / "sub" / "added.md").write_text("# added in a new folder\n")
    for skipped in ("logins.md", "notes-secret.md", ".hidden.md", "empty.md", "old.bak.md"):
        (tmp_path / "brain" / skipped).write_text("" if skipped == "empty.md" else "# skipped\n")
    walked = []
    import prepare_bulk
    monkeypatch.setattr(prepare_bulk, "inventory", lambda *a, **k: walked.append(a) or ([], []))
    assert rc.main([]) == 0
    assert calls == [] and "STALE" not in capsys.readouterr().out
    assert walked == []  # files the inventory always skips never force a walk of the root


def test_legacy_report_over_max_files_of_new_files_is_left_alone(tmp_path, monkeypatch, capsys):
    # prepare_bulk would not add them (health-fitness-reuse, #150), so a refresh would change nothing.
    calls = _setup(tmp_path, monkeypatch)
    (tmp_path / "brain" / "moving.md").write_text("# moving\n")
    _unpin(); _only_moving(tmp_path); _snap()
    import prepare_bulk
    monkeypatch.setattr(prepare_bulk, "MAX_FILES", 2)
    for i in range(3):
        (tmp_path / "brain" / f"added{i}.md").write_text(f"# added {i}\n")
    assert rc.main([]) == 0
    assert calls == [] and "STALE" not in capsys.readouterr().out


def test_a_folder_two_pointers_pin_never_grows(tmp_path, monkeypatch, capsys):
    calls = _setup(tmp_path, monkeypatch)
    (tmp_path / "brain" / "moving.md").write_text("# moving\n")
    _unpin(); _snap("moving"); _snap("steady")
    (tmp_path / "brain" / "added.md").write_text("# added\n")
    assert rc.main([]) == 0
    assert calls == [] and "STALE" not in capsys.readouterr().out  # their principals may differ


def test_an_auto_rebuilt_index_never_counts_as_new(tmp_path, monkeypatch, capsys):
    calls = _setup(tmp_path, monkeypatch)
    (tmp_path / "brain" / "moving.md").write_text("# moving\n")
    _unpin(); _only_moving(tmp_path); _snap()
    (tmp_path / "brain" / "links.md").write_text("# links\n")
    (tmp_path / "brain" / "INDEX.md").write_text("# index\n")
    assert rc.main([]) == 0
    assert calls == [] and "STALE" not in capsys.readouterr().out


def test_a_pinned_folder_inside_documents_never_grows_or_is_walked(tmp_path, monkeypatch, capsys):
    # Review 7: the medical pointers' roots sit inside documents/<person>/medical; the inventory skips
    # documents/ only below a root, so their new files would have been connected with no person deciding.
    calls = _setup(tmp_path, monkeypatch)
    vault = tmp_path / "documents" / "bob" / "medical"; vault.mkdir(parents=True)
    (vault / "visit.md").write_text("# visit\n")
    rp = rc.CACHE_DIR / "moving-report.json"
    rep = json.loads(rp.read_text()); rep.pop("noRecurse"); rep["roots"] = [str(vault)]; rep["approved"] = [str(vault / "visit.md")]
    rp.write_text(json.dumps(rep))
    _snap()
    (vault / "biopsy-results.md").write_text("# results\n")
    walked = []
    import prepare_bulk
    monkeypatch.setattr(prepare_bulk, "inventory", lambda *a, **k: walked.append(a) or ([], []))
    assert rc.new_files(rep, rc.known_files(rep)) == [] and walked == []
    assert prepare_bulk.growth({str(vault / "visit.md")}, [vault / "biopsy-results.md"],
                               {"since": 0, "present": []}, set()) == []


def test_a_recipe_pointer_rooted_inside_documents_is_never_walked_for_new_files(tmp_path, monkeypatch):
    # Review 8: marvin-medical-full is a recipe report rooted in documents/marvin/medical.
    calls = _setup(tmp_path, monkeypatch)
    vault = tmp_path / "documents" / "bob" / "medical"; vault.mkdir(parents=True)
    (vault / "visit.md").write_text("# visit\n")
    rep = {"pointer": "med", "roots": [str(vault)], "approved": [str(vault / "visit.md")], "excludes": [],
           "noRecurse": False, "principals": ["agent"]}
    (vault / "2026-09-biopsy.md").write_text("# results\n")
    walked = []
    import prepare_bulk
    monkeypatch.setattr(prepare_bulk, "inventory", lambda *a, **k: walked.append(a) or ([], []))
    assert rc.new_files(rep, rc.known_files(rep)) == [] and walked == []


def test_a_vault_pointer_lists_no_waiting_files(tmp_path, monkeypatch):
    # Review 9: a hand refresh of a medical pointer printed WAITING lines naming documents/ files,
    # promising the folder would take in new notes once admitted; it never does.
    _setup(tmp_path, monkeypatch)
    vault = tmp_path / "documents" / "bob" / "medical"; vault.mkdir(parents=True)
    for f in ("visit.md", "old.md"):
        (vault / f).write_text(f"# {f}\n")
    rep = {"pointer": "med", "roots": [str(vault)], "approved": [str(vault / "visit.md")]}
    import prepare_bulk
    snap = prepare_bulk.take_snapshot("med", {str(vault / "visit.md")}, [str(vault / "old.md")], set(), rc.CACHE_DIR)
    assert snap["present"] == [] and rc.waiting(rep) == []


def test_no_snapshot_is_taken_while_a_pinned_folder_is_missing(tmp_path, monkeypatch):
    # Review 10: an empty snapshot of a missing folder opened it to every file later restored into it,
    # including one left out at connect (lead0923-notes' root is missing today).
    _setup(tmp_path, monkeypatch)
    notes = tmp_path / "brain" / "notes"
    rep = {"pointer": "moving", "roots": [str(tmp_path / "brain")], "approved": [str(notes / "a.md")]}
    assert rc.new_files(rep, rc.known_files(rep)) == []
    assert not (rc.CACHE_DIR / "growth" / "moving.json").exists()
    notes.mkdir()
    for f in ("a.md", "journal-2026-08.md"):  # restored together; the journal was left out at connect
        (notes / f).write_text(f"# {f}\n")
    assert rc.new_files(rep, rc.known_files(rep)) == []  # first look now: the journal waits for a person
    assert rc.waiting(rep) == [str(notes / "journal-2026-08.md")]


def test_no_snapshot_while_a_pinned_folder_holds_none_of_its_files(tmp_path, monkeypatch):
    # Review 11: a folder that exists but is still being restored opened to the files that came back later.
    _setup(tmp_path, monkeypatch)
    notes = tmp_path / "brain" / "notes"; notes.mkdir()
    rep = {"pointer": "moving", "roots": [str(tmp_path / "brain")], "approved": [str(notes / "a.md")]}
    assert rc.new_files(rep, rc.known_files(rep)) == []
    assert not (rc.CACHE_DIR / "growth" / "moving.json").exists()
    for f in ("a.md", "journal-2026-08.md"):
        (notes / f).write_text(f"# {f}\n")
    assert rc.new_files(rep, rc.known_files(rep)) == [] and rc.waiting(rep) == [str(notes / "journal-2026-08.md")]


def test_an_unreadable_pinned_folder_does_not_stop_other_refreshes(tmp_path, monkeypatch, capsys):
    # Review 11: os.scandir raised PermissionError out of main, skipping every later pointer.
    calls = _setup(tmp_path, monkeypatch)
    _unpin(("moving",)); _snap("moving")  # sorts first; its pinned folder cannot be listed
    (tmp_path / "brain" / "steady.md").write_text("# steady\nchanged too\n")  # a later pointer's change
    real_scandir = os.scandir
    def deny(d):
        if str(d) == str(tmp_path / "brain"):
            raise PermissionError(d)
        return real_scandir(d)
    monkeypatch.setattr(rc.os, "scandir", deny)
    assert rc.main([]) == 0
    assert any("--pointer" in c and c[c.index("--pointer") + 1] == "steady" for c in calls)


def test_a_new_py_file_in_a_pointer_recorded_with_py_is_new(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    brain = tmp_path / "brain"
    (brain / "new_tool.py").write_text("def run_tool():\n    return 1\n")
    rep = {"pointer": "code", "roots": [str(brain)], "excludes": [], "noRecurse": False, "principals": ["agent"],
           "extensions": [".md", ".py"]}
    assert [Path(p).name for p in rc.new_files(rep, set()) if p.endswith(".py")] == ["new_tool.py"]
    assert not any(p.endswith(".py") for p in rc.new_files({**rep, "extensions": [".md"]}, set()))
    assert not any(p.endswith(".py") for p in rc.new_files({**rep, "extensions": [".md", ".pem"]}, set()))
