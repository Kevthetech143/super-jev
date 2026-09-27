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
many it would need. Cases use the scorecard's format (question, gold, principal, split);
a held-out row must match its frozen reservation exactly.

Freezing: one snapshot is taken before the first ask: the principals' state (links
followed, without traces, lookups, approvals or saved claim verdicts) and the memory
backend (its answer database and dataset registry, copied). Every run gets a fresh copy
of it as SUPERJEV_STATE_DIR, with SUPERJEV_REPLAY=1, which makes ask.py skip saved
answers, saved claim verdicts, stale-pointer reconnects and auto-heal. A build whose ask
code does not declare REPLAY_PROTOCOL = 2 (all of those, and a literal question after
--) is refused: it could start paid refresh work outside the cap. Both builds must read
byte-identical prepare-caches (copy one release twice); the report carries the
fingerprints, and a cache that changes mid-replay voids the run (exit 3). Every connected
original and the gold are hashed before each pair; a case is inconclusive when a file
either run read, or the gold, changed from then until the end of the pair.

Wobble: live Jev scores move about 0.05-0.10 run to run. A flip counts as gained or lost
unless the passing side cleared its line by less than --wobble (default 0.10) AND the
failing side missed it by less than --wobble (the line: the best file left out of the
top 5, the 5th score, or the claim's 0.90 sure line). That, and any error, timeout or
drift, is INCONCLUSIVE. Exit 1 on any real loss, or on a held-out case the old build
passed and the new one did not, even within the wobble.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scorecard as sc  # noqa: E402

CLAIM_SURE = 0.9  # ask.py CLAIM_SURE: a TRUE/FALSE verdict needs this probability
POSSIBLE_FLOOR = 0.6  # ask.py POSSIBLE_FLOOR: the line when the top 5 is not full
# Left out of the state snapshot: logs, and every saved answer or verdict.
NOT_COPIED = ("traces.jsonl*", "lookups.jsonl*", "approvals.jsonl", "claim-verdicts.json",
              "pending_picks*")
VERDICT_RE = re.compile(r"^(TRUE|FALSE|CONFLICT|PARTIAL|UNSURE|NOT FOUND)\b"
                        r"(?: \((?:(supported|contradicted) )?(\d+(?:\.\d+)?)(, saved)?)?", re.M)
LEAN = {"supported": "TRUE", "contradicted": "FALSE"}
REPLAY_PROTOCOL = 2  # the ask.py replay guarantees this replay needs (see ask.py REPLAY_PROTOCOL)


def state_root() -> Path:
    """The state root every Super Jev tool uses (ask.state_dir without the principal)."""
    root = os.environ.get("SUPERJEV_STATE_DIR")
    return Path(root).expanduser() if root else Path.home() / ".local/state/super-jev"


def file_sha(path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return "missing"


def tree_sha(root: Path) -> str:
    """Contents and names of every file under root, links followed."""
    h = hashlib.sha256()
    if root.is_dir():
        for f in sorted(p for p in root.rglob("*") if p.is_file()):
            h.update(f"{f.relative_to(root)}\0{file_sha(f)}\n".encode())
    return h.hexdigest()


def memory_config(ask: Path, override) -> Path:
    """The memory config this build's lookups use: its memory.sh's --config, else setup's."""
    if override:
        return Path(override).expanduser().resolve()
    wrapper = sc.build_path(ask).parent / "memory.sh"
    m = re.search(r"--config\s+(\S+)", wrapper.read_text()) if wrapper.is_file() else None
    return Path(m.group(1)).expanduser().resolve() if m else state_root() / "_memory" / "config.json"


def check_build(ask: Path) -> str:
    """Why this build cannot be replayed safely, or ''."""
    code = sc.build_path(ask)
    m = re.search(r"^REPLAY_PROTOCOL = (\d+)[ \t]*(?:#.*)?$", code.read_text(errors="replace"), re.M)
    if not m or int(m.group(1)) != REPLAY_PROTOCOL:
        return (f"{code} does not declare REPLAY_PROTOCOL = {REPLAY_PROTOCOL}: it could read saved answers, "
                "start paid auto-heal work or read a question as a flag")
    if not (code.parent.resolve().parents[1] / "experiments/verified-pointer-memory/cli.py").is_file():
        return f"{code} has no memory runtime two folders up (experiments/verified-pointer-memory)"
    return ""


def snapshot(principals, config: Path, dest: Path) -> dict:
    """Freeze the principals' state and a copy of the memory backend under dest."""
    for pr in principals:
        src = state_root() / pr
        if src.is_dir():
            shutil.copytree(src, dest / "state" / pr, symlinks=False, ignore_dangling_symlinks=True,
                            ignore=shutil.ignore_patterns(*NOT_COPIED))
        else:
            (dest / "state" / pr).mkdir(parents=True)
    cfg = json.loads(config.read_text())
    base = config.parent
    db, registry = (Path(cfg[k]).expanduser() for k in ("db", "registry"))
    db, registry = (p if p.is_absolute() else base / p for p in (db, registry))
    (dest / "memory").mkdir()
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as src, sqlite3.connect(dest / "memory" / "answers.sqlite") as out:
        src.backup(out)
    reg = json.loads(registry.read_text())
    for entry in (reg.get("datasets") or {}).values():  # a relative manifest path stays pointed at the original
        if isinstance(entry, dict) and entry.get("manifestPath") and not os.path.isabs(entry["manifestPath"]):
            entry["manifestPath"] = str(registry.parent / entry["manifestPath"])
    (dest / "memory" / "registry.json").write_text(json.dumps(reg))
    (dest / "config.json").write_text(json.dumps(cfg))
    return {"state": tree_sha(dest / "state"), "answers_db": file_sha(dest / "memory" / "answers.sqlite"),
            "registry": file_sha(registry)}


def run_once(ask: Path, base: Path, case: dict, timeout: float):
    """One full ask in a fresh copy of the snapshot; its whole process group dies with it."""
    pr, claim = case["principal"], case.get("kind") == "claim"
    with tempfile.TemporaryDirectory(prefix="sj-replay-") as tmp:
        tmp = Path(tmp)
        shutil.copytree(base / "state", tmp / "state")
        shutil.copytree(base / "memory", tmp / "memory")
        cfg = json.loads((base / "config.json").read_text())
        cfg.update(db=str(tmp / "memory" / "answers.sqlite"), registry=str(tmp / "memory" / "registry.json"))
        (tmp / "state" / "_memory").mkdir(exist_ok=True)
        (tmp / "state" / "_memory" / "config.json").write_text(json.dumps(cfg))
        # SUPERJEV_MEMORY_WRAPPER_ACTIVE: skip memory.sh (it names the real config) and use the copy.
        env = {**os.environ, "SUPERJEV_STATE_DIR": str(tmp / "state"), "SUPERJEV_REPLAY": "1",
               "SUPERJEV_AUTO_CACHE": "0", "SUPERJEV_TRACES": "1", "SUPERJEV_MEMORY_WRAPPER_ACTIVE": "1"}
        cmd = [sys.executable, str(ask), "--principal", pr] + (
            ["--claim", case["question"]] if claim else ["--", case["question"]])
        p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             start_new_session=True)
        try:
            stdout, stderr = p.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if timed_out:
            p.communicate()
            return None, {"error": f"timeout after {timeout:g}s"}
        traces = [t for t in sc._jsonl(tmp / "state" / pr / "traces.jsonl") if t.get("kind") == "trace"]
        if any((d / "traces.jsonl").exists() for d in (tmp / "state").iterdir() if d.name != pr):
            return None, {"error": "a trace was written for another principal; not this case's lookup"}
    return (p.returncode, stdout, stderr, traces), None


def grade(ask: Path, base: Path, case: dict, timeout: float) -> dict:
    ran, failed = run_once(ask, base, case, timeout)
    if failed:
        return failed
    rc, stdout, stderr, traces = ran
    claim = case.get("kind") == "claim"
    out = {"lookup_id": traces[-1].get("lookup_id") if traces else None}
    m = VERDICT_RE.search(stdout)
    if rc not in (0, 1):
        out["error"] = f"exit {rc}: {(stderr or stdout).strip()[-200:]}"
    elif (m and m.group(4)) or any(t.get("tier") in ("cache", "stale") for t in traces):
        out["error"] = "answered from a saved answer, not live"
    elif not traces:
        out["error"] = "no trace written"
    elif not all(same_question(t.get("question"), case["question"]) for t in traces):
        out["error"] = "a trace for another question was written; not this case's lookup"
    elif traces[-1].get("errors"):
        out["error"] = "; ".join(map(str, traces[-1]["errors"]))[:200]
    if "error" in out:
        return out
    t = traces[-1]
    final = [f for f in t.get("final_ranked") or [] if isinstance(f, dict)][:5]
    paths = [os.path.realpath(str(f.get("path"))) for f in final]
    scores = [f.get("score") or 0 for f in final]
    checked = {os.path.realpath(p): v.get("score") or 0
               for p, v in (t.get("content_check") or {}).items() if isinstance(v, dict)}
    gold = {os.path.realpath(g) for g in case["gold"]}
    rank = next((i + 1 for i, p in enumerate(paths) if p in gold), None)
    out.update(tier=t.get("tier"), rank=rank, score=scores[rank - 1] if rank else None)
    out["read"] = {p: file_sha(p) for p in sorted(set(paths) | set(checked) | gold)}
    if claim:
        out["verdict"], out["prob"] = (m.group(1), float(m.group(3)) if m.group(3) else None) if m else (None, None)
        out["ok"] = out["verdict"] == case["expected"]
        leans = out["ok"] or (out["verdict"] == "UNSURE" and LEAN.get(m.group(2)) == case["expected"])
        out["margin"] = (out["prob"] or 0) - CLAIM_SURE if leans and out["prob"] is not None else -1.0
    elif rank:  # cleared the best file it kept out of the top 5
        out["ok"] = True
        out["margin"] = out["score"] - max((s for p, s in checked.items() if p not in paths), default=0)
    else:  # missed the 5th score, or the floor when the top 5 had room
        out["ok"] = False
        line = min(scores) if len(final) == 5 else POSSIBLE_FLOOR
        out["margin"] = max((s for p, s in checked.items() if p in gold), default=0) - line
    out["margin"] = round(out["margin"], 6)
    return out


def eligible_sources(base: Path) -> set:
    """Every connected original the frozen registry lists: what any build may read."""
    reg = json.loads((base / "memory" / "registry.json").read_text())
    return {os.path.realpath(o["path"]) for e in (reg.get("datasets") or {}).values() if isinstance(e, dict)
            for o in e.get("originals") or [] if isinstance(o, dict) and isinstance(o.get("path"), str)}


def drifted(old: dict, new: dict, before: dict) -> bool:
    """Did any file either run read (or the gold) change from before the pair to after it,
    or between a run reading it and the end of the pair?"""
    for p in set(old["read"]) | set(new["read"]):
        now = file_sha(p)
        if now != before.get(p, now) or now != old["read"].get(p, now) or now != new["read"].get(p, now):
            return True
    return False


def same_question(traced, asked: str) -> bool:
    """The trace's question is the asked one (a trace cuts long fields to a prefix)."""
    if isinstance(traced, str) and traced.endswith("...[truncated]"):
        return asked.startswith(traced[:-len("...[truncated]")])
    return traced == asked


def compare(old: dict, new: dict, wobble: float, before: dict) -> str:
    if "error" in old or "error" in new:
        return "inconclusive"
    if drifted(old, new, before):
        old["error"] = new["error"] = "a file it read changed during the pair"
        return "inconclusive"
    if old["ok"] == new["ok"]:
        return "same"
    passing, failing = (new, old) if new["ok"] else (old, new)
    if passing["margin"] < wobble and failing["margin"] > -wobble:
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
    ap.add_argument("--memory-config", help="memory config to snapshot (default: the builds' memory.sh one)")
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
        reserved = sc.frozen_cases(a.held_out, a.held_out_sha256) if a.held_out else []
        if reserved:  # an unfrozen row may repeat a frozen one, never change it (scorecard's rule)
            for c in sc.normalize_cases(rows, a.principal[0]):
                if c["split"] == "held-out" and c not in reserved:
                    raise ValueError("held-out input differs from the frozen reservation")
        cases = [c for c in sc.normalize_cases(rows + reserved, a.principal[0]) if c["principal"] in a.principal]
        for c in cases:
            if c.get("kind") == "claim" and c.get("expected") not in ("TRUE", "FALSE"):
                raise ValueError(f"claim case needs expected TRUE or FALSE: {c['question']}")
    except (OSError, ValueError, TypeError, AttributeError) as e:
        ap.error(str(e))
    builds = [Path(p).expanduser().resolve() for p in a.ask]
    for b in builds:
        if not b.is_file():
            ap.error(f"no such build: {b}")
    dashed = [c["question"] for c in cases if c["question"].lstrip().startswith("-")]
    if dashed:  # ask.py would read it as a flag (--followup, --add ...), not a question
        ap.error(f"a question may not start with '-': {dashed[0]!r}")
    need = len(cases) * len(builds)
    if not cases:
        ap.error("no cases for the given --principal")
    if need > a.max_asks:
        ap.error(f"{len(cases)} case(s) x {len(builds)} builds needs {need} paid asks; --max-asks is {a.max_asks}")
    for b in builds:
        if why := check_build(b):
            ap.error(why)
    caches = [sc.build_path(b).parent / "prepare-cache" for b in builds]
    fingerprints = {"prepare_cache": tree_sha(caches[0])}
    if tree_sha(caches[1]) != fingerprints["prepare_cache"]:
        ap.error("the builds read different prepare-caches; copy one release twice so they differ in code only")
    configs = {memory_config(b, a.memory_config) for b in builds}
    if len(configs) != 1 or not next(iter(configs)).is_file():
        ap.error(f"the builds need one existing memory config (found {sorted(map(str, configs))}); pass --memory-config")

    report = {"asks": need, "wobble": a.wobble, "builds": [str(b) for b in builds], "rows": []}
    with tempfile.TemporaryDirectory(prefix="sj-replay-base-") as base:
        base = Path(base)
        try:
            fingerprints.update(snapshot({c["principal"] for c in cases}, configs.pop(), base))
        except (OSError, ValueError, KeyError, sqlite3.Error) as e:
            ap.error(f"cannot snapshot state and memory: {e}")
        report["fingerprints"] = fingerprints
        sources = eligible_sources(base)
        for c in cases:
            before = {p: file_sha(p) for p in sources | {os.path.realpath(g) for g in c["gold"]}}
            old, new = (grade(b, base, c, a.timeout) for b in builds)
            report["rows"].append({"question": c["question"], "split": c["split"], "kind": c.get("kind") or "question",
                                   "result": compare(old, new, a.wobble, before), "old": old, "new": new})
    drift = [str(p) for p in caches if tree_sha(p) != fingerprints["prepare_cache"]]
    rows = report["rows"]
    for r in rows:
        for side in ("old", "new"):
            r[side].pop("read", None)
        if drift:
            r["result"] = "inconclusive"
    report["drift"] = drift
    held_drop = [r for r in rows if r["split"] == "held-out" and r["old"].get("ok") and r["new"].get("ok") is False
                 and "error" not in r["old"] and "error" not in r["new"]]
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
        print("  fingerprints: " + ", ".join(f"{k} {v[:12]}" for k, v in fingerprints.items()))
        if drift:
            print(f"  DRIFT: prepare-cache changed during the replay ({', '.join(drift)}); every case is void")
        print("(inconclusive = an error, a timeout, a saved answer, drift, or a change inside the wobble)")
    if drift:
        return 3
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
