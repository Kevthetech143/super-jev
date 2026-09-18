#!/usr/bin/env python3
"""Offline tests for the arm registry (skills/super-jev/arms).

No network, no door, no model call. Discovery is tested by writing a
throwaway arm into the package directory inside a temp fixture and taking
it away again, because "the registry lists the directory" is the claim,
and a test that imports a hard-coded name would not test it.

    python3 -m pytest skills/super-jev/tests/test_arms.py -q
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

import arms                                              # noqa: E402
import window_model as wm                                # noqa: E402

ARMS_DIR = SKILL / "arms"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts with no arm env set and no warning spent."""
    for k in list(__import__("os").environ):
        if k.startswith(arms.ARM_MODE_ENV_PREFIX) or k in (
                arms.ARMS_CONFIG_ENV, arms.ARMS_SWITCH_ENV):
            monkeypatch.delenv(k, raising=False)
    arms.reset_mode_warnings()
    yield
    arms.reset_mode_warnings()


@pytest.fixture
def temp_arm():
    """Write an extra arm module into the package, remove it after.

    Returns a callable taking the module body; yields the arm's name.
    """
    written = []

    def _write(name, body):
        p = ARMS_DIR / f"{name}.py"
        assert not p.exists(), f"{p} already exists — pick another test name"
        p.write_text(body, encoding="utf-8")
        written.append(p)
        sys.modules.pop(f"arms.{name}", None)
        importlib.invalidate_caches()
        return name

    yield _write
    for p in written:
        sys.modules.pop(f"arms.{p.stem}", None)
        p.unlink(missing_ok=True)


# --------------------------------------------------------- discovery

def test_discovery_lists_the_package_not_a_hard_coded_list(temp_arm):
    before = arms.arm_names()
    assert "pr_state" in before, "the migrated template arm should be found"
    name = temp_arm("zz_test_probe", ARM_BODY.format(mode="block"))
    after = arms.arm_names()
    assert name in after
    assert after == sorted(after), "arm_names should be deterministic order"
    assert set(after) - set(before) == {name}


def test_discovery_skips_private_modules(temp_arm):
    temp_arm("_zz_test_private", ARM_BODY.format(mode="block"))
    assert "_zz_test_private" not in arms.arm_names()


def test_a_module_without_check_is_not_an_arm(temp_arm, capsys):
    temp_arm("zz_test_nocheck", "NAME = 'zz_test_nocheck'\n")
    assert arms.load_arm("zz_test_nocheck") is None
    assert "no callable check" in capsys.readouterr().err


def test_load_arms_returns_specs_with_declared_metadata():
    spec = arms.load_arm("pr_state")
    assert spec is not None
    assert (spec.name, spec.kind, spec.default_mode) == (
        "pr_state", "deterministic", "block")


# ------------------------------------------------------ mode parsing

def test_mode_defaults_to_the_arms_own_default(temp_arm):
    temp_arm("zz_test_adv", ARM_BODY.format(mode="advisory"))
    assert arms.arm_mode(arms.load_arm("zz_test_adv")) == "advisory"


@pytest.mark.parametrize("value,expected", [
    ("block", "block"), ("advisory", "advisory"), ("off", "off"),
    ("ADVISORY", "advisory"), ("  off  ", "off"),
])
def test_env_sets_the_mode(monkeypatch, value, expected):
    monkeypatch.setenv(arms.ARM_MODE_ENV_PREFIX + "PR_STATE", value)
    assert arms.arm_mode(arms.load_arm("pr_state")) == expected


def test_unknown_env_value_falls_back_to_default_with_one_stderr_line(
        monkeypatch, capsys):
    monkeypatch.setenv(arms.ARM_MODE_ENV_PREFIX + "PR_STATE", "loud")
    spec = arms.load_arm("pr_state")
    assert arms.arm_mode(spec) == "block"
    err = capsys.readouterr().err
    assert "SUPERJEV_ARM_PR_STATE='loud'" in err
    assert err.count("\n") == 1, "exactly one line, so a hook is not spammed"
    # And it stays one line across repeated lookups in the same process.
    arms.arm_mode(spec)
    arms.arm_mode(spec)
    assert capsys.readouterr().err == ""


def test_config_file_sets_the_mode(monkeypatch, tmp_path):
    cfg = tmp_path / "arms.json"
    cfg.write_text(json.dumps({"arms": {"pr_state": "advisory"}}) + "\n")
    monkeypatch.setenv(arms.ARMS_CONFIG_ENV, str(cfg))
    assert arms.arm_mode(arms.load_arm("pr_state")) == "advisory"


def test_config_file_accepts_a_bare_map(monkeypatch, tmp_path):
    cfg = tmp_path / "arms.json"
    cfg.write_text(json.dumps({"pr_state": "off"}) + "\n")
    monkeypatch.setenv(arms.ARMS_CONFIG_ENV, str(cfg))
    assert arms.arm_mode(arms.load_arm("pr_state")) == "off"


def test_env_wins_over_the_config_file(monkeypatch, tmp_path):
    cfg = tmp_path / "arms.json"
    cfg.write_text(json.dumps({"arms": {"pr_state": "off"}}) + "\n")
    monkeypatch.setenv(arms.ARMS_CONFIG_ENV, str(cfg))
    monkeypatch.setenv(arms.ARM_MODE_ENV_PREFIX + "PR_STATE", "advisory")
    assert arms.arm_mode(arms.load_arm("pr_state")) == "advisory"


def test_unreadable_config_is_one_warning_not_an_exception(
        monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(arms.ARMS_CONFIG_ENV, str(tmp_path / "nope.json"))
    assert arms.arm_mode(arms.load_arm("pr_state")) == "block"
    assert "per-arm config ignored" in capsys.readouterr().err


def test_malformed_config_is_one_warning_not_an_exception(
        monkeypatch, tmp_path, capsys):
    cfg = tmp_path / "arms.json"
    cfg.write_text("{not json")
    monkeypatch.setenv(arms.ARMS_CONFIG_ENV, str(cfg))
    assert arms.arm_mode(arms.load_arm("pr_state")) == "block"
    assert "per-arm config ignored" in capsys.readouterr().err


@pytest.mark.parametrize("value,on", [
    (None, False), ("", False), ("0", False), ("no", False),
    ("1", True), ("true", True), ("YES", True), ("on", True),
])
def test_arms_switch(monkeypatch, value, on):
    if value is None:
        monkeypatch.delenv(arms.ARMS_SWITCH_ENV, raising=False)
    else:
        monkeypatch.setenv(arms.ARMS_SWITCH_ENV, value)
    assert arms.arms_enabled() is on


# --------------------------------------------------- one arm round-trip

MERGED_DRAFT = "Done. PR #58 merged and CI is green."

OPEN_WINDOW = (
    "=== CURRENT TURN — TOOL RESULTS ===\n\n"
    "$ gh pr view 58 --json state,number\n"
    '{"number":58,"state":"OPEN"}\n'
)


def test_pr_state_arm_blocks_a_merge_claim_the_window_contradicts():
    window = wm.from_text(OPEN_WINDOW)
    verdicts, ran = arms.run_arms(window, MERGED_DRAFT, names=["pr_state"])
    assert ran == [("pr_state", "block")]
    assert len(verdicts) == 1
    v = verdicts[0]
    assert v.arm == "pr_state"
    assert v.is_block()
    assert "PR mismatch" in v.reason and "#58" in v.reason


def test_pr_state_arm_says_nothing_when_the_window_agrees():
    window = wm.from_text(OPEN_WINDOW.replace("OPEN", "MERGED"))
    verdicts, _ran = arms.run_arms(window, MERGED_DRAFT, names=["pr_state"])
    assert verdicts == []


def test_pr_state_arm_says_nothing_about_a_pr_the_draft_never_names():
    window = wm.from_text(OPEN_WINDOW)
    verdicts, _ran = arms.run_arms(window, "Done. Tests pass.",
                                   names=["pr_state"])
    assert verdicts == []


def test_advisory_mode_downgrades_the_same_verdict(monkeypatch):
    monkeypatch.setenv(arms.ARM_MODE_ENV_PREFIX + "PR_STATE", "advisory")
    window = wm.from_text(OPEN_WINDOW)
    verdicts, ran = arms.run_arms(window, MERGED_DRAFT, names=["pr_state"])
    assert ran == [("pr_state", "advisory")]
    assert [v.decision for v in verdicts] == ["advisory"]
    assert not verdicts[0].is_block()
    assert arms.block_reasons(window, MERGED_DRAFT, names=["pr_state"]) == []


def test_off_mode_does_not_run_the_arm(monkeypatch):
    monkeypatch.setenv(arms.ARM_MODE_ENV_PREFIX + "PR_STATE", "off")
    window = wm.from_text(OPEN_WINDOW)
    verdicts, ran = arms.run_arms(window, MERGED_DRAFT, names=["pr_state"])
    assert (verdicts, ran) == ([], [])


def test_block_reasons_matches_the_windows_own_verdict_string():
    window = wm.from_text(OPEN_WINDOW)
    expected, _note = wm.pr_state_verdict_from_window(window, MERGED_DRAFT)
    assert arms.block_reasons(window, MERGED_DRAFT, names=["pr_state"]) == [
        expected]


def test_a_raising_arm_is_skipped_not_fatal(temp_arm, capsys):
    temp_arm("zz_test_boom",
             "NAME = 'zz_test_boom'\nKIND = 'deterministic'\n"
             "DEFAULT_MODE = 'block'\n"
             "def check(window, draft, ctx):\n"
             "    raise RuntimeError('boom')\n")
    verdicts, ran = arms.run_arms(None, "anything", names=["zz_test_boom"])
    assert verdicts == []
    assert ran == [("zz_test_boom", "block")]
    assert "raised" in capsys.readouterr().err


def test_a_non_verdict_return_is_ignored(temp_arm, capsys):
    temp_arm("zz_test_wrongtype",
             "NAME = 'zz_test_wrongtype'\nKIND = 'deterministic'\n"
             "DEFAULT_MODE = 'block'\n"
             "def check(window, draft, ctx):\n"
             "    return 'just a string'\n")
    verdicts, _ran = arms.run_arms(None, "x", names=["zz_test_wrongtype"])
    assert verdicts == []
    assert "not a Verdict" in capsys.readouterr().err


def test_an_arm_sees_the_ctx_it_was_handed(temp_arm):
    temp_arm("zz_test_ctx",
             "from . import Verdict\n"
             "NAME = 'zz_test_ctx'\nKIND = 'deterministic'\n"
             "DEFAULT_MODE = 'block'\n"
             "def check(window, draft, ctx):\n"
             "    return Verdict(arm=NAME, decision='block',\n"
             "                   reason='saw ' + str(ctx.get('caller')))\n")
    verdicts, _ran = arms.run_arms(None, "x", ctx={"caller": "test"},
                                   names=["zz_test_ctx"])
    assert [v.reason for v in verdicts] == ["saw test"]


# ----------------------------------------------- the gate-side switch

def test_gate_uses_the_legacy_arm_with_the_switch_off(monkeypatch):
    sj = _superjev()
    monkeypatch.delenv(arms.ARMS_SWITCH_ENV, raising=False)
    legacy = sj._pr_mismatch_reason(MERGED_DRAFT, OPEN_WINDOW)
    assert legacy and "PR mismatch" in legacy
    assert sj._pr_state_reason(MERGED_DRAFT, OPEN_WINDOW) == legacy


def test_gate_and_registry_agree_with_the_switch_on(monkeypatch):
    sj = _superjev()
    monkeypatch.setenv(arms.ARMS_SWITCH_ENV, "1")
    legacy = sj._pr_mismatch_reason(MERGED_DRAFT, OPEN_WINDOW)
    assert sj._pr_state_reason(MERGED_DRAFT, OPEN_WINDOW) == legacy


def test_switch_on_and_off_give_the_same_deterministic_reasons(monkeypatch):
    sj = _superjev()
    draft = "Done. PR #58 merged. 12 tests pass."
    for value in (None, "1"):
        if value is None:
            monkeypatch.delenv(arms.ARMS_SWITCH_ENV, raising=False)
        else:
            monkeypatch.setenv(arms.ARMS_SWITCH_ENV, value)
        got = sj.deterministic_block_reasons(draft, OPEN_WINDOW)
        assert any("PR mismatch" in r for r in got), value
        if value is None:
            first = got
    assert sj.deterministic_block_reasons(draft, OPEN_WINDOW) == first


def _superjev():
    import importlib.util
    if "superjev" in sys.modules:
        return sys.modules["superjev"]
    spec = importlib.util.spec_from_file_location("superjev",
                                                  SKILL / "superjev.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["superjev"] = mod
    spec.loader.exec_module(mod)
    return mod


ARM_BODY = """\
from . import Verdict

NAME = "probe"
KIND = "deterministic"
DEFAULT_MODE = "{mode}"


def check(window, draft, ctx):
    return None
"""
