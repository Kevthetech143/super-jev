#!/usr/bin/env python3
"""Scorecard: grade a Super Jev build on your own past questions, free and offline.

Every question an agent answered from a file (--approve, a confirmed pick) or reported
found elsewhere (--miss with the right file) becomes a test case: the question and the
file that held the answer. The scorecard replays each case through word search (step 4,
local, no Jev call) and reports where the right file ranks, and whether it made the read
slots (the top FALLBACK_FILES). Given two builds (--ask OLD --ask NEW) it compares them
case by case and exits 1 if any compared build drops a case the baseline read.
Reports tuned, held-out and retrospective cases separately; untagged and legacy
harvested cases are retrospective. See references/scorecard-splits.md for frozen
held-out reservation and its limits.

It measures the free stage only: routing and the content check call Jev, so a case
the scorecard passes can still miss live. Use it to reject a change that makes search
worse before spending paid asks on it.

    python3 scorecard.py --principal primary                       # grade the installed build
    python3 scorecard.py --principal primary --ask OLD/ask.py --ask NEW/ask.py
    python3 scorecard.py --principal primary --cases extra.jsonl   # add hand-written cases

Cases found in the state folder are kept in <state>/scorecard-cases.jsonl (the one file
it writes, in each named principal's state folder), so a miss stays a test after its trace
rotates away; a later --miss replaces that question's saved file. It prints questions,
ranks and file paths, never file contents. A --cases line is
{"question": "...", "gold": ["/abs/path"], "principal": "...", "split": "held-out"}.
"""
import argparse
import hashlib
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


def build_path(path: Path) -> Path:
    """An install may keep the real ask.py as ask_impl.py behind a small launcher named
    ask.py; grade the real code, never the launcher."""
    impl = path.with_name("ask_impl.py")
    return impl if path.name == "ask.py" and impl.is_file() else path


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
    return [{"question": q, "gold": [f], "principal": principal, "split": "retrospective"}
            for q, f in found.items() if os.path.isabs(f) and os.path.isfile(f)]


def merge_saved(sdir: Path, new: list) -> list:
    """Merge harvested cases into the principal's saved set. The current harvest wins for
    every question it covers (a later --miss corrects an earlier approval's file); saved
    questions it no longer covers (their trace rotated away) are kept. Returns the set."""
    path = sdir / CASES_FILE
    saved = _jsonl(path)
    merged = {c.get("question"): c for c in saved if c.get("question")}
    merged.update({c["question"]: c for c in new})
    cases = list(merged.values())
    if cases != saved:
        sdir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases))
        os.replace(tmp, path)
    return cases


SPLITS = ("tuned", "held-out", "retrospective")


def is_absent(case) -> bool:
    """A not-in-files case: an absent question ("absent": true) or a claim expecting ABSENT (the flag is for
    questions only; a TRUE or FALSE claim always names its gold). Nothing in the files settles it, so its
    gold list is empty."""
    if case.get("kind") == "claim":
        return case.get("expected") == "ABSENT"
    return case.get("absent") is True


def normalize_cases(cases, default_principal, absent_ok=False):
    """Reject known leakage instead of silently merging split labels. A caller that grades
    not-in-files cases (paid replay) passes absent_ok: such a case then takes gold [] and
    every other case still needs its gold files; the free word search cannot grade one."""
    seen, merged = {}, {}
    for raw in cases:
        c = dict(raw)
        split = c.get("split") or "retrospective"
        if split == "harvested":
            split = "retrospective"
        if not isinstance(split, str):
            raise ValueError("case split must be a string")
        if split not in SPLITS:
            c["original_split"] = split
            split = "retrospective"
        c["split"] = split
        c["principal"] = c.get("principal") or default_principal
        absent = absent_ok and is_absent(c)  # a not-in-files case takes no gold; every other case needs some
        if (not isinstance(c.get("question"), str) or not c["question"].strip()
                or not isinstance(c.get("gold"), list) or bool(c["gold"]) == absent
                or any(not isinstance(g, str) or not os.path.isabs(g) for g in c["gold"])):
            raise ValueError("cases need a question and a nonempty list of absolute gold paths"
                             + ("; an absent question or ABSENT claim takes an empty gold list" if absent_ok else ""))
        question = " ".join(c["question"].casefold().split()).rstrip(".!?")
        identities = [("question", question)] + [("source", os.path.realpath(g)) for g in c["gold"]]
        for field in ("group", "source_family"):
            if field in c:
                if not isinstance(c[field], str) or not c[field].strip():
                    raise ValueError(f"{field} must be a nonempty string")
                identities.append((field, c[field].strip().casefold()))
        for identity in identities:
            if identity in seen and seen[identity] != split:
                raise ValueError(f"cross-split overlap in {identity[0]}; keep the entire family together")
            seen[identity] = split
        key = (c["principal"], question)
        if key in merged and any(merged[key].get(f) != c.get(f) for f in ("group", "source_family")):
            raise ValueError("duplicate question has conflicting family metadata")
        if absent_ok and key in merged and is_absent(merged[key]) != absent:
            raise ValueError("duplicate question is a not-in-files case in one row and not in another")
        m = merged.setdefault(key, {**c, "gold": []})
        m["gold"] += [g for g in c["gold"] if g not in m["gold"]]
    return list(merged.values())


def input_cases(path):
    """Explicit evaluation inputs must not silently lose malformed rows."""
    rows = []
    for line in Path(path).expanduser().read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError("case input must contain JSON objects")
        rows.append(row)
    return rows


def frozen_cases(path, checksum):
    data = Path(path).expanduser().read_bytes()
    if hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError("held-out checksum mismatch")
    payload = json.loads(data)
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("cases"), list):
        raise ValueError("invalid held-out manifest")
    rows = payload["cases"]
    if not rows or any(c.get("split") != "held-out" or not c.get("group") or not c.get("source_family") for c in rows):
        raise ValueError("frozen cases must be held-out with group and source_family")
    return rows


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
    ap.add_argument("--freeze-held-out", metavar="FILE", help="reserve held-out rows from --cases into a new manifest; print its checksum")
    ap.add_argument("--held-out", metavar="FILE", help="frozen held-out manifest")
    ap.add_argument("--held-out-sha256", help="checksum pinned before tuning")
    a = ap.parse_args(argv)
    if bool(a.held_out) != bool(a.held_out_sha256):
        ap.error("--held-out and --held-out-sha256 must be supplied together")
    try:
        reserved = frozen_cases(a.held_out, a.held_out_sha256) if a.held_out else []
        if any(c.get("principal") not in a.principal for c in reserved):
            raise ValueError("include every frozen principal with --principal")
        if a.freeze_held_out:
            if not a.cases or a.held_out:
                raise ValueError("--freeze-held-out needs --cases and cannot combine with --held-out")
            rows = normalize_cases([c for f in a.cases for c in input_cases(f)], a.principal[0])
            held = [c for c in rows if c["split"] == "held-out"]
            if not held or any(not c.get("group") or not c.get("source_family") for c in held):
                raise ValueError("reserve held-out cases with explicit group and source_family")
            if any(c["principal"] not in a.principal for c in held):
                raise ValueError("include every frozen principal with --principal")
            data = (json.dumps({"version": 1, "cases": held}, sort_keys=True, indent=2) + "\n").encode()
            with os.fdopen(os.open(Path(a.freeze_held_out).expanduser(),
                                   os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
                out.write(data)
            print("held-out sha256:", hashlib.sha256(data).hexdigest())
            return 0
    except (OSError, ValueError, TypeError, AttributeError) as e:
        ap.error(str(e))

    builds = [build_path(Path(p).expanduser().resolve()) for p in a.ask] or [build_path(HERE / "ask.py")]
    mods = [load_ask(p, f"scorecard_ask_{i}") for i, p in enumerate(builds)]
    for p, m in zip(builds, mods):
        if not hasattr(m, "word_search"):
            ap.error(f"{p} is not a Super Jev ask module (no word_search); pass the real ask.py with --ask")
    base = mods[0]
    cache = Path(a.cache).expanduser() if a.cache else base.prepare_bulk.CACHE_DIR
    for m in mods:
        m.prepare_bulk.CACHE_DIR = cache
    cases = []
    excluded_no_gold = 0
    for pr in a.principal:
        if not a.no_harvest:
            cases += merge_saved(base.state_dir(pr), harvest(base.state_dir(pr), pr))
    try:
        for f in a.cases:
            raw = input_cases(f)
            excluded_no_gold += sum(c.get("gold") == [] for c in raw)
            incoming = normalize_cases([c for c in raw if c.get("gold") != []], a.principal[0])
            cases += [c for c in incoming if c["principal"] in a.principal]
        if reserved:
            for c in normalize_cases(cases, a.principal[0]):
                if c["split"] == "held-out" and c not in reserved:
                    raise ValueError("held-out input differs from the frozen reservation")
        cases = normalize_cases(cases + reserved, a.principal[0])
    except (OSError, ValueError) as e:
        ap.error(str(e))
    pointers = {pr: base.my_pointers(pr) for pr in a.principal}

    slots = base.FALLBACK_FILES
    ranks = [[gold_rank(m, c, pointers[c["principal"]]) for c in cases] for m in mods]
    read = lambda r: r is not None and r <= slots  # noqa: E731
    report = {"cases": len(cases), "excluded_no_gold": excluded_no_gold, "slots": slots, "builds": [str(b) for b in builds],
              "read": [sum(read(r) for r in rs) for rs in ranks], "rows": []}
    for i, c in enumerate(cases):
        report["rows"].append({"question": c["question"], "split": c.get("split"),
                               "original_split": c.get("original_split"),
                               "gold": c["gold"][0], "ranks": [rs[i] for rs in ranks]})
    lost = gained = []
    if len(mods) > 1:
        lost = [r for r in report["rows"] if read(r["ranks"][0]) and any(not read(v) for v in r["ranks"][1:])]
        gained = [r for r in report["rows"] if not read(r["ranks"][0]) and read(r["ranks"][-1])]
        report["lost"], report["gained"] = len(lost), len(gained)
    report["held_out_verified"] = bool(a.held_out)
    report["held_out_sha256"] = a.held_out_sha256
    report["splits"] = {}
    for split in SPLITS:
        rows = [r for r in report["rows"] if r["split"] == split]
        report["splits"][split] = {
            "cases": len(rows),
            "read": [sum(read(r["ranks"][i]) for r in rows) for i in range(len(mods))],
            "lost": sum(r in lost for r in rows), "gained": sum(r in gained for r in rows)}
    if a.json:
        print(json.dumps(report, indent=1))
    else:
        print(f"scorecard: {len(cases)} case(s); right file in the top {slots} (read by word search):")
        if excluded_no_gold:
            print(f"  excluded: {excluded_no_gold} cases without gold files (not graded)")
        for b, n in zip(builds, report["read"]):
            print(f"  {n}/{len(cases)}  {b}")
        for split, result in report["splits"].items():
            print(f"  {split}: {result['cases']} cases; read {result['read']}; {result['lost']} lost")
        if any(r.get("original_split") for r in report["rows"]):
            print("Unrecognized legacy split labels counted as retrospective; never as held-out.")
        print("held-out checksum:", "verified" if a.held_out else "not supplied (labels only)")
        if len(mods) > 1:
            print(f"new vs baseline: {len(gained)} gained, {len(lost)} lost")
            for r in lost:
                print(f"  LOST  rank {' -> '.join(str(v) for v in r['ranks'])}  {r['question']}")
            for r in gained:
                print(f"  GAINED rank {' -> '.join(str(v) for v in r['ranks'])}  {r['question']}")
        print("(free word-search stage only; routing and content checks are not replayed)")
    return 1 if lost else 0


if __name__ == "__main__":
    raise SystemExit(main())
