#!/usr/bin/env python3
"""Ranking: a build-cycle run folder's receipt edges behind a note with a close content score; a name
that shares question words never edges a file ahead. No network.

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


def test_run_folder_receipt_edges_behind(monkeypatch):
    monkeypatch.setattr(ask, "is_run_folder", lambda folder: folder.endswith("run"))
    assert ask.rank_score(1.0, "/x/run/10-learn.md") < ask.rank_score(0.99, "/x/notes/a.md")


def test_a_far_higher_receipt_score_still_wins(monkeypatch):
    monkeypatch.setattr(ask, "is_run_folder", lambda folder: folder.endswith("run"))
    assert ask.rank_score(1.0, "/x/run/10-learn.md") > ask.rank_score(0.90, "/x/notes/a.md")


def test_a_shared_name_word_never_reorders(monkeypatch):
    # "which email address is tied to Amazon Seller Central": the generic faq that shares the word "amazon"
    # keeps its content rank; the right note is not pushed below it by name words (no name bonus)
    monkeypatch.setattr(ask, "is_run_folder", lambda folder: False)
    faq, note = "/r/faq/global-faq-amazon-bm-fb-1.md", "/b/gmail-accounts.md"
    assert ask.rank_score(0.96, note) > ask.rank_score(0.95, faq)
    assert ask.rank_score(0.96, note) == 0.96
