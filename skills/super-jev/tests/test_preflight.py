"""ask.py --preflight: free readiness + per-folder coverage; asks only with --about/--skill."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ask
import prepare_bulk

_REAL_RUN = subprocess.run  # the `world` fixture replaces subprocess.run; one test runs the real launcher


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


def skills_reply(monkeypatch, body, calls=None):
    """Fake the skills door: one reply to every subprocess call, recorded in `calls`."""
    seen = calls if calls is not None else []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: seen.append(cmd) or _R(json.dumps(body)))
    return seen


def test_not_ready_skips_the_connection_questions_but_still_searches_skills(world, monkeypatch, capsys):
    """One rule: a paid check runs when its own inputs are ready. --about reads the connections, so a
    NOT READY preflight makes no --about ask; --skill reads skill folders, so it still runs."""
    monkeypatch.setattr(ask, "memory", panel(("bad", "error")))
    seen = skills_reply(monkeypatch, {"status": "suggestions", "source": "jev",
                                      "candidates": [{"name": "x-poster", "path": "/s/x/SKILL.md"}]})
    rc, out = run(monkeypatch, capsys, "--about", "anything", "--skill", "posts things")
    assert rc == 1 and "NOT READY" in out
    assert len(seen) == 1 and "dispatch.py" in seen[0][1] and "skills" in seen[0]  # no ask.py, no --about question
    assert "x-poster" in out and "x-poster (unverified guess)" not in out


def test_skill_search_runs_while_nothing_is_connected(world, monkeypatch, capsys):
    """A fresh install: no connections at all, and --skill still reports (it used to be skipped silently)."""
    monkeypatch.setattr(ask, "memory", lambda req: {"status": "error", "reason": "not-set-up"})
    skills_reply(monkeypatch, {"status": "exact", "source": "local", "candidates": [{"name": "x-poster", "path": "/s/x/SKILL.md"}]})
    rc, out = run(monkeypatch, capsys, "--skill", "posts a tweet", "--json")
    rep = json.loads(out)
    assert rc == 1 and rep["verdict"] == "NOT READY"
    assert rep["existing_skills"] == ["x-poster"]


@pytest.mark.parametrize("body,labelled,warning", [
    ({"status": "exact", "source": "local"}, False, None),
    ({"status": "suggestions", "source": "jev"}, False, None),
    ({"status": "suggestions", "source": "local"}, True, None),
    ({"status": "clarify", "source": "jev", "error": "Did you mean one of: a, b?"}, True, None),
    ({"status": "fallback", "source": "local", "error": "Judge unavailable (key rejected); local-only results"}, True,
     "skill search fell back to local guesses: Judge unavailable (key rejected); local-only results"),
])
def test_preflight_labels_a_skill_only_a_confirmed_result_is_called_a_match(world, monkeypatch, capsys, body, labelled, warning):
    """The ask path and preflight read one door with one rule: only an exact name or a live judge pick is a match.
    A clarify question is no warning; a fallback is, with its cause (a rejected key must not look like READY)."""
    monkeypatch.setattr(ask, "memory", panel(("notes", "available")))
    skills_reply(monkeypatch, {**body, "candidates": [{"name": "x-poster", "path": "/s/x/SKILL.md"}]})
    rc, out = run(monkeypatch, capsys, "--skill", "posts a tweet", "--json")
    rep = json.loads(out)
    assert rep["existing_skills"] == (["x-poster (unverified guess)"] if labelled else ["x-poster"])
    assert rep["warnings"] == ([warning] if warning else [])
    assert rep["verdict"] == ("READY WITH WARNINGS" if warning else "READY")


def test_the_launchers_setup_line_reaches_the_user_whole_from_a_deep_checkout(world, monkeypatch, capsys):
    """The real launcher message for a missing roots.json, from a deep checkout path, is printed uncut."""
    deep = world["tmp"] / ("a-deep-checkout-folder-" * 4) / "super-jev"
    shutil.copytree(SKILLS / "skill-search", deep / "skills/skill-search")
    (deep / "skills/skill-search/roots.json").unlink(missing_ok=True)  # this checkout may have its own
    request = deep / "request.json"
    request.write_text('{"request": "posts a tweet"}')
    r = _REAL_RUN(["bash", str(deep / "skills/skill-search/launcher.sh"), "--request-file", str(request)],
                  capture_output=True, text=True, env={k: v for k, v in os.environ.items() if k != "CLAUDECODE"})
    assert r.returncode == 2 and "roots.example.json" in r.stdout  # the real message, not a made-up one
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: _R(r.stdout, "", r.returncode))
    monkeypatch.setattr(ask, "memory", panel(("notes", "available")))
    rc, out = run(monkeypatch, capsys, "--skill", "posts a tweet")
    warning = next(l for l in out.splitlines() if "existing-skill search failed" in l)
    assert str(deep / "skills/skill-search") in warning
    assert "copy roots.example.json to roots.json and list your skill folders" in warning


def test_no_match_is_none_found_not_a_failed_search(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", panel(("notes", "available")))
    skills_reply(monkeypatch, {"status": "no_match", "source": "jev", "candidates": []})
    rc, out = run(monkeypatch, capsys, "--skill", "posts a tweet", "--json")
    rep = json.loads(out)
    assert rep["existing_skills"] == [] and rep["warnings"] == []


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
    outside = world["proj"] / "shared"  # a link out of the folder is not walked, so the target sits inside
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
