#!/usr/bin/env python3
"""The judge profile (judge_profiles.json): one table of the judge's limits, one token unit,
one function every size check calls, no second selector.

    python3 -m pytest skills/super-jev/tests/test_judge_profile.py -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.append(str(SKILL))
import judge_profile  # noqa: E402

# Each derived value and the number that was written in place before the profile.
OLD = {
    "superjev.DEFAULT_INPUT_CAP_TOK": 32_000,
    "superjev.JUDGE_CALL_TOKENS": 30_000,
    "superjev.DEFAULT_INPUT_USD_PER_MTOK": 0.042,
    "superjev.DEFAULT_MAX_PARTS": 40,
    "superjev.DEFAULT_GATE_WINDOW_TOK": 16_000,
    "jev_client.MAX_INPUT_TOKENS": 30_000,
    "jev_client.MAX_QUESTIONS": 255,
    "jev_client.LINE": 0.80,
    "jev_client.API_URL": "https://api.typesafe.ai/v1/systemone",
    "jev_client.MODEL": "jev-latest",
    "prepare_bulk.CEILING_BYTES": 250_000,
    "connect_checked.CEILING_MSG": "exceeds the 32,768-token ceiling",
}

_PROBE = r"""
import json, sys
sys.path[:0] = [sys.argv[1], sys.argv[1] + "/lib"]
import superjev, jev_client, connect_checked, prepare_bulk, judge_profile
print(json.dumps({
    "superjev.DEFAULT_INPUT_CAP_TOK": superjev.DEFAULT_INPUT_CAP_TOK,
    "superjev.JUDGE_CALL_TOKENS": superjev.JUDGE_CALL_TOKENS,
    "superjev.DEFAULT_INPUT_USD_PER_MTOK": superjev.DEFAULT_INPUT_USD_PER_MTOK,
    "superjev.DEFAULT_MAX_PARTS": superjev.DEFAULT_MAX_PARTS,
    "superjev.DEFAULT_GATE_WINDOW_TOK": superjev.DEFAULT_GATE_WINDOW_TOK,
    "jev_client.MAX_INPUT_TOKENS": jev_client.MAX_INPUT_TOKENS,
    "jev_client.MAX_QUESTIONS": jev_client.MAX_QUESTIONS,
    "jev_client.LINE": jev_client.LINE,
    "jev_client.API_URL": jev_client.API_URL,
    "jev_client.MODEL": jev_client.MODEL,
    "prepare_bulk.CEILING_BYTES": prepare_bulk.CEILING_BYTES,
    "connect_checked.CEILING_MSG": connect_checked.CEILING_MSG,
    "same_fn": superjev._judge_tokens is judge_profile.judge_tokens
              and jev_client.estimate_tokens is judge_profile.judge_tokens,
}))
"""


def _probe(**env):
    clean = {k: v for k, v in os.environ.items() if k != "SUPERJEV_JUDGE_PROFILE"}
    clean.update(env)
    out = subprocess.run([sys.executable, "-c", _PROBE, str(SKILL)], env=clean,
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1]), out.stderr


def test_default_profile_derives_every_old_number_exactly():
    got, _ = _probe()
    assert got.pop("same_fn") is True
    assert got == OLD
    assert type(got["superjev.JUDGE_CALL_TOKENS"]) is int


def test_the_jev_profile_states_its_limits_and_nothing_speculative():
    p = judge_profile.PROFILE
    assert (p.window_tokens, p.max_questions_per_call, p.input_usd_per_mtok) == (32_768, 255, 0.042)
    assert (p.input_cap_tokens, p.call_tokens) == (32_000, 30_000)
    assert (p.max_parts, p.confidence_line, p.file_ceiling_bytes, p.gate_window_tokens) == (40, 0.80, 250_000, 16_000)
    for dead in ("question_kinds", "output_usd_per_mtok", "label", "call_usd"):
        assert not hasattr(p, dead)
    raw = json.loads(judge_profile.PROFILES_PATH.read_text())
    for prof in raw["profiles"].values():
        assert not {"question_kinds", "output_usd_per_mtok", "label"} & set(prof)


def test_there_is_no_second_judge_selector():
    assert not hasattr(judge_profile, "PROFILE_ENV")
    for f in list(SKILL.glob("*.py")) + list((SKILL / "lib").glob("*.py")) + list(SKILL.glob("*.json")):
        assert "SUPERJEV_JUDGE_PROFILE" not in f.read_text(), f.name
    # setting the old variable changes nothing, not even a stderr line
    got, err = _probe(SUPERJEV_JUDGE_PROFILE="no-such-judge")
    got.pop("same_fn")
    assert got == OLD and "SUPERJEV_JUDGE_PROFILE" not in err


def test_unknown_name_is_an_error_not_a_silent_fallback():
    with pytest.raises(ValueError, match="no-such-judge"):
        judge_profile.load("no-such-judge")


def test_a_broken_or_missing_table_stops_with_one_clear_line(tmp_path):
    bad = tmp_path / "p.json"
    bad.write_text("{not json")
    for path in (bad, tmp_path / "missing.json"):
        with pytest.raises(SystemExit) as e:
            judge_profile.load(path=path)
        msg = str(e.value)
        assert "judge_profiles" in msg or path.name in msg
        assert "\n" not in msg.strip()


def test_ceiling_text_names_the_window():
    assert judge_profile.PROFILE.ceiling_text == "the 32,768-token ceiling"


# The one unit: bytes/2, rounded up, high on purpose. The Node twin asserts this same table.
TOKENS = [("", 0), ("a", 1), ("abc", 2), ("abcd", 2), ("é" * 3, 3), ("中文", 3),
          ("12345678901234567890", 10)]


def test_judge_tokens_counts_bytes_over_two_rounded_up():
    for text, want in TOKENS:
        assert judge_profile.judge_tokens(text) == want, text
    assert judge_profile.judge_tokens(None) == 0
    assert judge_profile.judge_tokens({"a": "bc"}) == judge_profile.judge_tokens('{"a":"bc"}')


def test_input_cap_uses_the_same_unit_as_the_call_budget(monkeypatch):
    import superjev as sj
    monkeypatch.setenv("SUPERJEV_INPUT_CAP_TOK", "10")
    text = "1234567890" * 3            # 30 bytes: 15 tokens here, 7 by the old chars/4
    kept, truncated, est, cap = sj.cap_check_and_truncate([("a.md", text)], "", "gate")
    assert (truncated, est, cap) == (True, 15, 10)
    assert judge_profile.judge_tokens("".join(t for _, t in kept)) <= 10
    kept, truncated, est, _ = sj.cap_check_and_truncate([("a.md", "é" * 8)], "", "gate")
    assert (truncated, est) == (False, 8)      # 16 bytes = 8 tokens, under the 10 cap
