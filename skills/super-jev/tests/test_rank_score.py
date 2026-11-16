#!/usr/bin/env python3
"""Ranking: among close content scores a file named for the question's words edges ahead and a
build-cycle run folder's receipt edges behind. No network.

    python3 -m pytest skills/super-jev/tests/test_rank_score.py -q
"""
import importlib.util
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_rank_score", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def test_named_file_edges_past_a_close_unnamed_one():
    q = "where is the together ai api key kept"
    assert ask.rank_score(q, 0.99, "/n/together-ai.md") > ask.rank_score(q, 1.0, "/n/ssh-access.md")


def test_a_far_lower_score_is_not_rescued_by_its_name():
    q = "where is the together ai api key kept"
    assert ask.rank_score(q, 0.80, "/n/together-ai.md") < ask.rank_score(q, 1.0, "/n/ssh-access.md")


def test_run_folder_receipt_edges_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(ask, "is_run_folder", lambda folder: folder.endswith("run"))
    assert ask.rank_score("a note", 1.0, "/x/run/10-learn.md") < ask.rank_score("a note", 0.99, "/x/notes/a.md")
