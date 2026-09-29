"""Source retrieval contract: discovery proposes; content admits; callers reason.
Provider judgments are mocked here. These tests verify pipeline decisions,
not semantic model accuracy (which requires a separately frozen live run).
"""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("ask_source_contract", Path(__file__).resolve().parent.parent / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def lookup(tmp_path, monkeypatch, question, files, scores, notes=None, cover=None, route_scores=None):
    paths = []
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        paths.append(str(p))
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})
    monkeypatch.setattr(ask, "memory", lambda r:
        {"status": "miss"} if r["action"] == "cached" else
        {"pointers": ["p1"]} if r["action"] == "panel" else
        {"status": "candidates", "candidates": [
            {"score": (route_scores or {}).get(Path(p).name, .99 - i * .01), "originalPath": p} for i, p in enumerate(paths)]})
    monkeypatch.setattr(ask, "word_search", lambda *args, **kwargs: [])
    monkeypatch.setattr(ask, "confirm", lambda q, ps: (
        {str(tmp_path / n): s for n, s in scores.items()}, set(), None,
        {str(tmp_path / n): v for n, v in (notes or {}).items()}))
    def no_answer_picker(*args):
        raise AssertionError("ordinary retrieval must not choose one complete answer")
    monkeypatch.setattr(ask, "judge_listwise", no_answer_picker)
    monkeypatch.setattr(ask, "apply_near_twin_tiebreak", no_answer_picker)
    sdir = tmp_path / "state"
    ask.lookup(question, "me", sdir)
    records = [json.loads(l) for l in (sdir / "lookups.jsonl").read_text().splitlines()]
    return next(r.get("top", []) for r in reversed(records) if r.get("kind") == "lookup")


@pytest.mark.parametrize("question", ["How much did it cost?", "Where is it stored?", "What is our opinion?", "How many are ready?", "What is the balance right now?"])
@pytest.mark.parametrize("score,accepted", [(0, False), (.699, False), (.700, True), (.84, True), (.96, True)])
def test_one_source_floor_across_query_shapes(tmp_path, monkeypatch, question, score, accepted):
    top = lookup(tmp_path, monkeypatch, question, {"note.md": "reviewed source"}, {"note.md": score})
    assert bool(top) is accepted


def test_two_components_survive_without_a_single_complete_answer(tmp_path, monkeypatch):
    top = lookup(tmp_path, monkeypatch, "Purchase cost including delivery?",
                 {"purchase.md": "Equipment cost 300.", "freight.md": "Delivery cost 20."},
                 {"purchase.md": .91, "freight.md": .93})
    assert {Path(r["path"]).name for r in top} == {"purchase.md", "freight.md"}
    assert all(not r["possible"] for r in top)


def test_paraphrase_survives_zero_lexical_overlap(tmp_path, monkeypatch):
    top = lookup(tmp_path, monkeypatch, "Why did the technician replace the light?",
                 {"note.md": "The electrician swapped the lamp because it flickered."},
                 {"note.md": .99}, cover={"note.md": 0})
    assert [Path(r["path"]).name for r in top] == ["note.md"]


def test_file_name_does_not_override_direct_evidence(tmp_path, monkeypatch):
    top = lookup(tmp_path, monkeypatch, "Where are the backups stored?",
                 {"backup.md": "See another note for storage.", "README.md": "Backups are on disk B."},
                 {"README.md": .96})
    assert [Path(r["path"]).name for r in top] == ["README.md"]


def test_unfinished_check_is_explicit_and_below_completed_evidence(tmp_path, monkeypatch):
    top = lookup(tmp_path, monkeypatch, "Where are the backups stored?",
                 {"unread.md": "not checked", "read.md": "disk B"}, {"read.md": .9},
                 notes={"unread.md": ask.INCONCLUSIVE})
    assert [Path(r["path"]).name for r in top] == ["read.md", "unread.md"]
    assert top[0]["possible"] is False and top[1]["possible"] is True


def test_secret_cannot_be_rescued_by_route_or_mocked_content_score(tmp_path, monkeypatch):
    top = lookup(tmp_path, monkeypatch, "Find the record", {"held.md": "held"},
                 {"held.md": .99}, notes={"held.md": ask.HELD_SECRET})
    assert top == []


@pytest.mark.parametrize("invalid", [float('nan'), float('inf'), 1.1, True, "0.99", None])
def test_invalid_content_score_is_not_evidence(tmp_path, monkeypatch, invalid):
    top = lookup(tmp_path, monkeypatch, "Find the record", {"note.md": "text"}, {"note.md": invalid})
    assert top == []


@pytest.mark.parametrize("flag", ["0", "1"])
def test_claim_verdict_judge_remains_mandatory(tmp_path, monkeypatch, capsys, flag):
    source = tmp_path / "limit.md"
    source.write_text("# Limit\nThe limit is 255 options.\n")
    monkeypatch.setenv("SUPERJEV_LISTWISE", flag)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})
    monkeypatch.setattr(ask, "word_search", lambda *args, **kwargs: [])
    monkeypatch.setattr(ask, "memory", lambda r:
        {"status": "miss"} if r["action"] == "cached" else
        {"pointers": ["p"]} if r["action"] == "panel" else
        {"status": "candidates", "candidates": [{"score": .95, "originalPath": str(source)}]})
    monkeypatch.setattr(ask, "confirm", lambda *args: ({str(source): .95}, set(), None, {}))
    monkeypatch.setitem(ask._CLAIM, "text", "The limit is 255 options.")
    calls = []
    def judge(*args):
        calls.append(args)
        ask._STAGE["claim_read"] = [str(source)]
        ask._STAGE["claim_files"] = {str(source): {
            "verdict": "supported", "prob": .99, "line": "The limit is 255 options.", "line_no": 2}}
        return str(source), .99
    monkeypatch.setattr(ask, "judge_listwise", judge)
    ask.lookup("The limit is 255 options.", "me", tmp_path / "state")
    assert len(calls) == 1
    assert capsys.readouterr().out.splitlines()[0].startswith("TRUE")


def test_relative_routing_mass_does_not_drop_a_required_source(tmp_path, monkeypatch):
    files = {f"part{i}.md": f"Required item {i}." for i in range(4)}
    top = lookup(tmp_path, monkeypatch, "Which items are required?", files,
                 {name: .9 for name in files}, route_scores={name: .01 for name in files})
    assert {Path(r["path"]).name for r in top} == set(files)


@pytest.mark.parametrize("invalid", [-.01, float("nan"), float("inf"), True, "0.9", None])
def test_invalid_route_is_an_error_not_an_evidence_candidate(tmp_path, monkeypatch, capsys, invalid):
    top = lookup(tmp_path, monkeypatch, "Find the note", {"note.md": "text"},
                 {"note.md": .99}, route_scores={"note.md": invalid})
    assert top == []
    assert "invalid routing candidate" in capsys.readouterr().out


def test_claim_content_floor_is_independent_of_source_policy(tmp_path, monkeypatch):
    path = tmp_path / "note.md"
    path.write_text("# Claim evidence\nA relevant premise.\n")
    monkeypatch.setitem(ask._CLAIM, "text", "A relevant premise.")
    monkeypatch.setattr(ask, "run_navigation", lambda payload: (
        {"status": "candidates", "candidates": [{"score": .65, "sourceId": "0"}]}, None))
    assert ask.confirm_one("A relevant premise.", str(path))[0] == .65
