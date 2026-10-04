#!/usr/bin/env python3
"""Source content controls ranking; word overlap and filenames do not override it.
Person-scoped discovery and admission still apply. No live calls.
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
    rec = [r for r in map(json.loads, (sdir / "lookups.jsonl").read_text().splitlines()) if r["kind"] == "lookup"][-1]
    return rec["top"], checked, navigated


def test_folder_readme_uses_content_order(tmp_path):
    readme, note = str(tmp_path / "pending/README.md"), str(tmp_path / "pending/stamps-com-auto-charges.md")
    top, _, _ = _run(tmp_path, "why is stamps.com charging me automatically?",
                     {readme: "- stamps-com-auto-charges.md", note: "auto charges"},
                     [{"score": 0.9, "originalPath": readme}, {"score": 0.8, "originalPath": note}],
                     {readme: 0.99, note: 0.94})
    assert [t["path"] for t in top] == [readme, note]
    assert not any(t["possible"] for t in top)


def test_profile_uses_content_order(tmp_path):
    prof = str(tmp_path / "documents/gustavo/medical/PROFILE.md")
    note = str(tmp_path / "documents/gustavo/medical/insurance/bluecrest-travel-coverage.md")
    top, _, _ = _run(tmp_path, "does the Bluecrest plan cover travel abroad?",
                     {prof: "summary", note: "travel coverage"},
                     [{"score": 0.9, "originalPath": prof}, {"score": 0.8, "originalPath": note}],
                     {prof: 0.98, note: 0.93})
    assert top[0]["path"] == prof


def test_dashboard_readme_keeps_top_over_an_unrelated_note(tmp_path):
    readme = str(tmp_path / "investing/campaigns/clov/README.md")
    other = str(tmp_path / "notebook/read-campaign-numbers-live-not-memory.md")
    top, _, _ = _run(tmp_path, "what's our all-in breakeven on the CLOV wheel",
                     {readme: "breakeven 3.94", other: "read live numbers"},
                     [{"score": 0.9, "originalPath": readme}, {"score": 0.8, "originalPath": other}],
                     {readme: 0.92, other: 0.92})
    assert top[0]["path"] == readme and not top[0]["possible"]


def test_staging_copy_uses_content_order(tmp_path):
    copy = str(tmp_path / "brain/_staging/events-split/events-part1.md")
    note = str(tmp_path / "brain/practice/bp-wearables-2026.md")
    top, _, _ = _run(tmp_path, "which blood pressure watch can I trust?",
                     {copy: "watch", note: "watch"},
                     [{"score": 0.9, "originalPath": copy}, {"score": 0.8, "originalPath": note}],
                     {copy: 0.92, note: 0.84})
    assert [t["path"] for t in top] == [copy, note]


def test_pr_writeup_uses_content_order(tmp_path):
    pr = str(tmp_path / "brain/superjev-pr-history/pr-19-hooks.md")
    doc = str(tmp_path / "docs/wire-into-claude-code.md")
    q = "how do I wire super jev into claude code hooks?"
    cands = [{"score": 0.9, "originalPath": pr}, {"score": 0.8, "originalPath": doc}]
    top, _, _ = _run(tmp_path, q, {pr: "hooks", doc: "hooks"}, cands, {pr: 0.93, doc: 0.86})
    assert top[0]["path"] == pr
    top, _, _ = _run(tmp_path, q, {pr: "hooks", doc: "hooks"}, cands, {pr: 0.88, doc: 0.61})
    assert top[0]["path"] == pr


def _family(tmp_path):
    d = tmp_path / "agents/global/documents"
    files = {str(d / "marvin/medical/PROFILE.md"): "# Marvin\n- Relation: self\n",
             str(d / "gustavo/medical/PROFILE.md"): "# Gustavo\n- Relation: father\n",
             str(d / "nora/medical/PROFILE.md"): "# Nora\n- Relation: mother\n",
             str(d / "marvin/medical/medications/current.md"): "meds",
             str(d / "gustavo/medical/medications/current.md"): "meds"}
    caches = {"p1": {}, **{f"{n}-medical": {p: {} for p in files if f"/{n}/" in p} for n in ("marvin", "gustavo", "nora")}}
    return files, caches


def test_my_dad_never_confirms_my_own_file(tmp_path):
    files, caches = _family(tmp_path)
    mine = str(tmp_path / "agents/global/documents/marvin/medical/medications/current.md")
    dads = str(tmp_path / "agents/global/documents/gustavo/medical/medications/current.md")
    top, checked, navigated = _run(tmp_path, "how many prescriptions is my dad on?", files,
                                   [{"score": 0.9, "originalPath": mine}, {"score": 0.8, "originalPath": dads}],
                                   {mine: 0.90, dads: 0.80}, caches)
    assert [t["path"] for t in top] == [dads]
    assert mine not in checked
    assert "marvin-medical" not in navigated and "nora-medical" not in navigated


def test_no_person_named_filters_nothing(tmp_path):
    files, caches = _family(tmp_path)
    mine = str(tmp_path / "agents/global/documents/marvin/medical/medications/current.md")
    dads = str(tmp_path / "agents/global/documents/gustavo/medical/medications/current.md")
    top, checked, _ = _run(tmp_path, "list the medications on file", files,
                             [{"score": 0.9, "originalPath": mine}, {"score": 0.8, "originalPath": dads}],
                             {mine: 0.90, dads: 0.88}, caches)
    assert {t["path"] for t in top} == {mine, dads}
    assert {mine, dads} <= set(checked)  # no person named: every person's file is read (routing calls are gone)


def test_question_people():
    folks = {"marvin": {"self"}, "gustavo": {"father"}, "nora": {"mother"}, "belinda": {"wife"}}
    assert ask.question_people("what's still open for my mom?", folks) == {"nora"}
    assert ask.question_people("what did my dad's blood test show?", folks) == {"gustavo"}
    assert ask.question_people("who is belinda's dentist?", folks) == {"belinda"}
    assert ask.question_people("is a flu shot a good idea for me?", folks) == {"marvin"}
    assert ask.question_people("what is on the pending list", folks) == set()


def test_readme_keeps_confirm_over_possible_sibling(tmp_path):
    readme = str(tmp_path / "campaigns/clov/README.md")
    ledger = str(tmp_path / "campaigns/clov/ledger.md")
    top, _, _ = _run(tmp_path, "what's our all-in breakeven on the CLOV wheel",
                     {readme: "breakeven 3.94", ledger: "breakeven history"},
                     [{"score": 0.9, "originalPath": readme}, {"score": 0.8, "originalPath": ledger}],
                     {readme: 0.92, ledger: 0.75})
    assert top[0]["path"] == readme and not top[0]["possible"]


def test_group_and_multi_person_questions():
    folks = {"marvin": {"self"}, "gustavo": {"father"}, "nora": {"mother"}, "belinda": {"wife"}, "joanie": {"daughter"}}
    for q in ["what meds are my parents on?", "when are my kids' checkups?", "do my children need shots?",
              "what does my family owe?", "what's our insurance plan?"]:
        assert ask.question_people(q, folks) == set(), q
    assert ask.question_people("when did my wife and I see the doctor?", folks) == {"belinda", "marvin"}
    assert ask.question_people("my dad and my mom's appointments", folks) == {"gustavo", "nora"}
    assert ask.question_people("what did my grandma say?", folks) == set()


def test_person_filter_only_under_global_documents():
    assert ask.person_of("/Users/x/agents/global/documents/nora/medical/a.md") == "nora"
    assert ask.person_of("/Users/x/projects/documents/nora/a.md") is None
