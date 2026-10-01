#!/usr/bin/env python3
"""Offline tests for a stock machine's first run: setup.py names what is missing (found: ...)
and prints the exact, no-sudo steps to get it; the docs repeat those steps word for word; the
key has one file story everywhere.

No network, no real key, no real toolchain probing: the Node and Python versions come through
the two seams setup.py reads them by (`_node_major`, `_python_version`), and every test runs
with HOME and SUPERJEV_STATE_DIR in tmp_path.

    python3 -m pytest skills/super-jev/tests/test_setup_toolchain.py -q
"""
import builtins
import dataclasses
import importlib.util
import io
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parent.parent

NVM_URL = "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.8/install.sh | bash"
EXPORT_LINE = 'export TYPESAFE_API_KEY="$(cat ~/.typesafe-api-key)"'
CREATE_LINE = "(umask 077; cat > ~/.typesafe-api-key)"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


setup = load("setup_toolchain_under_test", SKILL / "setup.py")
judges = setup.judges   # the doorway setup.py imports
prepare_bulk = load("prepare_bulk_toolchain_under_test", SKILL / "prepare_bulk.py")


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("TYPESAFE_API_KEY", "not-a-real-key")
    monkeypatch.setenv("SHELL", "/bin/zsh")
    # a healthy toolchain unless a test says otherwise; raising=False so a missing seam
    # fails the test on what setup prints, not on an AttributeError
    monkeypatch.setattr(setup, "_node_major", lambda: 24)
    monkeypatch.setattr(setup, "_python_version", lambda: (3, 12, 1), raising=False)
    return tmp_path


def run_setup(capsys):
    rc = setup.main([])
    return rc, capsys.readouterr().out


def command_lines(out):
    """The lines setup prints as commands to run: the ones indented 8 spaces."""
    return [ln.strip() for ln in out.splitlines() if ln.startswith(" " * 8) and ln.strip()]


def doc_lines(path):
    return {ln.strip() for ln in Path(path).read_text().splitlines()}


# ------------------------------------------------ what is missing, and how to get it

def test_missing_node_and_old_python_print_no_sudo_install_steps(env, monkeypatch, capsys):
    monkeypatch.setattr(setup, "_node_major", lambda: None)
    monkeypatch.setattr(setup, "_python_version", lambda: (3, 9, 6), raising=False)
    rc, out = run_setup(capsys)
    assert rc == 1
    for literal in ("found: 3.9.6", "found: none",
                    "curl -LsSf https://astral.sh/uv/install.sh | sh",
                    'source "$HOME/.local/bin/env"',
                    "uv python install 3.12 --default",
                    "touch ~/.zshrc",
                    NVM_URL,
                    '\\. "$HOME/.nvm/nvm.sh"',
                    "nvm install 24",
                    "brew install node python"):
        assert literal in out, literal
    assert "sudo" not in out.lower()
    # one list, in one order: Python first, then Node
    assert out.index("uv python install 3.12 --default") < out.index("nvm install 24")
    assert "run setup again" in out


def test_too_old_node_names_version_and_steps(env, monkeypatch, capsys):
    monkeypatch.setattr(setup, "_node_major", lambda: 20)
    monkeypatch.setenv("SHELL", "/bin/bash")
    rc, out = run_setup(capsys)
    assert rc == 1
    assert "found: v20" in out
    assert NVM_URL in out and "nvm install 24" in out and '\\. "$HOME/.nvm/nvm.sh"' in out
    assert "touch ~/.zshrc" not in out         # only printed for zsh
    assert "uv python install" not in out      # Python is fine here
    assert "sudo" not in out.lower()


def test_a_ready_toolchain_prints_no_install_steps(env, capsys):
    rc, out = run_setup(capsys)
    assert rc == 0 and "READY." in out
    assert "nvm" not in out and "uv python" not in out


def test_missing_key_points_at_the_one_key_file(env, monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    key_file = Path(os.environ["HOME"]) / ".typesafe-api-key"

    # no file yet: create it first, then load it
    rc, out = run_setup(capsys)
    assert rc == 1
    assert CREATE_LINE in out and EXPORT_LINE in out
    assert out.index(CREATE_LINE) < out.index(EXPORT_LINE)
    assert "Ctrl-D" in out and "never paste a key into chat" in out
    assert "/path/to" not in out and "sudo" not in out.lower()

    # file present: only the export line, and setup never opens the file
    key_file.write_text("not-a-real-key\n")
    key_file.chmod(0)
    opened = []
    real_open = io.open

    def watching_open(file, *a, **k):
        if str(file) == str(key_file):
            opened.append(file)
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", watching_open)
    monkeypatch.setattr(io, "open", watching_open)
    monkeypatch.setattr(pathlib.Path, "open", lambda self, *a, **k: watching_open(str(self), *a, **k))
    try:
        rc, out = run_setup(capsys)
    finally:
        key_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert rc == 1
    assert EXPORT_LINE in out and CREATE_LINE not in out
    assert opened == []
    assert "not-a-real-key" not in out

    # the judge doorway's own message names the same file
    with pytest.raises(judges.NoKey) as e:
        judges.require_key()
    assert "~/.typesafe-api-key" in str(e.value) and "/path/to" not in str(e.value)


def test_a_keyless_judge_prints_no_key_lines(env, monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    keyless = dataclasses.replace(judges.profile(), key_env="", key_required=False)
    monkeypatch.setattr(judges.judge_profile, "PROFILE", keyless)
    rc, out = run_setup(capsys)
    assert rc == 0 and "READY." in out
    assert "export" not in out and "umask" not in out


def test_install_sh_prints_the_node_steps_when_node_is_missing_or_too_old(tmp_path):
    # a PATH holding only `cat` (a stock machine has it), then the same plus a node that is too old
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "cat").symlink_to(shutil.which("cat"))
    old = tmp_path / "old"
    old.mkdir()
    (old / "cat").symlink_to(shutil.which("cat"))
    fake = old / "node"
    fake.write_text('#!/bin/sh\n[ "$1" = "-v" ] && echo v20.1.0 || echo 20\n')
    fake.chmod(0o755)
    for path in (bare, old):
        for shell in ("/bin/zsh", "/bin/bash"):
            r = subprocess.run(["/bin/bash", str(REPO / "install.sh")], capture_output=True, text=True,
                               env={"PATH": str(path), "HOME": str(tmp_path), "SHELL": shell})
            assert r.returncode == 1, r.stderr
            out = r.stdout + r.stderr
            assert NVM_URL in out and "nvm install 24" in out and '\\. "$HOME/.nvm/nvm.sh"' in out, out
            assert ("touch ~/.zshrc" in out) == shell.endswith("zsh"), out
            assert "brew install node" in out and "sudo" not in out.lower()
    assert "found v20.1.0" in out


def test_npm_refusal_names_setup_py_by_absolute_path(capsys):
    sj = load("superjev_toolchain_under_test", SKILL / "superjev.py")
    assert sj._npm_missing_refusal(False, "sweep") == 1
    err = capsys.readouterr().err
    assert str(SKILL / "setup.py") in err and os.path.isabs(str(SKILL / "setup.py"))


# ------------------------------------------------ the docs say the same thing

def printed_commands(env, monkeypatch, capsys):
    """Every command line setup can print: all problems at once, on zsh."""
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.setattr(setup, "_node_major", lambda: None)
    monkeypatch.setattr(setup, "_python_version", lambda: (3, 9, 6), raising=False)
    rc, out = run_setup(capsys)
    cmds = command_lines(out)
    assert rc == 1 and len(cmds) >= 9, out
    return cmds, out


def test_docs_repeat_setup_install_lines_verbatim(env, monkeypatch, capsys):
    cmds, out = printed_commands(env, monkeypatch, capsys)
    assert EXPORT_LINE in cmds and CREATE_LINE in cmds
    for doc in (REPO / "AGENTS.md", REPO / "docs" / "GETTING-STARTED.md"):
        lines = doc_lines(doc)
        missing = [c for c in cmds if c not in lines]
        assert not missing, f"{doc.name} does not repeat setup's lines: {missing}"
        assert "brew install node python" in doc.read_text()
    # the Node lines are in install.sh too
    sh = doc_lines(REPO / "install.sh")
    node_cmds = [c for c in cmds if "nvm" in c or "zshrc" in c]
    assert len(node_cmds) == 4
    assert [c for c in node_cmds if c not in sh] == []

    # one key file everywhere: every `$(cat X)` in the docs, in setup's output and in the
    # doorway's message is ~/.typesafe-api-key, and no placeholder path is left behind
    with pytest.raises(judges.NoKey) as e:
        judges.require_key()
    judge_msg = str(e.value)
    texts = [(d.name, d.read_text()) for d in (REPO / "AGENTS.md", REPO / "docs" / "GETTING-STARTED.md")]
    texts += [("setup output", out), ("NoKey message", judge_msg)]
    for name, text in texts:
        paths = set(re.findall(r"\$\(cat ([^)]*)\)", text))
        assert paths == {"~/.typesafe-api-key"}, (name, paths)
        assert "/path/to/typesafe-key-file" not in text and "/path/to/your/key-file" not in text, name
    for src in (SKILL / "setup.py", SKILL / "judges" / "__init__.py"):
        assert "key-file" not in src.read_text(), src.name   # the old placeholders end in key-file
    assert "Ask your human where the key file is" not in (REPO / "AGENTS.md").read_text()

    # these docs get connected as notes, and a line that reads as a secret is refused: none of the new lines may
    for c in cmds:
        assert not prepare_bulk.has_secret(c), c
    for doc in (REPO / "AGENTS.md", REPO / "docs" / "GETTING-STARTED.md"):
        assert not prepare_bulk.has_secret(doc.read_text()), doc.name


def test_python_floor_matches_docs_and_ci():
    ci = (REPO / ".github" / "workflows" / "test.yml").read_text()
    assert re.search(r"python[-\w]*\s*:\s*\[[^\]]*['\"]3\.10['\"]", ci), "the CI Python matrix must include the floor, 3.10"
    for doc in (REPO / "AGENTS.md", REPO / "docs" / "GETTING-STARTED.md"):
        assert "3.10" in doc.read_text(), doc.name
    assert "Node 24 or newer" in (REPO / "docs" / "GETTING-STARTED.md").read_text()
    assert getattr(setup, "MIN_PYTHON", None) == (3, 10) and getattr(setup, "MIN_NODE", None) == 24
