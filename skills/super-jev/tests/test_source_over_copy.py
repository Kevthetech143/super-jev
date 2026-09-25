#!/usr/bin/env python3
"""Guards for the 2026-09-25 full-eval misses: a summary/copy (hub README/PROFILE,
_staging copy, PR-history write-up) must not outrank the real note, and a question
about one person must not read or confirm another person's records.

    python3 -m pytest skills/super-jev/tests/test_source_over_copy.py -q
"""
import importlib.util
import json
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ask_source_over_copy", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def _run(tmp_path, question, files, candidates, scores, caches=None):
    """Run lookup with fake routing/content check. Returns (top rows, checked paths, navigated pointers)."""
    mp = pytest.MonkeyPatch()
    for p, text in files.items():
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        Path(p).write_text(text)
    caches = caches or {"p1": {}}
    navigated, checked = [], []
    def memory(r):
        if r["action"] == "cached":
            return {"status": "miss"}
        if r["action"] == "panel":
            return {"pointers": list(caches)}
        navigated.append(r["pointer"])
        rows = [c for c in candidates if c["originalPath"] in caches[r["pointer"]] or r["pointer"] == "p1"]
        return {"status": "candidates", "candidates": rows}
    def confirm(q, ps):
        checked.extend(ps)
        return ({p: s for p, s in scores.items() if p in ps}, set(), None, {})
    mp.setattr(ask, "load_cache_files", lambda ptr: caches.get(ptr, {}))
    mp.setattr(ask, "word_search", lambda *a, **k: [])
    mp.setattr(ask, "memory", memory)
    mp.setattr(ask, "confirm", confirm)
    mp.setattr(ask, "judge_near_twin", lambda *a: None)
    sdir = tmp_path / "s"
    sdir.mkdir(parents=True, exist_ok=True)
    ask.lookup(question, "me", sdir)
    mp.undo()
    rec = [json.loads(l) for l in (sdir / "lookups.jsonl").read_text().splitlines()][-1]
    return rec["top"], checked, navigated


def test_folder_readme_yields_to_the_note_beside_it(tmp_path):
    """q49: pending/README 0.99 (a one-line index) beat the stamps.com note 0.70."""
    readme, note = str(tmp_path / "pending/README.md"), str(tmp_path / "pending/stamps-com-auto-charges.md")
    top, _, _ = _run(tmp_path, "why is stamps.com charging me automatically?",
                     {readme: "- stamps-com-auto-charges.md", note: "auto charges"},
                     [{"score": 0.9, "originalPath": readme}, {"score": 0.8, "originalPath": note}],
                     {readme: 0.99, note: 0.70})
    assert [t["path"] for t in top] == [note, readme]
    assert top[1]["possible"]


def test_profile_yields_to_the_dedicated_note(tmp_path):
    """q24: a person's PROFILE summary 0.98 beat the travel-coverage note 0.93."""
    prof = str(tmp_path / "documents/esteban/medical/PROFILE.md")
    note = str(tmp_path / "documents/esteban/medical/insurance/healthfirst-travel-coverage.md")
    top, _, _ = _run(tmp_path, "does the Healthfirst plan cover travel abroad?",
                     {prof: "summary", note: "travel coverage"},
                     [{"score": 0.9, "originalPath": prof}, {"score": 0.8, "originalPath": note}],
                     {prof: 0.98, note: 0.93})
    assert top[0]["path"] == note


def test_dashboard_readme_keeps_top_over_an_unrelated_note(tmp_path):
    """q30: campaigns/clov/README.md IS the answer; a note elsewhere must not push it down."""
    readme = str(tmp_path / "investing/campaigns/clov/README.md")
    other = str(tmp_path / "notebook/read-campaign-numbers-live-not-memory.md")
    top, _, _ = _run(tmp_path, "what's our all-in breakeven on the CLOV wheel",
                     {readme: "breakeven 3.94", other: "read live numbers"},
                     [{"score": 0.9, "originalPath": readme}, {"score": 0.8, "originalPath": other}],
                     {readme: 0.92, other: 0.92})
    assert top[0]["path"] == readme and not top[0]["possible"]


def test_staging_copy_yields_to_the_real_note(tmp_path):
    """q10: a _staging/ split copy 0.92 would beat the bp-wearables note 0.84."""
    copy = str(tmp_path / "brain/_staging/events-split/events-part1.md")
    note = str(tmp_path / "brain/practice/bp-wearables-2026.md")
    top, _, _ = _run(tmp_path, "which blood pressure watch can I trust?",
                     {copy: "watch", note: "watch"},
                     [{"score": 0.9, "originalPath": copy}, {"score": 0.8, "originalPath": note}],
                     {copy: 0.92, note: 0.84})
    assert [t["path"] for t in top] == [note, copy]


def test_pr_writeup_yields_only_to_a_confirmed_doc(tmp_path):
    """q59: the pr-19 survey 0.93 beat docs/wire-into-claude-code.md 0.86 (both confirmed);
    q56: a pr-history note stays #1 over a merely possible doc."""
    pr = str(tmp_path / "brain/superjev-pr-history/pr-19-hooks.md")
    doc = str(tmp_path / "docs/wire-into-claude-code.md")
    q = "how do I wire super jev into claude code hooks?"
    cands = [{"score": 0.9, "originalPath": pr}, {"score": 0.8, "originalPath": doc}]
    top, _, _ = _run(tmp_path, q, {pr: "hooks", doc: "hooks"}, cands, {pr: 0.93, doc: 0.86})
    assert top[0]["path"] == doc
    top, _, _ = _run(tmp_path, q, {pr: "hooks", doc: "hooks"}, cands, {pr: 0.88, doc: 0.61})
    assert top[0]["path"] == pr


def _family(tmp_path):
    d = tmp_path / "documents"
    files = {str(d / "kelvin/medical/PROFILE.md"): "# Kelvin\n- Relation: self\n",
             str(d / "esteban/medical/PROFILE.md"): "# Esteban\n- Relation: father\n",
             str(d / "iris/medical/PROFILE.md"): "# Iris\n- Relation: mother\n",
             str(d / "kelvin/medical/medications/current.md"): "meds",
             str(d / "esteban/medical/medications/current.md"): "meds"}
    caches = {"p1": {}, **{f"{n}-medical": {p: {} for p in files if f"/{n}/" in p} for n in ("kelvin", "esteban", "iris")}}
    return files, caches


def test_my_dad_never_confirms_my_own_file(tmp_path):
    """q16: 'my dad' ranked Kelvin's medications (0.80) over the father's (0.60)."""
    files, caches = _family(tmp_path)
    mine = str(tmp_path / "documents/kelvin/medical/medications/current.md")
    dads = str(tmp_path / "documents/esteban/medical/medications/current.md")
    top, checked, navigated = _run(tmp_path, "how many prescriptions is my dad on?", files,
                                   [{"score": 0.9, "originalPath": mine}, {"score": 0.8, "originalPath": dads}],
                                   {mine: 0.80, dads: 0.60}, caches)
    assert [t["path"] for t in top] == [dads]
    assert mine not in checked
    assert "kelvin-medical" not in navigated and "iris-medical" not in navigated


def test_no_person_named_filters_nothing(tmp_path):
    files, caches = _family(tmp_path)
    mine = str(tmp_path / "documents/kelvin/medical/medications/current.md")
    dads = str(tmp_path / "documents/esteban/medical/medications/current.md")
    top, _, navigated = _run(tmp_path, "list the medications on file", files,
                             [{"score": 0.9, "originalPath": mine}, {"score": 0.8, "originalPath": dads}],
                             {mine: 0.90, dads: 0.88}, caches)
    assert {t["path"] for t in top} == {mine, dads}
    assert set(navigated) == set(caches)


def test_question_people():
    folks = {"kelvin": {"self"}, "esteban": {"father"}, "iris": {"mother"}, "milbeny": {"wife"}}
    assert ask.question_people("what's still open for my mom?", folks) == {"iris"}
    assert ask.question_people("what did my dad's DEXA scan show?", folks) == {"esteban"}
    assert ask.question_people("who is milbeny's neurologist?", folks) == {"milbeny"}
    assert ask.question_people("is LASIK a good idea for me?", folks) == {"kelvin"}
    assert ask.question_people("what is on the pending list", folks) == set()
