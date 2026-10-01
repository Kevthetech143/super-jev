#!/usr/bin/env python3
"""A judge's fingerprint changes exactly when its judge, profile, explicit settings or engine code
change. Against a tmp copy of the real tree: no ask, no judge call, no network.

    python3 -m pytest skills/super-jev/tests/test_judge_fingerprint.py -q
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parent.parent
sys.path.insert(0, str(SKILL))
import judge_support as js  # noqa: E402

DEFAULT = "typesafe-jev"


@pytest.fixture
def build(tmp_path):
    """The real judge path, copied (skills/super-jev with its tests, src, experiments/verified-pointer-memory),
    plus a docs folder, a changelog and a prepared set, which are not on it."""
    root = tmp_path / "build"
    skip = shutil.ignore_patterns("__pycache__", "node_modules", ".pytest_cache")
    for sub in js.JUDGE_PATH:
        shutil.copytree(REPO / sub, root / sub, ignore=skip)
    (root / "docs").mkdir()
    (root / "docs" / "notes.md").write_text("Quillbrook notes\n")
    (root / "CHANGELOG.md").write_text("## Unreleased\n")
    (root / "skills/super-jev/prepare-cache").mkdir()
    (root / "skills/super-jev/prepare-cache/sam-notes.json").write_text('{"files": 1}')
    return root


def fp(root, judge=DEFAULT, env=None):
    return js.fingerprint(root / "skills/super-jev/ask.py", judge, env)["fingerprint"]


def edit_profiles(root, fn):
    path = root / "skills/super-jev/judge_profiles.json"
    data = json.loads(path.read_text())
    fn(data["profiles"])
    path.write_text(json.dumps(data, indent=1))


def append(root, rel, text):
    with open(root / rel, "a") as f:
        f.write(text)


NOT_THE_JUDGE = {
    "another profile": lambda r: edit_profiles(r, lambda p: p["laya"].update(sure_line=0.5)),
    "a _source on this profile": lambda r: edit_profiles(r, lambda p: p[DEFAULT].update(_source="re-measured")),
    "a test file": lambda r: append(r, "skills/super-jev/tests/test_judge_fingerprint.py", "# edit\n"),
    "a doc": lambda r: append(r, "docs/notes.md", "more\n"),
    "the skill's own doc": lambda r: append(r, "skills/super-jev/SKILL.md", "more\n"),
    "the changelog": lambda r: append(r, "CHANGELOG.md", "- a line\n"),
    "a prepared set": lambda r: (r / "skills/super-jev/prepare-cache/sam-notes.json").write_text('{"files": 2}'),
    "a bytecode file": lambda r: (r / "skills/super-jev/__pycache__").mkdir() or
                                 (r / "skills/super-jev/__pycache__/ask.cpython-312.py").write_text("x = 1\n"),
}


@pytest.mark.parametrize("what", sorted(NOT_THE_JUDGE))
def test_the_fingerprint_ignores_what_is_not_the_judge(build, what):
    before = fp(build)
    NOT_THE_JUDGE[what](build)
    assert fp(build) == before


THE_JUDGE = {
    "this profile's sure_line": lambda r: edit_profiles(r, lambda p: p[DEFAULT].update(sure_line=0.5)),
    "this profile's model": lambda r: edit_profiles(r, lambda p: p[DEFAULT].update(model="other-model")),
    "an engine .py": lambda r: append(r, "skills/super-jev/ask.py", "\n# edit\n"),
    "a lib .py": lambda r: append(r, "skills/super-jev/lib/jev_client.py", "\n# edit\n"),
    "an engine .ts": lambda r: append(r, "src/jev.ts", "\n// edit\n"),
    "a memory .py": lambda r: append(r, "experiments/verified-pointer-memory/cli.py", "\n# edit\n"),
    "a memory .mjs": lambda r: append(r, "experiments/verified-pointer-memory/chunk_paths.mjs", "\n// edit\n"),
    "secret_patterns.json": lambda r: append(r, "skills/super-jev/secret_patterns.json", "\n"),
    "a new engine .sh": lambda r: (r / "skills/super-jev/wrap.sh").write_text("#!/bin/sh\n"),
}


@pytest.mark.parametrize("what", sorted(THE_JUDGE))
def test_the_fingerprint_changes_when_the_judge_or_the_engine_changes(build, what):
    before = fp(build)
    THE_JUDGE[what](build)
    assert fp(build) != before


def test_an_explicit_setting_changes_it_by_value_and_by_name_but_not_by_order(build):
    base = fp(build)
    one = fp(build, env={"SUPERJEV_QUIET": "1"})
    assert one != base
    assert fp(build, env={"SUPERJEV_QUIET": "2"}) != one
    assert fp(build, env={"SUPERJEV_LOUD": "1"}) != one
    two = {"A_SETTING": "x", "B_SETTING": "y"}
    assert fp(build, env=dict(reversed(list(two.items())))) == fp(build, env=two)


def test_an_alias_hashes_like_its_profile_and_fake_never_hashes_like_the_default(build):
    assert fp(build, "typesafe") == fp(build, DEFAULT)
    parts = js.fingerprint(build / "skills/super-jev/ask.py", "fake")
    default = js.fingerprint(build / "skills/super-jev/ask.py", DEFAULT)
    assert parts["implementation"] == "fake" and default["implementation"] == "profile"
    assert parts["judge"] == default["judge"] == DEFAULT      # fake uses the default's numbers ...
    assert parts["fingerprint"] != default["fingerprint"]     # ... and still never stands in for it
    assert fp(build, "laya") != fp(build, DEFAULT)


def test_it_reads_the_builds_own_table_and_an_unknown_judge_is_a_value_error(build):
    edit_profiles(build, lambda p: p.update(sam=dict(p["laya"], aliases=["sam-alias"])))
    assert fp(build, "sam-alias") == fp(build, "sam")
    with pytest.raises(ValueError, match="unknown judge 'nope'"):
        fp(build, "nope")
    (build / "skills/super-jev/judge_profiles.json").write_text("not json")
    with pytest.raises(ValueError, match="judge limits table"):
        fp(build)


def run_cli(*args, env=None):
    return subprocess.run([sys.executable, str(SKILL / "judge_support.py"), *args], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", **(env or {})})


def test_the_command_prints_the_fingerprint_as_json_and_never_a_setting_value(build):
    ask = str(build / "skills/super-jev/ask.py")
    r = run_cli("--fingerprint", "--ask", ask, "--judge", "typesafe", "--env", "SAM_TOKEN=quillbrook-secret-value")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["fingerprint"] == fp(build, env={"SAM_TOKEN": "quillbrook-secret-value"})
    assert "quillbrook-secret-value" not in r.stdout + r.stderr
    assert set(out) == {"judge", "implementation", "profile_sha256", "judge_path_sha256", "env_sha256", "fingerprint"}
    # A stray SUPERJEV_JUDGE in the caller's shell does not stop it: this tool never judges.
    assert run_cli("--fingerprint", "--ask", ask, "--judge", "laya", env={"SUPERJEV_JUDGE": "bogus"}).returncode == 0


@pytest.mark.parametrize("args", [
    ["--judge", "nope"],
    ["--judge", DEFAULT, "--env", "NO_EQUALS_SIGN"],
    ["--judge", DEFAULT, "--env", "A=1", "--env", "A=2"],
    ["--judge", DEFAULT, "--env", "=value"],
])
def test_bad_input_is_exit_2_with_a_line_that_says_why(build, args):
    r = run_cli("--fingerprint", "--ask", str(build / "skills/super-jev/ask.py"), *args)
    assert r.returncode == 2 and r.stderr.strip().splitlines()[-1].startswith("judge_support.py: error:"), r.stderr
    assert r.stdout == ""


def test_a_missing_build_is_exit_2(tmp_path):
    r = run_cli("--fingerprint", "--ask", str(tmp_path / "nowhere/skills/super-jev/ask.py"), "--judge", DEFAULT)
    assert r.returncode == 2 and "judge limits table" in r.stderr


def test_the_same_build_in_another_folder_has_the_same_fingerprint(build, tmp_path):
    other = tmp_path / "releases" / "main-20261001"
    shutil.copytree(build, other)
    assert fp(other) == fp(build)
