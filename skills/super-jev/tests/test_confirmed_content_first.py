"""Offline source retrieval controls; provider decisions are mocked."""
import importlib.util
import json
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ask_confirmed_content_first", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def _top_paths(sdir, question):
    lines = (sdir / "lookups.jsonl").read_text().splitlines()
    for line in reversed(lines):
        rec = json.loads(line)
        if rec.get("kind") == "lookup" and rec.get("question") == question :
            return [t["path"] for t in rec.get("top", [])]
    raise AssertionError("no lookup log entry found")


def _run(tmp_path, question, files, candidates, scores):
    monkeypatch = pytest.MonkeyPatch()
    for p, text in files.items():
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        Path(p).write_text(text)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    # The TOC search now picks the read list (routing asks Jev nothing): stub it with the candidates
    # in the order the old routing ranked them, so what is tested is how confirmed files are ranked.
    listed = [c["originalPath"] for c in sorted(candidates, key=lambda c: -c["score"])]
    monkeypatch.setattr(ask, "candidate_files", lambda *a, **k: [
        ("p1", p_, {"sha256": ask.sha256_file(Path(p_))}) for p_ in files])
    monkeypatch.setattr(ask.toc_search, "run", lambda *a, **k: (listed, [], {}))
    monkeypatch.setattr(ask, "memory", lambda r: {"status": "miss"} if r["action"] == "cached" else
                        {"pointers": ["p1"]} if r["action"] == "panel" else
                        {"status": "candidates", "candidates": candidates})
    monkeypatch.setattr(ask, "confirm", lambda q, ps: (dict(scores), set(), None, {}))
    sdir = tmp_path / "s"
    sdir.mkdir(parents=True, exist_ok=True)
    ask.lookup(question, "me", sdir)
    monkeypatch.undo()
    return _top_paths(sdir, question)


def test_confirmed_content_score_beats_higher_routed_confirmed_file(tmp_path):
    high_content = str(tmp_path / "crypto_market.md")
    high_route = str(tmp_path / "other_market.md")
    top = _run(
        tmp_path, "what is the crypto market doing",
        {high_content: "crypto market notes", high_route: "other market notes"},
        [{"score": 0.5, "originalPath": high_content}, {"score": 0.99, "originalPath": high_route}],
        {high_content: 0.94, high_route: 0.93},
    )
    assert top.index(high_content) < top.index(high_route)


def test_confirmed_content_score_beats_higher_routed_confirmed_file_wide_gap(tmp_path):
    higher = str(tmp_path / "credit_bureau.md")
    lower = str(tmp_path / "other_bureau.md")
    top = _run(
        tmp_path, "what does the credit bureau report say",
        {higher: "credit bureau report", lower: "other bureau report"},
        [{"score": 0.4, "originalPath": higher}, {"score": 0.98, "originalPath": lower}],
        {higher: 0.96, lower: 0.89},
    )
    assert top.index(higher) < top.index(lower)


def test_confirmed_near_tie_broken_by_read_list_order(tmp_path):
    a = str(tmp_path / "a.md")
    b = str(tmp_path / "b.md")
    top = _run(
        tmp_path, "what is the status",
        {a: "a", b: "b"},
        [{"score": 0.2, "originalPath": a}, {"score": 0.9, "originalPath": b}],
        {a: 0.85, b: 0.85},
    )
    assert top.index(b) < top.index(a)


def test_hub_name_does_not_reverse_content_order(tmp_path):
    note = str(tmp_path / "smart_glasses_voice.md")
    index = str(tmp_path / "knowledge" / "INDEX.md")
    top = _run(
        tmp_path, "what do we know about smart glasses voice control",
        {note: "smart glasses voice", index: "index of knowledge files"},
        [{"score": 0.9, "originalPath": note}, {"score": 0.95, "originalPath": index}],
        {note: 0.85, index: 0.97},
    )
    assert top.index(index) < top.index(note)


def test_template_name_does_not_reverse_content_order(tmp_path):
    ledger = str(tmp_path / "clov_ledger.md")
    template = str(tmp_path / "positions_watch_template.md")
    top = _run(
        tmp_path, "what is the clov ledger position",
        {ledger: "clov ledger positions", template: "positions watch template"},
        [{"score": 0.9, "originalPath": ledger}, {"score": 0.95, "originalPath": template}],
        {ledger: 0.85, template: 0.97},
    )
    assert top.index(template) < top.index(ledger)


@pytest.mark.parametrize("name", ["_TEMPLATE-report.md", "campaign-template.md", "template.md"])
def test_is_hub_file_recognizes_template_names(tmp_path, name):
    assert ask.is_hub_file(str(tmp_path / name)) is True


def test_is_hub_file_still_recognizes_original_stems(tmp_path):
    for name in ["INDEX.md", "CATALOG.md", "tools-used.md", "handoff.md", "README.md"]:
        assert ask.is_hub_file(str(tmp_path / name)) is True


def test_is_hub_file_false_for_a_real_note(tmp_path):
    assert ask.is_hub_file(str(tmp_path / "watchlist.md")) is False


def test_confirmed_readme_dashboard_outranks_lower_note(tmp_path):
    readme = str(tmp_path / "clov" / "README.md")
    note = str(tmp_path / "read-campaign-numbers-live-not-memory.md")
    top = _run(
        tmp_path, "what's our all-in breakeven on the CLOV wheel right now",
        {readme: "all-in breakeven $3.94 / share", note: "read campaign numbers live"},
        [{"score": 0.5, "originalPath": readme}, {"score": 0.9, "originalPath": note}],
        {readme: 0.93, note: 0.91},
    )
    assert top[0] == readme


def test_rejected_sources_are_not_ranked(tmp_path):
    readme = str(tmp_path / "clov" / "README.md")
    note = str(tmp_path / "clov-plan.md")
    top = _run(
        tmp_path, "what is the clov plan",
        {readme: "clov", note: "clov plan"},
        [{"score": 0.9, "originalPath": readme}, {"score": 0.5, "originalPath": note}],
        {},
    )
    assert top == []
