#!/usr/bin/env python3
"""Offline tests: a diff holding secret-shaped text (a card scanner's fake card numbers, a
redaction test's sample key) is judged file by file instead of refused whole. A file whose
text scans as a secret is withheld whole, real and fake values alike; every other file goes
to the judge as it is. A claim holding a secret is still refused, and jev_client.ask keeps
its own full scan.

Why whole files: the scanner finds where a secret starts, never where it ends. Four review
rounds of masking inside a file each found a part of a secret still sent (a key body, a
wrapped token, a JWT signature, the secret beside an AWS key id, a card's CVV, a value
concatenated over lines). Every such shape is kept below as a leak case.

Token-shaped strings are built by concatenation so this file itself never holds one.

    python3 -m pytest skills/super-jev/tests/test_masked_evidence.py -q
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
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
AWS_ID = "AKIA" + "ABCDEFGHIJKLMNOP"
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/" + "bPxRfiCYEXAMPLEKEY"
VALUE = "Hunter2" + "Hunter2Xy9"
PEM = "-----BEGIN RSA " + "PRIVATE KEY-----"
PEM_END = "-----END RSA " + "PRIVATE KEY-----"
KEY_BODY = ["MIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun",
            "VTLw7onLRnrq0/IzW7yWR7QkrmBL7jTKEn5u+qKhbwKfBstIs+bMY2Zkp18gnTxK",
            "LxoS2tFczGkPLPgizskuemMghRniWaoLcyehkd3qqGElvW/VDL5AaWTg0nLVkjRo"]
JWT_HEAD = "ey" + "JhbGciOiJIUzI1NiJ9"
JWT_BODY = "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0"
JWT_SIG = "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
BASIC = "dXNlcjpwYXNzd29y" + "ZDEyMzQ1Njc4OTA="

PRODUCT = ("diff --git a/src/scan.py b/src/scan.py\n--- a/src/scan.py\n+++ b/src/scan.py\n"
           "@@ -1 +1,2 @@\n def scan(text):\n+    return card_hit(text)\n")


def test_file(body, name="tests/test_scan.py"):
    return (f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n"
            f"@@ -1,2 +1,{body.count(chr(10)) + 1} @@\n{body}")


test_file.__test__ = False  # a helper, not a test

FIXTURES = test_file("+def test_card_is_held():\n"
                     f'+    assert scan.card_hit("{CARD}")  # a fake visa\n'
                     f'+    assert scan.has_secret("{STRIPE}")\n'
                     f"+    assert scan.has_secret('{PW}')\n")
DIFF = PRODUCT + FIXTURES


def test_the_reported_case_is_judged_without_its_fixtures():
    """The live failure: a worktree diff with test card numbers was refused whole."""
    assert pb.has_secret(DIFF)
    out, withheld = pb.mask_secrets(DIFF)
    assert out is not None and not pb.has_secret(out) and withheld == ["tests/test_scan.py"]
    assert out.startswith(PRODUCT)  # the product file is sent as it is
    assert out.endswith("+++ b/tests/test_scan.py\n" + pb.SECRET_WITHHELD.format(n=5) + "\n")
    assert "hunter2" not in out and "4111" not in out and "card_is_held" not in out


# Every part of each secret below reached the judge under some masking-inside-a-file rule.
LEAKS = {
    "card-with-cvv": (f"+CARD = '{CARD} 12/29 737'\n", ["12/29 737"]),
    "stripe-concat": (f"+KEY = ('{STRIPE}'\n+       'TAILabcdef99')\n", ["TAILabcdef99"]),
    "jwt-signature": (f"+TOKEN = '{JWT_HEAD}.{JWT_BODY}.{JWT_SIG}'\n", [JWT_SIG]),
    "jwt-wrapped": (f"+curl -H 'Authorization: Bearer {JWT_HEAD}.{JWT_BODY}.\\\n+{JWT_SIG}'\n", [JWT_SIG]),
    "basic-wrapped": ("+H = ('Authorization: Basic " + BASIC[:12] + "'\n+     '" + BASIC[12:] + "')\n", [BASIC[12:]]),
    "aws-csv-row": (f"+deploy,{AWS_ID},{AWS_SECRET}\n", [AWS_SECRET]),
    "pem-body": ("+" + PEM + "\n" + "".join("+" + b + "\n" for b in KEY_BODY) + "+" + PEM_END + "\n", KEY_BODY),
    "pem-before-hunk-start": ("".join(" " + b + "\n" for b in KEY_BODY[:2]) + " " + PEM_END + "\n+" + PEM + "\n+"
                              + KEY_BODY[2] + "\n", KEY_BODY),
    "pem-second-hunk": (" " + PEM + "\n-" + KEY_BODY[0] + "\n+" + KEY_BODY[1] + "\n@@ -20,2 +20,2 @@\n-"
                        + KEY_BODY[2] + "\n " + PEM_END + "\n", KEY_BODY),
    "pem-py-paren": ('+KEY = (\n+    "' + PEM + '\\n"\n' + "".join('+    "' + b + '\\n"\n' for b in KEY_BODY) + "+)\n",
                     KEY_BODY),
    "pem-js-plus": ('+const KEY = "' + PEM + '\\n"\n' + "".join('+  + "' + b + '\\n"\n' for b in KEY_BODY), KEY_BODY),
    "yaml-block": ("+db:\n+  password: |  # rotated\n+    " + VALUE + "\n+  host: x\n", [VALUE]),
    "yaml-plain-multiline": ("+password:\n+  hunter2xyz\n+  " + VALUE + "\n", [VALUE]),
    "py-dict-concat": ('+cfg = dict(\n+    password="hunter2xyz"\n+    "' + VALUE + '",\n+)\n', [VALUE]),
    "js-template": ("+const password = `\n+" + VALUE + "\n+`;\n", [VALUE]),
    "shell-heredoc": ("+password=$(cat <<EOF\n+" + VALUE + "\n+EOF\n+)\n", [VALUE]),
    "pem-tab-header": ("+-----BEGIN\tRSA " + "PRIVATE KEY-----\n" + "".join("+" + b + "\n" for b in KEY_BODY), KEY_BODY),
}


@pytest.mark.parametrize("name", sorted(LEAKS))
def test_no_part_of_a_secret_reaches_the_judge(name):
    body, parts = LEAKS[name]
    text = test_file(body, "fixtures/creds.txt") + PRODUCT
    assert pb.has_secret(text)  # origin/main refused this text whole
    out, withheld = pb.mask_secrets(text)
    assert out is not None and not pb.has_secret(out) and withheld == ["fixtures/creds.txt"]
    for part in parts:
        assert part not in out, (name, part)
    assert out.endswith(PRODUCT)


def test_text_that_is_not_a_diff_is_not_judged():
    assert pb.mask_secrets(f"notes\nthe card is {CARD}\n") == (None, ["the whole text"])


def test_a_secret_shaped_header_is_withheld_too():
    name = "password: " + "hunter2xyz9"
    text = f"diff --git a/x b/x {name}\n--- a/x\n+++ b/x\n@@ -0,0 +1 @@\n+x = 1\n"
    assert pb.has_secret(text)
    out, withheld = pb.mask_secrets(text)
    assert out == pb.SECRET_WITHHELD.format(n=5) + "\n" and withheld == ["a file"]


def test_text_before_the_first_file_is_judged_on_its_own():
    out, withheld = pb.mask_secrets(f"summary: rotate {PW}\n" + PRODUCT)
    assert withheld == ["a file"] and out == pb.SECRET_WITHHELD.format(n=1) + "\n" + PRODUCT


def test_non_ascii_digit_in_one_file_keeps_the_others_judged():
    # a non-ASCII digit turns the whole-text card check's Luhn test off; parts must match it
    other = test_file("+label = '\u0661\u0662'\n+TRACK = '9302 2110 4790 0005 3721 11'\n", "labels.py")
    out, withheld = pb.mask_secrets(FIXTURES + other + PRODUCT)
    assert out is not None and not pb.has_secret(out) and out.endswith(PRODUCT)


def test_clean_text_is_returned_unchanged():
    text = "commit 5f2a9c1e0b\n2026-09-28 run 1234 5678 took 12s\nhttps://x.test/?id=4111111111111111\n"
    assert pb.mask_secrets(text) == (text, [])


def test_many_files_mask_in_linear_time():
    text = "".join(test_file(f"+v = '{CARD}'\n", f"t{k}.py") + PRODUCT for k in range(2000))
    t = time.process_time()
    out, withheld = pb.mask_secrets(text)
    assert len(withheld) == 2000 and time.process_time() - t < 20


class Judge:
    def __init__(self):
        self.calls = []

    def __call__(self, state, questions):
        self.calls.append((state, questions))
        return {"answers": {k: {"noul": 0.9} for k in questions}}


def test_code_gate_judges_the_diff_without_the_withheld_file():
    judge = Judge()
    info = {}
    rows, code = sj.run_code_gate([("wt.diff", DIFF)], ["scan returns card_hit(text)"],
                                  ask_fn=judge, mask_info=info)
    (state, _q), = judge.calls
    assert not pb.has_secret(state) and "return card_hit(text)" in state
    assert "4111" not in state and "hunter2" not in state and code == 0
    assert info["withheld"] == ["tests/test_scan.py"]


def test_code_gate_drops_a_plain_evidence_file_and_keeps_the_rest():
    judge = Judge()
    sj.run_code_gate([("notes.md", f"card {CARD}\n"), ("wt.diff", PRODUCT)],
                     ["scan returns card_hit(text)"], ask_fn=judge)
    (state, _q), = judge.calls
    assert "notes.md" not in state and "return card_hit(text)" in state


def test_code_gate_refuses_when_no_evidence_is_left():
    with pytest.raises(ValueError, match="contains a secret; not sent"):
        sj.run_code_gate([("notes.md", f"card {CARD}\n")], ["x is y"], ask_fn=Judge())


def test_code_gate_pattern_arm_still_reads_the_local_text():
    ev = 'import re\nCARD_RE = re.compile(r"[0-9]{4}( [0-9]{4}){3}")\n'
    judge = Judge()
    rows, code = sj.run_code_gate([("ev", ev + f"# {CARD}\n")], [f"`{CARD}` matches CARD_RE"],
                                  ask_fn=judge)
    assert judge.calls == [] and rows[0]["verdict"] == "SUPPORTED"


def test_pattern_only_claims_never_need_the_mask(monkeypatch):
    # nothing goes to the judge, so evidence with nothing judgeable does not refuse them
    monkeypatch.setattr(sj, "_mask_evidence", lambda items: pytest.fail("masked for nothing"))
    ev = 'import re\nFOO_RE = re.compile(r"foo")\n'
    rows, code = sj.run_code_gate([("ev", ev)], ["`foo` matches FOO_RE"], ask_fn=Judge())
    assert rows[0]["verdict"] == "SUPPORTED" and code == 0


def test_ask_still_refuses_raw_secret_text(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-not-a-key")
    monkeypatch.setattr(jc, "transport", lambda *a: pytest.fail("secret was sent"))
    with pytest.raises(jc.JevError, match="contains a secret; not sent"):
        jc.ask("CODE:\n" + DIFF, {"c1": {"instructions": "Is it?", "criteria": {"YES": "y"}}})


def test_check_drops_a_secret_file_and_names_it(tmp_path, monkeypatch, capsys):
    notes, diff = tmp_path / "notes.md", tmp_path / "wt.diff"
    notes.write_text(f"card {CARD}\n")
    diff.write_text(DIFF)
    sent = []
    monkeypatch.setattr(jc, "check", lambda ev, *a, **k: sent.append(ev) or ([], {}, 0))
    monkeypatch.setattr(jc, "print_table", lambda *a, **k: None)
    assert jc.main([str(notes), str(diff), "--claim", "scan returns card_hit(text)"]) == 0
    (path, text), = sent[0]
    assert path == str(diff) and text.startswith(PRODUCT) and not pb.has_secret(text)
    err = capsys.readouterr().err
    assert "withheld 2 file(s)" in err and str(notes) in err and "tests/test_scan.py" in err


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
def test_cli_sends_the_diff_without_its_secret_files(tmp_path, entry):
    """End to end, over the recorded wire: the request goes out, holding the product file
    and no part of any leak case."""
    ev = tmp_path / "wt.diff"
    ev.write_text(DIFF + "".join(test_file(body, f"fx/{n}.txt") for n, (body, _p) in sorted(LEAKS.items())))
    claim = ["--claim", "scan returns card_hit(text)"]
    argv = {"gate-code": [str(SKILL / "superjev.py"), "gate", str(ev), *claim],
            "check": [str(SKILL / "lib" / "jev_client.py"), str(ev), *claim]}[entry]
    p, sent = run_cli(tmp_path, argv)
    assert sent, p.stdout + p.stderr
    for body in sent.strip().split("\n"):
        state = json.loads(body)["state"]
        assert "return card_hit(text)" in state and not pb.has_secret(state)
        assert "4111" not in state and "hunter2" not in state
        for _body, parts in LEAKS.values():
            assert not any(part in state for part in parts)
    assert "contains a secret; not sent" not in p.stdout + p.stderr
    if entry == "check":  # the gate prints its note with the verdict, which the blocked wire stops
        assert f"withheld {len(LEAKS) + 1} file(s)" in p.stderr


def test_cli_claim_holding_a_secret_is_still_refused(tmp_path):
    ev = tmp_path / "wt.diff"
    ev.write_text(DIFF)
    p, sent = run_cli(tmp_path, [str(SKILL / "superjev.py"), "gate", str(ev),
                                 "--claim", f"the test uses {CARD}"])
    assert sent == "" and p.returncode != 0 and "CLEAN" not in p.stdout
