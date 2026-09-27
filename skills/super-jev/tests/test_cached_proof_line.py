"""Cache evidence keeps distinguishing dates and displays reviewed proof lines."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ask


def test_approval_selects_the_event_date_not_a_neighbour(tmp_path, monkeypatch):
    source = tmp_path / "events.md"
    source.write_text("# Events\n2024-04-01: Alex chose the Cedar plan.\n"
                      "2024-04-02: Alex chose the Cedar plan.\n")
    calls = []
    def memory(req):
        calls.append(req)
        return {"status": "ready", "passages": [{"sourceId": "one", "reviewedText": source.read_text()}]}
    monkeypatch.setattr(ask, "memory", memory)
    result, why = ask.file_evidence("reader", "notes", "Which plan was chosen?",
                                    "On 2024-04-02 Alex chose the Cedar plan.", str(source), "one",
                                    {"attemptId": "attempt"})
    assert result and not why
    assert {r["startLine"] for r in calls[0]["references"]} == {1, 3}


def test_cache_replay_shows_the_saved_supporting_line_without_reading_source(tmp_path, capsys):
    proof = "2024-04-02: Alex chose the Cedar plan."
    quote = "# Events\n" + "Background about the plans. " * 20 + "\n" + proof
    hit = {"answer": "On 2024-04-02 Alex chose the Cedar plan.",
           "evidence": [{"sourceId": "one", "quote": quote}]}
    assert ask.print_hit(hit, tmp_path, "reader", "Which plan was chosen?") == 0
    out = capsys.readouterr().out
    assert "evidence: one | " + proof in out
    assert "Background about" not in out


def test_date_heading_does_not_displace_the_supporting_sentence():
    text = "# 2024-04-02\nAlex chose the Cedar plan."
    assert ask.best_evidence_line(text, "On 2024-04-02 Alex chose the Cedar plan.") == "Alex chose the Cedar plan."


def test_digit_alone_cannot_support_approval(tmp_path, monkeypatch):
    source = tmp_path / "notes.md"
    source.write_text("There are 3 umbrellas.")
    monkeypatch.setattr(ask, "memory", lambda req: (_ for _ in ()).throw(AssertionError("must refuse before assist")))
    result, why = ask.file_evidence("reader", "notes", "Which plan?", "Cedar has 3 phases.",
                                    str(source), "one", {"attemptId": "attempt"})
    assert result is None and "shares a word" in why


def test_assisted_digit_alone_cannot_support_approval(tmp_path, monkeypatch):
    source = tmp_path / "notes.md"
    source.write_text("Cedar has 3 phases.")
    monkeypatch.setattr(ask, "memory", lambda req: {"passages": [
        {"sourceId": "one", "reviewedText": "There are 3 umbrellas."}]})
    result, why = ask.file_evidence("reader", "notes", "Which plan?", "Cedar has 3 phases.",
                                    str(source), "one", {"attemptId": "attempt"})
    assert result is None and "no passage" in why


def test_replay_caps_selected_line(tmp_path, capsys):
    hit = {"answer": "Cedar plan", "evidence": [{"sourceId": "one", "quote": "Cedar plan " * 100}]}
    ask.print_hit(hit, tmp_path, "reader", "Which plan?")
    line = next(x for x in capsys.readouterr().out.splitlines() if "evidence:" in x)
    assert len(line.split(" | ", 1)[1]) == 120
