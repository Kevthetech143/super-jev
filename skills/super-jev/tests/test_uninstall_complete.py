#!/usr/bin/env python3
"""Offline tests: --uninstall removes everything Super Jev wrote, and only that.

One rule: in every folder Super Jev writes, delete the names it writes, keep everything
else, and remove the folder once it is empty. Every test runs with HOME, the state folder,
the chat config folder, the launcher folder and the in-repo folders inside tmp_path.
Made-up data only; no network and no key.

    python3 -m pytest skills/super-jev/tests/test_uninstall_complete.py -q
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


setup = load("setup_uninstall_complete", SKILL / "setup.py")
ah = load("auto_heal_uninstall_complete", SKILL / "auto_heal.py")


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("SUPERJEV_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    folders = (tmp_path / "prepare-cache", tmp_path / "ledger", tmp_path / "autoheal-state")
    monkeypatch.setattr(setup, "IN_REPO_LEFTOVERS", folders)
    return tmp_path


def touch(path: Path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def made_state(tmp_path):
    """The state folder as setup.py makes it (the marker is what lets uninstall touch it)."""
    marker = touch(tmp_path / "state" / "_memory" / "config.json", "{}")
    return marker.parent.parent


# (k) a real offline ask, then uninstall: nothing is left, and no false "not made by Super Jev" line
def test_uninstall_after_an_ask_with_nothing_connected_leaves_no_state(env, capsys):
    assert setup.main([]) == 0
    run_env = {**os.environ, "SUPERJEV_SKILLS": "0", "SUPERJEV_NEW_FILE_SCAN": "0",
               "SUPERJEV_SHARED_POINTERS": str(env / "none.json")}
    asked = subprocess.run([sys.executable, str(SKILL / "ask.py"), "--principal", "me", "where is my lease?"],
                           env=run_env, capture_output=True, text=True)
    assert asked.returncode == 4 and asked.stdout.startswith("OUTCOME: needs-setup")
    assert any((env / "state" / "me").iterdir()), "the ask must have left logs, or this test proves nothing"
    capsys.readouterr()
    assert setup.main(["--uninstall"]) == 0
    assert not (env / "state").exists()
    assert "left in place" not in capsys.readouterr().out


# (l) every name a principal folder gets is removed
PRINCIPAL_NAMES = ["lookups.jsonl", "traces.jsonl", "traces.jsonl.1", "approvals.jsonl", "claim-verdicts.json",
                   "pointer_health.json", "pointer-words.json", "pointer-words.4242.tmp", "index-sync.stamp", "word-index.json", "set-rows.json",
                   "scorecard-cases.jsonl", "scorecard-cases.tmp", "manual/a-note.md", "github/acme-widgets/README.md"]


def test_every_principal_file_name_is_removed(env):
    state = made_state(env)
    for name in PRINCIPAL_NAMES:
        touch(state / "me" / name)
    assert setup.main(["--uninstall"]) == 0
    assert not state.exists()


# (m) a user's file next to the logs stays; the logs go
def test_a_user_file_in_a_principal_folder_stays_and_the_log_goes(env, capsys):
    state = made_state(env)
    touch(state / "me" / "lookups.jsonl")
    touch(state / "me" / "notes.md", "mine")
    assert setup.main(["--uninstall"]) == 0
    assert (state / "me" / "notes.md").read_text() == "mine"
    assert not (state / "me" / "lookups.jsonl").exists()
    assert not (state / "_memory").exists()
    out = capsys.readouterr().out
    assert "left in place" in out and "notes.md" in out


# (n) the auto-heal folder, written by auto_heal's own writers, is removed
def test_autoheal_state_written_by_its_own_writers_is_removed(env, monkeypatch):
    state = made_state(env)
    heal = env / "autoheal-state"
    monkeypatch.setattr(ah, "STATE_DIR", heal)
    monkeypatch.setattr(ah, "LOG_PATH", heal / "autoheal.log")
    ah._log(action="started")
    ah._queue("me", "notes", "refresh")
    assert ah._acquire_lock("me", "notes")
    touch(heal / "me-notes-last-refresh.log")
    assert len(list(heal.iterdir())) >= 5
    assert setup.main(["--uninstall"]) == 0
    assert not heal.exists() and not state.exists()


# (o) the ledger folder: every name the call and catch ledgers write
LEDGER_NAMES = ["calls.jsonl", "catches.jsonl", "catches.jsonl.lock", "catches.jsonl.tmp", "catch-cases.json",
                "catch-cases.json.tmp", "signals.jsonl", "payloads/abc.json", "state/receipts-s1.jsonl",
                "state/stop-state-s1.json", "last/gate-draft.md", "calibration/run.json"]


def test_every_ledger_name_is_removed_and_a_user_file_there_stays(env):
    made_state(env)
    ledger = env / "ledger"
    for name in LEDGER_NAMES:
        touch(ledger / name)
    touch(ledger / "keep-me.txt", "mine")
    assert setup.main(["--uninstall"]) == 0
    assert [p.name for p in ledger.iterdir()] == ["keep-me.txt"]


# (p) the chat CLI's config (it holds an API key) and its launcher
def launcher_text(checkout: Path) -> str:
    return f'#!/usr/bin/env bash\n# super-jev-installer\nexec node "{checkout}/src/jev-chat-cli.ts" "$@"\n'


def test_chat_config_and_our_own_launcher_are_removed(env):
    made_state(env)
    touch(env / "xdg" / "superjev" / "config.json", json.dumps({"typesafeApiKey": "sk-made-up"}))
    touch(env / "bin" / "superjev", launcher_text(REPO))
    assert setup.main(["--uninstall"]) == 0
    assert not (env / "xdg" / "superjev").exists()      # empty after the config went
    assert (env / "xdg").is_dir()                       # the user's config home is never ours
    assert not (env / "bin" / "superjev").exists()


def test_an_unrelated_file_next_to_the_chat_config_stays(env):
    made_state(env)
    touch(env / "xdg" / "superjev" / "config.json")
    touch(env / "xdg" / "superjev" / "other.txt", "mine")
    assert setup.main(["--uninstall"]) == 0
    assert not (env / "xdg" / "superjev" / "config.json").exists()
    assert (env / "xdg" / "superjev" / "other.txt").read_text() == "mine"


def test_the_key_file_in_home_stays(env, capsys):
    """The terminal app saves a pasted key in ~/.typesafe-api-key; hooks and agents read it too.
    The docs (README, AGENTS, GETTING-STARTED) say uninstall keeps it, so it must."""
    made_state(env)
    key = touch(env / "home" / ".typesafe-api-key", "sk-made-up")
    assert setup.main(["--uninstall"]) == 0
    assert key.read_text() == "sk-made-up"
    assert ".typesafe-api-key" not in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["another-checkout", "no-marker", "marker-but-not-an-exec"])
def test_a_launcher_that_is_not_ours_stays(env, kind):
    text = {
        "another-checkout": launcher_text(Path("/somewhere/else/super-jev")),
        "no-marker": '#!/usr/bin/env bash\nexec node "%s/src/jev-chat-cli.ts" "$@"\n' % REPO,
        "marker-but-not-an-exec": "#!/usr/bin/env bash\n# super-jev-installer\necho my own script\n",
    }[kind]
    made_state(env)
    touch(env / "bin" / "superjev", text)
    assert setup.main(["--uninstall"]) == 0
    assert (env / "bin" / "superjev").read_text() == text


# (q) drift guard: a name a shipped writer builds must be one uninstall removes
WRITER = re.compile(
    r"""(?P<base>\bsdir|\b_state_dir\([^()\n]*\)|\bSTATE_DIR|\bCATCH_LEDGER_PATH\.parent|\bLEDGER_PATH\.parent)
        \s*/\s*(?P<chain>f?"[^"\n]*"(?:\s*/\s*f?"[^"\n]*")*)""", re.X)


def writer_names():
    """(folder, relative path) for every `base / "name"` writer in the shipped Python, with
    each {placeholder} filled in with 'x'. Narrow on purpose: a tripwire, not proof."""
    found = set()
    for path in sorted(SKILL.glob("*.py")) + sorted((SKILL / "lib").glob("*.py")):
        for m in WRITER.finditer(path.read_text()):
            base = m.group("base")
            if base == "STATE_DIR" and path.name != "auto_heal.py":
                continue
            folder = ("principal" if base.startswith(("sdir", "_state_dir"))
                      else "autoheal-state" if base == "STATE_DIR" else "ledger")
            parts = re.findall(r'f?"([^"\n]*)"', m.group("chain"))
            found.add((folder, "/".join(re.sub(r"\{[^}]*\}", "x", p) for p in parts)))
        consts = dict(re.findall(r'^([A-Z][A-Z0-9_]*)\s*=\s*"([^"\n]+)"', path.read_text(), re.M))
        for m in re.finditer(r"\bsdir\s*/\s*([A-Z][A-Z0-9_]*)\b", path.read_text()):
            if m.group(1) in consts:
                found.add(("principal", consts[m.group(1)]))
    return sorted(found)


def test_a_connected_ask_with_the_index_default_leaves_nothing_after_uninstall(env, monkeypatch, capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_index_read_path as t
    notes, names, sdir = t.build(env, monkeypatch, 30)
    t.Rig(monkeypatch, notes, names)
    monkeypatch.delenv("SUPERJEV_INDEX", raising=False)  # unset = on
    monkeypatch.setattr(t.ask, "_engine_target", lambda: None)
    monkeypatch.setattr(t.ask, "spawn_index_updater", lambda principal: None)  # no detached process in a test
    t.sync(sdir)
    rc, out = t.ask_it(t.PLANTED[0][0], sdir, capsys)
    assert rc == 0
    assert (sdir / "index.sqlite").exists() and (sdir / "index-sync.stamp").exists(), "the ask must have used the index"
    touch(sdir.parent / "_memory" / "config.json", "{}")
    assert setup.main(["--uninstall"]) == 0
    assert not sdir.parent.exists()


def test_the_scan_finds_the_writers_it_is_meant_to_watch():
    names = writer_names()
    assert ("principal", "traces.jsonl") in names and ("autoheal-state", "autoheal.log") in names
    assert ("ledger", "payloads") in names and len(names) >= 20


def test_every_shipped_writer_name_is_removed_by_uninstall(env):
    state = made_state(env)
    where = {"principal": state / "me", "ledger": env / "ledger", "autoheal-state": env / "autoheal-state"}
    names = writer_names()
    # "payloads" and "payloads/x" are one folder: make the inner file, not a file named like the folder
    names = [(f, r) for f, r in names if not any(f2 == f and r2.startswith(r + "/") for f2, r2 in names)]
    made = [touch(where[folder] / rel) for folder, rel in names]
    assert setup.main(["--uninstall"]) == 0
    left = [str(p) for p in made if p.exists()]
    assert not left, f"uninstall left Super Jev's own files behind: {left}"
