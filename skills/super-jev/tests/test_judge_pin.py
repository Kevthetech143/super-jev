"""Judge pin + judge multi-run: env-var wiring and retry behaviour for the
TypeSafe judge call.

The pin logic lives in the TypeScript request builder (src/jev.ts); these
tests drive its unit tests (`node --test test/jev-pin.test.ts`, fake
transport, no live TypeSafe call) through the resolved node binary and fail
this pytest run if any of them fail.

GOAL A of judgepin-000900: the pin is OPT-IN. SUPERJEV_JUDGE_TEMPERATURE
(default unset) and SUPERJEV_JUDGE_SEED (default unset) are sent in the judge
request body only when set; when a pin IS sent and the call gets ANY non-2xx,
it retries once WITHOUT the pin and logs a "judge pin:" note ONLY when the
retry succeeds — when the retry also fails the thrown error keeps both texts
("pin rejected: <original>; unpinned retry: <second>") and nothing is logged.
With the env unset the request is exactly the pre-pin call, so an unpinned
failure can never fire a second billed judge call.

GOAL B of judgepin2-001500: SUPERJEV_JUDGE_RUNS=N calls the judge N times
per evaluate; run 1 is canonical, every run's decision summary is recorded,
and the case is marked unstable when the runs disagree. A failure on a
later run keeps run 1's evaluation and is recorded in judgeRuns.runErrors
instead of throwing.
"""
import os
import re
import shutil
import subprocess

# R6: resolve node from PATH, falling back to the pinned nvm install.
NODE = shutil.which("node") or "/Users/admin/.nvm/versions/node/v24.11.1/bin/node"
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _expected_node_cases():
    # The node case count is read from test/jev-pin.test.ts at runtime and
    # never hand-maintained: one node:test `test(` declaration per case,
    # each at the start of its own line. The suite test below asserts it so
    # the file can never pass vacuously (e.g. "pass 0" after a bad edit).
    path = os.path.join(REPO, "test", "jev-pin.test.ts")
    with open(path, encoding="utf-8") as fh:
        return len(re.findall(r"(?m)^test\(", fh.read()))


EXPECTED_NODE_CASES = _expected_node_cases()

_PIN_ENV = ("SUPERJEV_JUDGE_TEMPERATURE", "SUPERJEV_JUDGE_SEED",
            "SUPERJEV_JUDGE_RUNS")


def _clean_env():
    return {k: v for k, v in os.environ.items() if k not in _PIN_ENV}


def _assert_node_counts(out, expected_passes, label):
    # R6: assert the exact pass count line, not just the word "pass" (which
    # "pass 0" would satisfy). The \b guards keep "pass 13" from matching
    # when exactly "pass 1" is expected.
    m = re.search(r"\bpass (\d+)\b", out)
    assert m is not None and int(m.group(1)) == expected_passes, \
        "%s: expected exactly %d passing node test(s), saw %r:\n%s" % (
            label, expected_passes, m.group(1) if m else None, out[-2000:])
    assert re.search(r"\bfail 0\b", out), \
        "%s: expected zero node failures:\n%s" % (label, out[-2000:])


def test_judge_pin_node_suite_passes():
    assert os.path.exists(os.path.join(REPO, "test", "jev-pin.test.ts")), \
        "test/jev-pin.test.ts is missing"
    assert os.path.exists(NODE), "node binary is missing: %s" % NODE
    proc = subprocess.run(
        [NODE, "--test", "test/jev-pin.test.ts"],
        cwd=REPO, capture_output=True, text=True, timeout=300,
        env=_clean_env(),
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, \
        "node --test test/jev-pin.test.ts failed (rc=%d):\n%s" % (
            proc.returncode, out[-4000:])
    _assert_node_counts(out, EXPECTED_NODE_CASES, "full suite")


def _run_node_cases(name_pattern, expected_passes):
    """Run a subset of test/jev-pin.test.ts by name through the resolved node."""
    assert os.path.exists(os.path.join(REPO, "test", "jev-pin.test.ts")), \
        "test/jev-pin.test.ts is missing"
    assert os.path.exists(NODE), "node binary is missing: %s" % NODE
    proc = subprocess.run(
        [NODE, "--test", "--test-name-pattern=" + name_pattern,
         "test/jev-pin.test.ts"],
        cwd=REPO, capture_output=True, text=True, timeout=300,
        env=_clean_env(),
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, \
        "node --test --test-name-pattern=%r failed (rc=%d):\n%s" % (
            name_pattern, proc.returncode, out[-4000:])
    _assert_node_counts(out, expected_passes,
                        "pattern %r" % name_pattern)


def test_judge_pin_masking_case():
    """The pin-rejected failure is not masked when the unpinned retry also fails."""
    _run_node_cases("pin rejected", 1)


def test_judge_pin_runs_two_unstable_case():
    """SUPERJEV_JUDGE_RUNS=2 records both decisions and flags disagreement."""
    _run_node_cases("SUPERJEV_JUDGE_RUNS=2", 2)
