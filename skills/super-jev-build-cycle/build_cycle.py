#!/usr/bin/env python3
"""Super Jev build cycle: one receipt per step, Super Jev used inside each step.

Usage: build_cycle.py --dir CYCLE_DIR [--principal NAME] [--super-jev DIR] STEP [options]

Steps (receipts are CYCLE_DIR/NN-step.md; Super Jev uses go to CYCLE_DIR/uses.jsonl):
  preflight [--project-dir DIR ...] [--skill "what the new skill does"]
                                       free readiness check (reachable, every connection ready, project
                                       folders connected); --skill also searches for an existing skill
  start "idea" [--project NAME]        4 asks: tried before / design rules / known traps / which files;
                                       flags NEW GROUND when Super Jev knows little
  target --goal TEXT --evidence TEXT [--ask]   the concrete failure or goal and where it came from
  cause --cause TEXT [--ask Q ...] [--trace ID|last]   root cause + what Super Jev returned for it
  brief                                print (and save) a block to paste into a helper's task
  check-report --report FILE [--claims FILE] [--worktree DIR] [--test-cmd CMD]
  prove --cmd CMD --output FILE [--output FILE ...] [--note TEXT]
  review --reviewer NAME --verdict TEXT --file FILE [--claims FILE]
  reply-check --claims FILE            one key fact of the draft reply per line
  learn [--note FILE ...] [--fact Q A [--source PATH]]   teach back what this cycle learned
  skip STEP --reason TEXT              recorded and shown at close, never silent
                                       (start, check-report and reply-check cannot be skipped)
  mark USE_ID helped|neutral|missed [--note TEXT]
  status                               which steps are done, skipped or missing
  close [--log FILE]                   exit 1 naming missing steps / unmarked uses; else summary + log lines

The principal is --principal or SUPERJEV_PRINCIPAL, never guessed. Super Jev is found at
--super-jev DIR, else the sibling folder ../super-jev of this skill.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

STEPS = ["preflight", "start", "target", "cause", "brief", "check-report", "prove", "review", "reply-check",
         "learn"]
UNSKIPPABLE = ("start", "check-report", "reply-check")
STRONG = 0.85  # a hit at or above this, not marked "possible", counts as Super Jev knowing the topic
VERDICTS = ("helped", "neutral", "missed")
NAME_RE = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"  # same agent-name rule as ask.py
START_QUESTIONS = [
    "have we already tried {idea}, and how did it go",
    "which design rules and steps apply to {idea}",
    "what errors, traps or quirks are known for {idea}",
    "which code files and tests handle {idea}",
]
HIT_RE = re.compile(r"^\s*[0-9]+\.[0-9]{2}\s+\S")  # a ranked hit line: "0.91  /path  [pointer]"
STATUS_RE = re.compile(r"^\s+([A-Za-z0-9][A-Za-z0-9._-]*): (.*)$")  # "  pointer: ready" from ask.py --status


class CycleError(Exception):
    pass


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def find_super_jev(explicit: str | None) -> Path:
    if explicit:
        cands = [Path(explicit)]
    else:  # sibling of this skill folder, as invoked first (a skills-folder link), then its real location
        cands = [Path(__file__).absolute().parent.parent / "super-jev",
                 Path(__file__).resolve().parent.parent / "super-jev"]
    for c in cands:
        if (c / "ask.py").is_file():
            return c
    raise CycleError("Super Jev not found (looked for ask.py in: " + ", ".join(str(c) for c in cands)
                     + "); install it next to this skill or pass --super-jev DIR")


def receipt_path(d: Path, step: str) -> Path:
    return d / f"{STEPS.index(step) + 1:02d}-{step}.md"


def skip_path(d: Path, step: str) -> Path:
    return d / f"{STEPS.index(step) + 1:02d}-{step}.skip.md"


def write_receipt(d: Path, step: str, principal: str, fields: dict, outputs: list) -> Path:
    lines = [f"# {step}", f"- time: {now()}", f"- principal: {principal}"]
    lines += [f"- {k}: {v}" for k, v in fields.items() if v not in (None, "", [])]
    for title, text in outputs:
        lines += ["", f"## {title}", "```", text.rstrip(), "```"]
    p = receipt_path(d, step)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sp = skip_path(d, step)
    if sp.exists():
        sp.unlink()  # a real receipt replaces an earlier skip
    return p


def next_use_id(d: Path) -> str:
    f = d / "uses.jsonl"
    n = sum(1 for line in f.read_text(encoding="utf-8").splitlines() if line.strip()) if f.exists() else 0
    return f"u{n + 1}"


def run_sj(ctx, step: str, script: str, args: list, what: str) -> tuple[str, str]:
    """Run one Super Jev command, log it as a use, return (use id, printed output)."""
    cmd = [sys.executable, str(ctx.sj / script)] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=ctx.timeout)
        out, code = (r.stdout + r.stderr), r.returncode
    except subprocess.TimeoutExpired:
        out, code = f"(timed out after {ctx.timeout}s)", "timeout"
    uid = next_use_id(ctx.dir)
    rec = {"id": uid, "step": step, "time": now(), "tool": script, "what": what, "exit": code}
    with open(ctx.dir / "uses.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return uid, f"$ {script} {' '.join(args)}\n(exit {code})\n{out}"


def ask(ctx, step, question):
    return run_sj(ctx, step, "ask.py", ["--principal", ctx.principal, "--", question], question)


def claims(ctx, step, path: Path, label: str):
    if not path.is_file() or not path.read_text(encoding="utf-8").strip():
        raise CycleError(f"{label} file missing or empty: {path}")
    return run_sj(ctx, step, "ask.py", ["--principal", ctx.principal, "--claims-file", str(path.absolute())],
                  f"claims in {path}")


def need_file(p: str, label: str) -> Path:
    path = Path(p).expanduser()
    if not path.is_file() or path.stat().st_size == 0:
        raise CycleError(f"{label} must be an existing, non-empty file: {p}")
    return path.absolute()


def connected_files(ctx, pointer: str) -> list:
    """Files a pointer actually connected, per its prepare report (a split part "<p>-2" uses its parent's).
    Roots alone are not enough: a connection can name single files inside a big folder."""
    names = [pointer] + ([re.sub(r"-[0-9]+$", "", pointer)] if re.search(r"-[0-9]+$", pointer) else [])
    for n in names:
        rep = ctx.sj / "prepare-cache" / f"{n}-report.json"
        if rep.is_file():
            try:
                files = json.loads(rep.read_text(encoding="utf-8")).get("approved") or []
            except (OSError, ValueError):
                return []
            return [Path(f).expanduser().resolve() for f in files if isinstance(f, str)]
    return []


def covers(root: Path, target: Path) -> bool:
    return target == root or root in target.parents


def status_rows(ctx) -> tuple[int, str, list]:
    """ask.py --status for this principal: (exit code, printed text, [(pointer, state), ...]). Free, no search."""
    try:
        r = subprocess.run([sys.executable, str(ctx.sj / "ask.py"), "--principal", ctx.principal, "--status"],
                           capture_output=True, text=True, timeout=ctx.timeout)
    except subprocess.TimeoutExpired:
        return 1, f"(status timed out after {ctx.timeout}s)", []
    out = r.stdout + r.stderr
    return r.returncode, out, [m.groups() for m in map(STATUS_RE.match, out.splitlines()) if m]


def core_preflight(ctx, a):
    """Ask Super Jev's own preflight (ask.py --preflight --json). None when this Super Jev predates it."""
    args = ["--principal", ctx.principal, "--preflight", "--json"]
    for d in a.project_dir or []:
        args += ["--project-dir", d]
    if a.skill:
        args += ["--skill", a.skill]
    try:
        r = subprocess.run([sys.executable, str(ctx.sj / "ask.py")] + args, capture_output=True, text=True,
                           timeout=ctx.timeout)
        rep = json.loads(r.stdout)
    except (subprocess.TimeoutExpired, ValueError):
        return None
    if not isinstance(rep, dict) or "verdict" not in rep:
        return None
    if a.skill:  # the skill search is a Jev call: log it as a use to mark
        uid = next_use_id(ctx.dir)
        with open(ctx.dir / "uses.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": uid, "step": "preflight", "time": now(), "tool": "ask.py --preflight",
                                "what": f"existing skill for: {a.skill}", "exit": r.returncode},
                               ensure_ascii=False) + "\n")
    return rep


def cmd_preflight(ctx, a):
    """Readiness check. No receipt unless every check passes; fix what it names, or skip with a reason."""
    rep = core_preflight(ctx, a)
    if rep is not None:
        if rep["problems"]:
            raise CycleError("preflight NOT READY (no receipt written):\n- " + "\n- ".join(rep["problems"])
                             + "\nFix these and rerun, or: skip preflight --reason \"why\"")
        lines = [f"{rep['verdict']} (Super Jev's own preflight)"] + [f"WARNING {w}" for w in rep["warnings"]]
        lines += [f"folder {c['folder']}: {c['connected']} of {c['connectable']} connectable files connected"
                  for c in rep.get("folders", [])]
        if "existing_skills" in rep:
            lines.append("existing skills that may already do this: " + (", ".join(rep["existing_skills"]) or "none")
                         + " (reuse or extend a match instead of building a duplicate)")
        print("\n".join(["preflight " + lines[0]] + lines[1:]))
        return {"checks": "; ".join(lines), "project dirs": ", ".join(a.project_dir or [])}, [
            ("Super Jev preflight (json)", json.dumps(rep, indent=2, ensure_ascii=False))]
    # Older Super Jev without --preflight: the skill's own checks.
    problems, lines = [], [f"Super Jev found: {ctx.sj}", f"principal: {ctx.principal}"]
    code, out, rows = status_rows(ctx)
    if code != 0 or not rows:
        problems.append("connection status unavailable or nothing connected:\n" + out.strip())
    ready = [n for n, st in rows if st == "ready"]
    for n, st in rows:
        if st.startswith("stale"):  # searchable at its last snapshot; asks refresh it in the background
            lines.append(f"WARNING {n} is stale (its last snapshot is still searched; an ask starts a refresh): {st}")
        elif st != "ready":
            problems.append(f"{n} is not ready: {st}")
    lines.append(f"connections: {len(ready)} of {len(rows)} ready")
    for d in a.project_dir or []:
        target = Path(d).expanduser().resolve()
        if not target.is_dir():
            problems.append(f"--project-dir is not a folder: {d}")
            continue
        inside = {n: sum(1 for f in connected_files(ctx, n) if covers(target, f)) for n in ready}
        inside = {n: c for n, c in inside.items() if c}
        if inside:
            lines.append(f"project folder connected: {target} ({sum(inside.values())} files via "
                         + ", ".join(sorted(inside)) + ")")
        else:
            problems.append(f"project folder not on Super Jev's shelves: {target}. Connect it (see "
                            f"{ctx.sj.parent / 'super-jev-connect' / 'SKILL.md'}), then rerun preflight")
    outs = [("connection status", out)]
    if a.skill:
        req = ctx.dir / "preflight-skill-request.json"
        req.write_text(json.dumps({"request": f"a skill that {a.skill}", "context": []}), encoding="utf-8")
        uid, sout = run_sj(ctx, "preflight", "dispatch.py", ["skills", "--request-file", str(req)],
                           f"existing skill for: {a.skill}")
        outs.append((f"[{uid}] existing skills for: {a.skill}", sout))
        lines.append(f"existing-skill search: see [{uid}]; reuse or extend a match instead of building a duplicate")
    if problems:
        raise CycleError("preflight NOT READY (no receipt written):\n- " + "\n- ".join(problems)
                         + "\nFix these and rerun, or: skip preflight --reason \"why\"")
    print("\n".join(["preflight READY"] + lines))
    return {"checks": "; ".join(lines), "project dirs": ", ".join(a.project_dir or [])}, outs


def strong_hits(out: str) -> int:
    n = 0
    for line in out.splitlines():
        if HIT_RE.match(line) and "(possible" not in line:
            try:
                n += float(line.split()[0]) >= STRONG
            except ValueError:
                pass
    return n


def cmd_start(ctx, a):
    if step_state(ctx.dir, "preflight") == "missing":
        raise CycleError("run preflight first (or: skip preflight --reason \"why\")")
    idea = a.idea + (f" in {a.project}" if a.project else "")
    outs, strong = [], 0
    for q in START_QUESTIONS:
        q = q.format(idea=idea)
        uid, out = ask(ctx, "start", q)
        strong += strong_hits(out)
        outs.append((f"[{uid}] {q}", out))
    fields = {"idea": a.idea, "project": a.project}
    if not strong:
        fields["new ground"] = ("YES: Super Jev has no strong note on this. Research outside (official docs, "
                                "maintained open-source projects) before building, and teach what you learn back "
                                "at the learn step")
        print("NEW GROUND: " + fields["new ground"])
    return fields, outs


def cmd_target(ctx, a):
    outs = []
    if a.ask:
        uid, out = ask(ctx, "target", f"past misses or failures related to {a.goal}")
        outs.append((f"[{uid}] related past misses", out))
    return {"goal": a.goal, "evidence": a.evidence}, outs


def cmd_cause(ctx, a):
    outs = []
    for q in a.ask or []:
        uid, out = ask(ctx, "cause", q)
        outs.append((f"[{uid}] {q}", out))
    if a.trace:
        uid, out = run_sj(ctx, "cause", "ask.py", ["--principal", ctx.principal, "--trace-show", a.trace],
                          f"trace {a.trace}")
        outs.append((f"[{uid}] trace {a.trace}", out))
    return {"cause": a.cause, "files": ", ".join(a.file or [])}, outs


def cmd_brief(ctx, a):
    start = receipt_path(ctx.dir, "start")
    if not start.exists():
        raise CycleError("no start receipt yet: run the start step first")
    text = start.read_text(encoding="utf-8")
    idea = next((l[len("- idea: "):] for l in text.splitlines() if l.startswith("- idea: ")), "")
    block = [f"BRIEFING (Super Jev, principal {ctx.principal}) for: {idea}"]
    for section in text.split("\n## ")[1:]:
        title, _, body = section.partition("\n")
        hits = [l.strip() for l in body.splitlines() if HIT_RE.match(l)][:5]
        block.append(f"- {title.split('] ', 1)[-1]}")
        block += [f"    {h}" for h in hits] or ["    (nothing found: may still exist, check by hand)"]
    for step in ("target", "cause"):
        p = receipt_path(ctx.dir, step)
        if p.exists():
            block += [l for l in p.read_text(encoding="utf-8").splitlines()
                      if l.startswith(("- goal:", "- evidence:", "- cause:", "- files:"))]
    block.append("Open the listed files before you build; a listed file is a lead, not an answer.")
    print("\n".join(block))
    return {}, [("paste into the helper's task", "\n".join(block))]


def cmd_check_report(ctx, a):
    rep = need_file(a.report, "--report")
    extra = []
    if a.worktree:
        extra += ["--worktree", a.worktree]
    if a.test_cmd:
        extra += ["--test-cmd", a.test_cmd]
    outs = []
    uid, out = run_sj(ctx, "check-report", "dispatch.py", ["verify", str(rep)] + extra, f"verify {rep}")
    outs.append((f"[{uid}] verify report", out))
    if a.claims:
        uid, out = claims(ctx, "check-report", Path(a.claims), "--claims")
        outs.append((f"[{uid}] claim verdicts", out))
    return {"report": rep}, outs


def cmd_prove(ctx, a):
    files = [need_file(o, "--output") for o in a.output]
    return {"command": a.cmd, "outputs": ", ".join(map(str, files)), "note": a.note}, []


def cmd_review(ctx, a):
    rev = need_file(a.file, "--file")
    outs = []
    if a.claims:
        uid, out = claims(ctx, "review", Path(a.claims), "--claims")
        outs.append((f"[{uid}] reviewer claim verdicts", out))
    return {"reviewer": a.reviewer, "verdict": a.verdict, "review file": rev}, outs


def cmd_reply_check(ctx, a):
    uid, out = claims(ctx, "reply-check", Path(a.claims), "--claims")
    return {"claims": Path(a.claims).absolute()}, [(f"[{uid}] draft reply claim verdicts", out)]


def cmd_learn(ctx, a):
    """Teach back: notes written into a connected folder and/or facts saved with ask.py --add."""
    if not a.note and not a.fact:
        raise CycleError("learn needs --note FILE (a note in a connected folder) and/or --fact QUESTION ANSWER")
    fields, outs = {}, []
    if a.note:
        notes = [need_file(n, "--note") for n in a.note]
        ready = [n for n, st in status_rows(ctx)[2] if st == "ready"]
        files = [f for n in ready for f in connected_files(ctx, n)]
        dirs = {f.parent for f in files}
        off = [str(n) for n in notes if n.resolve() not in files and n.resolve().parent not in dirs]
        if off:
            raise CycleError("not on Super Jev's shelves, so it cannot learn from them: " + ", ".join(off)
                             + ". Save the note next to connected notes, or connect its folder first")
        fields["note check"] = ("each note is connected or sits beside connected notes; a connection that lists "
                                "named files only needs the note added to it")
        fields["notes"] = ", ".join(map(str, notes))
    for q, ans in a.fact or []:
        args = ["--principal", ctx.principal, "--add", q, ans] + (["--source", a.source] if a.source else [])
        uid, out = run_sj(ctx, "learn", "ask.py", args, f"add fact: {q}")
        outs.append((f"[{uid}] add fact: {q}", out))
    return fields, outs


def load_uses(d: Path) -> list:
    f = d / "uses.jsonl"
    if not f.exists():
        return []
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_marks(d: Path) -> dict:
    f = d / "marks.jsonl"
    marks = {}
    if f.exists():
        for l in f.read_text(encoding="utf-8").splitlines():
            if l.strip():
                m = json.loads(l)
                marks[m["id"]] = m  # the latest mark wins
    return marks


def step_state(d: Path, step: str) -> str:
    r, s = receipt_path(d, step), skip_path(d, step)
    if r.exists() and r.stat().st_size > 0:
        return "done"
    if s.exists() and s.stat().st_size > 0:
        return "skipped"
    return "missing"


def cmd_status(ctx, a):
    for step in STEPS:
        print(f"{STEPS.index(step) + 1}. {step}: {step_state(ctx.dir, step)}")
    marks = load_marks(ctx.dir)
    for u in load_uses(ctx.dir):
        print(f"  {u['id']} [{u['step']}] {marks.get(u['id'], {}).get('verdict', 'UNMARKED')}: {u['what']}")
    return 0


def cmd_skip(ctx, a):
    if a.step not in STEPS:
        raise CycleError(f"unknown step {a.step!r}; steps: {', '.join(STEPS)}")
    if a.step in UNSKIPPABLE:
        raise CycleError(f"{a.step} cannot be skipped (unskippable: {', '.join(UNSKIPPABLE)})")
    if not a.reason.strip():
        raise CycleError("--reason must say why the step is skipped")
    skip_path(ctx.dir, a.step).write_text(
        f"# {a.step} SKIPPED\n- time: {now()}\n- principal: {ctx.principal}\n- reason: {a.reason}\n", encoding="utf-8")
    print(f"skipped {a.step} (shown at close): {a.reason}")
    return 0


def cmd_mark(ctx, a):
    if a.use_id not in {u["id"] for u in load_uses(ctx.dir)}:
        raise CycleError(f"no Super Jev use {a.use_id!r} in this cycle (see the status step)")
    with open(ctx.dir / "marks.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": a.use_id, "verdict": a.verdict, "note": a.note or "", "time": now()},
                           ensure_ascii=False) + "\n")
    print(f"{a.use_id}: {a.verdict}")
    return 0


def cmd_close(ctx, a):
    missing = [s for s in STEPS if step_state(ctx.dir, s) == "missing"]
    uses, marks = load_uses(ctx.dir), load_marks(ctx.dir)
    unmarked = [u["id"] for u in uses if u["id"] not in marks]
    if missing or unmarked:
        if missing:
            print("close refused: missing receipts for: " + ", ".join(missing)
                  + " (run the step, or skip it with --reason)")
        if unmarked:
            print("close refused: unmarked Super Jev uses: " + ", ".join(unmarked)
                  + " (mark each helped, neutral or missed)")
        return 1
    lines = ["# build cycle summary", f"- closed: {now()}", f"- principal: {ctx.principal}", ""]
    for s in STEPS:
        st = step_state(ctx.dir, s)
        if st == "skipped":
            reason = next((l[len("- reason: "):] for l in skip_path(ctx.dir, s).read_text(encoding="utf-8")
                           .splitlines() if l.startswith("- reason: ")), "")
            lines.append(f"- {s}: SKIPPED ({reason})")
        else:
            lines.append(f"- {s}: done ({receipt_path(ctx.dir, s).name})")
    counts = {v: sum(1 for m in marks.values() if m["verdict"] == v) for v in VERDICTS}
    lines += ["", "Super Jev uses: " + ", ".join(f"{counts[v]} {v}" for v in VERDICTS)]
    for u in uses:
        m = marks[u["id"]]
        lines.append(f"- {u['id']} [{u['step']}] {m['verdict']}: {u['what']}" + (f" ({m['note']})" if m["note"] else ""))
    (ctx.dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log = Path(a.log).expanduser() if a.log else ctx.dir / "super-jev-uses.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as f:
        for u in uses:
            m = marks[u["id"]]
            f.write("\t".join([now(), ctx.principal, str(ctx.dir.absolute()), u["id"], u["step"], m["verdict"],
                               u["what"].replace("\t", " ").replace("\n", " "),
                               m["note"].replace("\t", " ").replace("\n", " ")]) + "\n")
    print("\n".join(lines))
    print(f"\nsummary: {ctx.dir / 'summary.md'}\nuse log: {log}")
    return 0


RECEIPT_STEPS = {"preflight": cmd_preflight, "learn": cmd_learn, "start": cmd_start, "target": cmd_target, "cause": cmd_cause, "brief": cmd_brief,
                 "check-report": cmd_check_report, "prove": cmd_prove, "review": cmd_review,
                 "reply-check": cmd_reply_check}
NEEDS_SJ = {"preflight", "start", "target", "cause", "check-report", "review", "reply-check", "learn"}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="build_cycle.py", description=__doc__.split("\n")[0])
    p.add_argument("--dir", required=True, help="per-cycle folder for receipts (created if absent)")
    p.add_argument("--principal", default=os.environ.get("SUPERJEV_PRINCIPAL", ""),
                   help="agent name for Super Jev (or SUPERJEV_PRINCIPAL); required")
    p.add_argument("--super-jev", help="Super Jev skill folder (default: sibling ../super-jev)")
    p.add_argument("--timeout", type=int, default=600, help="seconds per Super Jev call")
    sub = p.add_subparsers(dest="step", required=True)
    s = sub.add_parser("preflight"); s.add_argument("--project-dir", action="append")
    s.add_argument("--skill", help="building a skill: what it does (searches for an existing one first)")
    s = sub.add_parser("start"); s.add_argument("idea"); s.add_argument("--project")
    s = sub.add_parser("target"); s.add_argument("--goal", required=True); s.add_argument("--evidence", required=True)
    s.add_argument("--ask", action="store_true", help="also ask for related past misses")
    s = sub.add_parser("cause"); s.add_argument("--cause", required=True)
    s.add_argument("--ask", action="append", help="a lookup about the cause (repeatable)")
    s.add_argument("--file", action="append", help="a file the cause lives in (repeatable)")
    s.add_argument("--trace", help="lookup id or 'last': show a Super Jev ask's trace")
    sub.add_parser("brief")
    s = sub.add_parser("check-report"); s.add_argument("--report", required=True); s.add_argument("--claims")
    s.add_argument("--worktree"); s.add_argument("--test-cmd")
    s = sub.add_parser("prove"); s.add_argument("--cmd", required=True)
    s.add_argument("--output", action="append", required=True); s.add_argument("--note")
    s = sub.add_parser("review"); s.add_argument("--reviewer", required=True); s.add_argument("--verdict", required=True)
    s.add_argument("--file", required=True); s.add_argument("--claims")
    s = sub.add_parser("reply-check"); s.add_argument("--claims", required=True)
    s = sub.add_parser("learn"); s.add_argument("--note", action="append")
    s.add_argument("--fact", nargs=2, action="append", metavar=("QUESTION", "ANSWER"))
    s.add_argument("--source", help="file the facts come from")
    s = sub.add_parser("skip"); s.add_argument("skip_step", metavar="STEP"); s.add_argument("--reason", required=True)
    s = sub.add_parser("mark"); s.add_argument("use_id"); s.add_argument("verdict", choices=VERDICTS)
    s.add_argument("--note")
    sub.add_parser("status")
    s = sub.add_parser("close"); s.add_argument("--log", help="use log to append to (default CYCLE_DIR/super-jev-uses.log)")
    return p


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    if not a.principal:
        print("build_cycle: --principal (or SUPERJEV_PRINCIPAL) is required; it is never guessed", file=sys.stderr)
        return 2
    if not re.fullmatch(NAME_RE, a.principal):
        print(f"build_cycle: invalid principal {a.principal!r} (letters, digits, . _ -; no spaces)", file=sys.stderr)
        return 2
    ctx = argparse.Namespace(dir=Path(a.dir).expanduser(), principal=a.principal, timeout=a.timeout, sj=None)
    try:
        ctx.dir.mkdir(parents=True, exist_ok=True)
        if a.step in NEEDS_SJ:
            ctx.sj = find_super_jev(a.super_jev)
        if a.step == "skip":
            return cmd_skip(ctx, argparse.Namespace(step=a.skip_step, reason=a.reason))
        if a.step in ("mark", "status", "close"):
            return {"mark": cmd_mark, "status": cmd_status, "close": cmd_close}[a.step](ctx, a)
        fields, outs = RECEIPT_STEPS[a.step](ctx, a)
        p = write_receipt(ctx.dir, a.step, ctx.principal, fields, outs)
        print(f"receipt: {p}")
        return 0
    except CycleError as e:
        print(f"build_cycle: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
