#!/usr/bin/env python3
"""Offline tests for live decision traces, outcome linking, the trace report,
and Jev's voice line -- all in ask.py.

No network, no live harness, no live provider: `ask.memory` is monkeypatched
at the module level, `ask.confirm`/`ask.word_search` are monkeypatched to skip
the real content-check subprocess and local file search, and the state
directory is always an isolated tmp_path.

    python3 -m pytest skills/super-jev/tests/test_traces_and_voice.py -q
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "ask.py"

spec = importlib.util.spec_from_file_location("ask", SCRIPT)
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def _no_candidates_memory(req):
    if req["action"] == "cached":
        return {"status": "cache-miss", "checked": []}
    if req["action"] == "panel":
        return {"pointers": ["p1"]}
    if req["action"] == "navigate":
        return {"status": "no-candidates", "candidates": []}
    raise AssertionError(req)


def read_jsonl(path: Path) -> list:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------- voice line

def test_miss_lookup_prints_voice_line_as_the_last_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", _no_candidates_memory)
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    rc = ask.lookup("where is the deed?", "alice", tmp_path)

    assert rc == 0
    out = capsys.readouterr().out.rstrip("\n").splitlines()
    assert out[-1] == ask.VOICE_LINE


def test_only_errors_lookup_prints_voice_line(tmp_path, monkeypatch, capsys):
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": ["p1"]}
        if req["action"] == "navigate":
            return {"status": "preparation-required", "reason": "scope-change"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    rc = ask.lookup("q", "alice", tmp_path)

    assert rc == 1
    out = capsys.readouterr().out.rstrip("\n").splitlines()
    assert out[-1] == ask.VOICE_LINE


def test_nothing_connected_prints_voice_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", lambda req: (
        {"status": "cache-miss", "checked": []} if req["action"] == "cached" else
        {"pointers": []} if req["action"] == "panel" else
        (_ for _ in ()).throw(AssertionError(req))
    ))
    rc = ask.lookup("q", "alice", tmp_path)

    assert rc == 1
    out = capsys.readouterr().out.rstrip("\n").splitlines()
    assert out[-1] == ask.VOICE_LINE


def test_hit_lookup_prints_no_voice_line(tmp_path, monkeypatch, capsys):
    note = tmp_path / "hit.md"
    note.write_text("The car is blue.\n")

    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": ["p1"]}
        if req["action"] == "navigate":
            return {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": str(note)}]}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    monkeypatch.setattr(ask, "confirm", lambda q, paths: ({str(note): 0.95}, set(), None, {}))
    rc = ask.lookup("what color is the car?", "alice", tmp_path)

    assert rc == 0
    out = capsys.readouterr().out
    assert ask.VOICE_LINE not in out


def test_cache_hit_prints_no_voice_line_and_writes_cache_trace(tmp_path, monkeypatch, capsys):
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "verified-cache-hit", "answer": "Blue.",
                     "evidence": [], "freshness": {"mode": "snapshot"}, "resolution": "retrieval"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup("what color?", "alice", tmp_path)

    assert rc == 0
    out = capsys.readouterr().out
    assert ask.VOICE_LINE not in out
    rec = json.loads((tmp_path / "traces.jsonl").read_text().splitlines()[-1])
    assert rec["tier"] == "cache"


# -------------------------------------------------------------------- traces

def test_live_miss_lookup_writes_a_trace_line_with_tier_none(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", _no_candidates_memory)
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    ask.lookup("where is the deed?", "alice", tmp_path)
    capsys.readouterr()

    recs = read_jsonl(tmp_path / "traces.jsonl")
    traces = [r for r in recs if r.get("kind") == "trace"]
    assert len(traces) == 1
    tr = traces[0]
    assert tr["question"] == "where is the deed?"
    assert tr["tier"] == "none"
    assert tr["final_ranked"] == []
    assert "p1" in tr["routing"]
    assert "total_secs" in tr["timings"]
    assert tr["lookup_id"]


def test_live_hit_lookup_writes_a_trace_line_with_tier_confirmed_and_ranked_list(tmp_path, monkeypatch):
    note = tmp_path / "hit.md"
    note.write_text("The car is blue.\n")

    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": ["p1"]}
        if req["action"] == "navigate":
            return {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": str(note)}]}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    monkeypatch.setattr(ask, "confirm", lambda q, paths: ({str(note): 0.95}, set(), None, {}))
    ask.lookup("what color is the car?", "alice", tmp_path)

    recs = read_jsonl(tmp_path / "traces.jsonl")
    traces = [r for r in recs if r.get("kind") == "trace"]
    assert len(traces) == 1
    assert traces[0]["tier"] == "confirmed"
    assert traces[0]["final_ranked"][0]["path"] == str(note)
    assert traces[0]["content_check"][str(note)]["label"] == "confirmed"


def test_trace_redacts_secret_shaped_fields_and_truncates_long_ones(tmp_path):
    ask.write_trace(tmp_path, kind="trace", lookup_id="abc123", question="what is my api-key?",
                    errors=["password: hunter2 leaked here"], notes="x" * 900)

    rec = read_jsonl(tmp_path / "traces.jsonl")[0]
    assert rec["errors"] == ["[redacted]"]
    assert rec["notes"].endswith("...[truncated]")
    assert len(rec["notes"]) < 900


def test_trace_redacts_secret_looking_filename_not_in_key_value_shape(tmp_path):
    """A path/filename carrying a secret keyword glued to other characters (no
    key=value/key:value/'is' shape) must still be masked, e.g.
    password-hunter2xyz-notes.md -> password-[REDACTED].md."""
    ask.write_trace(tmp_path, kind="trace", lookup_id="abc124", question="q",
                    file="/tmp/notes/password-hunter2xyz-notes.md")

    rec = read_jsonl(tmp_path / "traces.jsonl")[0]
    assert rec["file"] == "/tmp/notes/password-[REDACTED].md"
    assert "hunter2xyz" not in json.dumps(rec)


def test_trace_leaves_normal_paths_untouched(tmp_path):
    ask.write_trace(tmp_path, kind="trace", lookup_id="abc125", question="q",
                    file="/Users/alice/projects/notes/tokenizer.md")

    rec = read_jsonl(tmp_path / "traces.jsonl")[0]
    assert rec["file"] == "/Users/alice/projects/notes/tokenizer.md"


def test_trace_leaves_prose_secret_words_in_paths_untouched(tmp_path):
    """A path segment that merely contains a secret keyword glued to a plain word
    (no digit, no real value shape) is prose, not a leaked secret -- e.g. a
    pytest tmp dir literally named after a test called test_secret_held_...
    must never get mangled by the path-redaction heuristic."""
    path = "/tmp/pytest-535/test_secret_held_does_not_save0/car.md"
    ask.write_trace(tmp_path, kind="trace", lookup_id="abc128", question="q", file=path)

    rec = read_jsonl(tmp_path / "traces.jsonl")[0]
    assert rec["file"] == path


def test_lookups_jsonl_stays_raw_so_find_top_still_matches(tmp_path):
    """lookups.jsonl is the working index --answer/--approve read back by exact
    question and on-disk path; redacting or truncating it breaks auto-cache."""
    q = "where is my token-2fa setup? " + "x" * 600
    path = "/notes/password_2024_notes.md"
    ask.log(tmp_path, "lookup", question=q, top=[{"score": 0.9, "path": path, "pointer": "p1", "possible": False}])

    top = ask.find_top(tmp_path, q)
    assert top and top["path"] == path
    assert ask.find_pointer(tmp_path, q) == "p1"


@pytest.mark.parametrize("val", ["0", "off", "OFF", "no", "False", " 0 "])
def test_superjev_traces_off_spellings(tmp_path, monkeypatch, val):
    monkeypatch.setenv("SUPERJEV_TRACES", val)
    ask.write_trace(tmp_path, kind="trace", lookup_id="x", question="q")
    ask.write_outcome(tmp_path, "x", "q", "right")
    assert not (tmp_path / "traces.jsonl").exists()


def test_path_secret_unicode_dash_and_case():
    assert ask.path_has_secret("PASSWORD\u2011hunter2.md")
    assert not ask.path_has_secret("password-reset-guide.md")


def test_superjev_traces_env_switch_disables_tracing(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERJEV_TRACES", "0")
    ask.write_trace(tmp_path, kind="trace", lookup_id="abc126", question="q")

    assert not (tmp_path / "traces.jsonl").exists()


def test_superjev_traces_env_switch_defaults_on(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERJEV_TRACES", raising=False)
    ask.write_trace(tmp_path, kind="trace", lookup_id="abc127", question="q")

    assert (tmp_path / "traces.jsonl").is_file()


def test_trace_rotation_caps_file_and_keeps_one_old_generation(tmp_path):
    path = tmp_path / "traces.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * (ask.TRACE_CAP_BYTES + 1))

    ask.write_trace(tmp_path, kind="trace", lookup_id="new1", question="q")

    old = tmp_path / "traces.jsonl.1"
    assert old.is_file()
    assert old.stat().st_size == ask.TRACE_CAP_BYTES + 1
    recs = read_jsonl(path)
    assert len(recs) == 1
    assert recs[0]["lookup_id"] == "new1"


def test_rotation_overwrites_a_prior_old_generation_keeping_only_one(tmp_path):
    path = tmp_path / "traces.jsonl"
    old = tmp_path / "traces.jsonl.1"
    old.write_text('{"kind": "ancient"}\n')
    path.write_bytes(b"x" * (ask.TRACE_CAP_BYTES + 1))

    ask.write_trace(tmp_path, kind="trace", lookup_id="new2", question="q")

    assert "ancient" not in old.read_text()
    assert path.stat().st_size < ask.TRACE_CAP_BYTES


# ---------------------------------------------------------------- outcomes

def _file_memory(path):
    """Fake harness citing `path` through open + assist (the file ask() ranked)."""
    def fake_memory(req):
        if req["action"] == "sources":
            return {"status": "ok", "sources": [{"sourceId": "s", "originalPath": str(path)}]}
        if req["action"] == "cached":
            return {"status": "cache-miss"}
        if req["action"] == "open":
            return {"status": "ok", "attemptId": "a1"}
        if req["action"] == "assist":
            return {"status": "ready", "approvalTicket": "tix",
                    "passages": [{"sourceId": "s", "reviewedText": "answer text"}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        raise AssertionError(req)
    return fake_memory


def test_approve_marks_the_last_lookup_right_with_evidence_file(tmp_path, monkeypatch):
    note = tmp_path / "a.md"
    note.write_text("answer text\n")
    ask.write_trace(tmp_path, kind="trace", lookup_id="lid1", question="q",
                    final_ranked=[{"score": 0.9, "path": str(note), "pointer": "p1"}], tier="confirmed")
    ask.log(tmp_path, "lookup", question="q", top=[{"score": 0.9, "path": str(note), "pointer": "p1", "possible": False}])

    monkeypatch.setattr(ask, "memory", _file_memory(note))
    rc = ask.approve("alice", "q", "answer", tmp_path)

    assert rc == 0
    outcomes = [r for r in read_jsonl(tmp_path / "traces.jsonl") if r.get("kind") == "outcome"]
    assert len(outcomes) == 1
    assert outcomes[0]["lookup_id"] == "lid1"
    assert outcomes[0]["result"] == "right"
    assert outcomes[0]["file"] == str(note)


def test_miss_marks_the_last_lookup_wrong_with_the_actual_path(tmp_path, monkeypatch):
    ask.write_trace(tmp_path, kind="trace", lookup_id="lid2", question="q2", final_ranked=[], tier="none")
    monkeypatch.setattr(ask, "memory", lambda req: {"status": "not-cached"})

    rc = ask.miss("alice", "q2", "/real/answer.md", tmp_path)

    assert rc == 0
    outcomes = [r for r in read_jsonl(tmp_path / "traces.jsonl") if r.get("kind") == "outcome"]
    assert len(outcomes) == 1
    assert outcomes[0]["lookup_id"] == "lid2"
    assert outcomes[0]["result"] == "wrong"
    assert outcomes[0]["file"] == "/real/answer.md"


def test_add_after_a_miss_marks_the_lookup_wrong_added(tmp_path, monkeypatch):
    ask.write_trace(tmp_path, kind="trace", lookup_id="lid3", question="q3", final_ranked=[], tier="none")
    monkeypatch.setattr(ask, "memory", lambda req: {"status": "not-cached"})
    ask.miss("alice", "q3", "/real/answer.md", tmp_path)

    def fake_memory(req):
        if req["action"] == "panel":
            return {"pointers": []}
        if req["action"] == "connect" and not req.get("reviewed"):
            return {"status": "preparation-required",
                     "sources": [{"path": req["sources"][0]["path"], "sha256": "deadbeef"}]}
        if req["action"] == "connect" and req.get("reviewed"):
            return {"status": "registered",
                     "sources": [{"id": "file:abc", "originalPath": req["sources"][0]["path"]}]}
        if req["action"] == "search":
            return {"status": "ready", "approvalTicket": "tix",
                     "passages": [{"sourceId": "s", "reviewedText": "answer text"}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.add_manual("alice", "q3", "answer text", None, tmp_path)

    assert rc == 0
    outcomes = [r for r in read_jsonl(tmp_path / "traces.jsonl") if r.get("kind") == "outcome"]
    assert outcomes[-1]["lookup_id"] == "lid3"
    assert outcomes[-1]["result"] == "wrong-added"


def test_approve_with_no_prior_lookup_writes_no_outcome(tmp_path, monkeypatch):
    note = tmp_path / "a.md"
    note.write_text("answer text\n")
    ask.log(tmp_path, "lookup", question="q4", top=[{"score": 0.9, "path": str(note), "pointer": "p1", "possible": False}])

    monkeypatch.setattr(ask, "memory", _file_memory(note))
    rc = ask.approve("alice", "q4", "answer", tmp_path)

    assert rc == 0
    assert not (tmp_path / "traces.jsonl").is_file()


# ------------------------------------------------------------------- report

def test_trace_report_counts_right_wrong_unlabeled_and_lists_top_wrong(tmp_path, capsys):
    ask.write_trace(tmp_path, kind="trace", lookup_id="r1", question="right one",
                    final_ranked=[{"score": 0.9, "path": "/a.md", "pointer": "p"}], tier="confirmed")
    ask.write_outcome(tmp_path, "r1", "right one", "right", file="/a.md")

    ask.write_trace(tmp_path, kind="trace", lookup_id="w1", question="wrong one",
                    final_ranked=[{"score": 0.3, "path": "/b.md", "pointer": "p"}], tier="possible")
    ask.write_outcome(tmp_path, "w1", "wrong one", "wrong", file="/real.md")

    ask.write_trace(tmp_path, kind="trace", lookup_id="w2", question="wrong one",
                    final_ranked=[{"score": 0.2, "path": "/c.md", "pointer": "p"}], tier="none")
    ask.write_outcome(tmp_path, "w2", "wrong one", "wrong-added", file="/real.md")

    ask.write_trace(tmp_path, kind="trace", lookup_id="u1", question="unlabeled one",
                    final_ranked=[], tier="none")

    rc = ask.trace_report(tmp_path)

    assert rc == 0
    out = capsys.readouterr().out
    assert "right: 1  wrong: 2  unlabeled: 1" in out
    assert "(2) wrong one" in out
    assert "/b.md" in out  # a still-wrong trace's ranked list for that question


def test_trace_report_days_filter_excludes_old_entries(tmp_path, capsys):
    old_ts = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() - 30 * 86400))
    (tmp_path / "traces.jsonl").write_text(json.dumps(
        {"ts": old_ts, "kind": "trace", "lookup_id": "old1", "question": "ancient", "final_ranked": [], "tier": "none"}
    ) + "\n")
    ask.write_trace(tmp_path, kind="trace", lookup_id="new1", question="recent", final_ranked=[], tier="none")

    rc = ask.trace_report(tmp_path, days=7)

    assert rc == 0
    out = capsys.readouterr().out
    assert "right: 0  wrong: 0  unlabeled: 1" in out


def test_trace_report_reads_the_rotated_old_generation_too(tmp_path, capsys):
    (tmp_path / "traces.jsonl.1").write_text(json.dumps(
        {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": "trace", "lookup_id": "prev1",
         "question": "from before rotation", "final_ranked": [], "tier": "none"}
    ) + "\n")
    ask.write_trace(tmp_path, kind="trace", lookup_id="new2", question="after rotation",
                    final_ranked=[], tier="none")

    rc = ask.trace_report(tmp_path)

    assert rc == 0
    assert "unlabeled: 2" in capsys.readouterr().out


# ---------------------------------------------------------------- per-stage trace

def test_trace_records_stages_and_trace_show_prints_where_a_file_dropped(tmp_path, monkeypatch, capsys):
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": ["p1"]}
        if req["action"] == "panel":
            return {"pointers": ["p1"]}
        if req["action"] == "navigate":
            return {"status": "candidates", "candidates": [{"score": 0.7, "originalPath": "/a.md"},
                                                           {"score": 0.02, "originalPath": "/b.md"}],
                    "trace": [{"path": ["root"], "choices": [{"nodeId": "root", "none": 0.2}]}]}
        raise AssertionError(req)

    def fake_word_search(q, ptrs, skip=()):
        ranked = [(9.0, "/a.md", "p1"), (8.0, "/w.md", "p1"), (7.0, "/x.md", "p1"), (6.0, "/y.md", "p1"),
                  (5.0, "/z.md", "p1")]
        ask._STAGE["word"] = {"terms": ["knee"], "files_searched": 5, "passed_coverage": 5, "ranked": ranked}
        return [r for r in ranked if r[1] not in skip][:3]

    def fake_confirm(q, paths):
        ask._STAGE["checks"] = {"/a.md": {"chunks": 11, "read": [0, 1, 2, 9], "wording": "exact-value",
                                          "best": 0.93, "none": 0.05}}
        return {"/a.md": 0.93}, set(), None, {}

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "word_search", fake_word_search)
    monkeypatch.setattr(ask, "confirm", fake_confirm)
    ask.lookup("knee code?", "alice", tmp_path)
    capsys.readouterr()

    st = read_jsonl(tmp_path / "traces.jsonl")[-1]["stages"]
    assert st["cache"] == {"result": "cache-miss", "checked": 1}
    assert st["routing"]["p1"]["none"] == 0.2
    assert [f["kept"] for f in st["routing"]["p1"]["files"]] == [True, False]
    fates = [f["fate"] for f in st["word_search"]["top"]]
    assert fates == ["already routed", "read", "read", "read", "not read: past top 5"]
    assert st["read_list"] == ["/a.md", "/w.md", "/x.md", "/y.md"]
    assert st["content_check"]["/a.md"]["read"] == [0, 1, 2, 9]
    assert st["final"][0]["path"] == "/a.md" and st["final"][0]["rule"].startswith("confirmed")

    assert ask.trace_show(tmp_path, "last") == 0
    out = capsys.readouterr().out
    assert "/z.md  -> not read: past top 5" in out
    assert "b.md 0.02 (under floor)" in out
    assert "chunks [0, 1, 2, 9] of 11" in out
    assert ask.trace_show(tmp_path, "nope") == 1
