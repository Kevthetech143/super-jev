"""Per-principal harness-only switch: settings.json {"harness_only": true} makes judges.ask refuse before any network call."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import context_setting  # noqa: E402
import judges  # noqa: E402


def test_setting_read_per_state_dir(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    (a / "settings.json").write_text(json.dumps({"harness_only": True, "context": "small"}))
    (b / "settings.json").write_text(json.dumps({"harness_only": "yes"}))
    assert context_setting.harness_only(a) is True
    assert context_setting.harness_only(b) is False          # only a real true counts
    assert context_setting.harness_only(tmp_path / "none") is False
    (b / "settings.json").write_text("not json")
    assert context_setting.harness_only(b) is False


def test_judge_door_refuses_without_network(monkeypatch):
    monkeypatch.setenv("SUPERJEV_HARNESS_ONLY", "1")
    jc = judges._impl()
    monkeypatch.setattr(jc, "transport", lambda *a, **k: pytest.fail("network call made"))
    with pytest.raises(judges.Unreachable):
        judges.ask("state", {"q": {"type": "choice"}})


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("SUPERJEV_HARNESS_ONLY", raising=False)
    jc = judges._impl()
    called = []
    def fake(*a, **k):
        called.append(1); raise OSError("stop")
    monkeypatch.setattr(jc, "transport", fake)
    monkeypatch.setenv(judges.key_env(), "k")
    with pytest.raises(judges.JudgeError):
        judges.ask("state", {"q": {"type": "choice", "instructions": "x", "criteria": {}}})
    assert called
