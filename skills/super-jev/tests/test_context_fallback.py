#!/usr/bin/env python3
"""Offline tests for the fallback to small (context setting 5/7): what an ask does when a content check at the medium or
large size fails.

A file's check at a bigger size can fail: too big for the judge, a long read that ran out of time, the long-input mode
turned down, the network, retries used up. When it does, and small would have read less of that note (its passages cost
more than small's 14,000 characters), the file is planned again at small and sent again, once, in a run that carries no
`context` key (so no long-input header, small's ceilings and small's timeout). That happens right after the batched run
and before the old network and timeout re-checks, which then read the file at small too. What that run answers is the
file's answer, and the ask says so:

  - one line, right after the OUTCOME line, in the words the design fixed (it starts `CONTEXT:`);
  - `context.fallback` in the --json object ({files, reason, message}) and `stages.context` in the trace;
  - the exit code and the OUTCOME are those of the run that produced the answer.

Small has nothing new. A file that fits small is never sent again for this reason; a re-send that fails leaves the first
error as it was and says nothing; and an ask at the preset small adds no retry.

No network, no key, no Jev call: the navigation-cli process is replaced by a stand-in that records every input it is
handed and answers each file by a rule the test states.

    python3 -m pytest skills/super-jev/tests/test_context_fallback.py -q
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_context_fallback", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

REAL_RUN = subprocess.run
QUESTION = "when is the zeppelin quokka inspection scheduled"
ANSWER = "The zeppelin quokka inspection is scheduled for the ninth Thursday of each quarter.\n"
SCORE = 0.97  # what the stand-in judge says for a passage that holds the answer: made up
SMALL_READ = 14_000  # characters of one note small reads (the profile's read_chars)

# What the content check reports, in the words navigation-cli and the judge's door use.
TIMEOUT = ask.NAV_TIMED_OUT
BAD_REQUEST = "Navigation provider failed: Jev HTTP 400"
OVERLOADED = "Navigation provider failed: Jev HTTP 503"
NETWORK = "Navigation provider failed: could not reach TypeSafe (network)"

# The line, written out here in full: the words are fixed, so they are not built from the code's own pieces.
SLOW_AT_LARGE = ("CONTEXT: Large did not work this time (the judge was slow to answer), so this ask used Small. "
                 "Small reads less of each note. Try again later, or pick Small yourself with --context small.")


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    """No staged zoom picks, no claim, the preset small, a state folder of its own, and no detached index run."""
    monkeypatch.setattr(ask, "_STAGE", {})
    monkeypatch.setitem(ask._CLAIM, "text", None)
    monkeypatch.setattr(ask, "_CONTEXT", {"name": "small", "source": "default", "small_paths": set()})
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SUPERJEV_AUTO_CACHE", "0")
    monkeypatch.setenv("SUPERJEV_INDEX", "0")
    monkeypatch.setattr(ask, "spawn_index_updater", lambda principal: None)


@pytest.fixture(params=[False, True], ids=["one-call-per-file", "one-batched-run"])
def batched(request, monkeypatch):
    """The two ways a content check runs: file by file (SUPERJEV_BATCH_JEV=0, which the other suites fake) and every file in
    one batched run (what an ask does)."""
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "1" if request.param else "0")
    return request.param


# ---------------------------------------------------------------- the notes
def note(chars, title="Harbor log"):
    """A plain note of about `chars` characters with ANSWER in the middle of it. Over 14,000 it is a long read at a bigger
    size: small cuts it to the passages that match, the bigger sizes read all of it."""
    lines = [f"# {title}\n"]
    i = 0
    while sum(map(len, lines)) < chars:
        lines.append(f"- amber anchor basil beacon entry {i}: cedar copper delta ember noted again.\n")
        i += 1
    lines[len(lines) // 2] = ANSWER
    return "".join(lines)


LONG, LONG_TOO, SHORT = note(20_000), note(20_000, "Dock log"), "# Short log\n" + ANSWER  # SHORT is whole at small


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def connect(tmp_path, monkeypatch, notes):
    """One connected set holding `notes` ({file name: text}), every one of them routed for the question: the read list is
    these files, in this order. Only the engine is replaced. Returns the paths."""
    folder = tmp_path / "set"
    folder.mkdir()
    cache = {}
    for name, text in notes.items():
        (folder / name).write_text(text)
        cache[str(folder / name)] = {"sha256": hashlib.sha256(text.encode()).hexdigest(), "pass": True, "description": "", "question": ""}
    routed = [{"score": 0.9 - n / 100, "originalPath": str(folder / name)} for n, name in enumerate(notes)]

    def memory(req):
        if req["action"] == "cached":
            return {"status": "miss"}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1"}]}
        return {"status": "candidates", "candidates": routed}
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: cache)
    monkeypatch.setattr(ask, "memory", memory)
    return [str(folder / name) for name in notes]


# ---------------------------------------------------------------- the navigation-cli stand-in
def passages(item):
    """The passages one file's payload sends (the leaves of its catalog)."""
    return [n for n in item["catalog"]["nodes"] if n["id"] != "root"]


def sent(item):
    """Characters of passage text one file's payload sends."""
    return sum(len(n["description"]) for n in passages(item))


def title(item):
    """Which note a payload is: the first line of the first passage it sends."""
    return passages(item)[0]["description"].splitlines()[0]


def answers(item):
    """A file's row from a judge that finds the answer in the passage that states it, and none in a file that does not."""
    for n in passages(item):
        if "zeppelin" in n["description"]:
            return {"status": "candidates", "candidates": [{"sourceId": n["sourceId"], "nodeId": n["id"], "path": ["root", n["id"]], "score": SCORE}]}
    return {"status": "no-candidates", "candidates": []}


def finds_nothing(item):
    return {"status": "no-candidates", "candidates": []}


def failed(reason):
    """One file's check that ended in an error (a row of the batch)."""
    return {"status": "error", "reason": reason}


class Down(Exception):
    """The navigation-cli process itself fails (a non-zero exit and the last line it printed), not one file's check."""
    def __init__(self, stderr):
        super().__init__(stderr)
        self.stderr = stderr


def times_out():
    raise subprocess.TimeoutExpired("node", 120)


def too_large_to_run():
    raise Down("Navigation input is too large")


def only_the_bigger_size_fails(fault):
    """A rule: a file planned at a bigger size gets `fault()` (a row, or it raises); planned at small it is answered."""
    return lambda item: fault() if "context" in item else answers(item)


def long_reads_fail(fault):
    """A rule: a read at a bigger size that is more than small reads gets `fault()`; every other read is answered."""
    return lambda item: fault() if "context" in item and sent(item) > SMALL_READ else answers(item)


class Nav:
    """The navigation-cli process, replaced. It keeps every input it is handed (`runs`, one per process) and answers each
    file in it through rule(item) -- a row, or a raised Down or TimeoutExpired for the whole run -- unless down(run) names
    a last line to print and fail the whole run with. It prints what the real one prints (read off the real process, run against
    a fake judge): `{results, calls, longCalls}` for a batch, where a file that fails is an error row, and for one file its row
    with `calls` and `longCalls` -- except that one file that fails kills the process: exit 2, its message as the last line of
    stderr, nothing on stdout. A request is made per file, and a long one for a file that reads more than small at a bigger size."""
    def __init__(self, rule, down=None):
        self.rule, self.down, self.runs = rule, down, []

    def items(self):
        """Every file's payload, in the order they were sent, whether alone or inside a batch."""
        return [item for run in self.runs for item in (run["batch"] if "batch" in run else [run])]

    def __call__(self, cmd, input=None, **kw):
        if list(cmd) != list(ask.navigation_command()):
            return REAL_RUN(cmd, input=input, **kw)
        body = json.loads(input)
        self.runs.append(body)
        batch = body["batch"] if "batch" in body else [body]
        if self.down and (stderr := self.down(body)):
            return subprocess.CompletedProcess(cmd, 2, "", stderr)
        try:
            rows = [self.rule(item) for item in batch]
        except Down as e:
            return subprocess.CompletedProcess(cmd, 2, "", e.stderr)
        long_calls = sum(1 for item in batch if "context" in item and sent(item) > SMALL_READ)
        if "batch" in body:
            printed = {"results": rows, "calls": len(batch), "longCalls": long_calls}
        elif rows[0]["status"] == "error":
            return subprocess.CompletedProcess(cmd, 2, "", rows[0]["reason"] + "\n")
        else:
            printed = {**rows[0], "calls": 1, "longCalls": long_calls}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(printed), "")


def at(monkeypatch, preset, source="flag"):
    """Read at `preset`, the way an ask sets it once from the flag, the variable or the saved choice."""
    monkeypatch.setattr(ask, "_CONTEXT", {"name": preset, "source": source, "small_paths": set()})


def ask_main(monkeypatch, capsys, *argv):
    """One ask.py run for principal "me": (exit code, what it printed)."""
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "me", *argv])
    rc = ask.main()
    return rc, capsys.readouterr().out


def context_lines(out):
    return [ln for ln in out.splitlines() if ln.startswith("CONTEXT:")]


# ---------------------------------------------------------------- the stand-in, against the real process
# The real navigation-cli with `fetch` replaced by an offline stub, the way test_ask_to_node_end_to_end.py does it: the judge answers
# every passage "states the answer", except a request about the Dock log, which it refuses as too large (HTTP 400).
OFFLINE_JUDGE = """
globalThis.fetch = async (_url, opts) => {
  const payload = JSON.parse(opts.body);
  if (JSON.stringify(payload.state).includes('Dock log')) return new Response('request too large for the context window', {status: 400});
  const answers = Object.fromEntries(Object.entries(payload.questions).map(([id, q]) => {
    const keys = Object.keys(q.criteria);
    const choice = keys.find(k => k.endsWith('o_0')) || keys[0];
    return [id, {type: 'choice', choice, confidence: 0.95,
      probabilities: Object.fromEntries(keys.map(k => [k, k === choice ? 0.95 : 0.05 / (keys.length - 1 || 1)]))}];
  }));
  return new Response(JSON.stringify({model: 'offline', answers}), {status: 200});
};
"""


def printed(proc):
    """What one navigation-cli process did, as the code above reads it: its exit code, the last line of stderr, the counters it
    printed and each row's status and reason."""
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    rows = out["results"] if "results" in out else [out] if out else []
    return (proc.returncode, (proc.stderr.strip().splitlines() or [""])[-1], {k: out[k] for k in ("calls", "longCalls") if k in out},
            [{k: row[k] for k in ("status", "reason") if k in row} for row in rows])


def test_the_stand_in_prints_what_the_real_navigation_cli_prints(tmp_path, monkeypatch):
    """The stand-in above copies the real process's output, and every test here leans on the copy; this pins it. The real
    navigation-cli (no network, no key of ours, no Jev) and the stand-in are handed the same inputs -- a note that works, a note on
    its own that the judge refuses, and a batch holding both -- and must agree on the exit code, the last line of stderr, the counters
    and every row's status and reason. A note on its own that fails is a dead process; inside a batch it is an error row."""
    if subprocess.run(["node", "--version"], capture_output=True).returncode:
        pytest.skip("node is not installed")
    at(monkeypatch, "large")
    hook = tmp_path / "offline.cjs"
    hook.write_text(OFFLINE_JUDGE)
    env = {**os.environ, "NODE_OPTIONS": f"--require={hook}", "TYPESAFE_API_KEY": "fake-not-real"}
    works, refused = write(tmp_path, "works.md", SHORT), write(tmp_path, "refused.md", "# Dock log\n" + ANSWER)
    one, other = (ask.confirm_start(QUESTION, path)[1]["payload"] for path in (works, refused))
    assert one["context"] == other["context"] == "large"  # the key the real process reads the size from
    stand_in = Nav(lambda item: failed(BAD_REQUEST) if title(item) == "# Dock log" else answers(item))
    for payload, code in ((one, 0), (other, 2), ({"batch": [one, other]}, 0)):
        real = REAL_RUN(ask.navigation_command(), input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=120)
        copy = stand_in(ask.navigation_command(), input=json.dumps(payload))
        assert real.returncode == code, real.stderr
        assert printed(copy) == printed(real), (real.stdout, real.stderr)


# ---------------------------------------------------------------- the fallback, one file
def test_an_error_on_a_long_read_is_resent_at_small_once(tmp_path, monkeypatch, batched):
    at(monkeypatch, "large")
    long = write(tmp_path, "long.md", LONG)
    nav = Nav(only_the_bigger_size_fails(lambda: failed(TIMEOUT)))
    monkeypatch.setattr(ask.subprocess, "run", nav)
    scores, _, error, notes = ask.confirm(QUESTION, [long])
    first, *again = nav.items()
    assert first["context"] == "large" and sent(first) > SMALL_READ  # the first read is the long one, at the bigger size
    assert len(again) == 1, f"the file was sent {1 + len(again)} times, not twice"  # and it is sent again, once
    assert "context" not in again[0] and sent(again[0]) <= SMALL_READ  # at small: no key, so no header; small's read
    assert error is None and notes == {} and scores == {long: SCORE}  # what small answers is the file's answer


def test_files_that_fit_small_are_never_resent(tmp_path, monkeypatch, batched):
    at(monkeypatch, "large")
    long, short = write(tmp_path, "long.md", LONG), write(tmp_path, "short.md", SHORT)
    assert len(SHORT) < SMALL_READ  # small reads it whole: a read at a bigger size is no bigger than small's
    nav = Nav(only_the_bigger_size_fails(lambda: failed(BAD_REQUEST)))  # every read at the bigger size fails, which no old rule retries
    monkeypatch.setattr(ask.subprocess, "run", nav)
    scores, _, error, notes = ask.confirm(QUESTION, [long, short])
    # the long note is sent again at small; the short one, which small would have read just the same, is not
    assert Counter(title(item) for item in nav.items()) == {"# Harbor log": 2, "# Short log": 1}
    assert [("context" in item) for item in nav.items() if title(item) == "# Short log"] == [True]
    assert scores == {long: SCORE} and notes == {short: ask.INCONCLUSIVE} and error == BAD_REQUEST


def test_a_small_failure_after_fallback_keeps_the_original_error_and_prints_no_context_line(tmp_path, monkeypatch, capsys):
    connect(tmp_path, monkeypatch, {"long.md": LONG})
    # the read at the bigger size is turned down; the read at small is not served either
    nav = Nav(lambda item: failed(BAD_REQUEST) if "context" in item else failed(OVERLOADED))
    monkeypatch.setattr(ask.subprocess, "run", nav)
    rc, out = ask_main(monkeypatch, capsys, "--context", "large", "--", QUESTION)
    lines = out.splitlines()
    assert lines[0].startswith("OUTCOME: error") and rc == ask.OUTCOME_EXIT["error"]
    assert f"[content-check] error: {BAD_REQUEST}" in lines and OVERLOADED not in out  # the first error stays; small's is not shown
    assert not context_lines(out)  # a line saying small fixed it would claim a fix that did not happen
    assert [("context" in item) for item in nav.items()] == [True, False]  # it did try small, and tried it once


def test_the_context_line_is_printed_once_right_after_the_outcome_line_with_the_exact_text(tmp_path, monkeypatch, capsys, batched):
    connect(tmp_path, monkeypatch, {"long.md": LONG, "dock.md": LONG_TOO})  # two files fall back: one line all the same
    nav = Nav(only_the_bigger_size_fails(lambda: failed(TIMEOUT)))
    monkeypatch.setattr(ask.subprocess, "run", nav)
    rc, out = ask_main(monkeypatch, capsys, "--context", "large", "--", QUESTION)
    lines = out.splitlines()
    assert lines[0].startswith("OUTCOME: found") and lines[1] == SLOW_AT_LARGE
    assert out.count("CONTEXT:") == 1 and context_lines(out) == [SLOW_AT_LARGE]
    # the other size says its own name
    nav.runs.clear()
    rc, out = ask_main(monkeypatch, capsys, "--context", "medium", "--", QUESTION)
    assert out.splitlines()[1] == SLOW_AT_LARGE.replace("CONTEXT: Large", "CONTEXT: Medium")


def test_json_has_context_fallback_with_reason_and_file_count(tmp_path, monkeypatch, capsys, batched):
    connect(tmp_path, monkeypatch, {"long.md": LONG, "dock.md": LONG_TOO, "short.md": SHORT})
    # nothing fails: the object says the size and nothing about a fallback
    monkeypatch.setattr(ask.subprocess, "run", Nav(answers))
    rc, out = ask_main(monkeypatch, capsys, "--json", "--context", "large", "--", QUESTION)
    assert json.loads(out)["context"] == {"preset": "large", "source": "flag"}
    # the two long reads fail and are read again at small; the short note, which small reads the same, had no need to
    monkeypatch.setattr(ask.subprocess, "run", Nav(long_reads_fail(lambda: failed(TIMEOUT))))
    rc, out = ask_main(monkeypatch, capsys, "--json", "--context", "large", "--", QUESTION)
    obj = json.loads(out)
    assert rc == 0 and out.count("\n") == 1 and obj["outcome"] == "found"
    assert obj["context"] == {"preset": "large", "source": "flag", "fallback": {"files": 2, "reason": "slow", "message": SLOW_AT_LARGE}}
    assert '"score"' not in out


def test_exit_code_and_outcome_come_from_the_small_run(tmp_path, monkeypatch, capsys):
    connect(tmp_path, monkeypatch, {"long.md": LONG})
    for at_small, outcome in ((answers, "found"), (finds_nothing, "not-found")):
        monkeypatch.setattr(ask.subprocess, "run", Nav(lambda item: failed(TIMEOUT) if "context" in item else at_small(item)))
        rc, out = ask_main(monkeypatch, capsys, "--context", "large", "--", QUESTION)
        lines = out.splitlines()
        # the run that gave the answer says found, or says the note does not hold it: never the first run's error
        assert lines[0].startswith(f"OUTCOME: {outcome}") and rc == ask.OUTCOME_EXIT[outcome], (outcome, out)
        assert lines[1] == SLOW_AT_LARGE and "[content-check] error" not in out, (outcome, out)


def test_resent_paths_are_read_at_small_by_the_later_rechecks(tmp_path, monkeypatch, capsys):
    # the shared run's timeout check: two files ride one run, the long one times out, and its re-send at small fails too
    # (so its first error stays); the old check then sends it once more alone, and that read is small's
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "1")
    connect(tmp_path, monkeypatch, {"long.md": LONG, "short.md": SHORT})
    small_tries = Counter()

    def rule(item):
        if title(item) != "# Harbor log":
            return answers(item)
        if "context" in item:
            return failed(TIMEOUT)
        small_tries["long"] += 1
        return failed(OVERLOADED) if small_tries["long"] == 1 else answers(item)
    nav = Nav(rule)
    monkeypatch.setattr(ask.subprocess, "run", nav)
    rc, out = ask_main(monkeypatch, capsys, "--context", "large", "--", QUESTION)
    assert len(nav.runs) == 3 and ask._STAGE.get("timeout_rechecks") == 1  # the shared run, the re-send, the old re-check
    recheck = nav.runs[2]
    assert "batch" not in recheck and "context" not in recheck and sent(recheck) <= SMALL_READ
    lines = out.splitlines()
    assert lines[0].startswith("OUTCOME: found") and lines[1] == SLOW_AT_LARGE and "[content-check] error" not in out


def test_resent_paths_are_read_at_small_by_the_network_recheck(tmp_path, monkeypatch):
    # the same for the network check, file by file: both reads at small are cut off, so the first error (the network) stays
    # and the old check sends the file once more; it too reads small's
    at(monkeypatch, "large")
    long = write(tmp_path, "long.md", LONG)
    small_tries = Counter()

    def rule(item):
        if "context" in item:
            return failed(NETWORK)
        small_tries["long"] += 1
        return failed(NETWORK) if small_tries["long"] == 1 else answers(item)
    nav = Nav(rule)
    monkeypatch.setattr(ask.subprocess, "run", nav)
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")
    scores, _, error, notes = ask.confirm(QUESTION, [long])
    assert [("context" in item) for item in nav.items()] == [True, False, False]  # the bigger size, then small twice
    assert all(sent(item) <= SMALL_READ for item in nav.items()[1:])
    assert ask._STAGE.get("network_rechecks") == 1 and error is None and scores == {long: SCORE}


def test_a_failed_batch_is_redone_once_at_small(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "1")
    connect(tmp_path, monkeypatch, {"long.md": LONG, "short.md": SHORT})
    # the shared run at the bigger size fails as a whole (the input is too large); every other run is served
    nav = Nav(answers, down=lambda run: "Navigation input is too large" if "batch" in run and "context" in run["batch"][0] else None)
    monkeypatch.setattr(ask.subprocess, "run", nav)
    rc, out = ask_main(monkeypatch, capsys, "--context", "large", "--", QUESTION)
    shared, *redone = nav.runs
    assert "batch" in shared and len(redone) == 2  # then each file on its own, once
    assert Counter(title(r) for r in redone) == {"# Harbor log": 1, "# Short log": 1}
    assert all("batch" not in r and "context" not in r for r in redone)  # every file is planned at small this time
    assert all(sent(r) <= SMALL_READ for r in redone)
    lines = out.splitlines()
    assert lines[0].startswith("OUTCOME: found") and "[content-check] error" not in out
    # the line names why: the run said its input was too large
    assert lines[1] == SLOW_AT_LARGE.replace("the judge was slow to answer", "the note was too long for the judge")
    # and counts the file that had a read bigger than small's: the short one is read the same at small
    rc, out = ask_main(monkeypatch, capsys, "--json", "--context", "large", "--", QUESTION)
    assert json.loads(out)["context"]["fallback"]["files"] == 1


def test_no_new_retry_at_small(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "1")
    long, short = write(tmp_path, "long.md", LONG), write(tmp_path, "short.md", SHORT)
    # the preset small, a claim check, and every kind of failure: only the old checks run, and no key ever says a size
    for claim in (None, "The zeppelin quokka inspection is scheduled for the ninth Thursday of each quarter"):
        for preset in ("small", "large"):
            if preset == "large" and not claim:
                continue  # an ordinary ask at large is the next part of this test
            at(monkeypatch, preset)
            monkeypatch.setitem(ask._CLAIM, "text", claim)
            ask._STAGE.clear()
            nav = Nav(lambda item: failed(TIMEOUT) if title(item) == "# Harbor log" else failed(BAD_REQUEST))
            monkeypatch.setattr(ask.subprocess, "run", nav)
            scores, _, error, notes = ask.confirm(QUESTION, [long, short])
            # one shared run, and the old re-check of the one file that timed out in it: two runs, as on main
            assert [len(r.get("batch", [r])) for r in nav.runs] == [2, 1], (preset, claim)
            assert not any("context" in item for item in nav.items()), (preset, claim)
            assert scores == {} and error == TIMEOUT and notes == {long: ask.INCONCLUSIVE, short: ask.INCONCLUSIVE}
    monkeypatch.setitem(ask._CLAIM, "text", None)
    # a file read again at small is never sent a third time, whatever small says: one re-send, no new retry
    at(monkeypatch, "large")
    nav = Nav(lambda item: failed(BAD_REQUEST))
    monkeypatch.setattr(ask.subprocess, "run", nav)
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")
    scores, _, error, notes = ask.confirm(QUESTION, [long])
    assert [("context" in item) for item in nav.items()] == [True, False] and error == BAD_REQUEST


# ---------------------------------------------------------------- why, how many, and what the trace says
REASONS = [  # what went wrong at the bigger size: its reason in --json, and the words in the line
    (lambda: failed(TIMEOUT), "slow", "the judge was slow to answer"),
    (times_out, "slow", "the judge was slow to answer"),  # the process ran past its own 120 seconds
    (lambda: failed("Navigation provider failed: the judge cut the input to fit its window -- split the file; it is never truncated"),
     "too-long", "the note was too long for the judge"),
    (too_large_to_run, "too-long", "the note was too long for the judge"),  # the run itself fails, and says so
    (lambda: failed(BAD_REQUEST), "turned-down", "the judge turned the request down"),
    (lambda: failed("Navigation provider failed: Jev HTTP 401 (TypeSafe rejected the API key)"), "turned-down", "the judge turned the request down"),
    (lambda: failed(NETWORK), "unreachable", "the judge could not be reached"),
    (lambda: failed(OVERLOADED), "problem", "the judge had a problem"),  # retries used up
    (lambda: failed("Navigation provider failed: Jev HTTP 429"), "problem", "the judge had a problem"),
    (lambda: failed("Navigation provider returned an invalid response"), "problem", "the judge had a problem"),
]


@pytest.mark.parametrize("fault,reason,words", REASONS, ids=[f"{n}-{r[1]}" for n, r in enumerate(REASONS)])
def test_the_reason_is_named_from_the_error(tmp_path, monkeypatch, capsys, fault, reason, words):
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")
    connect(tmp_path, monkeypatch, {"long.md": LONG})
    monkeypatch.setattr(ask.subprocess, "run", Nav(only_the_bigger_size_fails(fault)))
    rc, out = ask_main(monkeypatch, capsys, "--json", "--context", "large", "--", QUESTION)
    fallback = json.loads(out)["context"]["fallback"]
    assert fallback["reason"] == reason and fallback["files"] == 1, out
    assert fallback["message"] == SLOW_AT_LARGE.replace("the judge was slow to answer", words)


def test_files_that_fall_back_for_different_reasons_are_counted_and_the_first_names_it(tmp_path, monkeypatch, capsys, batched):
    connect(tmp_path, monkeypatch, {"long.md": LONG, "dock.md": LONG_TOO})
    faults = {"# Harbor log": TIMEOUT, "# Dock log": BAD_REQUEST}  # the read list's first file times out, its second is turned down
    nav = Nav(lambda item: failed(faults[title(item)]) if "context" in item else answers(item))
    monkeypatch.setattr(ask.subprocess, "run", nav)
    rc, out = ask_main(monkeypatch, capsys, "--json", "--context", "large", "--", QUESTION)
    assert json.loads(out)["context"]["fallback"] == {"files": 2, "reason": "slow", "message": SLOW_AT_LARGE}
    # a file whose re-send at small fails too is not counted, and keeps its error: the line says only what worked
    nav = Nav(lambda item: failed(TIMEOUT) if title(item) == "# Harbor log" and "context" in item
              else failed(BAD_REQUEST) if title(item) == "# Dock log" else answers(item))
    monkeypatch.setattr(ask.subprocess, "run", nav)
    rc, out = ask_main(monkeypatch, capsys, "--context", "large", "--", QUESTION)
    assert out.splitlines()[1] == SLOW_AT_LARGE and f"[content-check] error: {BAD_REQUEST}" in out and out.count("CONTEXT:") == 1
    assert json.loads(ask_main(monkeypatch, capsys, "--json", "--context", "large", "--", QUESTION)[1])["context"]["fallback"]["files"] == 1


def stages_of(sdir):
    """The last trace's stages."""
    records = [json.loads(line) for line in (sdir / "traces.jsonl").read_text().splitlines()]
    return [r for r in records if r.get("kind") == "trace"][-1]["stages"]


def test_the_trace_says_the_size_what_was_sent_and_the_fallback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")
    connect(tmp_path, monkeypatch, {"long.md": LONG})
    sdir = tmp_path / "state" / "me"
    # at large, nothing fails: the size, its read size, the requests and the long ones, the characters of passages sent
    nav = Nav(answers)
    monkeypatch.setattr(ask.subprocess, "run", nav)
    ask_main(monkeypatch, capsys, "--context", "large", "--", QUESTION)
    first = sent(nav.items()[0])
    assert stages_of(sdir)["context"] == {"preset": "large", "source": "flag", "budget_chars": 112_000, "requests": 1,
                                          "chars_sent": first, "long_calls": 1}
    # at medium, the read fails and is sent again at small: both runs count, and the fallback is named
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "1")
    nav = Nav(only_the_bigger_size_fails(lambda: failed(TIMEOUT)))
    monkeypatch.setattr(ask.subprocess, "run", nav)
    ask_main(monkeypatch, capsys, "--context", "medium", "--", QUESTION)
    big, small = (sent(item) for item in nav.items())
    assert small <= SMALL_READ < big
    assert stages_of(sdir)["context"] == {"preset": "medium", "source": "flag", "budget_chars": 56_000, "requests": 2,
                                          "chars_sent": big + small, "long_calls": 1,
                                          "fallback": {"files": 1, "reason": "slow"}}
    # at small there is nothing to say, and the trace is what it always was
    monkeypatch.setattr(ask.subprocess, "run", Nav(answers))
    ask_main(monkeypatch, capsys, "--", QUESTION)
    assert "context" not in stages_of(sdir)


def test_the_trace_counts_what_a_run_printed_and_nothing_for_a_run_that_printed_nothing(tmp_path, monkeypatch, capsys):
    connect(tmp_path, monkeypatch, {"long.md": LONG, "dock.md": LONG_TOO})
    sdir = tmp_path / "state" / "me"

    def context(nav):
        """The last ask's `stages.context`, with the one number every case below shares checked against what the stand-in saw."""
        stage = stages_of(sdir)["context"]
        assert stage["chars_sent"] == sum(map(sent, nav.items())) and stage["budget_chars"] == 112_000
        return {k: stage[k] for k in ("requests", "long_calls", "fallback") if k in stage}

    def ask_at_large(nav):
        monkeypatch.setattr(ask.subprocess, "run", nav)
        ask_main(monkeypatch, capsys, "--context", "large", "--", QUESTION)
        return context(nav)

    # one batched run prints the requests it made (a request per note) and the long ones, both notes being long reads
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "1")
    assert ask_at_large(Nav(answers)) == {"requests": 2, "long_calls": 2}
    # a note whose row is an error inside a batched run still counts, since the run prints its requests; read again at small it is
    # one more request, and not a long one
    nav = Nav(lambda item: failed(TIMEOUT) if "context" in item and title(item) == "# Harbor log" else answers(item))
    assert ask_at_large(nav) == {"requests": 3, "long_calls": 2, "fallback": {"files": 1, "reason": "slow"}}
    # a batched run that fails as a whole prints nothing: no request and no long one for it, and each note is then read once at
    # small (a run of one note prints its own `calls`)
    nav = Nav(answers, down=lambda run: "Navigation input is too large" if "batch" in run else None)
    assert ask_at_large(nav) == {"requests": 2, "long_calls": 0, "fallback": {"files": 2, "reason": "too-long"}}
    assert "batch" in nav.runs[0] and len(nav.runs) == 3
    # one note on its own that fails kills its process: nothing printed, so no request and no long one for it (its characters,
    # handed over, still count: the check in `context`); each is then read again at small
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")
    nav = Nav(only_the_bigger_size_fails(lambda: failed(TIMEOUT)))
    assert ask_at_large(nav) == {"requests": 2, "long_calls": 0, "fallback": {"files": 2, "reason": "slow"}}


def test_the_usage_text_names_the_fallback_line_and_the_json_field():
    doc = " ".join(ask.__doc__.split())
    for words in ("CONTEXT: Large did not work this time", "read again at small, once", "fallback {files, reason, message}",
                  "the first error stays", "adds no retry"):
        assert words in doc, words
