"""Offline source retrieval controls; provider decisions are mocked."""
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
        if rec.get("kind") == "lookup" and rec.get("question") == question:
            return [t["path"] for t in rec.get("top", [])]
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


def test_evidence_is_kept_and_route_only_file_is_dropped(tmp_path):
    watchlist = str(tmp_path / "watchlist.md")
    faq = str(tmp_path / "faq.md")
    top = _run(
        tmp_path, "should I rebalance",
        {watchlist: "rebalance plan", faq: "rebalance frequently asked questions"},
        [{"score": 0.5, "originalPath": watchlist}, {"score": 0.95, "originalPath": faq}],
        {watchlist: 0.92},  # faq scores below SOURCE_FLOOR -> no entry, kept only via ROUTE_KEEP
    )
    assert top == [watchlist]


def test_routing_cannot_rescue_rejected_content(tmp_path):
    a = str(tmp_path / "a.md")
    b = str(tmp_path / "b.md")
    top = _run(
        tmp_path, "should I invest",
        {a: "a", b: "b"},
        [{"score": 0.5, "originalPath": a}, {"score": 0.99, "originalPath": b}],
        {},  # barely above SOURCE_FLOOR, but a real score
    )
    assert top == []


def test_content_score_orders_selected_sources(tmp_path):
    higher = str(tmp_path / "overlays_readme.md")
    lower = str(tmp_path / "campaigns_readme.md")
    top = _run(
        tmp_path, "what is our campaign overlay plan",
        {higher: "overlay plan", lower: "campaign plan"},
        [{"score": 0.3, "originalPath": higher}, {"score": 0.9, "originalPath": lower}],
        {higher: 0.95, lower: 0.92},
    )
    assert top.index(higher) < top.index(lower)


def test_near_tie_content_broken_by_routing(tmp_path):
    a = str(tmp_path / "a.md")
    b = str(tmp_path / "b.md")
    top = _run(
        tmp_path, "what is the status",
        {a: "a", b: "b"},
        [{"score": 0.2, "originalPath": a}, {"score": 0.9, "originalPath": b}],
        {a: 0.90, b: 0.90},
    )
    assert top.index(b) < top.index(a)


def test_index_uses_same_content_order(tmp_path):
    note = str(tmp_path / "smart-glasses-voice.md")
    index = str(tmp_path / "knowledge" / "INDEX.md")
    Path(index).parent.mkdir(parents=True, exist_ok=True)
    top = _run(
        tmp_path, "what do we know about smart glasses voice control",
        {note: "smart glasses voice", index: "index of knowledge files"},
        [{"score": 0.9, "originalPath": note}, {"score": 0.95, "originalPath": index}],
        {note: 0.90, index: 0.88},  # both possible-range; note's real content narrowly wins
    )
    assert top.index(note) < top.index(index)


@pytest.mark.parametrize("name", ["INDEX.md", "CATALOG.md", "tools-used.md", "handoff.md", "README.md"])
def test_is_hub_file_names(tmp_path, name):
    assert ask.is_hub_file(str(tmp_path / name)) is True


def test_is_hub_file_false_for_a_real_note(tmp_path):
    assert ask.is_hub_file(str(tmp_path / "watchlist.md")) is False


def test_readme_uses_same_content_order(tmp_path):
    d = tmp_path / "clov"
    d.mkdir()
    readme, note, route_only, index = (str(d / n) for n in ("README.md", "strategy.md", "positions.md", "INDEX.md"))
    top = _run(
        tmp_path, "should I hold CLOV shares",
        {readme: "shares", note: "shares", route_only: "shares", index: "shares"},
        [{"score": 0.5, "originalPath": readme}, {"score": 0.5, "originalPath": note},
         {"score": 0.95, "originalPath": route_only}, {"score": 0.5, "originalPath": index}],
        {readme: 0.92, note: 0.98, index: 0.95},
    )
    assert top == [note, index, readme]


def test_no_candidates_message_does_not_claim_absence(tmp_path, capsys):
    assert _run(tmp_path, "what is my shoe size", {}, [], {}) == []
    out = capsys.readouterr().out
    assert "OUTCOME: not-found" in out and "may still exist" in out
    assert "not in their files" not in out
