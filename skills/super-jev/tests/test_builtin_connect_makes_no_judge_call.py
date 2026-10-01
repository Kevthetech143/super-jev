#!/usr/bin/env python3
"""The docs say a connect with the built-in writer makes no TypeSafe call and needs no key.
This pins that sentence to the code: every judge entry point raises, and the run still ends 0.

    python3 -m pytest skills/super-jev/tests/test_builtin_connect_makes_no_judge_call.py -q
"""
import importlib.util
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("prepare_bulk_no_judge", SKILL / "prepare_bulk.py")
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)
import connect_checked  # noqa: E402


def test_a_builtin_connect_never_calls_the_judge(tmp_path, monkeypatch):
    def judge_called(*args, **kwargs):
        raise AssertionError("the built-in writer must not call the judge")

    for owner, name in ((pb, "gate"), (pb, "gate_many"), (pb, "gate_pack"), (connect_checked, "gate")):
        monkeypatch.setattr(owner, name, judge_called)
    root = tmp_path / "notes"
    root.mkdir()
    (root / "a.md").write_text("# Travel\n\n## Hotels\nUp to 220 dollars per night.\n")
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--root", str(root), "--pointer", "notes",
                                      "--principal", "me", "--writer", "builtin", "--no-connect"])
    assert pb.main() == 0
