"""Optional local-model check for harness-only asks (SUPERJEV_LOCAL_CHECK_URL). The model is faked here: these tests pin the
rule around it (annotate only: every file stays, in the same order; off by default)."""
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
def test_none_from_the_model_annotates_and_keeps_every_file(cid, tmp_path, monkeypatch):
    base, _r, _c = _run(cid, tmp_path, monkeypatch, lambda p, t: None)
    got, res, calls = _run(cid, tmp_path, monkeypatch, lambda p, t: "")
    assert got == base and got and res["outcome"] == "found" and calls
    assert "local check found no answering line" in res["why"]
    assert all(f["local_check"] == ask.LOCAL_NONE_NOTE for f in res["files"][:len(calls)])


def test_an_answer_is_quoted_and_keeps_the_files(tmp_path, monkeypatch):
    got, res, _calls = _run("wrong-person", tmp_path, monkeypatch, lambda p, t: "Bob takes metformin every morning")
    assert got and got[0] == "bob-meds.md"
    assert res["files"][0]["local_check"] == "answer line: Bob takes metformin every morning"
    assert "local check found" not in res["why"]


def test_order_is_never_changed(tmp_path, monkeypatch):
    base, _r, _c = _run("hold-flag-kept", tmp_path, monkeypatch, lambda p, t: None)
    got, _res, _calls = _run("hold-flag-kept", tmp_path, monkeypatch,
                             lambda p, t: "Zeta prescription refill every 30 days" if "refill every" in t else "")
    assert "zeta-hold.md" in got and got == base


def test_no_verdict_adds_nothing(tmp_path, monkeypatch):  # model down or slow: today's behaviour
    got, res, _calls = _run("absent-value-missing", tmp_path, monkeypatch, lambda p, t: None)
    assert got == ["passport.md"] and "local_check" not in res["files"][0] and "no judge check" in res["why"]


def test_off_by_default(tmp_path, monkeypatch):
    got, _res, calls = _run("absent-value-missing", tmp_path, monkeypatch, lambda p, t: "", url="")
    assert got == ["passport.md"] and not calls


class _R:
    def __init__(s, c): s.c = c
    def read(s): return json.dumps({"choices": [{"message": {"content": s.c}}]}).encode()
    def __enter__(s): return s
    def __exit__(s, *a): return False


def _verdict(reply, passage="Sam's passport renewal is due in May. It costs 130 dollars."):
    import urllib.request
    orig = urllib.request.urlopen
    try:
        urllib.request.urlopen = lambda *a, **k: _R(reply)
        return ask.local_verdict("http://x", "q", "/n.md", passage)
    finally:
        urllib.request.urlopen = orig


def test_the_whole_quote_must_be_in_the_passage():
    assert _verdict("Sam's passport renewal is due in May.") == "Sam's passport renewal is due in May."
    assert _verdict("Sam's  passport renewal\nis due in May.") == "Sam's passport renewal is due in May."  # spacing normalized
    # first 40 chars real, tail invented: no verdict, never shown
    assert _verdict("Sam's passport renewal is due in May and the number is 123456789") is None


def test_only_an_explicit_none_means_no_answer():
    assert _verdict("NONE") == "" and _verdict("none.") == ""
    for junk in ("I cannot tell from this passage", "NONE of the lines say so, but maybe May", "{}", "???", "The passport"
                 " is invented"):
        assert _verdict(junk) is None


def test_junk_reply_leaves_outcome_unchanged(tmp_path, monkeypatch):
    base, bres, _c = _run("absent-value-missing", tmp_path, monkeypatch, lambda p, t: None)
    assert "local check" not in bres["why"] and all("local_check" not in f for f in bres["files"])


def test_budget_caps_total_time(monkeypatch, tmp_path):
    import time as _t
    paths = []
    for i in range(3):
        p = tmp_path / f"n{i}.md"
        p.write_text("some note text about passports")
        paths.append(str(p))
    monkeypatch.setattr(ask, "LOCAL_CHECK_BUDGET", 0.05)
    calls = []
    def slow(u, q, path, passage):
        calls.append(path)
        _t.sleep(0.1)
        return ""
    monkeypatch.setattr(ask, "local_verdict", slow)
    r = ask.local_check("http://x", "passport", paths)
    assert len(calls) == 1 and list(r["quotes"].values()) == ["", None, None] and not r["all_none"]
