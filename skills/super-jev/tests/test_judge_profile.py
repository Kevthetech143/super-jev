#!/usr/bin/env python3
"""The judge profile (judge_profiles.json): with the default TypeSafe Jev profile every
derived size equals the literal it replaced, and one setting selects another profile.

    python3 -m pytest skills/super-jev/tests/test_judge_profile.py -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.append(str(SKILL))
import judge_profile  # noqa: E402

# Each derived value and the number that was written in place before the profile.
OLD = {
    "superjev.DEFAULT_INPUT_CAP_TOK": 32_000,
    "superjev.JUDGE_CALL_TOKENS": 30_000,
    "superjev.DEFAULT_INPUT_USD_PER_MTOK": 0.042,
    "jev_client.MAX_INPUT_TOKENS": 30_000,
    "jev_client.MAX_QUESTIONS": 255,
    "connect_checked.CEILING_MSG": "exceeds the 32,768-token ceiling",
}

_PROBE = r"""
import json, sys
sys.path[:0] = [sys.argv[1], sys.argv[1] + "/lib"]
import superjev, jev_client, connect_checked, judge_profile
print(json.dumps({
    "superjev.DEFAULT_INPUT_CAP_TOK": superjev.DEFAULT_INPUT_CAP_TOK,
    "superjev.JUDGE_CALL_TOKENS": superjev.JUDGE_CALL_TOKENS,
    "superjev.DEFAULT_INPUT_USD_PER_MTOK": superjev.DEFAULT_INPUT_USD_PER_MTOK,
    "jev_client.MAX_INPUT_TOKENS": jev_client.MAX_INPUT_TOKENS,
    "jev_client.MAX_QUESTIONS": jev_client.MAX_QUESTIONS,
    "connect_checked.CEILING_MSG": connect_checked.CEILING_MSG,
    "profile": judge_profile.PROFILE.name,
}))
"""


def _probe(**env):
    clean = {k: v for k, v in os.environ.items() if k != judge_profile.PROFILE_ENV}
    clean.update(env)
    out = subprocess.run([sys.executable, "-c", _PROBE, str(SKILL)], env=clean,
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1]), out.stderr


def test_default_profile_derives_every_old_number_exactly():
    got, _ = _probe()
    assert got.pop("profile") == "typesafe-jev"
    assert got == OLD
    assert type(got["superjev.JUDGE_CALL_TOKENS"]) is int


def test_the_jev_profile_states_its_limits():
    p = judge_profile.load("typesafe-jev")
    assert (p.window_tokens, p.max_questions_per_call, p.question_kinds) == (32_768, 255, ("noul", "choice", "score"))
    assert (p.input_usd_per_mtok, p.output_usd_per_mtok) == (0.042, 0.0)
    assert (p.input_cap_tokens, p.call_tokens) == (32_000, 30_000)
    assert abs(p.call_usd - 30_000 * 0.042 / 1e6) < 1e-12


def test_connect_unchecked_reason_is_unchanged():
    src = (SKILL / "connect_checked.py").read_text()
    assert 'f"over {PROFILE.window_tokens // 1000}k-token ceiling; split the file"' in src
    assert f"over {judge_profile.load('typesafe-jev').window_tokens // 1000}k-token ceiling" == "over 32k-token ceiling"


def test_one_setting_selects_another_profile(tmp_path, monkeypatch):
    data = json.loads(judge_profile.PROFILES_PATH.read_text())
    data["profiles"]["big"] = dict(data["profiles"]["typesafe-jev"], label="Big", window_tokens=200_000,
                                   max_questions_per_call=50, input_usd_per_mtok=1.0)
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv(judge_profile.PROFILE_ENV, "big")
    p = judge_profile.load(path=path)
    assert (p.name, p.input_cap_tokens, p.call_tokens, p.max_questions_per_call) == ("big", 199_232, 197_232, 50)
    assert p.ceiling_text == "the 200,000-token ceiling"


def test_unknown_profile_falls_back_to_jev_and_says_so():
    got, err = _probe(**{judge_profile.PROFILE_ENV: "no-such-judge"})
    assert got.pop("profile") == "typesafe-jev"
    assert got == OLD
    assert "unknown SUPERJEV_JUDGE_PROFILE='no-such-judge'" in err
