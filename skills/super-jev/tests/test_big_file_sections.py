#!/usr/bin/env python3
"""Offline tests for reading big many-topic files by section (2026-09-27): a long file
is cut at line ends near every CONFIRM_CHUNK (a rule is not split across two), and the passages
read are the ones best matching the question's words by BM25 within the file, up to the
same READ_CHARS budget as before.

No network and no real key.

    python3 -m pytest skills/super-jev/tests/test_big_file_sections.py -q
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_sections", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

C = ask.CONFIRM_CHUNK
RULE = "- A split part now heals by its parent's recipe.\n"


def _changelog(n_sections=12, rule_at=7):
    """A changelog-shaped file: every section says "split part pointer ... parent", only
    one says "heals" and "recipe" (RULE)."""
    out = []
    for i in range(n_sections):
        line = ("- Fixed thing {i}.{k} in the tool.\n" if i == rule_at
                else "- The split part pointer moved to the parent folder {i}.{k}.\n")
        out.append(f"## v1.0.{i}\n" + "".join(line.format(i=i, k=k) for k in range(40))
                   + (RULE if i == rule_at else ""))
    return "".join(out)


def test_split_passages_are_whole_lines_near_the_old_size():
    text = _changelog()
    parts = ask.split_passages(text)
    fixed = [text[i:i + C] for i in range(0, len(text), C)]
    assert "".join(parts) == text
    assert len(parts) == len(fixed)  # same number of passages as fixed cuts: no extra spread
    assert all(C // 2 <= len(p) <= C * 3 // 2 for p in parts[:-1])
    assert all(p.endswith("\n") for p in parts)  # no line cut in two
    assert sum(RULE in p for p in parts) == 1


def test_split_passages_cuts_only_a_line_longer_than_half_a_passage():
    text = "short\n" + "x" * (C * 2 + 10) + "\nend\n"
    parts = ask.split_passages(text)
    assert "".join(parts) == text and all(len(p) <= C * 3 // 2 for p in parts)
    assert ask.split_passages("") == [""]
    assert ask.split_passages("small\n") == ["small\n"]


def test_straddling_rule_now_sits_whole_in_one_passage():
    """The SKILL.md line 50 shape: a long rule line crossing a fixed 3500 cut."""
    rule = " ".join([RULE.strip()] * 12) + "\n"  # one long markdown line
    text = "intro line\n" * (C // 11 - 20) + rule + "tail\n" * 50
    fixed = [text[i:i + C] for i in range(0, len(text), C)]
    assert not any(rule in c for c in fixed)  # the old cut split it
    assert any(rule in p for p in ask.split_passages(text))


def test_pick_reads_the_matching_section_not_the_intro():
    text = _changelog()
    parts = ask.split_passages(text)
    assert sum(map(len, parts)) > ask.READ_CHARS
    q = "a split part pointer heals by its parent's recipe"
    picked = ask.pick_chunks(q, parts)
    assert any(RULE in parts[i] for i in picked)
    assert sum(len(parts[i]) for i in picked) <= ask.READ_CHARS
    assert picked == sorted(picked)


def test_pick_does_not_spend_a_slot_on_an_unmatching_intro():
    text = "# Notes\n" + "general intro words\n" * 150 + _changelog()
    parts = ask.split_passages(text)
    assert "recipe" not in parts[0]
    assert 0 not in ask.pick_chunks("a split part pointer heals by its parent's recipe", parts)


def test_pick_reads_a_file_whole_when_it_fits_the_budget():
    parts = ask.split_passages("## A\n" + "line\n" * 2000)
    assert sum(map(len, parts)) <= ask.READ_CHARS
    assert ask.pick_chunks("anything at all", parts) == list(range(len(parts)))


def test_pick_with_no_matching_words_reads_from_the_top():
    parts = ask.split_passages(_changelog())
    picked = ask.pick_chunks("zebra quokka", parts)
    assert picked == list(range(len(picked))) and picked[0] == 0


def test_confirm_sends_the_rule_section_and_best_passage_shows_it(tmp_path, monkeypatch):
    f = tmp_path / "CHANGELOG.md"
    f.write_text(_changelog())
    parts = ask.split_passages(f.read_text())
    rule_i = next(i for i, p in enumerate(parts) if RULE in p)
    sent = []

    def fake_run(cmd, input, **kw):
        sent.append(json.loads(input))
        return subprocess.CompletedProcess(cmd, 0, json.dumps(
            {"status": "candidates", "candidates": [{"score": 0.93, "sourceId": str(rule_i)}]}), "")
    monkeypatch.setattr(ask.subprocess, "run", fake_run)
    monkeypatch.setattr(ask, "_STAGE", {})
    score, _, err, _ = ask.confirm_one("does a split part heal through its parent's recipe", str(f))
    assert err is None and score == 0.93
    leaves = sent[0]["catalog"]["nodes"][1:]
    assert any(RULE in leaf["description"] for leaf in leaves)
    assert sum(len(leaf["description"]) for leaf in leaves) <= ask.READ_CHARS
    # listwise / claim line pick see the same line-aligned passage
    assert RULE in ask.best_passage(str(f))


def test_big_file_on_topic_records_its_section_heading(tmp_path, monkeypatch):
    f = tmp_path / "CHANGELOG.md"
    f.write_text(_changelog())
    parts = ask.split_passages(f.read_text())
    rule_i = next(i for i, p in enumerate(parts) if RULE in p)
    monkeypatch.setattr(ask.subprocess, "run", lambda cmd, input, **kw: subprocess.CompletedProcess(
        cmd, 0, json.dumps({"status": "candidates", "candidates": [{"score": 0.5, "sourceId": str(rule_i)}]}), ""))
    monkeypatch.setattr(ask, "_STAGE", {})
    ask.confirm_one("does a split part heal through its parent's recipe", str(f))
    detail = ask._STAGE["checks"][str(f)]
    assert detail["best_chunk"] == rule_i
    assert detail["best_line"] >= 1 + sum(p.count("\n") for p in parts[:rule_i])


def test_middle_passage_keeps_the_file_title():
    chunks = ask.split_passages("# Trash schedule for 12 Oak St\n" + "row | Monday\n" * 1500)
    assert len(chunks) > 1
    assert ask.with_subject(chunks, 0) == chunks[0]
    shown = ask.with_subject(chunks, 2)
    assert shown.startswith("# Trash schedule for 12 Oak St\n...\n") and shown.endswith(chunks[2])


def test_title_is_capped_and_blank_start_skipped():
    chunks = ["\n\n" + "T" * 300 + "\nbody\n", "second\n"]
    assert ask.with_subject(chunks, 1) == "T" * ask.SUBJECT_CHARS + "\n...\nsecond\n"


def _claim_line(tmp_path, text, shown, pick, cand):
    a = tmp_path / "big.md"
    a.write_text(text)
    answers = {"verdict_1": {"choice": "supported", "probabilities": {"supported": 0.97}},
               "line_1": {"choice": f"L{pick}", "probabilities": {f"L{pick}": 0.9}}}
    return ask.read_claim_answers(answers, [str(a)], {"file_1": cand}, {str(a): shown})[str(a)]["line_no"]


def test_proof_line_in_a_titled_middle_passage_keeps_its_own_number(tmp_path):
    text = "# Big log\n## Setup\n" + "filler\n" * 10 + "## Setup\nThe limit is 255 options.\n"
    shown = "# Big log" + ask.SUBJECT_SEP + text[text.rindex("## Setup"):]
    assert _claim_line(tmp_path, text, shown, 1, ["## Setup", "The limit is 255 options."]) == 13


def test_title_picked_as_proof_line_maps_to_line_one(tmp_path):
    text = "# Big log title line\n" + "filler\n" * 10 + "## Later\nbody text here\n"
    shown = "# Big log title line" + ask.SUBJECT_SEP + text[text.rindex("## Later"):]
    assert _claim_line(tmp_path, text, shown, 1, ["# Big log title line"]) == 1
