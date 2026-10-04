#!/usr/bin/env python3
"""PRIV-1 (red team 2026-10-04): the shared secret scan holds a credential label followed by a value,
a password/PIN table column, seed phrases, US SSNs and Luhn-valid cards split by dots, slashes or spaces.
Every body below uses obviously fake values. Ordinary prose that only names these things is not held.
Offline; no provider calls.

    python3 -m pytest skills/super-jev/tests/test_secret_shapes_redteam.py -q
"""
import importlib.util
import json
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, SKILL / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pb = _load("pb_priv1", "prepare_bulk.py")
ask = _load("ask_priv1", "ask.py")

SEED12 = "abandon ability able about above absent absorb abstract absurd abuse access accident"
TABLE = "| Site | User | Password |\n|---|---|---|\n| Gmail | fakeuser | Tr0ub4dor&3FAKE |"
BODIES = [
    "Password - Tr0ub4dor&3FAKE",
    TABLE,
    "| Site | User | PIN |\n|:--|:--|--:|\n| Bank | fakeuser | 4821 |",
    "Office wifi password: correct horse battery staple",
    "the password is correct horse battery staple",
    "Online banking login fakeuser, pw Hunter2Fake!",
    "Mail user fakeuser / pass: BlueSky!2026",
    "**Password:** Tr0ub4dor&3FAKE",
    "Passcode: 918273",
    "Bank PIN: 4821",
    "PIN 4821",
    "PIN: 48",
    "pwd: hunter2fake",
    "Netflix login: rt@example.com  P@ssw0rd99",
    "sk-ant\u200b-api03-FAKEFAKEFAKEFAKEFAKEFAKEFAKE0000",
    "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7FAKE",
    "password (base64): VHIwdWI0ZG9yJjM=",
    "Passphrase = purple monkey dishwasher lamp",
    "Wallet seed phrase: " + SEED12,
    "Recovery phrase - " + SEED12.replace(" ", ", "),
    SEED12,
    SEED12 + " " + "acid acoustic acquire across act action actor actress actual adapt add addict",
    "SSN 123-45-6789",
    "Social security number 123-45-6789",
    "Employee tax id 123-45-6789 on file",
    "SSN: 123 45 6789",
    "Card number 4000.0566.5566.5556, CVV 123.",
    "Card number 4000 0566 5566 5556.",
    "card 4000/0566/5566/5556",
    "card 4000-0566-5566-5556",
    "amex 3412.345678.90127",
]
# Twenty ordinary sentences: the words are there, no value follows.
CLEAN = [
    "Click the password reset link to continue.",
    "Reset your PIN in settings.",
    "The SSN field on the form is optional for contractors.",
    "Passwords must be rotated every 90 days by the security team.",
    "Ask IT for a password manager licence.",
    "Your PIN is never shown on the screen.",
    "Pass the salt to the guests at table 4.",
    "1. Open the app\n2. Tap Settings\n3. Choose Password\n4. Save",
    "The seed packets arrive in March and are stored in the shed.",
    "Keep your recovery phrase offline and never share it with anyone.",
    "The password policy page explains how to pick a strong passphrase.",
    "Social security numbers are collected by the payroll office only.",
    "We ship in 3-5 business days and returns take 10-14 days.",
    "Call 212-555-0143 between 9 and 5 on weekdays.",
    "The invoice total was 1,234.56 and the order number was 4821.",
    "Meeting on 2026-09-01 at 3pm, room 12.",
    "| Field | Notes |\n|---|---|\n| Password | required |\n| Email | optional |",
    "| Name | Role |\n|---|---|\n| Dana | Lead |",
    "Enter your PIN on the keypad to unlock the door.",
    "She said the password is something only she knows.",
    "password: see vault",
    "PIN: none",
    "password: ${PASSWORD}",
    "password: <your password here>",
    "Part 123-45-6789x ships on Friday",
    "red green blue and yellow are colors, the end",
    "pass: 12",
    "pass = 0",
    "pass: 10/12",
    "pass: 00:00",
    "pass: $0",
    "Pass: 2 of 3 checks",
    "pwd -- x",
    "Use pwd -- to print the working directory.",
    'cmd = "password=" + value',
    'q = "password=" +',
]


@pytest.mark.parametrize("body", BODIES)
def test_red_team_body_is_held(body):
    assert pb.has_secret(body)
    assert pb.has_secret("Some notes first.\n\n" + body + "\n\nMore notes after.")


def test_false_positive_corpus_is_20_plus_and_not_held():
    assert len(CLEAN) >= 20
    for s in CLEAN:
        assert not pb.has_secret(s), s


def test_repeated_word_line_is_not_a_seed_phrase():
    assert not pb.has_secret("filler " * 14)


def test_shape_rules_live_in_the_one_pattern_file():
    pat = json.loads((SKILL / "secret_patterns.json").read_text())
    for k in ("cred_label", "seed_label", "seed_bare", "ssn", "ssn_label", "card_sep", "table_label"):
        assert k in pat


def test_payload_and_saved_answer_scans_use_the_same_rule():
    assert pb.payload_has_secret({"question": "x", "passages": {"b": [TABLE]}})
    assert ask.secret_why("Wallet seed phrase: " + SEED12)
    assert ask.secret_why("fakeuser signs in with Password - Tr0ub4dor&3FAKE")
    assert not ask.secret_why("Click the password reset link")


def test_package_builder_never_emits_a_held_line(tmp_path):
    f = tmp_path / "kit.md"
    filler = "\n".join(f"Narwhal deploy note {i}: the build runs weekly on Tuesdays." for i in range(40))
    f.write_text(f"# Narwhal notes\n\n{filler}\n\n## Logins\n\n{TABLE}\n\n"
                 f"Bank PIN: 4821\nSeed phrase: {SEED12}\nSSN 123-45-6789\n\n"
                 f"## Tail\n\n{filler}\n")
    done, ctx = ask.confirm_start("How often does the Narwhal build run?", str(f))
    assert done is None, done
    wire = json.dumps(ctx["payload"])
    for value in ("Tr0ub4dor", "4821", "abandon ability", "123-45-6789", "fakeuser"):
        assert value not in wire
    assert "Narwhal deploy note 0" in wire
    sent = "\n".join(n.get("description", "") for n in ctx["payload"]["catalog"]["nodes"])
    for line in sent.split("\n"):
        assert not pb.has_secret(line), line


def test_file_that_is_mostly_secret_is_held_whole(tmp_path):
    f = tmp_path / "passwords.md"
    f.write_text(TABLE + "\n\nBank PIN: 4821\n")
    done, ctx = ask.confirm_start("What is the mail password?", str(f))
    assert ctx is None and done[3] == ask.HELD_SECRET
