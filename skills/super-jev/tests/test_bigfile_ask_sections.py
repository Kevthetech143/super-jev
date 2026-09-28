#!/usr/bin/env python3
"""Offline tests for asking about a file connected in sections (2026-09-28): the content check
reads the routed section's passages first, the result names the section to open, and saving an
answer cites the section's own reviewed lines. No network and no real key.

    python3 -m pytest skills/super-jev/tests/test_bigfile_ask_sections.py -q
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_bigfile_sections", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)
prepare_bulk = ask.prepare_bulk

C = ask.CONFIRM_CHUNK


@pytest.fixture(autouse=True)
def stage():
    ask._STAGE.clear()
    yield
    ask._STAGE.clear()


def test_routed_section_passage_is_read_before_louder_passages_elsewhere():
    loud = ("the refund window policy refund window policy refund\n" * 70)[:C]
    quiet = ("filler line\n" * 150 + "refund window is 30 days\n" + "filler line\n" * 130)[:C]
    chunks = [loud] * 6 + ["other text here\n" * 200] + [quiet]
    q = "what is the refund window"
    assert 7 not in ask.pick_chunks(q, chunks)  # without routing, the loud passages fill the budget
    picked = ask.pick_chunks(q, chunks, prefer={7})
    assert 7 in picked and sum(len(chunks[i]) for i in picked) <= ask.READ_CHARS
    # A routed passage sharing no question word earns no slot of its own.
    assert 6 not in ask.pick_chunks(q, chunks, prefer={6})


def _read_lines(ctx):
    first, spans = 1, []
    for c in ctx["chunks"]:
        spans.append((first, first + c.count("\n") - (1 if c.endswith("\n") else 0)))
        first += c.count("\n")
    return [spans[i] for i in ctx["picked"]]


def test_confirm_start_prefers_the_routed_sections_lines(tmp_path):
    lines = [f"line {i} about the general topic\n" for i in range(1, 2001)]
    for i in range(100, 700):  # louder passages elsewhere fill the read budget without routing
        lines[i] = "cold backups retention period cold backups retention period\n"
    lines[1500] = "the retention period for cold backups is 90 days\n"
    f = tmp_path / "big.md"
    f.write_text("".join(lines))
    q = "what is the retention period for cold backups"
    done, ctx = ask.confirm_start(q, str(f))
    assert not any(a <= 1501 <= b for a, b in _read_lines(ctx))  # unrouted: the loud passages win
    ask._STAGE["section_routes"] = {str(f): [(0.9, (1450, 1560))]}
    done, ctx = ask.confirm_start(q, str(f))
    assert done is None and any(a <= 1501 <= b for a, b in _read_lines(ctx))


def test_routed_lines_stay_aligned_when_passages_cut_long_lines(tmp_path):
    lines = [("y" * 20000 if i % 3 == 0 else f"short line {i}") + "\n" for i in range(1, 201)]
    lines[180] = "the retention period for cold backups is 90 days\n"
    f = tmp_path / "long.md"
    f.write_text("".join(lines))
    ask._STAGE["section_routes"] = {str(f): [(0.9, (181, 181))]}
    done, ctx = ask.confirm_start("what is the retention period for cold backups", str(f))
    assert done is None and any(a <= 181 <= b for a, b in _read_lines(ctx))


def test_result_names_the_section_holding_the_best_passage(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(prepare_bulk, "CACHE_DIR", cache_dir)
    path = "/x/big.py"
    (cache_dir / "code.json").write_text(json.dumps({path: {"pass": True, "sections": [
        {"lines": [1, 400], "pass": True}, {"lines": [401, 900], "pass": True},
        {"lines": [901, 1200], "pass": False}]}}))
    ask._STAGE["checks"] = {path: {"best_line": 650}}
    assert ask.section_shown(path, "code") == (401, 900)
    ask._STAGE["checks"] = {path: {"best_line": 1000}}
    assert ask.section_shown(path, "code") == (901, 1200)  # a section the gate failed is still where to read
    assert ask.section_shown(path, "code-2") == (901, 1200)  # a part pointer reads its base's sections
    ask._STAGE["checks"] = {}
    ask._STAGE["section_routes"] = {path: [(0.4, (1, 400)), (0.8, (401, 900))]}
    assert ask.section_shown(path, "code") == (401, 900)  # no read passage: the best-routed section
    assert ask.section_shown("/x/other.md", "code") is None


def test_edited_sectioned_file_stays_readable_up_to_the_section_limit(monkeypatch):
    monkeypatch.setattr(ask, "refresh_would_admit", lambda path, ptr: True)
    raw = b"x = 1\n" * (prepare_bulk.CEILING_BYTES // 6 + 10)
    entry = {"pass": True, "sha256": "0" * 64, "sections": [{"lines": [1, 2]}]}
    assert ask.edited_readable("/x/big.py", "code", entry, raw, raw.decode())
    assert not ask.edited_readable("/x/big.py", "code", {k: v for k, v in entry.items() if k != "sections"},
                                   raw, raw.decode())


def test_saving_an_answer_cites_the_section_holding_it(tmp_path, monkeypatch):
    f = tmp_path / "big.md"
    body = [f"note {i}\n" for i in range(1, 301)]
    body[209] = "the gate timeout is 45 seconds\n"
    f.write_text("".join(body))
    rows = [{"sourceId": f"s{a}", "originalPath": str(f), "lines": [a, b],
             "contentSHA": hashlib.sha256(prepare_bulk.section_text(f.read_text(), [a, b]).encode()).hexdigest()}
            for a, b in ((1, 100), (101, 200), (201, 300))]
    assist = []

    def memory(req):
        if req["action"] == "sources":
            return {"status": "ok", "sources": rows}
        if req["action"] == "assist":
            assist.append(req)
            return {"status": "ok", "passages": [{"sourceId": "s201", "reviewedText": "the gate timeout is 45 seconds"}]}
        raise AssertionError(req)
    monkeypatch.setattr(ask, "memory", memory)
    row, status = ask.source_row("me", "notes", str(f), "the gate timeout is 45 seconds", "what is the gate timeout")
    assert status == "ok" and row["lines"] == [201, 300]
    assert row["contentSHA"] == ask.live_sha(str(f), row)
    out, why = ask.file_evidence("me", "notes", "what is the gate timeout", "the gate timeout is 45 seconds",
                                 str(f), row["sourceId"], {"attemptId": "a1"}, row["lines"])
    assert why is None
    assert assist[0]["references"] == [{"sourceId": "s201", "startLine": 1, "endLine": 1},
                                       {"sourceId": "s201", "startLine": 10, "endLine": 10}]


def test_best_line_is_the_passage_line_sharing_most_question_words():
    chunks = ["a\nb\n", "intro words\nother\ndef _pinned_package_json_sha256(checkout):\n    pin file digest\n"]
    assert ask.best_line("what format must the pin file digest have", chunks, 1) == 3 + 3
    assert ask.best_line("zzz", chunks, 1) == 3  # no word shared: the passage's first line
