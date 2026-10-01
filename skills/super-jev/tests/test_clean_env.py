#!/usr/bin/env python3
"""The suite gives the same result whatever the caller's shell exports.

conftest.py clears, before any test loads, the product's own settings: every SUPERJEV_* except
SUPERJEV_TEST_*, every judge profile's key and url variable (read from judge_profiles.json),
and SWEEP_BATCH. This pins that: once as a plain check inside a normal run, and once against a
deliberately polluted shell, so the rule is proved here and not only by whatever the caller
happens to have exported.

    python3 -m pytest skills/super-jev/tests/test_clean_env.py -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parent.parent
THIS = Path(__file__).resolve()
PROFILES = json.loads((SKILL / "judge_profiles.json").read_text())["profiles"]

# set per test by the autouse fixtures in conftest.py, so they are expected to be present
FIXTURE_SET = {"SUPERJEV_BATCH_JEV", "SUPERJEV_SKILLS", "SUPERJEV_SHARED_POINTERS", "SUPERJEV_NEW_FILE_SCAN",
               "SUPERJEV_BIN_DIR"}


def judge_variables():
    names = set()
    for profile in PROFILES.values():
        names.update(v for v in (profile.get("key_env"), profile.get("api_url_env")) if v)
    return names


def test_test_run_starts_from_a_clean_environment():
    stray = sorted(k for k in os.environ
                   if k.startswith("SUPERJEV_") and not k.startswith("SUPERJEV_TEST_") and k not in FIXTURE_SET)
    assert stray == [], f"the caller's settings reached the tests: {stray}"
    leaked = sorted(judge_variables() & set(os.environ))
    assert leaked == [], f"a judge key or url reached the tests: {leaked}"
    assert "SWEEP_BATCH" not in os.environ


def test_a_polluted_shell_is_scrubbed_before_any_test_loads():
    polluted = dict(os.environ)
    for name in judge_variables():
        polluted[name] = "not-a-real-value"
    polluted.update({
        "SUPERJEV_JUDGE": "typesafe",
        "SUPERJEV_STATE_DIR": "/nonexistent/not-a-real-state-dir",
        "SUPERJEV_WRITER_COMMAND": "false",
        "SUPERJEV_AUTO_CACHE": "0",
        "SWEEP_BATCH": "7",
        "SUPERJEV_TEST_KEEP": "kept",     # developer test knobs are the one thing left alone
        "TYPESAFE_API_KEY_FILE": "/nonexistent/not-a-real-file",
    })
    node = f"{THIS}::test_test_run_starts_from_a_clean_environment"
    r = subprocess.run([sys.executable, "-m", "pytest", node, "-q", "-p", "no:cacheprovider"],
                       cwd=REPO, env=polluted, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 passed" in r.stdout, r.stdout


def test_test_knobs_survive_the_scrub():
    # run conftest.py's module-level scrub in a fresh process and see what is left
    code = (
        "import os, runpy, sys\n"
        "os.environ['SUPERJEV_TEST_KEEP'] = 'kept'\n"
        "os.environ['SUPERJEV_JUDGE'] = 'typesafe'\n"
        f"runpy.run_path({str(THIS.parent / 'conftest.py')!r})\n"
        "print(os.environ.get('SUPERJEV_TEST_KEEP', 'gone'), os.environ.get('SUPERJEV_JUDGE', 'gone'))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["kept", "gone"], r.stdout
