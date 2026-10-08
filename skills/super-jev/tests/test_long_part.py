"""A part the zoom chose is never cut: one longer than a passage is split into passages the way a long file is,
each with its own line range, so an answer near the end of a long part still reaches Jev.
Made-up file only; the judge is a stub that confirms only what it was actually sent.

    python3 -m pytest skills/super-jev/tests/test_long_part.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ask  # noqa: E402

ANSWER = "The lighthouse keeper repainted the lamp room in cobalt blue."
QUESTION = "what colour was the lamp room repainted"


def _note(tmp_path, filler_lines):
    lines = ["# Harbour log", "", "## Daily entries"]
    lines += [f"Day {i}: the tide came in and the gulls circled the pier twice." for i in range(filler_lines)]
    lines += [ANSWER, "Day end: the boats stayed tied up."]
    f = tmp_path / "harbour.md"
    f.write_text("\n".join(lines) + "\n")
    return f, len(lines)


def _fake_navigation(sent):
    """Confirms (0.95) only a leaf whose text holds the answer sentence."""
    def run(payload):
        leaves = [n for n in payload["catalog"]["nodes"] if n["id"] != "root"]
        sent.extend(leaves)
        return {"status": "candidates", "candidates": [
            {"sourceId": n["sourceId"], "score": 0.95 if ANSWER in n["description"] else 0.05} for n in leaves]}, None
    return run


def _check(tmp_path, monkeypatch, filler_lines):
    f, n = _note(tmp_path, filler_lines)
    ask._STAGE.clear()
    ask._STAGE["toc_parts"] = {str(f): [("Daily entries", 3, n)]}
    sent = []
    monkeypatch.setattr(ask, "run_navigation", _fake_navigation(sent))
    score, _partial, err, note = ask.confirm_one(QUESTION, str(f))
    assert err is None and note is None
    assert score is not None and score >= 0.9, score
    loc = ask._STAGE["checks"][str(f)]["location"]
    assert loc["part"] == "Daily entries" and loc["start"] <= n - 1 <= loc["end"]
    assert loc["end"] - loc["start"] < n - 3  # a passage of the part, not the whole part
    # every leaf sent stays passage-sized, and the file's read stays within the long-file budget
    assert all(len(x["description"]) < 2 * ask.CONFIRM_CHUNK for x in sent)
    assert sum(len(x["description"]) for x in sent) <= ask.READ_CHARS + 400 * len(sent)
    return sent


def test_an_answer_past_the_old_cut_of_a_long_part_reaches_jev(tmp_path, monkeypatch):
    sent = _check(tmp_path, monkeypatch, 150)  # about 10,000 characters: every passage fits the read budget
    assert len(sent) > 1


def test_a_very_long_part_is_read_like_a_long_file_within_the_same_budget(tmp_path, monkeypatch):
    _check(tmp_path, monkeypatch, 1500)  # about 100,000 characters: passages picked as a long file's are
