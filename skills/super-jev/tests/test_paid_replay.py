#!/usr/bin/env python3
"""Paid replay: the cap, the per-case old -> new verdicts, the wobble, the frozen state
and backend, and the refusals, all against fake ask builds. No Jev, no network.

    python3 -m pytest skills/super-jev/tests/test_paid_replay.py -q
"""
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
import paid_replay as pr  # noqa: E402
import scorecard as sc  # noqa: E402
import ask  # noqa: E402

# A fake build (it declares REPLAY_PROTOCOL = 2): answers each question from PLAN, writes the
# trace ask.py would, and logs what it saw. PLAN may also make it write to its state, its
# memory database, a file, or leave a child running.
FAKE = r'''
REPLAY_PROTOCOL = 2  # honors SUPERJEV_REPLAY
import json, os, sqlite3, subprocess, sys, time
from pathlib import Path
PLAN = %r
args = sys.argv[1:]
principal = args[args.index("--principal") + 1]
if args[-1] == "--status":  # the free preflight: PLAN["status"] is its output
    print(PLAN.get("status", "Connections for " + principal + " (registered snapshots):\n  p1: ready\nNext: ..."))
    sys.exit(PLAN.get("status_rc", 0))
claim = "--claim" in args
q = args[args.index("--claim") + 1] if claim else args[-1]
root = Path(os.environ["SUPERJEV_STATE_DIR"])
sdir = root / principal
cfg = json.loads((root / "_memory" / "config.json").read_text())
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"q": q, "state": str(sdir), "replay": os.environ.get("SUPERJEV_" + "REPLAY"), "db": cfg["db"],
                        "files": sorted(p.name for p in sdir.iterdir())}) + "\n")
p = PLAN.get(q, {})
for path, text in p.get("write", {}).items():
    Path(path).write_text(text)
if p.get("child"):
    subprocess.Popen([sys.executable, "-c", "import time, pathlib; time.sleep(2); pathlib.Path(%%r).write_text('alive')" %% p["child"]])
time.sleep(p.get("sleep", 0))
(sdir / "claim-verdicts.json").write_text("{}")  # stray writes must stay in the copy
(sdir / "pointer_health.json").write_text("written by a replay")
with sqlite3.connect(cfg["db"]) as db:
    db.execute("INSERT INTO cache VALUES ('written by a replay')")
if p.get("other"):
    (root / p["other"]).mkdir(exist_ok=True)
    (root / p["other"] / "traces.jsonl").write_text("{}")
trace = {"kind": "trace", "lookup_id": "id-" + q[:4], "tier": p.get("tier", "confirmed"), "question": p.get("traced", q),
         "final_ranked": [{"score": s, "path": f} for s, f in p.get("final", [])],
         "content_check": {k: {"score": v} for k, v in p.get("checked", {}).items()}, "errors": p.get("errors", [])}
with open(sdir / "traces.jsonl", "a") as f:
    f.write(json.dumps(trace) + "\n")
if claim and "verdict" in p:
    print(p["verdict"])
sys.exit(p.get("rc", 0))
'''

TOP = [(0.99, "/a.md"), (0.95, "/b.md"), (0.9, "/c.md"), (0.85, "/d.md"), (0.5, "/e.md")]


def _build(tmp_path, name, plan, code=None):
    d = tmp_path / name / "skills" / "super-jev"
    d.mkdir(parents=True, exist_ok=True)
    cli = tmp_path / name / "experiments" / "verified-pointer-memory" / "cli.py"
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text("")
    (d / "ask.py").write_text(FAKE % (plan,) if code is None else code)
    return str(d / "ask.py")


def _cases(tmp_path, rows, name="cases.jsonl"):
    f = tmp_path / name
    f.write_text("".join(json.dumps({"principal": "me", "split": "tuned", **r}) + "\n" for r in rows))
    return str(f)


def _register(registry, paths):
    """The fixture's connected originals: the paths a replay treats as pinned."""
    Path(registry).write_text(json.dumps({"version": 1, "datasets": {"d": {"originals": [{"path": p} for p in paths]}}}))


def _rows(db):
    with sqlite3.connect(db) as c:
        return c.execute("SELECT count(*) FROM cache").fetchone()[0]


@pytest.fixture
def env(tmp_path, monkeypatch):
    real = tmp_path / "real-state"
    (real / "me").mkdir(parents=True)
    (real / "_memory").mkdir()
    mem = tmp_path / "real-memory"
    mem.mkdir()
    with sqlite3.connect(mem / "answers.sqlite") as c:
        c.execute("CREATE TABLE cache (k TEXT)")
    _register(mem / "datasets.json", [f"/{c}.md" for c in "abcdefghvwxyz"])
    (real / "_memory" / "config.json").write_text(json.dumps(
        {"db": str(mem / "answers.sqlite"), "registry": str(mem / "datasets.json")}))
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(real))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "log.jsonl"))
    return real


def _run(tmp_path, cases, old, new, *extra):
    return pr.main(["--principal", "me", "--cases", cases, "--ask", _build(tmp_path, "old", old),
                    "--ask", _build(tmp_path, "new", new), "--max-asks", "20", "--json", *extra])


def _log(tmp_path):
    p = tmp_path / "log.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_refuses_over_the_cap_and_says_how_many_before_any_ask(tmp_path, env, capsys):
    cases = _cases(tmp_path, [{"question": f"q{i}", "gold": ["/a.md"]} for i in range(3)])
    with pytest.raises(SystemExit) as e:
        pr.main(["--principal", "me", "--cases", cases, "--ask", _build(tmp_path, "old", {}),
                 "--ask", _build(tmp_path, "new", {}), "--max-asks", "5"])
    assert e.value.code == 2
    assert "needs 6 paid asks" in capsys.readouterr().err
    assert _log(tmp_path) == []
    with pytest.raises(SystemExit):  # the cap is required
        pr.main(["--principal", "me", "--cases", cases, "--ask", "x", "--ask", "y"])


def test_gained_lost_same_per_split_and_a_loss_fails(tmp_path, env, capsys):
    lost_top = [(0.99, "/x.md"), (0.95, "/y.md")]
    cases = _cases(tmp_path, [{"question": "gain", "gold": ["/a.md"]},
                              {"question": "loss", "gold": ["/h.md"], "split": "held-out"},
                              {"question": "same", "gold": ["/b.md"]}])
    old = {"gain": {"final": lost_top}, "loss": {"final": [(0.99, "/h.md")]}, "same": {"final": TOP}}
    new = {"gain": {"final": TOP}, "loss": {"final": lost_top}, "same": {"final": TOP}}
    assert _run(tmp_path, cases, old, new) == 1
    rep = json.loads(capsys.readouterr().out)
    got = {r["question"]: r["result"] for r in rep["rows"]}
    assert got == {"gain": "gained", "loss": "lost", "same": "same"}
    assert rep["splits"]["tuned"] == {"gained": 1, "lost": 0, "same": 1, "inconclusive": 0}
    assert rep["splits"]["held-out"]["lost"] == 1
    row = next(r for r in rep["rows"] if r["question"] == "same")
    assert (row["old"]["rank"], row["old"]["tier"], row["old"]["lookup_id"]) == (2, "confirmed", "id-same")


def test_a_change_inside_the_wobble_is_inconclusive_except_a_held_out_drop(tmp_path, env, capsys):
    # Gold was 5th at 0.5; now 0.48 against a 0.55 file that took its slot: a wobble.
    near = [(0.99, "/a.md"), (0.95, "/b.md"), (0.9, "/c.md"), (0.85, "/d.md"), (0.55, "/z.md")]
    old, new = {"edge": {"final": TOP, "checked": {"/z.md": 0.45}}}, {"edge": {"final": near, "checked": {"/e.md": 0.48}}}
    cases = _cases(tmp_path, [{"question": "edge", "gold": ["/e.md"]}])
    assert _run(tmp_path, cases, old, new) == 0
    assert json.loads(capsys.readouterr().out)["rows"][0]["result"] == "inconclusive"
    held = _cases(tmp_path, [{"question": "edge", "gold": ["/e.md"], "split": "held-out"}])
    assert _run(tmp_path, held, old, new) == 1
    assert json.loads(capsys.readouterr().out)["rows"][0]["result"] == "inconclusive"


def test_a_fifth_place_gold_that_drops_far_out_is_a_loss_not_noise(tmp_path, env, capsys):
    # Astra's repro on d6eb3b8: the 5th gold was its own cutoff, so it always looked near.
    other = [(0.99, "/v.md"), (0.95, "/w.md"), (0.9, "/x.md"), (0.85, "/y.md"), (0.8, "/z.md")]
    cases = _cases(tmp_path, [{"question": "q", "gold": ["/e.md"]}])
    assert _run(tmp_path, cases, {"q": {"final": TOP}}, {"q": {"final": other, "checked": {"/e.md": 0.0}}}) == 1
    assert json.loads(capsys.readouterr().out)["rows"][0]["result"] == "lost"


def test_gold_just_outside_the_top_5_on_both_sides_of_a_flip_is_inconclusive(tmp_path, env, capsys):
    cases = _cases(tmp_path, [{"question": "q", "gold": ["/a.md"]}])
    other = [(0.99, "/v.md"), (0.95, "/w.md"), (0.9, "/x.md"), (0.85, "/y.md"), (0.8, "/z.md")]
    old = {"q": {"final": [(0.82, "/a.md")] + other[:4], "checked": {"/z.md": 0.78}}}
    new = {"q": {"final": other, "checked": {"/a.md": 0.75}}}
    assert _run(tmp_path, cases, old, new) == 0
    assert json.loads(capsys.readouterr().out)["rows"][0]["result"] == "inconclusive"


def test_errors_timeouts_and_saved_answers_are_inconclusive_never_a_loss(tmp_path, env, capsys):
    cases = _cases(tmp_path, [{"question": q, "gold": ["/a.md"]} for q in ("err", "slow", "crash", "saved")])
    old = {q: {"final": TOP} for q in ("err", "slow", "crash", "saved")}
    new = {"err": {"final": [], "errors": ["pointer p1: timeout"]}, "slow": {"sleep": 5},
           "crash": {"rc": 3}, "saved": {"tier": "cache"}}
    assert _run(tmp_path, cases, old, new, "--timeout", "1") == 0
    rep = json.loads(capsys.readouterr().out)
    assert {r["result"] for r in rep["rows"]} == {"inconclusive"}
    assert "timeout" in next(r for r in rep["rows"] if r["question"] == "slow")["new"]["error"]
    assert "saved answer" in next(r for r in rep["rows"] if r["question"] == "saved")["new"]["error"]


def test_a_timeout_kills_the_work_the_ask_started(tmp_path, env, capsys):
    marker = tmp_path / "child-was-alive"
    cases = _cases(tmp_path, [{"question": "q", "gold": ["/a.md"]}])
    new = {"q": {"child": str(marker), "sleep": 5}}
    _run(tmp_path, cases, {"q": {"final": TOP}}, new, "--timeout", "1")
    time.sleep(2.5)
    assert not marker.exists()


def test_claim_cases_grade_the_verdict_against_both_margins(tmp_path, env, capsys):
    cases = _cases(tmp_path, [{"question": "c1", "gold": ["/a.md"], "kind": "claim", "expected": "TRUE"},
                              {"question": "c2", "gold": ["/a.md"], "kind": "claim", "expected": "FALSE"}])
    old = {"c1": {"final": TOP, "verdict": "UNSURE: read these files"},
           "c2": {"final": TOP, "verdict": "UNSURE (contradicted 0.85): read these files"}}
    new = {"c1": {"final": TOP, "verdict": "TRUE (0.93)"}, "c2": {"final": TOP, "verdict": "FALSE (0.93)"}}
    assert _run(tmp_path, cases, old, new) == 0
    rep = json.loads(capsys.readouterr().out)
    got = {r["question"]: (r["result"], r["new"]["verdict"], r["old"]["verdict"]) for r in rep["rows"]}
    assert got == {"c1": ("gained", "TRUE", "UNSURE"), "c2": ("inconclusive", "FALSE", "UNSURE")}
    with pytest.raises(SystemExit):  # a claim case must say what it expects
        _run(tmp_path, _cases(tmp_path, [{"question": "c", "gold": ["/a.md"], "kind": "claim"}]), {}, {})


def test_a_false_claim_exit_is_graded_not_an_error(tmp_path, env, capsys):
    cases = _cases(tmp_path, [{"question": "c2", "gold": ["/a.md"], "kind": "claim", "expected": "FALSE"}])
    old = {"c2": {"final": TOP, "verdict": "UNSURE (contradicted 0.85): read these files"}}
    new = {"c2": {"final": TOP, "verdict": "FALSE (0.93)", "rc": 5}}  # ask exits 5 for a FALSE claim
    _run(tmp_path, cases, old, new)
    row = json.loads(capsys.readouterr().out)["rows"][0]
    assert "error" not in row["new"] and row["new"]["verdict"] == "FALSE" and row["new"]["ok"] is True


def test_a_claim_that_needs_setup_is_graded_like_an_ordinary_ask(tmp_path, env, capsys):
    cases = _cases(tmp_path, [{"question": "c1", "gold": ["/a.md"], "kind": "claim", "expected": "TRUE"}])
    old = {"c1": {"final": TOP, "verdict": "TRUE (0.93)"}}
    new = {"c1": {"final": [], "rc": 4}}  # nothing settled and a set was stale: exit 4, no verdict word
    _run(tmp_path, cases, old, new)
    row = json.loads(capsys.readouterr().out)["rows"][0]
    assert "error" not in row["new"] and row["new"]["verdict"] is None


def test_runs_never_see_or_write_real_state_links_or_the_memory_backend(tmp_path, env, capsys):
    (env / "me" / "claim-verdicts.json").write_text('{"c1": {"verdict": "TRUE"}}')
    (env / "me" / "approvals.jsonl").write_text("{}\n")
    (env / "me" / "pointer-words.json").write_text("{}")
    target = tmp_path / "health-target.json"
    target.write_text("real health")
    (env / "me" / "pointer_health.json").symlink_to(target)
    db = json.loads((env / "_memory" / "config.json").read_text())["db"]
    cases = _cases(tmp_path, [{"question": "c1", "gold": ["/a.md"], "kind": "claim", "expected": "TRUE"}])
    _run(tmp_path, cases, {}, {})
    seen = _log(tmp_path)
    assert len(seen) == 2 and len({s["state"] for s in seen}) == 2
    for s in seen:
        assert s["replay"] == "1" and not s["state"].startswith(str(env)) and s["db"] != db
        assert s["files"] == ["pointer-words.json", "pointer_health.json"]
    assert (env / "me" / "claim-verdicts.json").read_text() == '{"c1": {"verdict": "TRUE"}}'
    assert target.read_text() == "real health" and _rows(db) == 0
    assert set(json.loads(capsys.readouterr().out)["fingerprints"]) == {"prepare_cache", "state", "answers_db", "registry"}


def test_refuses_builds_whose_prepare_caches_differ_even_at_the_same_size(tmp_path, env, capsys):
    cases = _cases(tmp_path, [{"question": "q", "gold": ["/a.md"]}])
    old, new = _build(tmp_path, "old", {}), _build(tmp_path, "new", {})
    for b, text in ((old, "{1}"), (new, "{2}")):
        (Path(b).parent / "prepare-cache").mkdir()
        (Path(b).parent / "prepare-cache" / "p.json").write_text(text)
    with pytest.raises(SystemExit) as e:
        pr.main(["--principal", "me", "--cases", cases, "--ask", old, "--ask", new, "--max-asks", "2"])
    assert e.value.code == 2 and "prepare-caches" in capsys.readouterr().err
    assert _log(tmp_path) == []


def test_a_cache_or_read_file_that_changes_mid_replay_is_drift(tmp_path, env, capsys):
    gold = tmp_path / "gold.md"
    gold.write_text("v1")
    cases = _cases(tmp_path, [{"question": "q", "gold": [str(gold)]}])
    edits = {"q": {"final": [(0.99, str(gold))], "write": {str(gold): "v2"}}}
    assert _run(tmp_path, cases, {"q": {"final": [(0.99, str(gold))]}}, edits) == 0
    row = json.loads(capsys.readouterr().out)["rows"][0]
    assert row["result"] == "inconclusive" and "changed" in row["new"]["error"]
    cache = tmp_path / "new" / "skills" / "super-jev" / "prepare-cache" / "p.json"
    cache.parent.mkdir(exist_ok=True)
    (tmp_path / "old" / "skills" / "super-jev" / "prepare-cache").mkdir(exist_ok=True)
    (tmp_path / "old" / "skills" / "super-jev" / "prepare-cache" / "p.json").write_text("{}")
    cache.write_text("{}")
    assert _run(tmp_path, cases, {}, {"q": {"write": {str(cache): "{2}"}}}) == 3
    assert json.loads(capsys.readouterr().out)["drift"] == [str(cache.parent)]


OLD_ASKS = ("d6eb3b8", "0a3185d")  # SUPERJEV_REPLAY in name, without every replay guarantee


@pytest.mark.parametrize("ref", ("no-protocol",) + OLD_ASKS)
def test_refuses_a_build_without_the_replay_protocol_before_any_ask(tmp_path, env, capsys, ref):
    if ref == "no-protocol":
        code = FAKE.replace("REPLAY_PROTOCOL = 2", "# SUPERJEV_REPLAY") % ({},)
    else:  # the real, partly protected ask.py of an earlier commit on this branch
        got = subprocess.run(["git", "-C", str(SKILL), "show", f"{ref}:skills/super-jev/ask.py"],
                             capture_output=True, text=True)
        if got.returncode:
            pytest.skip(f"{ref} is not in this clone")
        code = got.stdout
    cases = _cases(tmp_path, [{"question": "q", "gold": ["/a.md"]}])
    old = _build(tmp_path, "old", {}, code=code)
    with pytest.raises(SystemExit) as e:
        pr.main(["--principal", "me", "--cases", cases, "--ask", old,
                 "--ask", _build(tmp_path, "new", {}), "--max-asks", "2"])
    assert e.value.code == 2 and "REPLAY_PROTOCOL = 2" in capsys.readouterr().err
    assert _log(tmp_path) == []
    assert pr.check_build(SKILL / "ask.py") == ""  # this build declares it


def test_a_file_only_the_old_run_read_that_changes_during_the_pair_is_drift(tmp_path, env, capsys):
    # Astra's repro on 0a3185d: old reads a competitor and misses gold; new changes the
    # competitor and finds gold without reading it. Only shared reads were compared.
    gold, rival = tmp_path / "gold.md", tmp_path / "rival.md"
    gold.write_text("gold")
    rival.write_text("v1")
    _register(json.loads((env / "_memory" / "config.json").read_text())["registry"], [str(rival)])
    cases = _cases(tmp_path, [{"question": "q", "gold": [str(gold)]}])
    old = {"q": {"final": [(0.99, str(rival))]}}
    new = {"q": {"final": [(0.99, str(gold))], "write": {str(rival): "v2"}}}
    assert _run(tmp_path, cases, old, new) == 0
    row = json.loads(capsys.readouterr().out)["rows"][0]
    assert row["result"] == "inconclusive" and "changed" in row["new"]["error"]


def test_a_connected_file_only_the_new_run_read_that_changed_before_it_is_drift(tmp_path, env, capsys):
    gold, rival = tmp_path / "gold.md", tmp_path / "rival.md"
    gold.write_text("gold")
    rival.write_text("v1")
    registry = json.loads((env / "_memory" / "config.json").read_text())["registry"]
    _register(registry, [str(rival)])
    cases = _cases(tmp_path, [{"question": "q", "gold": [str(gold)]}])
    old = {"q": {"final": [], "write": {str(rival): "v2"}}}
    new = {"q": {"final": [(0.99, str(gold)), (0.5, str(rival))]}}
    assert _run(tmp_path, cases, old, new) == 0
    row = json.loads(capsys.readouterr().out)["rows"][0]
    assert row["result"] == "inconclusive" and "changed" in row["new"]["error"]


def test_an_unfrozen_row_cannot_widen_a_frozen_held_out_row(tmp_path, env, capsys):
    row = {"question": "held", "gold": ["/right.md"], "split": "held-out", "group": "g", "source_family": "f"}
    frozen = tmp_path / "frozen.json"
    assert sc.main(["--principal", "me", "--cases", _cases(tmp_path, [row], "freeze.jsonl"),
                    "--freeze-held-out", str(frozen)]) == 0
    pin = hashlib.sha256(frozen.read_bytes()).hexdigest()
    capsys.readouterr()
    wider = _cases(tmp_path, [{**row, "gold": ["/wrong.md"]}])
    with pytest.raises(SystemExit) as e:
        pr.main(["--principal", "me", "--cases", wider, "--held-out", str(frozen), "--held-out-sha256", pin,
                 "--ask", _build(tmp_path, "old", {}), "--ask", _build(tmp_path, "new", {}), "--max-asks", "2"])
    assert e.value.code == 2 and "frozen reservation" in capsys.readouterr().err
    assert _log(tmp_path) == []


def test_ask_in_replay_mode_reads_no_saved_answer_or_claim_verdict(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_memory(req):
        calls.append(req["action"])
        return {"pointers": []} if req["action"] == "panel" else {"status": "verified-cache-hit", "answer": "Old."}

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setenv("SUPERJEV_REPLAY", "1")
    ask.lookup("what color?", "alice", tmp_path)
    assert "cached" not in calls and "CACHE HIT" not in capsys.readouterr().out
    proof = tmp_path / "proof.md"
    proof.write_text("x\n")
    ask.claim_cache_put(tmp_path, "The sky is blue.", "TRUE", str(proof), {"prob": 0.99})
    monkeypatch.setitem(ask._CLAIM, "text", "The sky is blue.")
    ask.lookup("The sky is blue.", "alice", tmp_path)
    assert "saved" not in capsys.readouterr().out


def test_ask_in_replay_mode_never_reconnects_or_heals_a_stale_pointer(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SUPERJEV_REPLAY", "1")

    def fake(req):
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1"}]}
        return {"status": "preparation-required"}
    monkeypatch.setattr(ask, "memory", fake)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({}, set(), None, {}))
    monkeypatch.setattr(ask.auto_heal, "reconnect_now", lambda *a, **k: pytest.fail("reconnect in a replay"))
    monkeypatch.setattr(ask.auto_heal, "reconnect_recipe", lambda *a, **k: pytest.fail("reconnect in a replay"))
    monkeypatch.setattr(ask.auto_heal, "maybe_heal", lambda *a, **k: pytest.fail("auto-heal in a replay"))
    ask.lookup("what is pending", "hf", tmp_path / "s")


def test_a_question_that_looks_like_a_flag_is_refused_before_the_cap(tmp_path, env, capsys):
    # Astra's repro on d6eb3b8: "--followup" ran ask.py's followup (up to 5 retries), not one lookup.
    cases = _cases(tmp_path, [{"question": "--followup", "gold": ["/a.md"]}])
    with pytest.raises(SystemExit) as e:
        pr.main(["--principal", "me", "--cases", cases, "--ask", _build(tmp_path, "old", {}),
                 "--ask", _build(tmp_path, "new", {}), "--max-asks", "0"])
    assert e.value.code == 2 and "may not start with '-'" in capsys.readouterr().err
    assert _log(tmp_path) == []


def test_a_trace_for_another_question_or_principal_is_not_this_case(tmp_path, env, capsys):
    cases = _cases(tmp_path, [{"question": q, "gold": ["/a.md"]} for q in ("mine", "theirs")])
    old = {"mine": {"final": TOP}, "theirs": {"final": TOP}}
    new = {"mine": {"final": [], "traced": "something else"}, "theirs": {"final": [], "other": "someone"}}
    assert _run(tmp_path, cases, old, new) == 0
    rows = {r["question"]: r for r in json.loads(capsys.readouterr().out)["rows"]}
    assert rows["mine"]["result"] == rows["theirs"]["result"] == "inconclusive"
    assert "another question" in rows["mine"]["new"]["error"]
    assert "another principal" in rows["theirs"]["new"]["error"]
    seen = _log(tmp_path)
    assert all(s["q"] in ("mine", "theirs") for s in seen)


def test_ask_reads_everything_after_a_double_dash_as_the_question(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(ask, "lookup", lambda q, principal, sdir: asked.append((q, principal)) or 0)
    monkeypatch.setattr(ask, "followup", lambda *a, **k: pytest.fail("ran --followup"))
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "alice", "--", "--followup"])
    assert ask.main() == 0 and asked == [("--followup", "alice")]


@pytest.mark.parametrize("pinned_by", ("nothing", "prepare-cache"))
def test_a_file_read_that_no_registry_or_cache_pins_is_unpinned(tmp_path, env, capsys, pinned_by):
    # Astra's repro on 09a4863: a missing pre-pair hash counted as unchanged. The old run
    # changes an unregistered competitor; the new one reads it and finds gold.
    gold, rival = tmp_path / "gold.md", tmp_path / "rival.md"
    gold.write_text("gold")
    rival.write_text("v1")
    if pinned_by == "prepare-cache":  # word search reads cache-named files: those are pinned too
        for b in ("old", "new"):
            cache = tmp_path / b / "skills" / "super-jev" / "prepare-cache"
            cache.mkdir(parents=True)
            (cache / "p.json").write_text(json.dumps({str(rival): {"pass": True}}))
    cases = _cases(tmp_path, [{"question": "q", "gold": [str(gold)]}])
    old = {"q": {"final": [], "write": {str(rival): "v2"}}}
    new = {"q": {"final": [(0.99, str(gold)), (0.5, str(rival))]}}
    assert _run(tmp_path, cases, old, new) == 0
    row = json.loads(capsys.readouterr().out)["rows"][0]
    assert row["result"] == "inconclusive"
    assert ("unpinned source" if pinned_by == "nothing" else "changed during the pair") in row["new"]["error"]


@pytest.mark.parametrize("status,rc", [
    ("Connections for me (registered snapshots):\n  p1: ready\n  brain-reviewed: stale (preparation-required); its files changed", 0),
    ("Not set up. Next: python3 setup.py", 1)])
def test_a_stale_pointer_or_no_status_refuses_before_any_paid_ask(tmp_path, env, capsys, status, rc):
    # First live run: a stale pointer turned every case into ERROR -> ERROR after 14 paid asks.
    cases = _cases(tmp_path, [{"question": "q", "gold": ["/a.md"]}])
    plan = {"status": status, "status_rc": rc}
    with pytest.raises(SystemExit) as e:
        _run(tmp_path, cases, {"q": {"final": TOP}}, plan)
    err = capsys.readouterr().err
    assert e.value.code == 2 and "no ask spent" in err
    assert ("brain-reviewed: stale" if rc == 0 else "status unavailable") in err
    assert _log(tmp_path) == []


@pytest.mark.parametrize("status", ["error", "unknown", "nothing", "empty"])
def test_preflight_refuses_unless_every_pointer_is_explicitly_ready(tmp_path, env, monkeypatch, capsys, status):
    # Astra's repro on f03c4e6: the real --status prints "broken: error" and exits 0.
    if status in ("error", "unknown"):
        monkeypatch.setattr(ask, "memory", lambda req: {"pointers": [
            {"pointer": "p1", "snapshotStatus": "available"}, {"pointer": "broken", "snapshotStatus": status}]})
        assert ask.connection_status("me") == 0
        text = capsys.readouterr().out
    else:
        text = "Nothing connected for me." if status == "nothing" else ""
    cases = _cases(tmp_path, [{"question": "q", "gold": ["/a.md"]}])
    with pytest.raises(SystemExit) as e:
        _run(tmp_path, cases, {"q": {"final": TOP}}, {"status": text})
    err = capsys.readouterr().err
    assert e.value.code == 2 and "no ask spent" in err
    assert (f"broken: {status}" if status in ("error", "unknown") else "not understood") in err
    assert _log(tmp_path) == []


def test_ordinary_asks_that_exit_1_or_4_are_graded_and_2_or_3_are_errors(tmp_path, env, capsys):
    qs = ("miss", "setup", "unsupported", "failed")
    cases = _cases(tmp_path, [{"question": q, "gold": ["/a.md"]} for q in qs])
    old = {q: {"final": TOP} for q in qs}
    new = {"miss": {"final": [], "rc": 1}, "setup": {"final": [], "rc": 4},
           "unsupported": {"final": [], "rc": 2}, "failed": {"final": [], "rc": 3}}
    _run(tmp_path, cases, old, new)
    rows = {r["question"]: r for r in json.loads(capsys.readouterr().out)["rows"]}
    for q in ("miss", "setup"):
        assert "error" not in rows[q]["new"], rows[q]
    for q in ("unsupported", "failed"):
        assert f"exit {new[q]['rc']}" in rows[q]["new"]["error"]
