#!/usr/bin/env python3
"""Offline tests: evidence holding secret-shaped text (a card scanner's fake card numbers,
a redaction test's sample key) is masked before it is judged, instead of refusing the
whole check. No secret-shaped text is ever sent, real or fake; a claim holding one is
still refused, and jev_client.ask keeps its own full scan.

Token-shaped strings are built by concatenation so this file itself never holds one.

    python3 -m pytest skills/super-jev/tests/test_masked_evidence.py -q
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(SKILL))
pb = load("prepare_bulk_mask", SKILL / "prepare_bulk.py")
jc = load("jev_client_mask", SKILL / "lib" / "jev_client.py")
sj = load("superjev_mask", SKILL / "superjev.py")

CARD = "4111 " + "1111 1111 1111"
PW = "password=" + "hunter2" + "hunter2"
STRIPE = "sk_" + "live_" + "abcdefghij1234567890"
AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
DIFF = f'''diff --git a/tests/test_scan.py b/tests/test_scan.py
--- a/tests/test_scan.py
+++ b/tests/test_scan.py
@@ -1,2 +1,6 @@
 import scan
+def test_card_is_held():
+    assert scan.card_hit("{CARD}")  # a fake visa
+def test_key_is_held():
+    assert scan.has_secret("{STRIPE}")
+    assert scan.has_secret('{PW}')
'''


@pytest.mark.parametrize("secret", [CARD, PW, STRIPE, AWS, "api_key = abcDEF123456",
                                    "-----BEGIN RSA " + "PRIVATE KEY-----",
                                    "token = " + "Zx9Qw8Er7Ty6Ui5Op4As3Df2"])
def test_mask_removes_every_secret_shape_and_keeps_the_rest(secret):
    text = f"+    check(x)\n+    assert f(\"{secret}\")  # fixture\n+    done()\n"
    assert pb.has_secret(text)
    out, n = pb.mask_secrets(text)
    assert out is not None and not pb.has_secret(out) and n == 1
    assert secret not in out and pb.SECRET_MASK in out
    assert "+    check(x)" in out and "+    done()" in out
    assert out.split("\n")[1].startswith("+    assert f(")  # the code before the secret stays


def test_keyword_hit_masks_the_value_after_it():
    # TOKEN_RE's key-assignment match stops at "api_key="; the value must not survive it
    out, _ = pb.mask_secrets("+api_key=" + "Qw8Er7Ty6Ui5Op4As3Df2Gh1")
    assert "Qw8Er7" not in out


def test_card_mask_keeps_the_rest_of_the_line():
    out, n = pb.mask_secrets(f'+    assert card_hit("{CARD}")  # a fake visa')
    assert out == f'+    assert card_hit("{pb.SECRET_MASK}")  # a fake visa' and n == 1


def test_non_ascii_line_is_masked_whole_with_its_diff_marker():
    out, n = pb.mask_secrets(f"+ café {CARD}")
    assert out == "+" + pb.SECRET_MASK and n == 1


def test_secret_split_over_two_lines_masks_both():
    text = "ok\napi_key\n= " + "abcdefghijk12345ZZZZZZ" + "\nok"
    out, n = pb.mask_secrets(text)
    assert out is not None and not pb.has_secret(out) and n == 2
    assert out.startswith("ok\n") and out.endswith("\nok")


def test_clean_text_is_returned_unchanged():
    text = "commit 5f2a9c1e0b\n2026-09-28 run 1234 5678 took 12s\nhttps://x.test/?id=4111111111111111\n"
    assert pb.mask_secrets(text) == (text, 0)


def test_unmaskable_text_is_refused(monkeypatch):
    monkeypatch.setattr(pb, "has_secret", lambda t: True)
    assert pb.mask_secrets("anything")[0] is None


class Judge:
    def __init__(self):
        self.calls = []

    def __call__(self, state, questions):
        self.calls.append((state, questions))
        return {"answers": {k: {"noul": 0.9} for k in questions}}


def test_code_gate_sends_the_masked_diff():
    judge = Judge()
    rows, code = sj.run_code_gate([("wt.diff", DIFF)], ["the test checks a fake visa number"],
                                  ask_fn=judge)
    (state, _q), = judge.calls
    assert not pb.has_secret(state) and pb.SECRET_MASK in state
    assert CARD not in state and STRIPE not in state and "hunter2" not in state
    assert "test_card_is_held" in state and code == 0


def test_code_gate_pattern_arm_still_reads_the_local_text():
    ev = 'import re\nCARD_RE = re.compile(r"[0-9]{4}( [0-9]{4}){3}")\n'
    judge = Judge()
    rows, code = sj.run_code_gate([("ev", ev + f"# {CARD}\n")], [f"`{CARD}` matches CARD_RE"],
                                  ask_fn=judge)
    assert judge.calls == [] and rows[0]["verdict"] == "SUPPORTED"


def test_ask_still_refuses_raw_secret_text(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-not-a-key")
    monkeypatch.setattr(jc, "transport", lambda *a: pytest.fail("secret was sent"))
    with pytest.raises(jc.JevError, match="contains a secret; not sent"):
        jc.ask("CODE:\n" + DIFF, {"c1": {"instructions": "Is it?", "criteria": {"YES": "y"}}})


SITE = """import urllib.request, os
def _rec(req, *a, **k):
    open(os.environ["MASK_SENT"], "ab").write(req.data + b"\\n")
    raise OSError("blocked in test")
urllib.request.urlopen = _rec
"""


def run_cli(tmp_path, argv):
    (tmp_path / "site").mkdir(exist_ok=True)
    (tmp_path / "site" / "sitecustomize.py").write_text(SITE)
    sent = tmp_path / "sent.log"
    env = dict(os.environ, PYTHONPATH=str(tmp_path / "site"), MASK_SENT=str(sent),
               TYPESAFE_API_KEY="test-not-a-key")
    env.pop("SUPERJEV_GATE_CMD", None)
    p = subprocess.run([sys.executable, *argv], env=env, capture_output=True, text=True, timeout=120)
    return p, (sent.read_text() if sent.exists() else "")


@pytest.mark.parametrize("entry", ["gate-code", "check"])
def test_cli_sends_the_diff_masked_never_refuses_it(tmp_path, entry):
    """The live failure: a worktree diff with test card numbers was refused whole, so the
    change could not be checked. Now the request goes out, with no secret-shaped text in it."""
    ev = tmp_path / "wt.diff"
    ev.write_text(DIFF)
    claim = ["--claim", "the new test checks that a fake visa number is held"]
    argv = {"gate-code": [str(SKILL / "superjev.py"), "gate", str(ev), *claim],
            "check": [str(SKILL / "lib" / "jev_client.py"), str(ev), *claim]}[entry]
    p, sent = run_cli(tmp_path, argv)
    assert sent, p.stdout + p.stderr
    for body in sent.strip().split("\n"):
        state = json.loads(body)["state"]
        assert pb.SECRET_MASK in state and not pb.has_secret(state)
        assert CARD not in state and STRIPE not in state and "hunter2" not in state
    assert "contains a secret; not sent" not in p.stdout + p.stderr


def test_cli_claim_holding_a_secret_is_still_refused(tmp_path):
    ev = tmp_path / "wt.diff"
    ev.write_text(DIFF)
    p, sent = run_cli(tmp_path, [str(SKILL / "superjev.py"), "gate", str(ev),
                                 "--claim", f"the test uses {CARD}"])
    assert sent == "" and p.returncode != 0 and "CLEAN" not in p.stdout
