"""Harness-only outcome rule, filler words and plural stemming (made-up notes, no live calls).
No judge runs in this mode, so a missing zoom or content check is expected, not a failure."""
import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ask_harness_outcome", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

GOOD_Q = "where does the pottery club keep its glaze recipes"
FILLER_Q = "what number looks too low and is a known quirk"


def _notes(tmp_path):
    good = tmp_path / "pottery-club.md"
    good.write_text("# Pottery club\nThe pottery club keeps its glaze recipes in the blue binder by the kiln.\n")
    others = []
    for i in range(8):
        f = tmp_path / f"misc{i}.md"
        f.write_text(f"Misc note {i}: the number on the oven looks fine; a known quirk of the washer is noise. "
                     f"Prices were too low last week, see the garden plan.\n")
        others.append(f)
    return good, others


def _lookup(tmp_path, question, files, harness=True, nav=None, monkeypatch=None):
    mp = monkeypatch
    mp.setenv("SUPERJEV_HARNESS_ONLY", "1") if harness else mp.delenv("SUPERJEV_HARNESS_ONLY", raising=False)
    mp.setattr(ask, "load_cache_files", lambda ptr: {} if nav else {
        str(f): {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True} for f in files})
    mp.setattr(ask, "memory", lambda r: {"status": "miss"} if r["action"] == "cached" else
               {"pointers": ["p1"]} if r["action"] == "panel" else
               (nav or {"status": "no-candidates"}))
    mp.setattr(ask, "judge_listwise", lambda q, pool: (None, None))
    sdir = tmp_path / "s"
    sdir.mkdir(exist_ok=True)
    ask._RESULT.clear()
    rc = ask.lookup(question, "me", sdir)
    return rc, dict(ask._RESULT)


def test_files_that_pass_the_gate_read_as_found_not_error(tmp_path, monkeypatch):
    good, others = _notes(tmp_path)
    rc, res = _lookup(tmp_path, GOOD_Q, [good, *others], monkeypatch=monkeypatch)
    assert res["outcome"] == "found" and rc == 0
    assert "word match, no judge check" in res["why"]
    assert res["files"][0]["path"] == str(good)
    assert all(f["tier"] in ("unchecked", "possible") for f in res["files"])   # never confirmed
    assert not res.get("saved_now")


def test_nothing_surviving_the_gate_is_not_found(tmp_path, monkeypatch):
    good, others = _notes(tmp_path)
    rc, res = _lookup(tmp_path, FILLER_Q, [good, *others], monkeypatch=monkeypatch)
    assert res["outcome"] == "not-found" and rc == 1
    assert not res["files"]


def test_a_set_that_truly_failed_keeps_error(tmp_path, monkeypatch):
    good, others = _notes(tmp_path)
    rc, res = _lookup(tmp_path, GOOD_Q, [good], nav={"status": "error", "reason": "disk gone"}, monkeypatch=monkeypatch)
    assert res["outcome"] == "error" and rc == 3


def test_a_set_only_the_judge_could_route_is_not_a_failure(tmp_path, monkeypatch):
    good, _ = _notes(tmp_path)
    rc, res = _lookup(tmp_path, GOOD_Q, [good], nav={"status": "error", "reason": "harness-only: no judge call made"},
                      monkeypatch=monkeypatch)
    assert res["outcome"] == "not-found" and rc == 1
    # that set has no local file list, so word search could not cover it either: never a complete no-match
    assert "partial: 1 set not searched" in res["why"] and "searched 0 sets" in res["why"]


def _second_note(tmp_path):
    f = tmp_path / "pottery-club-glaze-log.md"
    f.write_text("# Glaze log\nPottery club glaze recipes were tested last spring.\n")
    return f


def test_judged_mode_ranking_and_tiers_are_unchanged(tmp_path, monkeypatch):
    # Same expectations hold on v1.0.145: judge stubbed to score two notes, the rest rejected.
    good, others = _notes(tmp_path)
    second = _second_note(tmp_path)
    scores = {str(good): 0.9, str(second): 0.7}
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({p: scores.get(p, 0.1) for p in ps}, set(), None, {}))
    rc, res = _lookup(tmp_path, GOOD_Q, [good, second, *others], harness=False, monkeypatch=monkeypatch)
    assert rc == 0 and res["outcome"] == "found"
    assert [f["path"] for f in res["files"]] == [str(good), str(second)]
    assert [f["tier"] for f in res["files"]] == ["confirmed", "confirmed"]
    assert "no judge check" not in res["why"] and "partial" not in res["why"]


def test_judged_mode_with_the_judge_down_still_errors(tmp_path, monkeypatch):
    good, others = _notes(tmp_path)
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({}, set(), "judge down", {p: ask.INCONCLUSIVE for p in ps}))
    rc, res = _lookup(tmp_path, GOOD_Q, [good, *others], harness=False, monkeypatch=monkeypatch)
    assert res["outcome"] == "error" and rc == 3
    assert "no judge check" not in res["why"] and "harness" not in res["why"]


def test_filler_words_are_not_search_terms_in_harness_mode_only(monkeypatch):
    q = "what number looks too low, a known quirk, see the missing tax form"
    monkeypatch.setitem(ask._STAGE, "harness_only", True)
    assert ask.query_terms(q) == ["tax", "form"]
    monkeypatch.delitem(ask._STAGE, "harness_only")
    assert {"number", "looks", "quirk"} <= set(ask.query_terms(q))


def test_plural_stem_in_harness_mode_only(monkeypatch):
    vocab = ["tax", "form", "policy", "glass", "status"]
    monkeypatch.setitem(ask._STAGE, "harness_only", True)
    v = ask.term_variants(["taxes", "policies", "glass", "status"], vocab)
    assert "tax" in v["taxes"] and "policy" in v["policies"]
    assert "glass" not in (v["glass"] - {"glass"}) and v["status"] == {"status"}
    monkeypatch.delitem(ask._STAGE, "harness_only")
    assert "tax" not in ask.term_variants(["taxes"], vocab)["taxes"]


@pytest.mark.parametrize("word,one", [("taxes", "tax"), ("policies", "policy"), ("boxes", "box"), ("houses", "house"),
                                       ("glass", ""), ("status", ""), ("analysis", ""), ("tax", "")])
def test_singular_of(word, one):
    assert ask.singular_of(word) == one


def test_lead_passage_below_two_thirds_is_held_back_in_harness_mode(tmp_path, monkeypatch):
    # five words, each in two notes; the best note carries three of them (0.6): enough in the judged code-only gate (0.6),
    # not in harness-only mode (two thirds)
    words = ["alpine", "borage", "cobalt", "dahlia", "ember"]
    texts = {"a.md": words[:3], "b.md": words[:3], "c.md": words[3:], "d.md": words[3:]}
    files = []
    for name, ws in texts.items():
        f = tmp_path / name
        f.write_text("# Note\n" + " ".join(ws) + " were written here.\n")
        files.append(f)
    for i in range(8):
        f = tmp_path / f"pad{i}.md"
        f.write_text(f"# Pad {i}\nunrelated text about the garden fence number {i}.\n")
        files.append(f)
    rc, res = _lookup(tmp_path, " ".join(words), files, monkeypatch=monkeypatch)
    assert 0.55 < ask._STAGE["cover_gate"]["pcover"] < 0.65
    assert res["outcome"] == "not-found" and rc == 1
