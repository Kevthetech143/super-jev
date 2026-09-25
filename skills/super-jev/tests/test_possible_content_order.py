#!/usr/bin/env python3
"""Unit tests for the possible-group ordering fix in lookup() (metric_of/better/
sort_metric) and is_hub_file(): the possible group (content 0.60-0.84) sorts by
content score alone, routing only breaks a near-tie, a route-kept file with no
real content score sorts below every file that has one, and an index/catalog/
handoff file is moved into the possible group so a real note outranks it.

    python3 -m pytest skills/super-jev/tests/test_possible_content_order.py -q
"""
import importlib.util
import json
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ask_possible_order", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def _top_paths(sdir, question):
    lines = (sdir / "lookups.jsonl").read_text().splitlines()
    for line in reversed(lines):
        rec = json.loads(line)
        if rec.get("kind") == "lookup" and rec.get("question") == question and rec.get("top"):
            return [t["path"] for t in rec["top"]]
    raise AssertionError("no lookup log entry found")


def _run(tmp_path, question, files, candidates, scores):
    """files: {path: text}. candidates: navigate() candidates (routing scores).
    scores: confirm()'s {path: content score} for files that got a real score."""
    monkeypatch = pytest.MonkeyPatch()
    for p, text in files.items():
        Path(p).write_text(text)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    monkeypatch.setattr(ask, "memory", lambda r: {"status": "miss"} if r["action"] == "cached" else
                        {"pointers": ["p1"]} if r["action"] == "panel" else
                        {"status": "candidates", "candidates": candidates})
    monkeypatch.setattr(ask, "confirm", lambda q, ps: (dict(scores), set(), None, {}))
    sdir = tmp_path / "s"
    sdir.mkdir(parents=True, exist_ok=True)
    ask.lookup(question, "me", sdir)
    monkeypatch.undo()
    return _top_paths(sdir, question)


def test_real_content_score_beats_route_only_file(tmp_path):
    """watchlist.md at 0.72 content beats faq.md, which was route-kept (an opinion
    ask) with no real content score -- the exact regression named in the task."""
    watchlist = str(tmp_path / "watchlist.md")
    faq = str(tmp_path / "faq.md")
    top = _run(
        tmp_path, "should I rebalance",
        {watchlist: "rebalance plan", faq: "rebalance frequently asked questions"},
        [{"score": 0.5, "originalPath": watchlist}, {"score": 0.95, "originalPath": faq}],
        {watchlist: 0.72},  # faq scores below POSSIBLE_FLOOR -> no entry, kept only via ROUTE_KEEP
    )
    assert top.index(watchlist) < top.index(faq)


def test_route_only_file_ranks_below_every_real_content_score(tmp_path):
    a = str(tmp_path / "a.md")
    b = str(tmp_path / "b.md")
    top = _run(
        tmp_path, "should I invest",
        {a: "a", b: "b"},
        [{"score": 0.5, "originalPath": a}, {"score": 0.99, "originalPath": b}],
        {a: 0.61},  # barely above POSSIBLE_FLOOR, but a real score
    )
    assert top.index(a) < top.index(b)


def test_content_score_alone_orders_the_possible_group(tmp_path):
    """overlays/README (0.65) beats campaigns/README (0.62); routing does not
    reverse a real content-score difference that is not a near-tie."""
    higher = str(tmp_path / "overlays_readme.md")
    lower = str(tmp_path / "campaigns_readme.md")
    top = _run(
        tmp_path, "what is our campaign overlay plan",
        {higher: "overlay plan", lower: "campaign plan"},
        [{"score": 0.3, "originalPath": higher}, {"score": 0.9, "originalPath": lower}],
        {higher: 0.65, lower: 0.62},
    )
    assert top.index(higher) < top.index(lower)


def test_near_tie_content_broken_by_routing(tmp_path):
    """Equal (rounded) content scores fall back to the routing score."""
    a = str(tmp_path / "a.md")
    b = str(tmp_path / "b.md")
    top = _run(
        tmp_path, "what is the status",
        {a: "a", b: "b"},
        [{"score": 0.2, "originalPath": a}, {"score": 0.9, "originalPath": b}],
        {a: 0.70, b: 0.70},
    )
    assert top.index(b) < top.index(a)


def test_index_file_moved_into_possible_sorts_below_real_note(tmp_path):
    """smart-glasses-voice (0.70, a real note) beats knowledge/INDEX.md (0.68): the
    index file is moved into the possible group even though its own content score
    would otherwise let it compete on equal footing (it is a table of contents,
    not a topic file, so on a near-tie or lower score it must not win)."""
    note = str(tmp_path / "smart-glasses-voice.md")
    index = str(tmp_path / "knowledge" / "INDEX.md")
    Path(index).parent.mkdir(parents=True, exist_ok=True)
    top = _run(
        tmp_path, "what do we know about smart glasses voice control",
        {note: "smart glasses voice", index: "index of knowledge files"},
        [{"score": 0.9, "originalPath": note}, {"score": 0.95, "originalPath": index}],
        {note: 0.70, index: 0.68},  # both possible-range; note's real content narrowly wins
    )
    assert top.index(note) < top.index(index)


@pytest.mark.parametrize("name", ["INDEX.md", "CATALOG.md", "tools-used.md", "handoff.md", "README.md"])
def test_is_hub_file_names(tmp_path, name):
    assert ask.is_hub_file(str(tmp_path / name)) is True


def test_is_hub_file_false_for_a_real_note(tmp_path):
    assert ask.is_hub_file(str(tmp_path / "watchlist.md")) is False
