"""Optional local-model check for harness-only asks (SUPERJEV_LOCAL_CHECK_URL). The model is faked here: these tests pin the
rule around it (veto only on a clear NONE from every checked file; any answer, or no verdict, keeps the list; off by default)."""
import importlib.util
import json
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("harness_danger_mod", SKILL / "tests" / "test_harness_danger_set.py")
_d = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_d)
_h, ask = _d._h, _d._h.ask


def _run(cid, tmp_path, monkeypatch, verdict, url="http://127.0.0.1:1"):
    question, notes, _ok = _d.CASES[cid]
    files = []
    for name, text in {**notes, **{f"noise{i}.md": t for i, t in enumerate(_d.NOISE)}}.items():
        p = tmp_path / name
        p.write_text(text)
        files.append(p)
    calls = []
    monkeypatch.setattr(ask, "local_verdict", lambda u, q, path, passage: calls.append(Path(path).name) or verdict(path, passage))
    monkeypatch.delenv("SUPERJEV_LOCAL_CHECK_URL", raising=False)
    if url:
        monkeypatch.setenv("SUPERJEV_LOCAL_CHECK_URL", url)
    rc, res = _h._lookup(tmp_path, question, files, monkeypatch=monkeypatch)
    return [Path(x["path"]).name for x in res.get("files") or []], res, calls


@pytest.mark.parametrize("cid", ["absent-neighbour-topic", "absent-value-missing"])
def test_none_from_the_model_flips_the_known_gaps(cid, tmp_path, monkeypatch):
    got, res, calls = _run(cid, tmp_path, monkeypatch, lambda p, t: "")
    assert got == [] and res["outcome"] == "not-found" and calls


def test_an_answer_keeps_the_files(tmp_path, monkeypatch):
    got, _res, _calls = _run("wrong-person", tmp_path, monkeypatch, lambda p, t: "Bob takes metformin every morning")
    assert got and got[0] == "bob-meds.md"


def test_one_answer_among_nones_keeps_the_list(tmp_path, monkeypatch):
    got, _res, _calls = _run("hold-flag-kept", tmp_path, monkeypatch,
                             lambda p, t: "Zeta prescription refill every 30 days" if "refill every" in t else "")
    assert "zeta-hold.md" in got


def test_no_verdict_keeps_the_list(tmp_path, monkeypatch):  # model down or slow: today's behaviour, never a new refusal
    got, _res, _calls = _run("absent-value-missing", tmp_path, monkeypatch, lambda p, t: None)
    assert got == ["passport.md"]


def test_off_by_default(tmp_path, monkeypatch):
    got, _res, calls = _run("absent-value-missing", tmp_path, monkeypatch, lambda p, t: "", url="")
    assert got == ["passport.md"] and not calls


def test_a_copied_line_must_be_in_the_passage():
    class R:
        def __init__(s, c): s.c = c
        def read(s): return json.dumps({"choices": [{"message": {"content": s.c}}]}).encode()
        def __enter__(s): return s
        def __exit__(s, *a): return False
    import urllib.request
    passage = "Sam's passport renewal is due in May."
    orig = urllib.request.urlopen
    try:
        urllib.request.urlopen = lambda *a, **k: R("Sam's passport number is 123456789")  # invented line
        assert ask.local_verdict("http://x", "q", "/n.md", passage) == ""
        urllib.request.urlopen = lambda *a, **k: R("Sam's passport renewal is due in May.")
        assert ask.local_verdict("http://x", "q", "/n.md", passage)
        urllib.request.urlopen = lambda *a, **k: R("NONE")
        assert ask.local_verdict("http://x", "q", "/n.md", passage) == ""
    finally:
        urllib.request.urlopen = orig
