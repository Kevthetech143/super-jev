#!/usr/bin/env python3
"""Paid replay: the cap, the per-case old -> new verdicts, the wobble and the cache
isolation, all against fake ask builds. No Jev, no network.

    python3 -m pytest skills/super-jev/tests/test_paid_replay.py -q
"""
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
import paid_replay as pr  # noqa: E402
import ask  # noqa: E402

# A fake build: answers each question from PLAN, writes the trace ask.py would, and
# logs what it saw (its state folder, the replay flag, which saved files were there).
FAKE = r'''
import json, os, sys, time
from pathlib import Path
PLAN = %r
args = sys.argv[1:]
principal = args[args.index("--principal") + 1]
claim = "--claim" in args
q = args[args.index("--claim") + 1] if claim else args[-1]
sdir = Path(os.environ["SUPERJEV_STATE_DIR"]) / principal
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"q": q, "state": str(sdir), "replay": os.environ.get("SUPERJEV_REPLAY"),
                        "files": sorted(p.name for p in sdir.iterdir())}) + "\n")
p = PLAN.get(q, {})
time.sleep(p.get("sleep", 0))
(sdir / "claim-verdicts.json").write_text("{}")  # a stray write must stay in the copy
trace = {"kind": "trace", "lookup_id": "id-" + q[:4], "tier": p.get("tier", "confirmed"),
         "final_ranked": [{"score": s, "path": f} for s, f in p.get("final", [])],
         "content_check": p.get("checked", {}), "errors": p.get("errors", [])}
with open(sdir / "traces.jsonl", "a") as f:
    f.write(json.dumps(trace) + "\n")
if claim and "verdict" in p:
    print(p["verdict"])
sys.exit(p.get("rc", 0))
'''

TOP = [(0.99, "/a.md"), (0.95, "/b.md"), (0.9, "/c.md"), (0.85, "/d.md"), (0.5, "/e.md")]


def _build(tmp_path, name, plan):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    (d / "ask.py").write_text(FAKE % (plan,))
    return str(d / "ask.py")


def _cases(tmp_path, rows):
    f = tmp_path / "cases.jsonl"
    f.write_text("".join(json.dumps({"principal": "me", "split": "tuned", **r}) + "\n" for r in rows))
    return str(f)


@pytest.fixture
def env(tmp_path, monkeypatch):
    real = tmp_path / "real-state"
    (real / "me").mkdir(parents=True)
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
    # Gold is 5th, 0.5; the file that replaced it scored 0.55: a wobble, not a loss.
    near = [(0.99, "/a.md"), (0.95, "/b.md"), (0.9, "/c.md"), (0.85, "/d.md"), (0.55, "/z.md")]
    cases = _cases(tmp_path, [{"question": "edge", "gold": ["/e.md"]}])
    assert _run(tmp_path, cases, {"edge": {"final": TOP}}, {"edge": {"final": near}}) == 0
    assert json.loads(capsys.readouterr().out)["rows"][0]["result"] == "inconclusive"
    held = _cases(tmp_path, [{"question": "edge", "gold": ["/e.md"], "split": "held-out"}])
    assert pr.main(["--principal", "me", "--cases", held, "--ask", str(tmp_path / "old/ask.py"),
                    "--ask", str(tmp_path / "new/ask.py"), "--max-asks", "2", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["rows"][0]["result"] == "inconclusive"


def test_gold_just_outside_the_top_5_on_the_losing_side_is_inconclusive(tmp_path, env, capsys):
    cases = _cases(tmp_path, [{"question": "q", "gold": ["/a.md"]}])
    other = [(0.99, "/v.md"), (0.95, "/w.md"), (0.9, "/x.md"), (0.85, "/y.md"), (0.8, "/z.md")]
    new = {"q": {"final": other, "checked": {"/a.md": {"score": 0.75}}}}
    assert _run(tmp_path, cases, {"q": {"final": TOP}}, new) == 0
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


def test_claim_cases_grade_the_verdict_with_the_wobble(tmp_path, env, capsys):
    cases = _cases(tmp_path, [{"question": "c1", "gold": ["/a.md"], "kind": "claim", "expected": "TRUE"},
                              {"question": "c2", "gold": ["/a.md"], "kind": "claim", "expected": "FALSE"}])
    old = {"c1": {"final": TOP, "verdict": "UNSURE (supported 0.80): read"},
           "c2": {"final": TOP, "verdict": "UNSURE: read"}}
    new = {"c1": {"final": TOP, "verdict": "TRUE (1.00)"}, "c2": {"final": TOP, "verdict": "FALSE (0.93)"}}
    assert _run(tmp_path, cases, old, new) == 0
    rep = json.loads(capsys.readouterr().out)
    got = {r["question"]: (r["result"], r["new"]["verdict"], r["old"]["verdict"]) for r in rep["rows"]}
    assert got == {"c1": ("gained", "TRUE", "UNSURE"), "c2": ("inconclusive", "FALSE", "UNSURE")}
    with pytest.raises(SystemExit):  # a claim case must say what it expects
        _run(tmp_path, _cases(tmp_path, [{"question": "c", "gold": ["/a.md"], "kind": "claim"}]), {}, {})


def test_runs_never_see_or_write_the_real_saved_verdicts_or_approvals(tmp_path, env, capsys):
    (env / "me" / "claim-verdicts.json").write_text('{"c1": {"verdict": "TRUE"}}')
    (env / "me" / "approvals.jsonl").write_text("{}\n")
    (env / "me" / "pointer-words.json").write_text("{}")
    cases = _cases(tmp_path, [{"question": "c1", "gold": ["/a.md"], "kind": "claim", "expected": "TRUE"}])
    _run(tmp_path, cases, {}, {})
    seen = _log(tmp_path)
    assert len(seen) == 2 and len({s["state"] for s in seen}) == 2
    for s in seen:
        assert s["replay"] == "1" and not s["state"].startswith(str(env))
        assert s["files"] == ["pointer-words.json"]
    assert (env / "me" / "claim-verdicts.json").read_text() == '{"c1": {"verdict": "TRUE"}}'


def test_refuses_builds_that_read_different_prepare_caches(tmp_path, env, capsys):
    cases = _cases(tmp_path, [{"question": "q", "gold": ["/a.md"]}])
    old, new = _build(tmp_path, "old", {}), _build(tmp_path, "new", {})
    (Path(new).parent / "prepare-cache").mkdir()
    (Path(new).parent / "prepare-cache" / "p.json").write_text("{}")
    with pytest.raises(SystemExit) as e:
        pr.main(["--principal", "me", "--cases", cases, "--ask", old, "--ask", new, "--max-asks", "2"])
    assert e.value.code == 2 and "prepare-caches" in capsys.readouterr().err
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
