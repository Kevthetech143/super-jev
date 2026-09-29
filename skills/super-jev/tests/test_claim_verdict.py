#!/usr/bin/env python3
"""ask.py --claim: per-file verdicts ride the listwise Jev call and plain code combines them.

    python3 -m pytest skills/super-jev/tests/test_claim_verdict.py -q
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ask  # noqa: E402
import judges  # noqa: E402


def _f(tmp_path, name, text, age_days=0):
    p = tmp_path / name
    p.write_text(text)
    t = 1_780_000_000 - age_days * 86400
    os.utime(p, (t, t))
    return str(p)


def rec(verdict, prob, line=None, line_no=None):
    return {"verdict": verdict, "prob": prob, "line": line, "line_no": line_no}


def test_sure_true_quotes_the_proof_line(tmp_path):
    a = _f(tmp_path, "a.md", "x")
    b = _f(tmp_path, "b.md", "y")
    word, lines = ask.claim_verdict({a: rec("supported", 0.98, "limit is 255", 7), b: rec("not_stated", 0.99)}, [a, b])
    assert word == "TRUE" and lines[0] == "TRUE (0.98)" and a in lines[1]
    assert 'line 7: "limit is 255"' in lines[2] and "Other files read: b.md" in lines[-1]


def test_files_that_disagree_are_a_conflict_newest_first(tmp_path):
    old = _f(tmp_path, "pr-5.md", "x", age_days=300)
    new = _f(tmp_path, "protocol.md", "y", age_days=1)
    word, lines = ask.claim_verdict({old: rec("contradicted", 0.95), new: rec("supported", 0.99)}, [old, new])
    assert word == "CONFLICT" and "newest says TRUE" in lines[0]
    assert new in lines[1] and old in lines[2]


def test_under_the_line_is_unsure_never_a_verdict(tmp_path):
    a = _f(tmp_path, "a.md", "x")
    word, lines = ask.claim_verdict({a: rec("contradicted", 0.84)}, [a])
    assert word == "UNSURE" and "0.84" in lines[0] and a in lines[1]


def test_all_not_stated_is_not_found_in_the_files_read(tmp_path):
    a = _f(tmp_path, "a.md", "x")
    word, lines = ask.claim_verdict({a: rec("not_stated", 1.0)}, [a])
    assert word == "NOT FOUND" and "1 file(s) I read" in lines[0]
    assert ask.claim_verdict({}, [])[0] == "NOT FOUND"


def test_partial(tmp_path):
    a = _f(tmp_path, "a.md", "x")
    b = _f(tmp_path, "b.md", "y")
    word, lines = ask.claim_verdict({a: rec("partly", 0.93), b: rec("partly", 0.95)}, [a, b])
    assert word == "PARTIAL" and len(lines) == 3


def test_answers_are_read_per_file_with_the_picked_line(tmp_path):
    a = _f(tmp_path, "a.md", "# Title\nThe choice limit is 255 options.\n")
    files = {a: open(a).read()}
    qs, lines = ask.claim_questions("A choice can have 255 options.", [a], files)
    assert set(qs) == {"verdict_1", "line_1"} and "`file_1.text`" in qs["verdict_1"]["instructions"]
    answers = {"verdict_1": {"choice": "supported", "probabilities": {"supported": 0.97}},
               "line_1": {"choice": "L1", "probabilities": {"L1": 0.9}}}
    got = ask.read_claim_answers(answers, [a], lines)[a]
    assert got == {"verdict": "supported", "prob": 0.97, "line": "The choice limit is 255 options.", "line_no": 2}


def test_listwise_carries_the_claim_questions_in_the_same_call(tmp_path, monkeypatch):
    a = _f(tmp_path, "a.md", "The choice limit is 255 options.\n")
    calls = []

    def fake(state, questions):
        calls.append(set(questions))
        return {"answers": {"pick": {"choice": "file_1", "probabilities": {"file_1": 0.95}},
                            "verdict_1": {"choice": "supported", "probabilities": {"supported": 0.96}},
                            "line_1": {"choice": "L1", "probabilities": {"L1": 0.9}}}}
    monkeypatch.setattr(ask, "jev_choice", fake)
    monkeypatch.setitem(ask._CLAIM, "text", "A choice can have 255 options.")
    ask._STAGE.clear()
    assert ask.judge_listwise("A choice can have 255 options.", [a]) == (a, 0.95)
    assert len(calls) == 1 and {"pick", "verdict_1", "line_1"} <= calls[0]
    assert ask._STAGE["claim_files"][a]["verdict"] == "supported"
    monkeypatch.setitem(ask._CLAIM, "text", None)
    ask.judge_listwise("q", [a])
    assert calls[-1] == {"pick"}  # a normal ask is unchanged


def test_a_saved_verdict_drops_when_its_proof_file_changes(tmp_path):
    a = _f(tmp_path, "a.md", "The choice limit is 255 options.\n")
    sdir = tmp_path / "state"
    ask.claim_cache_put(sdir, "A choice can have 255 options.", "TRUE", a, rec("supported", 0.97, "x", 1))
    assert ask.claim_cache_get(sdir, "a choice can have 255 options")["verdict"] == "TRUE"
    Path(a).write_text("changed\n")
    assert ask.claim_cache_get(sdir, "A choice can have 255 options.") is None
    assert ask.claim_cache_get(sdir, "A choice can have 255 options.") is None


def test_symbols_keep_claims_apart_in_the_saved_verdicts():
    assert ask.claim_key("balance is -50") != ask.claim_key("balance is 50")
    assert ask.claim_key("latency is < 200 ms") != ask.claim_key("latency is > 200 ms")
    assert ask.claim_key("Jev is  Deterministic.") == ask.claim_key("jev is deterministic")


def test_line_choices_stay_under_their_budget(tmp_path):
    a = _f(tmp_path, "a.md", "\n".join(f"line number {i} " + "x" * 150 for i in range(60)))
    qs, lines = ask.claim_questions("s", [a], {a: open(a).read()})
    assert sum(len(v) for v in qs["line_1"]["criteria"].values()) <= ask.CLAIM_LINE_BUDGET + 100


def test_a_too_big_claim_call_retries_without_line_picks(tmp_path, monkeypatch):
    a = _f(tmp_path, "a.md", "The choice limit is 255 options.\n")
    calls = []

    def fake(state, questions):
        calls.append(set(questions))
        if any(k.startswith("line_") for k in questions):
            raise judges.TooBig("evidence exceeds the 32,768-token ceiling")
        return {"answers": {"pick": {"choice": "file_1", "probabilities": {"file_1": 0.95}},
                            "verdict_1": {"choice": "supported", "probabilities": {"supported": 0.96}}}}
    monkeypatch.setattr(ask, "jev_choice", fake)
    monkeypatch.setitem(ask._CLAIM, "text", "s")
    ask._STAGE.clear()
    assert ask.judge_listwise("s", [a]) == (a, 0.95)
    assert len(calls) == 2 and ask._STAGE["claim_files"][a]["line"] is None


def test_a_reply_without_verdicts_is_not_read_as_not_found(tmp_path, monkeypatch):
    a = _f(tmp_path, "a.md", "text\n")
    monkeypatch.setattr(ask, "jev_choice", lambda st, q: {"answers": {"pick": {"choice": "none",
                                                                                "probabilities": {"none": 0.5}}}})
    monkeypatch.setitem(ask._CLAIM, "text", "s")
    ask._STAGE.clear()
    ask.judge_listwise("s", [a])
    assert "claim_files" not in ask._STAGE


def test_line_number_counts_from_the_shown_passage(tmp_path):
    text = "## Setup\nfirst\n" + "filler\n" * 10 + "## Setup\nThe limit is 255 options.\n"
    a = _f(tmp_path, "a.md", text)
    shown = text[text.rindex("## Setup"):]
    answers = {"verdict_1": {"choice": "supported", "probabilities": {"supported": 0.97}},
               "line_1": {"choice": "L1", "probabilities": {"L1": 0.9}}}
    got = ask.read_claim_answers(answers, [a], {"file_1": ["## Setup", "The limit is 255 options."]}, {a: shown})[a]
    assert got["line_no"] == 13


def test_a_timeout_in_claim_mode_is_not_retried(tmp_path, monkeypatch):
    a = _f(tmp_path, "a.md", "text\n")
    calls = []

    def fake(state, questions):
        calls.append(1)
        raise RuntimeError("could not reach TypeSafe: timed out")
    monkeypatch.setattr(ask, "jev_choice", fake)
    monkeypatch.setitem(ask._CLAIM, "text", "s")
    ask._STAGE.clear()
    assert ask.judge_listwise("s", [a]) == (None, None) and len(calls) == 1


def test_claims_file_checks_every_statement(tmp_path, monkeypatch, capsys):
    f = tmp_path / "report-claims.txt"
    f.write_text("# worker report\n- The limit is 255.\n\n2. Tests pass in test_x.py.\n")
    seen = []
    monkeypatch.setattr(ask, "resolve_principal", lambda argv: ("p", argv))
    monkeypatch.setattr(ask, "state_dir", lambda p: tmp_path / "s")
    monkeypatch.setattr(ask, "lookup", lambda q, p, s: seen.append((q, ask._CLAIM["text"])) or 0)
    monkeypatch.setattr(sys, "argv", ["ask.py", "--claims-file", str(f), "--claim", "One more."])
    assert ask._main() == 0
    assert [q for q, _ in seen] == ["The limit is 255.", "Tests pass in test_x.py.", "One more."]
    assert all(q == c for q, c in seen) and ask._CLAIM["text"] is None


def test_claims_file_is_capped(tmp_path, monkeypatch, capsys):
    f = tmp_path / "many.txt"
    f.write_text("\n".join(f"Statement {i}." for i in range(ask.MAX_CLAIMS + 1)))
    monkeypatch.setattr(ask, "resolve_principal", lambda argv: ("p", argv))
    monkeypatch.setattr(ask, "state_dir", lambda p: tmp_path / "s")
    monkeypatch.setattr(ask, "lookup", lambda q, p, s: (_ for _ in ()).throw(AssertionError("no lookup")))
    monkeypatch.setattr(sys, "argv", ["ask.py", "--claims-file", str(f)])
    assert ask._main() == 2 and "at most" in capsys.readouterr().out


def test_the_line_that_proves_it_is_offered_even_when_long_and_late(tmp_path):
    # Live miss (2026-09-27): the rule sat ~450 chars into a long paragraph line after ~25 other
    # lines; choices showed each line's first 160 chars in file order, so Jev picked a wrong line.
    filler = "\n".join(f"Setup note {i} about folders and pointers " + "x" * 150 for i in range(40))
    rule = ("Every agent should see the fleet's shared sets, not only its own brain. " + "y" * 350 +
            " an unmarked pointer is refused, and one with any source in documents or profile cannot be marked.")
    a = _f(tmp_path, "a.md", filler + "\n" + rule + "\n")
    qs, lines = ask.claim_questions("a pointer with a source in documents or profile cannot be marked",
                                    [a], {a: open(a).read()})
    assert rule in lines["file_1"]
    shown = qs["line_1"]["criteria"][f"L{lines['file_1'].index(rule) + 1}"]
    assert "cannot be marked" in shown and len(shown) <= ask.CLAIM_LINE_CHARS


def test_a_proof_line_past_the_sixtieth_line_is_still_offered(tmp_path):
    filler = "\n".join(f"Unrelated setup note number {i} here" for i in range(120))
    a = _f(tmp_path, "a.md", filler + "\nThe choice limit is 255 options per question.\n")
    qs, lines = ask.claim_questions("A choice can have 255 options.", [a], {a: open(a).read()})
    assert "The choice limit is 255 options per question." in lines["file_1"]


def test_a_word_longer_than_the_window_does_not_crash_the_line_pick(tmp_path):
    # Live crash (2026-09-27): a line holding a long URL made the window drop a word it never added.
    url = "https://example.com/answers/" + "a" * 200  # the long word holds a statement word
    line = f"see {url} where the answer about choice questions reliability is stated " + "z " * 40
    a = _f(tmp_path, "a.md", line + "\n")
    qs, lines = ask.claim_questions("Jev answers choice questions more reliably", [a], {a: open(a).read()})
    shown = qs["line_1"]["criteria"]["L1"]
    assert "choice questions" in shown and len(shown) <= ask.CLAIM_LINE_CHARS


def test_one_failed_statement_does_not_drop_the_rest(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_lookup(q, principal, sdir):
        calls.append(q)
        if q == "bad":
            raise KeyError("x")
        return 0
    monkeypatch.setattr(ask, "lookup", fake_lookup)
    f = tmp_path / "c.txt"
    f.write_text("bad\ngood\n")
    monkeypatch.setattr(ask.sys, "argv", ["ask.py", "--principal", "me", "--claims-file", str(f)])
    rc = ask.main()
    assert calls == ["bad", "good"] and rc == 3
    assert "could not be checked" in capsys.readouterr().out
