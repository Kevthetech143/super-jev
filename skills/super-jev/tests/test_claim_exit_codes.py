#!/usr/bin/env python3
"""ask.py --claim exits 0 only when a connected file proves the statement.

One exit table for every result: TRUE 0, FALSE 5, a check that did not run 3, and every other result
exits by how the search went: 1 searched fully, 2 refused input, 3 a set or the check failed, 4 setup
needed. That holds whether or not files were listed. A run of several statements exits with its highest
code. A statement that ends before any verdict prints the same OUTCOME line an ordinary ask prints, and
never a traceback. Made-up notes, a fake memory and a fake judge: no Jev, no network.

    python3 -m pytest skills/super-jev/tests/test_claim_exit_codes.py -q
"""
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ask  # noqa: E402

STATEMENT = "Hotel stays are capped at 300 dollars per night."
TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"  # built here so no token sits in the repo


class World:
    """A fake memory holding two made-up policy files, a fake content check, a fake judge."""

    def __init__(self, tmp_path, monkeypatch, nav="candidates"):
        self.sdir = tmp_path / "state"
        self.sdir.mkdir()
        notes = tmp_path / "notes"
        notes.mkdir()
        self.new = notes / "travel-policy.md"
        self.new.write_text("# Travel policy\nHotels: up to $220 per night unless pre-approved.\n")
        self.old = notes / "travel-policy-2019.md"
        self.old.write_text("# Travel policy 2019\nHotels: up to $300 per night.\n")
        os.utime(self.old, (1_500_000_000, 1_500_000_000))
        cache = {str(p): {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "pass": True}
                 for p in (self.new, self.old)}
        cdir = tmp_path / "cache"
        cdir.mkdir()
        (cdir / "notes-report.json").write_text(json.dumps({"pointer": "notes", "principals": ["me"],
                                                            "roots": [str(notes)]}))
        monkeypatch.setattr(ask.prepare_bulk, "CACHE_DIR", cdir)
        monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "unused"))
        self.nav, self.panel, self.sent = nav, {"pointers": [{"pointer": "notes"}]}, []
        monkeypatch.setattr(ask, "memory", self._memory)
        monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
        monkeypatch.setattr(ask, "load_cache_files", lambda ptr: cache if ptr == "notes" else {})
        monkeypatch.setattr(ask, "confirm", lambda q, ps: ({p: 0.95 for p in ps}, set(), None, {}))
        monkeypatch.setattr(ask, "claim_cache_put", lambda *a: None)
        monkeypatch.setattr(ask, "state_dir", lambda p: self.sdir)
        # A failed or stale set must not start a real refresh from a test.
        monkeypatch.setattr(ask.auto_heal, "maybe_heal", lambda *a, **k: "cooldown")
        monkeypatch.setattr(ask.auto_heal, "reconnect_now", lambda *a, **k: "changed")
        monkeypatch.setattr(ask.auto_heal, "last_refresh_error", lambda *a: None)
        self.monkeypatch = monkeypatch

    def second_set(self, answer):
        """Connect a second set, 'other', that always answers `answer`; 'notes' answers as before."""
        self.panel = {"pointers": [{"pointer": "notes"}, {"pointer": "other"}]}
        base = self._memory

        def memory(req):
            if ask.payload_has_secret(req):  # keep the real guard here too
                raise ask.SecretHeld("memory request contains a secret; not sent")
            if req.get("action") == "navigate-many":
                return {"status": "ok", "results": {p: self._answer() if p == "notes" else answer
                                                    for p in req["pointers"]}}
            if req.get("action") == "navigate" and req.get("pointer") != "notes":
                return answer
            return base(req)
        self.monkeypatch.setattr(ask, "memory", memory)

    def _answer(self):
        if self.nav == "none":
            return {"status": "no-candidates", "candidates": []}
        if self.nav == "error":
            return {"status": "error", "message": "boom"}
        if self.nav == "stale":
            return {"status": "preparation-required"}
        return {"status": "candidates", "candidates": [{"originalPath": str(p), "score": 0.9}
                                                       for p in (self.new, self.old)]}

    def _memory(self, req):
        if ask.payload_has_secret(req):  # the real memory() guard, kept: faking memory must not drop it
            raise ask.SecretHeld("memory request contains a secret; not sent")
        self.sent.append(json.dumps(req))
        if req["action"] == "cached":
            return {"status": "miss"}
        if req["action"] == "panel":
            return self.panel
        if req["action"] == "navigate-many":
            return {"status": "ok", "results": {p: self._answer() for p in req["pointers"]}}
        if req["action"] == "navigate":
            return self._answer()
        return {"status": "error"}

    def judge(self, verdicts):
        """verdicts: {path: (verdict, prob)}, or None when the judge returns nothing at all."""
        def fake(q, pool):
            if verdicts is None:
                ask._STAGE.pop("claim_files", None)
                return None, None
            ask._STAGE["claim_files"] = {str(p): {"verdict": v, "prob": pr, "line": "Hotels: up to $220", "line_no": 2}
                                         for p, (v, pr) in verdicts.items()}
            return (str(next(iter(verdicts))), 0.9) if verdicts else (None, None)
        self.monkeypatch.setattr(ask, "judge_listwise", fake)
        self.monkeypatch.setattr(ask, "claim_cache_get", lambda *a: None)

    def run(self, monkeypatch, capfd, *argv):
        monkeypatch.setattr(ask.sys, "argv", ["ask.py", "--principal", "me", *argv])
        rc = ask.main()
        o = capfd.readouterr()
        return rc, o.out, o.err


def claim(w, monkeypatch, capfd, text=STATEMENT):
    return w.run(monkeypatch, capfd, "--claim", text)


def test_a_true_claim_exits_0(tmp_path, monkeypatch, capfd):  # control: the old code agrees
    w = World(tmp_path, monkeypatch)
    w.judge({w.new: ("supported", 0.98)})
    rc, out, _ = claim(w, monkeypatch, capfd)
    assert out.startswith("TRUE (0.98)") and rc == 0


def test_a_false_claim_exits_5(tmp_path, monkeypatch, capfd):
    w = World(tmp_path, monkeypatch)
    w.judge({w.new: ("contradicted", 0.99)})
    rc, out, _ = claim(w, monkeypatch, capfd)
    assert out.startswith("FALSE (0.99)") and rc == 5


@pytest.mark.parametrize("word", ["UNSURE", "PARTIAL", "CONFLICT", "NOT_FOUND"])
def test_a_claim_no_file_settles_exits_1(tmp_path, monkeypatch, capfd, word):
    w = World(tmp_path, monkeypatch, nav="none" if word == "NOT_FOUND" else "candidates")
    if word == "UNSURE":
        w.judge({w.new: ("contradicted", 0.55)})
    elif word == "PARTIAL":
        w.judge({w.new: ("partly", 0.95)})
    elif word == "CONFLICT":
        w.judge({w.new: ("contradicted", 0.97), w.old: ("supported", 0.95)})
    else:
        w.judge({})
    rc, out, _ = claim(w, monkeypatch, capfd)
    assert out.startswith(word.replace("_", " ")) and rc == 1


@pytest.mark.parametrize("verdict,code", [("FALSE", 5), ("TRUE", 0)])
def test_saved_verdicts_exit_by_their_verdict(tmp_path, monkeypatch, capfd, verdict, code):
    w = World(tmp_path, monkeypatch)
    (w.sdir / "claim-verdicts.json").write_text(json.dumps({ask.claim_key(STATEMENT): {
        "claim": STATEMENT, "verdict": verdict, "path": str(w.new), "sha": hashlib.sha256(w.new.read_bytes()).hexdigest(),
        "prob": 0.99, "line": "Hotels: up to $220", "line_no": 2}}))
    rc, out, _ = claim(w, monkeypatch, capfd)
    assert out.startswith(f"{verdict} (0.99, saved") and rc == code


def test_a_failed_search_exits_3_and_says_it_was_incomplete(tmp_path, monkeypatch, capfd):
    w = World(tmp_path, monkeypatch, nav="error")
    w.judge({})
    rc, out, _ = claim(w, monkeypatch, capfd)
    first = out.splitlines()[0]
    assert rc == 3 and first.startswith("NOT FOUND")
    assert "incomplete" in first and "somewhere not connected" not in first


def test_a_stale_set_with_nothing_settled_exits_4(tmp_path, monkeypatch, capfd):
    w = World(tmp_path, monkeypatch, nav="stale")
    w.judge({})
    rc, out, _ = claim(w, monkeypatch, capfd)
    first = out.splitlines()[0]
    assert rc == 4 and first.startswith("NOT FOUND") and "incomplete" in first


# One rule for a claim nothing settles: it exits by how the search went, whether or not the judge left files
# listed. A healthy set plus a failed or stale one is the usual real setup, and the judge's confidence in the
# files it read must not change the exit code.
NOT_SETTLED = {
    "NOT FOUND (judge unsure the files lack it)": ({"new": ("not_stated", 0.60)}, "NOT FOUND"),
    "NOT FOUND (judge sure the files lack it)": ({"new": ("not_stated", 0.97), "old": ("not_stated", 0.97)}, "NOT FOUND"),
    "UNSURE": ({"new": ("contradicted", 0.55)}, "UNSURE"),
    "PARTIAL": ({"new": ("partly", 0.95)}, "PARTIAL"),
    "CONFLICT": ({"new": ("contradicted", 0.97), "old": ("supported", 0.95)}, "CONFLICT"),
}
OTHER_SET = {"failed": ({"status": "error", "message": "boom"}, 3),
             "stale": ({"status": "preparation-required"}, 4),
             "healthy": ({"status": "no-candidates", "candidates": []}, 1)}


@pytest.mark.parametrize("other", list(OTHER_SET))
@pytest.mark.parametrize("case", list(NOT_SETTLED))
def test_a_claim_nothing_settles_exits_by_how_the_search_went_even_with_files_listed(
        tmp_path, monkeypatch, capfd, case, other):
    answer, code = OTHER_SET[other]
    verdicts, word = NOT_SETTLED[case]
    w = World(tmp_path, monkeypatch)
    w.second_set(answer)
    w.judge({getattr(w, k): v for k, v in verdicts.items()})
    rc, out, _ = claim(w, monkeypatch, capfd)
    first = out.splitlines()[0]
    assert first.startswith(word) and rc == code
    if word == "NOT FOUND" and other != "healthy":
        assert "incomplete" in first  # the exit and the first line agree


@pytest.mark.parametrize("other", ["failed", "stale"])
@pytest.mark.parametrize("verdict,code", [("supported", 0), ("contradicted", 5)])
def test_a_settled_claim_keeps_its_own_code_on_a_partial_search(tmp_path, monkeypatch, capfd, verdict, code, other):
    # TRUE and FALSE name a proof line, so a set that was not searched does not change their code.
    w = World(tmp_path, monkeypatch)
    w.second_set(OTHER_SET[other][0])
    w.judge({w.new: (verdict, 0.98)})
    rc, out, _ = claim(w, monkeypatch, capfd)
    assert out.startswith(("TRUE", "FALSE")) and rc == code


@pytest.mark.parametrize("verdict,code", [("contradicted", 1), ("supported", 0)])
def test_files_skipped_at_setup_leave_an_unsettled_claim_unsure_not_needs_setup(
        tmp_path, monkeypatch, capfd, verdict, code):
    w = World(tmp_path, monkeypatch)
    w.judge({w.new: (verdict, 0.55 if verdict == "contradicted" else 0.98)})
    monkeypatch.setattr(ask, "skipped_for_question", lambda *a, **k: [("notes/hotels.pdf", "scanned image", "run ocr")])
    rc, out, _ = claim(w, monkeypatch, capfd)
    assert rc == code and out.startswith("UNSURE" if code else "TRUE")


def test_a_claim_the_judge_never_judged_exits_3(tmp_path, monkeypatch, capfd):
    # The same code also covers the rare case where every candidate was unreadable or held, which
    # is closer to "setup needed" (4) than to a failed check; it is accepted as 3.
    w = World(tmp_path, monkeypatch)
    w.judge(None)
    rc, out, _ = claim(w, monkeypatch, capfd)
    assert out.startswith("UNSURE: the true/false check did not run") and rc == 3


def test_a_statement_holding_a_secret_is_refused_cleanly(tmp_path, monkeypatch, capfd):
    w = World(tmp_path, monkeypatch)
    w.judge({w.new: ("supported", 0.98)})
    rc, out, err = claim(w, monkeypatch, capfd, f"The deploy token is {TOKEN}.")
    assert rc == 2
    assert out.startswith("OUTCOME: not-supported") and "not sent" in out
    assert "Traceback" not in out + err and TOKEN not in out + err
    assert not any(TOKEN in req for req in w.sent)


@pytest.mark.parametrize("case,code,line", [
    ("empty", 2, "OUTCOME: not-supported - empty statement"),
    ("too-long", 2, "OUTCOME: not-supported - statement too long"),
    ("nothing-connected", 4, "OUTCOME: needs-setup - nothing is connected yet"),
    ("not-set-up", 4, "OUTCOME: needs-setup - Super Jev is not set up yet"),
])
def test_a_claim_that_ends_before_any_verdict_says_why(tmp_path, monkeypatch, capfd, case, code, line):
    w = World(tmp_path, monkeypatch)
    w.judge({w.new: ("supported", 0.98)})
    if case == "nothing-connected":
        w.panel = {"pointers": []}
    elif case == "not-set-up":
        w.panel = {"reason": "not-set-up"}
    text = {"empty": "   ", "too-long": "x " * ask.MAX_QUESTION}.get(case, STATEMENT)
    rc, out, err = claim(w, monkeypatch, capfd, text)
    assert out.startswith(line) and rc == code and "Traceback" not in err


def test_a_claim_that_ends_before_any_verdict_starts_with_its_outcome_line(tmp_path, monkeypatch, capfd):
    # Output printed on the way (here the "join a shared set" hint) must not come before the OUTCOME line:
    # the first line of a claim's output is always the verdict word or the OUTCOME line.
    import share_pointers
    monkeypatch.setattr(share_pointers, "load_shared", lambda: ["team-notes"])
    w = World(tmp_path, monkeypatch)
    w.panel = {"pointers": []}
    rc, out, _ = claim(w, monkeypatch, capfd)
    lines = out.splitlines()
    assert rc == 4 and lines[0].startswith("OUTCOME: needs-setup - nothing is connected yet")
    assert any("shared set" in ln for ln in lines[1:])


def test_a_claim_that_ends_before_any_verdict_points_the_next_step_at_claim(tmp_path, monkeypatch, capfd):
    w = World(tmp_path, monkeypatch)
    rc, out, _ = claim(w, monkeypatch, capfd, "   ")
    assert rc == 2 and "--claim" in out.splitlines()[0] and "find the note about" not in out


def test_a_crash_keeps_what_the_claim_already_printed(tmp_path, monkeypatch, capfd):  # guards the buffering
    w = World(tmp_path, monkeypatch)
    w.judge({w.new: ("supported", 0.98)})

    def boom(*a, **k):
        print("printed before the crash")
        raise RuntimeError("boom")
    monkeypatch.setattr(ask, "claim_verdict", boom)
    rc, out, err = claim(w, monkeypatch, capfd)
    assert rc == 3 and "could not be checked" in out
    assert out.index("printed before the crash") < out.index("could not be checked")


@pytest.mark.parametrize("second,code", [("contradicted", 5), ("not_stated", 1)])
def test_claims_file_exits_with_its_highest_statement_code(tmp_path, monkeypatch, capfd, second, code):
    w = World(tmp_path, monkeypatch)
    plan = {"Hotels are capped at 220 dollars per night.": ("supported", 0.97), STATEMENT: (second, 0.99)}

    def judge(q, pool):
        v, p = plan[ask._CLAIM["text"]]
        ask._STAGE["claim_files"] = {str(w.new): {"verdict": v, "prob": p, "line": "Hotels: up to $220", "line_no": 2}}
        return str(w.new), p
    monkeypatch.setattr(ask, "judge_listwise", judge)
    monkeypatch.setattr(ask, "claim_cache_get", lambda *a: None)
    f = tmp_path / "claims.txt"
    f.write_text("\n".join(plan) + "\n")
    rc, out, _ = w.run(monkeypatch, capfd, "--claims-file", str(f))
    assert "TRUE (0.97)" in out and rc == code
