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
            {str(f): {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True}}))
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


def test_new_file_check_skips_a_legacy_report_pinned_to_its_file_list(tmp_path, monkeypatch, capsys):
    calls = _setup(tmp_path, monkeypatch)
    (tmp_path / "brain" / "moving.md").write_text("# moving\n")
    (tmp_path / "brain" / "added.md").write_text("# added\n")
    for name in ("steady", "moving"):
        rp = rc.CACHE_DIR / f"{name}-report.json"
        rep = json.loads(rp.read_text()); rep.pop("noRecurse"); rp.write_text(json.dumps(rep))
    assert rc.main([]) == 0
    assert calls == [] and "STALE" not in capsys.readouterr().out
