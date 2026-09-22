#!/usr/bin/env python3
"""Offline tests for the rc1 hardening round 4 fixes: the widened secret scan,
one file's content-check error no longer unfilters the rest, a favorable side
label at low confidence does not block, and the exact-value confirm label.

No network and no real key. Token-shaped strings are built by concatenation so
this file itself never holds one.

    python3 -m pytest skills/super-jev/tests/test_hardening_round4.py -q
"""
import importlib.util
import json
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
ask = load("ask_r4", SKILL / "ask.py")
pb = load("prepare_bulk_r4", SKILL / "prepare_bulk.py")
jc = load("jev_client_r4", SKILL / "lib" / "jev_client.py")

RAND = "q8Zr2LmX7vKp4TnB9wYc3HdJ6sFa1Ge5"  # 32 mixed chars, high entropy


# 1. secret scan: every shape the breaker named is held
@pytest.mark.parametrize("text", [
    "API key: " + RAND,
    "api key = " + RAND,
    "stripe " + "sk_" + "live_" + RAND,
    "openai " + "sk-" + "proj-" + RAND,
    "legacy " + "sk-" + RAND,
    "gh" + "p_" + RAND + "abcd",
    "github" + "_pat_" + "11ABCDEFG0" + RAND,
    "AK" + "IA" + "IOSFODNN7EXAMPLE",
    "AWS_SECRET_ACCESS_KEY=" + RAND,
    "slack " + "xo" + "xb-" + "1234567890-abcdefghij",
    "Authorization: " + "Bearer " + RAND,
    "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIE...\n",
    "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
    "deploy_token = '" + RAND + "'",
    "client secret: " + RAND,
])
def test_secret_shapes_are_held(text):
    assert pb.has_secret("notes\n" + text + "\nmore")


@pytest.mark.parametrize("text", [
    "Remember to get an API key from the dashboard before setup.",
    "The key to a good espresso is a fine grind and 94 C water.",
    "Keep this a secret from Maria until her birthday.",
    "The token ring network was replaced in 2019.",
    "Bearer bonds were common in the 1920s.",
    "See https://example.com/docs?id=abcdefghijklmnopqrstuvwxyz for the API.",
    "The next cleaning is booked for 2026-09-10 at 9:30 am.",
    "key: value",
    "Token: aaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "sort key = created_at_descending_order",
    "Market risk-and-the-economy-outlook-for-2026 is covered below.",
])
def test_normal_prose_is_not_held(text):
    assert not pb.has_secret(text)


def test_secret_detail_masks_a_token_line(tmp_path):
    f = tmp_path / "n.md"
    f.write_text("ok\nAuthorization: " + "Bearer " + RAND + "\n")
    d = pb.secret_detail(f)
    assert d["line"] == 2 and RAND not in d["masked"]


# 2. one file's check error marks only that file INCONCLUSIVE
def test_one_errored_file_does_not_unfilter_the_others(tmp_path, monkeypatch, capsys):
    good, absent, broken = (tmp_path / n for n in ("good.md", "absent.md", "broken.md"))
    for f in (good, absent, broken):
        f.write_text("x")
    results = {str(good): (0.9, False, None, None), str(absent): (None, False, None, None),
               str(broken): (None, False, "content check could not run: TimeoutExpired", None)}
    monkeypatch.setattr(ask, "confirm_one", lambda q, p: results[p])
    scores, partial, err, notes = ask.confirm("q", list(results))
    assert notes == {str(broken): ask.INCONCLUSIVE} and err
    cands = [{"score": 0.8, "originalPath": p} for p in results]
    monkeypatch.setattr(ask, "memory", lambda req: {"pointers": ["p1"]} if req["action"] == "panel" else
                        {"status": "candidates", "candidates": cands})
    ask.lookup("q", "me", tmp_path / "s")
    out = capsys.readouterr().out
    assert str(good) in out and str(absent) not in out
    assert f"{broken}  [p1]  (inconclusive" in out and "[content-check] error" in out


# 3. a favorable side label under 0.80 does not block; red or missing still does
def _check(monkeypatch, claim_ans, side_ans):
    answers = {"c1": claim_ans, **{k: side_ans.get(k, {"choice": v, "confidence": 0.99})
                                   for k, v in (("leaked_internal", "CLEAN"),
                                                ("time_sensitive", "NOT_TIME_SENSITIVE"),
                                                ("self_contradictory", "CONSISTENT"),
                                                ("overclaim", "HONEST"))}}
    monkeypatch.setattr(jc, "ask", lambda state, qs: {"answers": answers})
    return jc.check([("rent.md", "Late rent after the 5th: $75 penalty.")],
                    ["If rent is paid after the fifth, there's a seventy-five dollar penalty."])[2]


def test_low_confidence_favorable_side_label_does_not_block(monkeypatch):
    assert _check(monkeypatch, {"choice": "SUPPORTED", "confidence": 1.0},
                  {"time_sensitive": {"choice": "NOT_TIME_SENSITIVE", "confidence": 0.78}}) == 0


@pytest.mark.parametrize("claim, side", [
    ({"choice": "SUPPORTED", "confidence": 1.0}, {"time_sensitive": {"choice": "TIME_SENSITIVE", "confidence": 0.55}}),
    ({"choice": "SUPPORTED", "confidence": 1.0}, {"overclaim": {"choice": "OVERCLAIMS", "confidence": 0.9}}),
    ({"choice": "SUPPORTED", "confidence": 1.0}, {"leaked_internal": None}),
    ({"choice": "SUPPORTED", "confidence": 1.0}, {"overclaim": {"choice": "MAYBE", "confidence": 0.99}}),
    ({"choice": "SUPPORTED", "confidence": 0.79}, {}),
    ({"choice": "NOT_SUPPORTED", "confidence": 0.99}, {}),
    ({"choice": "UNSURE", "confidence": 0.99}, {}),
    (None, {}),
])
def test_unfavorable_or_weak_main_verdict_still_blocks(monkeypatch, claim, side):
    assert _check(monkeypatch, claim, side) == 3


# 4. the confirm label asks for the exact value for the exact event, floor 0.70
def test_confirm_label_asks_for_the_exact_value(tmp_path, monkeypatch):
    f = tmp_path / "trip.md"
    f.write_text("Return flight TP201 is on 2026-10-12.")
    calls = []

    def fake_run(cmd, input, **kw):
        calls.append(input)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(
            {"status": "candidates", "candidates": [{"score": 0.64}]}), "")
    monkeypatch.setattr(ask.subprocess, "run", fake_run)
    assert ask.confirm_one("What time does TP201 depart?", str(f))[0] is None
    label = json.loads(calls[0])["catalog"]["nodes"][1]["label"]
    assert "exact value asked for" in label and "another event" in label


def test_client_sends_explicit_user_agent(monkeypatch):
    """TypeSafe's edge answers 403 to the default Python-urllib user agent."""
    import importlib, sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
    jc = importlib.import_module("jev_client")
    seen = {}
    def fake(url, body, headers, timeout):
        seen.update(headers)
        return {"answers": {}, "model": "jev-test"}
    monkeypatch.setattr(jc, "transport", fake)
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    try:
        jc.ask("state", {"q": {"type": "choice", "question": "q?", "choice": {"criteria": {"a": "a", "b": "b"}}}})
    except Exception:
        pass
    assert "User-Agent" in seen and "Python-urllib" not in seen["User-Agent"]
