#!/usr/bin/env python3
"""Super Jev build cycle: one receipt per step, Super Jev used inside each step.

Usage: build_cycle.py --dir CYCLE_DIR [--principal NAME] [--super-jev DIR] STEP [options]

Required steps (receipts are CYCLE_DIR/NN-step.md; Super Jev uses go to CYCLE_DIR/uses.jsonl):
  preflight [--project-dir DIR ...] [--skill "what the new skill does"]   free readiness check
  start "idea" [--topic "subject"] [--project NAME] [--ask Q ...]   one ask (what we already know) plus your own
  check-report --report FILE [--claims FILE] [--worktree DIR] [--evidence FILE ...] [--test-cmd CMD]
  review-brief --worktree DIR [--test-cmd CMD]   brief for a fresh reviewer agent (no receipt)
  review --reviewer NAME --verdict "SHIP|FIX ..." --file FILE [--claims FILE] [--worktree DIR] [--evidence FILE ...]
  reply-check --claims FILE [--worktree DIR] [--evidence FILE ...]
  learn [--note FILE ...] [--fact Q A [--source PATH]]   teach back what this cycle learned
Optional notes: target, cause, brief, prove.
  skip STEP --reason TEXT              recorded and shown at close (start, review, reply-check cannot be skipped)
  mark USE_ID helped|neutral|missed [--note TEXT]
  onboard need|simplest|promise|frozen|build|prove|record|check|status   Earn the Button (see SKILL.md)
  status                               which steps are done, skipped or missing
  close [--log FILE]                   exit 1 naming missing steps / unmarked uses / a FIX review

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
# The six steps close requires. target, cause, brief and prove stay available as optional notes.
REQUIRED = ["preflight", "start", "check-report", "review", "reply-check", "learn"]
UNSKIPPABLE = ("start", "review", "reply-check")
STRONG = 0.85  # a hit at or above this, not marked "possible", counts as Super Jev knowing the topic
VERDICTS = ("helped", "neutral", "missed")
NAME_RE = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"  # same agent-name rule as ask.py
START_QUESTION = "what have we already tried, decided or learned about {topic}"
# The history question names the subject, not the whole idea: a long, detailed question makes a passage
# answer all of it, so notes on the subject come back weak or not at all. An idea longer than this needs
# --topic; the tool never shortens it itself (the words it would keep are not the subject's).
TOPIC_MAX_WORDS = 12
REVIEW_BRIEF = """INDEPENDENT REVIEW. You have not seen this work before; keep it that way: judge only what is below
and what you can run or read yourself.

Goal of the change: {idea}
Where it lives: {worktree}
Test command: {test_cmd}

Try to break it. Look for: a case the change gets wrong, a claim in the report that is not true, a test that
passes without proving the goal, anything unsafe (secrets, money, deletion, live systems). Run the tests yourself.
Write probes if needed; change no product files.

Reply with one line starting SHIP or FIX, then each finding: what, where (file:line), and how you proved it.

The change (diff against origin/main):
{diff}
"""
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


def worktree_diff(ctx, step: str, worktree: str) -> Path:
    """The worktree's uncommitted and committed changes against origin/main, saved in the cycle folder:
    new code is not on Super Jev's shelves yet, so claims about it are checked against this diff."""
    wt = Path(worktree).expanduser()
    diffs = []
    for args in (["diff", "origin/main...HEAD"], ["diff", "HEAD"]):
        r = subprocess.run(["git", "-C", str(wt)] + args, capture_output=True)
        if r.returncode:
            raise CycleError(f"git {' '.join(args)} failed in {wt}: "
                             f"{r.stderr.decode('utf-8', 'replace').strip()[:200]}")
        # bytes as git wrote them: text mode would turn a lone CR in a file into a line break
        diffs.append(r.stdout.decode("utf-8", "replace"))
    text = "".join(diffs)
    if not text.strip():
        raise CycleError(f"no changes in {wt} against origin/main; pass --evidence FILE instead")
    out = ctx.dir / f"{step}-diff.patch"
    out.write_text(text, encoding="utf-8", newline="")
    return out.absolute()


def claims(ctx, step, path: Path, label: str, evidence: list = None):
    """Check each line of a claims file. With evidence files, Super Jev judges them directly
    (dispatch.py check: no connecting, so new or unconnected files work; a diff is judged as code).
    Without, the claims are looked up on its connected shelves (ask.py --claims-file)."""
    if not path.is_file() or not path.read_text(encoding="utf-8").strip():
        raise CycleError(f"{label} file missing or empty: {path}")
    if evidence:
        lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        args = ["check"] + [str(e) for e in evidence] + [x for c in lines for x in ("--claim", c)]
        return run_sj(ctx, step, "dispatch.py", args, f"claims in {path} against {len(evidence)} evidence file(s)")
    return run_sj(ctx, step, "ask.py", ["--principal", ctx.principal, "--claims-file", str(path.absolute())],
                  f"claims in {path}")


def evidence_files(ctx, step, a) -> list:
    """--evidence files plus, with --worktree, the worktree's diff."""
    ev = [need_file(e, "--evidence") for e in (getattr(a, "evidence", None) or [])]
    if getattr(a, "worktree", None):
        ev.append(worktree_diff(ctx, step, a.worktree))
    return ev


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
    idea = " ".join(a.idea.split())  # one line: brief and review-brief read it back from the receipt
    topic = idea if a.topic is None else " ".join(a.topic.split())
    if not idea or not topic:
        raise CycleError("start needs an idea, and --topic cannot be empty")
    if len(topic.split()) > TOPIC_MAX_WORDS:
        which = "--topic" if a.topic is not None else f"the idea ({len(topic.split())} words)"
        raise CycleError(f"{which} is too long for the history question (at most {TOPIC_MAX_WORDS} words): a long "
                         "question finds fewer notes. Rerun with --topic \"the subject in a few words\" (the problem "
                         "or feature, as you would name it when searching your notes; details stay in the idea), "
                         "e.g. --topic \"secret scan holding files over numbers read as card numbers\"")
    outs, strong = [], 0
    for q in [START_QUESTION.format(topic=topic + (f" in {a.project}" if a.project else ""))] + (a.ask or []):
        uid, out = ask(ctx, "start", q)
        strong += strong_hits(out)
        outs.append((f"[{uid}] {q}", out))
    fields = {"idea": idea, "topic": topic if topic != idea else None, "project": a.project}
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
        uid, out = claims(ctx, "check-report", Path(a.claims), "--claims", evidence_files(ctx, "check-report", a))
        outs.append((f"[{uid}] claim verdicts", out))
    return {"report": rep}, outs


def cmd_prove(ctx, a):
    files = [need_file(o, "--output") for o in a.output]
    return {"command": a.cmd, "outputs": ", ".join(map(str, files)), "note": a.note}, []


def cmd_review(ctx, a):
    rev = need_file(a.file, "--file")
    if a.reviewer == ctx.principal:
        raise CycleError("the reviewer must be a different, fresh agent, not the builder (see review-brief)")
    if not re.match(r"\s*(SHIP|FIX)\b", a.verdict):
        raise CycleError("--verdict must start with SHIP or FIX")
    outs = []
    if a.claims:
        uid, out = claims(ctx, "review", Path(a.claims), "--claims", evidence_files(ctx, "review", a))
        outs.append((f"[{uid}] reviewer claim verdicts", out))
    return {"reviewer": a.reviewer, "verdict": a.verdict, "review file": rev}, outs


def cmd_review_brief(ctx, a):
    """Write a self-contained brief for a fresh reviewer agent: the goal, the diff, the tests, what to try."""
    start = receipt_path(ctx.dir, "start")
    idea = next((l[len("- idea: "):] for l in start.read_text(encoding="utf-8").splitlines()
                 if l.startswith("- idea: ")), "") if start.exists() else ""
    if not idea:
        raise CycleError("no start receipt yet: run the start step first")
    diff = worktree_diff(ctx, "review", a.worktree).read_text(encoding="utf-8")
    out = ctx.dir / "review-brief.md"
    out.write_text(REVIEW_BRIEF.format(idea=idea, worktree=Path(a.worktree).expanduser(),
                                       test_cmd=a.test_cmd or "(find and run the repo's tests)", diff=diff),
                   encoding="utf-8")
    print(f"review brief: {out}\nStart a NEW agent with no history of this work, give it only this file, "
          "save its answer to a file, then: review --reviewer NAME --verdict \"SHIP|FIX ...\" --file ANSWER")
    return 0


def cmd_reply_check(ctx, a):
    uid, out = claims(ctx, "reply-check", Path(a.claims), "--claims", evidence_files(ctx, "reply-check", a))
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
    missing = [s for s in REQUIRED if step_state(ctx.dir, s) == "missing"]
    rv = receipt_path(ctx.dir, "review")
    verdict = next((l[len("- verdict: "):] for l in rv.read_text(encoding="utf-8").splitlines()
                    if l.startswith("- verdict: ")), "") if rv.exists() else ""
    if rv.exists() and not verdict.lstrip().startswith("SHIP"):
        print("close refused: the latest review says " + (verdict or "nothing") + "; fix, get a new review, record it")
        return 1
    if any(onb_path(ctx.dir, s).is_file() for s in ONB):  # an onboarding was started: it must be earned
        probs = onb_check(ctx.dir)
        if probs:
            print("close refused: onboarding not earned:\n- " + "\n- ".join(probs))
            return 1
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
        if st == "missing":
            continue  # an optional step not used
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


# ---- Earn the Button: onboarding a new feature or file type. One artifact per step in CYCLE_DIR ----
ONB = ["need", "simplest", "promise", "frozen", "build", "prove", "record"]
CONTRACT = Path("/Users/admin/agents/primary-brain/superjev-source-contract.md")
NO_EVIDENCE = {"", "-", "--", "x", "xx", "none", "n/a", "na", "unknown", "tbd", "no", "nothing", "?", "todo", "..."}
APPROVED_RE = re.compile(r"kelvin approved.*(\b\d{4}-\d{2}-\d{2}\b|[\"'\u201c][^\"'\u201d]{2,}[\"'\u201d])", re.I)
PASS_RE = re.compile(r"^q\d+\b.*(\brank 1\b|not-found OK|absent-OK)")
RESULT_RE = re.compile(r"^q\d+\b")
CASE_KINDS = ("supported", "absent", "invalid")
SPLITS = ("dev", "heldout")


def onb_path(d: Path, step: str) -> Path:
    return d / f"onboard-{ONB.index(step) + 1}-{step}.md"


def onb_read(d: Path, step: str) -> dict:
    p = onb_path(d, step)
    if not p.is_file():
        raise CycleError(f"onboarding step '{step}' has no artifact yet ({p.name}); run: onboard {step}")
    return {m.group(1): m.group(2) for m in map(re.compile(r"^- ([^:]+): (.*)$").match,
                                                p.read_text(encoding="utf-8").splitlines()) if m}


def onb_write(d: Path, step: str, principal: str, fields: dict) -> Path:
    lines = [f"# onboard {step}", f"- time: {now()}", f"- principal: {principal}"]
    lines += [f"- {k}: {' '.join(str(v).split())}" for k, v in fields.items()]
    p = onb_path(d, step)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def sha256(p: Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()


def need_text(v, label, words=1):
    v = " ".join((v or "").split())
    if all(w.lower().strip(".:;,") in NO_EVIDENCE or len(w) < 2 for w in v.split()) or len(v.split()) < words:
        raise CycleError(f"{label} is required" + (f" (at least {words} words)" if words > 1 else "")
                         + ". No real-use evidence or answer: stop, do not onboard this")
    return v


def load_cases(path: Path) -> list:
    rows = []
    for i, l in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not l.strip():
            continue
        try:
            row = json.loads(l)
        except ValueError:
            raise CycleError(f"{path.name} line {i} is not JSON")
        if not isinstance(row, dict) or row.get("kind") not in CASE_KINDS:
            raise CycleError(f"{path.name} line {i} needs a \"kind\" of {', '.join(CASE_KINDS)}")
        rows.append(row)
    missing = [k for k in CASE_KINDS if not any(r["kind"] == k for r in rows)]
    if missing:
        raise CycleError(f"{path.name} has no case of kind: {', '.join(missing)} (needs supported, absent "
                         "'not found', invalid/failure)")
    return rows


def review_problem(d: Path, branch: str, worktree: str) -> str:
    """Why the review does not count for this build, or "" when it does."""
    rv = receipt_path(d, "review")
    if step_state(d, "review") != "done":
        return "no independent review receipt: run review first"
    text = rv.read_text(encoding="utf-8")
    if not re.search(r"^- verdict: SHIP\b", text, re.M):
        return "the latest review does not say SHIP"
    if not onb_path(d, "need").is_file() or rv.stat().st_mtime < onb_path(d, "need").stat().st_mtime:
        return "the SHIP review is older than the need step: review the build, not an earlier change"
    m = re.search(r"^- review file: (.+)$", text, re.M)
    ans = Path(m.group(1)) if m else None
    body = ans.read_text(encoding="utf-8", errors="replace") if ans and ans.is_file() else ""
    if not re.search(r"(?<![\w/.-])" + re.escape(branch) + r"(?![\w/.-])", body) and worktree not in body:
        return f"the reviewer's answer must name the branch {branch} or the worktree {worktree}"
    return ""


def count_pass(path: Path, expect: int, label: str) -> int:
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if RESULT_RE.match(l)]
    if len(lines) != expect:
        raise CycleError(f"{label} file must hold {expect} result lines (q.. rank 1 / not-found OK ...), has {len(lines)}")
    return sum(1 for l in lines if PASS_RE.match(l))


def onb_check(d: Path) -> list:
    """Every onboarding problem, re-checked against the real files. Empty list = earned."""
    probs = []
    for step in ONB:
        try:
            f = onb_read(d, step)
        except CycleError as e:
            probs.append(str(e))
            continue
        try:
            if step == "need":
                for k in ("who asked", "how often", "what fails today"):
                    need_text(f.get(k), k, 3)
            elif step == "simplest":
                need_text(f.get("answer"), "answer", 5)
            elif step == "promise":
                need_text(f.get("kelvin approved"), "Kelvin approved")
                c = Path(f.get("contract", ""))
                if not c.is_file() or f.get("contract entry", "\0") not in c.read_text(encoding="utf-8").splitlines():
                    raise CycleError(f"promise line is not in the contract file {c}")
            elif step == "frozen":
                for sp in SPLITS:
                    p = Path(f.get(sp + " file", ""))
                    if not p.is_file() or sha256(p) != f.get(sp + " sha256"):
                        raise CycleError(f"frozen {sp} cases file changed or missing since it was frozen")
            elif step == "build":
                need_text(f.get("deletes or replaces"), "deletes or replaces", 3)
                bad = review_problem(d, f.get("branch", "\0"), f.get("worktree", "\0"))
                if bad:
                    raise CycleError(bad)
            elif step == "prove":
                if f.get("cases10") != "PASS" or f.get("eval60") != "PASS" or f.get("own test") != "PASS":
                    raise CycleError("prove is not all PASS (cases10, eval60, own test)")
                for k in ("cases10 file", "eval60 file", "eval60 before file"):
                    if not Path(f.get(k, "")).is_file() or sha256(Path(f[k])) != f.get(k + " sha256"):
                        raise CycleError(f"{k} changed or missing since prove")
            elif step == "record":
                need_text(f.get("timeline row"), "timeline row")
                need_text(f.get("card now line"), "card NOW line")
                for txt, fl in ((f["timeline row"], "timeline file"), (f["card now line"], "card file")):
                    p = Path(f.get(fl, ""))
                    if not p.is_file() or txt not in p.read_text(encoding="utf-8"):
                        raise CycleError(f"the recorded text is no longer in the {fl} {p}")
        except CycleError as e:
            probs.append(f"{step}: {e}")
    return probs


def cmd_onboard(ctx, a):
    step, d = a.ostep, ctx.dir
    if step == "check":
        probs = onb_check(d)
        print("onboarding EARNED: all 7 steps have artifacts and still check out" if not probs
              else "onboarding NOT earned:\n- " + "\n- ".join(probs))
        return 1 if probs else 0
    if step == "status":
        for s in ONB:
            print(f"{ONB.index(s) + 1}. {s}: {'done' if onb_path(d, s).is_file() else 'missing'}")
        return 0
    if ONB.index(step) and not onb_path(d, ONB[ONB.index(step) - 1]).is_file():
        raise CycleError(f"do '{ONB[ONB.index(step) - 1]}' first: the steps go in order")
    later = [s for s in ONB[ONB.index(step) + 1:] if onb_path(d, s).is_file()]
    if later:
        raise CycleError(f"'{step}' cannot be redone: '{later[0]}' is already recorded. Start a new cycle dir")
    if step == "need":
        f = {"who asked": need_text(a.who, "--who", 3), "how often": need_text(a.how_often, "--how-often", 3),
             "what fails today": need_text(a.fails, "--fails", 3)}
    elif step == "simplest":
        f = {"answer": need_text(a.answer, "--answer", 5)}
    elif step == "promise":
        line = need_text(a.line, "--line")
        low = line.lower()
        if not all(w in low for w in ("accept", "return", "fail")):
            raise CycleError("--line must say what it accepts, what it returns, and how failure is reported")
        ok = need_text(a.approved, "--approved")
        if not APPROVED_RE.search(ok):
            raise CycleError('--approved must say "Kelvin approved" and give a date (YYYY-MM-DD) or a quoted phrase')
        m = re.search(r"\b\d{4}-\d{2}-\d{2}\b", ok)
        if m:
            try:
                dt.date.fromisoformat(m.group(0))
            except ValueError:
                raise CycleError(f"--approved has an impossible date: {m.group(0)}")
        c = Path(a.contract).expanduser() if a.contract else CONTRACT
        if not c.is_file():
            raise CycleError(f"contract file not found: {c}")
        entry = f"- {line} ({ok})"
        if entry not in c.read_text(encoding="utf-8").splitlines():
            with open(c, "a", encoding="utf-8") as fh:
                fh.write(f"\n{entry}\n")
        f = {"line": line, "kelvin approved": ok, "contract": c.absolute(), "contract entry": entry}
    elif step == "frozen":
        dev, held = need_file(a.dev, "--dev"), need_file(a.heldout, "--heldout")
        dr, hr = load_cases(dev), load_cases(held)
        if dev.resolve() == held.resolve() or {json.dumps(r, sort_keys=True) for r in dr} & \
                {json.dumps(r, sort_keys=True) for r in hr}:
            raise CycleError("dev and held-out cases must be separate files with no case in both")
        f = {"dev file": dev, "dev sha256": sha256(dev), "heldout file": held, "heldout sha256": sha256(held),
             "frozen before build": "yes: recorded before the build step can run"}
    elif step == "build":
        wt = Path(a.worktree).expanduser()
        r = subprocess.run(["git", "-C", str(wt), "symbolic-ref", "--short", "HEAD"], capture_output=True, text=True)
        br = r.stdout.strip()
        if r.returncode or br in ("main", "master", "HEAD") or br != a.branch:
            raise CycleError(f"{wt} must be a git worktree with branch {a.branch!r} checked out, not main (found {br!r})")
        f = {"worktree": wt.absolute(), "branch": br, "deletes or replaces": need_text(a.deletes, "--deletes", 3)}
        bad = review_problem(d, br, str(wt.absolute()))
        if bad:
            raise CycleError(bad)
    elif step == "prove":
        fz = onb_read(d, "frozen")
        for sp in SPLITS:
            if sha256(Path(fz[sp + " file"])) != fz[sp + " sha256"]:
                raise CycleError(f"frozen {sp} cases changed after freezing: the proof is void")
        c10, e_after, e_before = (need_file(x, l) for x, l in ((a.cases10_file, "--cases10-file"),
                                  (a.eval60_file, "--eval60-file"), (a.eval60_before_file, "--eval60-before-file")))
        n10 = count_pass(c10, 10, "cases10")
        if n10 != 10:
            raise CycleError(f"the 10 frozen questions must all pass ({n10}/10 lines pass in {c10.name})")
        before, after = count_pass(e_before, 60, "eval60 before"), count_pass(e_after, 60, "eval60")
        if after < before:
            raise CycleError(f"eval60 went down ({before} -> {after}): not proven")
        if fz["dev file"] not in a.test_cmd and fz["heldout file"] not in a.test_cmd:
            raise CycleError("--test-cmd must use the frozen dev or held-out cases file (its full path), "
                             "so the test is the frozen one")
        wt = onb_read(d, "build")["worktree"]
        try:
            r = subprocess.run(a.test_cmd, shell=True, cwd=wt, capture_output=True, text=True, timeout=ctx.timeout)
        except subprocess.TimeoutExpired:
            raise CycleError("the feature's own frozen test timed out")
        (d / "onboard-own-test.txt").write_text(r.stdout + r.stderr, encoding="utf-8")
        if r.returncode:
            raise CycleError(f"the feature's own frozen test failed (exit {r.returncode}); output: onboard-own-test.txt")
        f = {"cases10": "PASS", "cases10 file": c10, "cases10 file sha256": sha256(c10),
             "eval60": "PASS", "eval60 before": before, "eval60 after": after,
             "eval60 file": e_after, "eval60 file sha256": sha256(e_after),
             "eval60 before file": e_before, "eval60 before file sha256": sha256(e_before),
             "own test": "PASS", "own test cmd": a.test_cmd}
    else:  # record
        row, now_line = need_text(a.timeline_row, "--timeline-row"), need_text(a.card_now, "--card-now")
        for txt, fl, label in ((row, a.timeline_file, "timeline"), (now_line, a.card_file, "card")):
            p = Path(fl).expanduser()
            if not p.is_file() or txt not in p.read_text(encoding="utf-8"):
                raise CycleError(f"the {label} text is not in {fl}: write it there first")
        f = {"timeline row": row, "card now line": now_line, "timeline file": Path(a.timeline_file).absolute(),
             "card file": Path(a.card_file).absolute()}
    p = onb_write(d, step, ctx.principal, f)
    print(f"receipt: {p}")
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
    s.add_argument("--topic", help=f"the subject in at most {TOPIC_MAX_WORDS} words (needed when the idea is longer)")
    s.add_argument("--ask", action="append", help="a sharper question of your own (repeatable)")
    s = sub.add_parser("target"); s.add_argument("--goal", required=True); s.add_argument("--evidence", required=True)
    s.add_argument("--ask", action="store_true", help="also ask for related past misses")
    s = sub.add_parser("cause"); s.add_argument("--cause", required=True)
    s.add_argument("--ask", action="append", help="a lookup about the cause (repeatable)")
    s.add_argument("--file", action="append", help="a file the cause lives in (repeatable)")
    s.add_argument("--trace", help="lookup id or 'last': show a Super Jev ask's trace")
    sub.add_parser("brief")
    s = sub.add_parser("check-report"); s.add_argument("--report", required=True); s.add_argument("--claims")
    s.add_argument("--worktree", help="the worktree; its diff also becomes evidence for --claims"); s.add_argument("--test-cmd"); s.add_argument("--evidence", action="append", help="a file to check the claims against directly (repeatable)")
    s = sub.add_parser("prove"); s.add_argument("--cmd", required=True)
    s.add_argument("--output", action="append", required=True); s.add_argument("--note")
    s = sub.add_parser("review"); s.add_argument("--reviewer", required=True); s.add_argument("--verdict", required=True)
    s.add_argument("--file", required=True); s.add_argument("--claims"); s.add_argument("--worktree"); s.add_argument("--evidence", action="append", help="a file to check the claims against directly (repeatable)")
    s = sub.add_parser("review-brief"); s.add_argument("--worktree", required=True); s.add_argument("--test-cmd")
    s = sub.add_parser("reply-check"); s.add_argument("--claims", required=True); s.add_argument("--worktree"); s.add_argument("--evidence", action="append", help="a file to check the claims against directly (repeatable)")
    s = sub.add_parser("learn"); s.add_argument("--note", action="append")
    s.add_argument("--fact", nargs=2, action="append", metavar=("QUESTION", "ANSWER"))
    s.add_argument("--source", help="file the facts come from")
    s = sub.add_parser("skip"); s.add_argument("skip_step", metavar="STEP"); s.add_argument("--reason", required=True)
    s = sub.add_parser("mark"); s.add_argument("use_id"); s.add_argument("verdict", choices=VERDICTS)
    s.add_argument("--note")
    sub.add_parser("status")
    o = sub.add_parser("onboard", help="Earn the Button: onboard a new feature or file type, 7 steps")
    os_ = o.add_subparsers(dest="ostep", required=True)
    for n in ("check", "status"):
        os_.add_parser(n)
    x = os_.add_parser("need"); x.add_argument("--who"); x.add_argument("--how-often"); x.add_argument("--fails")
    x = os_.add_parser("simplest"); x.add_argument("--answer")
    x = os_.add_parser("promise"); x.add_argument("--line"); x.add_argument("--approved")
    x.add_argument("--contract", help=f"default {CONTRACT}")
    x = os_.add_parser("frozen"); x.add_argument("--dev", required=True); x.add_argument("--heldout", required=True)
    x = os_.add_parser("build"); x.add_argument("--worktree", required=True); x.add_argument("--branch", required=True)
    x.add_argument("--deletes", help="what it deletes or replaces, or 'none: why'")
    x = os_.add_parser("prove"); x.add_argument("--cases10-file", required=True, help="result file of the 10 frozen questions")
    x.add_argument("--eval60-file", required=True, help="result file of the 60-question run after the build")
    x.add_argument("--eval60-before-file", required=True, help="result file of the 60-question run before the build")
    x.add_argument("--test-cmd", required=True, help="the feature's own frozen test; must use the frozen cases file path")
    x = os_.add_parser("record"); x.add_argument("--timeline-row"); x.add_argument("--timeline-file", required=True)
    x.add_argument("--card-now"); x.add_argument("--card-file", required=True)
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
        if a.step == "onboard":
            return cmd_onboard(ctx, a)
        if a.step == "skip":
            return cmd_skip(ctx, argparse.Namespace(step=a.skip_step, reason=a.reason))
        if a.step in ("mark", "status", "close", "review-brief"):
            return {"mark": cmd_mark, "status": cmd_status, "close": cmd_close,
                    "review-brief": cmd_review_brief}[a.step](ctx, a)
        fields, outs = RECEIPT_STEPS[a.step](ctx, a)
        p = write_receipt(ctx.dir, a.step, ctx.principal, fields, outs)
        print(f"receipt: {p}")
        return 0
    except CycleError as e:
        print(f"build_cycle: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
