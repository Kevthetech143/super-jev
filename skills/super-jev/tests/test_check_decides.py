"""The content check decides: a file it rejects is never listed as found, and listed files
follow the check, not the routing score. Fake judge only; no paid calls.

    python3 -m pytest skills/super-jev/tests/test_check_decides.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ask  # noqa: E402


def _setup(tmp_path, monkeypatch, files, routing, scores, notes=None, claim=None, verdicts=None):
    """files: names. routing: {name: routing score}. scores: {name: content score}.
    verdicts (claim mode): {name: (verdict, prob)} as the fake judge returns them."""
    paths = {}
    for name in files:
        p = tmp_path / name
        p.write_text(f"# {name}\nsome text about the topic\n")
        paths[name] = str(p)
    monkeypatch.setitem(ask._CLAIM, "text", claim)
    monkeypatch.setattr(ask, "load_cache_files", lambda *a: {})
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    monkeypatch.setattr(ask, "pointer_words", lambda *a: ({}, []))
    monkeypatch.setattr(ask, "batch_jev", lambda: False)
    monkeypatch.setattr(ask, "skipped_for_question", lambda *a: [])
    monkeypatch.setattr(ask, "apply_near_twin_tiebreak", lambda q, top: top)
    monkeypatch.setattr(ask, "memory", lambda r:
                        {"status": "miss"} if r["action"] == "cached" else
                        {"pointers": ["p1"]} if r["action"] == "panel" else
                        {"status": "candidates",
                         "candidates": [{"score": routing[n], "originalPath": paths[n]} for n in files]})
    monkeypatch.setattr(ask, "confirm", lambda q, ps: (
        {paths[n]: s for n, s in scores.items()}, set(), None, {paths[n]: v for n, v in (notes or {}).items()}))

    def judge(q, ps):
        ask._STAGE["claim_read"] = list(ps)
        ask._STAGE["claim_files"] = {paths[n]: {"verdict": v, "prob": pr, "line": None, "line_no": None}
                                     for n, (v, pr) in (verdicts or {}).items()}
        return ps[0], 0.5
    monkeypatch.setattr(ask, "judge_listwise", judge)
    return paths


def _ask(tmp_path, capsys, question):
    rc = ask.lookup(question, "me", tmp_path / "state")
    return rc, capsys.readouterr().out


# (1) an absent question whose only candidate fails the check: not found, exit 1, file not listed

def test_rejected_only_candidate_is_not_found(tmp_path, monkeypatch, capsys):
    paths = _setup(tmp_path, monkeypatch, ["timeline.md"], {"timeline.md": 0.9}, {})
    rc, out = _ask(tmp_path, capsys, "what is the blood type")
    assert rc == 1 and "OUTCOME: not-found" in out and paths["timeline.md"] not in out


def test_claim_the_check_says_not_stated_is_not_listed_as_found(tmp_path, monkeypatch, capsys):
    paths = _setup(tmp_path, monkeypatch, ["timeline.md"], {"timeline.md": 0.9}, {"timeline.md": 0.74},
                   claim="The blood type is O.", verdicts={"timeline.md": ("not_stated", 0.95)})
    rc, out = _ask(tmp_path, capsys, "The blood type is O.")
    assert "NOT FOUND" in out and "Files found:" not in out and paths["timeline.md"] not in out


# (2) routing ranks the failing file first: the passing one is listed first

def test_passing_file_listed_before_higher_routed_rejected_file(tmp_path, monkeypatch, capsys):
    paths = _setup(tmp_path, monkeypatch, ["bad.md", "good.md"], {"bad.md": 0.97, "good.md": 0.5},
                   {"good.md": 0.91})
    rc, out = _ask(tmp_path, capsys, "what is left to do")
    assert rc == 0 and paths["bad.md"] not in out and paths["good.md"] in out


def test_claim_passing_file_listed_first_and_rejected_one_left_out(tmp_path, monkeypatch, capsys):
    paths = _setup(tmp_path, monkeypatch, ["bad.md", "good.md"], {"bad.md": 0.97, "good.md": 0.5},
                   {"bad.md": 0.97, "good.md": 0.91}, claim="The list is open.",
                   verdicts={"bad.md": ("not_stated", 0.95), "good.md": ("partly", 0.93)})
    rc, out = _ask(tmp_path, capsys, "The list is open.")
    listed = out.split("Files found:")[1] if "Files found:" in out else ""
    assert paths["good.md"] in listed and paths["bad.md"] not in listed


# (3) an unfinished check: the file is kept and labelled, but alone it is not found (2026-10-07)

def test_unfinished_check_is_kept_and_labelled_unconfirmed(tmp_path, monkeypatch, capsys):
    paths = _setup(tmp_path, monkeypatch, ["a.md"], {"a.md": 0.8}, {}, notes={"a.md": ask.INCONCLUSIVE})
    rc, out = _ask(tmp_path, capsys, "what is left to do")
    assert rc == 1 and "OUTCOME: not-found" in out and paths["a.md"] in out and "inconclusive" in out


def test_claim_unjudged_file_is_not_dropped(tmp_path, monkeypatch, capsys):
    paths = _setup(tmp_path, monkeypatch, ["a.md"], {"a.md": 0.8}, {"a.md": 0.8}, claim="The list is open.",
                   verdicts={"a.md": ("not_stated", 0.5)})  # under the sure line: no rejection
    rc, out = _ask(tmp_path, capsys, "The list is open.")
    assert paths["a.md"] in out
