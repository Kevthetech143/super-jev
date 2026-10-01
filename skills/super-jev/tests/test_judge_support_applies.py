#!/usr/bin/env python3
"""judge_support --applies: is this record a finished one, made on this build, these cases and this bar?
Free (no ask). Reuses the fake-build fixture of test_judge_support.py.

    python3 -m pytest skills/super-jev/tests/test_judge_support_applies.py -q
"""
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
import judge_support as js  # noqa: E402
from test_judge_support import Q, TOKEN, asked, go, sha, w  # noqa: E402,F401
from test_paid_replay import env  # noqa: E402,F401

BAR = [{"measure": "top5", "min_rate": 1.0}, {"measure": "secs", "percentile": 90, "max_secs": 60}]


def made(w, hit=True, judge="typesafe-jev", extra=()):
    """Run the judge on two cases (both found, or the second missed) and return (record path, pieces for --applies)."""
    rows = [Q(w, "one", "a"), Q(w, "two", "b")]
    plan = {rows[0]["question"]: {"final": [(0.9, w.f["a"])]},
            rows[1]["question"]: {"final": [(0.9, w.f["b"] if hit else w.f["c"])]}}
    rc, rec = go(w, rows, plan, BAR, *extra, judge=judge, record=f"rec{w.calls + 1}.json")
    assert rc == (0 if hit else 1), rec["stop_reason"]
    n = w.calls
    return w.tmp / f"rec{n}.json", {"ask": str(w.tmp / f"build{n}/skills/super-jev/ask.py"), "judge": judge,
                                "cases": w.tmp / f"cases{n}.jsonl", "bar": w.tmp / f"bar{n}.json"}


def applies(rec, p, capsys, judge=None, env=(), **over):
    """--applies for the pieces `p`; returns (exit code, stdout, stderr)."""
    p = {**p, **over}
    argv = ["--applies", str(rec), "--ask", p["ask"], "--judge", judge or p["judge"], "--cases", str(p["cases"]),
            "--cases-sha256", p.get("cases_sha256", sha(p["cases"])), "--bar", str(p["bar"]),
            "--bar-sha256", p.get("bar_sha256", sha(p["bar"]))]
    for e in env:
        argv += ["--env", e]
    capsys.readouterr()
    try:
        rc = js.main(argv)
    except SystemExit as e:
        rc = e.code
    out = capsys.readouterr()
    return rc, out.out, out.err


def edit(path, fn):
    rec = json.loads(Path(path).read_text())
    fn(rec)
    Path(path).write_text(json.dumps(rec))


def test_a_fresh_finished_record_applies_and_an_engine_edit_ends_it(w, capsys):
    rec, p = made(w)
    rc, out, _ = applies(rec, p, capsys)
    assert rc == 0 and out.startswith("applies: supported") and asked(w.tmp) != [] and len(asked(w.tmp)) == 2  # no new ask
    # The same build in another folder: still applies. An engine file edited: it no longer does.
    engine = Path(p["ask"]).parent / "judge_profile.py"
    engine.write_text(engine.read_text() + "\n# a changed line\n")
    rc, out, _ = applies(rec, p, capsys)
    assert rc == 1 and out == "does not apply: fingerprint differs\n"
    assert len(asked(w.tmp)) == 2


def test_a_doc_or_test_edit_does_not_end_a_record(w, capsys):
    rec, p = made(w)
    root = Path(p["ask"]).parent
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_x.py").write_text("# a test\n")
    (root / "README.md").write_text("a doc\n")
    assert applies(rec, p, capsys)[0] == 0


def test_a_finished_not_supported_record_applies_and_says_not_supported(w, capsys):
    rec, p = made(w, hit=False)
    rc, out, _ = applies(rec, p, capsys)
    assert rc == 0 and out.startswith("applies: not supported")


def test_an_incomplete_record_does_not_apply(w, capsys):
    rows = [Q(w, f"n{i}", "abc"[i]) for i in range(3)]
    plan = {r["question"]: {"final": [(0.9, w.f["abc"[i]])], "sleep": 0.5} for i, r in enumerate(rows)}
    rc, rec = go(w, rows, plan, BAR[:1], "--time-cap-min", "0.001")
    assert rc == 3
    n = w.calls
    p = {"ask": str(w.tmp / f"build{n}/skills/super-jev/ask.py"), "judge": "typesafe-jev",
         "cases": w.tmp / f"cases{n}.jsonl", "bar": w.tmp / f"bar{n}.json"}
    rc, out, _ = applies(w.tmp / "rec.json", p, capsys)
    assert rc == 1 and out == "does not apply: incomplete (time cap)\n"


def test_a_record_with_no_fingerprint_does_not_apply(w, capsys):
    rec, p = made(w)
    edit(rec, lambda r: r.pop("fingerprint"))
    rc, out, _ = applies(rec, p, capsys)
    assert rc == 1 and out == "does not apply: no fingerprint\n"
    edit(rec, lambda r: r.update(fingerprint=""))
    assert applies(rec, p, capsys)[1] == "does not apply: no fingerprint\n"


def test_an_external_record_with_no_stop_reason_does_not_apply(w, capsys):
    rec, p = made(w)
    edit(rec, lambda r: r.pop("stop_reason"))
    rc, out, _ = applies(rec, p, capsys)
    assert rc == 1 and out.startswith("does not apply: incomplete")


def test_a_fake_record_never_stands_in_for_the_default_judge(w, capsys):
    rec, p = made(w, judge="fake")
    assert applies(rec, p, capsys)[0] == 0                                    # as the fake: yes
    rc, out, _ = applies(rec, p, capsys, judge="typesafe-jev")
    assert rc == 1 and out == "does not apply: fingerprint differs\n"         # as the default judge: no


def test_an_unknown_judge_is_exit_1_with_the_known_names(w, capsys):
    rec, p = made(w)
    rc, out, _ = applies(rec, p, capsys, judge="nope")
    assert rc == 1 and out.startswith("does not apply: unknown judge 'nope' (known: ") and "typesafe-jev" in out


def test_a_different_judge_or_env_setting_is_a_different_fingerprint(w, capsys):
    rec, p = made(w)
    assert applies(rec, p, capsys, judge="laya")[1] == "does not apply: fingerprint differs\n"
    assert applies(rec, p, capsys, env=["SAM_TOKEN=something"])[1] == "does not apply: fingerprint differs\n"
    rec, p = made(w, extra=("--env", f"SAM_TOKEN={TOKEN}"))
    assert applies(rec, p, capsys, env=[f"SAM_TOKEN={TOKEN}"])[0] == 0
    assert applies(rec, p, capsys)[0] == 1


@pytest.mark.parametrize("name", ["SUPERJEV_JUDGE", "SUPERJEV_REPLAY", "SUPERJEV_AUTO_CACHE"])
def test_a_setting_the_tool_owns_is_exit_2_here_as_in_the_run(w, capsys, name):
    rec, p = made(w)
    rc, out, err = applies(rec, p, capsys, env=[f"{name}=1"])
    assert rc == 2 and out == "" and "the tool sets this one itself" in err


def test_changed_cases_or_a_changed_bar_does_not_apply_and_a_wrong_pin_is_exit_2(w, capsys):
    rec, p = made(w)
    # Cases changed, correctly re-pinned: a different set, so the record is not for it.
    cases2 = w.tmp / "cases-v2.jsonl"
    cases2.write_text(Path(p["cases"]).read_text() + "\n")
    rc, out, _ = applies(rec, p, capsys, cases=cases2)
    assert rc == 1 and out == "does not apply: cases differs\n"
    bar2 = w.tmp / "bar-v2.json"
    bar2.write_text(json.dumps({"max_excluded": 1, "rows": BAR}))
    rc, out, _ = applies(rec, p, capsys, bar=bar2)
    assert rc == 1 and out == "does not apply: bar differs\n"
    # A pin that does not match the file it names is bad input, with the real SHA printed.
    rc, out, err = applies(rec, p, capsys, cases_sha256="0" * 64)
    assert rc == 2 and out == "" and f"cases sha256 mismatch: the file is {sha(p['cases'])}" in err
    rc, out, err = applies(rec, p, capsys, bar_sha256="f" * 64)
    assert rc == 2 and f"bar sha256 mismatch: the file is {sha(p['bar'])}" in err


def test_the_printed_verdict_comes_from_the_stored_hits_not_the_verdict_field(w, capsys):
    rec, p = made(w)                       # supported
    edit(rec, lambda r: r.update(verdict="not supported"))
    assert applies(rec, p, capsys)[1].startswith("applies: supported")
    edit(rec, lambda r: r["rows"][0].update(hits=1))       # the stored hits now miss the bar (1 of 2), field says nothing
    assert applies(rec, p, capsys)[1].startswith("applies: not supported")
    rec2, p2 = made(w, hit=False)          # not supported, hand-edited to look supported
    edit(rec2, lambda r: r.update(verdict="supported", stop_reason="complete"))
    rc, out, _ = applies(rec2, p2, capsys)
    assert rc == 0 and out.startswith("applies: not supported")
    # A stop for an unreachable bar is never "supported", even with hits edited to pass.
    rec3, p3 = made(w, hit=False)
    edit(rec3, lambda r: [x.update(hits=x["n"]) for x in r["rows"]])
    assert applies(rec3, p3, capsys)[1].startswith("applies: not supported")
    # A stored open row (pass: null) or a stored pass: true is never read; the verdict comes from hits and n.
    edit(rec3, lambda r: [x.update({"pass": None}) for x in r["rows"]])
    assert applies(rec3, p3, capsys)[1].startswith("applies: not supported")
    rec4, p4 = made(w)
    edit(rec4, lambda r: [x.update({"pass": None}) for x in r["rows"]])
    assert applies(rec4, p4, capsys)[1].startswith("applies: supported")


def test_the_integer_rate_rule_is_applied_again_from_the_stored_row(w, capsys):
    rec, p = made(w)
    edit(rec, lambda r: r["rows"][0].update(bar={"min_rate": 0.28}, hits=7, n=25))
    assert applies(rec, p, capsys)[1].startswith("applies: supported")
    edit(rec, lambda r: r["rows"][0].update(hits=6))
    assert applies(rec, p, capsys)[1].startswith("applies: not supported")
    edit(rec, lambda r: r["rows"][0].update(hits=0, n=0))       # measured nothing: never passes
    assert applies(rec, p, capsys)[1].startswith("applies: not supported")


@pytest.mark.parametrize("damage", [lambda r: r.pop("rows"), lambda r: r.update(rows=[]),
                                    lambda r: r["rows"][0].pop("hits"), lambda r: r["rows"][0].update(hits="3"),
                                    lambda r: r["rows"][0].update(kind="vibes"), lambda r: r["rows"][0].pop("bar")])
def test_a_damaged_record_does_not_apply_and_never_crashes(w, capsys, damage):
    rec, p = made(w)
    edit(rec, damage)
    rc, out, _ = applies(rec, p, capsys)
    assert rc == 1 and out.startswith("does not apply: ")


def test_an_unreadable_record_does_not_apply(w, capsys):
    rec, p = made(w)
    for text in ("{not json", "[1, 2]", ""):
        rec.write_text(text)
        rc, out, _ = applies(rec, p, capsys)
        assert rc == 1 and out.startswith("does not apply: record unreadable"), text
    rc, out, _ = applies(w.tmp / "missing.json", p, capsys)
    assert rc == 1 and out.startswith("does not apply: record unreadable")


def test_missing_flags_and_mixed_commands_are_exit_2(w, capsys):
    rec, p = made(w)
    for argv, why in ((["--applies", str(rec), "--ask", p["ask"], "--judge", "typesafe-jev"], "required: --cases, --cases-sha256, --bar, --bar-sha256"),
                      (["--applies", str(rec), "--fingerprint", "--ask", p["ask"], "--judge", "typesafe-jev"], "separate commands")):
        capsys.readouterr()
        with pytest.raises(SystemExit) as e:
            js.main(argv)
        assert e.value.code == 2 and why in capsys.readouterr().err


def test_applies_asks_nothing_and_writes_nothing(w, capsys):
    rec, p = made(w)
    before = rec.read_text()
    calls = len(asked(w.tmp))
    for _ in range(3):
        assert applies(rec, p, capsys)[0] == 0
    assert rec.read_text() == before and len(asked(w.tmp)) == calls
