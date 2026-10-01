#!/usr/bin/env python3
"""judge_support grades one judge on one build against your bar: every row of the case file once, timed,
fail-fast, with excluded cases, a time cap, one record. Against a tmp copy of the real tree whose ask.py
is a FAKE (no network, no key). Users are the made-up sam and the Quillbrook notes.

    python3 -m pytest skills/super-jev/tests/test_judge_support.py -q
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parent.parent
sys.path.insert(0, str(SKILL))
import judge_support as js  # noqa: E402
from test_paid_replay import FAKE, _log, _register, env  # noqa: E402,F401

# The paid_replay fake, plus the environment it was started with (the SUPERJEV_* and SAM_* names).
FAKE_JS = FAKE.replace('f.write(json.dumps({"q": q,',
                       'f.write(json.dumps({"env": {k: v for k, v in os.environ.items() if k.startswith(("SUPERJEV_", "SAM_"))}, "q": q,')
assert FAKE_JS != FAKE
# Joined so no single literal after "TOKEN =" is eight characters or more: the CI secret scan flags one.
TOKEN = "quill" + "brook-token-123456"


@pytest.fixture
def w(tmp_path, env):
    """Eight Quillbrook notes, registered as the connected originals the replay pins."""
    notes = tmp_path / "notes"
    notes.mkdir()
    files = {c: str(notes / f"{c}.md") for c in "abcdefgh"}
    for p in files.values():
        Path(p).write_text("Quillbrook note\n")
    _register(json.loads((env / "_memory" / "config.json").read_text())["registry"], list(files.values()))
    return SimpleNamespace(tmp=tmp_path, f=files, calls=0)


def build(tmp_path, plan, name="build"):
    """The real judge path, copied, with the fake in place of ask.py."""
    root = tmp_path / name
    skip = shutil.ignore_patterns("__pycache__", "node_modules", ".pytest_cache")
    for sub in js.JUDGE_PATH:
        shutil.copytree(REPO / sub, root / sub, ignore=skip)
    (root / "skills/super-jev/prepare-cache").mkdir()
    (root / "skills/super-jev/ask.py").write_text(FAKE_JS % (plan,))
    return str(root / "skills/super-jev/ask.py")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def go(w, rows, plan, bar, *extra, judge="typesafe-jev", max_excluded=0, max_asks=None, record="rec.json",
       bar_obj=None):
    """Write the cases and the bar, run judge_support, return (exit code, the record or None)."""
    w.calls += 1
    tmp = w.tmp
    cases = tmp / f"cases{w.calls}.jsonl"
    cases.write_text("".join(json.dumps({"principal": "me", "split": "tuned", **r}) + "\n" for r in rows))
    barf = tmp / f"bar{w.calls}.json"
    barf.write_text(json.dumps(bar_obj if bar_obj is not None else {"max_excluded": max_excluded, "rows": bar}))
    out = tmp / record
    try:
        rc = js.main(["--ask", build(tmp, plan, f"build{w.calls}"), "--judge", judge, "--cases", str(cases),
                      "--cases-sha256", sha(cases), "--bar", str(barf), "--bar-sha256", sha(barf), "--record", str(out),
                      "--max-asks", str(max_asks or len(rows)), *extra])
    except SystemExit as e:
        rc = e.code
    return rc, (json.loads(out.read_text()) if out.exists() else None)


def Q(w, name, gold, **kw):
    return {"question": f"Where is the Quillbrook {name} note?", "gold": [w.f[g] for g in gold], **kw}


def asked(tmp_path):
    return [e["q"] for e in _log(tmp_path)]


def test_a_run_that_meets_every_row_is_supported_and_the_record_says_everything(w, capsys):
    a, c = w.f["a"], w.f["c"]
    rows = [Q(w, "one", "a"), Q(w, "two", "b"), Q(w, "three", "c", split="retrospective"), Q(w, "four", "d", split="retrospective"),
            {"question": "Where is the Quillbrook parking policy?", "absent": True, "gold": []},
            {"question": "Where is the Quillbrook ferry timetable?", "absent": True, "gold": []},
            {"kind": "claim", "question": "Sam owns the Quillbrook cottage.", "expected": "TRUE", "gold": [w.f["e"]]},
            {"kind": "claim", "question": "Sam sold the Quillbrook cottage.", "expected": "FALSE", "gold": [w.f["e"]]},
            {"kind": "claim", "question": "Sam owns a Quillbrook boat.", "expected": "ABSENT", "gold": []}]
    plan = {rows[0]["question"]: {"final": [(0.95, a)]}, rows[1]["question"]: {"final": [(0.9, w.f["b"])]},
            rows[2]["question"]: {"final": [(0.9, w.f["a"]), (0.8, c)]},   # rank 2
            rows[3]["question"]: {"final": [(0.9, w.f["a"])]},             # a miss
            rows[4]["question"]: {"final": [], "rc": 1}, rows[5]["question"]: {"final": [], "rc": 4, "sleep": 0.4},
            rows[6]["question"]: {"final": [(0.9, w.f["e"])], "verdict": "TRUE (supported 0.95)"},
            rows[7]["question"]: {"final": [(0.9, w.f["e"])], "verdict": "FALSE (contradicted 0.93)", "rc": 5},
            rows[8]["question"]: {"final": [], "verdict": "NOT FOUND", "rc": 1}}
    bar = [{"measure": "top5", "min_rate": 0.75}, {"measure": "rank1", "min_rate": 0.5},
           {"measure": "absent", "min_rate": 1.0}, {"measure": "claims_right", "min_rate": 1.0},
           {"measure": "claims_wrong", "max_count": 0}, {"measure": "claims_absent_asserted", "max_count": 0},
           {"measure": "secs", "percentile": 50, "max_secs": 30}, {"measure": "secs", "percentile": 90, "max_secs": 60}]
    rc, rec = go(w, rows, plan, bar, "--seed", "7")
    out = capsys.readouterr().out
    assert rc == 0 and rec["verdict"] == "supported" and rec["stop_reason"] == "complete"
    assert "verdict: supported" in out and out.count("PASS") == 8 and "FAIL" not in out
    assert (rec["asks"], rec["n_run"], rec["N"], rec["error_count"]) == (9, 9, 9, 0)
    by = {r["name"]: r for r in rec["rows"]}
    assert (by["top5"]["hits"], by["top5"]["n"], by["top5"]["need"]) == (3, 4, 3)
    assert (by["rank1"]["hits"], by["rank1"]["n"]) == (2, 4)
    assert by["top5"]["by_split"] == {"tuned": {"hits": 2, "n": 2}, "retrospective": {"hits": 1, "n": 2}}
    assert (by["absent"]["hits"], by["claims_right"]["hits"], by["claims_wrong"]["hits"]) == (2, 2, 0)
    assert by["secs_p50"]["bar"] == {"percentile": 50, "max_secs": 30} and by["secs_p50"]["n"] == 9
    assert rec["seed"] == 7 and sorted(c["question"] for c in rec["cases"]) == sorted(r["question"] for r in rows)
    exits = {c["question"]: c["rc"] for c in rec["cases"] if c["stratum"] == "absent"}
    assert sorted(exits.values()) == [1, 4]
    assert all(c["secs"] >= 0.4 for c in rec["cases"] if c["question"] == rows[5]["question"])
    assert rec["judge"] == "typesafe-jev" and rec["implementation"] == "profile" and rec["env_names"] == []
    assert rec["fingerprint"] == js.fingerprint(Path(rec["ask"]), "typesafe-jev")["fingerprint"]
    assert rec["cases_sha256"] and rec["bar_sha256"] and rec["max_excluded"] == 0
    assert rec["state_root"] and set(rec["snapshot"]) == {"state", "answers_db", "registry"}
    assert len(asked(w.tmp)) == 9 and rec["start"] and rec["end"]


def test_a_failing_row_is_not_supported_after_a_full_run(w, capsys):
    rows = [Q(w, "one", "a"), Q(w, "two", "b")]
    plan = {rows[0]["question"]: {"final": [(0.9, w.f["a"])]}, rows[1]["question"]: {"final": [(0.9, w.f["c"])]}}
    rc, rec = go(w, rows, plan, [{"measure": "top5", "min_rate": 1.0}])
    assert rc == 1 and rec["verdict"] == "not supported" and rec["stop_reason"] == "bar-unreachable: top5"
    assert "FAIL  top5" in capsys.readouterr().out


def test_one_asserted_wrong_claim_stops_the_run_early_not_supported(w):
    rows = [{"kind": "claim", "question": f"Sam owns Quillbrook plot {i}.", "expected": "TRUE", "gold": [w.f["e"]]}
            for i in range(4)]
    plan = {r["question"]: {"final": [(0.9, w.f["e"])], "verdict": "FALSE (contradicted 0.93)", "rc": 5} for r in rows}
    rc, rec = go(w, rows, plan, [{"measure": "claims_wrong", "max_count": 0}])
    assert rc == 1 and rec["verdict"] == "not supported" and rec["stop_reason"].startswith("bar-unreachable")
    assert rec["asks"] == len(asked(w.tmp)) == 1 < rec["N"] == 4


@pytest.mark.parametrize("top5_rate,mark,decided", [(0.75, "OPEN", None), (0.25, "PASS", True)])
def test_an_early_stop_marks_another_row_pass_only_when_the_cases_run_already_settle_it(w, capsys, top5_rate, mark, decided):
    # Every case is found at rank 2: top5 hits each time, rank1 never does, so rank1 (0.75 of 8 = 6) is out of reach
    # after 3 asks. top5 has 3 hits of 3 asked, but 8 cases in all: 0.75 needs 6 (still open), 0.25 needs 2 (met).
    rows = [Q(w, name, "abcdefgh"[i]) for i, name in enumerate(["one", "two", "three", "four", "five", "six", "seven", "eight"])]
    plan = {r["question"]: {"final": [(0.9, w.f["h"]), (0.8, w.f["abcdefgh"[i]])]} for i, r in enumerate(rows)}
    rc, rec = go(w, rows, plan, [{"measure": "top5", "min_rate": top5_rate}, {"measure": "rank1", "min_rate": 0.75}])
    by = {r["name"]: r for r in rec["rows"]}
    assert rc == 1 and rec["verdict"] == "not supported" and rec["stop_reason"] == "bar-unreachable: rank1" and rec["asks"] == 3
    assert (by["rank1"]["pass"], by["rank1"]["hits"], by["rank1"]["n"], by["rank1"]["not_run"]) == (False, 0, 8, 5)
    top5 = by["top5"]
    assert (top5["pass"], top5["hits"], top5["n"], top5["not_run"]) == (decided, 3, 8, 5)  # n is every case the row will see
    assert top5["need"] == js.need(round(top5_rate * 100), 8)
    out = capsys.readouterr().out
    assert f"{mark:5} top5" in out and "FAIL  rank1" in out and "3/8, 5 not run" in out


def test_an_early_stop_leaves_a_count_row_and_a_rate_row_open_whichever_case_ran_first(w, capsys):
    # The time row is out of reach after the first ask (every ask outlasts it), whatever case came first.
    rows = [Q(w, name, "abc"[i]) for i, name in enumerate(["one", "two", "three"])] + [
        {"kind": "claim", "question": f"Sam owns Quillbrook plot {i}.", "expected": "TRUE", "gold": [w.f["e"]]} for i in range(2)]
    plan = {r["question"]: {"final": [(0.9, r["gold"][0])], "sleep": 0.2,
                            **({"verdict": "TRUE (supported 0.95)"} if "kind" in r else {})} for r in rows}
    rc, rec = go(w, rows, plan, [{"measure": "top5", "min_rate": 0.75}, {"measure": "claims_wrong", "max_count": 0},
                                 {"measure": "secs", "percentile": 90, "max_secs": 0.05}])
    by = {r["name"]: r for r in rec["rows"]}
    assert rc == 1 and rec["stop_reason"] == "bar-unreachable: secs_p90" and rec["asks"] == 1
    assert by["secs_p90"]["pass"] is False
    assert by["top5"]["pass"] is None and by["top5"]["n"] == 3 and by["top5"]["need"] == 3 and by["top5"]["not_run"] >= 2
    assert by["claims_wrong"]["pass"] is None and by["claims_wrong"]["not_run"] >= 1
    out = capsys.readouterr().out
    assert "OPEN  top5" in out and "OPEN  claims_wrong" in out and "FAIL  secs_p90" in out and "PASS" not in out


def test_a_timed_out_absent_claim_is_held_counted_as_an_error_and_the_run_goes_on(w):
    rows = [{"kind": "claim", "question": "Sam owns the Quillbrook cottage.", "expected": "TRUE", "gold": [w.f["e"]]},
            {"kind": "claim", "question": "Sam owns a Quillbrook boat.", "expected": "ABSENT", "gold": []}]
    plan = {rows[0]["question"]: {"final": [(0.9, w.f["e"])], "verdict": "TRUE (supported 0.95)"},
            rows[1]["question"]: {"sleep": 5}}
    rc, rec = go(w, rows, plan, [{"measure": "claims_right", "min_rate": 1.0},
                                 {"measure": "claims_absent_asserted", "max_count": 0}], "--timeout", "1")
    assert rc == 0 and rec["verdict"] == "supported"
    assert rec["error_count"] == 1 and rec["asks"] == 2
    boat = next(c for c in rec["cases"] if c["question"] == rows[1]["question"])
    assert "timeout" in boat["error"] and boat["secs"] >= 1


def test_an_errored_true_claim_fails_claims_right_only(w):
    rows = [{"kind": "claim", "question": "Sam owns the Quillbrook cottage.", "expected": "TRUE", "gold": [w.f["e"]]}]
    plan = {rows[0]["question"]: {"sleep": 5}}
    rc, rec = go(w, rows, plan, [{"measure": "claims_right", "min_rate": 0.5},
                                 {"measure": "claims_wrong", "max_count": 0}], "--timeout", "1")
    by = {r["name"]: r for r in rec["rows"]}
    assert rc == 1 and not by["claims_right"]["pass"] and by["claims_wrong"]["pass"] and by["claims_wrong"]["hits"] == 0
    assert rec["error_count"] == 1


def test_the_integer_rate_rule_needs_7_of_25_for_0_28_not_8():
    assert js.need(28, 25) == 7                      # a float ceil gives 8
    assert js.need(55, 20) == 11 and js.need(7, 100) == 7   # two more float traps
    assert js.need(33, 9) == 3 and js.need(67, 12) == 9 and js.need(50, 7) == 4 and js.need(100, 13) == 13


@pytest.mark.parametrize("hits,rc,verdict", [(7, 0, "supported"), (6, 1, "not supported")])
def test_rate_0_28_over_25_needs_7(w, hits, rc, verdict):
    rows = [{"question": f"Where is the Quillbrook gate {i} policy?", "absent": True, "gold": []} for i in range(25)]
    plan = {r["question"]: ({"final": []} if i < hits else {"final": [(0.9, w.f["a"])]}) for i, r in enumerate(rows)}
    got, rec = go(w, rows, plan, [{"measure": "absent", "min_rate": 0.28}])
    assert (got, rec["verdict"]) == (rc, verdict)
    row = rec["rows"][0]
    assert row["need"] == 7 and (row["hits"] == hits if rc == 0 else row["hits"] < 7)


def test_two_principals_both_run_and_n_counts_every_row(w, env):
    (env / "sam").mkdir()
    rows = [Q(w, "one", "a"), Q(w, "two", "b", principal="sam"), Q(w, "three", "c", principal="sam")]
    plan = {r["question"]: {"final": [(0.9, w.f[g])]} for r, g in zip(rows, "abc")}
    rc, rec = go(w, rows, plan, [{"measure": "top5", "min_rate": 1.0}])
    assert rc == 0 and rec["N"] == rec["n_run"] == rec["asks"] == 3
    assert {c["principal"] for c in rec["cases"]} == {"me", "sam"}
    assert {Path(e["state"]).name for e in _log(w.tmp)} == {"me", "sam"}


def test_a_row_with_no_principal_is_bad_input(w, capsys):
    cases = w.tmp / "nop.jsonl"
    cases.write_text(json.dumps({"question": "Where is it?", "gold": [w.f["a"]]}) + "\n")
    barf = w.tmp / "nopbar.json"
    barf.write_text(json.dumps({"max_excluded": 0, "rows": [{"measure": "top5", "min_rate": 1.0}]}))
    with pytest.raises(SystemExit) as e:
        js.main(["--ask", build(w.tmp, {}), "--judge", "typesafe-jev", "--cases", str(cases), "--cases-sha256", sha(cases),
                 "--bar", str(barf), "--bar-sha256", sha(barf), "--record", str(w.tmp / "r.json"), "--max-asks", "5"])
    assert e.value.code == 2 and asked(w.tmp) == []
    assert "case has no principal" in capsys.readouterr().err


def test_a_case_whose_source_changes_during_its_ask_is_excluded_counted_and_the_run_goes_on(w):
    rows = [Q(w, "one", "a"), Q(w, "two", "b"), Q(w, "three", "c")]
    plan = {rows[0]["question"]: {"final": [(0.9, w.f["a"])], "write": {w.f["a"]: "changed under the ask"}},
            rows[1]["question"]: {"final": [(0.9, w.f["b"])]}, rows[2]["question"]: {"final": [(0.9, w.f["c"])]}}
    rc, rec = go(w, rows, plan, [{"measure": "top5", "min_rate": 1.0}], max_excluded=1)
    assert rc == 0 and rec["verdict"] == "supported" and rec["asks"] == 3
    assert [d["question"] for d in rec["excluded"]["drift"]] == [rows[0]["question"]]
    assert (rec["N"], rec["n_run"]) == (3, 2) and rec["rows"][0]["n"] == 2


def test_a_case_whose_gold_is_all_missing_at_the_start_is_excluded_and_not_in_n(w):
    rows = [Q(w, "one", "a"), {"question": "Where is the Quillbrook deed?", "gold": [str(w.tmp / "gone.md")]}]
    plan = {rows[0]["question"]: {"final": [(0.9, w.f["a"])]}}
    rc, rec = go(w, rows, plan, [{"measure": "top5", "min_rate": 1.0}], max_excluded=1)
    assert rc == 0 and rec["excluded"]["missing_gold"] == [rows[1]["question"]]
    assert (rec["N"], rec["asks"]) == (1, 1) and asked(w.tmp) == [rows[0]["question"]]


def test_more_excluded_cases_than_the_bar_allows_is_incomplete(w):
    gone = [{"question": f"Where is the Quillbrook deed {i}?", "gold": [str(w.tmp / f"gone{i}.md")]} for i in range(2)]
    rows = [Q(w, "one", "a"), *gone]
    rc, rec = go(w, rows, {rows[0]["question"]: {"final": [(0.9, w.f["a"])]}}, [{"measure": "top5", "min_rate": 1.0}],
                 max_excluded=1)
    assert rc == 3 and rec["verdict"] == "incomplete" and rec["stop_reason"] == "excluded over max_excluded"
    assert rec["asks"] == 0 and asked(w.tmp) == []
    # Drift counts the same way, and stops the run when it passes the cap.
    rows = [Q(w, f"n{i}", "abc"[i]) for i in range(3)]
    plan = {r["question"]: {"final": [(0.9, w.f["abc"[i]])], "write": {w.f["abc"[i]]: "changed"}} for i, r in enumerate(rows)}
    rc, rec = go(w, rows, plan, [{"measure": "top5", "min_rate": 1.0}], max_excluded=1, record="rec2.json")
    assert rc == 3 and rec["stop_reason"] == "excluded over max_excluded" and rec["asks"] == 2


def test_the_time_cap_ends_the_run_incomplete(w):
    rows = [Q(w, f"n{i}", "abc"[i]) for i in range(3)]
    plan = {r["question"]: {"final": [(0.9, w.f["abc"[i]])], "sleep": 0.5} for i, r in enumerate(rows)}
    rc, rec = go(w, rows, plan, [{"measure": "top5", "min_rate": 1.0}], "--time-cap-min", "0.001")
    assert rc == 3 and rec["verdict"] == "incomplete" and rec["stop_reason"] == "time cap" and rec["asks"] < 3
    assert all(c["secs"] >= 0.5 for c in rec["cases"])


def test_a_prepare_cache_change_during_the_run_voids_it(w):
    rows = [Q(w, "one", "a"), Q(w, "two", "b")]
    cache = w.tmp / f"build{w.calls + 1}" / "skills/super-jev/prepare-cache/new-set.json"
    plan = {rows[0]["question"]: {"final": [(0.9, w.f["a"])], "write": {str(cache): "{}"}},
            rows[1]["question"]: {"final": [(0.9, w.f["b"])]}}
    rc, rec = go(w, rows, plan, [{"measure": "top5", "min_rate": 1.0}])
    assert rc == 3 and rec["verdict"] == "incomplete" and rec["stop_reason"] == "prepare-cache changed during the run"


def test_a_pointer_not_ready_is_could_not_start_with_no_ask_spent(w, capsys):
    rows = [Q(w, "one", "a")]
    plan = {"status": "Connections for me (registered snapshots):\n  p1: stale, 2 files changed\nNext: ..."}
    rc, rec = go(w, rows, plan, [{"measure": "top5", "min_rate": 1.0}])
    assert rc == 3 and rec["verdict"] == "incomplete" and rec["stop_reason"] == "could-not-start"
    assert rec["asks"] == 0 and asked(w.tmp) == [] and "p1: stale" in rec["not_ready"]
    out = capsys.readouterr().out
    assert "not started, no ask spent" in out and "OPEN  top5" in out and "FAIL" not in out
    assert rec["rows"][0]["pass"] is None and rec["rows"][0]["not_run"] == 1


def test_a_parent_superjev_variable_never_reaches_the_child_and_an_env_value_does_not_reach_the_record(w, monkeypatch, capsys):
    monkeypatch.setenv("SUPERJEV_QUILLBROOK", "from-the-parent")
    monkeypatch.setenv("SUPERJEV_JUDGE", "laya")
    monkeypatch.setenv("SAM_PASSES_THROUGH", "yes")
    rows = [Q(w, "one", "a")]
    plan = {rows[0]["question"]: {"final": [(0.9, w.f["a"])], "errors": [f"judge said {TOKEN}"]}}
    rc, rec = go(w, rows, plan, [{"measure": "top5", "min_rate": 1.0}], "--env", f"SAM_TOKEN={TOKEN}", judge="fake")
    seen = _log(w.tmp)[0]["env"]
    assert seen["SUPERJEV_JUDGE"] == "fake" and seen["SAM_TOKEN"] == TOKEN and seen["SAM_PASSES_THROUGH"] == "yes"
    assert "SUPERJEV_QUILLBROOK" not in seen and seen["SUPERJEV_REPLAY"] == "1" and seen["SUPERJEV_AUTO_CACHE"] == "0"
    assert rec["env_names"] == ["SAM_TOKEN"] and rec["implementation"] == "fake"
    text = (w.tmp / "rec.json").read_text()
    assert TOKEN not in text and "<env value>" in text  # the one place it could leak, an error line, is scrubbed
    assert TOKEN not in "".join(capsys.readouterr())
    assert rec["fingerprint"] == js.fingerprint(Path(rec["ask"]), "fake", {"SAM_TOKEN": TOKEN})["fingerprint"]
    # The same environment again, a different judge name, gives a different fingerprint.
    assert rec["fingerprint"] != js.fingerprint(Path(rec["ask"]), "typesafe-jev", {"SAM_TOKEN": TOKEN})["fingerprint"]


def test_the_seed_picks_the_recorded_case_order(w):
    rows = [Q(w, f"n{i}", "abcdefgh"[i]) for i in range(6)]
    plan = {r["question"]: {"final": [(0.9, w.f["abcdefgh"[i]])]} for i, r in enumerate(rows)}
    orders = {}
    for seed in (0, 0, 1, 2, 3):
        rc, rec = go(w, rows, plan, [{"measure": "top5", "min_rate": 1.0}], "--seed", str(seed), record=f"r{len(orders)}-{seed}.json")
        orders.setdefault(seed, []).append([c["question"] for c in rec["cases"]])
        assert rec["seed"] == seed and sorted(orders[seed][-1]) == sorted(r["question"] for r in rows)
    assert orders[0][0] == orders[0][1]
    assert len({tuple(v[0]) for v in orders.values()}) > 1


BAD = {
    "no bar rows": ({"max_excluded": 0, "rows": []}, "at least one row"),
    "no max_excluded": ({"rows": [{"measure": "top5", "min_rate": 0.5}]}, "there are no defaults"),
    "three decimals": ({"max_excluded": 0, "rows": [{"measure": "top5", "min_rate": 0.285}]}, "at most two decimals"),
    "a rate above 1": ({"max_excluded": 0, "rows": [{"measure": "top5", "min_rate": 1.5}]}, "at most 1"),
    "an unknown measure": ({"max_excluded": 0, "rows": [{"measure": "vibes", "min_rate": 0.5}]}, "measure must be one of"),
    "a misspelt key": ({"max_excluded": 0, "rows": [{"measure": "top5", "min_rat": 0.5}]}, "a rate is a number"),
    "an extra key": ({"max_excluded": 0, "rows": [{"measure": "top5", "min_rate": 0.5, "note": "x"}]}, "expected exactly"),
    "a repeated row": ({"max_excluded": 0, "rows": [{"measure": "top5", "min_rate": 0.5}] * 2}, "given twice"),
    "a stratum with no cases": ({"max_excluded": 0, "rows": [{"measure": "claims_wrong", "max_count": 0}]}, "the file has none"),
    "a negative cap": ({"max_excluded": -1, "rows": [{"measure": "top5", "min_rate": 0.5}]}, "max_excluded must be a whole number"),
}


@pytest.mark.parametrize("what", sorted(BAD))
def test_a_bad_bar_is_exit_2_before_any_ask_and_no_record(w, what, capsys):
    rows = [Q(w, "one", "a")]
    bar, why = BAD[what]
    rc, rec = go(w, rows, {}, None, bar_obj=bar)
    assert rc == 2 and rec is None and asked(w.tmp) == []
    assert why in capsys.readouterr().err


def test_a_sha_mismatch_is_exit_2_and_prints_the_actual_sha(w, capsys):
    cases = w.tmp / "c.jsonl"
    cases.write_text(json.dumps({"principal": "me", "question": "Where is it?", "gold": [w.f["a"]]}) + "\n")
    barf = w.tmp / "b.json"
    barf.write_text(json.dumps({"max_excluded": 0, "rows": [{"measure": "top5", "min_rate": 1.0}]}))
    base = ["--ask", build(w.tmp, {}), "--judge", "typesafe-jev", "--max-asks", "5", "--record", str(w.tmp / "r.json")]
    for flag, good_cases, good_bar in (("cases", "0" * 64, sha(barf)), ("bar", sha(cases), "f" * 64)):
        with pytest.raises(SystemExit) as e:
            js.main([*base, "--cases", str(cases), "--cases-sha256", good_cases, "--bar", str(barf), "--bar-sha256", good_bar])
        err = capsys.readouterr().err
        assert e.value.code == 2 and f"{flag} sha256 mismatch" in err
        assert (sha(cases) if flag == "cases" else sha(barf)) in err
    assert asked(w.tmp) == [] and not (w.tmp / "r.json").exists()


@pytest.mark.parametrize("extra,why", [(["--judge", "nope"], "unknown judge"),
                                       (["--env", "SUPERJEV_JUDGE=laya"], "the tool sets this one itself"),
                                       (["--env", "SUPERJEV_REPLAY=0"], "the tool sets this one itself"),
                                       (["--env", "SUPERJEV_AUTO_CACHE=0"], "the tool sets this one itself"),  # even its own value
                                       (["--env", "NO_EQUALS"], "a value with no NAME=")])
def test_an_unknown_judge_or_a_setting_the_tool_owns_is_exit_2_with_no_ask(w, extra, why, capsys):
    rows = [Q(w, "one", "a")]
    rc, rec = go(w, rows, {}, [{"measure": "top5", "min_rate": 1.0}], *extra)
    assert rc == 2 and rec is None and asked(w.tmp) == []
    assert why in capsys.readouterr().err


@pytest.mark.parametrize("name", ["SUPERJEV_JUDGE", "SUPERJEV_REPLAY", "SUPERJEV_AUTO_CACHE"])
def test_the_fingerprint_command_refuses_a_setting_the_tool_owns_like_the_run_does(w, name, capsys):
    base = ["--fingerprint", "--ask", build(w.tmp, {}), "--judge", "typesafe-jev"]
    assert js.main([*base, "--env", "QUILLBROOK_URL=http://127.0.0.1:1"]) == 0  # any other setting is fine
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        js.main([*base, "--env", f"{name}=1"])
    assert e.value.code == 2 and "the tool sets this one itself" in capsys.readouterr().err


def test_a_record_is_never_overwritten(w, capsys):
    rows = [Q(w, "one", "a")]
    plan = {rows[0]["question"]: {"final": [(0.9, w.f["a"])]}}
    bar = [{"measure": "top5", "min_rate": 1.0}]
    assert go(w, rows, plan, bar)[0] == 0
    before = (w.tmp / "rec.json").read_text()
    capsys.readouterr()
    rc, _ = go(w, rows, plan, bar)
    assert rc == 2 and (w.tmp / "rec.json").read_text() == before and len(asked(w.tmp)) == 1
    assert "never overwritten" in capsys.readouterr().err


def test_a_stray_superjev_judge_in_the_callers_shell_does_not_stop_the_tool(w, monkeypatch):
    monkeypatch.setenv("SUPERJEV_JUDGE", "bogus")
    rows = [Q(w, "one", "a")]
    rc, rec = go(w, rows, {rows[0]["question"]: {"final": [(0.9, w.f["a"])]}}, [{"measure": "top5", "min_rate": 1.0}])
    assert rc == 0 and rec["verdict"] == "supported"
