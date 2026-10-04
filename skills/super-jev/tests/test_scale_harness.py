"""Smoke of the synthetic scale harness at a tiny size: the stub judge answers (calls counted), no network
is attempted, and the per-ask timing and file counts come out. Exact path:

    python3 -m pytest -q tests/test_scale_harness.py
"""
import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "scale_harness.py"
spec = importlib.util.spec_from_file_location("scale_harness", SCRIPT)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def test_corpus_has_the_planted_answers_and_the_size_mix(tmp_path):
    n = harness.make_corpus(tmp_path, 160)
    assert n == 160 + len(harness.QUESTIONS)
    names = {p.name for p in tmp_path.rglob("*") if p.is_file()}
    assert all(f"answer-{w}.md" in names for _q, w in harness.QUESTIONS)
    assert {p.suffix for p in tmp_path.rglob("*") if p.is_file()} == {".md", ".py", ".sh", ".json"}


def test_small_run_uses_the_stub_judge_and_no_network():
    if subprocess.run(["node", "--version"], capture_output=True).returncode:
        pytest.skip("node is not installed")
    lines = []
    result = harness.run(60, questions=1, say=lines.append)
    ask = result["asks"][0]
    assert ask["exit"] == 0, ask
    assert result["stub_calls"] >= 1, "the ask never reached the stub judge"
    assert result["network_attempts"] == 0
    assert ask["seconds"] > 0 and ask["opened"] > 0 and ask["hashed"] > 0
    assert any(line.startswith("ask 1:") for line in lines), "no timing line printed"
