#!/usr/bin/env python3
"""Offline tests for how a person picks the context size (context setting 4/7): small, medium or large.

The size says how much of each note one content check reads (judge_profiles.json; read_budget() in ask.py). Where it
comes from, strongest first: the `--context NAME` flag of one ask, the SUPERJEV_CONTEXT environment variable, the choice
saved with `--set-context NAME` in the principal's settings.json, then the judge profile's own default_context. The first
one that is set is used, and it must name one of the three sizes the judge profile states.

What these tests hold:
  - the order above, and that one ask's choice never outlives it;
  - a bad name is exit 2 and one line, whichever source it came from;
  - the flag may stand anywhere before a `--`, is taken out before the --json check, and after a `--` is part of the question;
  - --status always names the size, the default and where the size came from; an ask's --json names it only when it is not small;
  - --set-context writes settings.json atomically and only the three names;
  - a content-check payload carries `context` only when the ask reads at more than small, and no other request does.

No network, no key, no Jev call: ask.lookup is replaced by a stand-in that records what it was asked, or the engine and the
navigation run are faked.

The shipped table offers Small and Medium only (Large waits for its own test), so the tests of how a size is picked read a
made-up table: the shipped one plus a `large` row (the large_row fixture in conftest.py). The two tests marked shipped_table
read the shipped table and pin what it offers; test_large_not_offered.py says what asking for Large does.

    python3 -m pytest skills/super-jev/tests/test_context_setting.py -q
"""
import dataclasses
import errno
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parents[1]
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_context_setting", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)
judge_profile = ask.judge_profile

USAGE = 'ask: unknown context "{}" (choose small, medium or large)'
QUESTION = "when is the zeppelin quokka inspection scheduled"


@pytest.fixture(autouse=True)
def _made_up_large(request):
    """A table with a `large` row for every test here, except the ones marked shipped_table."""
    if request.node.get_closest_marker("shipped_table") is None:
        request.getfixturevalue("large_row")


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    """No staged zoom picks, no claim, the preset small, and a state folder of its own."""
    monkeypatch.setattr(ask, "_STAGE", {})
    monkeypatch.setitem(ask._CLAIM, "text", None)
    monkeypatch.setattr(ask, "_CONTEXT", {"name": "small", "small_paths": set()})
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SUPERJEV_AUTO_CACHE", "0")


def run(monkeypatch, capsys, *argv):
    """One ask.py run for principal "me": (exit code, what it printed)."""
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "me", *argv])
    rc = ask.main()
    return rc, capsys.readouterr().out


def spy(monkeypatch):
    """ask.lookup stands in for the whole search: it records the question, the size and where the size came from, and
    finishes as a found ask. Every request that is not a search never reaches it."""
    seen = []

    def lookup(question, principal, sdir):
        seen.append((question, ask._CONTEXT["name"], ask._CONTEXT.get("source")))
        return ask._done("found", "1 file")
    monkeypatch.setattr(ask, "lookup", lookup)
    return seen


def settings(tmp_path):
    return tmp_path / "state" / "me" / "settings.json"


def save(tmp_path, text):
    """settings.json as it stands on disk: `text` is written as it is, a dict as JSON."""
    path = settings(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if isinstance(text, str) else json.dumps(text))


def at(monkeypatch, preset, source="flag"):
    """Read as `preset`, the way an ask sets it once from the flag, the variable or the saved choice."""
    monkeypatch.setattr(ask, "_CONTEXT", {"name": preset, "source": source, "small_paths": set()})


# ---------------------------------------------------------------- where the size comes from
def test_flag_beats_env_beats_settings_beats_default(tmp_path, monkeypatch, capsys):
    seen = spy(monkeypatch)

    def read_at(*flags):
        seen.clear()
        rc, _ = run(monkeypatch, capsys, *flags, "--", QUESTION)
        assert rc == 0 and len(seen) == 1 and seen[0][0] == QUESTION
        return seen[0][1:]

    assert read_at() == ("small", "default")
    for saved, env, flag, want in [  # the strongest source that is set wins, and its name is told
        ("medium", None, None, ("medium", "settings")),
        ("medium", "large", None, ("large", "env")),
        ("medium", "large", "small", ("small", "flag")),  # a flag for less still beats a bigger choice saved or exported
        ("large", None, "small", ("small", "flag")),
        (None, "large", None, ("large", "env")),
        (None, "large", "medium", ("medium", "flag")),
        (None, None, "large", ("large", "flag")),
        (None, None, None, ("small", "default")),  # nothing of the last ask is left behind
    ]:
        monkeypatch.delenv("SUPERJEV_CONTEXT", raising=False)
        settings(tmp_path).unlink(missing_ok=True)
        if saved:
            save(tmp_path, {"context": saved})
        if env:
            monkeypatch.setenv("SUPERJEV_CONTEXT", env)
        assert read_at(*(["--context", flag] if flag else [])) == want, (saved, env, flag)
    # the variable is read as a person types it: trimmed, any case, and empty is not set
    save(tmp_path, {"context": "medium"})
    monkeypatch.setenv("SUPERJEV_CONTEXT", "  LARGE ")
    assert read_at() == ("large", "env")
    monkeypatch.setenv("SUPERJEV_CONTEXT", "  ")
    assert read_at() == ("medium", "settings")
    assert read_at("--context", " Small") == ("small", "flag")
    assert read_at("--context", "large", "--context", "small") == ("small", "flag")  # said twice: the last word
    # a settings file that cannot be read, or holds no choice, is no choice
    monkeypatch.delenv("SUPERJEV_CONTEXT")
    for text in ("{not json", "[]", '"large"', '{"context": null}', '{"other": "large"}'):
        save(tmp_path, text)
        assert read_at() == ("small", "default"), text
    # the factory default is the judge profile's own: nothing in ask.py says small
    monkeypatch.setattr(judge_profile, "PROFILE", dataclasses.replace(judge_profile.PROFILE, default_context="medium"))
    settings(tmp_path).unlink()
    assert read_at() == ("medium", "default")
    assert read_at("--context", "small") == ("small", "flag")


def test_bad_name_exits_2_with_the_usage_line(tmp_path, monkeypatch, capsys):
    seen = spy(monkeypatch)
    rc, out = run(monkeypatch, capsys, "--context", "xl", "--", QUESTION)
    assert (rc, out) == (2, USAGE.format("xl") + "\n")
    assert run(monkeypatch, capsys, "--context", "--", QUESTION) == (2, USAGE.format("") + "\n")  # a flag with no name
    assert run(monkeypatch, capsys, "--context", "Huge", "--status")[0] == 2
    assert not seen  # nothing was searched
    # the same rule for the variable and for the saved choice, and each says where the bad name was
    monkeypatch.setenv("SUPERJEV_CONTEXT", "xl")
    rc, out = run(monkeypatch, capsys, "--", QUESTION)
    assert (rc, out) == (2, USAGE.format("xl").replace(" (choose", " in SUPERJEV_CONTEXT (choose") + "\n")
    assert run(monkeypatch, capsys, "--context", "medium", "--", QUESTION)[0] == 0  # only the source that is used is read
    monkeypatch.delenv("SUPERJEV_CONTEXT")
    save(tmp_path, {"context": "xl"})
    rc, out = run(monkeypatch, capsys, "--", QUESTION)
    assert (rc, out) == (2, USAGE.format("xl").replace(" (choose", " in settings.json (choose") + "\n")
    # a program that asked for --json gets one object, not a bare line
    rc, out = run(monkeypatch, capsys, "--json", "--context", "xl", "--", QUESTION)
    obj = json.loads(out)
    assert rc == 2 and obj["v"] == 1 and obj["outcome"] == "not-supported" and USAGE.format("xl") in obj["why"]
    # a judge profile with no such size is refused the same way, naming the sizes it does state
    monkeypatch.setattr(judge_profile, "PROFILE", judge_profile.load("laya"))
    assert run(monkeypatch, capsys, "--context", "medium", "--", QUESTION) == (2, 'ask: unknown context "medium" (choose small)\n')
    assert run(monkeypatch, capsys, "--context", "small", "--", QUESTION)[0] == 0
    assert [s[1:] for s in seen] == [("medium", "flag"), ("small", "flag")]  # only the two valid runs searched


def test_flag_is_removed_before_the_json_check(tmp_path, monkeypatch, capsys):
    seen = spy(monkeypatch)
    for argv in (["--context", "large", "--json", "--", QUESTION], ["--json", "--context", "large", "--", QUESTION],
                 ["--context", "large", "--json", QUESTION]):
        seen.clear()
        rc, out = run(monkeypatch, capsys, *argv)
        obj = json.loads(out)  # --json was seen as the first word: one object, not text
        assert rc == 0 and obj["v"] == 1 and obj["outcome"] == "found", argv
        assert seen == [(QUESTION, "large", "flag")], argv  # the flag and its name are not part of the question
    # the same flag in text mode
    seen.clear()
    assert run(monkeypatch, capsys, "--context", "medium", QUESTION)[0] == 0
    assert seen == [(QUESTION, "medium", "flag")]


def test_flag_after_double_dash_is_part_of_the_question(tmp_path, monkeypatch, capsys):
    seen = spy(monkeypatch)
    assert run(monkeypatch, capsys, "--", "how", "do", "I", "use", "--context", "large")[0] == 0
    assert seen == [("how do I use --context large", "small", "default")]  # words after `--` are read literally
    seen.clear()
    assert run(monkeypatch, capsys, "--context", "medium", "--", "tell me about --context large")[0] == 0
    assert seen == [("tell me about --context large", "medium", "flag")]  # the flag before it still counts
    seen.clear()
    assert run(monkeypatch, capsys, "--", "--context", "xl")[0] == 0  # even a bad name there is only words
    assert seen == [("--context xl", "small", "default")]


# ---------------------------------------------------------------- what --status and --json say
def status_world(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    monkeypatch.setattr(ask.prepare_bulk, "CACHE_DIR", cache)
    monkeypatch.setattr(ask, "memory", lambda req: {"pointers": [{"pointer": "notes", "snapshotStatus": "available"}]}
                        if req["action"] == "panel" else {"status": "miss"})


def test_status_reports_preset_default_and_source(tmp_path, monkeypatch, capsys):
    status_world(tmp_path, monkeypatch)
    for flags, env, saved, want, line in [
        ([], None, None, ("small", "small", "default"), "Context: Small (the default)"),
        (["--context", "medium"], None, None, ("medium", "small", "flag"), "Context: Medium (from --context)"),
        ([], "large", None, ("large", "small", "env"), "Context: Large (from SUPERJEV_CONTEXT)"),
        ([], None, "medium", ("medium", "small", "settings"), "Context: Medium (from settings.json)"),
        (["--context", "small"], "large", "medium", ("small", "small", "flag"), "Context: Small (from --context)"),
    ]:
        monkeypatch.delenv("SUPERJEV_CONTEXT", raising=False)
        settings(tmp_path).unlink(missing_ok=True)
        if env:
            monkeypatch.setenv("SUPERJEV_CONTEXT", env)
        if saved:
            save(tmp_path, {"context": saved})
        rc, text = run(monkeypatch, capsys, *flags, "--status")
        rows = text.splitlines()
        assert rc == 0 and line in rows, (flags, text)
        assert rows[-1].startswith("Next:")  # the next step stays the last line
        rc, raw = run(monkeypatch, capsys, "--json", *flags, "--status")
        obj = json.loads(raw)
        assert rc == 0 and obj["context"] == dict(zip(("preset", "default", "source"), want)), (flags, raw)
        assert obj["next"] == "none" and obj["sets"] == [{"name": "notes", "state": "ready"}]  # what --status said before
    # nothing connected is still a status: the size is named there too
    monkeypatch.delenv("SUPERJEV_CONTEXT", raising=False)
    settings(tmp_path).unlink(missing_ok=True)
    monkeypatch.setattr(ask, "memory", lambda req: {"pointers": []})
    rc, raw = run(monkeypatch, capsys, "--json", "--status")
    assert rc == 0 and json.loads(raw)["context"] == {"preset": "small", "default": "small", "source": "default"}
    # the default shown is the judge profile's, and the size in use can differ from it
    monkeypatch.setattr(judge_profile, "PROFILE", dataclasses.replace(judge_profile.PROFILE, default_context="large"))
    status_world(tmp_path, monkeypatch)
    rc, raw = run(monkeypatch, capsys, "--json", "--context", "medium", "--status")
    assert json.loads(raw)["context"] == {"preset": "medium", "default": "large", "source": "flag"}


def test_json_context_field_only_when_not_small_or_after_a_fallback(tmp_path, monkeypatch, capsys):
    """An ask says its size in --json only when it read at more than small. (The fallback to small is item 5: it adds
    `fallback` to this object and makes it appear after a fallback as well; its tests are in test_context_fallback.py.)"""
    spy(monkeypatch)

    def asked(*argv, env=None, saved=None):
        monkeypatch.delenv("SUPERJEV_CONTEXT", raising=False)
        settings(tmp_path).unlink(missing_ok=True)
        if env:
            monkeypatch.setenv("SUPERJEV_CONTEXT", env)
        if saved:
            save(tmp_path, {"context": saved})
        rc, raw = run(monkeypatch, capsys, "--json", *argv, "--", QUESTION)
        assert rc == 0 and raw.count("\n") == 1
        return json.loads(raw)

    assert "context" not in asked()  # the default: the object is what it always was
    assert "context" not in asked("--context", "small")
    assert "context" not in asked(env="small") and "context" not in asked(saved="small")
    assert asked("--context", "medium")["context"] == {"preset": "medium", "source": "flag"}
    assert asked(env="large")["context"] == {"preset": "large", "source": "env"}
    assert asked(saved="medium")["context"] == {"preset": "medium", "source": "settings"}
    assert asked("--context", "small", env="large") .get("context") is None  # small by the flag: nothing to say
    obj = asked("--context", "large")
    assert set(obj) == {"v", "outcome", "why", "next", "context"} and '"score"' not in json.dumps(obj)
    # a claim never reads at the size: its object has no size to name, whatever was picked
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "me", "--json", "--context", "large", "--claim", QUESTION])
    assert ask.main() == 0
    assert "context" not in json.loads(capsys.readouterr().out)
    # --approve and --miss, which read nothing, keep their own small objects
    monkeypatch.setattr(ask, "approve", lambda *a, **k: ask.say("saved") and 0)
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "me", "--json", "--context", "large", "--approve", QUESTION])
    ask.main()
    assert "context" not in json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------- the saved choice
def test_set_context_writes_settings_atomically_and_only_the_three_names(tmp_path, monkeypatch, capsys):
    seen = spy(monkeypatch)
    path = settings(tmp_path)
    assert not path.parent.exists()
    rc, out = run(monkeypatch, capsys, "--set-context", "medium")
    assert rc == 0 and json.loads(path.read_text()) == {"context": "medium"}
    assert out.count("\n") == 1 and "Medium" in out and str(path) in out
    assert sorted(p.name for p in path.parent.iterdir()) == ["settings.json"]  # no temporary file is left behind
    assert not seen  # it set the choice; it searched for nothing
    # the next ask reads it
    assert run(monkeypatch, capsys, "--", QUESTION)[0] == 0 and seen[-1][1:] == ("medium", "settings")
    # a person's spelling is read as it is for the flag; what is written is the name
    assert run(monkeypatch, capsys, "--set-context", " Large ")[0] == 0 and json.loads(path.read_text()) == {"context": "large"}
    # the other keys of the file stay
    path.write_text(json.dumps({"context": "small", "theme": "dark"}))
    assert run(monkeypatch, capsys, "--set-context", "medium")[0] == 0
    assert json.loads(path.read_text()) == {"context": "medium", "theme": "dark"}
    # only the three names: any other is exit 2 and one line, and the file is as it was
    before = path.read_text()
    for bad in ("xl", "huge", "", "mediun", "small medium"):
        rc, out = run(monkeypatch, capsys, "--set-context", bad)
        assert (rc, out) == (2, USAGE.format(bad) + "\n"), bad
        assert path.read_text() == before
    for argv in (["--set-context"], ["--set-context", "small", "large"]):
        rc, out = run(monkeypatch, capsys, *argv)
        assert rc == 2 and out == "usage: --principal AGENT --set-context small|medium|large\n", argv
    assert path.read_text() == before
    # a refused name makes no state folder for a principal that has none
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "newcomer", "--set-context", "xl"])
    assert ask.main() == 2 and not (tmp_path / "state" / "newcomer").exists()
    capsys.readouterr()
    # a judge profile with no such size cannot save it either
    with monkeypatch.context() as m:
        m.setattr(judge_profile, "PROFILE", judge_profile.load("laya"))
        assert run(m, capsys, "--set-context", "large") == (2, 'ask: unknown context "large" (choose small)\n')
    assert path.read_text() == before
    # atomic: a write that breaks before the swap leaves the old file whole and no temporary file behind

    def boom(*a, **k):
        raise OSError(errno.EIO, "disk gone")
    with monkeypatch.context() as m:
        m.setattr(os, "replace", boom)
        m.setattr(Path, "replace", boom)
        rc, out = run(m, capsys, "--set-context", "small")
    assert rc == 3 and out.count("\n") == 1 and out.startswith("ask: could not save the context") and "disk gone" in out
    assert path.read_text() == before and sorted(p.name for p in path.parent.iterdir()) == ["settings.json"]
    # a program's --json does not cover it
    rc, out = run(monkeypatch, capsys, "--json", "--set-context", "small")
    assert rc == 2 and json.loads(out)["outcome"] == "not-supported" and path.read_text() == before


@pytest.mark.shipped_table
def test_the_names_are_the_ones_the_shipped_jev_profile_states():
    table = json.loads((SKILL / "judge_profiles.json").read_text())["profiles"][judge_profile.PROFILE.name]
    assert ask.context_setting.NAMES == ("small", *table["contexts"])
    assert ask.context_setting.NAMES == ("small", "medium")  # what the docs and the terminal app offer: Large waits for its own test


@pytest.mark.shipped_table
def test_the_usage_text_states_the_sizes_the_profile_states():
    """ask.py --help names how much each size reads: the numbers are the profile's, so they cannot drift from it."""
    doc = " ".join(ask.__doc__.split())
    for name in ask.context_setting.NAMES:
        assert f"{judge_profile.load(context=name).read_chars:,}" in doc, name
    for words in ("--set-context", "--context", "SUPERJEV_CONTEXT", "settings.json", "default_context", "context ({preset, source}",
                  "context: {preset, default, source}"):
        assert words in doc, words


# ---------------------------------------------------------------- what goes to the judge
def note(chars):
    """A plain note of at least `chars` characters."""
    lines = ["# Harbor log\n"]
    i = 0
    while sum(map(len, lines)) < chars:
        lines.append(f"- amber anchor basil beacon entry {i}: cedar copper delta ember noted again.\n")
        i += 1
    lines[len(lines) // 2] = "The zeppelin quokka inspection is scheduled for the ninth Thursday.\n"
    return "".join(lines)


def test_small_payload_has_no_context_key_and_medium_payload_has_one(tmp_path, monkeypatch):
    f = tmp_path / "harbor.md"
    f.write_text(note(30_000))
    short = tmp_path / "short.md"
    short.write_text("# Short\nThe zeppelin quokka inspection is on Thursday.\n")

    def payload(path=f):
        done, ctx = ask.confirm_start(QUESTION, str(path))
        assert done is None
        return ctx["payload"]

    at(monkeypatch, "small", "default")
    plain = payload()
    assert list(plain) == ["question", "limits", "catalog", "mode"]  # what it always sent: no context key
    for preset in ("medium", "large"):
        at(monkeypatch, preset)
        p = payload()
        assert p["context"] == preset and {k: v for k, v in p.items() if k != "context"}.keys() == plain.keys()
        assert payload(short)["context"] == preset  # every file of an ask at that size, short or long, says so
        # a claim check reads at small, so it says nothing of the size
        monkeypatch.setitem(ask._CLAIM, "text", "The zeppelin quokka inspection is on Thursday")
        assert "context" not in payload() and "context" not in payload(short)
        monkeypatch.setitem(ask._CLAIM, "text", None)
        # nor does a file re-sent at small after a read at the bigger size failed
        ask._CONTEXT["small_paths"].add(str(f))
        assert "context" not in payload() and payload(short)["context"] == preset
    # the content check's navigation run gets the key on every file it checks, alone and in a batch
    at(monkeypatch, "medium")
    runs = []

    def fake_run(body):
        runs.append(body)
        return ({"results": [{"status": "no-candidates", "candidates": []} for _ in body["batch"]]} if "batch" in body
                else {"status": "no-candidates", "candidates": []}), None
    monkeypatch.setattr(ask, "run_navigation", fake_run)
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "1")
    ask.confirm(QUESTION, [str(f), str(short)])
    assert len(runs) == 1 and [item["context"] for item in runs[0]["batch"]] == ["medium", "medium"]
    runs.clear()
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")
    ask.confirm(QUESTION, [str(f), str(short)])
    assert [r["context"] for r in runs] == ["medium", "medium"]


class FakeRun:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode, self.stderr = stdout, returncode, ""


def test_routing_and_near_twin_spawns_never_carry_a_context(tmp_path, monkeypatch):
    """At the biggest size, of everything one ask sends to the judge only the content check's files carry the size."""
    monkeypatch.setenv("SUPERJEV_INDEX", "0")
    monkeypatch.setattr(ask, "spawn_index_updater", lambda principal: None)
    at(monkeypatch, "large", "env")
    folder = tmp_path / "harbor"
    folder.mkdir()
    big, twin = folder / "notes.md", folder / "notes-summary.md"
    big.write_text(note(40_000))
    twin.write_text("A short summary of the harbor log.\n")
    # near twins: one navigation spawn over their snippets
    spawned = []
    real_run = ask.subprocess.run

    def spawn(cmd, *args, **kw):
        if list(cmd) != list(ask.navigation_command()):
            return real_run(cmd, *args, **kw)
        spawned.append(json.loads(kw["input"]))
        return FakeRun(json.dumps({"status": "candidates", "candidates": [{"sourceId": str(big), "score": 0.9}]}))
    monkeypatch.setattr(ask.subprocess, "run", spawn)
    assert ask.judge_near_twin(QUESTION, [(0.9, str(big), "bare"), (0.87, str(twin), "bare")]) == str(big)
    assert len(spawned) == 1 and "context" not in spawned[0]
    # routing: a set with no local rows is asked what files it names (one request to the engine)
    requests, checks = [], []

    def memory(req):
        requests.append(req)
        if req["action"] == "cached":
            return {"status": "miss"}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "bare", "snapshotStatus": "available"}]}
        return {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": str(big)}]}
    monkeypatch.setattr(ask, "memory", memory)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})
    monkeypatch.setattr(ask, "run_navigation", lambda payload: checks.append(payload) or ({"status": "no-candidates", "candidates": []}, None))
    ask.lookup(QUESTION, "me", tmp_path / "state" / "me")
    routing = [r for r in requests if r["action"] in ("navigate", "navigate-many")]
    assert routing and all("context" not in r for r in routing)  # routing stays at small
    assert checks and [c.get("context") for c in checks] == ["large"] * len(checks)  # the content check reads at the size
    # nothing but ask.py reads the variable, so no other program can pick a size for itself: it is named in ask.py and its
    # module, and the terminal app names it in what it says (it hands its environment to the helper untouched and reads none)
    named = {str(p.relative_to(REPO)): p.read_text(errors="replace") for root in ("skills", "src", "experiments")
             for p in (REPO / root).rglob("*")
             if p.is_file() and p.suffix in (".py", ".ts", ".sh", ".mjs", ".js") and "tests" not in p.parts}
    assert {name for name, text in named.items() if "SUPERJEV_CONTEXT" in text} == {
        "skills/super-jev/ask.py", "skills/super-jev/context_setting.py", "src/jev-chat-cli.ts", "src/jev-chat-config.ts"}
    assert not [name for name, text in named.items() if name.startswith("src/") and re.search(r"env[\w.]*[\[.(]\s*['\"]?SUPERJEV_CONTEXT", text)]
