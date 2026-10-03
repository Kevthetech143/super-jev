#!/usr/bin/env python3
"""Frozen contract tests (2026-09-30), contract item C1: `ask.py --json`.

With a leading --json, an ask, --claim, --status, --approve and --miss each print exactly one JSON
object (schema v: 1), do the same work, and exit with the same code as text mode (a crash exits 3).
An ask's outcome, files and skills are printed from one result by both modes; a claim verdict and
--status are recorded beside their text, and these tests hold the two together. Scores are left out
on purpose. Made-up company Quillbrook, made-up user "me"; no network, no real key, no Jev call.

    python3 -m pytest skills/super-jev/tests/test_ask_json.py -q
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_json", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)
ah = ask.auto_heal

KEY = "tsk_made_up_key_for_the_test_0123456789"
Q = "How long is the warranty?"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    ask._STAGE.clear()
    monkeypatch.setenv("TYPESAFE_API_KEY", KEY)
    monkeypatch.setenv("SUPERJEV_AUTO_CACHE", "0")  # a save is tested on its own
    yield
    ask._STAGE.clear()


@pytest.fixture
def notes(tmp_path):
    d = tmp_path / "Quillbrook Notes"  # a path with a space
    d.mkdir()
    (d / "warranty.md").write_text("# Warranty\n\nQuillbrook covers parts.\nThe warranty lasts 24 months from delivery.\n"
                                   "Claims go to support.\n")
    (d / "returns.md").write_text("# Returns\n\nItems come back within 30 days.\n")
    return d


def cands(*paths):
    return {"status": "candidates", "candidates": [{"score": 0.9 - i / 10, "originalPath": str(p)} for i, p in enumerate(paths)]}


def world(tmp_path, monkeypatch, pointers=("notes",), navigate=None, scores=None, lines=None, notes_by_path=None,
          heal="no-report", reports=None, cache=None, panel=None, extra=None):
    """A fake engine: `navigate(ptr)` answers routing, `scores` the content check, `lines` each file's best line."""
    sdir = tmp_path / "state"
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(sdir))
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(exist_ok=True)
    for name, rep in (reports or {}).items():
        (cache_dir / f"{name}-report.json").write_text(json.dumps(rep))
    monkeypatch.setattr(ask.prepare_bulk, "CACHE_DIR", cache_dir)

    def fake(req):
        if req["action"] == "cached":
            return {"status": "miss"}
        if req["action"] == "panel":
            return panel if panel is not None else {"pointers": [{"pointer": p} for p in pointers]}
        if req["action"] == "recipe":
            return {"status": "no-recipe"}
        if extra and req["action"] in extra:
            return extra[req["action"]](req)
        return navigate(req["pointer"])
    monkeypatch.setattr(ask, "memory", fake)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: (cache or {}).get(ptr, {}))
    monkeypatch.setattr(ask, "batch_jev", lambda: False)
    monkeypatch.setattr(ah, "reconnect_now", lambda ptr, principal, timeout=None: "no-report")
    monkeypatch.setattr(ah, "reconnect_recipe", lambda ptr, principal, memory=None: "no-recipe")
    monkeypatch.setattr(ah, "maybe_heal", lambda *a, **k: heal)
    monkeypatch.setattr(ah, "maybe_scan", lambda *a, **k: None)

    def confirm(q, paths):
        for p, n in (lines or {}).items():
            ask._STAGE.setdefault("checks", {})[p] = {"best_line": n}
        return dict(scores or {}), set(), None, dict(notes_by_path or {})
    monkeypatch.setattr(ask, "confirm", confirm)
    return sdir / "me"  # the principal's own state folder


def run(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "me", *argv])
    rc = ask.main()
    cap = capsys.readouterr()
    assert KEY not in cap.out + cap.err
    return rc, cap.out


def both(monkeypatch, capsys, *argv):
    """(text rc, text, json rc, the one object): the same request in both modes."""
    t_rc, text = run(monkeypatch, capsys, *argv)
    j_rc, raw = run(monkeypatch, capsys, "--json", *argv)
    assert raw.endswith("\n") and raw.count("\n") == 1, raw  # one line, one object, nothing else
    obj = json.loads(raw)
    assert isinstance(obj, dict) and obj["v"] == 1
    assert t_rc == j_rc  # the exit code is the work's, not the printer's
    assert '"score"' not in raw and '"pointer"' not in raw  # scores are left out on purpose
    return t_rc, text, j_rc, obj


def file_lines(text):
    return re.findall(r"^\s*(?:\d+\.\d{2}|saved)  (.+?)  \[", text, re.M)


def outcome_line(text):
    return text.splitlines()[0]


# --- an ask -----------------------------------------------------------------------------------


def test_found_is_one_object_and_text_prints_the_same_files(tmp_path, monkeypatch, capsys, notes):
    w, r = notes / "warranty.md", notes / "returns.md"
    world(tmp_path, monkeypatch, navigate=lambda p: cands(w, r), scores={str(w): 0.95, str(r): 0.9},
          lines={str(w): 4, str(r): 3})
    rc, text, _, obj = both(monkeypatch, capsys, Q)
    assert rc == 0 and obj["outcome"] == "found" and obj["next"] == "none"
    assert outcome_line(text) == f"OUTCOME: found - {obj['why']}"
    assert [f["path"] for f in obj["files"]] == file_lines(text) == [str(w), str(r)]  # rank order, spaces kept
    assert obj["files"][0] == {"path": str(w), "tier": "confirmed", "line": 4,
                               "text": "The warranty lasts 24 months from delivery."}
    assert obj["files"][1]["line"] == 3 and obj["files"][1]["text"] == "Items come back within 30 days."
    for key in ("unsearched", "left_out", "errors", "claim", "voice", "saved", "saved_now", "skills"):
        assert key not in obj  # fields are omitted when empty
    kinds = [json.loads(ln)["kind"] for ln in (tmp_path / "state" / "me" / "lookups.jsonl").read_text().splitlines()]
    assert kinds.count("lookup") == 2  # the JSON run did the work too, not just the printing


def test_a_line_that_holds_a_secret_is_never_quoted(tmp_path, monkeypatch, capsys, notes):
    w = notes / "warranty.md"
    w.write_text("# Warranty\nThe office key is ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2 + " for the shed.\n")
    world(tmp_path, monkeypatch, navigate=lambda p: cands(w), scores={str(w): 0.95}, lines={str(w): 2})
    _, _, _, obj = both(monkeypatch, capsys, Q)
    assert obj["files"][0]["line"] == 2 and "text" not in obj["files"][0]


def test_a_file_the_content_check_did_not_finish_is_unchecked(tmp_path, monkeypatch, capsys, notes):
    w = notes / "warranty.md"
    world(tmp_path, monkeypatch, navigate=lambda p: cands(w), notes_by_path={str(w): ask.INCONCLUSIVE})
    rc, text, _, obj = both(monkeypatch, capsys, Q)
    assert rc == 0 and obj["outcome"] == "found" and obj["files"][0]["tier"] == "unchecked"
    assert "inconclusive" in text


def test_found_but_one_set_failed_lists_it_as_not_searched(tmp_path, monkeypatch, capsys, notes):
    w = notes / "warranty.md"
    nav = lambda p: {"status": "error", "reason": "provider failed"} if p == "broken" else cands(w)
    world(tmp_path, monkeypatch, pointers=("broken", "notes"), navigate=nav, scores={str(w): 0.95})
    rc, text, _, obj = both(monkeypatch, capsys, Q)
    assert rc == 0 and obj["outcome"] == "found" and "partial: 1 set not searched" in obj["why"]
    assert obj["unsearched"] == [{"set": "broken", "state": "failed", "healing": False}]
    assert obj["errors"] == [{"set": "broken", "kind": "unknown"}]
    assert [f["path"] for f in obj["files"]] == file_lines(text) == [str(w)]


def test_found_with_a_refreshing_set_says_healing(tmp_path, monkeypatch, capsys, notes):
    w = notes / "warranty.md"
    nav = lambda p: {"status": "refresh-required"} if p == "old" else cands(w)
    rep = {"old": {"pointer": "old", "roots": [str(notes)], "principals": ["me"], "parts": []}}
    world(tmp_path, monkeypatch, pointers=("old", "notes"), navigate=nav, scores={str(w): 0.95}, heal="started",
          reports=rep)
    rc, _, _, obj = both(monkeypatch, capsys, Q)
    assert rc == 0 and obj["outcome"] == "found"
    assert obj["unsearched"] == [{"set": "old", "root": str(notes), "state": "stale", "healing": True}]
    assert "errors" not in obj


def test_skills_come_first_and_a_guess_is_marked(tmp_path, monkeypatch, capsys, notes):
    w = notes / "warranty.md"
    world(tmp_path, monkeypatch, navigate=lambda p: cands(w), scores={str(w): 0.95})
    monkeypatch.setattr(ask, "skill_question", lambda q: True)
    monkeypatch.setattr(ask, "skill_catalog", lambda q: [("deploy-helper", "/s/deploy/SKILL.md"),
                                                         ("misc " + ask.GUESS, "/s/misc/SKILL.md")])
    _, text, _, obj = both(monkeypatch, capsys, "which skill sets up the printer")
    assert obj["skills"] == [{"name": "deploy-helper", "path": "/s/deploy/SKILL.md", "guess": False},
                             {"name": "misc", "path": "/s/misc/SKILL.md", "guess": True}]
    assert text.count("skill  /s/") == 2 and "unverified local guess" in text
    assert "skills_off" not in obj


def test_skills_off_says_why_no_catalog_was_searched(tmp_path, monkeypatch, capsys, notes):
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "no-candidates"})  # conftest turns skills off
    _, _, _, obj = both(monkeypatch, capsys, "which skill sets up the printer")
    assert "SUPERJEV_SKILLS" in obj["skills_off"]
    _, _, _, plain = both(monkeypatch, capsys, Q)
    assert "skills_off" not in plain


@pytest.mark.parametrize("stdout,why", [
    ('{"status": "error", "candidates": [], "error": "Jev credential provider failed"}', "Jev credential provider failed"),
    ('{"status": "error", "reason": "not-set-up"}', "not-set-up"),
    ("Traceback: the skill search fell over", "exit 1: unreadable output: Traceback: the skill search fell over"),
])
def test_skills_off_says_when_the_catalog_search_failed(tmp_path, monkeypatch, capsys, notes, stdout, why):
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "no-candidates"})
    monkeypatch.setenv("SUPERJEV_SKILLS", "1")
    real = ask.subprocess.run

    def run_cmd(cmd, *a, **k):  # only the skills connector is faked
        if "skills" in cmd:
            return type("R", (), {"stdout": stdout, "returncode": 1})()
        return real(cmd, *a, **k)
    monkeypatch.setattr(ask.subprocess, "run", run_cmd)
    rc, _, _, obj = both(monkeypatch, capsys, "which skill sets up the printer")
    assert rc == 1 and obj["skills_off"] == f"no skill catalog was searched: {why}"
    _, _, _, plain = both(monkeypatch, capsys, Q)  # a question that is not about skills never says it
    assert "skills_off" not in plain


def test_a_saved_list_says_who_saved_it_and_that_it_was_saved_now(tmp_path, monkeypatch, capsys, notes):
    w = notes / "warranty.md"
    sdir = world(tmp_path, monkeypatch, navigate=lambda p: cands(w), scores={str(w): 0.95})
    monkeypatch.setenv("SUPERJEV_AUTO_CACHE", "1")
    monkeypatch.setenv("SUPERJEV_SAVE_AFTER", "1")
    monkeypatch.setattr(ask, "save_answer", lambda *a, **k: 0)
    rc, text, _, obj = both(monkeypatch, capsys, Q)
    assert rc == 0 and obj["saved_now"] is True and "Saved for next time" in text
    # the next ask is the saved list: no search, files from the record
    monkeypatch.setattr(ask, "memory", lambda req: {"status": "verified-cache-hit"} if req["action"] == "cached"
                        else {"pointers": [{"pointer": "notes"}]})
    ask.record_approver(sdir, ask.norm_q(Q), "auto-save", file=str(w), pointer="notes",
                        files=[{"score": 0.95, "path": str(w), "pointer": "notes", "sha": "x"}])
    monkeypatch.setattr(ask, "changed_saved_source", lambda rec, record: None)
    rc, text, _, obj = both(monkeypatch, capsys, Q)
    assert rc == 0 and obj["outcome"] == "found" and "saved_now" not in obj
    assert obj["saved"]["by"] == "auto" and "answer" not in obj["saved"] and obj["saved"]["date"]
    assert [f["path"] for f in obj["files"]] == file_lines(text) == [str(w)]


def test_a_saved_answer_carries_its_text(tmp_path, monkeypatch, capsys, notes):
    w = notes / "warranty.md"
    sdir = world(tmp_path, monkeypatch, navigate=lambda p: cands(w))
    monkeypatch.setattr(ask, "memory", lambda req: {"status": "verified-cache-hit", "answer": "24 months"}
                        if req["action"] == "cached" else {"pointers": [{"pointer": "notes"}]})
    ask.record_approver(sdir, ask.norm_q(Q), "principal:me", file=str(w), pointer="notes")
    monkeypatch.setattr(ask, "changed_saved_source", lambda rec, record: None)
    rc, text, _, obj = both(monkeypatch, capsys, Q)
    assert rc == 0 and obj["saved"]["by"] == "you" and obj["saved"]["answer"] == "24 months"
    assert "answer: 24 months" in text


def test_not_found_is_a_complete_search_with_the_voice_line(tmp_path, monkeypatch, capsys, notes):
    w, r = notes / "warranty.md", notes / "returns.md"
    sha = lambda p: ask.sha256_file(p)
    entry = lambda p: {"pass": True, "sha256": sha(p), "description": "a note"}
    world(tmp_path, monkeypatch, pointers=("a", "b"), navigate=lambda p: {"status": "no-candidates"},
          cache={"a": {str(w): entry(w)}, "b": {str(r): entry(r)}})
    rc, text, _, obj = both(monkeypatch, capsys, "find the note about the zeppelin hangar")
    assert rc == 1 and obj["outcome"] == "not-found" and obj["next"] == "connect"  # the folder that has it
    assert outcome_line(text).startswith(f"OUTCOME: not-found - {obj['why']}")
    assert obj["searched"] == {"sets": 2, "notes": 2}
    assert obj["voice"] == ask.VOICE_LINE and text.splitlines()[-1] == ask.VOICE_LINE
    for key in ("files", "unsearched", "left_out", "errors"):
        assert key not in obj


def test_not_supported_for_an_empty_question_asks_to_rephrase(tmp_path, monkeypatch, capsys, notes):
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "no-candidates"})
    rc, text, _, obj = both(monkeypatch, capsys, "   ")
    assert rc == 2 and obj["outcome"] == "not-supported" and obj["next"] == "rephrase"
    assert outcome_line(text).startswith(f"OUTCOME: not-supported - {obj['why']}")


# --- needs-setup ---------------------------------------------------------------------------------


def test_needs_setup_when_not_set_up(tmp_path, monkeypatch, capsys):
    world(tmp_path, monkeypatch, panel={"status": "error", "reason": "not-set-up"})
    rc, text, _, obj = both(monkeypatch, capsys, Q)
    assert rc == 4 and obj["outcome"] == "needs-setup" and obj["next"] == "setup"
    assert outcome_line(text).startswith(f"OUTCOME: needs-setup - {obj['why']}")
    assert "python3" not in json.dumps(obj)  # the app words no command; text keeps its own


def test_needs_setup_when_nothing_is_connected(tmp_path, monkeypatch, capsys):
    world(tmp_path, monkeypatch, pointers=(), navigate=lambda p: {"status": "no-candidates"})
    rc, _, _, obj = both(monkeypatch, capsys, Q)
    assert rc == 4 and obj["outcome"] == "needs-setup" and obj["next"] == "connect"
    assert "unsearched" not in obj and "python3" not in json.dumps(obj)


@pytest.mark.parametrize("heal,healing", [("started", True), ("in-progress", True), ("cooldown", False)])
def test_needs_setup_when_a_set_is_stale_and_says_if_it_is_healing(tmp_path, monkeypatch, capsys, notes, heal, healing):
    nav = lambda p: {"status": "preparation-required"} if p == "bench" else {"status": "no-candidates"}
    rep = {"bench": {"pointer": "bench", "roots": [str(notes)], "principals": ["me"], "parts": []}}
    world(tmp_path, monkeypatch, pointers=("bench", "notes"), navigate=nav, heal=heal, reports=rep)
    rc, text, _, obj = both(monkeypatch, capsys, Q)
    assert rc == 4 and obj["outcome"] == "needs-setup" and obj["next"] == "refresh"
    assert outcome_line(text).startswith(f"OUTCOME: needs-setup - {obj['why']}")
    assert obj["unsearched"] == [{"set": "bench", "root": str(notes), "state": "unprepared", "healing": healing}]
    assert "errors" not in obj and "not-found" not in json.dumps(obj)  # never a complete not-found


def test_a_split_part_is_one_set_under_its_base_name(tmp_path, monkeypatch, capsys, notes):
    rep = {"big": {"pointer": "big", "roots": [str(notes)], "principals": ["me"],
                   "parts": [{"pointer": "big", "count": 250, "connected": True},
                             {"pointer": "big-2", "count": 40, "connected": True}]}}
    world(tmp_path, monkeypatch, pointers=("big", "big-2"), reports=rep,
          navigate=lambda p: {"status": "refresh-required"} if p == "big-2" else {"status": "no-candidates"})
    _, _, _, obj = both(monkeypatch, capsys, Q)
    assert [u["set"] for u in obj["unsearched"]] == ["big"] and obj["unsearched"][0]["root"] == str(notes)


def test_left_out_files_name_where_they_are_and_the_way_in(tmp_path, monkeypatch, capsys, notes):
    docs = tmp_path / "documents"
    docs.mkdir()
    held = [docs / "card.md", docs / "login.md"]
    for p in held:
        p.write_text("warranty notes\n")
    rep = {"notes": {"pointer": "notes", "roots": [str(notes)], "principals": ["me"], "parts": [],
                     "held": [[str(p), "card/password"] for p in held]}}
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "no-candidates"}, reports=rep)
    rc, text, _, obj = both(monkeypatch, capsys, "find the warranty card login")
    # the set was searched: files left out at setup are a note on a plain not-found, not a setup gap
    assert rc == 1 and obj["outcome"] == "not-found" and obj["next"] == "connect"
    assert obj["left_out"] == [{"what": "held back: it looks like it holds a password, key or card number",
                                "count": 2, "where": str(docs),
                                "way_in": "remove or move the flagged value, then re-run setup"}]
    assert "2 files skipped at setup" in obj["why"] and "Skipped at setup" in text


# --- errors ----------------------------------------------------------------------------------------


def test_an_error_names_the_failed_set_and_never_the_key(tmp_path, monkeypatch, capsys):
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "error", "reason": "provider failed"})
    rc, text, _, obj = both(monkeypatch, capsys, Q)
    assert rc == 3 and obj["outcome"] == "error" and obj["next"] == "none"
    assert outcome_line(text).startswith(f"OUTCOME: error - {obj['why']}")
    assert obj["errors"] == [{"set": "notes", "kind": "unknown"}]  # a kind survives only if the row carries it
    assert obj["unsearched"] == [{"set": "notes", "state": "failed", "healing": False}]


@pytest.mark.parametrize("kind,next_", [("auth-rejected", "key"), ("no-key", "key"), ("unreachable", "none")])
def test_an_error_row_that_carries_a_judge_kind_keeps_it(tmp_path, monkeypatch, capsys, kind, next_):
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "error", "reason": "TypeSafe said no", "kind": kind})
    rc, _, _, obj = both(monkeypatch, capsys, Q)
    assert rc == 3 and obj["errors"] == [{"set": "notes", "kind": kind}] and obj["next"] == next_


def test_a_crash_is_still_one_object_with_the_same_exit_code(tmp_path, monkeypatch, capsys):
    def boom(req):
        raise RuntimeError("the engine fell over")
    world(tmp_path, monkeypatch, navigate=boom)
    rc, _, _, obj = both(monkeypatch, capsys, Q)
    assert rc == 3 and obj["outcome"] == "error" and "the engine fell over" in obj["why"]


def test_a_held_secret_is_one_object_with_text_modes_exit_code(tmp_path, monkeypatch, capsys):
    world(tmp_path, monkeypatch)
    monkeypatch.setattr(ask, "memory", lambda req: (_ for _ in ()).throw(ask.SecretHeld("request holds a secret; not sent")))
    rc, text, _, obj = both(monkeypatch, capsys, Q)
    assert rc == 1 and obj["outcome"] == "error" and obj["why"] == "request holds a secret; not sent"


# --- --claim ---------------------------------------------------------------------------------------


def claim_world(tmp_path, monkeypatch, notes, verdicts, files=("warranty.md",)):
    paths = [notes / f for f in files]
    world(tmp_path, monkeypatch, navigate=lambda p: cands(*paths), scores={str(p): 0.95 for p in paths})
    monkeypatch.setenv("SUPERJEV_REPLAY", "1")  # each run is live: no saved verdict answers the second one
    monkeypatch.setattr(ask, "apply_near_twin_tiebreak", lambda q, top: top)

    def listwise(question, ps):
        ask._STAGE["claim_read"] = list(ps)
        ask._STAGE["claim_files"] = {str(notes / f): v for f, v in verdicts.items()}
        return str(paths[0]), 0.95
    monkeypatch.setattr(ask, "judge_listwise", listwise)
    return paths


def rec(verdict, prob, line, line_no):
    return {"verdict": verdict, "prob": prob, "line": line, "line_no": line_no}


@pytest.mark.parametrize("verdict,word,code", [("supported", "TRUE", 0), ("contradicted", "FALSE", 5)])
def test_claim_true_and_false_carry_their_proof(tmp_path, monkeypatch, capsys, notes, verdict, word, code):
    claim_world(tmp_path, monkeypatch, notes,
                {"warranty.md": rec(verdict, 0.97, "The warranty lasts 24 months from delivery.", 4)})
    rc, text, _, obj = both(monkeypatch, capsys, "--claim", "The warranty lasts 24 months.")
    assert rc == code and text.splitlines()[0].startswith(word) and obj["outcome"] == "found"
    claim = obj["claim"]
    assert claim["verdict"] == word and claim["read"] == 1
    assert claim["proof"] == {"path": str(notes / "warranty.md"), "line": 4,
                              "text": "The warranty lasts 24 months from delivery.",
                              "date": claim["proof"]["date"]}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", claim["proof"]["date"])
    assert f"Proof: {notes / 'warranty.md'}" in text and "line 4:" in text
    assert [f["path"] for f in obj["files"]] == file_lines(text) == [str(notes / "warranty.md")]
    assert obj["files"][0]["tier"] == "confirmed" and "possible:" not in text  # the tier is the label text prints


def test_claim_conflict_lists_both_files_newest_first(tmp_path, monkeypatch, capsys, notes):
    import os
    old, new = notes / "returns.md", notes / "warranty.md"
    os.utime(old, (1_600_000_000, 1_600_000_000))
    claim_world(tmp_path, monkeypatch, notes,
                {"warranty.md": rec("supported", 0.97, "24 months", 4), "returns.md": rec("contradicted", 0.95, "30 days", 3)},
                files=("warranty.md", "returns.md"))
    rc, text, _, obj = both(monkeypatch, capsys, "--claim", "The warranty lasts 24 months.")
    claim = obj["claim"]
    assert rc == 1 and claim["verdict"] == "CONFLICT" and text.splitlines()[0].startswith("CONFLICT")
    assert [(f["path"], f["says"], f["line"]) for f in claim["files"]] == [(str(new), "TRUE", 4), (str(old), "FALSE", 3)]
    assert claim["proof"] is None


def test_claim_not_found_says_how_many_files_were_read(tmp_path, monkeypatch, capsys, notes):
    claim_world(tmp_path, monkeypatch, notes, {"warranty.md": rec("not_stated", 0.99, None, None)})
    rc, text, _, obj = both(monkeypatch, capsys, "--claim", "The warranty covers dents.")
    assert rc == 1 and obj["claim"] == {"verdict": "NOT FOUND", "proof": None, "files": [], "read": 1}
    assert "NOT FOUND in the 1 file(s) I read" in text


def test_claim_not_found_with_nothing_connected_to_read_has_no_next_step(tmp_path, monkeypatch, capsys, notes):
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "no-candidates"})
    rc, text, _, obj = both(monkeypatch, capsys, "--claim", "The warranty covers dents.")
    assert rc == 1 and obj["next"] == "none" and obj["claim"]["verdict"] == "NOT FOUND" and obj["claim"]["read"] == 0
    assert text.startswith("NOT FOUND in the connected files")


def test_claim_unsure_when_the_check_did_not_run(tmp_path, monkeypatch, capsys, notes):
    w = notes / "warranty.md"
    world(tmp_path, monkeypatch, navigate=lambda p: cands(w), scores={str(w): 0.95})
    monkeypatch.setenv("SUPERJEV_REPLAY", "1")
    monkeypatch.setattr(ask, "judge_listwise", lambda q, ps: (None, None))
    rc, text, _, obj = both(monkeypatch, capsys, "--claim", "The warranty lasts 24 months.")
    assert rc == 3 and obj["claim"]["verdict"] == "UNSURE" and text.startswith("UNSURE")  # the check did not run


def test_claim_that_needs_setup_exits_like_an_ask(tmp_path, monkeypatch, capsys):
    world(tmp_path, monkeypatch, panel={"status": "error", "reason": "not-set-up"})
    rc, _, _, obj = both(monkeypatch, capsys, "--claim", "The warranty lasts 24 months.")
    assert rc == 4 and obj["outcome"] == "needs-setup" and obj["next"] == "setup"  # one exit table for asks and claims


def test_a_saved_claim_verdict_is_the_same_shape(tmp_path, monkeypatch, capsys, notes):
    paths = claim_world(tmp_path, monkeypatch, notes,
                        {"warranty.md": rec("supported", 0.97, "The warranty lasts 24 months from delivery.", 4)})
    monkeypatch.delenv("SUPERJEV_REPLAY")
    t_rc, text = run(monkeypatch, capsys, "--claim", "The warranty lasts 24 months.")  # computes and saves it
    j_rc, raw = run(monkeypatch, capsys, "--json", "--claim", "The warranty lasts 24 months.")  # served from the save
    obj = json.loads(raw)
    assert t_rc == j_rc == 0 and "saved" not in text.splitlines()[0] and obj["outcome"] == "found"
    assert obj["claim"]["verdict"] == "TRUE" and obj["claim"]["proof"]["path"] == str(paths[0])
    assert obj["claim"]["proof"]["line"] == 4 and '"prob"' not in raw
    assert obj["saved"]["by"] == "auto" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", obj["saved"]["date"])
    assert obj["claim"]["read"] == 0  # served from the save: no file was read


def test_json_takes_one_statement_at_a_time(tmp_path, monkeypatch, capsys):
    world(tmp_path, monkeypatch, navigate=lambda p: (_ for _ in ()).throw(AssertionError("no work")))
    rc, raw = run(monkeypatch, capsys, "--json", "--claim", "one.", "--claim", "two.")
    obj = json.loads(raw)
    assert rc == 2 and obj["outcome"] == "not-supported" and "one statement" in obj["why"]


# --- --status ----------------------------------------------------------------------------------------


def status_world(tmp_path, monkeypatch, notes, rows, reports=None):
    return world(tmp_path, monkeypatch, panel={"pointers": rows}, reports=reports or {})


def test_status_lists_sets_with_roots_and_folds_split_parts(tmp_path, monkeypatch, capsys, notes):
    rows = [{"pointer": "notes", "snapshotStatus": "available"},
            {"pointer": "big", "snapshotStatus": "available"},
            {"pointer": "big-2", "snapshotStatus": "refresh-required"},
            {"pointer": "odd", "snapshotStatus": "who-knows"}]
    rep = {"notes": {"pointer": "notes", "roots": [str(notes)], "principals": ["me"],
                     "parts": [{"pointer": "notes", "count": 2, "connected": True}]},
           "big": {"pointer": "big", "roots": ["/x/Big Folder", "/x/Other"], "principals": ["me"],
                   "parts": [{"pointer": "big", "count": 250, "connected": True},
                             {"pointer": "big-2", "count": 40, "connected": True}]}}
    status_world(tmp_path, monkeypatch, notes, rows, rep)
    rc, text, _, obj = both(monkeypatch, capsys, "--status")
    assert rc == 0 and obj["principal"] == "me" and obj["next"] == "refresh"
    assert obj["sets"] == [{"name": "notes", "roots": [str(notes)], "notes": 2, "state": "ready"},
                           {"name": "big", "roots": ["/x/Big Folder", "/x/Other"], "notes": 290, "state": "stale"},
                           {"name": "odd", "state": "failed"}]
    assert "notes: ready" in text and "big-2: stale" in text and "odd: who-knows" in text  # text stays per pointer


def test_status_says_refreshing_while_a_refresh_runs(tmp_path, monkeypatch, capsys, notes):
    rows = [{"pointer": "notes", "snapshotStatus": "available"}, {"pointer": "old", "snapshotStatus": "refresh-required"}]
    status_world(tmp_path, monkeypatch, notes, rows)
    monkeypatch.setattr(ah, "_lock_holder_alive", lambda lock: "me.old.lock" in str(lock))
    _, _, _, obj = both(monkeypatch, capsys, "--status")
    assert [(s["name"], s["state"]) for s in obj["sets"]] == [("notes", "ready"), ("old", "refreshing")]
    assert obj["next"] == "none"  # it is already being refreshed


def test_status_when_all_is_ready(tmp_path, monkeypatch, capsys, notes):
    status_world(tmp_path, monkeypatch, notes, [{"pointer": "notes", "snapshotStatus": "available"}])
    rc, _, _, obj = both(monkeypatch, capsys, "--status")
    assert rc == 0 and obj["next"] == "none" and obj["sets"] == [{"name": "notes", "state": "ready"}]


def test_status_nothing_connected_and_not_set_up(tmp_path, monkeypatch, capsys, notes):
    status_world(tmp_path, monkeypatch, notes, [])
    rc, _, _, obj = both(monkeypatch, capsys, "--status")
    assert rc == 0 and obj["next"] == "connect" and obj["sets"] == [] and obj["principal"] == "me"
    world(tmp_path, monkeypatch, panel={"status": "error", "reason": "not-set-up"})
    rc, _, _, obj = both(monkeypatch, capsys, "--status")
    assert rc == 1 and obj["next"] == "setup" and obj["sets"] == []


@pytest.mark.parametrize("panel,why", [
    ({"status": "error", "reason": "missing-dependency"}, "Connection status unavailable. Next: check"),
    ({"pointers": [42]}, "Connection status unavailable: malformed pointer metadata"),
])
def test_status_that_could_not_be_read_is_an_error_not_an_empty_list(tmp_path, monkeypatch, capsys, notes, panel, why):
    world(tmp_path, monkeypatch, panel=panel)
    rc, text, _, obj = both(monkeypatch, capsys, "--status")
    assert rc == 1 and obj["outcome"] == "error" and obj["why"].startswith(why) and "sets" not in obj
    assert obj["why"] in text  # the same line text mode printed


def test_status_that_crashes_is_an_error_with_exit_3(tmp_path, monkeypatch, capsys, notes):
    world(tmp_path, monkeypatch)

    def boom(req):
        raise RuntimeError("the panel fell over")
    monkeypatch.setattr(ask, "memory", boom)
    rc, raw = run(monkeypatch, capsys, "--json", "--status")
    obj = json.loads(raw)
    assert rc == 3 and obj["outcome"] == "error" and "the panel fell over" in obj["why"] and "sets" not in obj


# --- --approve and --miss ----------------------------------------------------------------------------


def seed(tmp_path, notes):
    sdir = tmp_path / "state" / "me"
    ask.log(sdir, "lookup", question=Q, top=[{"score": 0.9, "path": str(notes / "warranty.md"), "pointer": "notes",
                                                 "possible": False}])
    return sdir


def ready_memory(notes):
    sha = ask.sha256_file(notes / "warranty.md")
    return {"open": lambda req: {"status": "ok", "attemptId": "a-1"},
            "assist": lambda req: {"status": "ready", "approvalTicket": "tix-1",
                                   "passages": [{"sourceId": "s", "reviewedText": "The warranty lasts 24 months."}]},
            "approve": lambda req: {"status": "saved"},
            "sources": lambda req: {"status": "ok", "sources": [{"sourceId": "s", "originalPath": str(notes / "warranty.md"),
                                                                "contentSHA": sha}]}}


def test_approve_done_and_why(tmp_path, monkeypatch, capsys, notes):
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "no-candidates"}, extra=ready_memory(notes))
    seed(tmp_path, notes)
    monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None: ("CLEAN", 0.93))
    rc, text, _, obj = both(monkeypatch, capsys, "--approve", Q, "24 months", "--rank", "1")
    assert rc == 0 and obj["done"] is True and obj["why"] == "saved" and "outcome" not in obj
    assert text.strip().splitlines()[-1] == "approve: saved"


def test_approve_that_cannot_save_says_why(tmp_path, monkeypatch, capsys, notes):
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "no-candidates"})
    rc, text, _, obj = both(monkeypatch, capsys, "--approve", Q, "24 months")  # no earlier lookup of it
    assert rc == 1 and obj["done"] is False and obj["why"].startswith("no confirmed top candidate")
    assert obj["why"] == text.strip().splitlines()[-1]


def test_approve_refused_by_the_claim_check_says_so_without_a_score(tmp_path, monkeypatch, capsys, notes):
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "no-candidates"}, extra=ready_memory(notes))
    seed(tmp_path, notes)
    monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None: ("CLEAN", 0.42))
    t_rc, text = run(monkeypatch, capsys, "--approve", Q, "24 months", "--rank", "1")
    j_rc, raw = run(monkeypatch, capsys, "--json", "--approve", Q, "24 months", "--rank", "1")
    obj = json.loads(raw)
    assert t_rc == j_rc == 1 and "0.42" in text  # text mode still shows its number
    assert obj == {"v": 1, "done": False, "why": "the claim check did not pass it, so it was not saved"}
    assert not re.search(r"\d\.\d\d", raw)  # no score anywhere in the JSON


def test_approve_with_two_answers_says_its_usage(tmp_path, monkeypatch, capsys, notes):
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "no-candidates"})
    rc, raw = run(monkeypatch, capsys, "--json", "--approve", Q, "one", "two")
    obj = json.loads(raw)
    assert rc == 2 and obj["done"] is False and obj["why"].startswith("usage: --approve")


def test_miss_reports_what_it_removed(tmp_path, monkeypatch, capsys, notes):
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "no-candidates"},
          extra={"forget": lambda req: {"status": "forgotten", "pointers": ["me-saved-1"]}})
    rc, text, _, obj = both(monkeypatch, capsys, "--miss", Q, "it was in the returns note")
    assert rc == 0 and obj["done"] is True and obj["removed"] == ["me-saved-1"]
    assert obj["why"] == "the saved answer was removed" and text.strip().splitlines()[-1].startswith("un-saved:")


def test_miss_with_nothing_saved_is_not_done(tmp_path, monkeypatch, capsys, notes):
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "no-candidates"},
          extra={"forget": lambda req: {"status": "nothing"}})
    rc, _, _, obj = both(monkeypatch, capsys, "--miss", Q, "it was in the returns note")
    assert rc == 1 and obj["done"] is False and "no saved answer" in obj["why"] and "removed" not in obj


# --- the flag itself -----------------------------------------------------------------------------------


def test_the_flag_may_lead_or_follow_the_principal(tmp_path, monkeypatch, capsys, notes):
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "no-candidates"})
    monkeypatch.setattr(sys, "argv", ["ask.py", "--json", "--principal", "me", Q])
    assert ask.main() == 1 and json.loads(capsys.readouterr().out)["outcome"] == "not-found"
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "me", "--json", Q])
    assert ask.main() == 1 and json.loads(capsys.readouterr().out)["outcome"] == "not-found"


def test_json_inside_a_question_is_just_words(tmp_path, monkeypatch, capsys, notes):
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "no-candidates"})
    for argv in (["does --json work in the app"], ["--", "--json"]):
        rc, text = run(monkeypatch, capsys, *argv)
        assert text.startswith("OUTCOME: not-found")  # text mode, the question read literally


@pytest.mark.parametrize("argv", [["--add", Q, "24 months"], ["--followup"], ["--trace-show", "last"], ["--preflight"], []])
def test_json_refuses_what_it_does_not_cover_without_doing_it(tmp_path, monkeypatch, capsys, notes, argv):
    world(tmp_path, monkeypatch, navigate=lambda p: (_ for _ in ()).throw(AssertionError("no work")))
    monkeypatch.setattr(ask, "memory", lambda req: (_ for _ in ()).throw(AssertionError("no work")))
    rc, raw = run(monkeypatch, capsys, "--json", *argv)
    obj = json.loads(raw)
    assert rc == 2 and obj["v"] == 1 and obj["outcome"] == "not-supported" and obj["next"] == "rephrase"
    assert "--claim" in obj["why"] or argv == []


def test_dashes_with_no_question_name_what_is_missing_not_the_docstring(tmp_path, monkeypatch, capsys, notes):
    world(tmp_path, monkeypatch, navigate=lambda p: (_ for _ in ()).throw(AssertionError("no work")))
    rc, raw = run(monkeypatch, capsys, "--json", "--")
    obj = json.loads(raw)
    assert rc == 2 and obj["outcome"] == "not-supported" and obj["why"] == "a question is required"
    assert "Front door" not in raw


def test_a_crash_in_approve_or_miss_exits_3_and_is_not_done(tmp_path, monkeypatch, capsys, notes):
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "no-candidates"})
    monkeypatch.setattr(ask, "miss", lambda *a, **k: (_ for _ in ()).throw(IndexError("the miss list ran out")))
    rc, raw = run(monkeypatch, capsys, "--json", "--miss", Q)  # text mode lets it fall out, JSON exits 3
    obj = json.loads(raw)
    assert rc == 3 and obj["done"] is False and "IndexError" in obj["why"]


def test_miss_with_no_question_names_what_is_missing(tmp_path, monkeypatch, capsys, notes):
    world(tmp_path, monkeypatch, navigate=lambda p: {"status": "no-candidates"})
    rc, raw = run(monkeypatch, capsys, "--json", "--miss")
    obj = json.loads(raw)
    assert rc == 2 and obj["done"] is False and obj["why"] == "a question after --miss is required"


def test_crash_text_never_carries_a_secret_shaped_value(tmp_path, monkeypatch, capsys, notes):
    def boom(req):
        raise RuntimeError("password: hunter2hunter2xx")
    world(tmp_path, monkeypatch, navigate=boom)
    for argv in ((Q,), ("--json", Q)):
        monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "me", *argv])
        ask.main()
        assert "hunter2" not in capsys.readouterr().out  # the stderr traceback is separate, as before


def test_a_bad_principal_is_still_one_object(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ask.py", "--json", "--principal", "bad name", Q])
    assert ask.main() == 2
    obj = json.loads(capsys.readouterr().out)
    assert obj["v"] == 1 and obj["outcome"] == "not-supported" and "invalid --principal" in obj["why"]
