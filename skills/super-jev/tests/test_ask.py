#!/usr/bin/env python3
"""Offline tests for ask.py, the cache-first front door over the memory harness.

No network, no live harness call, no live provider: `ask.memory` is monkeypatched
at the module level for every test, and the state directory is always an isolated
tmp_path (either passed directly to the lower-level functions or via a
monkeypatched `ask.state_dir`). `navigate` is asserted never called on a cache
hit; the harness `cached` action is asserted never confused with local bookkeeping.

    python3 -m pytest skills/super-jev/tests/test_ask.py -q
"""
import hashlib
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


@pytest.fixture(autouse=True)
def no_content_check(monkeypatch):
    """Stub completed evidence so these tests isolate routing and queue behavior."""
    monkeypatch.setattr(ask, "confirm", lambda question, paths: ({p: .9 for p in paths}, set(), None, {}))
    # Every save runs the claim check; these tests fake it CLEAN (test_saved_answers.py covers refusals).
    monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None: ("CLEAN", 0.93))


def test_cache_hit_prints_answer_and_never_navigates(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_memory(req):
        calls.append(req["action"])
        if req["action"] == "cached":
            return {"status": "verified-cache-hit", "answer": "Blue.",
                     "evidence": [{"sourceId": "one", "quote": "blue", "path": "/x/source.txt", "contentSHA": "s"}],
                     "freshness": {"mode": "snapshot"}, "resolution": "retrieval"}
        raise AssertionError(f"unexpected action: {req['action']}")

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup("what color?", "alice", tmp_path)

    assert rc == 0
    assert "navigate" not in calls
    assert "panel" not in calls
    out = capsys.readouterr().out
    assert "CACHE HIT" in out
    assert "Blue." in out
    assert "one" in out


def _toc_read_list(monkeypatch, tmp_path, files, scores=None):
    """The TOC search picks the read list now (routing asks Jev nothing): stub it to list `files`
    ({name: pointer}) in order, with `scores` as the content check's verdicts."""
    paths = {}
    for name, ptr in files.items():
        f = tmp_path / "notes" / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("the answer is here\n")
        paths[str(f)] = ptr
    monkeypatch.setattr(ask, "candidate_files", lambda *a, **k: [
        (ptr, p, {"sha256": ask.sha256_file(Path(p))}) for p, ptr in paths.items()])
    # a set with a prepare-cache is searched through the TOC list; one without is routed by navigate
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {
        p: {"pass": True, "sha256": ask.sha256_file(Path(p))} for p, q in paths.items() if q == ptr})
    monkeypatch.setattr(ask.toc_search, "run", lambda *a, **k: (list(paths), [], {}))
    by_name = {str(tmp_path / "notes" / n): v for n, v in (scores or {}).items()}
    monkeypatch.setattr(ask, "confirm", lambda q, ps: (dict(by_name), set(), None, {}))
    return list(paths)


def test_miss_fans_out_over_panel_pointers_merged_by_score_and_logs(tmp_path, monkeypatch, capsys):
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1"}, {"pointer": "p2"}]}
        raise AssertionError(req)  # routing asks Jev nothing

    monkeypatch.setattr(ask, "memory", fake_memory)
    low, high = _toc_read_list(monkeypatch, tmp_path, {"low.md": "p1", "high.md": "p2"},
                               {"low.md": 0.4, "high.md": 0.9})
    rc = ask.lookup("where is it?", "alice", tmp_path / "state")

    assert rc == 0
    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if l.strip() and l.strip()[0].isdigit()]
    assert lines[0].strip().startswith("0.90") and high in lines[0] and "[p2]" in lines[0]
    log_path = tmp_path / "state" / "lookups.jsonl"
    assert log_path.is_file()
    rec = json.loads(log_path.read_text().splitlines()[-1])
    assert rec["kind"] == "lookup"
    assert rec["pointers"] == 2
    assert rec["statuses"] == {"p1": "no-candidates", "p2": "no-candidates"}


def test_pointer_error_prints_status_line_and_is_not_folded_into_no_candidates(tmp_path, monkeypatch, capsys):
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1", "snapshotStatus": "refresh-required"}, {"pointer": "p2"}]}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    _toc_read_list(monkeypatch, tmp_path, {"a.md": "p1", "b.md": "p2"})
    rc = ask.lookup("q", "alice", tmp_path / "state")

    # routing now reads each set's status from the registry: a stale set is still searched and named
    # (as of its last refresh), never folded into a plain no-candidates
    out = capsys.readouterr().out
    assert "[p1] stale: searched as of its last refresh" in out
    assert "no-candidates across" not in out
    assert rc == 1 and out.startswith("OUTCOME: not-found")


def test_hit_from_healthy_pointer_prints_before_a_sibling_pointer_error(tmp_path, monkeypatch, capsys):
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1", "snapshotStatus": "refresh-required"}, {"pointer": "p2"}]}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    _stale, hit = _toc_read_list(monkeypatch, tmp_path, {"stale.md": "p1", "hit.md": "p2"}, {"hit.md": 0.8})
    rc = ask.lookup("q", "alice", tmp_path / "state")

    # A healthy pointer answering the question is a real result: the sibling
    # pointer's note stays visible (below), but does not fail the whole lookup.
    assert rc == 0
    out = capsys.readouterr().out
    assert hit in out
    assert "[p2]" in out
    assert "[p1] stale" in out
    # the hit must appear before the note line
    assert out.index(hit) < out.index("[p1] stale")


def test_lookup_logs_top_from_the_healthy_pointer_so_answer_can_still_auto_cache(tmp_path, monkeypatch, capsys):
    """Bug report: after a pointer errors (preparation-required), the save of the
    same question said "not saved: no prior lookup with candidates". Confirmed here: lookup()
    already logs `top` from whichever pointers DID answer, independent of a sibling pointer's
    error, so find_top() (what --approve reads) still finds the healthy candidate. The reported
    symptom traced back to the multi-principal refresh bug (prepare_bulk.py connect_part),
    which left every pointer perpetually preparation-required with nothing left to answer from
    -- not a gap in this recording path."""
    note = tmp_path / "hit.md"
    note.write_text("The car is blue.\n")

    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "broken", "snapshotStatus": "preparation-required"}, {"pointer": "healthy"}]}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "candidate_files", lambda *a, **k: [("healthy", str(note), {"sha256": ask.sha256_file(note)})])
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {str(note): {"pass": True, "sha256": ask.sha256_file(note)}} if ptr == "healthy" else {"/x": {}})
    monkeypatch.setattr(ask.toc_search, "run", lambda *a, **k: ([str(note)], [], {}))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({str(note): 0.9}, set(), None, {}))
    sdir = tmp_path / "state"
    rc = ask.lookup("what color is the car?", "alice", sdir)

    assert rc == 0  # the broken pointer stays visible, but the healthy hit still succeeds
    top = ask.find_top(sdir, "what color is the car?")
    assert top == {"score": 0.9, "path": str(note), "pointer": "healthy", "possible": False}

    monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None: ("CLEAN", 0.93))
    monkeypatch.setattr(ask, "memory", lambda req: (
        {"status": "ok", "sources": [{"sourceId": "s1", "originalPath": str(note),
                                       "contentSHA": ask.sha256_file(note)}]} if req["action"] == "sources" else
        {"status": "ok", "attemptId": "a1"} if req["action"] == "open" else
        {"status": "cache-miss"} if req["action"] == "cached" else
        {"status": "ready", "approvalTicket": "t1", "passages": [{"sourceId": "s1", "reviewedText": "The car is blue."}]}
        if req["action"] == "assist" else
        {"status": "saved"} if req["action"] == "approve" else
        (_ for _ in ()).throw(AssertionError(req))
    ))
    assert ask.save_answer("alice", "what color is the car?", "The car is blue.", sdir) == 0
    assert "not saved" not in capsys.readouterr().out


def test_all_pointers_empty_gives_no_candidates_hint_naming_connectors_and_add(tmp_path, monkeypatch, capsys):
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": ["p1"]}
        if req["action"] == "navigate":
            return {"status": "no-candidates", "candidates": []}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup("q", "alice", tmp_path)

    assert rc == 1  # not-found
    out = capsys.readouterr().out
    assert out.startswith("OUTCOME: not-found - searched 1 set")
    assert "may still exist" in out
    assert "--add" in out


def test_miss_report_says_what_was_searched_and_next_steps(tmp_path, monkeypatch, capsys):
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": ["p1", "p2"]}
        if req["action"] == "navigate":
            return {"status": "no-candidates", "candidates": []}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    assert ask.lookup("q", "alice", tmp_path) == 1  # not-found
    out = capsys.readouterr().out.strip().splitlines()
    assert "What was searched:" in out
    assert "  - 2 connected sets; 2 searched after the topic filter; descriptions matched in 0" in out
    assert any("ask.py --principal alice --add" in line for line in out)
    assert any("/prepare_bulk.py --root <folder> --pointer alice-<name>" in line for line in out)
    assert out[-1] == ask.VOICE_LINE  # the voice line stays last


def test_miss_report_names_closest_files_read_first():
    lines = ask.miss_report("bob", 5, {"p1": {"status": "candidates"}, "p2": {"status": "no-candidates"}},
                            {"/x/low.md": {"score": 0.2}, "/x/high.md": {"score": 0.5}})
    assert lines[1] == "  - 5 connected sets; 2 searched after the topic filter; descriptions matched in 1: p1"
    assert lines[2] == ("  - 2 file(s) read (picked by description or by words in the file); no answer confirmed. "
                        "Closest: x/high.md, x/low.md")


def test_skill_dir_for_display_prefers_env_override(monkeypatch):
    monkeypatch.setenv("SUPERJEV_SKILL_DIR", "/env/override/skills/super-jev")
    assert ask.skill_dir_for_display() == Path("/env/override/skills/super-jev")


def test_skill_dir_for_display_uses_invoked_argv0_not_resolved_path(monkeypatch, tmp_path):
    monkeypatch.delenv("SUPERJEV_SKILL_DIR", raising=False)
    # A stable symlinked skills dir, as an installed agent would invoke through --
    # this must stay stable across releases, unlike Path(__file__).resolve().
    stable_dir = tmp_path / "stable-skills-dir"
    stable_dir.symlink_to(Path(ask.__file__).resolve().parent)  # the link an install makes
    monkeypatch.setattr(sys, "argv", [str(stable_dir / "ask.py"), "--principal", "x", "q"])
    assert ask.skill_dir_for_display() == stable_dir


def test_skill_dir_for_display_ignores_an_unrelated_launcher_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("SUPERJEV_SKILL_DIR", raising=False)
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "pytest")])  # e.g. pytest's bin/
    assert ask.skill_dir_for_display() == Path(ask.__file__).resolve().parent


def test_skill_dir_for_display_falls_back_when_argv0_dir_missing(monkeypatch):
    monkeypatch.delenv("SUPERJEV_SKILL_DIR", raising=False)
    monkeypatch.setattr(sys, "argv", ["/does/not/exist/ask.py"])
    assert ask.skill_dir_for_display() == Path(ask.__file__).resolve().parent


def test_miss_report_next_steps_use_invoked_skill_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("SUPERJEV_SKILL_DIR", raising=False)
    stable_dir = tmp_path / "stable-skills-dir"
    stable_dir.symlink_to(Path(ask.__file__).resolve().parent)  # the link an install makes
    monkeypatch.setattr(sys, "argv", [str(stable_dir / "ask.py")])
    lines = ask.miss_report("bob", 1, {}, {})
    joined = "\n".join(lines)
    assert str(stable_dir / "prepare_bulk.py") in joined
    assert str(stable_dir / "ask.py") in joined


def test_add_writes_manual_record_connects_new_pointer_never_replace_then_approves(tmp_path, monkeypatch):
    src_file = tmp_path / "src.txt"
    src_file.write_text("hello source\n")
    calls = []

    def fake_memory(req):
        calls.append(req)
        if req["action"] == "panel":
            return {"pointers": []}
        if req["action"] == "connect" and not req.get("reviewed"):
            return {"status": "preparation-required",
                     "sources": [{"path": req["sources"][0]["path"], "sha256": "deadbeef"}]}
        if req["action"] == "connect" and req.get("reviewed"):
            assert req.get("replace") is not True
            return {"status": "registered",
                     "sources": [{"id": "file:abc123", "originalPath": req["sources"][0]["path"]}]}
        if req["action"] == "search":
            return {"status": "ready", "approvalTicket": "tix",
                     "passages": [{"sourceId": "s", "reviewedText": "answer text"}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    question = "which pointers hold the example agent brain"
    rc = ask.add_manual("alice", question, "The answer body.", str(src_file), tmp_path)

    assert rc == 0
    expected_pointer = f"alice-manual-{hashlib.sha1(question.encode()).hexdigest()[:10]}"
    record = tmp_path / "manual" / f"{expected_pointer}.md"
    assert record.is_file()
    text = record.read_text()
    assert f"source_path: {src_file.resolve()}" in text
    assert "source_sha256:" in text
    connects = [c for c in calls if c["action"] == "connect"]
    assert len(connects) == 2
    assert connects[0]["pointer"] == expected_pointer
    assert connects[1]["reviewed"] is True
    assert connects[1]["sources"][0]["sha256"] == "deadbeef"
    assist_calls = [c for c in calls if c["action"] == "assist"]
    assert assist_calls == []


def test_add_manual_matches_hash_when_memory_backend_returns_realpath(tmp_path, monkeypatch):
    """Bug: on a host where the state dir (or the source file) lives under a
    symlinked directory -- e.g. macOS /tmp -> /private/tmp -- the memory backend
    normalizes paths to realpath before echoing them back in the connect preview.
    add_manual used to look the sha256 up by the literal path string it sent and
    KeyError'd on the mismatch. Compare on os.path.realpath everywhere."""
    real_root = tmp_path / "real_root"
    real_root.mkdir()
    link_root = tmp_path / "link_root"
    link_root.symlink_to(real_root)

    sdir = link_root / "state"
    src_file = link_root / "src.txt"
    src_file.write_text("hello source\n")

    def fake_memory(req):
        if req["action"] == "panel":
            return {"pointers": []}
        if req["action"] == "connect" and not req.get("reviewed"):
            # Simulate a backend that canonicalizes the path before echoing it back.
            import os
            canon = os.path.realpath(req["sources"][0]["path"])
            return {"status": "preparation-required", "sources": [{"path": canon, "sha256": "deadbeef"}]}
        if req["action"] == "connect" and req.get("reviewed"):
            return {"status": "registered",
                     "sources": [{"id": "file:abc123", "originalPath": req["sources"][0]["path"]}]}
        if req["action"] == "search":
            return {"status": "ready", "approvalTicket": "tix",
                     "passages": [{"sourceId": "s", "reviewedText": "answer text"}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    question = "which pointers hold the symlinked example brain"
    rc = ask.add_manual("alice", question, "The answer body.", str(src_file), sdir)

    assert rc == 0
    pointer = ask.manual_pointer_name("alice", question)
    record = sdir / "manual" / f"{pointer}.md"
    assert record.is_file()


def test_add_falls_back_to_assist_on_no_match_then_approves_with_returned_ticket(tmp_path, monkeypatch):
    src_file = tmp_path / "src.txt"
    src_file.write_text("hello source\n")
    question = "which pointers hold the shared brain now"
    expected_pointer = f"alice-manual-{hashlib.sha1(question.encode()).hexdigest()[:10]}"
    calls = []

    def fake_memory(req):
        calls.append(req)
        if req["action"] == "panel":
            return {"pointers": []}
        if req["action"] == "connect" and not req.get("reviewed"):
            return {"status": "preparation-required",
                     "sources": [{"path": req["sources"][0]["path"], "sha256": "deadbeef"}]}
        if req["action"] == "connect" and req.get("reviewed"):
            return {"status": "registered",
                     "sources": [{"id": "file:src-id", "originalPath": req["sources"][0]["path"]}]}
        if req["action"] == "search":
            return {"status": "no-match", "attemptId": "attempt-1"}
        if req["action"] == "assist":
            return {"status": "ready", "approvalTicket": "assist-tix",
                     "passages": [{"sourceId": "file:src-id", "reviewedText": "The manual answer text."}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.add_manual("alice", question, "The manual answer text.", str(src_file), tmp_path)

    assert rc == 0
    record = tmp_path / "manual" / f"{expected_pointer}.md"
    expected_last_line = len(record.read_text().splitlines())
    assist_calls = [c for c in calls if c["action"] == "assist"]
    assert len(assist_calls) == 1
    assert assist_calls[0]["attemptId"] == "attempt-1"
    assert assist_calls[0]["principal"] == "alice"
    assert assist_calls[0]["references"] == [{"sourceId": "file:src-id", "startLine": 1, "endLine": expected_last_line}]
    approve_calls = [c for c in calls if c["action"] == "approve"]
    assert len(approve_calls) == 1
    assert approve_calls[0]["ticket"] == "assist-tix"
    assert approve_calls[0]["evidence"] == [{"sourceId": "file:src-id", "quote": "The manual answer text."}]


def test_add_refuses_when_pointer_already_exists_for_question(tmp_path, monkeypatch, capsys):
    question = "duplicate wording"
    expected_pointer = f"alice-manual-{hashlib.sha1(question.encode()).hexdigest()[:10]}"

    def fake_memory(req):
        if req["action"] == "panel":
            return {"pointers": [expected_pointer]}
        raise AssertionError("must refuse before ever calling connect")

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.add_manual("alice", question, "answer", None, tmp_path)

    assert rc == 1
    out = capsys.readouterr().out
    assert "refused" in out
    assert expected_pointer in out


def test_add_manual_default_labels_in_header_and_connect_description(tmp_path, monkeypatch):
    """No --subject/--kind/--status/--as-of given: the record header and the connect
    description both carry the defaults (record/active/today/derived subject)."""
    calls = []

    def fake_memory(req):
        calls.append(req)
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
    question = "what is pending for the widget project rollout"
    rc = ask.add_manual("alice", question, "The answer body.", None, tmp_path)

    assert rc == 0
    pointer = ask.manual_pointer_name("alice", question)
    record = tmp_path / "manual" / f"{pointer}.md"
    text = record.read_text()
    assert "kind: record" in text
    assert "status: active" in text
    assert f"as_of: {time.strftime('%Y-%m-%d')}" in text
    assert "subject: " in text
    assert "project: alice" in text

    connects = [c for c in calls if c["action"] == "connect"]
    desc = connects[0]["sources"][0]["description"]
    assert "[kind: record; status: active; as_of:" in desc
    assert "subject:" in desc


def test_add_manual_explicit_labels_override_defaults(tmp_path, monkeypatch):
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
    question = "where is the typesafe api key kept"
    rc = ask.add_manual("alice", question, "answer", None, tmp_path,
                         kind="pointer", status="done", as_of="2026-01-15", subject="typesafe api key")

    assert rc == 0
    pointer = ask.manual_pointer_name("alice", question)
    text = (tmp_path / "manual" / f"{pointer}.md").read_text()
    assert "kind: pointer" in text
    assert "status: done" in text
    assert "as_of: 2026-01-15" in text
    assert "subject: typesafe api key" in text


def test_do_add_invalid_kind_is_usage_error_exit_2(tmp_path, monkeypatch):
    monkeypatch.setattr(ask, "memory", lambda req: (_ for _ in ()).throw(AssertionError("must not call memory")))
    rc = ask.do_add("alice", ["q", "answer", "--kind", "not-a-kind"], tmp_path)
    assert rc == 2


def test_do_add_invalid_status_is_usage_error_exit_2(tmp_path, monkeypatch):
    monkeypatch.setattr(ask, "memory", lambda req: (_ for _ in ()).throw(AssertionError("must not call memory")))
    rc = ask.do_add("alice", ["q", "answer", "--status", "bogus"], tmp_path)
    assert rc == 2


def test_do_add_invalid_as_of_is_usage_error_exit_2(tmp_path, monkeypatch):
    monkeypatch.setattr(ask, "memory", lambda req: (_ for _ in ()).throw(AssertionError("must not call memory")))
    rc = ask.do_add("alice", ["q", "answer", "--as-of", "not-a-date"], tmp_path)
    assert rc == 2


def test_do_add_parses_flags_in_any_order_and_builds_answer_from_remaining_words(tmp_path, monkeypatch):
    captured = {}

    def fake_add_manual(principal, question, answer, source, sdir, **kw):
        captured.update(kw); captured["principal"] = principal; captured["question"] = question
        captured["answer"] = answer; captured["source"] = source
        return 0

    monkeypatch.setattr(ask, "add_manual", fake_add_manual)
    rc = ask.do_add("alice", ["the question", "the", "--subject", "widgets", "answer", "--kind", "note",
                               "words", "--status", "closed"], tmp_path)

    assert rc == 0
    assert captured["question"] == "the question"
    assert captured["answer"] == "the answer words"
    assert captured["subject"] == "widgets"
    assert captured["kind"] == "note"
    assert captured["status"] == "closed"


def test_replace_entry_removes_old_pointer_and_record_then_adds_fresh(tmp_path, monkeypatch):
    question = "duplicate wording to replace"
    pointer = ask.manual_pointer_name("alice", question)
    manual_dir = tmp_path / "manual"
    manual_dir.mkdir(parents=True)
    old_record = manual_dir / f"{pointer}.md"
    old_record.write_text("# old\n\nstale body\n")
    calls = []

    def fake_memory(req):
        calls.append(req)
        if req["action"] == "panel":
            return {"pointers": [pointer]}
        if req["action"] == "remove":
            return {"status": "removed"}
        if req["action"] == "connect" and not req.get("reviewed"):
            return {"status": "preparation-required",
                     "sources": [{"path": req["sources"][0]["path"], "sha256": "deadbeef"}]}
        if req["action"] == "connect" and req.get("reviewed"):
            return {"status": "registered",
                     "sources": [{"id": "file:abc", "originalPath": req["sources"][0]["path"]}]}
        if req["action"] == "search":
            return {"status": "ready", "approvalTicket": "tix",
                     "passages": [{"sourceId": "s", "reviewedText": "fresh text"}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.add_manual("alice", question, "fresh answer", None, tmp_path, replace=True)

    assert rc == 0
    remove_calls = [c for c in calls if c["action"] == "remove"]
    assert len(remove_calls) == 1
    assert remove_calls[0]["pointer"] == pointer
    new_text = old_record.read_text()
    assert "stale body" not in new_text
    assert "fresh answer" in new_text
    # `remove` only drops principal access; the harness keeps the dataset registered
    # under that pointer name, so the reconnect after an explicit remove still needs
    # replace:true, exactly like a stale-source reconnect elsewhere in this codebase.
    connect_calls = [c for c in calls if c["action"] == "connect"]
    assert connect_calls and all(c.get("replace") is True for c in connect_calls)


def test_replace_entry_sets_replace_true_even_when_panel_no_longer_lists_the_pointer(tmp_path, monkeypatch):
    """Live-discovered case: a pointer removed in an earlier run no longer shows up in
    `panel`, but the harness's dataset registry still has it under that pointer name --
    a connect without replace:true still fails as already-connected. --replace-entry
    must send replace:true regardless of what panel currently reports."""
    question = "duplicate wording, already removed from panel"
    pointer = ask.manual_pointer_name("alice", question)
    calls = []

    def fake_memory(req):
        calls.append(req)
        if req["action"] == "panel":
            return {"pointers": []}  # panel no longer lists it
        if req["action"] == "connect" and not req.get("reviewed"):
            if not req.get("replace"):
                return {"status": "preparation-required", "reason": "already-connected"}
            return {"status": "preparation-required",
                     "sources": [{"path": req["sources"][0]["path"], "sha256": "deadbeef"}]}
        if req["action"] == "connect" and req.get("reviewed"):
            if not req.get("replace"):
                return {"status": "preparation-required", "reason": "already-connected"}
            return {"status": "registered",
                     "sources": [{"id": "file:abc", "originalPath": req["sources"][0]["path"]}]}
        if req["action"] == "search":
            return {"status": "ready", "approvalTicket": "tix",
                     "passages": [{"sourceId": "s", "reviewedText": "fresh text"}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        if req["action"] == "remove":
            raise AssertionError("panel never listed it; remove should not be called")
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.add_manual("alice", question, "fresh answer", None, tmp_path, replace=True)

    assert rc == 0
    remove_calls = [c for c in calls if c["action"] == "remove"]
    assert remove_calls == []


def test_add_without_replace_flag_still_refuses_when_pointer_exists(tmp_path, monkeypatch, capsys):
    question = "duplicate wording no replace"
    pointer = ask.manual_pointer_name("alice", question)

    def fake_memory(req):
        if req["action"] == "panel":
            return {"pointers": [pointer]}
        raise AssertionError("must refuse before ever calling connect or remove")

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.add_manual("alice", question, "answer", None, tmp_path)

    assert rc == 1
    out = capsys.readouterr().out
    assert "refused" in out
    assert "--replace-entry" in out


def test_second_manual_entry_leaves_first_pointer_untouched(tmp_path, monkeypatch):
    """Adding a second manual entry must never replace or remove another pointer."""
    registered_pointers = []
    calls = []

    def fake_memory(req):
        calls.append(json.loads(json.dumps(req)))  # req is mutated in place by add_manual; snapshot it
        if req["action"] == "panel":
            return {"pointers": list(registered_pointers)}
        if req["action"] == "connect" and not req.get("reviewed"):
            return {"status": "preparation-required",
                     "sources": [{"path": req["sources"][0]["path"], "sha256": "deadbeef"}]}
        if req["action"] == "connect" and req.get("reviewed"):
            assert req.get("replace") is not True
            registered_pointers.append(req["pointer"])
            return {"status": "registered",
                     "sources": [{"id": "file:" + req["pointer"], "originalPath": req["sources"][0]["path"]}]}
        if req["action"] == "search":
            return {"status": "ready", "approvalTicket": "tix-" + req["pointer"],
                     "passages": [{"sourceId": "s", "reviewedText": "answer text"}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        if req["action"] == "remove":
            raise AssertionError("must never remove another pointer while adding a new one")
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc1 = ask.add_manual("alice", "first manual question", "answer one", None, tmp_path)
    rc2 = ask.add_manual("alice", "second manual question", "answer two", None, tmp_path)

    assert rc1 == 0
    assert rc2 == 0
    assert len(registered_pointers) == 2
    assert registered_pointers[0] != registered_pointers[1]
    remove_calls = [c for c in calls if c["action"] == "remove"]
    assert remove_calls == []
    connects = [c for c in calls if c["action"] == "connect" and c.get("reviewed")]
    assert connects[0]["pointer"] == registered_pointers[0]
    assert connects[1]["pointer"] == registered_pointers[1]
    # The second add's connect call never references the first pointer's name.
    assert registered_pointers[0] not in json.dumps(connects[1])


def test_cache_hit_on_manual_pointer_with_changed_source_is_invalidated_not_served(tmp_path, monkeypatch, capsys):
    # Staleness is keyed off the manual record this principal+question would have
    # written (ask.manual_record_path), not off evidence.path -- the harness only
    # ever returns its own internal prepared-copy path there, never the caller's file.
    question = "q"
    record = ask.manual_record_path(tmp_path, "alice", question)
    record.parent.mkdir(parents=True)
    src = tmp_path / "orig.txt"
    src.write_text("version 1\n")
    recorded_hash = hashlib.sha256(src.read_bytes()).hexdigest()
    record.write_text(f"# {question}\n\nsource_path: {src}\nsource_sha256: {recorded_hash}\n\nThe answer.\n")
    src.write_text("version 2, changed since recording\n")

    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "verified-cache-hit", "answer": "The answer.",
                     "evidence": [{"sourceId": "s", "quote": "The answer.",
                                  "path": "/registry/.prepared-xyz/0.txt", "contentSHA": "x"}],
                     "freshness": {"mode": "snapshot"}, "resolution": "retrieval"}
        if req["action"] == "panel":  # stale answer withheld; live search runs instead
            return {"pointers": []}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup(question, "alice", tmp_path)

    assert rc == 4  # the stale answer is withheld and the live search finds nothing connected: needs-setup
    out = capsys.readouterr().out
    assert "STALE: source changed since this answer was recorded" in out
    assert str(src) in out
    assert "CACHE HIT" not in out
    assert "The answer." not in out


def test_cache_hit_on_manual_pointer_with_unchanged_source_serves_the_answer(tmp_path, monkeypatch, capsys):
    question = "q"
    record = ask.manual_record_path(tmp_path, "alice", question)
    record.parent.mkdir(parents=True)
    src = tmp_path / "orig.txt"
    src.write_text("version 1\n")
    recorded_hash = hashlib.sha256(src.read_bytes()).hexdigest()
    record.write_text(f"# {question}\n\nsource_path: {src}\nsource_sha256: {recorded_hash}\n\nThe answer.\n")

    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "verified-cache-hit", "answer": "The answer.",
                     "evidence": [{"sourceId": "s", "quote": "The answer.",
                                  "path": "/registry/.prepared-xyz/0.txt", "contentSHA": "x"}],
                     "freshness": {"mode": "snapshot"}, "resolution": "retrieval"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup(question, "alice", tmp_path)

    assert rc == 0
    out = capsys.readouterr().out
    assert "STALE" not in out
    assert "CACHE HIT" in out
    assert "The answer." in out


def test_missing_principal_prints_usage_and_exits_2(monkeypatch, capsys):
    monkeypatch.delenv("SUPERJEV_PRINCIPAL", raising=False)
    monkeypatch.setattr(sys, "argv", ["ask.py", "some question"])

    rc = ask.main()

    assert rc == 2
    out = capsys.readouterr().out
    assert "ask.py" in out


def test_missing_question_prints_usage_and_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "alice"])

    rc = ask.main()

    assert rc == 2


def test_main_routes_miss_through_state_dir_and_logs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ask, "state_dir", lambda principal: tmp_path)
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "alice", "--miss", "q", "it was in the wiki"])
    monkeypatch.setattr(ask, "memory", lambda req: {"status": "not-cached"})

    rc = ask.main()

    assert rc == 1  # nothing was saved for "q", so nothing was removed
    out = capsys.readouterr().out
    assert "no saved answer" in out
    rec = json.loads((tmp_path / "lookups.jsonl").read_text().splitlines()[-1])
    assert rec["kind"] == "miss"
    assert rec["question"] == "q"
    assert rec["actual"] == "it was in the wiki"


def test_nav_concurrency_defaults_to_6(monkeypatch):
    monkeypatch.delenv("SUPERJEV_NAV_CONCURRENCY", raising=False)

    assert ask._nav_concurrency() == 6


def test_nav_concurrency_reads_env_override(monkeypatch):
    monkeypatch.setenv("SUPERJEV_NAV_CONCURRENCY", "4")

    assert ask._nav_concurrency() == 4


def test_nav_concurrency_falls_back_on_bad_value(monkeypatch):
    monkeypatch.setenv("SUPERJEV_NAV_CONCURRENCY", "not-a-number")

    assert ask._nav_concurrency() == 6


def test_nav_concurrency_falls_back_on_non_positive_value(monkeypatch):
    monkeypatch.setenv("SUPERJEV_NAV_CONCURRENCY", "0")

    assert ask._nav_concurrency() == 6


def test_lookup_caps_navigate_fanout_at_nav_concurrency(tmp_path, monkeypatch):
    """A claim lookup (the only path that still routes through navigate) with more pointers than
    SUPERJEV_NAV_CONCURRENCY never runs more navigate calls concurrently than the cap, even though
    every pointer is still attempted."""
    import threading

    monkeypatch.setitem(ask._CLAIM, "text", "a claim")
    monkeypatch.setattr(ask, "NAV_CONCURRENCY", 2)
    in_flight = []
    peak = []
    lock = threading.Lock()

    def fake_memory(req):
        if req.get("action") != "navigate":
            return {"status": "candidates", "pointers": ["p1", "p2", "p3", "p4"]} if req.get("action") == "panel" else {}
        with lock:
            in_flight.append(1)
            peak.append(sum(in_flight))
        time.sleep(0.05)
        with lock:
            in_flight.pop()
        return {"status": "no-candidates"}

    monkeypatch.setattr(ask, "memory", fake_memory)

    ask.lookup("q", "alice", tmp_path)  # completes without raising

    assert peak and max(peak) <= 2


@pytest.mark.real_toc
def test_lookup_asks_jev_nothing_at_routing_for_sets_with_a_prepare_cache(tmp_path, monkeypatch):
    """The TOC search picks the read list for a set that has a prepare-cache: no navigate call for it."""
    actions = []

    def fake_memory(req):
        actions.append(req.get("action"))
        return {"pointers": ["p1", "p2"]} if req.get("action") == "panel" else {}

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})
    monkeypatch.setattr(ask, "candidate_files", lambda *a, **k: [])
    monkeypatch.setattr(ask.toc_search, "run", lambda *a, **k: ([], [], {}))
    for ptr in ("p1", "p2"):
        f = tmp_path / f"{ptr}.md"
        f.write_text("made up note\n")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {str(tmp_path / f"{ptr}.md"): {"pass": True}})

    assert ask.lookup("q", "alice", tmp_path / "state") == 1
    assert "navigate" not in actions


@pytest.mark.real_toc
def test_a_set_without_a_prepare_cache_is_still_routed_by_navigate(tmp_path, monkeypatch, capsys):
    """A reviewed view, path-connected or recipe set has no cache files for the TOC search to list, so a
    question still reaches it through Jev's navigate: its candidate is read and shown."""
    note = tmp_path / "view-note.md"
    note.write_text("the made up answer is seven\n")
    asked = []

    def fake_memory(req):
        asked.append((req["action"], req.get("pointer")))
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "viewset"}]}
        if req["action"] == "navigate" and req["pointer"] == "viewset":
            return {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": str(note)}]}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({str(note): 0.9}, set(), None, {}))

    assert ask.lookup("what is the answer?", "alice", tmp_path / "state") == 0
    assert ("navigate", "viewset") in asked
    assert str(note) in capsys.readouterr().out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


def _three_candidate_lookup(sdir):
    for rel in ("r1/a.md", "r2/b.md", "r3/c.md"):
        (sdir / rel).parent.mkdir(parents=True, exist_ok=True)
        (sdir / rel).write_text("the ans is here\n")
    ask.log(sdir, "lookup", question="q", top=[
        {"score": 0.9, "path": str(sdir / "r1/a.md"), "pointer": "p1", "possible": False},
        {"score": 0.8, "path": str(sdir / "r2/b.md"), "pointer": "p2", "possible": False},
        {"score": 0.6, "path": str(sdir / "r3/c.md"), "pointer": "p3", "possible": True}])


def _ready_memory(calls, sdir):
    def fake_memory(req):
        calls.append(req)
        if req["action"] == "search":
            raise AssertionError("a file ask() ranked never goes back through search")
        if req["action"] == "cached":
            return {"status": "cache-miss"}
        if req["action"] == "open":
            return {"status": "ok", "attemptId": "a-" + req["pointer"]}
        if req["action"] == "assist":
            return {"status": "ready", "approvalTicket": "tix-" + req["attemptId"][2:],
                    "passages": [{"sourceId": "s", "reviewedText": "the ans is here"}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        if req["action"] == "sources":
            path = {"p1": "r1/a.md", "p2": "r2/b.md", "p3": "r3/c.md"}[req["pointer"]]
            return {"status": "ok", "sources": [{"sourceId": "s", "originalPath": str(sdir / path),
                                                  "contentSHA": ask.sha256_file(sdir / path)}]}
        raise AssertionError(req)
    return fake_memory


@pytest.mark.parametrize("kw,pointer,path", [
    ({"rank": 2}, "p2", "r2/b.md"),
    ({"rank": 3}, "p3", "r3/c.md"),            # possible tier, picked by the lead
    ({"file": "r2/b.md"}, "p2", "r2/b.md"),
    ({"file": "c.md"}, "p3", "r3/c.md"),       # bare file name, unique
])
def test_approve_any_listed_candidate_uses_its_own_pointer(tmp_path, monkeypatch, kw, pointer, path):
    _three_candidate_lookup(tmp_path)
    kw = {k: str(tmp_path / v) if str(v).startswith("r") else v for k, v in kw.items()}
    calls = []
    monkeypatch.setattr(ask, "memory", _ready_memory(calls, tmp_path))
    assert ask.approve("alice", "q", "ans", tmp_path, **kw) == 0
    assert [c["pointer"] for c in calls if c["action"] == "open"] == [pointer]
    assert next(c for c in calls if c["action"] == "approve")["ticket"] == "tix-" + pointer
    rec = json.loads((tmp_path / "approvals.jsonl").read_text().splitlines()[-1])
    assert rec["pointer"] == pointer and rec["file"] == str(tmp_path / path)


def test_approve_default_still_uses_rank_1(tmp_path, monkeypatch):
    _three_candidate_lookup(tmp_path)
    calls = []
    monkeypatch.setattr(ask, "memory", _ready_memory(calls, tmp_path))
    assert ask.approve("alice", "q", "ans", tmp_path) == 0
    assert [c["pointer"] for c in calls if c["action"] == "open"] == ["p1"]


@pytest.mark.parametrize("kw,needle", [({"rank": 4}, "out of range"), ({"rank": 0}, "out of range"),
                                       ({"file": "/nope.md"}, "matches 0")])
def test_approve_bad_pick_refuses_without_calling_memory(tmp_path, monkeypatch, capsys, kw, needle):
    _three_candidate_lookup(tmp_path)
    monkeypatch.setattr(ask, "memory", lambda req: (_ for _ in ()).throw(AssertionError(req)))
    assert ask.approve("alice", "q", "ans", tmp_path, **kw) == 1
    assert needle in capsys.readouterr().out


def test_approve_cli_parses_rank_flag(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(ask, "approve", lambda p, q, a, s, **kw: seen.update(q=q, a=a, **kw) or 0)
    monkeypatch.setattr(ask, "state_dir", lambda p: tmp_path)
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "alice", "--approve", "q", "ans", "--rank", "2"])
    assert ask.main() == 0
    assert seen == {"q": "q", "a": "ans", "rank": 2, "file": None}


def _same_name_memory(calls, main, analysis):
    """Two README.md files in one pointer; assist cites whichever source it is asked for."""
    def fake_memory(req):
        calls.append(req)
        if req["action"] == "cached":
            return {"status": "cache-miss"}
        if req["action"] == "open":
            return {"status": "ok", "attemptId": "a1"}
        if req["action"] == "assist":
            ref = req["references"][-1]
            text = {"s-main": "weekly range 2.10-2.40 ans", "s-analysis": "# CLOV - Analysis Log"}
            return {"status": "ready", "approvalTicket": "tix",
                    "passages": [{"sourceId": ref["sourceId"], "reviewedText": text[ref["sourceId"]]}]}
        if req["action"] == "sources":
            return {"status": "ok", "sources": [
                {"sourceId": "s-analysis", "originalPath": str(analysis), "contentSHA": ask.sha256_file(analysis)},
                {"sourceId": "s-main", "originalPath": str(main), "contentSHA": ask.sha256_file(main)}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        raise AssertionError(req)
    return fake_memory


def _same_name_files(tmp_path):
    main, analysis = tmp_path / "clov/README.md", tmp_path / "clov/analysis/README.md"
    analysis.parent.mkdir(parents=True)
    main.write_text("weekly range 2.10-2.40 ans\n")
    analysis.write_text("# CLOV - Analysis Log\n")
    return main, analysis


@pytest.mark.parametrize("by_file", [False, True])
def test_approve_same_name_file_saves_picked_files_evidence(tmp_path, monkeypatch, by_file):
    main, analysis = _same_name_files(tmp_path)
    ask.log(tmp_path, "lookup", question="q", top=[
        {"score": 0.9, "path": str(main), "pointer": "p", "possible": False},
        {"score": 0.7, "path": str(analysis), "pointer": "p", "possible": True}])
    calls = []
    monkeypatch.setattr(ask, "memory", _same_name_memory(calls, main, analysis))
    kw = {"file": str(main)} if by_file else {"rank": 1}
    assert ask.approve("alice", "q", "ans", tmp_path, **kw) == 0
    ev = next(c for c in calls if c["action"] == "approve")["evidence"]
    assert [e["sourceId"] for e in ev] == ["s-main"]


def test_approve_refuses_when_picked_file_has_no_line_for_the_answer(tmp_path, monkeypatch, capsys):
    main, analysis = _same_name_files(tmp_path)
    ask.log(tmp_path, "lookup", question="q", top=[
        {"score": 0.9, "path": str(main), "pointer": "p", "possible": False}])
    calls = []
    monkeypatch.setattr(ask, "memory", _same_name_memory(calls, main, analysis))
    assert ask.approve("alice", "q", "zeppelin", tmp_path, rank=1) == 1
    assert not any(c["action"] == "approve" for c in calls)
    assert f"no line in {main} shares a word with the answer" in capsys.readouterr().out


def test_approve_hand_picked_file_changed_since_connect_is_refused(tmp_path, monkeypatch, capsys):
    _three_candidate_lookup(tmp_path)
    calls, fake = [], None
    fake = _ready_memory(calls, tmp_path)
    monkeypatch.setattr(ask, "memory", lambda r: {"status": "ok", "sources": [
        {"sourceId": "s", "originalPath": str(tmp_path / "r2/b.md"), "contentSHA": "old"}]}
        if r["action"] == "sources" else fake(r))
    assert ask.approve("alice", "q", "ans", tmp_path, rank=2) == 1
    assert not any(c["action"] in ("open", "approve") for c in calls)
    assert "stale" in capsys.readouterr().out


def test_reviewed_dataset_copy_from_its_own_pointer_is_kept(tmp_path, monkeypatch, capsys):
    """A reviewed dataset's pointer answers with its prepared copy under
    .local/retrieval-datasets/. That copy is the answer; the read list must not drop it
    for where it lives (it used to vanish, leaving only weaker hits)."""
    copy = tmp_path / ".local" / "retrieval-datasets" / "brain-reviewed" / "operations-08.txt"
    copy.parent.mkdir(parents=True)
    copy.write_text("enqueue returning nothing means it was not sent\n")
    other = tmp_path / "other.md"
    other.write_text("something else\n")

    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "brain-reviewed"}, {"pointer": "skills"}]}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {
        str(other if ptr == "skills" else copy): {"pass": True, "sha256": ask.sha256_file(other if ptr == "skills" else copy)}})
    monkeypatch.setattr(ask, "candidate_files", lambda *a, **k: [
        ("skills", str(other), {"sha256": ask.sha256_file(other)}),
        ("brain-reviewed", str(copy), {"sha256": ask.sha256_file(copy)})])
    monkeypatch.setattr(ask.toc_search, "run", lambda *a, **k: ([str(other), str(copy)], [], {}))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({str(copy): 0.99, str(other): 0.5}, set(), None, {}))
    rc = ask.lookup("when enqueue returns nothing was it sent?", "alice", tmp_path / "state")

    assert rc == 0
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip() and l.strip()[0].isdigit()]
    assert str(copy) in lines[0] and "[brain-reviewed]" in lines[0]


def test_a_malformed_principal_is_refused_before_any_state_is_touched(monkeypatch, tmp_path, capsys):
    # Live 2026-09-27: "businessfi " (trailing space) had made a second, empty state folder.
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path))
    for bad in ("businessfi ", "x/../primary"):
        monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", bad, "a question"])
        assert ask.main() == 2
        assert "invalid --principal" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_add_under_an_uncalibrated_judge_saves_nothing(tmp_path, monkeypatch, capsys):
    """--add reaches send_approval, the one place every save passes through; it refuses there."""
    import judge_profile
    monkeypatch.setattr(judge_profile, "PROFILE", judge_profile.load("laya"))
    src_file = tmp_path / "src.txt"
    src_file.write_text("hello source\n")
    approvals = []

    def fake_memory(req):
        if req["action"] == "approve":
            approvals.append(req)
            return {"status": "saved"}
        if req["action"] == "panel":
            return {"pointers": []}
        if req["action"] == "connect" and not req.get("reviewed"):
            return {"status": "preparation-required", "sources": [{"path": req["sources"][0]["path"], "sha256": "deadbeef"}]}
        if req["action"] == "connect":
            return {"status": "registered", "sources": [{"id": "file:abc123", "originalPath": req["sources"][0]["path"]}]}
        if req["action"] == "search":
            return {"status": "ready", "approvalTicket": "tix", "passages": [{"sourceId": "s", "reviewedText": "answer text"}]}
        return {"status": "ok"}

    monkeypatch.setattr(ask, "memory", fake_memory)
    ask.add_manual("alice", "which pointers hold the example agent brain", "The answer body.", str(src_file), tmp_path)
    assert approvals == [] and "uncalibrated" in capsys.readouterr().out
    assert not (tmp_path / "approvals.jsonl").exists()
