#!/usr/bin/env python3
"""Round 9: the Python and Node secret scanners agree on Unicode input. Both normalize the
same way (NFKC, casefold, control/whitespace to space, non-ASCII letter to x, digit to 0)
and run the shared secret_patterns.json ASCII-only. Every breaker case in
data/unicode_scan_cases.json plus 20 seeded fuzz inputs goes to both; they must agree and
every secret must be held. Needs node on PATH; no network.

    python3 -m pytest skills/super-jev/tests/test_scan_parity_round9.py -q
"""
import importlib.util
import json
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parent.parent
sys.path.insert(0, str(SKILL))
_spec = importlib.util.spec_from_file_location("prepare_bulk_r9", SKILL / "prepare_bulk.py")
pb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pb)

CASES = json.loads((Path(__file__).parent / "data" / "unicode_scan_cases.json").read_text())
# Not secrets: prose, the 1Password app name, a card-like id inside a URL, and homoglyph
# spellings (Cyrillic or accented letters become the placeholder x, so they no longer spell
# the keyword - both sides agree they pass).
NOT_SECRET = {"The box is blue.", "1password", "١password", "https://x.com/4111111111111111",
              "сk_live_9fQ2xZ7pL0aB3cD8eF", "pаssword", "PÄSSWORD"}

# Secret shapes, built by concatenation so this file never holds one, and Unicode disguises
# that normalizing must see through.
SHAPES = ["sk" + "_live_9fQ2xZ7pL0aB3cD8eF", "gh" + "p_" + "a1" * 18, "pass" + "word: hunter2",
          "token=Ab3dEf9GhIjK1mNoPqRsTu", "4111 1111 1111 1111", "AK" + "IA1234567890ABCDEF"]
DISGUISE = [" ", "　", "﻿", " ", "\x1c", "​"]


def _disguise(s, rng):
    out = []
    for c in s:
        r = rng.random()
        if c.isdigit() and r < 0.3:
            out.append(chr(0xFF10 + int(c)) if r < 0.15 else chr(0x0660 + int(c)))
        elif c.isalpha() and r < 0.2:
            out.append(chr(0x1D41A + ord(c.lower()) - 97) if c.isascii() else c)  # math bold
        elif c == " " and r < 0.8:
            out.append(rng.choice(DISGUISE[:4]))
        else:
            out.append(c)
    pre = rng.choice(["", "é ", "note ü ", "١ "])
    return pre + "".join(out) + rng.choice(["", " été", "　"])


def _fuzz():
    rng = random.Random(9)
    return [_disguise(rng.choice(SHAPES), rng) for _ in range(20)]


FUZZ = _fuzz()


def _node(inputs):
    js = ("import {hasSecret} from %s; import fs from 'node:fs';"
          "console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(hasSecret)));"
          % json.dumps(str(REPO / "src" / "secret-scan.ts")))
    out = subprocess.run(["node", "--input-type=module", "-e", js], input=json.dumps(inputs),
                         capture_output=True, text=True, check=True, cwd=REPO)
    return json.loads(out.stdout)


@pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")
def test_python_and_node_agree_and_hold_every_secret():
    inputs = CASES + FUZZ
    py, js = [pb.has_secret(s) for s in inputs], _node(inputs)
    assert [s for s, a, b in zip(inputs, py, js) if a != b] == []
    assert [s for s, a in zip(inputs, py) if not a and s not in NOT_SECRET] == []
    assert [s for s, a in zip(inputs, py) if a and s in NOT_SECRET] == []


def test_normalize_sees_through_the_breaker_disguises():
    assert pb.normalize_for_scan("paſsword") == "password"
    assert pb.normalize_for_scan("Key\x1c=") == "key ="
    assert pb.normalize_for_scan("4111 1111") == "4111 1111"
    assert pb.normalize_for_scan("ésk١") == "xsk0"
    assert pb.normalize_for_scan("a\nb\tc") == "a\nb c"


@pytest.mark.parametrize("line", ["pwd_" * 112500, "key-" * 112500, "token_" * 75000,
                                  "ü" * 450000, " " * 450000, "١" * 450000],
                         ids=["pwd", "key", "token", "u-umlaut", "nbsp", "arabic-digit"])
def test_worst_case_lines_still_under_03s(line):
    t = time.monotonic()
    pb.has_secret(line)
    assert time.monotonic() - t < 0.3
