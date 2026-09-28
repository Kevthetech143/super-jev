#!/usr/bin/env python3
"""refresh_changed.py re-prepares only pointers whose connected files changed. No network:
subprocess.run is faked, so prepare_bulk never runs.

    python3 -m pytest skills/super-jev/tests/test_refresh_changed.py -q
"""
import hashlib
import importlib.util
import json
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
    assert "--refresh" in cmd and "--no-recurse" in cmd and cmd[cmd.index("--exclude") + 1] == "links.md"
    assert "steady" not in capsys.readouterr().out


def test_dry_run_and_skip_never_run_prepare(tmp_path, monkeypatch, capsys):
    calls = _setup(tmp_path, monkeypatch)
    assert rc.main(["--dry-run"]) == 0
    assert calls == [] and "STALE moving" in capsys.readouterr().out
    assert rc.main(["--skip", "moving"]) == 0
    assert calls == []


def test_report_without_principal_is_skipped_not_guessed(tmp_path, monkeypatch, capsys):
    calls = _setup(tmp_path, monkeypatch, principal=None)
    assert rc.main([]) == 0
    assert calls == [] and "SKIP  moving" in capsys.readouterr().out


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
    assert rc.main([]) == 0
    assert calls == []
    out = capsys.readouterr().out
    assert "NEEDS MANUAL PREPARE" in out and "orphan" in out


def test_a_file_added_to_a_connected_folder_refreshes_its_pointer(tmp_path, monkeypatch, capsys):
    calls = _setup(tmp_path, monkeypatch)
    (tmp_path / "brain" / "moving.md").write_text("# moving\n")  # undo the change: only the new file is left
    (tmp_path / "brain" / "added.md").write_text("# added after connect\n")
    assert rc.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    # Both pointers share the root, so both pick up the new in-scope file; neither re-claims the other's.
    assert "STALE moving: 0 changed, 1 new (added.md)" in out and "STALE steady: 0 changed, 1 new (added.md)" in out
    assert calls == []


def _unpin(names=("steady", "moving")):
    for name in names:
        rp = rc.CACHE_DIR / f"{name}-report.json"
        rep = json.loads(rp.read_text()); rep.pop("noRecurse"); rp.write_text(json.dumps(rep))


def test_legacy_report_picks_up_a_new_file_in_a_folder_it_already_connects(tmp_path, monkeypatch, capsys):
    # Fleet 2026-09 (amazon-bm-fb, helper 10-question tests): notes written that week into already
    # connected folders were never found, because 208 of 233 reports are pinned to their file list.
    calls = _setup(tmp_path, monkeypatch)
    (tmp_path / "brain" / "moving.md").write_text("# moving\n")
    (tmp_path / "brain" / "added.md").write_text("# added\n")
    _unpin()
    assert rc.main(["--dry-run"]) == 0
    # Both pointers pin this folder: a new file there joins neither (their principals may differ).
    assert "STALE" not in capsys.readouterr().out
    for f in ("steady-report.json", "steady.json"):
        (rc.CACHE_DIR / f).unlink()
    (tmp_path / "brain" / "steady.md").unlink()
    assert rc.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "STALE moving: 0 changed, 1 new (added.md)" in out and calls == []


def test_legacy_report_does_not_grow_into_a_folder_it_never_connected(tmp_path, monkeypatch, capsys):
    calls = _setup(tmp_path, monkeypatch)
    (tmp_path / "brain" / "moving.md").write_text("# moving\n")
    (tmp_path / "brain" / "sub").mkdir()
    (tmp_path / "brain" / "sub" / "added.md").write_text("# added in a new folder\n")
    for skipped in ("logins.md", "notes-secret.md", ".hidden.md", "empty.md", "old.bak.md"):
        (tmp_path / "brain" / skipped).write_text("" if skipped == "empty.md" else "# skipped\n")
    _unpin()
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
    import prepare_bulk
    monkeypatch.setattr(prepare_bulk, "MAX_FILES", 2)
    for i in range(3):
        (tmp_path / "brain" / f"added{i}.md").write_text(f"# added {i}\n")
    _unpin()
    assert rc.main([]) == 0
    assert calls == [] and "STALE" not in capsys.readouterr().out


def test_prepare_args_uses_the_asking_agent_only_when_the_report_has_no_principal():
    rep = {"pointer": "p", "roots": ["/r"]}
    assert rc.prepare_args(rep) is None
    assert rc.prepare_args(rep, "amazon")[-3:] == ["--principal", "amazon", "--refresh"]
    rep["principals"] = ["owner"]
    assert "amazon" not in rc.prepare_args(rep, "amazon")


def test_legacy_report_never_grows_a_file_that_was_there_at_connect(tmp_path, monkeypatch, capsys):
    # Review 2026-09-28: legacy reports never recorded their --exclude, so a links.md left out at
    # connect (rebuilt daily) must not come back as "new" and stale its pointer every day.
    calls = _setup(tmp_path, monkeypatch)
    (tmp_path / "brain" / "moving.md").write_text("# moving\n")
    (tmp_path / "brain" / "links.md").write_text("# links\n")
    _unpin()
    for name in ("steady", "moving"):  # connected after links.md was written
        cp = rc.CACHE_DIR / f"{name}.json"
        cp.write_text(cp.read_text().replace("2026-01-01T00:00:00", "2999-01-01T00:00:00"))
    assert rc.main([]) == 0
    assert calls == [] and "STALE" not in capsys.readouterr().out
    for name in ("steady", "moving"):  # no known connect time: nothing counts as new
        cp = rc.CACHE_DIR / f"{name}.json"
        cp.write_text(cp.read_text().replace('"checkedAt": "2999-01-01T00:00:00"', '"x": 0'))
    assert rc.main([]) == 0
    assert calls == [] and "STALE" not in capsys.readouterr().out
