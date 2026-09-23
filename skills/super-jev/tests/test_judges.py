#!/usr/bin/env python3
"""Offline tests for the judge backend seam (skills/super-jev/judges).

Nothing here makes a live TypeSafe call. The `fake` backend runs
tests/fake_door.py through a real subprocess, which is how the existing
self-mocking tests already prove the door boundary works; the `typesafe`
backend is only ever exercised with `cmd_gate` monkeypatched, so its test
asserts the wiring and never the model.

    python3 -m pytest skills/super-jev/tests/test_judges.py -q
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.append(str(SKILL))

import judges                                            # noqa: E402
import window_model as wm                                # noqa: E402

FAKE_DOOR = Path(__file__).resolve().parent / "fake_door.py"

WINDOW_TEXT = (
    "=== CURRENT TURN — TOOL RESULTS ===\n\n"
    "$ pytest -q\n"
    "12 passed\n"
)


def _sj():
    if "superjev" in sys.modules:
        return sys.modules["superjev"]
    spec = importlib.util.spec_from_file_location("superjev",
                                                  SKILL / "superjev.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["superjev"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(judges.JUDGE_ENV, raising=False)
    monkeypatch.delenv("SUPERJEV_GATE_CMD", raising=False)
    yield


# ------------------------------------------------------------ selection

def test_default_backend_is_typesafe():
    assert isinstance(judges.get_judge(), judges.TypeSafeJudge)
    assert judges.get_judge().name == "typesafe"


def test_env_selects_the_fake_backend(monkeypatch):
    monkeypatch.setenv(judges.JUDGE_ENV, "fake")
    assert isinstance(judges.get_judge(), judges.FakeJudge)


def test_argument_wins_over_env(monkeypatch):
    monkeypatch.setenv(judges.JUDGE_ENV, "fake")
    assert isinstance(judges.get_judge("typesafe"), judges.TypeSafeJudge)


def test_unknown_backend_falls_back_with_one_stderr_line(monkeypatch, capsys):
    monkeypatch.setenv(judges.JUDGE_ENV, "oracle")
    j = judges.get_judge()
    assert isinstance(j, judges.TypeSafeJudge)
    err = capsys.readouterr().err
    assert "SUPERJEV_JUDGE='oracle'" in err
    assert err.count("\n") == 1


def test_every_backend_implements_the_interface():
    for name, cls in judges.BACKENDS.items():
        assert issubclass(cls, judges.Judge), name
        assert cls.classify is not judges.Judge.classify, name


# ---------------------------------------------------------- fake judge

def test_fake_judge_is_unavailable_without_the_env_var():
    j = judges.FakeJudge()
    assert j.available() is False
    r = j.classify("a draft", wm.from_text(WINDOW_TEXT))
    assert r.code == judges.FakeJudge.UNAVAILABLE_CODE
    assert r.backend == "fake"
    assert "SUPERJEV_GATE_CMD" in r.stderr


def test_fake_judge_runs_the_gate_cmd_and_returns_its_exit_code(monkeypatch):
    monkeypatch.setenv("SUPERJEV_GATE_CMD", f"{sys.executable} {FAKE_DOOR}")
    monkeypatch.setenv("FAKE_DOOR_EXIT", "2")
    j = judges.FakeJudge()
    assert j.available() is True
    r = j.classify("Done. 12 tests pass.", wm.from_text(WINDOW_TEXT))
    assert r.code == 2
    assert r.rejected and not r.clean
    assert r.backend == "fake"
    assert "--kit" in r.stdout and "reply" in r.stdout


def test_fake_judge_returns_clean_on_exit_zero(monkeypatch):
    monkeypatch.setenv("SUPERJEV_GATE_CMD", f"{sys.executable} {FAKE_DOOR}")
    monkeypatch.delenv("FAKE_DOOR_EXIT", raising=False)
    r = judges.FakeJudge().classify("Done.", wm.from_text(WINDOW_TEXT))
    assert r.code == 0 and r.clean


def test_fake_judge_survives_an_unrunnable_command(monkeypatch):
    monkeypatch.setenv("SUPERJEV_GATE_CMD",
                       "/nonexistent/definitely-not-a-door")
    r = judges.FakeJudge().classify("Done.", wm.from_text(WINDOW_TEXT))
    assert r.code == judges.FakeJudge.UNAVAILABLE_CODE
    assert r.stderr


def test_fake_judge_leaves_no_temp_files_behind(monkeypatch, tmp_path):
    # tempfile.tempdir, not TMPDIR: gettempdir() caches its answer at first
    # use, so a test that set the env var would pass without proving
    # anything.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("SUPERJEV_GATE_CMD", f"{sys.executable} {FAKE_DOOR}")
    judges.FakeJudge().classify("Done.", wm.from_text(WINDOW_TEXT))
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------ typesafe judge

def test_typesafe_judge_hands_the_window_and_draft_to_cmd_gate(monkeypatch):
    sj = _sj()
    seen = {}

    def fake_cmd_gate(ns):
        seen["evidence"] = [Path(p).read_text(encoding="utf-8")
                            for p in ns.evidence]
        seen["draft"] = Path(ns.draft).read_text(encoding="utf-8")
        seen["hook_mode"] = ns.hook_mode
        seen["timeout"] = ns.timeout
        return 3, "READ", ""

    monkeypatch.setattr(sj, "cmd_gate", fake_cmd_gate)
    window = wm.from_text(WINDOW_TEXT)
    r = judges.TypeSafeJudge(timeout=7).classify("Done. 12 tests pass.", window)
    assert r == judges.JudgeResult(code=3, backend="typesafe", stdout="READ",
                                   stderr="")
    assert seen["draft"] == "Done. 12 tests pass."
    assert seen["evidence"] == [window.render()]
    assert seen["hook_mode"] is True
    assert seen["timeout"] == 7


def test_typesafe_judge_accepts_plain_window_text(monkeypatch):
    sj = _sj()
    captured = {}

    def fake_cmd_gate(ns):
        captured["evidence"] = Path(ns.evidence[0]).read_text(encoding="utf-8")
        return 0, "", ""

    monkeypatch.setattr(sj, "cmd_gate", fake_cmd_gate)
    judges.TypeSafeJudge().classify("Done.", WINDOW_TEXT)
    assert captured["evidence"] == WINDOW_TEXT


def test_typesafe_judge_cleans_up_its_temp_files(monkeypatch, tmp_path):
    sj = _sj()
    monkeypatch.setattr(sj, "cmd_gate", lambda ns: (0, "", ""))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    judges.TypeSafeJudge().classify("Done.", wm.from_text(WINDOW_TEXT))
    assert list(tmp_path.iterdir()) == []


def test_typesafe_availability_tracks_the_door(monkeypatch):
    sj = _sj()
    monkeypatch.setenv("SUPERJEV_GATE_CMD", f"{sys.executable} {FAKE_DOOR}")
    assert judges.TypeSafeJudge().available() is True
    monkeypatch.delenv("SUPERJEV_GATE_CMD", raising=False)
    monkeypatch.setattr(sj, "JEV_LIB", Path("/nonexistent/jev.py"))
    assert judges.TypeSafeJudge().available() is False


def test_judge_result_is_the_same_shape_from_both_backends(monkeypatch):
    sj = _sj()
    monkeypatch.setattr(sj, "cmd_gate", lambda ns: (0, "ok", ""))
    monkeypatch.setenv("SUPERJEV_GATE_CMD", f"{sys.executable} {FAKE_DOOR}")
    window = wm.from_text(WINDOW_TEXT)
    a = judges.TypeSafeJudge().classify("Done.", window)
    b = judges.FakeJudge().classify("Done.", window)
    assert type(a) is type(b) is judges.JudgeResult
    assert (a.code, b.code) == (0, 0)
    assert {a.backend, b.backend} == {"typesafe", "fake"}


def test_base_interface_raises_rather_than_answering_silently():
    with pytest.raises(NotImplementedError):
        judges.Judge().classify("Done.", None)
