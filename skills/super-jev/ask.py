#!/usr/bin/env python3
"""Front door for the super-jev harness: one cache-first lookup loop, any agent.

  ask.py --principal AGENT "question"
      Cache-first via the harness `cached` action (zero provider calls): a hit
      prints the answer + evidence and stops. A miss navigates every visible
      pointer in parallel, classifying each candidates/no-candidates/error. Any
      merged candidates from healthy pointers print first, always -- a pointer
      error never buries a real hit. A non-candidate pointer prints its own
      status line (e.g. "[pointer] refresh-required") after the candidates --
      errors never hide as "no-candidates". All-empty (no candidates, no
      errors) -> a no-candidates hint naming references/connectors.md and
      --add. Any error -> "unresolved: N of M pointers errored" last, exit 1
      (even when candidates printed above); exit 0 otherwise.

  ask.py --principal AGENT --approve "question" "answer"
      Re-searches the last lookup's top pointer and approves it, quotes taken
      verbatim from reviewedText. Next ask of the same question is a cache
      hit. Nothing ready -> hints --add.

  ask.py --principal AGENT --add "question" "answer" [--source /path]
                                 [--subject TEXT] [--kind KIND] [--status STATUS]
                                 [--as-of YYYY-MM-DD] [--replace-entry]
      Manual entry, no file needed: writes one record, connects it as its own
      one-file pointer "<principal>-manual-<10 hex question hash>", then
      approves it. Approval never depends on retrieval matching the tiny
      record: it searches once, and on anything short of "ready" falls back
      to the harness's assisted path (quoting the record's own reviewed
      text) so the answer is still cached. If agent assist is disabled on
      this deployment, --add prints the config to flip and exits 1 instead
      of silently leaving the answer uncached. Never replaces a pointer or
      touches another pointer's answers; many small manual pointers are
      fine, `cached` checks them all. Same wording twice refuses unless
      --replace-entry is given, which removes the existing manual pointer
      for that exact wording first, then adds fresh. --source records that
      file's path+sha256; a later cache hit on this pointer re-hashes it
      and, if changed, WITHHOLDS the answer (STALE, exit 1) instead of
      serving stale evidence.

      The record carries the same kind/status/as_of/subject labels
      prepare_bulk.py's bulk pipeline drafts and gates for onboarded files,
      so a manual entry gets the same chance at a hit: --kind (record,
      note, pointer, index, dashboard, playbook, ledger, research; default
      record), --status (active, closed, paper, done, unknown; default
      active), --as-of (default today), --subject (default: the first four
      meaningful words of the question). An invalid --kind/--status/--as-of
      value is a usage error (exit 2), never silently coerced. Labels ride
      the connect description in the same bracket format bulk prepare uses
      and `prepare_bulk.py --list --principal AGENT` reads them back.

  ask.py --principal AGENT --miss "question" "where it actually was"
      Log-only: the answer was found somewhere ask.py didn't reach.

AGENT can also come from SUPERJEV_PRINCIPAL. State lives under
$SUPERJEV_STATE_DIR or ~/.local/state/super-jev/<principal>/, never in this repo.
"""
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_bulk import KIND_VALUES, STATUS_VALUES, DATE_RE, validate_labels, label_bracket, has_secret  # noqa: E402

SKILL = Path(__file__).resolve().parent / "dispatch.py"
DEFAULT_KIND = "record"
DEFAULT_STATUS = "active"

# Words too generic to carry into a derived --subject; keeps the default to the
# question's actual content words.
SUBJECT_STOPWORDS = {
    "a", "an", "the", "is", "was", "are", "were", "did", "does", "do", "what", "where",
    "who", "how", "why", "when", "which", "of", "for", "to", "in", "on", "and", "or",
    "his", "her", "their", "my", "your", "its", "it", "this", "that", "with", "at",
    "by", "from", "as", "be", "been", "has", "have", "had",
}


def derive_subject(question: str) -> str:
    """Default --subject: the first four meaningful (non-stopword) words of the question."""
    words = re.findall(r"[A-Za-z0-9']+", question)
    meaningful = [w for w in words if w.lower() not in SUBJECT_STOPWORDS] or words
    return " ".join(meaningful[:4]) if meaningful else "unknown"

def state_dir(principal: str) -> Path:
    root = os.environ.get("SUPERJEV_STATE_DIR")
    base = Path(root).expanduser() if root else Path.home() / ".local/state/super-jev"
    return base / principal

def memory(req: dict) -> dict:
    r = subprocess.run([sys.executable, str(SKILL), "memory", "--input", "/dev/stdin"], input=json.dumps(req), capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"status": "error", "raw": (r.stdout + r.stderr)[-300:]}

def log(sdir: Path, kind: str, **fields) -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": kind, **fields}
    (sdir / "lookups.jsonl").open("a").write(json.dumps(entry) + "\n")

def my_pointers(principal: str) -> list:
    panel = memory({"action": "panel", "principal": principal})
    names = [(p.get("pointer") if isinstance(p, dict) else p) for p in panel.get("pointers", [])]
    return [n for n in names if n]

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def changed_source(record_path: Path):
    """Return the recorded source path if its current hash no longer matches, else None."""
    if not record_path.is_file():
        return None
    source_path = source_hash = None
    for line in record_path.read_text().splitlines():
        if line.startswith("source_path:"):
            source_path = line.split(":", 1)[1].strip()
        elif line.startswith("source_sha256:"):
            source_hash = line.split(":", 1)[1].strip()
    if source_path and source_hash and Path(source_path).is_file() and sha256_file(Path(source_path)) != source_hash:
        return source_path
    return None

def manual_pointer_name(principal: str, question: str) -> str:
    return f"{principal}-manual-{hashlib.sha1(question.encode()).hexdigest()[:10]}"

def manual_record_path(sdir: Path, principal: str, question: str) -> Path:
    return sdir / "manual" / f"{manual_pointer_name(principal, question)}.md"

def print_hit(hit: dict, sdir: Path, principal: str, question: str) -> int:
    # The harness's evidence "path" is always its own internal prepared-copy path,
    # never the caller's file -- so staleness is checked against the manual record
    # this exact principal+question would have written, not against evidence.path.
    record = manual_record_path(sdir, principal, question)
    if record.is_file():
        stale_source = changed_source(record)
        if stale_source:
            print(f"STALE: source changed since this answer was recorded ({stale_source}); answer withheld. Re-add with --add after verifying.")
            return 1
    print("CACHE HIT")
    print("answer:", hit.get("answer") or "")
    for e in (hit.get("evidence") or [])[:3]:
        print("  evidence:", e.get("sourceId", ""), "|", str(e.get("quote", ""))[:120])
    return 0

# navigation-cli refuses longer questions (src/enhance/navigation.ts MAX_QUESTION)
MAX_QUESTION = 8000
# Content check: routing picks files from their one-line descriptions only, so it
# can match an absent fact on topic alone and miss a present one. Each top routed
# file (routing score >= ROUTE_FLOOR) is re-offered ALONE with its own text, so it
# must beat "none of these" on its own merits, not merely out-rank weaker siblings;
# it survives only with a content score >= CONFIRM_FLOOR. One Jev call per file.
# A lone file only needs to out-score "none" to come back at all. With a plain file
# label, an on-topic file lacking the fact scored 0.64-0.69 (a present fact as low
# as 0.62), so absent facts passed on some runs. The label now tells the judge to
# pick a passage only if it states the answer: measured 2026-09-22, absent facts
# 0 (one near-answer 0.55-0.56), present facts 0.63-0.97. Near misses still passed
# (a flight's date for its departure time; another event's odometer figure), so the
# label now asks for the exact value for the exact event: measured 2026-09-22 on 10
# near-miss and 10 present questions x2, near misses 0-0.64, present 0.91-0.98.
CONFIRM_FILES, CONFIRM_CHUNK, CONFIRM_CHUNKS_PER_FILE = 5, 3500, 4
ROUTE_FLOOR, CONFIRM_FLOOR = 0.05, 0.70
CONFIRM_LABEL = ("Passage {n}, choose only if it states the exact value asked for, for the exact "
                 "event asked about (a value for another event, or only the topic, is none)")
HELD_SECRET = "contains a secret; not sent"
INCONCLUSIVE = "inconclusive"

def navigation_command() -> list:
    repo = Path(os.environ["SUPERJEV_REPO"]) if os.environ.get("SUPERJEV_REPO") else Path(__file__).resolve().parents[2]
    return ["node", str(repo / "src" / "navigation-cli.ts")]

def confirm_one(question: str, path: str):
    """(content score or None, whether the file was too long to read whole, error or None,
    note or None). note is HELD_SECRET (file not sent, not kept) or INCONCLUSIVE (the
    check did not finish; the file is kept on its routing score)."""
    try:
        text = Path(path).read_text(errors="replace")
    except OSError as e:
        return None, False, f"cannot read {path}: {e.strerror or e}", None
    # The file may have changed since connect scanned it; never ship a secret to Jev.
    if has_secret(text):
        return None, False, None, HELD_SECRET
    chunks = [text[i:i + CONFIRM_CHUNK] for i in range(0, len(text), CONFIRM_CHUNK)] or [""]
    partial = len(chunks) > CONFIRM_CHUNKS_PER_FILE
    leaves = [{"id": f"c{i}", "label": CONFIRM_LABEL.format(n=i + 1), "description": chunk,
               "sourceId": str(i)} for i, chunk in enumerate(chunks[:CONFIRM_CHUNKS_PER_FILE])]
    payload = {"question": question, "limits": {"beamWidth": 5, "maxResults": 10},
               "catalog": {"version": 1, "structure": "flat-files", "rootId": "root",
                           "nodes": [{"id": "root", "label": "Sources",
                                      "description": "Full text of candidate files",
                                      "children": [leaf["id"] for leaf in leaves]}, *leaves]}}
    try:
        r = subprocess.run(navigation_command(), input=json.dumps(payload), capture_output=True,
                           text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, partial, f"content check could not run: {e.__class__.__name__}", None
    if r.returncode:
        return None, partial, (r.stderr.strip().splitlines()[-1:] or ["content check failed"])[0][:200], None
    try:
        body = json.loads(r.stdout)
    except ValueError:
        return None, partial, "content check returned invalid JSON", None
    if not isinstance(body, dict) or body.get("status") not in ("candidates", "no-candidates"):
        # e.g. budget-exhausted: the read did not finish, so "not in file" would be a guess
        return None, partial, None, INCONCLUSIVE
    scores = [c.get("score") for c in body.get("candidates") or [] if isinstance(c, dict)]
    best = max((sc for sc in scores if isinstance(sc, (int, float))), default=0)
    return (best if best >= CONFIRM_FLOOR else None), partial, None, None

def confirm(question: str, paths: list):
    """Check each path alone, in parallel. Returns ({path: score} for kept files,
    set of paths too long to read whole, first error or None, {path: note})."""
    paths = paths[:CONFIRM_FILES]
    results = list(ThreadPoolExecutor(max_workers=CONFIRM_FILES).map(lambda p: confirm_one(question, p), paths))
    errors = [e for _, _, e, _ in results if e]
    scores = {p: sc for p, (sc, _, _, _) in zip(paths, results) if sc is not None}
    partial = {p for p, (_, part, _, _) in zip(paths, results) if part}
    # A file whose check errored was not read: it stays INCONCLUSIVE on its routing
    # score, and the other files are still filtered on their own results.
    notes = {p: note or INCONCLUSIVE for p, (_, _, e, note) in zip(paths, results) if note or e}
    return scores, partial, (errors[0] if errors else None), notes

def refresh_hint(ptr: str, principal: str, kind: str) -> str:
    """A stale pointer's files changed since connect; say the exact command that re-prepares it."""
    if not kind.startswith(("preparation-required", "refresh-required")):
        return ""
    if "-manual-" in ptr:
        return "; its source changed: re-add it with ask.py --add ... --replace-entry"
    try:
        roots = json.loads((Path(__file__).resolve().parent / "prepare-cache" / f"{ptr}-report.json")
                           .read_text()).get("roots") or []
    except (OSError, ValueError):
        roots = []
    root_args = " ".join(f"--root {shlex.quote(r)}" for r in roots) or "--root /path/to/folder"
    return (f"; its files changed since connect. Run: python3 skills/super-jev/prepare_bulk.py "
            f"--refresh --pointer {ptr} --principal {principal} {root_args}")

def lookup(question: str, principal: str, sdir: Path) -> int:
    if len(question) > MAX_QUESTION:
        print(f"question too long ({len(question):,} chars, max {MAX_QUESTION:,}); ask a shorter question")
        return 2
    t0 = time.time()
    cache = memory({"action": "cached", "principal": principal, "question": question})
    if cache.get("status") == "verified-cache-hit":
        rc = print_hit(cache, sdir, principal, question)
        log(sdir, "lookup", question=question, result="cache-hit" if rc == 0 else "stale-source", secs=round(time.time() - t0, 1))
        return rc
    panel = memory({"action": "panel", "principal": principal})
    if panel.get("reason") == "not-set-up":
        print("Super Jev is not set up yet. Run: python3 skills/super-jev/setup.py")
        return 1
    pointers = [n for n in ((p.get("pointer") if isinstance(p, dict) else p)
                            for p in panel.get("pointers", [])) if n]
    if not pointers:
        print(f"nothing connected yet for principal '{principal}' -- run connect first:\n"
              f"  python3 skills/super-jev/prepare_bulk.py --root /path/to/folder "
              f"--pointer my-notes --principal {principal}")
        log(sdir, "lookup", question=question, pointers=0, result="nothing-connected",
            secs=round(time.time() - t0, 1))
        return 1

    def nav(ptr):
        out = memory({"action": "navigate", "pointer": ptr, "principal": principal, "question": question})
        status = out.get("status")
        if status == "candidates" and out.get("candidates"):
            return ptr, "candidates", out["candidates"]
        if status in ("candidates", "no-candidates"):
            return ptr, "no-candidates", []
        kind = status or "error"
        return ptr, f"{kind}: {out['reason']}" if out.get("reason") else kind, []

    results = list(ThreadPoolExecutor(max_workers=8).map(nav, pointers)) if pointers else []
    merged, errored, statuses, error_lines = [], 0, {}, []
    for ptr, kind, rows in results:
        if kind == "candidates":
            statuses[ptr] = "candidates"
            merged += [(c.get("score", 0), c.get("originalPath", ""), ptr) for c in rows]
        elif kind == "no-candidates":
            statuses[ptr] = "no-candidates"
        else:
            errored += 1
            statuses[ptr] = kind
            error_lines.append(f"[{ptr}] {kind}" + refresh_hint(ptr, principal, kind))
    merged = sorted((m for m in merged if m[0] >= ROUTE_FLOOR), reverse=True)
    routed = list(dict.fromkeys(p for _, p, _ in merged))
    dropped, check_error, notes = 0, None, {}
    if merged:
        scores, partial, check_error, notes = confirm(question, routed)
        if check_error:
            errored += 1
            error_lines.append(f"[content-check] error: {check_error}")
        # Only files the check actually read may stay: a file past the first
        # CONFIRM_FILES was never read, so it is not evidence of anything.
        checked = set(routed[:CONFIRM_FILES])
        keep = [(scores.get(p, s), p, ptr) for s, p, ptr in merged
                if p in scores or p in partial or notes.get(p) == INCONCLUSIVE]
        dropped = len(checked - {p for _, p, _ in keep} - {p for p, n in notes.items() if n == HELD_SECRET})
        merged = sorted(keep, reverse=True)
    top = merged[:5]
    log(sdir, "lookup", question=question, pointers=len(pointers), statuses=statuses, secs=round(time.time() - t0, 1),
        top=[{"score": s, "path": p, "pointer": ptr} for s, p, ptr in top])
    # Hits always print first: a pointer error must never bury a real candidate
    # from a healthy pointer under the "unresolved" summary below it.
    for s, p, ptr in top:
        note = "  (inconclusive: content check did not finish; routing score)" if notes.get(p) == INCONCLUSIVE else ""
        print(f"{s:5.2f}  {p}  [{ptr}]{note}")
    for p, note in notes.items():
        if note == HELD_SECRET:
            print(f"HELD  {p}  ({HELD_SECRET})")
    for line in error_lines:
        print(line)
    if errored:
        print(f"unresolved: {errored} of {len(pointers)} pointers errored")
        return 1
    if not top:
        if dropped:
            print(f"({dropped} file(s) matched the topic but did not contain the answer on reading)")
        print(f"no-candidates across {len(pointers)} pointers: no connected file answers this. "
              "Tell your human it is not in their files; do not guess. To fill the gap, connect more "
              "files or record a fact with --add (see references/connectors.md).")
        return 0
    return 0

def find_pointer(sdir: Path, question: str):
    path = sdir / "lookups.jsonl"
    if not path.is_file():
        return None
    for line in reversed(path.read_text().splitlines()):
        rec = json.loads(line)
        if rec.get("kind") == "lookup" and rec.get("question") == question and rec.get("top"):
            return rec["top"][0]["pointer"]
    return None

def send_approval(principal: str, question: str, answer: str, pointer: str, ticket_result: dict, sdir: Path) -> int:
    evidence = [{"sourceId": p["sourceId"], "quote": p["reviewedText"]} for p in ticket_result.get("passages", [])[:3] if p.get("reviewedText")]
    res = memory({"action": "approve", "ticket": ticket_result["approvalTicket"], "principal": principal, "approved": True, "answer": answer, "evidence": evidence})
    ok = res.get("status") in ("approved", "saved", "ok")
    print("approve:", res.get("status"), "" if ok else json.dumps(res)[:200])
    log(sdir, "approve", question=question, pointer=pointer, result=res.get("status"))
    return 0 if ok else 1

def approve(principal: str, question: str, answer: str, sdir: Path, pointer=None) -> int:
    pointer = pointer or find_pointer(sdir, question)
    if not pointer:
        print("no prior lookup with candidates for that question; run ask first, or use --add")
        return 1
    out = memory({"action": "search", "pointer": pointer, "principal": principal, "question": question})
    if out.get("status") == "verified-cache-hit":
        print("already cached")
        return 0
    if out.get("status") != "ready":
        print(f"cannot approve: search returned {out.get('status')} on {pointer}. Use --add to record it manually.")
        log(sdir, "approve", question=question, pointer=pointer, result=out.get("status"))
        return 1
    return send_approval(principal, question, answer, pointer, out, sdir)

ASSIST_DISABLED_HINT = ("assist disabled: an operator must set \"allowAgentAssist\": true in the "
                        "memory config (the one setup.py wrote, ~/.local/state/super-jev/_memory/"
                        "config.json, or the file passed with --config) before --add can approve a "
                        "manual entry that retrieval does not match on its own.")

def approve_manual(principal: str, question: str, answer: str, pointer: str, source_id: str, record: Path, sdir: Path) -> int:
    """Approve a just-registered manual pointer, falling back to assisted review on a retrieval miss."""
    out = memory({"action": "search", "pointer": pointer, "principal": principal, "question": question})
    if out.get("status") == "verified-cache-hit":
        print("already cached")
        return 0
    if out.get("status") == "ready":
        return send_approval(principal, question, answer, pointer, out, sdir)
    attempt_id = out.get("attemptId")
    if not attempt_id:
        print(f"cannot approve: search returned {out.get('status')} on {pointer} with no attempt to assist from.")
        log(sdir, "approve", question=question, pointer=pointer, result=out.get("status"))
        return 1
    last_line = len(record.read_text().splitlines())
    reason = f"manual entry for {pointer}: the record is a single small reviewed source and its own text is the answer."
    assisted = memory({"action": "assist", "attemptId": attempt_id, "principal": principal, "reason": reason,
                       "references": [{"sourceId": source_id, "startLine": 1, "endLine": last_line}]})
    if assisted.get("status") == "error" and assisted.get("reason") == "agent assist is disabled":
        print(ASSIST_DISABLED_HINT)
        log(sdir, "approve", question=question, pointer=pointer, result="assist-disabled")
        return 1
    if assisted.get("status") != "ready":
        print(f"cannot approve: assist returned {assisted.get('status')} on {pointer}.")
        log(sdir, "approve", question=question, pointer=pointer, result=assisted.get("status"))
        return 1
    return send_approval(principal, question, answer, pointer, assisted, sdir)

def add_manual(principal: str, question: str, answer: str, source, sdir: Path,
                kind: str = DEFAULT_KIND, status: str = DEFAULT_STATUS,
                as_of: str = None, subject: str = None, replace: bool = False) -> int:
    labels = validate_labels({
        "kind": kind, "status": status,
        "as_of": as_of or time.strftime("%Y-%m-%d"),
        "subject": subject or derive_subject(question),
    })
    pointer = manual_pointer_name(principal, question)
    exists = pointer in my_pointers(principal)
    if exists and not replace:
        print(f"refused: {pointer} already exists for this question wording; use different wording, --replace-entry, or remove the pointer explicitly.")
        return 1
    manual_dir = sdir / "manual"
    manual_dir.mkdir(parents=True, exist_ok=True)
    record = manual_dir / f"{pointer}.md"
    if exists and replace:
        res = memory({"action": "remove", "pointer": pointer, "principal": principal})
        print(f"removed existing manual entry: pointer {pointer} ({res.get('status', res)})")
        if record.is_file():
            record.unlink()
    lines = [f"# {question}", "", f"kind: {labels['kind']}", f"status: {labels['status']}",
             f"as_of: {labels['as_of']}", f"subject: {labels['subject']}", f"project: {principal}", ""]
    if source and Path(source).is_file():
        src = Path(source).resolve()
        lines += [f"source_path: {src}", f"source_sha256: {sha256_file(src)}", ""]
    lines += [answer, "", f"recorded: {time.strftime('%Y-%m-%d %H:%M %Z')}"]
    record.write_text("\n".join(lines) + "\n")
    description = f"{question[:120]} {label_bracket(labels)}"
    req = {"action": "connect", "pointer": pointer, "principals": [principal], "sources": [{"path": str(record), "description": description}]}
    if replace:
        # `remove` (called above when the panel still lists the pointer) drops it from
        # this principal's approved-answer set, but the harness keeps the dataset
        # registered under that pointer name -- independent of the panel listing, so a
        # pointer already removed in an earlier run still needs replace:true here.
        # --replace-entry is an explicit "this may already be registered" assertion,
        # so always send it once the flag is given, not only when panel still shows it.
        req["replace"] = True
    preview = memory(req)
    if preview.get("status") != "preparation-required" or "sources" not in preview:
        print("connect preview failed:", json.dumps(preview)[:300])
        return 1
    hashes = {x["path"]: x["sha256"] for x in preview["sources"]}
    for s in req["sources"]:
        s["sha256"] = hashes[s["path"]]
    req["reviewed"] = True
    reg = memory(req)
    if reg.get("status") != "registered" or not reg.get("sources"):
        print("connect failed:", json.dumps(reg)[:300])
        return 1
    print(f"manual entry written: {record.name}; pointer {pointer} registered")
    source_id = reg["sources"][0]["id"]
    return approve_manual(principal, question, answer, pointer, source_id, record, sdir)

def resolve_principal(args: list) -> tuple[str, list]:
    if "--principal" in args:
        i = args.index("--principal")
        return args[i + 1], args[:i] + args[i + 2:]
    return os.environ.get("SUPERJEV_PRINCIPAL", ""), args

def main() -> int:
    principal, a = resolve_principal(sys.argv[1:])
    if not principal or not a:
        print(__doc__)
        return 2
    sdir = state_dir(principal)
    if a[0] == "--miss":
        log(sdir, "miss", question=a[1], actual=" ".join(a[2:]))
        print("miss recorded")
        return 0
    if a[0] == "--approve":
        return approve(principal, a[1], a[2], sdir)
    if a[0] == "--add":
        return do_add(principal, a[1:], sdir)
    return lookup(" ".join(a), principal, sdir)

ADD_VALUE_FLAGS = {"--source", "--subject", "--kind", "--status", "--as-of"}

def do_add(principal: str, a: list, sdir: Path) -> int:
    """Parse `--add "question" "answer words..." [flags]`. Flags may appear anywhere
    after the question; everything else in order becomes the answer text."""
    if not a:
        print("usage: --add \"question\" \"answer\" [--source PATH] [--subject TEXT] "
              "[--kind KIND] [--status STATUS] [--as-of YYYY-MM-DD] [--replace-entry]")
        return 2
    question, rest = a[0], a[1:]
    values = {f: None for f in ADD_VALUE_FLAGS}
    replace, answer_words, i = False, [], 0
    while i < len(rest):
        tok = rest[i]
        if tok in ADD_VALUE_FLAGS:
            if i + 1 >= len(rest):
                print(f"usage: {tok} requires a value"); return 2
            values[tok] = rest[i + 1]
            i += 2
        elif tok == "--replace-entry":
            replace = True
            i += 1
        else:
            answer_words.append(tok)
            i += 1
    kind = values["--kind"] or DEFAULT_KIND
    status = values["--status"] or DEFAULT_STATUS
    as_of = values["--as-of"] or time.strftime("%Y-%m-%d")
    if kind not in KIND_VALUES:
        print(f"usage: --kind must be one of {sorted(KIND_VALUES)}"); return 2
    if status not in STATUS_VALUES:
        print(f"usage: --status must be one of {sorted(STATUS_VALUES)}"); return 2
    if not DATE_RE.match(as_of):
        print("usage: --as-of must be YYYY-MM-DD"); return 2
    return add_manual(principal, question, " ".join(answer_words), values["--source"], sdir,
                       kind=kind, status=status, as_of=as_of, subject=values["--subject"], replace=replace)

if __name__ == "__main__":
    sys.exit(main())
