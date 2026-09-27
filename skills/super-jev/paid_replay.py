#!/usr/bin/env python3
"""Paid replay: compare two Super Jev builds on a small frozen case set through the FULL
ask path (routing, content check, listwise; --claim for claim cases), under a hard cap.

The scorecard replays the free word-search stage only; this one pays. Each case runs
once per build as a subprocess of that build's ask.py, old then new back to back, and
records whether a gold file is in the final top 5 (its rank and tier), the claim verdict
for "kind": "claim" cases (graded against "expected": TRUE or FALSE), and the lookup id.

    python3 paid_replay.py --principal primary --cases small.jsonl \\
        --ask OLD/ask.py --ask NEW/ask.py --max-asks 10

--max-asks is required: it refuses to start when cases x builds is larger, and says how
many it would need. Cases use the scorecard's format (question, gold, principal, split).

Freezing: every run gets a fresh temp SUPERJEV_STATE_DIR copied from the principal's
state, without traces, lookups, approvals or saved claim verdicts, and SUPERJEV_REPLAY=1,
so a build never reads or writes a saved answer or claim verdict (a build too old to
honor SUPERJEV_REPLAY that answers from a saved answer counts as inconclusive). Both
builds must read the same prepare-cache (copy one release twice, as the build playbook
says), or it refuses to start.

Wobble: live Jev scores move about 0.05-0.10 run to run. A change counts as gained or
lost only when neither side sits within --wobble (default 0.10) of the line that decides
it (the 5th top score, or the claim's sure line); otherwise, and on any error, timeout
or saved answer, the case is INCONCLUSIVE. Exit 1 on any real loss, or on a held-out
case the old build passed and the new one did not, even within the wobble.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scorecard as sc  # noqa: E402

CLAIM_SURE = 0.9  # ask.py CLAIM_SURE: a TRUE/FALSE verdict needs this probability
POSSIBLE_FLOOR = 0.6  # ask.py POSSIBLE_FLOOR: the cut when the top 5 is not full
# Left out of each run's state copy: logs, and every saved answer or verdict.
NOT_COPIED = ("traces.jsonl*", "lookups.jsonl*", "approvals.jsonl", "claim-verdicts.json",
              "pending_picks*")
VERDICT_RE = re.compile(r"^(TRUE|FALSE|CONFLICT|PARTIAL|UNSURE|NOT FOUND)\b(?: \((\d+(?:\.\d+)?)(, saved)?)?", re.M)


def state_root() -> Path:
    """The state root every Super Jev tool uses (ask.state_dir without the principal)."""
    root = os.environ.get("SUPERJEV_STATE_DIR")
    return Path(root).expanduser() if root else Path.home() / ".local/state/super-jev"


def snapshot(principal: str, dest: Path) -> None:
    src = state_root()
    if (src / principal).is_dir():
        shutil.copytree(src / principal, dest / principal, symlinks=True,
                        ignore=shutil.ignore_patterns(*NOT_COPIED))
    else:
        (dest / principal).mkdir(parents=True)
    if (src / "_memory" / "config.json").is_file():  # the memory config, never its answer store
        (dest / "_memory").mkdir()
        shutil.copy2(src / "_memory" / "config.json", dest / "_memory" / "config.json")


def cache_listing(ask: Path) -> list:
    cache = sc.build_path(ask).parent / "prepare-cache"
    return sorted((str(f.relative_to(cache)), f.stat().st_size) for f in cache.rglob("*") if f.is_file())


def run_one(ask: Path, case: dict, timeout: float, wobble: float) -> dict:
    """One full ask of one case on one build, in its own state copy."""
    pr, claim = case["principal"], case.get("kind") == "claim"
    with tempfile.TemporaryDirectory(prefix="sj-replay-") as tmp:
        snapshot(pr, Path(tmp))
        env = {**os.environ, "SUPERJEV_STATE_DIR": tmp, "SUPERJEV_REPLAY": "1",
               "SUPERJEV_AUTO_CACHE": "0", "SUPERJEV_TRACES": "1"}
        cmd = [sys.executable, str(ask), "--principal", pr] + (
            ["--claim", case["question"]] if claim else [case["question"]])
        try:
            r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"error": f"timeout after {timeout:g}s"}
        traces = [t for t in sc._jsonl(Path(tmp) / pr / "traces.jsonl") if t.get("kind") == "trace"]
    out = {"lookup_id": traces[-1].get("lookup_id") if traces else None}
    m = VERDICT_RE.search(r.stdout)
    if r.returncode not in (0, 1):
        out["error"] = f"exit {r.returncode}: {(r.stderr or r.stdout).strip()[-200:]}"
    elif (m and m.group(3)) or any(t.get("tier") in ("cache", "stale") for t in traces):
        out["error"] = "answered from a saved answer, not live (build lacks SUPERJEV_REPLAY)"
    elif not traces:
        out["error"] = "no trace written"
    elif traces[-1].get("errors"):
        out["error"] = "; ".join(map(str, traces[-1]["errors"]))[:200]
    if "error" in out:
        return out
    t = traces[-1]
    final = [f for f in t.get("final_ranked") or [] if isinstance(f, dict)][:5]
    scores = [f.get("score") or 0 for f in final]
    gold = {os.path.realpath(g) for g in case["gold"]}
    rank = next((i + 1 for i, f in enumerate(final) if os.path.realpath(str(f.get("path"))) in gold), None)
    out.update(tier=t.get("tier"), rank=rank, score=scores[rank - 1] if rank else None)
    cut = min(scores) if len(final) == 5 else None
    if claim:
        out["verdict"] = m.group(1) if m else None
        out["prob"] = float(m.group(2)) if m and m.group(2) else None
        out["ok"] = out["verdict"] == case["expected"]
        out["near"] = out["ok"] and (out["prob"] or 0) < CLAIM_SURE + wobble
    elif rank:
        out["ok"] = True
        out["near"] = cut is not None and out["score"] < cut + wobble
    else:
        out["ok"] = False
        checked = {os.path.realpath(p): (v or {}).get("score") for p, v in (t.get("content_check") or {}).items()
                   if isinstance(v, dict) or v is None}
        best = max((s for p, s in checked.items() if p in gold and isinstance(s, (int, float))), default=None)
        out["near"] = best is not None and best >= (POSSIBLE_FLOOR if cut is None else cut) - wobble
    return out


def compare(old: dict, new: dict) -> str:
    if "error" in old or "error" in new:
        return "inconclusive"
    if old["ok"] == new["ok"]:
        return "same"
    if old["near"] or new["near"]:
        return "inconclusive"
    return "gained" if new["ok"] else "lost"


def describe(res: dict, claim: bool) -> str:
    if "error" in res:
        return "ERROR"
    if claim:
        return f"{res['verdict'] or '?'}" + (f" {res['prob']:.2f}" if res.get("prob") is not None else "")
    return f"rank {res['rank']} {res['tier']}" if res["rank"] else f"not in top 5 ({res['tier']})"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--principal", action="append", type=sc.principal_name, required=True)
    ap.add_argument("--ask", action="append", required=True, help="ask.py of OLD, then of NEW")
    ap.add_argument("--cases", action="append", required=True, help="JSONL cases (scorecard format)")
    ap.add_argument("--max-asks", type=int, required=True, help="hard cap on paid ask.py runs")
    ap.add_argument("--wobble", type=float, default=0.10, help="run-to-run score wobble (default 0.10)")
    ap.add_argument("--timeout", type=float, default=300, help="seconds per ask (default 300)")
    ap.add_argument("--held-out", metavar="FILE", help="frozen held-out manifest (scorecard)")
    ap.add_argument("--held-out-sha256", help="its checksum")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if len(a.ask) != 2:
        ap.error("give exactly two builds: --ask OLD --ask NEW")
    if bool(a.held_out) != bool(a.held_out_sha256):
        ap.error("--held-out and --held-out-sha256 must be supplied together")
    try:
        rows = [c for f in a.cases for c in sc.input_cases(f)]
        if a.held_out:
            rows += sc.frozen_cases(a.held_out, a.held_out_sha256)
        cases = [c for c in sc.normalize_cases(rows, a.principal[0]) if c["principal"] in a.principal]
        for c in cases:
            if c.get("kind") == "claim" and c.get("expected") not in ("TRUE", "FALSE"):
                raise ValueError(f"claim case needs expected TRUE or FALSE: {c['question']}")
    except (OSError, ValueError, TypeError, AttributeError) as e:
        ap.error(str(e))
    builds = [Path(p).expanduser().resolve() for p in a.ask]
    for b in builds:
        if not b.is_file():
            ap.error(f"no such build: {b}")
    need = len(cases) * len(builds)
    if not cases:
        ap.error("no cases for the given --principal")
    if need > a.max_asks:
        ap.error(f"{len(cases)} case(s) x {len(builds)} builds needs {need} paid asks; --max-asks is {a.max_asks}")
    if cache_listing(builds[0]) != cache_listing(builds[1]):
        ap.error("the builds read different prepare-caches; copy one release twice so they differ in code only")

    report = {"asks": need, "wobble": a.wobble, "builds": [str(b) for b in builds], "rows": []}
    for c in cases:
        old, new = (run_one(b, c, a.timeout, a.wobble) for b in builds)
        report["rows"].append({"question": c["question"], "split": c["split"], "kind": c.get("kind") or "question",
                               "result": compare(old, new), "old": old, "new": new})
    rows = report["rows"]
    held_drop = [r for r in rows if r["split"] == "held-out" and r["old"].get("ok") and r["new"].get("ok") is False]
    report["splits"] = {s: {k: sum(r["result"] == k for r in rows if r["split"] == s)
                            for k in ("gained", "lost", "same", "inconclusive")} for s in sc.SPLITS}
    failed = any(r["result"] == "lost" for r in rows) or bool(held_drop)
    if a.json:
        print(json.dumps(report, indent=1))
    else:
        print(f"paid replay: {len(cases)} case(s), {need} asks; wobble {a.wobble:g}")
        for r in rows:
            claim = r["kind"] == "claim"
            why = "; ".join(f"{side}: {r[side]['error']}" for side in ("old", "new") if "error" in r[side])
            print(f"  {r['result'].upper():12} [{r['split']}] {describe(r['old'], claim)} -> "
                  f"{describe(r['new'], claim)}  {r['question']}" + (f"  ({why})" if why else "")
                  + f"  ids {r['old'].get('lookup_id')} {r['new'].get('lookup_id')}")
        for s, n in report["splits"].items():
            print(f"  {s}: {n['gained']} gained, {n['lost']} lost, {n['same']} same, {n['inconclusive']} inconclusive")
        if held_drop:
            print(f"  held-out: {len(held_drop)} case(s) the old build passed and the new one did not")
        print("(inconclusive = an error, a timeout, a saved answer, or a change inside the wobble; rerun or read it)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
