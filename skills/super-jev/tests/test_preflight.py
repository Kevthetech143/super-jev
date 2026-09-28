"""ask.py --preflight: free readiness + per-folder coverage; asks only with --about/--skill."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ask
import prepare_bulk


@pytest.fixture
def world(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    for n in ("a.md", "b.md", "sub/c.md"):
        (proj / n).write_text("# note\n")
    (proj / "tool.py").write_text("x = 1\n")
    (cache / "notes-report.json").write_text(json.dumps({"pointer": "notes", "roots": [str(tmp_path)]}))
    monkeypatch.setattr(prepare_bulk, "CACHE_DIR", cache)
    monkeypatch.setattr(ask, "refresh_hint", lambda *a: "; refresh it")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a[0]) or pytest.fail("no paid call"))
    REGISTERED.clear()
    REGISTERED.update({"notes": [str(proj / "a.md"), str(proj / "sub/c.md")]})
    REGISTERED["notes-2"] = REGISTERED["notes"]
    return {"tmp": tmp_path, "proj": proj, "cache": cache, "calls": calls}


REGISTERED = {}  # pointer -> the files the backend has registered for it (the `sources` action)


def panel(*rows):
    def memory(req):
        if req["action"] == "sources":
            files = REGISTERED.get(req["pointer"], [])
            page = files[req["offset"]:req["offset"] + 1]  # one row per page proves paging is followed
            nxt = req["offset"] + 1 if req["offset"] + 1 < len(files) else None
            return {"status": "ok", "sources": [{"originalPath": f} for f in page], "nextOffset": nxt}
        return {"status": "ok", "pointers": [{"pointer": n, "snapshotStatus": st} for n, st in rows]}
    return memory


def run(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "reader", "--preflight", *args])
    rc = ask._main()
    return rc, capsys.readouterr().out


def test_ready_with_partial_coverage_is_counted_not_hidden(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", panel(("notes", "available")))
    rc, out = run(monkeypatch, capsys, "--project-dir", str(world["proj"]))
    assert rc == 0
    assert "READY WITH WARNINGS" in out and "2 of 3 connectable files connected" in out
    assert "1 files of other types are not connected" in out
    assert world["calls"] == []  # the free part never asks Jev


def test_folder_with_nothing_connected_is_not_ready(world, monkeypatch, capsys):
    other = world["tmp"] / "other"
    other.mkdir()
    (other / "x.md").write_text("x\n")
    monkeypatch.setattr(ask, "memory", panel(("notes", "available")))
    rc, out = run(monkeypatch, capsys, "--project-dir", str(other))
    assert rc == 1 and "NOT READY" in out and "0 of 1 connectable files" in out


def test_broken_connection_blocks_and_stale_only_warns(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", panel(("notes", "available"), ("old", "preparation-required: changed"),
                                             ("bad", "error")))
    rc, out = run(monkeypatch, capsys)
    assert rc == 1
    assert "bad is not ready: error" in out and "old is stale" in out and "refresh it" in out


def test_status_unavailable_is_not_ready(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", lambda req: {"status": "error", "reason": "not-set-up"})
    rc, out = run(monkeypatch, capsys)
    assert rc == 1 and "not set up" in out


def test_json_output_for_skills(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", panel(("notes", "available")))
    rc, out = run(monkeypatch, capsys, "--project-dir", str(world["proj"]), "--json")
    rep = json.loads(out)
    assert rc == 0 and rep["verdict"] == "READY WITH WARNINGS"
    assert rep["folders"][0]["connected"] == 2 and rep["folders"][0]["connectable"] == 3


def test_split_part_counts_its_own_registered_files(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", panel(("notes-2", "available")))
    rc, out = run(monkeypatch, capsys, "--project-dir", str(world["proj"]))
    assert "2 of 3" in out


def test_about_asks_and_flags_new_ground(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", panel(("notes", "available")))
    asked = []
    class R:
        def __init__(self, out): self.stdout, self.stderr, self.returncode = out, "", 0
    def fake_run(cmd, **k):
        asked.append(cmd)
        return R(" 0.62  /n/x.md  [notes]  (possible: word-search match)\n")
    monkeypatch.setattr(subprocess, "run", fake_run)
    rc, out = run(monkeypatch, capsys, "--about", "quantum widgets")
    assert rc == 0 and len(asked) == 4 and all("--" in c and "quantum widgets" in c[-1] for c in asked)
    assert "NEW GROUND" in out


def test_about_known_topic_is_not_new_ground(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", panel(("notes", "available")))
    class R:
        stdout, stderr, returncode = " 0.93  /n/design.md  [notes]\n", "", 0
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: R())
    rc, out = run(monkeypatch, capsys, "--about", "retries")
    assert "NEW GROUND" not in out


def test_not_ready_skips_paid_questions(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", panel(("bad", "error")))
    rc, out = run(monkeypatch, capsys, "--about", "anything", "--skill", "posts things")
    assert rc == 1 and world["calls"] == []


def test_skill_search_lists_existing_skills(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", panel(("notes", "available")))
    class R:
        stdout, stderr, returncode = json.dumps({"candidates": [{"name": "x-poster"}]}), "", 0
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: R())
    rc, out = run(monkeypatch, capsys, "--skill", "posts a tweet")
    assert "x-poster" in out and "reuse or extend" in out


def test_bad_arguments_never_search(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", panel(("notes", "available")))
    monkeypatch.setattr(ask, "lookup", lambda *a: pytest.fail("must not search"))
    rc, out = run(monkeypatch, capsys, "--about")
    assert rc == 2 and "usage" in out


def test_opted_in_extensions_count_as_connectable(world, monkeypatch, capsys):
    (world["cache"] / "code-report.json").write_text(json.dumps({"pointer": "code", "extensions": [".md", ".py"]}))
    REGISTERED["code"] = [str(world["proj"] / "tool.py")]
    monkeypatch.setattr(ask, "memory", panel(("notes", "available"), ("code", "available")))
    rc, out = run(monkeypatch, capsys, "--project-dir", str(world["proj"]), "--json")
    f = json.loads(out)["folders"][0]
    assert f["connectable"] == 4 and f["connected"] == 3 and f["unsupported"] == 0


class _R:
    def __init__(self, out="", err="", rc=0): self.stdout, self.stderr, self.returncode = out, err, rc


def test_failed_asks_are_not_new_ground(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", panel(("notes", "available")))
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: _R("provider unavailable", "provider failure", 1))
    rc, out = run(monkeypatch, capsys, "--about", "synthetic feature", "--json")
    rep = json.loads(out)
    assert rep["new_ground"] is None and rep["verdict"] == "READY WITH WARNINGS"
    assert all("exit 1" in a["error"] for a in rep["knowledge"])


def test_ask_timeout_is_a_warning_not_a_crash(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", panel(("notes", "available")))
    def boom(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, 600)
    monkeypatch.setattr(subprocess, "run", boom)
    rc, out = run(monkeypatch, capsys, "--about", "x", "--skill", "posts things")
    assert "NEW GROUND UNKNOWN" in out and "timed out" in out and "existing-skill search failed" in out


def test_inconclusive_high_score_is_not_known(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", panel(("notes", "available")))
    line = " 0.99  /tmp/note.md  [notes]  (inconclusive: content check did not finish; routing score)\n"
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: _R(line))
    rc, out = run(monkeypatch, capsys, "--about", "x", "--json")
    assert json.loads(out)["new_ground"] is True


def test_failed_skill_search_is_not_none_found(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", panel(("notes", "available")))
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: _R(json.dumps({"status": "error", "candidates": []}), "", 1))
    rc, out = run(monkeypatch, capsys, "--skill", "posts things", "--json")
    rep = json.loads(out)
    assert rep["existing_skills"] is None and any("existing-skill search failed" in w for w in rep["warnings"])


def test_approved_but_unregistered_files_do_not_count(world, monkeypatch, capsys):
    (world["cache"] / "notes-report.json").write_text(json.dumps({"connected": False, "approved": [
        str(world["proj"] / n) for n in ("a.md", "b.md", "sub/c.md")]}))
    REGISTERED["notes"] = [str(world["proj"] / "a.md")]
    monkeypatch.setattr(ask, "memory", panel(("notes", "available")))
    rc, out = run(monkeypatch, capsys, "--project-dir", str(world["proj"]), "--json")
    assert json.loads(out)["folders"][0]["connected"] == 1


def test_symlinked_subfolder_is_walked_once(world, monkeypatch, capsys, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "linked.md").write_text("# l\n")
    (outside / "loop").symlink_to(outside, target_is_directory=True)
    (world["proj"] / "link").symlink_to(outside, target_is_directory=True)
    (world["proj"] / "api.schema.json").write_text("{}")
    (world["cache"] / "code-report.json").write_text(json.dumps({"extensions": [".schema.json"]}))
    REGISTERED["code"] = [str(world["proj"] / "api.schema.json")]
    monkeypatch.setattr(ask, "memory", panel(("notes", "available"), ("code", "available")))
    rc, out = run(monkeypatch, capsys, "--project-dir", str(world["proj"]), "--json")
    f = json.loads(out)["folders"][0]
    assert f["connectable"] == 5 and f["connected"] == 3  # a, b, c, linked.md, api.schema.json


def test_unlistable_pointer_blocks(world, monkeypatch, capsys):
    base = panel(("notes", "available"))
    monkeypatch.setattr(ask, "memory", lambda r: {"status": "error"} if r["action"] == "sources" else base(r))
    rc, out = run(monkeypatch, capsys, "--project-dir", str(world["proj"]))
    assert rc == 1 and "could not list the registered files of notes" in out


def test_failed_ask_output_is_never_strong(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", panel(("notes", "available")))
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: _R(" 0.99  /n/design.md  [notes]\n", "boom", 1))
    rc, out = run(monkeypatch, capsys, "--about", "x", "--json")
    assert json.loads(out)["new_ground"] is None


def test_md_alias_to_other_suffix_counts_as_md(world, monkeypatch, capsys, tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("# t\n")
    (world["proj"] / "alias.md").symlink_to(target)
    REGISTERED["notes"] = REGISTERED["notes"] + [str(target)]  # the backend echoes real paths
    monkeypatch.setattr(ask, "memory", panel(("notes", "available")))
    rc, out = run(monkeypatch, capsys, "--project-dir", str(world["proj"]), "--json")
    f = json.loads(out)["folders"][0]
    assert f["connectable"] == 4 and f["connected"] == 3
