#!/usr/bin/env python3
"""--quote: the line that answers, quoted word for word from the files an ask found, with no model call.

Made-up company Acme and made-up user "me"; no network, no real key, no Jev call. The picker
(quote_answer.pick) is tested on its own, then through ask.py in text and --json modes.

    python3 -m pytest skills/super-jev/tests/test_quote_answer.py -q
"""
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import quote_answer as qa  # noqa: E402
import test_ask_json as tj  # noqa: E402  (its fake engine: world, cands, run)

ask = tj.ask


def note(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def pick(question, *paths, tier="confirmed"):
    files = [{"path": p, "tier": tier} for p in paths]
    return ask.quote_for(question, files)


DAEMON = ("# Acme helper quirks\n\n"
          "- The **sync daemon** exits after 600 seconds idle and frees its memory.\n"
          "- Logs rotate every night at 02:00.\n"
          "- The importer skips files larger than 2 MB.\n")
PORTS = ("# Acme dashboard\n\nThe dashboard shows orders and refunds for the Acme shop.\n\n"
         "## Owners\n\n- web view: Dana Reyes since 2024\n\n## Ports\n\n- web view: 8080\n- metrics exporter: 9100\n")


def test_a_number_question_quotes_the_line_with_the_number_and_its_line(tmp_path):
    p = note(tmp_path, "quirks.md", DAEMON)
    q = pick("how long does the sync daemon stay idle before it exits?", p)
    assert q == {"found": True, "text": "The sync daemon exits after 600 seconds idle and frees its memory.",
                 "path": p, "line": 3}
    assert qa.render(q) == f'Answer: "The sync daemon exits after 600 seconds idle and frees its memory."  -- {p} line 3'


def test_cleanup_drops_marks_only_never_words():
    assert qa.tidy("- The **sync daemon** exits after *600* seconds.") == "The sync daemon exits after 600 seconds."
    assert qa.tidy("## Ports") == "Ports"
    assert qa.tidy("> 1. Keep `X-Key` as is") == "Keep `X-Key` as is"


def test_a_heading_counts_for_the_rows_under_it(tmp_path):
    p = note(tmp_path, "dashboard.md", PORTS)
    q = pick("which port does the web view use?", p)  # "port" is only in the heading over the row
    assert q["found"] and q["text"] == "web view: 8080" and q["line"] == 11


def test_two_lines_equally_good_with_different_values_is_honest_not_found(tmp_path):
    p = note(tmp_path, "warranty.md", "# Acme warranty\n\n- The warranty on blenders lasts 24 months.\n"
                                      "- The warranty on toasters lasts 12 months.\n")
    q = pick("how long does the Acme warranty last?", p)
    assert q == {"found": False, "why": qa.NOT_FOUND, "top": p}
    assert qa.render(q) == f"I found related files but no line that answers this. Top file: {p}"


def test_two_files_stating_the_same_value_agree(tmp_path):
    a = note(tmp_path, "a.md", "# Acme returns\n\nReturns are accepted within 30 days of delivery.\n")
    b = note(tmp_path, "b.md", "# Acme returns FAQ\n\nAcme returns are accepted for 30 days.\n")
    q = pick("how many days do Acme returns allow?", a, b)
    assert q["found"] and "30 days" in q["text"]


def test_no_line_holds_the_asked_for_kind_of_value(tmp_path):
    p = note(tmp_path, "quirks.md", DAEMON)
    q = pick("who maintains the sync daemon?", p)  # no name anywhere: never quote a line without one
    assert not q["found"] and q["top"] == p


def test_wrapped_lines_of_one_paragraph_are_one_passage_cited_at_its_first_line(tmp_path):
    p = note(tmp_path, "deploy.md", "# Acme deploy\n\nMerged is not live. The running shop loads code from the\n"
                                    "BUNDLE folder (/opt/acme/bundle), not the repo.\n\nOther notes here.\n")
    q = pick("where does the running shop load code from?", p)
    assert q["found"] and q["line"] == 3
    assert q["text"] == "Merged is not live. The running shop loads code from the BUNDLE folder (/opt/acme/bundle), not the repo."


def test_which_x_is_answered_only_by_a_line_that_names_an_x(tmp_path):
    p = note(tmp_path, "link.md", "# Acme laptops link\n\nGoal: connect the Acme laptops to the office servers.\n\n"
                                  "We picked a mesh network after the other tries failed.\n")
    assert not pick("which tool connects the Acme laptops to the office servers?", p)["found"]
    p2 = note(tmp_path, "lic.md", "# Acme engine\n\n- Released 2026-01-02 by the Acme team.\n- License: Apache-2.0, free to use.\n")
    q = pick("what license is the Acme engine under?", p2)
    assert q["found"] and q["text"] == "License: Apache-2.0, free to use."


def test_files_that_do_not_hold_the_question_words_answer_nothing(tmp_path):
    p = note(tmp_path, "quirks.md", DAEMON)
    q = pick("what is the harbor master's phone at the marina?", p)
    assert not q["found"]


def test_no_files_word_search_guesses_and_code_are_never_quoted(tmp_path):
    assert ask.quote_for("how long is the idle timeout?", []) == {"found": False, "why": qa.NOTHING}
    p = note(tmp_path, "quirks.md", DAEMON)
    assert not pick("how long does the sync daemon stay idle?", p, tier="possible")["found"]
    code = note(tmp_path, "daemon.py", "# the sync daemon exits after 600 seconds idle\nIDLE_SECONDS = 600\n")
    assert not pick("how long does the sync daemon stay idle?", code)["found"]


def test_a_line_that_looks_like_a_secret_is_never_quoted(tmp_path):
    fake = "sk_" + "live_" + "a1B2c3D4e5F6g7H8i9J0" * 2  # built at run time, never a literal key in the source
    p = note(tmp_path, "keys.md", f"# Acme billing\n\n- The billing api key is {fake}\n")
    q = pick("what is the billing api key?", p)
    assert not q["found"] and fake not in json.dumps(q)


def test_a_long_paragraph_is_shown_as_a_window_around_the_value(tmp_path):
    long_line = ("Acme intro words " * 30) + "the cutoff for same-day shipping is 3pm" + (" more filler words" * 30)
    p = note(tmp_path, "shipping.md", f"# Acme shipping\n\n{long_line}\n")
    q = pick("what time is the same-day shipping cutoff?", p)
    assert q["found"] and "3pm" in q["text"] and len(q["text"]) <= qa.QUOTE_CHARS + 30
    assert q["text"].startswith("...") and q["text"].endswith("...")
    assert q["text"].strip(".") in long_line  # a window of the line, never new words


def test_the_listwise_check_leaning_to_none_means_no_quote(tmp_path):
    p = note(tmp_path, "quirks.md", DAEMON)
    q = ask.quote_for("how long does the sync daemon stay idle?", [{"path": p, "tier": "confirmed"}], leans_none=True)
    assert q == {"found": False, "why": qa.NOT_FOUND, "top": p}


# --- through ask.py ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path, monkeypatch):
    ask._STAGE.clear()
    monkeypatch.setenv("TYPESAFE_API_KEY", tj.KEY)
    monkeypatch.setenv("SUPERJEV_AUTO_CACHE", "0")
    d = tmp_path / "Acme Notes"
    d.mkdir()
    w = d / "quirks.md"
    w.write_text(DAEMON)
    tj.world(tmp_path, monkeypatch, navigate=lambda p: tj.cands(w), scores={str(w): 0.95}, lines={str(w): 3})
    yield str(w)
    ask._STAGE.clear()


Q = "how long does the sync daemon stay idle before it exits?"


def test_off_by_default_nothing_changes(engine, monkeypatch, capsys):
    rc, text = tj.run(monkeypatch, capsys, Q)
    assert rc == 0 and "Answer:" not in text
    rc, raw = tj.run(monkeypatch, capsys, "--json", Q)
    assert rc == 0 and "quote" not in json.loads(raw)


def test_quote_in_text_mode_is_one_more_line_after_the_same_output(engine, monkeypatch, capsys):
    _, plain = tj.run(monkeypatch, capsys, Q)
    rc, text = tj.run(monkeypatch, capsys, "--quote", Q)
    assert rc == 0
    assert text == plain + f'Answer: "The sync daemon exits after 600 seconds idle and frees its memory."  -- {engine} line 3\n'


def test_quote_in_json_is_one_extra_field(engine, monkeypatch, capsys):
    _, raw = tj.run(monkeypatch, capsys, "--json", Q)
    rc, quoted = tj.run(monkeypatch, capsys, "--json", "--quote", Q)
    obj = json.loads(quoted)
    assert rc == 0 and quoted.count("\n") == 1
    assert obj.pop("quote") == {"text": "The sync daemon exits after 600 seconds idle and frees its memory.",
                                "path": engine, "line": 3}
    assert obj == json.loads(raw)


def test_quote_goes_with_a_question_only(engine, monkeypatch, capsys):
    rc, text = tj.run(monkeypatch, capsys, "--quote", "--status")
    assert rc == 2 and text.strip() == ask.QUOTE_REFUSED
    rc, raw = tj.run(monkeypatch, capsys, "--json", "--quote", "--claim", "the daemon exits after 600 seconds")
    assert rc == 2 and json.loads(raw)["outcome"] == "not-supported"


def test_quote_is_just_a_word_inside_a_question(engine, monkeypatch, capsys):
    rc, text = tj.run(monkeypatch, capsys, "--", "--quote", Q)
    assert "Answer:" not in text
