#!/usr/bin/env python3
"""Scorecard: grade a Super Jev build on your own past questions, free and offline.

Every question an agent answered from a file (--approve, a confirmed pick) or reported
found elsewhere (--miss with the right file) becomes a test case: the question and the
file that held the answer. The scorecard replays each case through word search (step 4,
local, no Jev call) and reports where the right file ranks, and whether it made the read
slots (the top FALLBACK_FILES). Given two builds (--ask OLD --ask NEW) it compares them
case by case and exits 1 if the new one drops any case the old one read.

It measures the free stage only: routing and the content check call Jev, so a case
the scorecard passes can still miss live. Use it to reject a change that makes search
worse before spending paid asks on it.

    python3 scorecard.py --principal primary                       # grade the installed build
    python3 scorecard.py --principal primary --ask OLD/ask.py --ask NEW/ask.py
    python3 scorecard.py --principal primary --cases extra.jsonl   # add hand-written cases

Cases found in the state folder are kept in <state>/scorecard-cases.jsonl, so a miss
stays a test after its trace rotates away. A --cases line is
{"question": "...", "gold": ["/abs/path"], "principal": "...", "split": "held-out"}.
"""
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def principal_name(name: str) -> str:
    """The agent-name rule every Super Jev tool applies (prepare_bulk.principal_name)."""
    sys.path.insert(0, str(HERE))
    try:
        import prepare_bulk
        return prepare_bulk.principal_name(name)
    finally:
        sys.path.remove(str(HERE))
        sys.modules.pop("prepare_bulk", None)

CASES_FILE = "scorecard-cases.jsonl"


def load_ask(path: Path, name: str):
    """A build's ask module, loaded in-process (no subprocess, no Jev)."""
    # Each build imports its own sibling modules (prepare_bulk, auto_heal...) and reads its
    # own prepare-cache: drop any sibling another build already imported.
    siblings = {f.stem for f in path.parent.glob("*.py")}
    saved = {k: sys.modules.pop(k) for k in list(sys.modules) if k in siblings}
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    argv, sys.argv, syspath = sys.argv, [str(path)], list(sys.path)
    sys.path[:0] = [str(path.parent), str(path.parent / "lib")]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv, sys.path[:] = argv, syspath
        for k in [k for k in sys.modules if k in siblings]:
            sys.modules.pop(k)
        sys.modules.update(saved)
    mod.STAGE_LIST_CAP = 10 ** 6  # keep the whole ranking, not the trace's top 10
    return mod


def _jsonl(path: Path):
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def harvest(sdir: Path, principal: str) -> list:
    """Cases from this principal's own records: approvals that still stand, and outcome
    lines (--approve right, --miss wrong) that name an existing absolute file."""
    found = {}
    for rec in _jsonl(sdir / "approvals.jsonl"):
        q = str(rec.get("question") or "").strip()
        if not q:
            continue
        if rec.get("removed_by"):
            found.pop(q, None)
        elif rec.get("approved_by") and rec.get("file"):
            found[q] = str(rec["file"])
    traces = [sdir / "traces.jsonl.1", sdir / "traces.jsonl"]
    for rec in (r for t in traces for r in _jsonl(t)):
        if rec.get("kind") == "outcome" and rec.get("result") in ("right", "wrong"):
            q = str(rec.get("question") or "").strip()
            if q and rec.get("file"):
                found[q] = str(rec["file"])
    return [{"question": q, "gold": [f], "principal": principal, "split": "harvested"}
            for q, f in found.items() if os.path.isabs(f) and os.path.isfile(f)]


def merge_saved(sdir: Path, new: list) -> list:
    """Append new cases to the principal's saved set; a question already saved keeps its
    first gold file. Returns the full saved set."""
    path = sdir / CASES_FILE
    saved = _jsonl(path)
    have = {c.get("question") for c in saved}
    add = [c for c in new if c["question"] not in have]
    if add:
        sdir.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            for c in add:
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    return saved + add


def gold_rank(ask, case: dict, pointers: list):
    """1-based rank of the first gold file in the full word-search ranking, or None."""
    ask._STAGE.clear()
    ask.word_search(case["question"], pointers, limit=10 ** 6)
    ranked = [p for _, p, _ in (ask._STAGE.get("word") or {}).get("ranked", [])]
    gold = {os.path.realpath(g) for g in case.get("gold") or []}
    return next((i + 1 for i, p in enumerate(ranked) if os.path.realpath(p) in gold), None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--principal", action="append", type=principal_name, required=True)
    ap.add_argument("--ask", action="append", default=[],
                    help="ask.py of a build to grade (repeat: first is the baseline); default: this folder's")
    ap.add_argument("--cases", action="append", default=[], help="extra JSONL cases")
    ap.add_argument("--no-harvest", action="store_true", help="use only --cases")
    ap.add_argument("--cache", help="prepare-cache every build reads (default: the first build's), "
                    "so builds differ in code only, never in data")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    builds = [Path(p).expanduser().resolve() for p in a.ask] or [HERE / "ask.py"]
    mods = [load_ask(p, f"scorecard_ask_{i}") for i, p in enumerate(builds)]
    base = mods[0]
    cache = Path(a.cache).expanduser() if a.cache else base.prepare_bulk.CACHE_DIR
    for m in mods:
        m.prepare_bulk.CACHE_DIR = cache
    cases = []
    for pr in a.principal:
        if not a.no_harvest:
            cases += merge_saved(base.state_dir(pr), harvest(base.state_dir(pr), pr))
    for f in a.cases:
        cases += [c for c in _jsonl(Path(f).expanduser()) if c.get("question") and c.get("gold")
                  and c.get("principal", a.principal[0]) in a.principal]
    # One case per question: every file recorded as holding its answer counts as right.
    merged = {}
    for c in cases:
        key = (c.get("principal") or a.principal[0], c["question"])
        m = merged.setdefault(key, {**c, "principal": key[0], "gold": []})
        m["gold"] += [g for g in c["gold"] if g not in m["gold"]]
    cases = list(merged.values())
    pointers = {pr: base.my_pointers(pr) for pr in a.principal}

    slots = base.FALLBACK_FILES
    ranks = [[gold_rank(m, c, pointers[c["principal"]]) for c in cases] for m in mods]
    read = lambda r: r is not None and r <= slots  # noqa: E731
    report = {"cases": len(cases), "slots": slots, "builds": [str(b) for b in builds],
              "read": [sum(read(r) for r in rs) for rs in ranks], "rows": []}
    for i, c in enumerate(cases):
        report["rows"].append({"question": c["question"], "split": c.get("split"),
                               "gold": c["gold"][0], "ranks": [rs[i] for rs in ranks]})
    lost = gained = []
    if len(mods) > 1:
        lost = [r for r in report["rows"] if read(r["ranks"][0]) and not read(r["ranks"][-1])]
        gained = [r for r in report["rows"] if not read(r["ranks"][0]) and read(r["ranks"][-1])]
        report["lost"], report["gained"] = len(lost), len(gained)
    if a.json:
        print(json.dumps(report, indent=1))
    else:
        print(f"scorecard: {len(cases)} case(s); right file in the top {slots} (read by word search):")
        for b, n in zip(builds, report["read"]):
            print(f"  {n}/{len(cases)}  {b}")
        if len(mods) > 1:
            print(f"new vs baseline: {len(gained)} gained, {len(lost)} lost")
            for r in lost:
                print(f"  LOST  rank {r['ranks'][0]} -> {r['ranks'][-1]}  {r['question']}")
            for r in gained:
                print(f"  GAINED rank {r['ranks'][0]} -> {r['ranks'][-1]}  {r['question']}")
        print("(free word-search stage only; routing and content checks are not replayed)")
    return 1 if lost else 0


if __name__ == "__main__":
    raise SystemExit(main())
