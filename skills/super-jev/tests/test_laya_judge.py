"""The second judge behind the door: Laya (keyless, small window, reads answer_confidence).

No network: the transport is replaced. Fake secrets are built at run time (CI's scanner fails
on literal secret-looking strings).

    python3 -m pytest skills/super-jev/tests/test_laya_judge.py -q
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "lib"))
import judge_profile  # noqa: E402
import judges  # noqa: E402
from judges import errors  # noqa: E402

PW = "password=" + "hunter2" + "hunter2"
Q = {"c1": {"type": "choice", "instructions": "Is it?", "criteria": {"SUPPORTED": "y", "NOT_SUPPORTED": "n"}}}


@pytest.fixture
def laya(monkeypatch):
    """The laya profile active, and a fresh Jev-wire client reading it, with no key in the environment."""
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("SUPERJEV_LAYA_URL", raising=False)
    monkeypatch.setenv("SUPERJEV_JUDGE", "laya")
    monkeypatch.setattr(judge_profile, "PROFILE", judge_profile.load())
    spec = importlib.util.spec_from_file_location("jev_client_laya", SKILL / "lib" / "jev_client.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setitem(sys.modules, "jev_client", mod)
    return mod


def _reply(field="answer_confidence", value=0.9, **usage):
    return {"model": "laya-rl-agent", "answers": {"c1": {"choice": "SUPPORTED", "confidence": 0.3, field: value}},
            "usage": {"input_tokens": 20, **usage}}


def test_profile_loads_with_its_own_wire_facts():
    p = judge_profile.load("laya")
    assert (p.kind, p.model, p.window_tokens, p.key_required) == ("jev", "typed-decisions", 1024, False)
    assert p.confidence_field == "answer_confidence" and p.input_usd_per_mtok == 0
    assert p.api_url == "http://127.0.0.1:18765/v1/systemone" and p.api_url_env == "SUPERJEV_LAYA_URL"
    assert p.call_tokens > 0
    raw = json.loads(judge_profile.PROFILES_PATH.read_text())["profiles"]["laya"]
    assert "UNCALIBRATED" in raw["_source"]
    # the Jev profile is unchanged by the new fields
    j = judge_profile.load("typesafe-jev")
    assert j.key_required and j.confidence_field == "confidence" and j.api_url_env == "SUPERJEV_JEV_URL"


def test_only_superjev_judge_selects_it():
    code = "import judge_profile as j; print(j.PROFILE.name)"
    for env, want in (({}, "typesafe-jev"), ({"SUPERJEV_JUDGE": "laya"}, "laya")):
        e = {k: v for k, v in os.environ.items() if k != "SUPERJEV_JUDGE"} | env
        p = subprocess.run([sys.executable, "-c", code], cwd=SKILL, env=e, capture_output=True, text=True, timeout=60)
        assert p.stdout.strip() == want, p.stderr


def test_keyless_judge_works_without_typesafe_key_and_sends_no_authorization(laya):
    sent = []
    laya.transport = lambda url, body, headers, timeout: sent.append((url, json.loads(body), headers)) or _reply()
    assert not os.environ.get("TYPESAFE_API_KEY")
    assert judges.key_present() and judges.require_key() == ""
    out = judges.ask("EVIDENCE:\nplain state", Q)
    url, body, headers = sent[0]
    assert url == "http://127.0.0.1:18765/v1/systemone" and body["model"] == "typed-decisions"
    assert "Authorization" not in headers
    assert out["answers"]["c1"]["choice"] == "SUPPORTED"


def test_url_comes_from_the_profiles_env_var(laya, monkeypatch):
    monkeypatch.setenv("SUPERJEV_LAYA_URL", "http://laya.example.test/v1/systemone")
    sent = []
    laya.transport = lambda url, body, headers, timeout: sent.append(url) or _reply()
    judges.ask("s", Q)
    assert sent == ["http://laya.example.test/v1/systemone"]


def test_pass_line_reads_answer_confidence_not_confidence(laya):
    assert laya.row("c1", {"choice": "SUPPORTED", "confidence": 0.99, "answer_confidence": 0.5}, "x")["confidence"] == 0.5
    assert laya.row("c1", {"choice": "SUPPORTED", "confidence": 0.1, "answer_confidence": 0.95}, "x")["flag"] is False


def test_check_goes_through_the_door_so_a_secret_never_reaches_laya(laya):
    sent = []
    laya.transport = lambda *a: sent.append(a) or _reply()
    with pytest.raises(errors.SecretBlocked):
        laya.check([("/e/a.md", "The build passes. " + PW)], ["The build passes on Tuesday."], "")
    with pytest.raises(errors.SecretBlocked):
        judges.ask("state " + PW, Q)
    assert sent == []


def test_over_the_window_is_toobig_and_never_sent(laya):
    sent = []
    laya.transport = lambda *a: sent.append(a) or _reply()
    with pytest.raises(errors.TooBig):
        judges.ask("x" * (judge_profile.PROFILE.window_tokens * 3), Q)
    assert sent == []


def test_a_server_side_cut_is_no_verdict(laya):
    laya.transport = lambda *a: _reply(truncated=True)
    with pytest.raises(errors.TooBig):
        judges.ask("s", Q)


# -- the key goes only to the judge's own host (or this machine), never to a URL override elsewhere
@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "-".join(["test", "not", "a", "key"]))
    monkeypatch.delenv("SUPERJEV_JUDGE", raising=False)
    monkeypatch.setattr(judge_profile, "PROFILE", judge_profile.load("typesafe-jev"))
    spec = importlib.util.spec_from_file_location("jev_client_keyed", SKILL / "lib" / "jev_client.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setitem(sys.modules, "jev_client", mod)
    return mod


def _ok(url, body, headers, timeout):
    return {"model": "m", "answers": {"c1": {"choice": "SUPPORTED", "confidence": 0.9}}}


def test_key_is_never_sent_to_another_host(keyed, monkeypatch):
    sent = []
    keyed.transport = lambda *a: sent.append(a) or _ok(*a)
    for bad in ("https://evil.example.test/v1/systemone", "http://api.typesafe.ai/v1/systemone",
                "https://api.typesafe.ai.evil.example.test/x"):
        monkeypatch.setenv("SUPERJEV_JEV_URL", bad)
        with pytest.raises(errors.AuthRejected):
            judges.ask("s", Q)
    assert sent == []


def test_key_still_goes_to_its_own_host_and_to_loopback(keyed, monkeypatch):
    sent = []
    keyed.transport = lambda url, *a: sent.append((url, a[1])) or _ok(url, *a)
    for ok in ("https://api.typesafe.ai/other/path", "http://127.0.0.1:9/x", "http://localhost:9/x"):
        monkeypatch.setenv("SUPERJEV_JEV_URL", ok)
        judges.ask("s", Q)
    assert len(sent) == 3 and all("Authorization" in h for _, h in sent)


def test_a_keyless_judge_may_be_pointed_anywhere(laya, monkeypatch):
    sent = []
    laya.transport = lambda url, *a: sent.append(url) or _reply()
    monkeypatch.setenv("SUPERJEV_LAYA_URL", "http://laya.example.test/x")
    judges.ask("s", Q)
    assert sent == ["http://laya.example.test/x"]


def test_calibrated_flag_in_the_table():
    assert judge_profile.load("typesafe-jev").calibrated is True
    assert judge_profile.load("laya").calibrated is False


# -- a redirect is never followed: the key must not reach a second host
def test_a_redirect_is_refused_and_the_key_never_reaches_the_second_host(keyed):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    seen = []

    class Second(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass
    second = HTTPServer(("127.0.0.1", 0), Second)

    class First(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{second.server_port}/steal")
            self.end_headers()

        def log_message(self, *a):
            pass
    first = HTTPServer(("127.0.0.1", 0), First)
    for srv in (first, second):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        headers = {"Authorization": "Bearer " + os.environ["TYPESAFE_API_KEY"], "Content-Type": "application/json"}
        with pytest.raises(errors.BadReply, match="redirect"):
            keyed._http_post(f"http://127.0.0.1:{first.server_port}/v1", b"{}", headers, 10)
    finally:
        first.shutdown()
        second.shutdown()
    assert seen == []


def test_error_text_names_the_active_judge(laya):
    import urllib.error
    import io

    def boom(*a):
        raise urllib.error.HTTPError("http://x", 500, "err", {}, io.BytesIO(b""))
    laya.transport = boom
    laya.time.sleep = lambda s: None
    with pytest.raises(errors.BadReply) as e:
        judges.ask("s", Q)
    assert "Laya" in str(e.value) and "TypeSafe" not in str(e.value)
    laya.transport = lambda *a: {"answers": "nope"}
    with pytest.raises(errors.BadReply, match="Laya reply"):
        judges.ask("s", Q)
    assert judge_profile.load("typesafe-jev").vendor == "TypeSafe"
