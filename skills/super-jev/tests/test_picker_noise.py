#!/usr/bin/env python3
"""Picker noise (2026-09-24 live asks): a Super Jev test report topped word search and was
returned as the answer; word search lost a slot to files routing already had; and the
secret scan held 'KEY: in other-file.md'. No network and no real key.

    python3 -m pytest skills/super-jev/tests/test_picker_noise.py -q
"""
import hashlib
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_picker", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)
pb = ask.prepare_bulk


def _cache(files):
    return {str(p): {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "pass": True,
                     "description": "", "question": ""} for p in files}


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


# 1. test/scratch output is never searched or connected; real ops notes still are
def test_word_search_skips_test_report_that_repeats_the_question(tmp_path, monkeypatch):
    q = "what is on the health-fitness pending to-do list"
    report = _write(tmp_path, "ops/superjev-test3-20260924.md",
                    "# Super Jev test 3\nQ: what is on the health-fitness pending to-do list\n" * 5)
    sj = _write(tmp_path, "ops/sjfix/run.md", "pending to-do list health fitness " * 5)
    hand = _write(tmp_path, "notes/ask-hand-test-1.md", "pending to-do list health fitness " * 5)
    sample = _write(tmp_path, "notes/pending-sample.md", "# Pending sample\nhealth-fitness pending to-do list")
    real = _write(tmp_path, "pending.md", "# Pending\n- health-fitness to-do: book physio\n- list refill")
    ops = _write(tmp_path, "ops/pending-review.md", "# Ops\nhealth-fitness pending to-do list review")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache([report, sj, hand, sample, real, ops]))
    found = {p for _, p, _ in ask.word_search(q, ["p1"])}
    assert found == {str(real), str(ops), str(sample)}


def test_connect_inventory_skips_test_material_but_keeps_real_ops_notes(tmp_path):
    _write(tmp_path, "ops/superjev-test3-20260924.md", "test report")
    _write(tmp_path, "ops/sj-r5/notes.md", "scratch")
    _write(tmp_path, "notes/x-hand-test-2.md", "scratch")
    keep = [_write(tmp_path, "ops/deploy-notes.md", "real"), _write(tmp_path, "pending.md", "real"),
            _write(tmp_path, "ops/sj-manual/pointers.md", "real"), _write(tmp_path, "reuse/superjev-sample/README.md", "real")]
    files, _held = pb.inventory([tmp_path])[:2]
    names = {Path(f["path"] if isinstance(f, dict) else f).resolve() for f in files}
    assert names == {p.resolve() for p in keep}


# 2. files routing already has are dropped before word search takes its top N
def test_word_search_fills_every_slot_after_skipping_routed_files(tmp_path, monkeypatch):
    files = [_write(tmp_path, f"f{i}.md", "knee brace size " * (6 - i)) for i in range(5)]
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache(files))
    top = [p for _, p, _ in ask.word_search("knee brace size", ["p1"])]
    got = [p for _, p, _ in ask.word_search("knee brace size", ["p1"], skip={top[0]})]
    assert len(got) == ask.FALLBACK_FILES and top[0] not in got


# 3. secret scan: a keyword followed by a short plain word is prose; real keys still held
NOT_SECRET = ["PASSWORD: in other-file.md", "password: see vault", "passwd = none",
              "API_KEY: in together-ai-secret.md", "client_secret = none", "aws_secret_access_key: in vault"]
SECRET = ["api_key: Zq8rTvLmWp4K", "API_KEY=" + "sk-" + "a1B2" * 9, "password: hunter2",
          "password = correcthorsebattery", "passwd: x#y", 'api_key: "Qm9vYmFyYmF6cXV4"',
          "secret_key=abcdefghij", "client_secret: x#y", "aws_secret_access_key = wJalrXUtnFEMIK7MDENG"]


@pytest.mark.parametrize("text", NOT_SECRET)
def test_keyword_with_short_plain_value_is_not_held(text):
    assert not pb.has_secret(text)


@pytest.mark.parametrize("text", SECRET)
def test_realistic_fake_keys_are_still_held(text):
    assert pb.has_secret(text)


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_node_scanner_agrees():
    from test_scan_parity_round9 import _node
    assert _node(NOT_SECRET + SECRET) == [False] * len(NOT_SECRET) + [True] * len(SECRET)
