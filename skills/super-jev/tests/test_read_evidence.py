"""Read evidence must reach claim judgment, including explicit contradictions."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ask


def test_abstract_worth_is_not_an_exact_money_question():
    q = "What should I remember about someone's worth depending on helping others?"
    assert ask.confirm_label(q) == ask.SOURCE_LABEL
    assert ask.confirm_label(q) == ask.SOURCE_LABEL
    assert ask.confirm_label("What is worth reading?") == ask.SOURCE_LABEL
    for q in ("What is the boat worth?", "What is our net worth?", "How much is it worth?", "What’s my car worth?"):
        assert ask.confirm_label(q) == ask.SOURCE_LABEL


def test_read_but_rejected_file_does_not_get_reconnect_advice(monkeypatch):
    monkeypatch.setattr(ask, "skipped_for_question", lambda *args: [])
    text = "\n".join(ask.miss_report("reader", 1, {}, {"/notes/topic.md": {"label": "dropped"}}))
    assert "none contained" not in text
    assert "probably not connected" not in text
    assert "--root <folder>" in text
    assert "different file outside the connected sets" in text
    assert "trace-show" in text


def test_contradiction_reaches_judge_even_when_answer_filter_drops_file(tmp_path, monkeypatch, capsys):
    p = tmp_path / "preferences.md"
    p.write_text("# Delivery\nThe team requested email, not postal mail.\n")
    path = str(p)
    claim = "The team requested postal mail rather than email."
    monkeypatch.setitem(ask._CLAIM, "text", claim)
    monkeypatch.setattr(ask, "load_cache_files", lambda *a: {})
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    monkeypatch.setattr(ask, "pointer_words", lambda *a: ({}, []))
    monkeypatch.setattr(ask, "batch_jev", lambda: False)
    monkeypatch.setattr(ask, "memory", lambda r:
                        {"pointers": ["reader-notes"]} if r["action"] == "panel" else
                        {"status": "candidates", "candidates": [{"score": ask.CONFIRM_FLOOR,
                                                                  "originalPath": path}]})
    monkeypatch.setattr(ask, "confirm", lambda *a: ({}, set(), None, {}))
    monkeypatch.setattr(ask, "skipped_for_question", lambda *a: [])
    def judge(q, paths):
        assert path in paths
        ask._STAGE["claim_read"] = [path]
        ask._STAGE["claim_files"] = {path: {"verdict": "contradicted", "prob": ask.CLAIM_SURE,
                                           "line": p.read_text().splitlines()[1], "line_no": 2}}
        return ask.LISTWISE_NONE, ask.LISTWISE_PROMOTE_FLOOR
    monkeypatch.setattr(ask, "judge_listwise", judge)
    assert ask.lookup(claim, "reader", tmp_path / "state") == 5  # a FALSE claim exits 5, not 0
    out = capsys.readouterr().out
    assert out.startswith("FALSE")
    assert "The team requested email, not postal mail." in out
    assert "Files found:" in out and path in out.split("Files found:")[1]
    assert "NOT FOUND" not in out


def test_strong_word_search_evidence_reaches_bounded_claim_judge(tmp_path, monkeypatch, capsys):
    routed = []
    for i in range(ask.LISTWISE_MAX_FILES + 1):
        p = tmp_path / f"route-{i}.md"
        p.write_text("# Related topic\nA delivery discussion.\n")
        routed.append(str(p))
    target = tmp_path / "delivery.md"
    target.write_text("# Delivery\nOnly email is authorized.\n")
    path = str(target)
    monkeypatch.setitem(ask._CLAIM, "text", "Postal delivery is authorized.")
    monkeypatch.setattr(ask, "load_cache_files", lambda *a: {})
    monkeypatch.setattr(ask, "pointer_words", lambda *a: ({}, []))
    monkeypatch.setattr(ask, "batch_jev", lambda: False)
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [(ask.CONFIRM_FLOOR, path, "notes")])
    monkeypatch.setattr(ask, "memory", lambda r: {"pointers": ["notes"]} if r["action"] == "panel" else
                        {"status": "candidates", "candidates": [{"score": ask.CONFIRM_FLOOR, "originalPath": p} for p in routed]})
    monkeypatch.setattr(ask, "confirm", lambda *a: ({path: ask.CONFIRM_FLOOR}, set(), None, {}))
    monkeypatch.setattr(ask, "apply_near_twin_tiebreak", lambda q, top: top)
    seen = []
    def judge(q, paths):
        seen.extend(paths[:ask.LISTWISE_MAX_FILES])
        ask._STAGE["claim_files"] = {path: {"verdict": "contradicted", "prob": ask.CLAIM_SURE,
                                           "line": "Only email is authorized.", "line_no": 2}}
        ask._STAGE["claim_read"] = seen
        return path, ask.LISTWISE_PROMOTE_FLOOR
    monkeypatch.setattr(ask, "judge_listwise", judge)
    assert ask.lookup("Postal delivery is authorized.", "reader", tmp_path / "state") == 5  # FALSE exits 5
    assert seen[0] == path
    assert capsys.readouterr().out.startswith("FALSE")
