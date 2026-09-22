#!/usr/bin/env python3
"""Offline tests for gate --claim code mode (protocol v1.0 rule 3).

No network, no secrets, no live door. The judge is always a fake:
_code_ask is monkeypatched, and subprocess.run is replaced for the evidence
path, so what we assert is the exact question shape, the verdict mapping,
the auto-detect, and the exit codes. Nothing here reaches TypeSafe.

    python3 -m pytest skills/super-jev/tests/test_noul_code_claims.py -q
"""
import argparse
import importlib.util
import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sj = sys.modules.get("superjev")
if sj is None:
    spec = importlib.util.spec_from_file_location("superjev", SKILL / "superjev.py")
    sj = importlib.util.module_from_spec(spec)
    sys.modules["superjev"] = sj
    spec.loader.exec_module(sj)

_REAL_RUN = subprocess.run

# The real fleet lib path, captured before the hermetic_ci fixture below
# stubs sj.JEV_LIB. Only the wire-shape test reads this, and only to
# skip when the lib is absent (the CI condition).
_REAL_LIB = Path(str(sj.JEV_LIB))


@pytest.fixture(autouse=True)
def no_key(monkeypatch):
    """Every test starts with no API key, so nothing can go live by accident."""
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def hermetic_ci(monkeypatch, tmp_path, request):
    """CI hermeticity: no test may depend on the fleet skill on disk.

    Points JEV_LIB at a tmp stub file, points the gate door env var at
    a fake door script, and injects a fake code-mode judge - unless the test
    is marked real_code_ask (the wire-shape test, which skips when the real
    lib is absent instead of running).
    """
    stub = tmp_path / "jev_stub.py"
    stub.write_text("# hermetic stub: the live fleet lib is never imported here",
                    encoding="utf-8")
    monkeypatch.setattr(sj, "JEV_LIB", stub)
    fake_door = tmp_path / "fake-door.sh"
    fake_door.write_text("#!/bin/sh", encoding="utf-8")
    fake_door.chmod(0o755)
    monkeypatch.setenv(sj.GATE_CMD_ENV, str(fake_door))
    if request.node.get_closest_marker("real_code_ask") is None:
        monkeypatch.setattr(sj, "_code_ask", FakeJudge({}))


@pytest.fixture
def evfile(tmp_path):
    def make(text, name="ev.txt"):
        f = tmp_path / name
        f.write_text(text, encoding="utf-8")
        return str(f)
    return make


DIFF = ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
        "@@ -1 +1 @@\n-old\n+new\n")
PLAIN = "some notes about the work\nnothing diff-like here\n"

CRITERIA = {
    "true": "the code text, read with ordinary programming knowledge, "
            "makes the claim true",
    "false": "the code text makes the claim false or does not establish it",
}


class FakeJudge:
    """A synthetic judge: answers noul with fixed p(yes) per question key."""

    def __init__(self, p_by_key):
        self.p_by_key = p_by_key
        self.calls = []

    def __call__(self, state, questions):
        self.calls.append({"state": state, "questions": questions})
        return {"answers": {k: {"noul": p} for k, p in self.p_by_key.items()},
                "model": "fake", "chunks": 1, "input_tokens": 0,
                "latency_ms": 1}


def _run_json(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = sj.main(argv)
    return code, json.loads(buf.getvalue())


def _run_text(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = sj.main(argv)
    return code, buf.getvalue()


# ------------------------------------------------ (1) code mode: the noul question


def test_code_question_shape_is_exact(evfile, monkeypatch):
    ev = evfile(DIFF)
    judge = FakeJudge({"c1": 0.9})
    monkeypatch.setattr(sj, "_code_ask", judge)
    claim = "when x, f returns y"
    code, obj = _run_json(["gate", ev, "--claim", claim, "--json"])
    assert code == 0
    assert obj["details"]["claim_mode"] == "code"
    assert len(judge.calls) == 1
    q = judge.calls[0]["questions"]["c1"]
    assert q["type"] == "noul"
    assert q["instructions"] == "Is this claim true of the code?\n\nCLAIM: " + claim
    assert q["criteria"] == CRITERIA
    state = judge.calls[0]["state"]
    assert state.startswith("CODE:\n")
    assert "diff --git" in state
    row = obj["details"]["rows"][0]
    assert row["verdict"] == "SUPPORTED"
    assert row["confidence"] == pytest.approx(0.9)
    assert row["arm"] == "noul:code"


def test_code_verdict_mapping():
    assert sj.code_verdict(0.9) == ("SUPPORTED", pytest.approx(0.9))
    assert sj.code_verdict(0.6) == ("SUPPORTED", pytest.approx(0.6))
    assert sj.code_verdict(0.59) == ("NOT_SUPPORTED", pytest.approx(0.59))
    assert sj.code_verdict(0.5) == ("NOT_SUPPORTED", pytest.approx(0.5))
    assert sj.code_verdict(0.41) == ("NOT_SUPPORTED", pytest.approx(0.59))
    assert sj.code_verdict(0.4) == ("CONTRADICTED", pytest.approx(0.6))
    assert sj.code_verdict(0.1) == ("CONTRADICTED", pytest.approx(0.9))


def test_code_mode_exit_codes(evfile, monkeypatch):
    ev = evfile(DIFF)
    judge = FakeJudge({"c1": 0.9, "c2": 0.1, "c3": 0.5})
    monkeypatch.setattr(sj, "_code_ask", judge)
    code, obj = _run_json(["gate", ev, "--claim", "when a, f returns b",
                           "--claim", "when c, f returns d",
                           "--claim", "when e, f returns g", "--json"])
    assert code == 3
    verdicts = [r["verdict"] for r in obj["details"]["rows"]]
    assert verdicts == ["SUPPORTED", "CONTRADICTED", "NOT_SUPPORTED"]


def test_code_mode_never_spawns_the_door(evfile, monkeypatch):
    ev = evfile(DIFF)
    judge = FakeJudge({"c1": 0.9})
    monkeypatch.setattr(sj, "_code_ask", judge)

    def boom(cmd, *a, **k):
        raise AssertionError("door must not be invoked in code mode: %r" % (cmd,))
    monkeypatch.setattr(sj.subprocess, "run", boom)
    code, obj = _run_json(["gate", ev, "--claim", "when x, f returns y", "--json"])
    assert code == 0
    assert obj["details"]["claim_mode"] == "code"


def test_code_mode_plain_output(evfile, monkeypatch):
    ev = evfile(DIFF)
    judge = FakeJudge({"c1": 0.9})
    monkeypatch.setattr(sj, "_code_ask", judge)
    code, out = _run_text(["gate", ev, "--claim", "when x, f returns y"])
    assert code == 0
    assert "claim-mode: code" in out
    assert "VERDICT:" in out


# ------------------------------------------------ (2) auto-detect


class FakeDoor:
    def __init__(self, code=0):
        self.code = code
        self.calls = []

    def __call__(self, cmd, *a, **k):
        self.calls.append([str(c) for c in cmd])
        return subprocess.CompletedProcess(cmd, self.code, stdout="", stderr="")


def test_autodetect_diff_defaults_to_code(evfile, monkeypatch):
    ev = evfile(DIFF)
    judge = FakeJudge({"c1": 0.9})
    monkeypatch.setattr(sj, "_code_ask", judge)
    code, obj = _run_json(["gate", ev, "--claim", "when x, f returns y", "--json"])
    assert code == 0
    assert obj["details"]["claim_mode"] == "code"


def test_autodetect_plain_defaults_to_evidence(evfile, monkeypatch):
    ev = evfile(PLAIN)
    door = FakeDoor()
    monkeypatch.setattr(sj.subprocess, "run", door)
    code, obj = _run_json(["gate", ev, "--claim", "when x, f returns y", "--json"])
    assert obj["details"]["claim_mode"] == "evidence"
    assert "--kit" in door.calls[-1] and "reply" in door.calls[-1]


def test_explicit_evidence_mode_overrides_diff(evfile, monkeypatch):
    ev = evfile(DIFF)
    door = FakeDoor()
    monkeypatch.setattr(sj.subprocess, "run", door)
    code, obj = _run_json(["gate", ev, "--claim", "when x, f returns y",
                           "--claim-mode", "evidence", "--json"])
    assert obj["details"]["claim_mode"] == "evidence"
    assert "--kit" in door.calls[-1] and "reply" in door.calls[-1]


@pytest.mark.parametrize("prefix", ["diff --git a/f b/f", "--- a/f", "+++ b/f",
                                    "@@ -1,2 +3,4 @@"])
def test_looks_like_unified_diff(prefix):
    assert sj.looks_like_unified_diff(prefix + "\nbody\n")
    assert not sj.looks_like_unified_diff("just some notes\nno markers\n")


# ------------------------------------------------ (3) judgment words


@pytest.mark.parametrize("claim,word", [
    ("the code should retry on timeout", "should"),
    ("the parser must not crash on empty input", "must"),
    ("the cache ought to expire hourly", "ought"),
    ("this is a bug in the parser", "bug"),
    ("there are bugs in the retry path", "bugs"),
    ("the output is correct", "correct"),
    ("the count is incorrect", "incorrect"),
    ("the result is wrong", "wrong"),
    ("the server looks broken after deploy", "broken"),
    ("the check seems flawed", "flawed"),
    ("the default remains unsafe", "unsafe"),
    ("the handling was improper", "improper"),
    ("the patch introduces a bug", "bug"),
])
def test_judgment_words_rejected(evfile, claim, word):
    ev = evfile(DIFF)
    code, obj = _run_json(["gate", ev, "--claim", claim, "--json"])
    assert code == 2
    assert obj["verdict"] == "REJECT"
    assert "'%s'" % word in obj["summary"]
    assert "when <input>, <function> returns <value>" in obj["summary"]


@pytest.mark.parametrize("claim", [
    # the two protocol-shaped claims review round 1 raised: judgment words
    # that are not the claim's main-clause predicate must not be refused
    "when the config key is missing, f returns None",
    "the `correct` flag is returned",
    # attributive uses from the old word list: not the predicate either
    "the buggy branch is skipped",
    "the function has a missing return",
    "the broken pipe is handled",
])
def test_non_predicate_judgments_not_refused(evfile, monkeypatch, claim):
    ev = evfile(DIFF)
    judge = FakeJudge({"c1": 0.9})
    monkeypatch.setattr(sj, "_code_ask", judge)
    code, obj = _run_json(["gate", ev, "--claim", claim, "--json"])
    assert code == 0
    assert obj["verdict"] != "REJECT"
    assert len(judge.calls) == 1


def test_allow_judgment_overrides(evfile, monkeypatch):
    ev = evfile(DIFF)
    judge = FakeJudge({"c1": 0.9})
    monkeypatch.setattr(sj, "_code_ask", judge)
    code, obj = _run_json(["gate", ev, "--claim", "the code should retry",
                           "--allow-judgment", "--json"])
    assert code == 0
    assert len(judge.calls) == 1


# ------------------------------------------------ (3b) main-clause filter, negated forms


def test_judgment_in_leading_clause_never_counts(evfile, monkeypatch):
    # the brief's live case: "missing" sits in the stripped subordinate
    # clause, so the claim passes through to the judge
    ev = evfile(DIFF)
    judge = FakeJudge({"c1": 0.9})
    monkeypatch.setattr(sj, "_code_ask", judge)
    code, obj = _run_json(["gate", ev, "--claim",
                           "When the unstable cell is missing, compare_runs treats the row as unstable.",
                           "--json"])
    assert code == 0
    assert obj["verdict"] != "REJECT"
    assert len(judge.calls) == 1
    assert obj["details"]["rows"][0]["arm"] == "noul:code"


@pytest.mark.parametrize("claim,word", [
    ("the output is not correct", "correct"),
    ("this isn't correct", "correct"),
    ("that's wrong", "wrong"),
    ("the path was never broken", "broken"),
    # the old substring exemption is gone: "is returned" never masks "should"
    ("the parser should retry, and an error is returned", "should"),
])
def test_judgment_negated_forms_refused(evfile, claim, word):
    ev = evfile(DIFF)
    code, obj = _run_json(["gate", ev, "--claim", claim, "--json"])
    assert code == 2
    assert obj["verdict"] == "REJECT"
    assert "'%s'" % word in obj["summary"]
    assert "when <input>, <function> returns <value>" in obj["summary"]


# ------------------------------------------------ (4) pattern claims


PATTERN_EV = "import re\nOUT_RE = re.compile(r\"^ERR:\")\n"


def test_pattern_claim_supported(evfile, monkeypatch):
    ev = evfile(PATTERN_EV)
    judge = FakeJudge({"c1": 0.0})  # must never be consulted
    monkeypatch.setattr(sj, "_code_ask", judge)
    code, obj = _run_json(["gate", ev, "--claim-mode", "code",
                           "--claim", "the watcher `ERR: disk full` is caught by OUT_RE",
                           "--json"])
    assert code == 0
    assert judge.calls == []
    row = obj["details"]["rows"][0]
    assert row["verdict"] == "SUPPORTED"
    assert row["confidence"] == 1.0
    assert row["arm"] == "pattern:code"


def test_pattern_claim_contradicted(evfile, monkeypatch):
    ev = evfile(PATTERN_EV)
    judge = FakeJudge({"c1": 1.0})  # must never be consulted
    monkeypatch.setattr(sj, "_code_ask", judge)
    code, obj = _run_json(["gate", ev, "--claim-mode", "code",
                           "--claim", "the watcher `WARN: low disk` is matched by OUT_RE",
                           "--json"])
    assert code == 3
    assert judge.calls == []
    row = obj["details"]["rows"][0]
    assert row["verdict"] == "CONTRADICTED"
    assert row["confidence"] == 1.0
    assert row["arm"] == "pattern:code"


def test_pattern_claim_without_defined_name_goes_to_judge(evfile, monkeypatch):
    ev = evfile("no regex defs here\n")
    judge = FakeJudge({"c1": 0.9})
    monkeypatch.setattr(sj, "_code_ask", judge)
    code, obj = _run_json(["gate", ev, "--claim-mode", "code",
                           "--claim", "`ERR: disk full` is caught by NOPE_RE",
                           "--json"])
    assert len(judge.calls) == 1
    assert obj["details"]["rows"][0]["arm"] == "noul:code"


def test_first_backtick_not_the_token_goes_to_judge(evfile, monkeypatch):
    # the arm fires only on the phrase shape; here the token in the phrase
    # position is `ERR:`, not the first backtick `run` — and `ERR:` is not an
    # ALL_CAPS name, so the judge must see this claim
    ev = evfile(PATTERN_EV)
    judge = FakeJudge({"c1": 0.9})
    monkeypatch.setattr(sj, "_code_ask", judge)
    code, obj = _run_json(["gate", ev, "--claim-mode", "code",
                           "--claim", "the function `run` matches `ERR:` via OUT_RE",
                           "--json"])
    assert code == 0
    assert len(judge.calls) == 1
    assert obj["details"]["rows"][0]["arm"] == "noul:code"


def test_judgment_rejection_beats_pattern_arm(evfile, monkeypatch):
    ev = evfile(PATTERN_EV)
    judge = FakeJudge({"c1": 0.9})  # must never be consulted
    monkeypatch.setattr(sj, "_code_ask", judge)
    code, obj = _run_json(["gate", ev, "--claim-mode", "code",
                           "--claim", "it is a bug that `ERR: boom` is caught by OUT_RE",
                           "--json"])
    assert code == 2
    assert obj["verdict"] == "REJECT"
    assert judge.calls == []


def test_pattern_arm_trailing_words_go_to_judge(evfile, monkeypatch):
    # the arm is deliberately narrow: with anything after the NAME the claim
    # is not a bare pattern match, so the judge must see it
    ev = evfile(PATTERN_EV)
    judge = FakeJudge({"c1": 0.9})
    monkeypatch.setattr(sj, "_code_ask", judge)
    code, obj = _run_json(["gate", ev, "--claim-mode", "code",
                           "--claim", "the watcher `ERR: disk full` is caught by OUT_RE, but only on Windows",
                           "--json"])
    assert code == 0
    assert len(judge.calls) == 1
    assert obj["details"]["rows"][0]["arm"] == "noul:code"


# -------------------------------- (4b) pattern claims: negative phrases


NEG_PATTERN_EV = "import re\nTOKEN_RE = re.compile(r\"^foo$\")\n"

# every recognized phrase crossed with a matching and a non-matching token
_NEG_CASES = [
    ("matches", "foo", "SUPPORTED"),
    ("matches", "bar", "CONTRADICTED"),
    ("does not match", "foo", "CONTRADICTED"),
    ("does not match", "bar", "SUPPORTED"),
    ("is matched by", "foo", "SUPPORTED"),
    ("is matched by", "bar", "CONTRADICTED"),
    ("is caught by", "foo", "SUPPORTED"),
    ("is caught by", "bar", "CONTRADICTED"),
    ("is not caught by", "foo", "CONTRADICTED"),
    ("is not caught by", "bar", "SUPPORTED"),
]


@pytest.mark.parametrize("phrase,token,verdict", _NEG_CASES)
def test_pattern_claim_polarity(phrase, token, verdict):
    det = sj.pattern_claim_answer("`%s` %s TOKEN_RE" % (token, phrase),
                                  NEG_PATTERN_EV)
    assert det is not None
    assert det["verdict"] == verdict
    assert det["confidence"] == 1.0
    assert det["arm"] == "pattern:code"
    assert det["negated"] == ("not" in phrase)


def test_pattern_negated_contradiction_action():
    # "`foo` does not match TOKEN_RE" is false because foo DOES match --
    # the action must say that, not claim the pattern missed
    judge = FakeJudge({})  # must never be consulted
    rows, code = sj.run_code_gate(
        [("ev", NEG_PATTERN_EV)],
        ["`foo` does not match TOKEN_RE", "`bar` does not match TOKEN_RE"],
        ask_fn=judge)
    assert judge.calls == []
    assert rows[0]["verdict"] == "CONTRADICTED"
    assert "did not match" not in rows[0]["action"]
    assert "the token matched" in rows[0]["action"]
    assert rows[1]["verdict"] == "SUPPORTED"
    assert rows[1]["action"] == "ok"
    assert code == 3


def test_pattern_negated_supported_exits_zero():
    judge = FakeJudge({})  # must never be consulted
    rows, code = sj.run_code_gate(
        [("ev", NEG_PATTERN_EV)], ["`bar` does not match TOKEN_RE"],
        ask_fn=judge)
    assert judge.calls == []
    assert rows[0]["verdict"] == "SUPPORTED"
    assert rows[0]["action"] == "ok"
    assert code == 0


@pytest.mark.parametrize("phrase", ["matches", "does not match"])
def test_pattern_malformed_regex_goes_to_judge(phrase):
    ev = "import re\nBAD_RE = re.compile(r\"([\")\n"
    judge = FakeJudge({"c1": 0.9})
    rows, code = sj.run_code_gate(
        [("ev", ev)], ["`foo` %s BAD_RE" % phrase], ask_fn=judge)
    assert len(judge.calls) == 1
    assert rows[0]["arm"] == "noul:code"


# ------------------------------------------------ (5) hook mode


def _hook_ns(ev, claims):
    return argparse.Namespace(evidence=[ev], draft="", claim=claims,
                              json=False, hook_mode=True, timeout=60,
                              claim_mode=None, allow_judgment=False)


def test_hook_mode_code_gate_returns_triple(evfile, monkeypatch):
    ev = evfile(DIFF)
    judge = FakeJudge({"c1": 0.9})
    monkeypatch.setattr(sj, "_code_ask", judge)
    code, out, err = sj.cmd_gate(_hook_ns(ev, ["when x, f returns y"]))
    assert code == 0
    assert "claim-mode: code" in out
    assert err == ""


def test_hook_mode_judgment_claim_dropped(evfile):
    ev = evfile(DIFF)
    code, out, err = sj.cmd_gate(_hook_ns(ev, ["the code should retry"]))
    assert code == 0  # dropped, never exit 2 in hook mode
    assert "dropped" in out
    assert err == ""


def test_hook_mode_judge_exception_is_advisory(evfile, monkeypatch):
    ev = evfile(DIFF)

    def boom(state, questions):
        raise RuntimeError("simulated lib failure")

    monkeypatch.setattr(sj, "_code_ask", boom)
    code, out, err = sj.cmd_gate(_hook_ns(ev, ["when x, f returns y"]))
    assert code == 3
    assert "simulated lib failure" in out
    assert "\n" not in out.strip()  # the reason is one line
    assert err == ""


def test_hook_mode_autoselect_code_path_uses_fake_judge(evfile, monkeypatch):
    # DIFF evidence with no --claim-mode auto-selects code; the fake judge
    # is reached THROUGH that auto-select, not around it
    ev = evfile(DIFF)
    judge = FakeJudge({"c1": 0.9})
    monkeypatch.setattr(sj, "_code_ask", judge)
    code, out, err = sj.cmd_gate(_hook_ns(ev, ["when x, f returns y"]))
    assert code == 0
    assert "claim-mode: code" in out
    assert err == ""
    assert len(judge.calls) == 1
    assert judge.calls[0]["state"].startswith("CODE:\n")


# ------------------------------------------------ (6) row order and full claims


def test_rows_sorted_numerically(evfile, monkeypatch):
    ev = evfile(DIFF)
    claims = ["when case %d, f returns %d" % (i, i) for i in range(1, 12)]
    judge = FakeJudge({"c%d" % i: 0.9 for i in range(1, 12)})
    monkeypatch.setattr(sj, "_code_ask", judge)
    rows, code = sj.run_code_gate([("ev", DIFF)], claims)
    assert code == 0
    assert [r["key"] for r in rows] == ["c%d" % i for i in range(1, 12)]


def test_json_rows_carry_full_claim(evfile, monkeypatch):
    ev = evfile(DIFF)
    judge = FakeJudge({"c1": 0.9})
    monkeypatch.setattr(sj, "_code_ask", judge)
    claim = ("when the input carries a long description running past the text "
             "table width, f returns the same long description unchanged")
    code, obj = _run_json(["gate", ev, "--claim", claim, "--json"])
    assert code == 0
    assert obj["details"]["rows"][0]["claim"] == claim
    code, out = _run_text(["gate", ev, "--claim", claim])
    assert code == 0
    assert claim not in out  # the text table alone truncates


# ------------------------------------------------ (7) the real wire shape


class _CannedHTTPResponse:
    """The transport-level fake: what urllib.request.urlopen returns."""

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


@pytest.mark.real_code_ask
def test_code_ask_posts_exact_noul_question_on_the_wire(tmp_path, monkeypatch):
    """_code_ask drives the real lib's ask() down to a canned HTTP response.

    urllib.request.urlopen is monkeypatched \u2014 never the lib's own _post \u2014
    so this asserts both the exact bytes POSTed and the lib's own response
    parsing, with no network and no key file read.
    """
    monkeypatch.chdir(tmp_path)
    lib = _REAL_LIB
    if not lib.exists():
        pytest.skip("fleet jev lib not present on this machine")
    spec = importlib.util.spec_from_file_location("jev_real_wire3", str(lib))
    jev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jev)
    # the key file can never be read here: its constant points at nothing and
    # the key lookup itself is stubbed
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    seen = []

    def fake_urlopen(req, timeout=None, context=None):
        seen.append(req)
        return _CannedHTTPResponse(
            {"answers": {"c1": {"type": "noul", "noul": 0.93}}})

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sj, "_load_jev_lib", lambda: jev)
    claim = "when x, f returns y"
    rows, code = sj.run_code_gate([("ev.py", "some code")], [claim])
    assert len(seen) == 1
    body = json.loads(seen[0].data.decode())
    assert set(body["questions"]) == {"c1"}  # exactly one noul question
    assert body["questions"]["c1"] == sj.noul_code_question(claim)
    assert rows[0]["p_yes"] == pytest.approx(0.93)
    assert rows[0]["verdict"] == "SUPPORTED"
    assert rows[0]["confidence"] == pytest.approx(0.93)
    assert rows[0]["arm"] == "noul:code"
    assert code == 0
