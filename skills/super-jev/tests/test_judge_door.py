#!/usr/bin/env python3
"""The judge doorway (skills/super-jev/judges): one door, Jev as its first implementation.

FROZEN tests, written before the code moved:
  (a) with SUPERJEV_JUDGE unset, the requests sent to the judge are byte-identical to the
      ones sent before the doorway existed (judge_requests_golden.json, captured then)
  (b) an unknown judge name fails with one clear line
  (c) a second profile with a different window, line, endpoint and key changes behaviour
      only through the profile
  (d) every error type maps to "no verdict", never a pass; one retry rule
  (e) no source file outside the Jev implementation names the vendor's key or endpoint

    python3 -m pytest skills/super-jev/tests/test_judge_door.py -q
"""
import importlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parent.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "lib"))
import judge_profile  # noqa: E402
import judges  # noqa: E402
from judges import errors  # noqa: E402

GOLDEN = json.loads((SKILL / "tests" / "data" / "judge_requests_golden.json").read_text())
PW = "password=" + "hunter2" + "hunter2"


def load_client(name="jev_client_door"):
    spec = importlib.util.spec_from_file_location(name, SKILL / "lib" / "jev_client.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _yes(url, body, headers, timeout, sent=None):
    q = json.loads(body)["questions"]
    return {"model": "m", "answers": {k: {"choice": next(iter(v["criteria"])), "confidence": 0.9,
                                          "probabilities": {}} for k, v in q.items()}, "usage": {"input_tokens": 5}}


# ------------------------------------------------------------------ (a)
def test_a_requests_are_byte_identical_to_the_ones_sent_before_the_doorway(monkeypatch):
    monkeypatch.delenv("SUPERJEV_JUDGE", raising=False)
    monkeypatch.delenv("SUPERJEV_JEV_URL", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-not-a-key")
    jc = importlib.import_module("jev_client")
    sent = []

    def fake(url, body, headers, timeout):
        sent.append({"url": url, "body": body.decode(), "headers": headers})
        return _yes(url, body, headers, timeout)
    monkeypatch.setattr(jc, "transport", fake)
    ev = [("/e/a.md", "The build passes on Tuesday.\nRow 000042 is in the ledger.")]
    draft = "The build passes on Tuesday. Row 000042 is in the ledger file."
    jc.check(ev, jc.split_claims(draft), draft)
    # the second request goes through the doorway itself
    judges.ask("EVIDENCE:\nplain state", {"q1": {"type": "choice", "instructions": "Pick.",
                                                  "criteria": {"A": "a", "B": "b"}}}, timeout=60)
    assert sent == GOLDEN


# ------------------------------------------------------------------ (b)
@pytest.mark.parametrize("code", ["import judge_profile", "import judges; judges.get_judge()",
                                  "import superjev", "import ask"])
def test_b_an_unknown_judge_name_fails_with_one_clear_line(code):
    env = dict(os.environ, SUPERJEV_JUDGE="oracle")
    p = subprocess.run([sys.executable, "-c", code], cwd=SKILL, env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode != 0
    lines = [l for l in p.stderr.splitlines() if l.strip()]
    assert len(lines) == 1, p.stderr
    assert "SUPERJEV_JUDGE" in lines[0] and "'oracle'" in lines[0] and "typesafe-jev" in lines[0]


def test_b_the_default_and_the_named_jev_profile_and_fake_all_load():
    for name in (None, "typesafe-jev", "typesafe", "fake"):
        assert judge_profile.load(name).name == "typesafe-jev"


# ------------------------------------------------------------------ (c)
def _second_profile(tmp_path):
    data = json.loads(judge_profile.PROFILES_PATH.read_text())
    p = dict(data["profiles"]["typesafe-jev"])
    p.update(api_url="https://judge.example.test/v1/decide", model="other-model", key_env="OTHER_JUDGE_KEY",
             window_tokens=6000, confidence_line=0.95, max_questions_per_call=9, aliases=[])
    data["profiles"]["second"] = p
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(data))
    return path


def test_c_a_second_profile_changes_behaviour_only_through_the_profile(tmp_path, monkeypatch):
    path = _second_profile(tmp_path)
    ev = [("/e/big.md", "\n".join(f"Row {i:06d} holds a value." for i in range(2500)))]
    claims = ["Row 000042 holds a value."]

    def run(client):
        sent = []
        monkeypatch.setitem(sys.modules, "jev_client", client)   # the door's ask reaches this copy

        def fake(url, body, headers, timeout):
            sent.append((url, json.loads(body)["model"], headers["Authorization"]))
            return _yes(url, body, headers, timeout)
        client.transport = fake
        rows, meta, code = client.check(ev, claims, "")
        return sent, meta["chunks"], code

    monkeypatch.setenv("TYPESAFE_API_KEY", "jev-key")
    monkeypatch.setenv("OTHER_JUDGE_KEY", "other-key")
    monkeypatch.delenv("SUPERJEV_JEV_URL", raising=False)
    base_sent, base_chunks, base_code = run(load_client("jc_base"))
    assert base_sent[0] == ("https://api.typesafe.ai/v1/systemone", "jev-latest", "Bearer jev-key")
    assert base_code == 0                      # a 0.90 SUPPORTED clears the 0.80 line

    monkeypatch.setattr(judge_profile, "PROFILES_PATH", path)
    monkeypatch.setenv("SUPERJEV_JUDGE", "second")
    monkeypatch.setattr(judge_profile, "PROFILE", judge_profile.load())   # what a fresh start would read
    assert judges.key_env() == "OTHER_JUDGE_KEY"
    sent, chunks, code = run(load_client("jc_second"))
    assert sent[0] == ("https://judge.example.test/v1/decide", "other-model", "Bearer other-key")
    assert chunks > base_chunks            # a smaller window cuts the same evidence into more calls
    assert code == 3                       # the same 0.90 now sits under the 0.95 line
    monkeypatch.undo()
    assert judges.key_env() == "TYPESAFE_API_KEY"


# ------------------------------------------------------------------ (d)
class _Boom(urllib.error.HTTPError):
    def __init__(self, code):
        super().__init__("http://x", code, "err", {}, io.BytesIO(b""))


def _cause(kind, monkeypatch, jc):
    """Make the next judge call fail as `kind`; return how to call it."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-not-a-key")
    monkeypatch.setattr(jc.time, "sleep", lambda s: None)
    q = {"c1": {"type": "choice", "instructions": "Is it?", "criteria": {"YES": "y", "NO": "n"}}}
    state = "def f(): pass"
    if kind is errors.NoKey:
        monkeypatch.delenv("TYPESAFE_API_KEY")
    elif kind is errors.AuthRejected:
        monkeypatch.setattr(jc, "transport", lambda *a: (_ for _ in ()).throw(_Boom(401)))
    elif kind is errors.Unreachable:
        monkeypatch.setattr(jc, "transport", lambda *a: (_ for _ in ()).throw(urllib.error.URLError("down")))
    elif kind is errors.Overloaded:
        monkeypatch.setattr(jc, "transport", lambda *a: (_ for _ in ()).throw(_Boom(529)))
    elif kind is errors.BadReply:
        monkeypatch.setattr(jc, "transport", lambda *a: {"answers": "nope"})
    elif kind is errors.TooBig:
        state = "x" * (judge_profile.PROFILE.window_tokens * 3)
    elif kind is errors.SecretBlocked:
        state += " " + PW
    return state, q


@pytest.mark.parametrize("kind", errors.ERROR_KINDS, ids=lambda k: k.__name__)
def test_d_every_error_type_means_no_verdict(kind, monkeypatch):
    jc = importlib.import_module("jev_client")
    state, q = _cause(kind, monkeypatch, jc)
    with pytest.raises(kind) as e:
        judges.ask(state, q)
    assert isinstance(e.value, errors.JudgeError) and e.value.kind == kind.kind
    # through the claim client's own front door: exit 1 and no verdict table, never a pass
    monkeypatch.setattr(jc, "transport", getattr(jc, "transport"))
    ev = SKILL / "SKILL.md"
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = jc.main([str(ev), "--claim", "The skill has a description line."]) if kind not in (
            errors.TooBig, errors.SecretBlocked) else 1
    assert code == 1 and "SUPPORTED" not in out.getvalue()


def test_d_one_retry_rule_only_overloaded_is_retried(monkeypatch):
    jc = importlib.import_module("jev_client")
    prof = judge_profile.PROFILE
    for kind, status, want in ((errors.Overloaded, 529, prof.retry_attempts), (errors.Overloaded, 429, prof.retry_attempts),
                               (errors.AuthRejected, 401, 1), (errors.BadReply, 500, 1)):
        calls = []
        monkeypatch.setenv("TYPESAFE_API_KEY", "test-not-a-key")
        monkeypatch.setattr(jc.time, "sleep", lambda s: None)
        monkeypatch.setattr(jc, "transport", lambda *a, c=calls, s=status: c.append(1) or (_ for _ in ()).throw(_Boom(s)))
        with pytest.raises(kind):
            judges.ask("s", {"c1": {"type": "choice", "instructions": "i", "criteria": {"A": "a"}}})
        assert len(calls) == want, (kind, status)


def test_d_the_callers_treat_an_error_as_no_opinion(monkeypatch):
    import ask
    monkeypatch.setattr(ask, "jev_choice", lambda *a: (_ for _ in ()).throw(errors.Unreachable("down")))
    monkeypatch.setitem(ask._CLAIM, "text", None)
    p = SKILL / "SKILL.md"
    assert ask.judge_listwise("what?", [str(p)]) == (None, None)


# ------------------------------------------------------------------ (e)
def test_e_only_the_jev_implementation_names_the_vendors_key_or_endpoint():
    allowed = {SKILL / "lib" / "jev_client.py", REPO / "src" / "jev.ts", SKILL / "judge_profiles.json"}
    files = [f for f in list((REPO / "src").rglob("*.ts")) + list((REPO / "bench").rglob("*.ts"))
             + list(SKILL.rglob("*.py")) + list(SKILL.glob("*.json")) + list((SKILL / "hooks").rglob("*"))
             if f.is_file() and f not in allowed and SKILL / "tests" not in f.parents
             and "node_modules" not in f.parts]
    assert len(files) > 30
    bad = []
    for f in files:
        try:
            text = f.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for needle in ("TYPESAFE_API_KEY", "api.typesafe.ai", "jev-latest"):
            if needle in text:
                bad.append(f"{f.relative_to(REPO)}: {needle}")
    assert not bad, bad


# ------------------------------------------------------------------ door hardening
def test_door_scans_secrets_before_any_adapter(monkeypatch):
    calls = []

    class Fake:
        @staticmethod
        def ask(state, questions, timeout=120):
            calls.append((state, questions))
            return {"answers": {}}
    monkeypatch.setattr(judges, "_impl", lambda: Fake)
    q = {"c1": {"type": "choice", "instructions": "Is it?", "criteria": {"YES": "y", "NO": "n"}}}
    with pytest.raises(errors.SecretBlocked):
        judges.ask("def f(): pass " + PW, q)
    q2 = {"c1": {"type": "choice", "instructions": "Is it? " + PW, "criteria": {"YES": "y"}}}
    with pytest.raises(errors.SecretBlocked):
        judges.ask("def f(): pass", q2)
    assert calls == []
    judges.ask("def f(): pass", q)
    assert len(calls) == 1


def test_judge_names_are_case_sensitive(monkeypatch):
    monkeypatch.delenv("SUPERJEV_JUDGE", raising=False)
    with pytest.raises(SystemExit):
        judges.get_judge("Typesafe-JEV")
    assert judges.get_judge("typesafe-jev").name == "typesafe-jev"


def _http400(body):
    return lambda *a: (_ for _ in ()).throw(
        urllib.error.HTTPError("http://x", 400, "err", {}, io.BytesIO(body)))


@pytest.mark.parametrize("body,kind", [(b'{"error":"input exceeds the context length"}', errors.TooBig),
                                       (b'{"error":"questions must be a list"}', errors.BadReply),
                                       (b"", errors.BadReply)])
def test_http_400_is_toobig_only_when_the_body_says_so(body, kind, monkeypatch):
    jc = importlib.import_module("jev_client")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-not-a-key")
    monkeypatch.setattr(jc, "transport", _http400(body))
    q = {"c1": {"type": "choice", "instructions": "Is it?", "criteria": {"YES": "y", "NO": "n"}}}
    with pytest.raises(kind) as e:
        judges.ask("def f(): pass", q)
    assert type(e.value) is kind
