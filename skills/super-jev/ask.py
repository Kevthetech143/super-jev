#!/usr/bin/env python3
"""Front door for the super-jev harness: one cache-first lookup loop, any agent.

  ask.py --principal AGENT --status
      Show this principal's connected snapshots and next steps, without a lookup.

  ask.py --principal AGENT --preflight [--project-dir DIR ...] [--about "the work"]
         [--skill "what a new skill would do"] [--json]
      Before work starts. Free part: connections ready, and per folder how many
      connectable files are connected. With --about: 4 asks (tried before, rules,
      traps, files) and NEW GROUND when nothing strong is known; --skill searches
      for an existing skill. Verdict READY / READY WITH WARNINGS / NOT READY (exit 1).

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

  ask.py --principal AGENT --claim "statement" [--claim "statement2" ...] [--claims-file FILE]
      Is a statement true by our own files? One lookup per statement; the same
      listwise Jev call also judges each shown file (supports / partly /
      contradicts / does not state) and picks the line that shows it. Prints
      TRUE / FALSE (>= 0.90, with the proof file, date and line), CONFLICT
      (sure files disagree; newest first), PARTIAL, UNSURE (read these files) or
      NOT FOUND (in the files read; may still exist), then the files found. A
      sure TRUE/FALSE is saved and answers the same statement instantly until
      its proof file changes. --claims-file FILE checks one statement per line
      (blank lines and # comments skipped): a draft's facts, or a worker
      report's claims next to `dispatch.py verify REPORT` for its tests and git.

  ask.py --principal AGENT --approve "question" "answer" [--rank N | --file PATH]
      Re-searches the last lookup's top pointer and approves it, quotes taken
      verbatim from reviewedText. Next ask of the same question is a cache
      hit. Nothing ready -> hints --add. --rank N (1-based, as printed) or
      --file PATH approves any listed candidate instead, possible tier
      included, from that candidate's own pointer.

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
      and, if changed, WITHHOLDS the answer (STALE) and falls through to a
      fresh live search instead of serving stale evidence.

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

  ask.py --principal AGENT --answer "question" "answer" [--no-auto]
      Auto-cache: a machine approval that needs evidence. Runs the check gate
      (superjev.py gate) on the answer against the last lookup's top file. Only
      a CLEAN verdict (every claim SUPPORTED at or above 0.80; the claim rows
      decide, HAS_LEAKS/SELF_CONTRADICTORY rows are advisory) on a file that is
      unchanged since connect saves it, exactly like --approve, recorded as
      approved_by=auto-check with the evidence file and score. READ, REJECT,
      ERROR, a TIME_SENSITIVE flag (a dated fact such as a price
      or breakeven; advisory in check --claim, blocking here), a stale file, or a secret in the file or answer saves nothing and
      prints why. On by default; off with --no-auto or SUPERJEV_AUTO_CACHE=0.

  ask.py --principal AGENT --followup [--max-tries N]
      Re-tries every pending --miss (one not yet --approve'd or already
      dropped) by re-running its lookup, in case pointers refreshed since the
      miss. A CONFIRMED top file (never a possible-only hit) is printed as a
      proposal with the --approve command to run -- it never approves on its
      own, a human still supplies the answer text. A miss that stays
      unconfirmed for --max-tries retries (default 5) is dropped and printed
      as such. Read-only otherwise: no state beyond the usual lookups.jsonl
      log lines (followup-try/followup-drop).

  ask.py --principal AGENT --miss "question" "where it actually was"
      Logs the miss. If that exact question is a cache hit, the saved answer
      (human or auto-check) is un-saved so the next ask looks it up fresh.

  ask.py --principal AGENT --used <lookup_id|last> (--rank N | --file PATH) [--answer "text"]
      Pick trail: records "the agent used choice N" of that trace into
      $STATE/pending_picks.jsonl (question, file, answer) and a "pick" line
      (rank, file) in traces.jsonl. When SUPERJEV_PICK_BATCH (default 5)
      unchecked picks queue, or on --flush-picks, each pick's answer is
      checked against its chosen file with the same gate as --answer: CLEAN
      saves as approved_by=agent-pick+check; REJECT/CONTRADICTED/
      TIME_SENSITIVE/stale/secret drops it; anything else (or no answer text)
      stays pending. --pending-picks lists them; --confirm-pick ID ["answer"]
      records explicit approval by the caller principal, --drop-pick ID removes it.

Cache hits print the caller principal for --approve and --add (principal:NAME)
or "approved_by: auto-check" (--answer), from $STATE/approvals.jsonl.

LIVE DECISION TRACES: every live lookup (a real navigate/content-check pass,
never a cache hit) appends one JSON line to $STATE/traces.jsonl -- timestamp,
lookup id, question, each pointer's routing candidates with scores, the
content-check scores/labels, the final ranked list, a tier (confirmed/
possible/none), timings and errors. No file contents or secrets ride in a
trace line (scanned with the same secret scan prepare_bulk uses; long fields
are truncated). The file rotates at ~20MB, keeping one old generation
(traces.jsonl.1). OUTCOMES link to the last trace for that principal+question:
--approve marks it "right" (with the evidence file); --miss marks it "wrong"
(with the path it was actually found at); --add run right after a miss marks
it "wrong, added". `ask.py --principal AGENT --trace-report [--days N]`
prints read-only counts of right/wrong/unlabeled and the top wrong questions
with their ranked lists -- the input for weekly tuning.
`ask.py --principal AGENT --trace-show <lookup_id|last>` prints one trace's
stages (cache, routing + none-probability, word-search top 10 with each
file's fate, read list, chunks/wording/score per content check, near-twin
tie-break, final ranking with the rule that kept each file) as plain lines.

JEV'S VOICE: when a lookup returns no usable answer (no confirmed or possible
file, or only errors), the very last line printed is exactly:
    Super Jev: I didn't have this. Want me to find it by hand and save it for next time?
Just above that line a no-candidates miss prints "What was searched:" (connected sets
checked and which looked on topic, files read with the closest named) and "Next step
(pick one):" with the exact connect and --add commands, so the agent can act on the
miss without digging. A hit prints nothing extra. Any harness relaying ask.py's output to a human
should relay that line to them verbatim, unedited.

AGENT can also come from SUPERJEV_PRINCIPAL. State lives under
$SUPERJEV_STATE_DIR or ~/.local/state/super-jev/<principal>/, never in this repo.
"""
import contextlib
import difflib
import fcntl
import hashlib
import math
import json
import os
import re
import shlex
import subprocess
import tempfile
import sys
import threading
import time
import traceback
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_bulk  # noqa: E402
from prepare_bulk import (KIND_VALUES, STATUS_VALUES, DATE_RE, validate_labels, label_bracket, has_secret,  # noqa: E402
                          payload_has_secret, path_has_secret, redact_path_secrets)
import auto_heal  # noqa: E402
import refresh_changed  # noqa: E402

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

class SecretHeld(RuntimeError):
    """A request carried a secret, so it was never sent. main() exits 1."""


def memory(req: dict) -> dict:
    # Every memory request (navigate, search, cached, add ...) goes through here: one scan.
    if payload_has_secret(req):
        raise SecretHeld("memory request contains a secret; not sent")
    r = subprocess.run([sys.executable, str(SKILL), "memory", "--input", "/dev/stdin"], input=json.dumps(req), capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"status": "error", "raw": (r.stdout + r.stderr)[-300:]}

def log(sdir: Path, kind: str, **fields) -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    # Not redacted/truncated: lookups.jsonl is the working index find_pointer/find_top
    # read back by exact question, pointer and on-disk path (--approve, --answer).
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": kind, **fields}
    (sdir / "lookups.jsonl").open("a").write(json.dumps(entry) + "\n")

# --- Live decision traces (traces.jsonl, next to lookups.jsonl) ------------------
VOICE_LINE = "Super Jev: I didn't have this. Want me to find it by hand and save it for next time?"
TRACE_CAP_BYTES = 20 * 1024 * 1024  # rotate at ~20MB
TRACE_FIELD_MAX_CHARS = 500

def _redact(value):
    """Recursively hold back secret-shaped strings and truncate long fields --
    a trace line never carries file contents or secrets. Reuses the same
    card/password-key scan prepare_bulk.py runs before onboarding a file."""
    if isinstance(value, str):
        if has_secret(value):
            return "[redacted]"
        if path_has_secret(value):
            value = redact_path_secrets(value)
        if len(value) > TRACE_FIELD_MAX_CHARS:
            return value[:TRACE_FIELD_MAX_CHARS] + "...[truncated]"
        return value
    if isinstance(value, dict):
        return {_redact(k) if isinstance(k, str) else k: _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value

def _rotate_if_needed(path: Path, cap_bytes: int = TRACE_CAP_BYTES) -> None:
    """Once path reaches cap_bytes, move it to path + '.1' (overwriting any
    older .1), keeping exactly one prior generation."""
    if path.is_file() and path.stat().st_size >= cap_bytes:
        old = path.with_suffix(path.suffix + ".1")
        if old.is_file():
            old.unlink()
        path.rename(old)

def traces_enabled() -> bool:
    """SUPERJEV_TRACES=0 (or any falsy-looking value) turns tracing off, e.g. to
    measure the overhead traces.jsonl writes add to a hot path."""
    return os.environ.get("SUPERJEV_TRACES", "1").strip().lower() not in ("0", "off", "false", "no", "")

def write_trace(sdir: Path, **fields) -> None:
    if not traces_enabled():
        return
    sdir.mkdir(parents=True, exist_ok=True)
    path = sdir / "traces.jsonl"
    try:
        _rotate_if_needed(path)
        entry = _redact({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields})
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # a trace is best-effort; never break the lookup over it

def new_lookup_id(principal: str, question: str, t0: float) -> str:
    return hashlib.sha1(f"{principal}|{question}|{t0}".encode()).hexdigest()[:12]

def last_lookup_id(sdir: Path, question: str):
    """The most recent trace's lookup id for this exact question. sdir is
    already scoped to one principal, so no principal filter is needed."""
    lines = []
    for name in ("traces.jsonl.1", "traces.jsonl"):
        p = sdir / name
        if p.is_file():
            lines += p.read_text().splitlines()
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("kind") == "trace" and rec.get("question") == question:
            return rec.get("lookup_id")
    return None

def last_outcome(sdir: Path, lookup_id: str):
    """The most recently recorded outcome for a lookup id, or None."""
    path = sdir / "traces.jsonl"
    if not lookup_id or not path.is_file():
        return None
    for line in reversed(path.read_text().splitlines()):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("kind") == "outcome" and rec.get("lookup_id") == lookup_id:
            return rec
    return None

def write_outcome(sdir: Path, lookup_id, question: str, result: str, file: str = None) -> None:
    """Record right/wrong/wrong-added against a lookup id. No-op when there is
    no lookup id to link to (e.g. --approve/--miss with no prior live lookup)."""
    if not lookup_id:
        return
    write_trace(sdir, kind="outcome", lookup_id=lookup_id, question=question, result=result, file=file)

# Per-stage detail for the current lookup. Helpers deeper in the pipeline
# (word_search, confirm_one, the near-twin tie-break) drop what they already
# computed here; lookup() resets it and copies it into the trace's "stages".
# Paths and numbers only, lists capped -- never file text. No extra Jev calls.
STAGE_LIST_CAP = 10
_STAGE = {}

def _root_none(out) -> "float | None":
    """Jev's "none of these fits" probability at the catalog root, from a
    navigation result's own trace (None when the result carries no trace)."""
    for step in (out.get("trace") or []) if isinstance(out, dict) else []:
        for ch in (step.get("choices") or []) if isinstance(step, dict) else []:
            if isinstance(ch, dict) and isinstance(ch.get("none"), (int, float)):
                return round(ch["none"], 3)
    return None

def find_trace(sdir: Path, which: str):
    """The trace line for a lookup id, or the newest trace when which == "last"."""
    lines = []
    for name in ("traces.jsonl.1", "traces.jsonl"):
        p = sdir / name
        if p.is_file():
            lines += p.read_text().splitlines()
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("kind") == "trace" and (which == "last" or rec.get("lookup_id") == which):
            return rec
    return None

def trace_show(sdir: Path, which: str) -> int:
    """Read-only: print one lookup's stages as plain lines, so a human can see
    where a file dropped out."""
    rec = find_trace(sdir, which)
    if not rec:
        print(f"no trace found for {which!r} in {sdir}")
        return 1
    st = rec.get("stages") or {}
    print(f"trace {rec.get('lookup_id')}  {rec.get('ts')}  tier={rec.get('tier')}  "
          f"{(rec.get('timings') or {}).get('total_secs')}s")
    print(f"question: {rec.get('question')}")
    c = st.get("cache") or {}
    if c:
        print(f"1 cache: {c.get('result')} ({c.get('checked')} pointer(s) checked)")
    if not st.get("routing"):
        if not st:
            print("(no stage detail: this trace predates --trace-show)")
        return 0
    print("2 routing (name + description only; floor %s):" % st.get("routing_floor", ROUTE_FLOOR))
    for ptr, r in st["routing"].items():
        none = f" none={r['none']}" if r.get("none") is not None else ""
        files = ", ".join(f"{Path(f['path']).name} {f['score']}{'' if f.get('kept') else ' (under floor)'}"
                          for f in r.get("files", []))
        print(f"  [{ptr}] {r.get('status')}{none} {r.get('secs', '')}s" + (f": {files}" if files else ""))
    for b in st.get("benched", []):
        print(f"  benched: {b}")
    w = st.get("word_search") or {}
    print(f"3 word search terms={w.get('terms')} files={w.get('files_searched')} "
          f"covering>=50%={w.get('passed_coverage')} slots={FALLBACK_FILES}:")
    edited = set(w.get("changed_since_connect") or [])
    for i, f in enumerate(w.get("top", []), 1):
        mark = " (edited since connect: current text)" if f["path"] in edited else ""
        print(f"  {i:2}. {f['score']:7.3f}  {f['path']}{mark}  -> {f['fate']}")
    print(f"4 read list ({len(st.get('read_list', []))}): " + ", ".join(Path(p).name for p in st.get("read_list", [])))
    print("5 content check (selected source evidence; unfinished checks are inconclusive):")
    for path, d in (st.get("content_check") or {}).items():
        chunks = f"chunks {d.get('read')} of {d.get('chunks')}" if d.get("chunks") is not None else "not sent"
        print(f"  {Path(path).name}: {d.get('verdict')}  best={d.get('best')} none={d.get('none')} "
              f"{chunks} wording={d.get('wording')}  ({path})")
    t = st.get("tiebreak") or {}
    print(f"6 near-twin tie-break: {t.get('result', 'not reached')}" +
          (f" winner={t['winner']}" if t.get("winner") else ""))
    pf = st.get("person") or {}
    if pf.get("who"):
        print(f"  person filter: about {', '.join(pf['who'])}; skipped pointers {pf.get('skipped_pointers') or []}"
              f"; dropped {pf.get('dropped') or []}")
    for mv in st.get("source_moves", []):
        print(f"  source first: {Path(mv['path']).name} ({mv['kind']}) moved below {Path(mv['below']).name}")
    print("7 final ranking:")
    for i, f in enumerate(st.get("final", []), 1):
        print(f"  {i}. {f['score']}  {f['path']}  [{f['rule']}]")
    for p in st.get("cut_after_top5", []):
        print(f"  cut (past top 5): {p}")
    if not st.get("final"):
        print("  (nothing kept)")
    return 0

def trace_report(sdir: Path, days=None) -> int:
    """Read-only: counts of right/wrong/unlabeled traces, and the top wrong
    questions with their ranked list -- the weekly-tuning input."""
    lines = []
    for name in ("traces.jsonl.1", "traces.jsonl"):
        p = sdir / name
        if p.is_file():
            lines += p.read_text().splitlines()
    if days is not None and days <= 0:
        print("--days must be a positive number")
        return 2
    cutoff = time.time() - days * 86400 if days else None
    traces, outcomes = {}, {}
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if cutoff is not None:
            try:
                ts = time.mktime(time.strptime(rec.get("ts", "")[:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                ts = None
            if ts is not None and ts < cutoff:
                continue
        lid = rec.get("lookup_id")
        if not lid:
            continue
        if rec.get("kind") == "trace":
            traces[lid] = rec
        elif rec.get("kind") == "outcome":
            outcomes[lid] = rec  # last one for this lid wins (file is append-only)
    right = wrong = unlabeled = 0
    wrong_questions = Counter()
    for lid, tr in traces.items():
        oc = outcomes.get(lid)
        if not oc:
            unlabeled += 1
        elif oc.get("result") == "right":
            right += 1
        else:
            wrong += 1
            wrong_questions[tr.get("question", "")] += 1
    print(f"right: {right}  wrong: {wrong}  unlabeled: {unlabeled}")
    if wrong_questions:
        print("\ntop wrong questions:")
        for q, n in wrong_questions.most_common(10):
            print(f"  ({n}) {q}")
            lid = next((l for l, tr in traces.items()
                       if tr.get("question") == q and outcomes.get(l, {}).get("result") != "right"), None)
            for row in (traces.get(lid) or {}).get("final_ranked", [])[:5]:
                print(f"      {row.get('score', 0):5.2f}  {row.get('path', '')}  [{row.get('pointer', '')}]")
    return 0

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
            print(f"STALE: source changed since this answer was recorded ({stale_source}); answer withheld. Re-add with --add --replace-entry after verifying.")
            return 1
    print("CACHE HIT")
    print("answer:", hit.get("answer") or "")
    who = approver(sdir, question)
    print("approved_by:", who.get("approved_by", "human") + (
        f" (evidence {who['evidence_file']}, score {who['score']:.2f})" if who.get("evidence_file") else ""))
    for e in (hit.get("evidence") or [])[:3]:
        quote = str(e.get("quote", ""))
        line = best_evidence_line(quote, hit.get("answer") or "", question)
        print("  evidence:", e.get("sourceId", ""), "|", line[:120])
    return 0

# navigation-cli refuses longer questions (src/enhance/navigation.ts MAX_QUESTION)
MAX_QUESTION = 8000
# What paid_replay.py may rely on, checked by value. 2 = with SUPERJEV_REPLAY=1 a lookup reads
# and writes no saved answer or claim verdict, never reconnects or auto-heals a stale
# pointer, and `ask.py --principal P -- QUESTION` reads QUESTION literally.
REPLAY_PROTOCOL = 2
# How many navigate() calls run at once (each is its own remote provider call).
# Default 6 keeps a many-pointer lookup off the provider's queue; override for a
# faster/slower provider. Falls back to the default on a non-positive-int value.
def _nav_concurrency() -> int:
    try:
        n = int(os.environ.get("SUPERJEV_NAV_CONCURRENCY", "6"))
        return n if n > 0 else 6
    except ValueError:
        return 6
NAV_CONCURRENCY = _nav_concurrency()
# Routing and content checks batch their Jev questions into shared calls (see
# src/enhance/coalesce.ts). SUPERJEV_BATCH_JEV=0 goes back to one call per pointer/file.
def batch_jev() -> bool:
    return os.environ.get("SUPERJEV_BATCH_JEV", "1") != "0"

# Sick-pointer circuit breaker: a pointer that fails N times in a row is benched
# for a short cool-off instead of being retried (and waited on) every single call.
# Health is per-principal, persisted in state so it survives across processes.
# Real pain (2026-09-24): 3-4 of 22 pointers erroring every call ("Navigation
# provider failed") turned an 8s median lookup into 59.5s, and rc=1 on every
# call even though most pointers were fine.
def _bench_threshold() -> int:
    try:
        n = int(os.environ.get("SUPERJEV_BENCH_THRESHOLD", "3"))
        return n if n > 0 else 3
    except ValueError:
        return 3
def _bench_cooldown_secs() -> float:
    try:
        n = float(os.environ.get("SUPERJEV_BENCH_COOLDOWN_SECS", "120"))
        return n if n > 0 else 120.0
    except ValueError:
        return 120.0
BENCH_FAIL_THRESHOLD = _bench_threshold()
BENCH_COOLDOWN_SECS = _bench_cooldown_secs()
# A 529/"overloaded" navigate gets exactly one retry after a short backoff,
# rather than being counted as a failure on the first try.
OVERLOAD_BACKOFF_SECS = 1.0

def _is_overloaded(text: str) -> bool:
    t = (text or "").lower()
    return "529" in t or "overload" in t

# preparation-required / refresh-required is a STALE pointer (needs a refresh run),
# not a live failure of the provider -- benching it hides its real, current facts
# behind a generic "benched" message instead of the honest "still preparing" one
# (post-#133 regression: amazon's amazon-bm-fb-brain-root sat preparation-required
# on 451 files > the 250-file cap and got benched, turning its real facts into
# "no candidates" every call). Only a real error (5xx/timeout/provider failure)
# should count toward the circuit breaker. Same rule auto_heal.py already uses to
# decide what is worth auto-refreshing -- reuse it so the two never drift apart.
_is_stale_kind = auto_heal.is_stale_kind

_ROOT_PTR_RE = re.compile(r"^(?P<principal>.+)-brain-root(-\d+)?$")

def is_principal_brain_root(ptr: str, principal: str) -> bool:
    """The principal's own brain root pointer(s) (`<principal>-brain-root`,
    `-root-2`, ...) are never benched: a bot's own brain is its primary source of
    truth, so hiding it behind a cool-off (even a deserved one) is worse than a
    slower call. It still shows its real status (including stale) every time."""
    m = _ROOT_PTR_RE.match(ptr)
    return bool(m) and m.group("principal") == principal

def health_path(sdir: Path) -> Path:
    return sdir / "pointer_health.json"

def load_pointer_health(sdir: Path) -> dict:
    try:
        return json.loads(health_path(sdir).read_text())
    except Exception:
        return {}

def save_pointer_health(sdir: Path, health: dict) -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    try:
        health_path(sdir).write_text(json.dumps(health))
    except Exception:
        pass  # health tracking is best-effort; never block a lookup on it

def record_pointer_outcome(health: dict, ptr: str, ok: bool, elapsed: float, stale: bool = False) -> None:
    rec = health.setdefault(ptr, {"fails": 0, "last_fail_ts": 0.0, "avg_latency": 0.0, "calls": 0, "stale": False})
    rec["calls"] = rec.get("calls", 0) + 1
    prev_avg = rec.get("avg_latency", 0.0)
    rec["avg_latency"] = prev_avg + (elapsed - prev_avg) / rec["calls"]
    rec["stale"] = stale
    # preparation-required/refresh-required is not a live failure -- it never
    # feeds the fail counter (up or down): it neither trips the breaker nor
    # papers over a real streak of errors that happened right before it.
    if stale:
        return
    if ok:
        rec["fails"] = 0
    else:
        rec["fails"] = rec.get("fails", 0) + 1
        rec["last_fail_ts"] = time.time()

def pointer_benched(health: dict, ptr: str, principal: str = ""):
    """(is_benched, seconds_remaining, fail_count) -- N consecutive real errors
    benches a pointer for a short cool-off instead of hitting it (and waiting on
    it) again every call. Never benches the principal's own brain root: that is
    the bot's primary source of truth, and a stale (preparation-required) root
    is not a failure -- see record_pointer_outcome."""
    if is_principal_brain_root(ptr, principal):
        return False, 0, (health.get(ptr) or {}).get("fails", 0)
    rec = health.get(ptr)
    fails = rec.get("fails", 0) if rec else 0
    if fails < BENCH_FAIL_THRESHOLD:
        return False, 0, fails
    remaining = BENCH_COOLDOWN_SECS - (time.time() - rec.get("last_fail_ts", 0))
    if remaining <= 0:
        return False, 0, fails
    return True, remaining, fails
# Content check: routing picks files from their one-line descriptions only, so it
# can match an absent fact on topic alone and miss a present one. Each top routed
# file is checked against its own text. Ordinary retrieval accepts a selected
# requested fact at SOURCE_FLOOR, including evidence that supplies only part of
# the answer. The same rule applies to every query; route confidence, filenames
# and complete-answer comparisons do not override it. Unfinished checks remain
# explicit. Claim mode retains its separate support/contradiction judgment.
CONFIRM_FILES, CONFIRM_CHUNK, CONFIRM_CHUNKS_PER_FILE = 5, 3500, 4
READ_CHARS = CONFIRM_CHUNK * CONFIRM_CHUNKS_PER_FILE  # most text one file's check sends
# A file this small is judged whole in one passage: split into pieces, a table or a list
# spreads Jev's confidence across them and none reaches the bar. Same text sent either way.
WHOLE_FILE_CHARS = 12000
ROUTE_FLOOR, CONFIRM_FLOOR = 0.05, 0.85
SOURCE_FLOOR = 0.7
CLAIM_CONTENT_FLOOR = 0.6
POSSIBLE_NOTE = "  (possible: on topic, answer not confirmed; read the file before answering)"
# Word search: on every lookup the
# principal's reviewed files (prepare-cache entries whose sha256 still matches) are
# searched locally for the question's words (typo-tolerant), and the best
# FALLBACK_FILES get the same source-evidence check as routed files.
FALLBACK_FILES, FALLBACK_MIN_COVERAGE, FALLBACK_REL_FLOOR = 5, 0.5, 0.55
FALLBACK_NOTE = "  (possible: word-search match, answer not confirmed; read the file before answering)"
# Words are Unicode letters/digits, case- and accent-folded, so "¿Cuántas medicinas
# toma mi papá?" gives cuantas/medicinas/toma/papa (an ASCII-only [a-z0-9] split
# "papá" into "pap" and "cuántas" into "cu" + "ntas"). Every word match (word search,
# cover gate, prefilter, term_hits) goes through words()/fold(). Bump WORDS_VERSION
# when this changes: saved pointer words carry it and rebuild on a mismatch.
WORD_RE = re.compile(r"[^\W_]+")
WORDS_VERSION = 3  # 3: word lists read every sources page (were first 25 files only)

_ASCII_WORD_RE = re.compile(r"[a-z0-9]+")  # same result on ASCII text, and faster

def fold(text: str) -> str:
    """Casefold and drop accents: "Papá" -> "papa"."""
    if text.isascii():
        return text.lower()
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in text if not unicodedata.combining(c))

def words(text: str) -> list:
    if text.isascii():
        return _ASCII_WORD_RE.findall(text.lower())
    # NFC first so a decomposed "e" + accent (macOS file names) stays one word;
    # folding word by word is about 2x faster than folding the whole text.
    found = WORD_RE.findall(unicodedata.normalize("NFC", text).casefold())
    return [w if w.isascii() else fold(w) for w in found]
QUERY_STOPWORDS = SUBJECT_STOPWORDS | {
    "i", "me", "we", "our", "us", "you", "can", "could", "should", "would", "will", "get",
    "got", "much", "many", "any", "now", "still", "need", "there", "about", "into", "out",
    "whats", "hows", "whens", "wheres", "whos", "im", "ive", "dont", "not", "no", "yes",
    "all", "some", "just", "so", "if", "than", "then", "up", "tell", "know", "right",
} | {  # Spanish question words, accent-folded like every other word
    "que", "cual", "cuales", "quien", "quienes", "como", "donde", "cuando", "cuanto", "cuanta",
    "cuantos", "cuantas", "por", "para", "con", "sin", "del", "los", "las", "una", "uno", "unos",
    "unas", "mis", "tus", "sus", "esta", "este", "estos", "estas", "esa", "ese", "eso",
    "fue", "muy", "pero", "tiene", "tengo",
}
SOURCE_LABEL = "Passage {n}: source text"
# Word search only: vague words that name a file's topic in other words.
SYNONYMS = {"verify": ["check", "feedback"], "rebalance": ["watchlist", "allocation"],
            "holdings": ["positions", "watchlist"], "money": ["funding", "revenue", "cash"]}

def confirm_label(question: str) -> str:
    """Retrieval selects evidence; claim mode tests support or contradiction."""
    if _CLAIM["text"]:
        return ("Passage {n}, choose if it provides evidence supporting OR contradicting "
                "the statement; disagreement is relevant evidence, not absence. "
                "A passage only sharing the topic is none.")
    return SOURCE_LABEL
HELD_SECRET = "contains a secret; not sent"
INCONCLUSIVE = "inconclusive"
# navigation-cli's reason when a provider call ran past its timeout (src/enhance/navigation.ts)
NAV_TIMED_OUT = "Navigation provider timed out"

def navigation_command() -> list:
    repo = Path(os.environ["SUPERJEV_REPO"]) if os.environ.get("SUPERJEV_REPO") else Path(__file__).resolve().parents[2]
    return ["node", str(repo / "src" / "navigation-cli.ts")]

# A file read as several passages is one Jev pick among them plus "none", so an
# answer spread over two passages (a list, a split table) or a file with several
# on-topic passages splits its probability: CLOV cousins.md scored 0.47 best
# passage with "none" at only 0.10 (full-run diag, cause B). The file score adds
# back SPREAD_CREDIT of the on-topic mass the best passage did not get. Replayed on
# the eval (recorded best/none, no new calls). Improved on internal eval (details
# kept private). 0.5 was the best-performing value tried; 0.6 added a wrong
# confirm and 1.0 (the whole mass) made it worse.
SPREAD_CREDIT, SPREAD_CAP = 0.5, 0.84

def file_score(best: float, none, passages: int, live: bool = False) -> float:
    """Content score for one file from its best passage and Jev's "none" probability.
    Spread credit ranks accepted evidence but cannot make a below-threshold
    passage pass. The optional live flag remains for callers requesting no
    spread credit; ordinary retrieval applies the same scoring to every query."""
    if live or passages <= 1 or not isinstance(none, (int, float)) or best < CLAIM_CONTENT_FLOOR:
        return best
    blended = max(best, best + SPREAD_CREDIT * (1 - none - best))
    return blended if best >= CONFIRM_FLOOR else min(blended, SPREAD_CAP)

def confirm_one(question: str, path: str):
    """(content score or None, whether the file was too long to read whole, error or None,
    note or None). note is HELD_SECRET (file not sent, not kept) or INCONCLUSIVE (the
    check did not finish; the file is kept on its routing score)."""
    done, ctx = confirm_start(question, path)
    if done:
        return done
    return confirm_finish(question, ctx, *run_navigation(ctx["payload"]))

def run_navigation(payload: dict):
    """(parsed navigation-cli output or None, error or None) for one payload or a batch."""
    try:
        r = subprocess.run(navigation_command(), input=json.dumps(payload), capture_output=True,
                           text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"content check could not run: {e.__class__.__name__}"
    if r.returncode:
        return None, (r.stderr.strip().splitlines()[-1:] or ["content check failed"])[0][:200]
    try:
        return json.loads(r.stdout), None
    except ValueError:
        return None, "content check returned invalid JSON"

def confirm_start(question: str, path: str):
    """(finished confirm_one result, None) when the file is never sent, else (None, what
    confirm_finish needs, including the navigation payload)."""
    try:
        text = Path(path).read_text(errors="replace")
    except OSError as e:
        return (None, False, f"cannot read {path}: {e.strerror or e}", None), None
    # The file may have changed since connect scanned it; never ship a secret to Jev.
    if has_secret(text):
        return (None, False, None, HELD_SECRET), None
    if len(text) <= WHOLE_FILE_CHARS:
        chunks = [text]
    else:
        chunks = split_passages(text)
    partial = False  # long files are judged on chosen passages, never passed through unread
    label = confirm_label(question)
    routed = [lines for _s, lines in sorted((_STAGE.get("section_routes") or {}).get(path) or [], reverse=True)]
    prefer, first = set(), 1
    for i, c in enumerate(chunks):
        last = first + c.count("\n") - (1 if c.endswith("\n") else 0)
        if any(a <= last and first <= b for a, b in routed):
            prefer.add(i)
        first += c.count("\n")  # a passage cut mid-line leaves the next one on the same line
    picked = pick_chunks(question, chunks, prefer)
    detail = _STAGE.setdefault("checks", {})[path] = {
        "chunks": len(chunks), "read": picked[:STAGE_LIST_CAP],
        "wording": "claim-evidence" if _CLAIM["text"] else "source-evidence"}
    leaves = [{"id": f"c{i}", "label": label.format(n=i + 1), "description": with_subject(chunks, i),
               "sourceId": str(i)} for i in picked]
    payload = {"question": question, "limits": {"beamWidth": 5, "maxResults": 10},
               "catalog": {"version": 1, "structure": "flat-files", "rootId": "root",
                           "nodes": [{"id": "root", "label": "Sources",
                                      "description": "Full text of candidate files",
                                      "children": [leaf["id"] for leaf in leaves]}, *leaves]}}
    if not _CLAIM["text"]:
        payload["mode"] = "source-evidence"
    if payload_has_secret(payload):  # the question rides in the payload too
        return (None, partial, None, HELD_SECRET), None
    return None, {"payload": payload, "text": text, "chunks": chunks, "picked": picked,
                  "detail": detail, "partial": partial}

def confirm_finish(question: str, ctx: dict, body, error):
    """confirm_one's result from one navigation-cli output (or its error)."""
    text, chunks, picked, detail, partial = (ctx[k] for k in ("text", "chunks", "picked", "detail", "partial"))
    if error:
        return None, partial, error, None
    if isinstance(body, dict) and body.get("status") == "error":
        return None, partial, str(body.get("reason") or "content check failed")[:200], None
    if not isinstance(body, dict) or body.get("status") not in ("candidates", "no-candidates"):
        # e.g. budget-exhausted: the read did not finish, so "not in file" would be a guess
        return None, partial, None, INCONCLUSIVE
    scores = [c.get("score") for c in body.get("candidates") or [] if isinstance(c, dict)]
    best = max((sc for sc in scores if isinstance(sc, (int, float))), default=0)
    detail.update(best=round(best, 3), none=_root_none(body), status=body.get("status"))
    top_c = max((c for c in body.get("candidates") or [] if isinstance(c, dict)
                 and isinstance(c.get("score"), (int, float))), key=lambda c: c["score"], default=None)
    if top_c and str(top_c.get("sourceId", "")).isdigit():
        detail["best_chunk"] = int(top_c["sourceId"])
        if int(top_c["sourceId"]) < len(chunks):
            detail["best_line"] = best_line(question, chunks, int(top_c["sourceId"]))
    if not _CLAIM["text"]:
        # Evidence mode scores each passage as requested property or required component.
        # The shared source floor admits leads, not proven answers; unlike the
        # old answer threshold it does not depend on the query shape.
        if body["status"] == "no-candidates":
            detail["score"] = 0
            return None, partial, None, None
        if top_c is None or type(best) not in (int, float) or not 0 <= best <= 1:
            return None, partial, "content check returned invalid evidence selection", None
        detail["score"] = round(best, 3)
        return (best if best >= SOURCE_FLOOR else None), partial, None, None
    score = file_score(best, detail["none"], len(picked))
    detail["score"] = round(score, 3)
    return (score if score >= CLAIM_CONTENT_FLOOR else None), partial, None, None

# --- Near-twin tie-break -----------------------------------------------
# When the top 2-3 ranked files are near-twins -- scores within a small gap,
# and either the same folder or similar file names (e.g. a note and its own
# summary sibling) -- ranking alone is often a coin flip: businessfi stress
# test 4 put the right file at rank 2-4 behind a close sibling/summary about
# half the time. One bounded Jev call over the short snippets of just those
# 2-3 files picks the one that best answers the question; anything else
# (no near-twin gap, an errored/inconclusive judge call) leaves ranking
# unchanged. Reuses the same navigation-cli judge path as confirm_one/
# judge_subject -- one extra call, only when it might actually change the
# answer.
NEAR_TWIN_FILES = 3
NEAR_TWIN_GAP = 0.05
NEAR_TWIN_NAME_SIM = 0.5
NEAR_TWIN_SNIPPET = 2000
NEAR_TWIN_LABEL = "Passage {n}, choose only if this is the single best answer to the question"

def is_near_twin(path_a: str, path_b: str) -> bool:
    """Same folder, or similar-enough file names (e.g. "notes.md" vs
    "notes-summary.md"), to be worth one judge call instead of trusting the
    score gap alone."""
    if Path(path_a).parent == Path(path_b).parent:
        return True
    return difflib.SequenceMatcher(None, Path(path_a).stem.lower(),
                                   Path(path_b).stem.lower()).ratio() >= NEAR_TWIN_NAME_SIM

def judge_near_twin(question: str, candidates: list):
    """One Jev call over up to NEAR_TWIN_FILES candidates' short snippets, asking
    which single file best answers the question. Returns the winning path, or
    None if fewer than 2 files could be read/sent, or the call errored or was
    inconclusive -- callers must leave ranking unchanged in that case."""
    texts = {}
    for _, p, _ in candidates[:NEAR_TWIN_FILES]:
        try:
            text = Path(p).read_text(errors="replace")
        except OSError:
            continue
        if has_secret(text):
            continue
        texts[p] = text[:NEAR_TWIN_SNIPPET]
    if len(texts) < 2:
        return None
    ordered = list(texts)
    leaves = [{"id": f"t{i}", "label": NEAR_TWIN_LABEL.format(n=i + 1),
               "description": texts[p], "sourceId": p} for i, p in enumerate(ordered)]
    payload = {"question": question, "limits": {"beamWidth": 5, "maxResults": 10},
               "catalog": {"version": 1, "structure": "flat-files", "rootId": "root",
                           "nodes": [{"id": "root", "label": "Sources",
                                      "description": "Candidate files", "children": [leaf["id"] for leaf in leaves]}, *leaves]}}
    if payload_has_secret(payload):
        return None
    try:
        r = subprocess.run(navigation_command(), input=json.dumps(payload), capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode:
        return None
    try:
        body = json.loads(r.stdout)
    except ValueError:
        return None
    if not isinstance(body, dict) or body.get("status") != "candidates" or not body.get("candidates"):
        return None
    best = max((c for c in body["candidates"] if isinstance(c, dict) and isinstance(c.get("score"), (int, float))),
               key=lambda c: c["score"], default=None)
    return best.get("sourceId") if best else None

# Listwise choice step (design step 5). One Jev choice call over the top
# LISTWISE_MAX_FILES ranked files -- each file's best passage from today's
# content check -- plus a "none of these files states the answer" option, with
# "When torn, pick none".
# A picked file moves to #1 and is promoted to CONFIRM_FLOOR if its own
# probability is >= LISTWISE_PROMOTE_FLOOR; another confirmed file is demoted
# only when the pick is that strong and not blocked. "none" at >= the same floor
# drops every possible hit, so a made-up question reports not found instead of an
# on-topic lookalike; a weaker "none" keeps the files and prints LEANS_NONE_NOTE. A failed call changes nothing.
# Claim mode always runs its verdict judge when evidence is available.
LISTWISE_MAX_FILES = 4
LISTWISE_PROMOTE_FLOOR = 0.9
LISTWISE_NONE = "none"
LEANS_NONE_NOTE = "(Jev leans none of these: read the files before answering; the answer may not be here)"
LISTWISE_INSTRUCTIONS = "Question: %s\nWhich file states the answer? When torn, pick none."

# Claim mode (ask.py --claim "statement"): is a statement true, by our own files?
# The same listwise Jev call also asks, for each file it shows, whether that file
# supports, partly supports, contradicts or does not state the statement, and which
# line of it says so (picked from the file's own lines, never written). No extra
# call. Plain code then combines the per-file answers (claim_verdict): only answers
# at >= CLAIM_SURE count; files that disagree are a CONFLICT, newest first.
CLAIM_SURE = 0.9
MAX_CLAIMS = 25
CLAIM_LINES = 2000  # lines ranked per passage; the line budget picks which are shown
CLAIM_LINE_CHARS = 160
CLAIM_SCAN_CHARS = 5000  # of one line, when ranking lines and placing the window: a minified file stays fast
CLAIM_LINE_BUDGET = 4000  # characters of line choices per file, so a claim call stays well under the input ceiling
CLAIM_CRITERIA = {
    "supported": "the file states that the statement is true",
    "partly": "the file states part of the statement but not all of it",
    "contradicted": "the file states something that makes the statement false",
    "not_stated": "the file does not say whether the statement is true",
}
CLAIM_VERDICT_INSTRUCTIONS = ("Statement: %s\nJudge only the file `%s.text` (%s). Does that file show the "
                              "statement is true or false? When torn, pick not_stated.")
CLAIM_LINE_INSTRUCTIONS = ("Statement: %s\nWhich line of the file `%s.text` (%s) shows whether the statement "
                           "is true or false? When torn, pick none.")
_CLAIM = {"text": None}

def claim_lines(text: str) -> list:
    """Up to CLAIM_LINES distinct candidate lines of a passage (non-empty, 12+ characters), in order."""
    out = {}
    for ln in text.splitlines():
        t = ln.strip()
        if len(t) >= 12:
            out.setdefault(t, len(out))
            if len(out) >= CLAIM_LINES:
                break
    return list(out)

def _stems(ws) -> set:
    """First four letters of each word, so "sources"/"source" and "mark"/"marked" match."""
    return {w[:4] for w in ws}

def claim_window(line: str, want: set) -> str:
    """At most CLAIM_LINE_CHARS of a line: a long markdown paragraph line is shown around the
    stretch holding the most statement words, not cut to its opening, which may not show it."""
    if len(line) <= CLAIM_LINE_CHARS:
        return line
    line = line[:CLAIM_SCAN_CHARS]
    size = CLAIM_LINE_CHARS - 2
    toks = [(m.start(), m.end(), want & _stems(words(m.group()))) for m in re.finditer(r"\S+", line)]
    counts, score, j = {}, [], 0
    for i, (st, _, _) in enumerate(toks):  # one pass: distinct statement words in each window
        j = max(j, i)  # a single word longer than the window (a long URL) is never counted
        while j < len(toks) and toks[j][1] <= st + size:
            for w in toks[j][2]:
                counts[w] = counts.get(w, 0) + 1
            j += 1
        score.append(sum(1 for c in counts.values() if c))
        if j > i:  # token i was counted; it leaves the window now
            for w in toks[i][2]:
                counts[w] -= 1
    best = max(score)
    top = [toks[i][0] for i, n in enumerate(score) if n == best]
    start = min(top[len(top) // 2], len(line) - size)  # the middle of the best stretch
    return ("…" if start else "") + line[start:start + size] + ("…" if start + size < len(line) else "")

def claim_questions(claim: str, ordered: list, files: dict) -> tuple:
    """Per shown file: a verdict question and a line-pick question. Returns (questions, lines by key)."""
    qs, lines = {}, {}
    for i, p in enumerate(ordered):
        key = f"file_{i + 1}"
        qs[f"verdict_{i + 1}"] = {"type": "choice", "criteria": CLAIM_CRITERIA,
                                  "instructions": CLAIM_VERDICT_INSTRUCTIONS % (claim, key, Path(p).name)}
        # Lines sharing the most statement words fill the budget first (a file's opening lines
        # used to crowd out the one that proves it), then are shown in file order.
        want = _stems(set(words(claim)) - QUERY_STOPWORDS)
        found = claim_lines(files[p])
        hits = {t: len(want & _stems(words(t[:CLAIM_SCAN_CHARS]))) for t in found}
        cand, used = [], 0
        for t in sorted(found, key=lambda t: -hits[t]):
            used += min(len(t), CLAIM_LINE_CHARS) + 8
            if used > CLAIM_LINE_BUDGET:
                break
            cand.append(t)
        order = {t: i for i, t in enumerate(found)}
        cand.sort(key=order.get)
        lines[key] = cand
        if cand:
            crit = {f"L{j + 1}": claim_window(t, want) if hits[t] else t[:CLAIM_LINE_CHARS]
                    for j, t in enumerate(cand)}
            crit["none"] = "no line of this file shows it"
            qs[f"line_{i + 1}"] = {"type": "choice", "criteria": crit,
                                   "instructions": CLAIM_LINE_INSTRUCTIONS % (claim, key, Path(p).name)}
    return qs, lines

def _answer_prob(ans: dict):
    probs = ans.get("probabilities") if isinstance(ans, dict) else None
    return probs.get(ans.get("choice")) if isinstance(probs, dict) else ans.get("probability")

def read_claim_answers(answers: dict, ordered: list, lines: dict, shown: dict = None) -> dict:
    """{path: {verdict, prob, line, line_no}} from one Jev response. line_no counts from the
    passage actually shown, so a line repeated earlier in the file is not misnumbered."""
    out = {}
    for i, p in enumerate(ordered):
        v = answers.get(f"verdict_{i + 1}")
        if not isinstance(v, dict) or v.get("choice") not in CLAIM_CRITERIA:
            continue
        rec = {"verdict": v["choice"], "prob": _answer_prob(v), "line": None, "line_no": None}
        ln = answers.get(f"line_{i + 1}")
        m = re.fullmatch(r"L(\d+)", str((ln or {}).get("choice", "")))
        cand = lines.get(f"file_{i + 1}") or []
        if m and 1 <= int(m.group(1)) <= len(cand):
            rec["line"] = cand[int(m.group(1)) - 1]
            try:
                text = Path(p).read_text(errors="replace")
                seen = (shown or {}).get(p) or ""
                if seen not in text:  # a middle passage shown led by the file's title
                    seen = seen.split(SUBJECT_SEP, 1)[-1]
                for off in (max(text.find(seen), 0), 0):  # the title line itself: from the top
                    base = text[:off].count("\n")
                    rec["line_no"] = next((base + k + 1 for k, t in enumerate(text[off:].splitlines())
                                           if t.strip() == rec["line"]), None)
                    if rec["line_no"]:
                        break
            except OSError:
                pass
        out[p] = rec
    return out

def _file_date(p: str):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0

def claim_verdict(files: dict, read: list) -> tuple:
    """(word, lines) combining per-file answers. word is TRUE, FALSE, CONFLICT, PARTIAL,
    UNSURE or NOT FOUND. Only answers at >= CLAIM_SURE decide; files that disagree
    are a CONFLICT with the newest (by modified date) first."""
    day = lambda p: time.strftime("%Y-%m-%d", time.localtime(_file_date(p))) if _file_date(p) else "date unknown"
    sure = {p: f for p, f in files.items() if isinstance(f.get("prob"), (int, float)) and f["prob"] >= CLAIM_SURE}
    yes = [p for p, f in sure.items() if f["verdict"] == "supported"]
    no = [p for p, f in sure.items() if f["verdict"] == "contradicted"]
    part = [p for p, f in sure.items() if f["verdict"] == "partly"]
    quote = lambda p: (f'line {files[p]["line_no"]}: "{files[p]["line"]}"' if files[p].get("line_no")
                       else f'"{files[p]["line"]}"' if files[p].get("line") else "(no single line picked)")
    if yes and no:
        both = sorted(yes + no, key=_file_date, reverse=True)
        word = "CONFLICT"
        out = [f"CONFLICT: the files disagree; the newest says {'TRUE' if both[0] in yes else 'FALSE'}. Read both before answering."]
        out += [f"  {'TRUE ' if p in yes else 'FALSE'} {p} ({day(p)}) {quote(p)}" for p in both]
        return word, out
    if yes or no:
        side = yes or no
        word = "TRUE" if yes else "FALSE"
        best = max(side, key=lambda p: files[p]["prob"])
        out = [f"{word} ({files[best]['prob']:.2f})", f"Proof: {best} ({day(best)})", f"  {quote(best)}"]
        also = [p for p in side if p != best]
        if also:
            out.append("Also says so: " + ", ".join(Path(p).name for p in also))
        other = [p for p in read if p not in side]
        if other:
            out.append("Other files read: " + ", ".join(Path(p).name for p in other))
        return word, out
    if part:
        out = ["PARTIAL: each file holds only part of it; read these before answering:"]
        out += [f"  {p} ({day(p)}) {quote(p)}" for p in part]
        return "PARTIAL", out
    leaning = sorted((p for p, f in files.items() if f["verdict"] != "not_stated"),
                     key=lambda p: -(files[p].get("prob") or 0))
    if leaning:
        f0 = files[leaning[0]]
        out = [f"UNSURE ({f0['verdict']} {f0.get('prob') or 0:.2f}): read these files before answering:"]
        out += [f"  {p}" for p in leaning]
        return "UNSURE", out
    if not read:
        return "NOT FOUND", ["NOT FOUND in the connected files; it may still exist somewhere not connected."]
    return "NOT FOUND", [f"NOT FOUND in the {len(read)} file(s) I read; it may still exist in a file that was not read."]

def claim_key(claim: str) -> str:
    """Case and spacing folded, every symbol kept: "-50" and "50", "<" and ">" stay different claims."""
    return " ".join(claim.casefold().split()).rstrip(".!?")

def claim_cache_path(sdir: Path) -> Path:
    return sdir / "claim-verdicts.json"

def claim_cache_get(sdir: Path, claim: str):
    """A saved sure TRUE/FALSE whose proof file is unchanged, else None (a changed file drops it)."""
    try:
        data = json.loads(claim_cache_path(sdir).read_text())
    except (OSError, ValueError):
        return None
    rec = data.get(claim_key(claim))
    if not isinstance(rec, dict):
        return None
    try:
        same = hashlib.sha256(Path(rec["path"]).read_bytes()).hexdigest() == rec.get("sha")
    except (OSError, KeyError):
        same = False
    if not same:
        data.pop(claim_key(claim), None)
        try:
            claim_cache_path(sdir).write_text(json.dumps(data, indent=1))
        except OSError:
            pass
        return None
    return rec

def claim_cache_put(sdir: Path, claim: str, word: str, path: str, rec: dict) -> None:
    try:
        data = json.loads(claim_cache_path(sdir).read_text())
    except (OSError, ValueError):
        data = {}
    try:
        sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return
    data[claim_key(claim)] = {"claim": claim, "verdict": word, "path": path, "sha": sha, "prob": rec.get("prob"),
                              "line": rec.get("line"), "line_no": rec.get("line_no"), "saved": int(time.time())}
    try:
        sdir.mkdir(parents=True, exist_ok=True)
        claim_cache_path(sdir).write_text(json.dumps(data, indent=1))
    except OSError:
        pass

def best_passage(path: str):
    """The passage today's content check scored best (the file's start if it was
    never checked), or None if the file cannot be read or holds a secret."""
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return None
    if has_secret(text):
        return None
    if len(text) <= WHOLE_FILE_CHARS:  # judged whole, so shown whole
        return text
    i = (_STAGE.get("checks") or {}).get(path, {}).get("best_chunk") or 0
    chunks = split_passages(text)
    return with_subject(chunks, min(i, len(chunks) - 1))

def jev_choice(state: dict, questions: dict) -> dict:
    """One Jev call through the built-in client (lib/jev_client.py)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
    import jev_client
    return jev_client.ask(state, questions, timeout=60)

def judge_listwise(question: str, paths: list):
    """Which of the first LISTWISE_MAX_FILES paths states the answer: (path, its
    probability), (LISTWISE_NONE, probability) when Jev picks none, or (None, None)
    if no file could be sent or the call failed -- callers treat that as no opinion."""
    files = {}
    for p in paths:
        if len(files) >= LISTWISE_MAX_FILES:
            break
        text = best_passage(p)
        if text is not None:
            files[p] = text
    if not files:
        return None, None
    ordered = list(files)
    state = {f"file_{i + 1}": {"path": p, "text": files[p]} for i, p in enumerate(ordered)}
    crit = {f"file_{i + 1}": f"{Path(p).name} answers the question" for i, p in enumerate(ordered)}
    crit[LISTWISE_NONE] = "none of the files states the answer to the question"
    questions = {"pick": {"type": "choice", "instructions": LISTWISE_INSTRUCTIONS % question, "criteria": crit}}
    claim_lines_by_key = {}
    if _CLAIM["text"]:
        extra, claim_lines_by_key = claim_questions(_CLAIM["text"], ordered, files)
        questions.update(extra)
        _STAGE["claim_read"] = ordered
    try:
        try:
            r = jev_choice(state, questions)
        except Exception as e:
            if not _CLAIM["text"] or not re.search(r"ceiling|max_tokens_exceeded", str(e)):
                raise  # only a size refusal is retried; a timeout or auth error is not waited on twice
            # A claim call can only be bigger than a normal one: retry once without the line picks.
            questions = {k: v for k, v in questions.items() if not k.startswith("line_")}
            r = jev_choice(state, questions)
        if _CLAIM["text"]:
            got = read_claim_answers(r.get("answers") or {}, ordered, claim_lines_by_key, files)
            if got:  # no verdicts back means the check did not run, never "not found"
                _STAGE["claim_files"] = got
        pick = r["answers"]["pick"]
        choice = pick["choice"]
    except Exception:
        return None, None
    probs = pick.get("probabilities") if isinstance(pick, dict) else None
    prob = probs.get(choice) if isinstance(probs, dict) else pick.get("probability")
    if choice == LISTWISE_NONE:
        return LISTWISE_NONE, prob
    m = re.fullmatch(r"file_(\d+)", str(choice))
    if not m or not 1 <= int(m.group(1)) <= len(ordered):
        return None, None
    return ordered[int(m.group(1)) - 1], prob

def apply_near_twin_tiebreak(question: str, top: list) -> list:
    """If the top NEAR_TWIN_FILES results are a near-twin cluster (small score
    gap, same folder or similar names), judge just that cluster and put the
    winner first, preserving relative order of everything else. Any other
    case -- not enough candidates, gap too wide, not a near-twin pair, an
    inconclusive/errored judge call -- returns top unchanged."""
    _STAGE["tiebreak"] = {"result": "skipped: fewer than 2 files"}
    if len(top) < 2:
        return top
    _STAGE["tiebreak"] = {"result": f"skipped: no file within {NEAR_TWIN_GAP} of the top score"}
    head = top[:NEAR_TWIN_FILES]
    # Only files within NEAR_TWIN_GAP of the top score form the cluster: a
    # third result far behind (e.g. 0.9, 0.87, 0.3) is not a near-twin of
    # either just because it made the top 3.
    cluster = [head[0]] + [m for m in head[1:] if head[0][0] - m[0] <= NEAR_TWIN_GAP]
    if len(cluster) < 2:
        return top
    _STAGE["tiebreak"] = {"result": "skipped: top files are not near-twins (folder/name)"}
    if not is_near_twin(cluster[0][1], cluster[1][1]):
        return top
    # A hub file (README/index/etc, see is_hub_file) in the cluster means the
    # sort already made a deliberate hub-demotion call -- see sort_metric --
    # to rank a real topic file above it, or (rarer) left the hub at the top
    # because nothing else beat it. Either way that ordering reflects a real
    # content-vs-navigation-hub judgment the sort already made; re-judging a
    # hub against a low-confidence sibling on short snippets alone (2026-09-24
    # regression: "how should I organize my stock campaigns" fell from rank 1
    # to 2 when a new marginal candidate formed a near-twin with the hub
    # README that answered the question) is more likely to override a correct
    # call than fix a wrong one, so hub files sit out the tie-break entirely;
    # so do copies and write-ups (copy_kind).
    _STAGE["tiebreak"] = {"result": "skipped: a hub file is in the cluster"}
    if any(is_hub_file(p) or copy_kind(p) for _, p, _ in cluster):
        return top
    winner = judge_near_twin(question, cluster)
    _STAGE["tiebreak"] = {"result": f"ran on {len(cluster)} files: " + (
        "no pick, order unchanged" if not winner else
        "kept #1" if winner == cluster[0][1] else "moved winner to #1"), "winner": winner}
    if not winner:
        return top
    cluster = sorted(cluster, key=lambda m: m[1] != winner)
    return cluster + top[len(cluster):]

def query_terms(question: str) -> list:
    found = words(question.replace("'", "").replace("\u2019", ""))
    return list(dict.fromkeys(w for w in found if len(w) > 2 and w not in QUERY_STOPWORDS))

def term_hits(terms: list, text: str) -> int:
    """How many terms appear in text, a term also matching by its first five letters."""
    low = fold(text)
    return sum(1 for t in terms if t in low or (len(t) > 5 and t[:5] in low))

def split_passages(text: str) -> list:
    """A long file cut about every CONFIRM_CHUNK characters, each cut moved to the
    nearest line end within half a passage (a longer line is cut where it falls).
    "".join(result) == text. Fixed 3500-character cuts split a rule in two:
    super-jev/SKILL.md line 50 (split parts heal through the parent's recipe)
    straddled chunks 2 and 3, so neither passage stated it whole."""
    cuts, pos = [0], 0
    while len(text) - pos > CONFIRM_CHUNK:
        target = pos + CONFIRM_CHUNK
        ends = [i + 1 for i in (text.rfind("\n", pos + CONFIRM_CHUNK // 2, target),
                                text.find("\n", target, target + CONFIRM_CHUNK // 2)) if i >= 0]
        pos = min(ends, key=lambda i: abs(i - target)) if ends else target
        cuts.append(pos)
    return [text[a:b] for a, b in zip(cuts, cuts[1:] + [len(text)])]

SUBJECT_CHARS, SUBJECT_SEP = 100, "\n...\n"

def with_subject(chunks: list, i: int) -> str:
    """Passage i, led by the file's first non-blank line (its title) when i is not the
    first passage: a middle section read alone can lose its subject (a table row that
    never names what it is about), now that the first passage is not always read."""
    if i == 0:
        return chunks[0]
    title = next((ln.strip() for ln in chunks[0].splitlines() if ln.strip()), "")[:SUBJECT_CHARS]
    return f"{title}{SUBJECT_SEP}{chunks[i]}" if title else chunks[i]

def pick_chunks(question: str, chunks: list, prefer=()) -> list:
    """Indexes to read, in file order: every passage if they fit in READ_CHARS, else
    the passages scoring best by BM25 on the question's words (each word weighted by
    how rare it is within this file; earlier passages win ties, so a file with few
    matches is read from its top) until READ_CHARS is spent. Counting distinct words,
    with the first chunk always read, spent the slots on the intro and on passages
    full of the file's common words, missing the one section that answered. `prefer` (the
    passages of the sections routing chose, for a file connected in sections) are read
    first when they match any question word."""
    # A passage after the first is sent led by the file's title (with_subject).
    cost = [len(c) + (SUBJECT_CHARS + len(SUBJECT_SEP) if i else 0) for i, c in enumerate(chunks)]
    if sum(cost) <= READ_CHARS:
        return list(range(len(chunks)))
    terms = query_terms(question)
    tfs = []
    for c in chunks:
        found = Counter(words(c))
        by_stem = Counter()
        for w, n in found.items():
            by_stem[w[:4]] += n  # first four letters, as claim lines match: "heals"/"heal"
        tfs.append({t: by_stem[t[:4]] for t in terms})
    sizes = [len(c) or 1 for c in chunks]
    avg = sum(sizes) / len(sizes)
    idf = {t: math.log(1 + (len(chunks) - df + 0.5) / (df + 0.5))
           for t, df in ((t, sum(1 for f in tfs if f[t])) for t in terms)}
    score = [sum(idf[t] * f[t] * 2.2 / (f[t] + 1.2 * (0.25 + 0.75 * size / avg)) for t in terms)
             for f, size in zip(tfs, sizes)]
    picked, used = [], 0
    for i in sorted(range(len(chunks)), key=lambda i: (not (i in prefer and score[i]), -score[i], i)):
        if used + cost[i] <= READ_CHARS:
            picked.append(i)
            used += cost[i]
        elif not score[i]:
            break  # no word left to match: read on from the top only, never skip ahead
    return sorted(picked)

def load_cache_files(pointer: str) -> dict:
    """Only prepare-cache/<pointer>.json. Part caches are registered as their own
    pointers (<pointer>-N) and arrive in the principal's pointer list when owned, so
    matching <pointer>-N.json here could read another principal's pointer."""
    if pointer in _STAGE.get("view_pointers", ()):
        return {}  # A leftover bulk cache names raw originals, never reviewed views.
    p = prepare_bulk.CACHE_DIR / f"{pointer}.json"
    try:
        data = json.loads(p.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}

# A written phone number ("212-305-6390") counts as the words "phone" and "number": a note lists
# the number without ever saying "phone" (NYP ENT line in the medical timeline).
PHONE_RE = re.compile(r"(?<!\d)\(?\d{3}\)?[-. ]\d{3}[-.]\d{4}(?!\d)")

def passage_words(text: str) -> Counter:
    counts = Counter(words(text))
    phones = len(PHONE_RE.findall(text))
    counts["phone"] += phones
    counts["number"] += phones
    return counts

# Routing costs one Jev call per pointer (about 21 of 29 calls per ask on the
# 60-question eval). A pointer none of whose files holds a single question word
# (same matching as term_hits, over path, description and reviewed text) is not
# asked: on the eval those 222 of 1361 pointer asks never produced a kept file.
# Each pointer's words are saved per generation, so a refresh re-reads them.
POINTER_WORDS_FILE = "pointer-words.json"
# Other words a file may use for a question word; a match on any keeps the pointer.
PREFILTER_SYNONYMS = {
    "doctor": ["physician", "pcp", "provider", "clinic", "dr"], "physician": ["doctor", "pcp"],
    "dad": ["father", "parent"], "father": ["dad"], "mom": ["mother", "mum", "parent"],
    "mother": ["mom", "mum"], "wife": ["spouse"], "husband": ["spouse"],
    "car": ["vehicle", "auto"], "vehicle": ["car", "auto"], "meds": ["medication", "prescription"],
    "medication": ["meds", "prescription", "drug"], "prescriptions": ["medication", "meds", "rx"],
    "phone": ["number", "tel", "call"], "money": ["cash", "funds", "dollars"], "taxes": ["tax", "irs"],
}

def _load_pointer_words(path: Path) -> dict:
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}

def pointer_words(sdir: Path, generations: dict) -> tuple:
    """({pointer: its files' words joined} for pointers known at their current
    generation, [pointers not known yet]). A pointer whose files could not be
    listed or read is saved with no words: known, but never skipped."""
    saved = _load_pointer_words(sdir / POINTER_WORDS_FILE)
    known, missing = {}, []
    for p, g in generations.items():
        entry = saved.get(p) or {}
        if not g:
            continue
        if entry.get("generation") != g or entry.get("version") != WORDS_VERSION:
            missing.append(p)  # changed files, or words saved by an older tokenizer
        elif entry.get("words") is not None:
            known[p] = entry["words"]
    return known, missing

def save_pointer_words(sdir: Path, principal: str, generations: dict, missing: list) -> None:
    """List and read the missing pointers' files (local, no provider calls) and save
    their words. Runs beside routing, so the first ask after a refresh is not slower."""
    def load(ptr):
        # sources is paged (25 rows by default): read every page, or a pointer's later
        # files never reach its word list and the prefilter skips questions about them.
        rows, offset = [], 0
        while True:
            out = memory({"action": "sources", "pointer": ptr, "principal": principal,
                          "offset": offset, "limit": 100})
            if out.get("status") != "ok":
                return ptr, None
            rows += out.get("sources") or []
            nxt = out.get("nextOffset")
            if not isinstance(nxt, int) or nxt <= offset:
                break  # last page, or a runtime that ignores offset: never loop forever
            offset = nxt
        if not rows:
            return ptr, None
        seen = set()
        for src in rows:
            head = f"{src.get('originalPath', '')} {src.get('description', '')}".lower()
            try:
                seen.update(words(head + " " + Path(src["path"]).read_text(errors="replace")))
            except (OSError, KeyError, TypeError):
                return ptr, None  # a file we cannot read: never skip this pointer
        return ptr, " ".join(sorted(seen))

    try:
        with ThreadPoolExecutor(max_workers=max(1, min(len(missing), NAV_CONCURRENCY))) as pool:
            loaded = list(pool.map(load, missing))
        path = sdir / POINTER_WORDS_FILE
        saved = _load_pointer_words(path)
        for ptr, found in loaded:
            saved[ptr] = {"generation": generations[ptr], "words": found, "version": WORDS_VERSION}
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(saved))
        tmp.replace(path)
    except Exception:
        pass  # best effort: an unsaved pointer is just asked again next time

def refresh_would_admit(path: str, ptr: str) -> bool:
    """Would the pointer's own refresh admit this file where it now resolves? The same rule
    prepare_bulk.inventory applies to a link target: under a recorded root or allow-target,
    no vault folder (profile/, documents/), no hidden folder, no logins/secret/backup name.
    No recorded report (a connector-built pointer): no."""
    report = auto_heal._report_for(ptr, prepare_bulk.CACHE_DIR)[0]
    if not isinstance(report, dict):
        return False
    rp = Path(os.path.realpath(path))
    bases = [Path(os.path.realpath(b)) for b in (report.get("roots") or []) + (report.get("allowTargets") or [])]
    base = next((b for b in bases if rp.is_relative_to(b)), None)
    name = rp.name.casefold()
    if base is None or ".bak" in name or name == "logins.md" or name.endswith("-secret.md"):
        return False
    return not any(x.casefold() in prepare_bulk.SKIP_PARTS or x.startswith(".") for x in rp.relative_to(base).parts)

PHRASE_WEIGHT = 0.7

def word_pairs(text: str) -> set:
    """Adjacent content words of text (stopwords and 1-2 letter words skipped), by their
    first four letters, so "reads the source" pairs read+source like the question does."""
    toks = [w[:4] for w in words(text.replace("'", "").replace("\u2019", ""))
            if len(w) > 2 and w not in QUERY_STOPWORDS]
    return set(zip(toks, toks[1:]))

def connector_names(ptr: str) -> list:
    """The --name list a pointer was connected with (its report; a split part uses its parent's)."""
    return (auto_heal._report_for(ptr, prepare_bulk.CACHE_DIR)[0] or {}).get("names") or []


def edited_readable(path: str, ptr: str, entry, raw: bytes, text: str) -> bool:
    """May a reviewed file edited since connect be read at its current text while its pointer
    waits on the refresh? Only as a refresh would admit it: reviewed at a known version and not
    failed by its last review, under the size ceiling, no secret-looking line, and still inside
    the pointer's recorded scope."""
    return (isinstance(entry, dict) and bool(entry.get("pass")) and bool(entry.get("sha256"))
            and len(raw) <= prepare_bulk.CEILING_BYTES
            and not has_secret(text) and refresh_would_admit(path, ptr))


def word_search(question: str, pointers: list, limit: int = FALLBACK_FILES, skip=()) -> list:
    """Local, no provider calls: [(score, path, pointer)] of the principal's reviewed
    files best matching the question's words (BM25 per CONFIRM_CHUNK passage, a file
    scores its best passage; a file's path, description and stored question count triple
    in every passage). Typos match a close word (difflib). A file must
    cover FALLBACK_MIN_COVERAGE of the question's weighted words to be offered. Test/scratch
    output is never searched; `skip` paths (already routed) are dropped before the top `limit`.
    A reviewed file edited since connect stays searchable at its current text while its
    pointer waits on the refresh (routing cannot see a stale pointer), if that text passes
    the same secret scan and size ceiling connect applies; it is listed in the trace."""
    terms = query_terms(question)
    if not terms:
        return []
    docs, changed = {}, []
    # Adjacent question words, in question order (before de-duplication); a passage keeps
    # only the pairs it shares with these, so the pair pass costs little memory.
    qwords = [w for w in words(question.replace("'", "").replace("\u2019", ""))
              if len(w) > 2 and w not in QUERY_STOPWORDS]
    qpairs = list(dict.fromkeys((a, b) for a, b in zip(qwords, qwords[1:])
                                if a != b and a in terms and b in terms))  # "step by step" is no phrase
    qkeys = {(a[:4], b[:4]) for a, b in qpairs}
    for ptr in pointers:
        names = connector_names(ptr)
        for path, entry in load_cache_files(ptr).items():
            if (path in docs or not isinstance(entry, dict) or not entry.get("pass")
                    or prepare_bulk.is_test_material(path, prepare_bulk.named_exactly(Path(path).name, names))):
                continue
            try:
                raw = Path(path).read_bytes()
            except OSError:
                continue
            text = raw.decode("utf-8", "replace")
            if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
                # Edited since connect: the refresh (auto-heal) re-gates it soon. Until then
                # search its current text, held back only as a refresh would hold it.
                if not edited_readable(path, ptr, entry, raw, text):
                    continue  # never reviewed at a known version, or a refresh would hold it
                changed.append(path)
            heading = next((ln.lstrip("# ") for ln in text.splitlines() if ln.startswith("#")), "")
            head = " ".join([path.replace("/", " ").replace("-", " ").replace("_", " "), heading,
                             str(entry.get("description") or ""), str(entry.get("question") or "")])
            head_words = Counter(w for w in words(head) for _ in range(3))
            chunks = [text[i:i + CONFIRM_CHUNK] for i in range(0, len(text), CONFIRM_CHUNK)] or [""]
            passages = [passage_words(c) + head_words for c in chunks]
            docs[path] = (ptr, sum(passages, Counter()),
                          [(c, sum(c.values()), word_pairs(t) & qkeys if qkeys else set())
                           for c, t in zip(passages, chunks)])
    if not docs:
        return []
    _STAGE["word_changed"] = changed[:STAGE_LIST_CAP]
    vocab = sorted(set().union(*(c.keys() for _, c, _ in docs.values())))
    variants = {}
    for t in terms:
        near = set(difflib.get_close_matches(t, vocab, n=3, cutoff=0.8)) if len(t) > 3 else set()
        stem = t[:5] if len(t) > 5 else t  # long words by stem, short ones plus an ending (owe/owed)
        variants[t] = near | {v for v in vocab if v.startswith(stem) and len(v) - len(t) <= (99 if len(t) > 5 else 2)}
        # A synonym counts as a match for its source word, not as an extra word.
        variants[t] |= {x for x in SYNONYMS.get(t, []) if x in vocab}
    def tf_of(c):
        return {t: sum(c.get(v, 0) for v in variants[t]) for t in terms}
    tf = {path: tf_of(c) for path, (_, c, _) in docs.items()}
    n = len(docs)
    sizes = [size for _, _, parts in docs.values() for _, size, _ in parts]
    avg = sum(sizes) / len(sizes) or 1
    idf = {t: math.log(1 + (n - df + 0.5) / (df + 0.5))
           for t, df in ((t, sum(1 for f in tf.values() if f[t])) for t in terms)}
    # Words no reviewed file contains (e.g. "time") cannot tell files apart; leave
    # them out of the coverage total so they do not sink every file.
    total = sum(idf[t] for t in terms if any(f[t] for f in tf.values())) or 1
    scored = []
    for path, (ptr, _, parts) in docs.items():
        f = tf[path]
        if sum(idf[t] for t in terms if f[t]) / total < FALLBACK_MIN_COVERAGE:
            continue
        # Scored per passage, best passage wins: whole-file BM25 sank a long file
        # (36 KB medical timeline) whose one passage held every question word.
        # Two question words side by side in a passage (a phrase: "read the source",
        # "parent folder") add their weight once more: scattered matches tie often, and a
        # file stating the phrase was left one slot past the read list.
        bm25 = max(sum(idf[t] * pf[t] * 2.2 / (pf[t] + 1.2 * (0.25 + 0.75 * size / avg)) for t in terms)
                   + PHRASE_WEIGHT * sum((idf[a] + idf[b]) / 2 for a, b in qpairs if (a[:4], b[:4]) in pairs)
                   for pf, size, pairs in ((tf_of(c), size, pairs) for c, size, pairs in parts))
        scored.append((round(bm25, 3), path, ptr))
    ranked = sorted(scored, key=lambda x: (-x[0], x[1]))
    _STAGE["word"] = {"terms": terms, "files_searched": len(docs), "passed_coverage": len(scored),
                      "ranked": ranked[:STAGE_LIST_CAP],
                      "cover": {p: sum(idf[t] for t in terms if f[t]) / total for p, f in tf.items()}}
    # A file under 0.55x the best eligible file's score is a weak match: not worth a read slot.
    # Measured against the best file still eligible, i.e. after routed files are skipped.
    rest = [r for r in ranked if r[1] not in skip]
    floor = rest[0][0] * FALLBACK_REL_FLOOR if rest else 0
    return [r for r in rest if r[0] >= floor][:limit]

def confirm(question: str, paths: list):
    """Check each path on its own, in one batched run. Returns ({path: score} for kept files,
    set of paths too long to read whole, first error or None, {path: note})."""
    paths = paths[:CONFIRM_FILES + FALLBACK_FILES]
    if not batch_jev():
        results = list(ThreadPoolExecutor(max_workers=max(1, len(paths))).map(lambda p: confirm_one(question, p), paths))
        return confirm_results(paths, results)
    # Every file's check rides in one navigation-cli run: their Jev questions share
    # calls, split under the input ceiling, and each is still judged on its own.
    started = [confirm_start(question, p) for p in paths]
    ctxs = [ctx for _, ctx in started if ctx]
    rows, error = run_navigation({"batch": [c["payload"] for c in ctxs]}) if ctxs else (None, None)
    rows = rows.get("results") if isinstance(rows, dict) else None
    if ctxs and (error or not isinstance(rows, list) or len(rows) != len(ctxs)):
        # The batched run itself failed: check each file on its own instead of
        # marking every file inconclusive.
        results = list(ThreadPoolExecutor(max_workers=max(1, len(paths))).map(lambda p: confirm_one(question, p), paths))
        return confirm_results(paths, results)
    outs = iter(rows or [])
    results = [done or confirm_finish(question, ctx, next(outs), None) for done, ctx in started]
    # The run's one shared call has one fixed timeout (SUPERJEV_NAV_TIMEOUT_MS) however
    # many files ride in it, so a busy moment can time out every file at once. A file
    # that timed out in a shared run is checked once more on its own, a small call, at most
    # NAV_CONCURRENCY at once; the same payload is judged the same way, and one that fails
    # again stays inconclusive.
    if len(ctxs) > 1:
        again = [i for i, (_, _, e, _) in enumerate(results) if e == NAV_TIMED_OUT]
        for i, r in zip(again, ThreadPoolExecutor(max_workers=max(1, min(len(again), NAV_CONCURRENCY))).map(
                lambda i: confirm_one(question, paths[i]), again)):
            results[i] = r
        if again:
            _STAGE["timeout_rechecks"] = len(again)
    return confirm_results(paths, results)

def confirm_results(paths: list, results: list):
    errors = [e for _, _, e, _ in results if e]
    scores = {p: sc for p, (sc, _, _, _) in zip(paths, results) if sc is not None}
    partial = {p for p, (_, part, _, _) in zip(paths, results) if part}
    # A file whose check errored was not read: it stays INCONCLUSIVE on its routing
    # score, and the other files are still filtered on their own results.
    notes = {p: note or INCONCLUSIVE for p, (_, _, e, note) in zip(paths, results) if note or e}
    return scores, partial, (errors[0] if errors else None), notes

def best_line(question: str, chunks: list, i: int) -> int:
    """The file line (1-based) in passage i sharing the most question words, earliest on a tie:
    a 3,500-character passage often starts in the section before the one it is about."""
    first = 1 + sum(c.count("\n") for c in chunks[:i])
    terms = query_terms(question)
    rows = chunks[i].split("\n")
    hits = [term_hits(terms, row) for row in rows]
    return first + max(range(len(rows)), key=lambda k: (hits[k], -k)) if rows else first

def section_shown(path: str, ptr: str):
    """For a file connected in sections: (first, last) of the section to open, the one holding
    the passage the content check scored best, else the best-routed one; else None."""
    routed = sorted((_STAGE.get("section_routes") or {}).get(path) or [], reverse=True)
    secs = []
    for name in dict.fromkeys((ptr, re.sub(r"-\d+$", "", ptr))):
        entry = load_cache_files(name).get(path)
        if isinstance(entry, dict) and entry.get("sections"):
            # Where to read in the file: any section, connected or not (the check read the file itself).
            secs = [tuple(x["lines"]) for x in entry["sections"] if x.get("lines")]
            break
    line = ((_STAGE.get("checks") or {}).get(path) or {}).get("best_line")
    hit = next((x for x in secs if line and x[0] <= line <= x[1]), None)
    return hit or (routed[0][1] if routed else None)

def refresh_hint(ptr: str, principal: str, kind: str) -> str:
    """A stale pointer's files changed since connect; say the exact command that re-prepares it."""
    if not kind.startswith(("preparation-required", "refresh-required")):
        return ""
    if "-manual-" in ptr:
        return "; its source changed: re-add it with ask.py --add ... --replace-entry"
    # The printed command must run as-is from any folder: the absolute script path plus the
    # pointer's recorded roots/excludes and every principal it serves (a bare --refresh with
    # no report is refused for want of --root; one principal short is refused as a scope change).
    script = skill_dir_for_display() / "prepare_bulk.py"  # stable across releases
    # A split part (<pointer>-N) has no report of its own: it refreshes through its parent's.
    report = auto_heal._report_for(ptr, prepare_bulk.CACHE_DIR)[0]
    args = refresh_changed.prepare_args(report) if isinstance(report, dict) else None
    if args:
        return f"; its files changed since connect. Run: {shlex.join(['python3', str(script), *args])}"
    # Not built by prepare_bulk: the ask already replayed its recorded connect recipe
    # (auto_heal.reconnect_recipe), or it has none and the prepare_bulk command below
    # would not rebuild it. One reconnect through the connector records a recipe.
    try:
        recipe = memory({"action": "recipe", "pointer": ptr, "principal": principal}).get("status")
    except Exception:
        recipe = None
    if recipe == "ok":
        return (f"; its files changed since connect and replaying its connect recipe failed "
                f"(see {auto_heal.LOG_PATH})")
    if recipe == "no-recipe":
        return ("; its files changed since connect and it was not built by prepare_bulk, with no "
                "recorded connect recipe. Reconnect it once through the connector "
                "(references/connectors.md); later changes then heal on their own")
    if not (prepare_bulk.CACHE_DIR / f"{ptr}.json").is_file():
        # Neither a prepare_bulk cache nor a readable recipe: a prepare_bulk command
        # could not rebuild it, so do not print one.
        return ("; its files changed since connect. Reconnect it through the connector it was "
                "built with (references/connectors.md)")
    return (f"; its files changed since connect and it has no recorded recipe. Run: python3 {script} "
            f"--root DIR --pointer {ptr} --principal {principal} --refresh "
            "(one --root per connected folder, one --principal per agent it serves)")

# A stale pointer that cannot refresh itself (no prepare_bulk report, no connect recipe,
# or a replay that failed) waits on a human. Warn about it once per day per principal,
# not on every answer; the other sightings go to lookups.jsonl only.
STALE_WARNED_FILE = "stale-warned.json"

def warn_stale_today(sdir: Path, ptr: str) -> bool:
    """True the first time today this pointer is seen stuck stale (and records it)."""
    path, today = sdir / STALE_WARNED_FILE, time.strftime("%Y-%m-%d")
    try:
        seen = json.loads(path.read_text())
    except (OSError, ValueError):
        seen = {}
    if not isinstance(seen, dict):
        seen = {}
    if seen.get(ptr) == today:
        return False
    seen[ptr] = today
    try:
        sdir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(seen))
    except OSError:
        pass
    return True

# "Which skill/tool/command ..." asks a skill catalog question: the answer is a SKILL.md
# in the trusted skill roots, which are not connected files. The skills connector
# (dispatch.py skills) searches them for every principal; its picks print first.
SKILL_Q_RE = re.compile(r"\b(skills?|slash commands?)\b|\b(which|what|any)\b.{0,30}\b(tools?|commands?)\b", re.I)
SKILL_NOTE = "  (skill catalog match: read the SKILL.md before using it)"

def skill_question(question: str) -> bool:
    return os.environ.get("SUPERJEV_SKILLS", "1") != "0" and bool(SKILL_Q_RE.search(question))

def skill_catalog(question: str) -> list:
    """[(name, SKILL.md path)] from the skills connector; [] on any failure."""
    try:
        r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "dispatch.py"),
                            "skills", "--request", question], capture_output=True, text=True, timeout=60)
        out = json.loads(r.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    return [(c.get("name") or c.get("id") or "", c["path"]) for c in out.get("candidates") or []
            if isinstance(c, dict) and isinstance(c.get("path"), str)] if isinstance(out, dict) else []

def path_rank(question: str, path: str) -> tuple:
    """Tie-break for equal scores: more question words in the file's name or folder
    first, and a brain-local file before its ~/agents/global/reuse copy."""
    parts = Path(path).parts
    name = " ".join(parts[-2:]).replace("-", " ").replace("_", " ")
    return (term_hits(query_terms(question), name), "/global/reuse/" not in path)

# Navigation hubs: a table of contents, not a topic file. Its own content/routing
# score often ties or beats a real note that answers the question, so it is moved
# into the possible group (see lookup()) and sorts by content there like any other
# possible file -- the real note that passed the content check outranks the index
# that merely points to it.
HUB_STEMS = {"index", "catalog", "tools-used", "handoff", "readme"}

def is_hub_file(path: str) -> bool:
    stem = Path(path).stem.lower()
    if stem in HUB_STEMS:
        return True
    # A file whose name is a template (e.g. "_TEMPLATE-report", "campaign-template")
    # is a shape to copy, not a topic file; treat it like a navigation hub.
    return "template" in stem

# Summaries and copies of a real note. A "hub" (README/INDEX/PROFILE/...) points
# at the notes in its folder; a "copy" (_staging/ split, _TEMPLATE) repeats one;
# a "writeup" (PR-history note) retells a change. Each can pass the content check
# above the note itself (pending/README 0.99 over the stamps.com note beside it;
# pr-19's hook survey over docs/wire-into-claude-code.md). Claim-mode
# ranking uses these categories; ordinary retrieval ranks evidence alone.
WRITEUP_DIRS = {"superjev-pr-history", "pr-history"}

def copy_kind(path: str):
    parts = [x.lower() for x in Path(path).parts[:-1]]
    stem = Path(path).stem.lower()
    if stem in HUB_STEMS or stem == "profile":
        return "hub"
    if "template" in stem or any(x == "_staging" or "template" in x for x in parts):
        return "copy"
    if any(x in WRITEUP_DIRS for x in parts):
        return "writeup"
    return None

# A folder documents/<name>/ holds one person's records. Its PROFILE's
# "Relation:" line (father / mother / self ...) lets "my dad" find that folder.
PERSON_RE = re.compile(r"/agents/global/documents/([a-z][a-z0-9_-]*)/", re.I)
RELATIONS = {"dad": "father", "father": "father", "mom": "mother", "mum": "mother", "mother": "mother",
             "wife": "wife", "husband": "husband", "daughter": "daughter", "son": "son",
             "sister": "sister", "brother": "brother", "self": "self",
             # No folder claims these today; naming one still means "not me", so nothing is filtered.
             **{w: w for w in ("grandma", "grandmother", "grandpa", "grandfather", "aunt", "uncle", "cousin",
                               "niece", "nephew", "partner", "girlfriend", "boyfriend", "friend", "baby")}}
FIRST_PERSON = {"i", "me", "my", "mine", "myself"}
# Words naming a group ("my parents", "our") could mean several folders: when
# unsure, filter nothing.
GROUP_WORDS = {"parents", "kids", "children", "family", "our", "we", "us", "everyone", "both", "grandparents"}

def person_of(path: str):
    m = PERSON_RE.search(path)
    return m.group(1).lower() if m else None

def people(pointers: list) -> dict:
    """{person folder name: relation words from its PROFILE's Relation line}."""
    out = {}
    for ptr in pointers:
        for path in load_cache_files(ptr):
            who = person_of(path)
            if not who:
                continue
            rel = out.setdefault(who, set())
            if Path(path).stem.lower() == "profile":
                try:
                    lines = Path(path).read_text(errors="replace").lower().splitlines()
                except OSError:
                    continue
                for ln in lines:
                    if "relation" in ln:
                        rel |= {RELATIONS[w] for w in re.findall(r"[a-z]+", ln) if w in RELATIONS}
                        break
    return out

def question_people(question: str, folks: dict) -> set:
    """Whose records the question is about: a relation word ("my dad") wins,
    and person folder names ("milbeny's") add their folders; "I"/"me" adds the
    "self" folder too ("my wife and I"), and I/me/my alone means just "self".
    A group word, or a relation no folder claims, filters nothing.
    Empty set = no one resolved, nothing is filtered."""
    words = {w.removesuffix("'s").strip("'") for w in re.findall(r"(?:[^\W\d_]|')+", fold(question))}
    if words & GROUP_WORDS:
        return set()
    rel = {RELATIONS[w] for w in words if w in RELATIONS and w != "self"}
    by_rel = {n for n, r in folks.items() if r & rel}
    if rel and not by_rel:
        return set()
    who = by_rel | {n for n in folks if fold(n) in words}
    self_ = {n for n, r in folks.items() if "self" in r}
    if not who:
        return self_ if words & FIRST_PERSON else set()
    return who | self_ if words & {"i", "me", "myself"} else who

def lookup(question: str, principal: str, sdir: Path) -> int:
    # Relative route mass ranks candidates; it is not ordinary evidence confidence.
    route_floor = ROUTE_FLOOR if _CLAIM["text"] else 0
    if len(question) > MAX_QUESTION:
        print(f"question too long ({len(question):,} chars, max {MAX_QUESTION:,}); ask a shorter question")
        return 2
    t0 = time.time()
    lookup_id = new_lookup_id(principal, question, t0)
    _STAGE.clear()
    # SUPERJEV_REPLAY=1 (paid_replay.py): answer live; never read a saved answer or claim verdict.
    replay = os.environ.get("SUPERJEV_REPLAY") == "1"
    panel = None
    if _CLAIM["text"]:
        panel = memory({"action": "panel", "principal": principal})
        view_originals = {os.path.realpath(path) for row in panel.get("pointers", []) if isinstance(row, dict)
                          for path in row.get("viewOriginals", [])}
        saved = None if replay else claim_cache_get(sdir, _CLAIM["text"])
        if saved and os.path.realpath(saved.get("path", "")) in view_originals:
            saved = None  # A verdict from a former raw source is not a view approval.
        if saved:
            q = f'line {saved["line_no"]}: "{saved["line"]}"' if saved.get("line_no") else (
                f'"{saved["line"]}"' if saved.get("line") else "")
            print(f"{saved['verdict']} ({saved.get('prob') or 0:.2f}, saved; its proof file is unchanged)")
            print(f"Proof: {saved['path']}")
            if q:
                print(f"  {q}")
            log(sdir, "claim", question=question, result="saved", verdict=saved["verdict"])
            return 0
        cache = {"status": "skipped (claim)"}  # saved answers answer questions, not statements
    elif replay:
        cache = {"status": "skipped (replay)"}
    else:
        cache = memory({"action": "cached", "principal": principal, "question": question})
    cache_stage = {"result": cache.get("status"), "checked": len(cache.get("checked") or [])}
    withheld = None
    if cache.get("status") == "verified-cache-hit":
        rc = print_hit(cache, sdir, principal, question)
        log(sdir, "lookup", question=question, result="cache-hit" if rc == 0 else "stale-source", secs=round(time.time() - t0, 1))
        write_trace(sdir, kind="trace", lookup_id=lookup_id, question=question, routing={},
                    content_check={}, final_ranked=[], tier="cache" if rc == 0 else "stale",
                    timings={"total_secs": round(time.time() - t0, 2)},
                    stages={"cache": {"result": "hit" if rc == 0 else "stale"}})
        if rc == 0:
            return rc
        # The stale answer stays withheld, but the question still gets a fresh live search.
        # Its manual pointer's record text IS the stale answer, so keep it out of the live search.
        withheld = manual_pointer_name(principal, question)
        print("Searching live instead...")
    panel = panel if panel is not None else memory({"action": "panel", "principal": principal})
    view_pointers = {row["pointer"] for row in panel.get("pointers", [])
                     if isinstance(row, dict) and row.get("viewOriginals")}
    _STAGE["view_pointers"] = sorted(view_pointers)
    if panel.get("reason") == "not-set-up":
        print(f"Super Jev is not set up yet. Run: python3 {skill_dir_for_display() / 'setup.py'}")
        return 1
    pointers = [n for n in ((p.get("pointer") if isinstance(p, dict) else p)
                            for p in panel.get("pointers", [])) if n and n != withheld]
    if not pointers:
        # Runnable from any folder (the skill folder as invoked), and a new agent in a fleet
        # learns the shared sets it can join at once, with no connect.
        here = skill_dir_for_display()
        print(f"nothing connected yet for principal '{principal}' -- run connect first:\n"
              f"  python3 {here / 'prepare_bulk.py'} --root /path/to/folder "
              f"--pointer my-notes --principal {principal}")
        try:
            import share_pointers
            shared = share_pointers.load_shared()
        except Exception:
            shared = []
        if shared:
            print(f"or join the fleet's {len(shared)} shared set(s) now ({', '.join(shared[:5])}):\n"
                  f"  python3 {here / 'share_pointers.py'} --principal {principal} --shared")
        log(sdir, "lookup", question=question, pointers=0, result="nothing-connected",
            secs=round(time.time() - t0, 1))
        write_trace(sdir, kind="trace", lookup_id=lookup_id, question=question, routing={},
                    content_check={}, final_ranked=[], tier="none",
                    timings={"total_secs": round(time.time() - t0, 2)}, errors=["nothing-connected"],
                    stages={"cache": cache_stage})
        print(VOICE_LINE)
        return 1

    # Sick-pointer circuit breaker: skip pointers already benched from repeated
    # recent failures instead of waiting on them (and re-erroring) again this call.
    health = load_pointer_health(sdir)
    original_pointers = pointers
    # Runs beside routing; its picks print first (see SKILL_Q_RE).
    skill_job = (ThreadPoolExecutor(max_workers=1).submit(skill_catalog, question)
                 if skill_question(question) else None)
    active_pointers, error_lines = [], []
    for ptr in original_pointers:
        benched, remaining, fails = pointer_benched(health, ptr, principal)
        if benched:
            error_lines.append(f"[{ptr}] benched ({fails} consecutive failures, cooling off {int(remaining)}s more)")
        else:
            active_pointers.append(ptr)
    pointers = active_pointers
    # Person resolution: a question about one person never reads (or confirms)
    # another person's records, and a pointer holding only theirs is not asked.
    folks = people(pointers)
    who = question_people(question, folks)
    def other_person(path: str) -> bool:
        return bool(who) and person_of(path) not in (None, *who)
    _STAGE["person"] = {"who": sorted(who), "dropped": []}
    if who:
        _STAGE["person"]["skipped_pointers"] = [ptr for ptr in pointers if (files := load_cache_files(ptr))
                                                and all(other_person(p) for p in files)]
        pointers = [ptr for ptr in pointers
                    if not (files := load_cache_files(ptr)) or not all(other_person(p) for p in files)]

    # Word prefilter: skip routing for pointers that hold none of the question's words.
    terms = query_terms(question)
    generations = {p.get("pointer"): p.get("generation") for p in panel.get("pointers", []) if isinstance(p, dict)}
    generations = {p: generations.get(p) for p in pointers}
    vocab, unknown = pointer_words(sdir, generations)
    # Only a question with 2+ words (synonyms counted) all missing skips a pointer,
    # and never one holding a resolved person's folder ("my mom" -> iris).
    wide = [[t, *PREFILTER_SYNONYMS.get(t, [])] for t in terms]
    def no_words(p):
        v = f" {vocab[p]} "
        return (not any(f" {name} " in v for name in who)
                and not any(term_hits(alts, vocab[p]) for alts in wide))
    _STAGE["prefilter"] = [p for p in pointers if len(terms) >= 2 and p in vocab and no_words(p)]
    # Word search still sees every pointer: the prefilter only saves routing calls.
    search_pointers = pointers
    pointers = [p for p in pointers if p not in _STAGE["prefilter"]]
    learner = threading.Thread(target=save_pointer_words, args=(sdir, principal, generations, unknown),
                               daemon=True) if unknown else None
    if learner:
        learner.start()

    nav_none = {}
    # A stale set is routed on the catalog of its last refresh (memory navigate lastGood), so
    # a refresh cooldown never hides the whole set: {pointer: {"changed": [...], "missing": [...]}}.
    stale_served = {}

    def classify(ptr, out, elapsed):
        status, reason = out.get("status"), out.get("reason", "")
        nav_none[ptr] = (_root_none(out), round(elapsed, 2))
        if isinstance(out.get("stale"), dict) and status in ("candidates", "no-candidates"):
            stale_served[ptr] = out["stale"]
        else:
            stale_served.pop(ptr, None)
        if status == "candidates" and out.get("candidates"):
            rows = out["candidates"]
            if not isinstance(rows, list) or any(
                    not isinstance(c, dict) or type(c.get("score")) not in (int, float)
                    or not 0 <= c["score"] <= 1
                    or not isinstance(c.get("originalPath"), str) or not c["originalPath"]
                    for c in rows):
                return ptr, "error: invalid routing candidate", [], elapsed, False
            return ptr, "candidates", out["candidates"], elapsed, True
        if status in ("candidates", "no-candidates"):
            return ptr, "no-candidates", [], elapsed, True
        kind = status or "error"
        return ptr, f"{kind}: {reason}" if reason else kind, [], elapsed, False

    def failed_overloaded(out):
        return (out.get("status") not in ("candidates", "no-candidates")
                and _is_overloaded(f"{out.get('status')} {out.get('reason', '')}"))

    # Match the five-source result bound on every routing path. Claim checks
    # retain their existing navigation defaults.
    routing_limits = {} if _CLAIM["text"] else {"mode": "source-discovery", "limits": {"beamWidth": 5, "maxResults": 5}}

    def nav(ptr):
        t_start = time.time()
        out = memory({"action": "navigate", "pointer": ptr, "principal": principal, "question": question,
                      "lastGood": True, **routing_limits})
        # One backoff retry for an overloaded provider (HTTP 529) -- not counted
        # as a failure unless the retry also fails.
        if failed_overloaded(out):
            time.sleep(OVERLOAD_BACKOFF_SECS)
            out = memory({"action": "navigate", "pointer": ptr, "principal": principal, "question": question,
                          "lastGood": True, **routing_limits})
        return classify(ptr, out, time.time() - t_start)

    def nav_many(ptrs):
        """{pointer: navigate result}: one navigation-cli run whose Jev questions for
        every pointer share as few calls as fit under Jev's input ceiling."""
        out = memory({"action": "navigate-many", "pointers": ptrs, "principal": principal, "question": question,
                      "lastGood": True, **routing_limits})
        rows = out.get("results") if out.get("status") == "ok" else None
        if not isinstance(rows, dict):  # an older runtime, or the batch itself failed
            return None
        return {ptr: rows.get(ptr) if isinstance(rows.get(ptr), dict) else {"status": "error"} for ptr in ptrs}

    # Routing asks Jev about every pointer at once: their questions ride in shared
    # calls (split under the input ceiling) instead of one navigate call per pointer.
    t_start = time.time()
    outs = nav_many(pointers) if pointers and batch_jev() else None
    if outs is None:  # one navigate call per pointer, as before batching
        results = list(ThreadPoolExecutor(max_workers=min(len(pointers), NAV_CONCURRENCY)).map(nav, pointers)) if pointers else []
    else:
        retry = [ptr for ptr in pointers if failed_overloaded(outs[ptr])]
        if retry:  # one backoff retry for the pointers an overloaded provider (HTTP 529) failed
            time.sleep(OVERLOAD_BACKOFF_SECS)
            outs.update(nav_many(retry) or {ptr: memory({"action": "navigate", "pointer": ptr, "principal": principal,
                                                         "question": question, "lastGood": True, **routing_limits}) for ptr in retry})
        results = [classify(ptr, outs[ptr], time.time() - t_start) for ptr in pointers]
    # A stale pointer whose files are all already reviewed at their current bytes needs no
    # redraft, only a reconnect: do it now and ask it again, so this lookup reads it.
    # One RECONNECT_TIMEOUT_SECS budget covers every reconnect in this lookup. A replay
    # never reconnects or heals: that changes connector state and starts paid work.
    reconnected, deadline = {}, time.time() + auto_heal.RECONNECT_TIMEOUT_SECS
    for i, (ptr, kind, *_rest) in enumerate(results):
        left = int(deadline - time.time())
        if (auto_heal.is_stale_kind(kind) or ptr in stale_served) and left >= 1 and not replay:
            reconnected[ptr] = ("no-report" if ptr in view_pointers else
                                auto_heal.reconnect_now(ptr, principal, timeout=left))
            if reconnected[ptr] == "no-report":
                # Not built by prepare_bulk: replay the connect recipe recorded at connect time.
                reconnected[ptr] = auto_heal.reconnect_recipe_or_queue(ptr, principal, memory=memory)
            if reconnected[ptr] == "reconnected":
                results[i] = nav(ptr)
        elif kind == "pointer-changed":
            # A refresh re-registered the set while Jev routed it (a stale set is routed now,
            # so a background refresh can land mid-call): ask it once more at its new generation.
            results[i] = nav(ptr)
    _STAGE["reconnect"] = reconnected
    # A note written into a connected folder after its connect is not in the pointer's file list,
    # so nothing marks the pointer stale: look for such files now and then (background, bounded)
    # and refresh their pointers, so a later lookup finds them without a hand reconnect.
    if not replay and search_pointers and os.environ.get("SUPERJEV_NEW_FILE_SCAN", "1") != "0":
        _STAGE["new_file_scan"] = auto_heal.maybe_scan(principal, search_pointers)
    merged, errored, statuses, stale_held = [], 0, {}, []
    _STAGE["stale_changed"], _STAGE["section_routes"] = [], {}
    for ptr, kind, rows, elapsed, ok in results:
        served = stale_served.get(ptr)
        record_pointer_outcome(health, ptr, ok, elapsed, stale=_is_stale_kind(kind) or bool(served))
        if served:
            # A file edited since the last refresh is read at its current text only as the
            # refresh would admit it (edited_readable), exactly like word search's edited files.
            edited, files = set(served.get("changed") or []), load_cache_files(ptr)
            kept = []
            for c in rows:
                path = c.get("originalPath", "")
                if path in edited:
                    try:
                        raw = Path(path).read_bytes()
                    except OSError:
                        continue
                    if not edited_readable(path, ptr, files.get(path), raw, raw.decode("utf-8", "replace")):
                        stale_held.append(path)
                        continue
                    _STAGE["stale_changed"].append(path)
                kept.append(c)
            rows = kept
        if kind == "candidates" and rows:
            statuses[ptr] = "candidates"
            merged += [(c.get("score", 0), c.get("originalPath", ""), ptr) for c in rows]
            # A big file connected in sections routes by section: its check reads the routed
            # sections' passages first, and the result names the section.
            for c in rows:
                if isinstance(c.get("lines"), list) and c.get("score", 0) >= route_floor:
                    _STAGE.setdefault("section_routes", {}).setdefault(c.get("originalPath", ""), []).append(
                        (c.get("score", 0), tuple(c["lines"])))
        elif kind in ("candidates", "no-candidates"):
            statuses[ptr] = "no-candidates"
        else:
            errored += 1
            statuses[ptr] = kind
        if served:
            statuses[ptr] += " (stale: last refresh)"
        if served or kind not in ("candidates", "no-candidates"):
            stale_kind = served.get("status", "preparation-required") if served else kind
            hint = ("; refresh through the recorded view recipe; raw bulk refresh is disabled"
                    if ptr in view_pointers else refresh_hint(ptr, principal, stale_kind))
            # A stale pointer never has to wait on a human to run the refresh hint above by
            # hand: this starts the exact same prepare_bulk.py --refresh in the background,
            # bounded (one in flight per principal, the rest queued behind it, per-pointer
            # cooldown, hourly cap -- see auto_heal.py), and returns immediately either way. It never blocks this lookup
            # and never changes what this lookup reports for the pointer that triggered it;
            # it only means the *next* lookup may no longer hit it.
            heal_note = ""
            stale = bool(served) or auto_heal.is_stale_kind(kind)
            result = None
            if stale and not replay:
                result = (reconnected.get(ptr, "no-recipe") if ptr in view_pointers else
                          auto_heal.maybe_heal(ptr, principal))
                if result == "started":
                    heal_note = " (auto-heal: refresh started in background)"
                elif result == "in-progress":
                    heal_note = " (auto-heal: another refresh in progress; queued to run after it)"
                elif result == "cooldown":
                    err = auto_heal.last_refresh_error(principal, ptr)
                    heal_note = (f" (auto-heal: last refresh FAILED: {err}; retrying after cooldown)" if err
                                 else " (auto-heal: refreshed recently, cooling down)")
                elif result == "rate-limited":
                    heal_note = " (auto-heal: hourly refresh limit reached)"
            # [STALE] marks a pointer that is just waiting on its own refresh (not
            # a live error, never benched) so it reads differently at a glance from
            # a real provider error -- appended after the existing kind/hint/heal
            # text so it never changes what those already say.
            if stale and result == "no-report" and reconnected.get(ptr) != "reconnected" \
                    and not warn_stale_today(sdir, ptr):
                if not served:
                    errored -= 1
                log(sdir, "stale-quiet", pointer=ptr, status=stale_kind)
                continue
            if served:
                # Not an error: this set answered from its last refresh. Say so, and how much is newer.
                gone = len(served.get("missing") or [])
                error_lines.append(f"[{ptr}] stale: searched as of its last refresh "
                                   f"({len(served.get('changed') or [])} file(s) changed since"
                                   + (f", {gone} removed" if gone else "") + ")"
                                   + hint + heal_note + " [STALE]")
                continue
            error_lines.append(f"[{ptr}] {kind}" + hint + heal_note + (" [STALE]" if stale else ""))
    save_pointer_health(sdir, health)
    if learner:
        learner.join()
    _STAGE["person"]["dropped"] = [m[1] for m in merged if other_person(m[1])][:STAGE_LIST_CAP]
    # A reviewed dataset's pointer serves its prepared copies under .local/retrieval-datasets/;
    # those copies ARE its answers. Folder inventory already keeps the copies out of every
    # other pointer (prepare_bulk.is_bench_dataset), so a path that reaches here came from its
    # own registered pointer and must not be dropped by where it lives.
    names_of = {ptr: connector_names(ptr) for ptr in {m[2] for m in merged}}
    merged = sorted((m for m in merged if m[0] >= route_floor
                     and not prepare_bulk.is_test_material(
                         m[1], prepare_bulk.named_exactly(Path(m[1]).name, names_of[m[2]]))
                     and not other_person(m[1])), reverse=True)
    routed = list(dict.fromkeys(p for _, p, _ in merged))
    route = {}
    for s, p, _ in merged:
        route.setdefault(p, s)
    dropped, check_error, notes, possible = 0, None, {}, {}
    # Ordinary source lookup checks only routed candidates. Lexical topic overlap
    # must not reintroduce files that semantic routing rejected. Claim checking
    # retains its broader evidence discovery for support and contradiction.
    found = (word_search(question, search_pointers, skip=set(routed[:CONFIRM_FILES])
                        | {p for ptr in search_pointers for p in load_cache_files(ptr) if other_person(p)})
             if _CLAIM["text"] else [])
    wpaths = {p: ptr for _, p, ptr in found}
    to_check = routed[:CONFIRM_FILES] + list(wpaths)
    checked = set(to_check)
    if to_check:
        scores, partial, check_error, notes = confirm(question, to_check)
        if check_error:
            errored += 1
            error_lines.append(f"[content-check] error: {check_error}")
        # One source-evidence floor for all questions. Routing discovers candidates;
        # it cannot rescue a file the content check rejected. Unfinished checks
        # remain explicitly inconclusive rather than being reported as absence.
        cands = merged + [(0, p, ptr) for p, ptr in wpaths.items()]
        keep = {}
        for routed_score, p, ptr in cands:
            if p in keep or p not in checked or notes.get(p) == HELD_SECRET:
                continue
            content = scores.get(p)
            if type(content) in (int, float) and SOURCE_FLOOR <= content <= 1:
                keep[p] = (content, p, ptr)
            elif notes.get(p) == INCONCLUSIVE:
                keep[p] = (routed_score, p, ptr)
                possible[p] = POSSIBLE_NOTE
        dropped = len(checked - set(keep) - {p for p, n in notes.items() if n == HELD_SECRET})
        # Completed evidence first, then unfinished checks. Routing only breaks
        # content-score ties; stable paths break the remaining ties.
        merged = sorted(keep.values(), key=lambda m: (
            notes.get(m[1]) != INCONCLUSIVE, m[0], route.get(m[1], 0), m[1]), reverse=True)
    if _CLAIM["text"] and to_check:
        # The answer filter is not a claim verdict. Let the bounded claim judge
        # inspect read evidence even when it does not affirm the statement.
        candidates = {p: (route.get(p, 0), p, ptr) for _s, p, ptr in cands}
        merged = [candidates[p] for p in dict.fromkeys(to_check)
                  if p in candidates and notes.get(p) not in (HELD_SECRET, INCONCLUSIVE)]
        merged.sort(key=lambda item: (scores.get(item[1], 0), route.get(item[1], 0)), reverse=True)
        possible.update({p: POSSIBLE_NOTE for _s, p, _ptr in merged})
    top = merged[:5]
    if _CLAIM["text"]:
        top = apply_near_twin_tiebreak(question, top)
    # Listwise choice step: see judge_listwise.
    listwise_winner = None
    if top and _CLAIM["text"]:
        pool = [p for _s, p, _ptr in top if notes.get(p) != HELD_SECRET]
        _STAGE["listwise"] = {"pool": pool[:LISTWISE_MAX_FILES], "ran": bool(pool), "reordered": False}
        listwise_winner, listwise_prob = judge_listwise(question, pool) if pool else (None, None)
        _STAGE["listwise"].update(winner=listwise_winner, winner_prob=listwise_prob, promoted=False)
    if listwise_winner == LISTWISE_NONE and not (isinstance(listwise_prob, (int, float))
                                                 and listwise_prob >= LISTWISE_PROMOTE_FLOOR):
        # A weak "none" keeps every file; the agent reads them and decides
        # (a weak "none" once dropped the right possible file).
        _STAGE["listwise"]["leans_none"] = True
    elif listwise_winner == LISTWISE_NONE:
        _STAGE["listwise"]["dropped"] = [p for _s, p, _ptr in top if p in possible]
        top = [m for m in top if m[1] not in possible]
        for _s, p, _ptr in top:  # a confirmed file stays only when the pick agrees
            possible[p] = POSSIBLE_NOTE
        if not top:
            dropped += len(_STAGE["listwise"]["dropped"])
    elif listwise_winner:
        winner_idx = next((i for i, (_s, p, _ptr) in enumerate(top) if p == listwise_winner), None)
        # A hub/copy/writeup winner never promotes past a non-hub file already
        # ranked ahead of it (a folder README once promoted itself over the confirmed
        # panel note beside it).
        blocked = winner_idx is None or ((is_hub_file(listwise_winner) or copy_kind(listwise_winner))
                                         and any(not (is_hub_file(p) or copy_kind(p))
                                                 for _s, p, _ptr in top[:winner_idx]))
        if not blocked:
            _STAGE["listwise"]["reordered"] = top[0][1] != listwise_winner
            top = sorted(top, key=lambda m: m[1] != listwise_winner)
            if isinstance(listwise_prob, (int, float)) and listwise_prob >= LISTWISE_PROMOTE_FLOOR \
                    and listwise_winner in possible and notes.get(listwise_winner) != INCONCLUSIVE:
                del possible[listwise_winner]
                s0, p0, ptr0 = top[0]
                top[0] = (max(s0, CONFIRM_FLOOR), p0, ptr0)
                _STAGE["listwise"]["promoted"] = True
        # Another confirmed file loses its label only to a strong, unblocked
        # pick; a weak or blocked pick never demotes a confirmed file (a
        # blocked pick once demoted the right confirmed file).
        strong = not blocked and isinstance(listwise_prob, (int, float)) \
            and listwise_prob >= LISTWISE_PROMOTE_FLOOR
        for _s, p, _ptr in top if strong else []:
            if p != listwise_winner and p not in possible and notes.get(p) != INCONCLUSIVE:
                possible[p] = POSSIBLE_NOTE
    if _CLAIM["text"]:
        # A listwise "none answers" cannot erase a file that disproves a claim.
        proven = {p: f for p, f in (_STAGE.get("claim_files") or {}).items()
                  if f.get("verdict") in ("supported", "contradicted")
                  and isinstance(f.get("prob"), (int, float)) and f["prob"] >= CLAIM_SURE}
        if proven:
            by_path = {p: (proven[p]["prob"], p, ptr) for _s, p, ptr in merged if p in proven}
            top = sorted(by_path.values(), reverse=True)[:5]
            for p in by_path:
                possible.pop(p, None)
    skills = skill_job.result() if skill_job else []
    _STAGE["skills"] = [path for _n, path in skills] if skill_job else None
    log(sdir, "lookup", question=question, pointers=len(pointers), statuses=statuses, secs=round(time.time() - t0, 1),
        top=[{"score": s, "path": p, "pointer": ptr, "possible": p in possible} for s, p, ptr in top])
    routing = {ptr: {"status": kind, "candidates": [{"path": c.get("originalPath", ""), "score": c.get("score", 0)} for c in rows]}
              for ptr, kind, rows, _elapsed, _ok in results}
    content_check = {p: {"score": scores.get(p) if type(scores.get(p)) in (int, float) and 0 <= scores[p] <= 1 else None,
                         "label": ("held-secret" if notes.get(p) == HELD_SECRET
                                   else "inconclusive" if notes.get(p) == INCONCLUSIVE
                                   else "possible" if p in possible
                                   else "evidence-selected" if type(scores.get(p)) in (int, float)
                                   and SOURCE_FLOOR <= scores[p] <= 1
                                   else "dropped")}
                     for p in checked}
    tier = "none" if not top else ("possible" if top[0][1] in possible else "confirmed" if _CLAIM["text"] else "sources")
    try:  # trace detail is best-effort; it must never fail the ask
        wsearch = _STAGE.get("word") or {}
        fates = {p: "read" for p in wpaths}
        stages = {
            "cache": cache_stage,
            "routing_floor": route_floor,
            "routing": {ptr: {"status": kind + (" (stale: last refresh)" if ptr in stale_served else ""),
                              "none": nav_none.get(ptr, (None,))[0],
                              "secs": nav_none.get(ptr, (None, None))[1],
                              "files": [{"path": c.get("originalPath", ""), "score": c.get("score", 0),
                                         "kept": c.get("score", 0) >= route_floor} for c in rows][:STAGE_LIST_CAP]}
                        for ptr, kind, rows, _elapsed, _ok in results},
            "benched": [ln for ln in error_lines if "] benched (" in ln][:STAGE_LIST_CAP],
            "stale_held": stale_held[:STAGE_LIST_CAP],
            "word_search": {"terms": wsearch.get("terms"), "files_searched": wsearch.get("files_searched"),
                            "changed_since_connect": sorted(set(_STAGE.get("word_changed") or [])
                                                            | set(_STAGE.get("stale_changed") or [])),
                            "passed_coverage": wsearch.get("passed_coverage"),
                            "top": [{"score": sc, "path": p,
                                     "fate": fates.get(p) or ("already routed" if p in routed[:CONFIRM_FILES]
                                                              else "not read: past top %d" % FALLBACK_FILES)}
                                    for sc, p, _ptr in wsearch.get("ranked", [])]},
            "read_list": to_check[:CONFIRM_FILES + FALLBACK_FILES],
            "cover_gate": _STAGE.get("cover_gate"),
            "timeout_rechecks": _STAGE.get("timeout_rechecks"),
            "content_check": {p: {**(_STAGE.get("checks") or {}).get(p, {}), "verdict": v["label"]}
                              for p, v in content_check.items()},
            "tiebreak": _STAGE.get("tiebreak") or {},
            "listwise": _STAGE.get("listwise") or {},
            "person": _STAGE.get("person") or {},
            "prefilter": (_STAGE.get("prefilter") or [])[:STAGE_LIST_CAP],
            "source_moves": _STAGE.get("source_moves") or [],
            "skills": _STAGE.get("skills"),
            "final": [{"score": s, "path": p,
                       "rule": ("inconclusive: routing score" if notes.get(p) == INCONCLUSIVE
                                else "possible (word search)" if p in possible and p in wpaths
                                else "possible" if p in possible
                                else "claim evidence" if _CLAIM["text"] else "evidence-selected")} for s, p, ptr in top],
            "cut_after_top5": [p for _s, p, _ptr in merged[5:5 + STAGE_LIST_CAP]],
        }
    except Exception as e:
        stages = {"error": type(e).__name__}
    write_trace(sdir, kind="trace", lookup_id=lookup_id, question=question, routing=routing,
                content_check=content_check,
                final_ranked=[{"score": s, "path": p, "pointer": ptr} for s, p, ptr in top],
                tier=tier, timings={"total_secs": round(time.time() - t0, 2)}, errors=error_lines,
                stages=stages)
    if _CLAIM["text"]:
        files = _STAGE.get("claim_files")
        if (top or _STAGE.get("claim_read")) and files is None:
            word, lines = "UNSURE", ["UNSURE: the true/false check did not run; read the files below before answering."]
        else:
            word, lines = claim_verdict(files or {}, _STAGE.get("claim_read") or list(content_check))
        for line in lines:
            print(line)
        if word in ("TRUE", "FALSE"):
            side = [p for p, f in (files or {}).items() if f["verdict"] == ("supported" if word == "TRUE" else "contradicted")
                    and isinstance(f.get("prob"), (int, float)) and f["prob"] >= CLAIM_SURE]
            best = max(side, key=lambda p: files[p]["prob"])
            # never save from text no refresh has passed, nor from a replay
            if best not in (_STAGE.get("word_changed") or []) + (_STAGE.get("stale_changed") or []) and not replay:
                claim_cache_put(sdir, _CLAIM["text"], word, best, files[best])
        log(sdir, "claim", question=question, result=word)
        if top:
            print("Files found:")
    # Hits always print first: a pointer error must never bury a real candidate
    # from a healthy pointer under the "unresolved" summary below it.
    for name, path in skills:
        print(f"skill  {path}  [skills: {name}]{SKILL_NOTE}")
    for s, p, ptr in top:
        note = ("  (inconclusive: content check did not finish; routing score)" if notes.get(p) == INCONCLUSIVE
                else possible.get(p, ""))
        lines = section_shown(p, ptr)
        print(f"{s:5.2f}  {p}  [{ptr}]" + (f"  (section: lines {lines[0]}-{lines[1]})" if lines else "") + note)
    if top and (_STAGE.get("listwise") or {}).get("leans_none"):
        print(LEANS_NONE_NOTE)
    for p, note in notes.items():
        if note == HELD_SECRET:
            print(f"HELD  {p}  ({HELD_SECRET})")
    for line in error_lines:
        print(line)
    if errored:
        print(f"unresolved: {errored} of {len(original_pointers)} pointers errored")
        if not top and not skills:
            # A miss with some pointers errored is still a miss on the healthy ones:
            # say what was searched and what was skipped, not just the voice line.
            for line in miss_report(principal, len(original_pointers), routing, content_check,
                                    question, original_pointers):
                print(line)
            print(VOICE_LINE)
            return 1
        # Partial failure: some pointers errored or are benched, but healthy
        # pointers still answered -- the failure stays visible above, it just
        # does not fail a lookup that actually has a real result.
        return 0
    if not top and not skills:
        if dropped:
            print(f"({dropped} file(s) matched the topic but no answer was confirmed on reading)")
        print(f"no-candidates across {len(original_pointers)} pointers: Super Jev couldn't find it in the connected "
              "files. It may still exist (see references/connectors.md to fill the gap).")
        for line in miss_report(principal, len(original_pointers), routing, content_check,
                                question, original_pointers):
            print(line)
        print(VOICE_LINE)
        return 0
    return 0

MISS_CLOSEST = 3


def skill_dir_for_display() -> Path:
    """Directory to print in user-facing next-step commands: the path this
    script was actually invoked with (stable across releases when it's run
    through a symlinked skills dir), not Path(__file__).resolve()'s dated
    release path, which goes stale the moment the next release deploys.
    SUPERJEV_SKILL_DIR overrides both when a caller knows the right answer.
    Falls back to the resolved path when argv[0] isn't a usable directory
    (e.g. run via -m, or a REPL/embedded invocation)."""
    env_dir = os.environ.get("SUPERJEV_SKILL_DIR")
    if env_dir:
        return Path(env_dir)
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and os.path.basename(argv0):
        candidate = Path(os.path.abspath(os.path.dirname(argv0) or "."))
        # Only a path to this same folder (a link to it counts), never an unrelated
        # launcher's folder such as pytest's or a wrapper's bin/.
        if candidate.is_dir() and candidate.resolve() == Path(__file__).resolve().parent:
            return candidate
    return Path(__file__).resolve().parent


MISS_SKIPPED = 3
SKIPPED_READ_BYTES = 256_000
SKIPPED_TEXT_COVERAGE = 0.75
SECRET_HELD = ("card/password", "secret-keyword")
PART_RE = re.compile(r"^(.+)-\d+$")


def skipped_files(pointers, principal: str = "") -> dict:
    """{path: (plain reason, fix, name-only)} for files setup held or whose label
    failed its check, across the principal's own visible pointers only. A part
    pointer <base>-N has no cache of its own: its base is read only when the base
    is itself visible to the principal. Local files only, no provider call."""
    cache_dir = prepare_bulk.CACHE_DIR
    visible = list(dict.fromkeys(pointers or ()))
    bases = []
    for ptr in visible:
        base = ptr
        if not any((cache_dir / f"{ptr}{suf}").is_file() for suf in (".json", "-report.json", "-held.txt")):
            m = PART_RE.match(ptr)
            base = m.group(1) if m and m.group(1) in visible else None
        if base and base not in bases:
            bases.append(base)
    out = {}
    for base in bases:
        try:
            report = json.loads((cache_dir / f"{base}-report.json").read_text())
            report = report if isinstance(report, dict) else {}
        except (OSError, ValueError):
            report = {}
        principals = report.get("principals") or ([report["principal"]] if report.get("principal") else [])
        if principal and principals and principal not in principals:
            continue  # a report naming its principals never lends its files to another one
        cache = load_cache_files(base)
        held = [tuple(e) for e in report.get("held") or [] if isinstance(e, list) and len(e) == 2]
        if not report:  # the report is the newer record; -held.txt only stands in without one
            try:
                held += [tuple(ln.split("\t", 1)) for ln in (cache_dir / f"{base}-held.txt").read_text().splitlines()
                         if "\t" in ln and not ln[:1].isspace()]
            except OSError:
                pass
        failed = [tuple(e) for e in report.get("exceptions") or [] if isinstance(e, list) and len(e) == 2]
        failed += [(path, str(c.get("verdict"))) for path, c in cache.items()
                   if isinstance(c, dict) and c.get("pass") is False]
        for path, why in held + failed:
            if (path in out or "admitted by --allow-held" in why or "admitted by --approve-held" in why
                    or (cache.get(path) or {}).get("pass") or not os.path.exists(path)):
                continue
            if "over size ceiling" in why:
                m = re.search(r"\(([\d,]+) bytes, max ([\d,]+)", why)
                size = (f" ({int(m.group(1).replace(',', '')) // 1000} KB, limit "
                        f"{int(m.group(2).replace(',', '')) // 1000} KB)") if m else ""
                out[path] = ("too big to connect" + size, "split it into smaller files, then re-run setup", "name")
            elif any(k in why for k in SECRET_HELD):
                out[path] = ("held back: it looks like it holds a password, key or card number",
                             "review the flagged line, then re-run setup with --allow-held", True)
            elif "UTF-8" in why:
                out[path] = ("not saved as UTF-8 text", "re-save it as UTF-8, then re-run setup", False)
            elif (path, why) in failed and "no draft" in why:
                out[path] = ("not connected: setup could not write a label for it",
                             "re-run setup on that file", False)
            elif (path, why) in failed:
                out[path] = ("not connected: its label did not pass the setup check",
                             "re-run setup on that file (its label failed the check)", False)
    return out


def skipped_for_question(question: str, pointers, principal: str = "") -> list:
    """Up to MISS_SKIPPED (path, reason, fix) skipped files sharing the question's
    words: by file name, and for a label-failed file its own local text too. A
    secret-like file is matched on its name only and never opened; a size-held file
    (a big log holds most questions' words) on its name only too."""
    terms = query_terms(question or "")
    if not terms:
        return []
    # Whole words, not substrings ("arc" is not in "search"); a long file holds half of
    # most questions' words, so its text must hold most of them to count.
    need = max(2, math.ceil(SKIPPED_TEXT_COVERAGE * len(terms))) if len(terms) > 1 else 2
    hits = lambda ws: sum(1 for t in terms if t in ws or (len(t) > 5 and any(w.startswith(t[:5]) for w in ws)))
    ranked = []
    for path, (why, fix, name_only) in skipped_files(pointers, principal).items():
        name_hits = hits(set(words(Path(path).stem)))
        text_hits = 0
        if not name_only and not name_hits:
            try:
                with open(path, "rb") as fh:
                    text_hits = hits(set(words(fh.read(SKIPPED_READ_BYTES).decode("utf-8", "replace"))))
            except OSError:
                pass
        if name_hits or text_hits >= need:
            ranked.append((-name_hits, -text_hits, path, why, fix))
    return [(p, why, fix) for _n, _t, p, why, fix in sorted(ranked)[:MISS_SKIPPED]]


def miss_report(principal: str, total: int, routing: dict, content_check: dict,
                question: str = "", pointers=()) -> list:
    """What a no-answer lookup searched, and the clean next steps, so the agent or
    human acting on a miss does not have to dig: which connected sets looked on
    topic, which files were read (closest first), and the exact commands to
    connect a missing folder or save a known answer."""
    on_topic = sorted(ptr for ptr, r in routing.items() if r.get("status") == "candidates")
    read = sorted((p for p in content_check if content_check[p].get("label") != "held-secret"),
                  key=lambda p: -(content_check[p].get("score") or 0))
    lines = ["What was searched:",
             f"  - {total} connected sets; {len(routing)} searched after the topic filter, {len(on_topic)} had matches"
             + (": " + ", ".join(on_topic[:5]) + (" ..." if len(on_topic) > 5 else "") if on_topic else "")]
    if read:
        lines.append(f"  - {len(read)} file(s) read; no answer confirmed. Closest: "
                     + ", ".join("/".join(Path(p).parts[-2:]) for p in read[:MISS_CLOSEST]))
    else:
        lines.append("  - no connected file matched the question's words closely enough to read")
    skipped = skipped_for_question(question, pointers, principal)
    if skipped:
        lines.append("Skipped at setup, and may hold the answer:")
        lines += [f"    {'/'.join(Path(p).parts[-2:])}: {why} -> Fix: {fix}" for p, why, fix in skipped]
    skill_dir = skill_dir_for_display()
    lines.append("Next step (pick one):")
    if skipped:
        # Their folder is already connected: connecting it again would skip them again.
        lines.append("  - The answer is in a skipped file above: apply its fix, then ask again.")
    elif read:
        lines += ["  - Inspect the read files and the filtering trace before changing connections:",
                  f"      python3 {skill_dir / 'ask.py'} --principal {principal} --trace-show last"]
    if not skipped:
        lines += ["  - If the answer is in a different file outside the connected sets, connect its folder:",
                  f"      python3 {skill_dir / 'prepare_bulk.py'} --root <folder> --pointer {principal}-<name> --principal {principal}"]
    lines += ["  - You know the answer: save it for next time:",
              f"      python3 {skill_dir / 'ask.py'} --principal {principal} --add \"<question>\" \"<answer>\"",
              "  - Neither: tell your human it was not found and offer to search by hand."]
    return lines


def find_pointer(sdir: Path, question: str):
    path = sdir / "lookups.jsonl"
    if not path.is_file():
        return None
    for line in reversed(path.read_text().splitlines()):
        rec = json.loads(line)
        if rec.get("kind") == "lookup" and rec.get("question") == question and rec.get("top"):
            # A possible-only hit is not a confirmed source: never approve from it.
            return None if rec["top"][0].get("possible") else rec["top"][0]["pointer"]
    return None

def find_candidate(sdir: Path, question: str, rank=None, file=None):
    """The row {score, path, pointer, possible} the lead picked from the last lookup's
    printed list: by 1-based rank, or by path (full path or unique file name).
    Returns (row, None) or (None, reason)."""
    path = sdir / "lookups.jsonl"
    top = None
    if path.is_file():
        for line in reversed(path.read_text().splitlines()):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("kind") == "lookup" and rec.get("question") == question and rec.get("top"):
                top = rec["top"]
                break
    if not top:
        return None, "no prior lookup with candidates for that question; run ask first, or use --add"
    if rank is not None:
        if not 1 <= rank <= len(top):
            return None, f"--rank {rank} is out of range: the last lookup listed {len(top)} candidate(s)"
        return top[rank - 1], None
    hits = [r for r in top if r.get("path") == file] or \
           [r for r in top if Path(r.get("path", "")).name == Path(file).name]
    if len(hits) != 1:
        return None, (f"--file {file!r} matches {len(hits)} of the last lookup's candidates; "
                      "pass the full path as printed, or use --rank N")
    return hits[0], None

def record_approver(sdir: Path, question: str, approved_by, **fields) -> None:
    """Who approved the saved answer for this exact question; the last line wins.
    approved_by None means un-saved."""
    sdir.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "question": question, "approved_by": approved_by, **fields}
    (sdir / "approvals.jsonl").open("a").write(json.dumps(entry) + "\n")

def approver(sdir: Path, question: str) -> dict:
    """The last approvals.jsonl entry for this question. No entry means a save from
    before auto-cache existed, when only humans could approve."""
    path = sdir / "approvals.jsonl"
    if path.is_file():
        for line in reversed(path.read_text().splitlines()):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("question") == question and rec.get("approved_by"):
                return rec
            if rec.get("question") == question:
                break
    return {"approved_by": "unknown (legacy)"}

def evidence_line_rank(text: str, answer: str, question: str = "") -> tuple:
    """Preserve exact dates and numeric identifiers that retrieval stopwords omit.
    This selects reviewed evidence for display; it is not a truth verdict."""
    dates = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", answer))
    numbers = {w for w in words(answer) if w.isdigit()}
    return (term_hits(query_terms(answer) or query_terms(question), text),
            len(dates & set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text))),
            len(numbers & set(words(text))))


def best_evidence_line(text: str, answer: str, question: str = "") -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return max(lines, key=lambda line: evidence_line_rank(line, answer, question), default="")


def file_evidence(principal: str, pointer: str, question: str, answer: str, path: str,
                  sid, out: dict, lines=None) -> tuple:
    """(search/assist result holding only passages from `path`, best-supporting first, or
    None, why). Search's passages are used when one comes from the file; otherwise the
    file's own reviewed lines that best match the answer are cited via assisted review,
    so a confirmed file is never refused just because search ranked another file first."""
    support = lambda p: evidence_line_rank(
        best_evidence_line(p.get("reviewedText") or "", answer, question), answer, question)
    passages = [p for p in out.get("passages") or [] if sid is not None and p.get("sourceId") == sid]
    if not any(p.get("reviewedText") for p in passages) and sid is not None and out.get("attemptId"):
        try:
            text = Path(path).read_text(errors="replace")
        except OSError:
            text = ""
        # A section of a big file is its own source, numbered from its first line.
        lines = (prepare_bulk.section_text(text, lines) if lines else text).splitlines()
        best = max(range(len(lines)), key=lambda i: evidence_line_rank(lines[i], answer, question), default=None)
        if best is not None and evidence_line_rank(lines[best], answer, question)[0] <= 0:
            return None, f"no line in {path} shares a word with the answer"
        if best is not None:
            # The file's opening lines ride along so the cited passage keeps its
            # subject (a bare table row
            # "Trash | Monday..." never says which address it is for).
            refs = [{"sourceId": sid, "startLine": n, "endLine": n} for n in sorted({1, best + 1})]
            out = memory({"action": "assist", "attemptId": out["attemptId"], "principal": principal,
                          "reason": f"reviewer picked {path}; citing its own lines that state the answer.",
                          "references": refs})
            if out.get("status") == "error" and out.get("reason") == "agent assist is disabled":
                return None, ASSIST_DISABLED_HINT
            # The file may have changed since connect: the cited reviewed text must still support the answer.
            passages = [p for p in out.get("passages") or [] if p.get("sourceId") == sid]
            if not any(support(p)[0] > 0 for p in passages):
                passages = []
    if not any(p.get("reviewedText") for p in passages):
        return None, f"no passage from {path} itself could be cited"
    return {**out, "passages": sorted(passages, key=support, reverse=True)}, None

def source_row(principal: str, pointer: str, path: str, answer: str = "", question: str = ""):
    """(the pointer's sources row for path or None, sources status); pages past 100. A file
    connected in sections has one row per section: the one holding the file's line that best
    matches the answer (evidence_line_rank) is returned, else its first."""
    offset, rows = 0, []
    while True:
        listed = memory({"action": "sources", "pointer": pointer, "principal": principal,
                         "offset": offset, "limit": 100})
        if listed.get("status") != "ok":
            return None, listed.get("status")
        rows += [s for s in listed.get("sources") or [] if s.get("originalPath") == path]
        if (rows and not rows[0].get("lines")) or listed.get("nextOffset") is None:
            break
        offset = listed["nextOffset"]
    if len(rows) > 1 and answer:
        try:
            lines = Path(path).read_text(errors="replace").split("\n")
        except OSError:
            lines = []
        best = max(range(len(lines)), key=lambda i: evidence_line_rank(lines[i], answer, question), default=None)
        if best is not None:
            rows.sort(key=lambda r: not (r.get("lines") and r["lines"][0] <= best + 1 <= r["lines"][1]))
    return (rows[0] if rows else None), "ok"

def live_sha(path: str, row: dict) -> str:
    """sha256 of what a sources row publishes now: the file, or its section's lines."""
    raw = Path(path).read_bytes()
    if row and row.get("lines"):
        raw = prepare_bulk.section_text(raw.decode("utf-8", "replace"), row["lines"]).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def ask_evidence(principal: str, pointer: str, question: str, answer: str, path: str, sid,
                 lines=None) -> tuple:
    """(ticket result citing only `path`, None) or (None, why). The file is the one ask()
    already ranked, so its own passage is cited through assist; memory's separate
    retrieval (description-only ranking) never decides whether it can be saved. An older
    runtime without the open action falls back to search only to get an attempt id."""
    hit = memory({"action": "cached", "principal": principal, "question": question})
    if hit.get("status") == "verified-cache-hit":
        return hit, None
    out = memory({"action": "open", "pointer": pointer, "principal": principal, "question": question})
    if out.get("reason") == "agent assist is disabled":
        return None, ASSIST_DISABLED_HINT
    if out.get("status") != "ok":
        out = memory({"action": "search", "pointer": pointer, "principal": principal, "question": question})
        if out.get("status") == "verified-cache-hit":
            return out, None
        if out.get("status") not in ("ready", "no-match", "refused") or not out.get("attemptId"):
            return None, f"cannot open {pointer}: {out.get('status')}"
    return file_evidence(principal, pointer, question, answer, path, sid,
                         {"attemptId": out["attemptId"]}, lines)

def send_approval(principal: str, question: str, answer: str, pointer: str, ticket_result: dict, sdir: Path,
                  approved_by: str = None, **fields) -> int:
    approved_by = approved_by if approved_by is not None else f"principal:{principal}"
    evidence = [{"sourceId": p["sourceId"], "quote": p["reviewedText"]} for p in ticket_result.get("passages", [])[:3] if p.get("reviewedText")]
    res = memory({"action": "approve", "ticket": ticket_result["approvalTicket"], "principal": principal, "approved": True, "answer": answer, "evidence": evidence})
    ok = res.get("status") in ("approved", "saved", "ok")
    print("approve:", res.get("status"), "" if ok else json.dumps(res)[:200])
    log(sdir, "approve", question=question, pointer=pointer, result=res.get("status"), approved_by=approved_by)
    if ok:
        record_approver(sdir, question, approved_by, pointer=pointer, **fields)
    return 0 if ok else 1

def find_top(sdir: Path, question: str):
    """The last lookup's top row {score, path, pointer} for this exact question."""
    path = sdir / "lookups.jsonl"
    if not path.is_file():
        return None
    for line in reversed(path.read_text().splitlines()):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("kind") == "lookup" and rec.get("question") == question and rec.get("top"):
            return rec["top"][0]
    return None

def auto_cache_on() -> bool:
    """Fleet default ON; SUPERJEV_AUTO_CACHE=0/off/false/no turns it off."""
    return os.environ.get("SUPERJEV_AUTO_CACHE", "1").strip().lower() not in ("0", "off", "false", "no")

def gate_command(claim: str, path: str) -> list:
    return [sys.executable, str(Path(__file__).resolve().parent / "superjev.py"), "gate", "--json",
            "--claim-mode", "evidence", "--claim", claim, path]

def evidence_text(ticket_result: dict) -> str:
    """The reviewed passages send_approval saves as evidence, joined."""
    return "\n\n".join(p["reviewedText"] for p in ticket_result.get("passages", [])[:3]
                       if p.get("reviewedText"))

def run_gate(claim: str, path: str, passage=None):
    """(verdict word, lowest claim score or None) from the existing check gate, run on
    path, or on passage (text from path) when given."""
    tmp = None
    try:
        if passage is not None:
            with tempfile.NamedTemporaryFile("w", suffix=Path(path).suffix or ".txt",
                                             prefix="evidence-", delete=False) as fh:
                fh.write(passage)
            tmp = path = fh.name
        r = subprocess.run(gate_command(claim, path), capture_output=True, text=True, timeout=300)
        body = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return "ERROR", None
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
    out = (body.get("details") or {}).get("stdout") or ""
    rows = re.findall(r"^\s*c\d+\s+[A-Z_]+\s+(\d+\.\d+)", out, re.M)
    verdict = body.get("verdict") or "ERROR"
    # check --claim treats time_sensitive as advisory, but a dated fact (a price,
    # a breakeven) must never auto-cache: it goes stale silently. jev's own
    # reply table prints whichever LABEL won the choice even when its own
    # confidence is under the 0.80 line it uses everywhere else to mean
    # "read the source, act on neither answer" -- a TIME_SENSITIVE label at,
    # say, 0.10 confidence is a near coin-flip, not a finding. Read the
    # confidence that rides next to the label and only let a *confident*
    # TIME_SENSITIVE call override CLEAN; a low-confidence one is noise
    # (bug: a NOT_TIME_SENSITIVE-leaning 0.05 call was blocking real
    # answers because only the label, never the score, was checked).
    ts_match = re.search(r"^\s*time_sensitive\s+TIME_SENSITIVE\s+(\d+\.\d+)", out, re.M)
    if verdict == "CLEAN" and ts_match and float(ts_match.group(1)) >= 0.80:
        verdict = "TIME_SENSITIVE"
    return verdict, (min(float(x) for x in rows) if rows else None)

_LAST_WHY = {"why": None}

def not_saved(sdir: Path, question: str, why: str) -> int:
    _LAST_WHY["why"] = why
    print(f"not saved: {why}")
    log(sdir, "auto-approve", question=question, result="not-saved", why=why)
    return 1

def auto_approve(principal: str, question: str, answer: str, sdir: Path,
                 top=None, approved_by: str = "auto-check") -> int:
    """--answer: save the answer only if the check gate calls it CLEAN against a fresh top file.
    top overrides the last lookup's top row (an agent's pick of a listed file)."""
    _LAST_WHY["why"] = None
    if not auto_cache_on():
        print("not saved: auto-cache is off (--no-auto or SUPERJEV_AUTO_CACHE=0); a human can still --approve")
        return 0
    top = top or find_top(sdir, question)
    if not top or not top.get("path"):
        return not_saved(sdir, question, "no prior lookup with candidates for that exact question; run ask first")
    pointer, evidence_file = top["pointer"], top["path"]
    try:
        text = Path(evidence_file).read_text(errors="replace")
    except OSError as e:
        return not_saved(sdir, question, f"cannot read {evidence_file}: {e.strerror or e}")
    if has_secret(text) or has_secret(question) or has_secret(answer):
        return not_saved(sdir, question, f"secret-held: {HELD_SECRET}")
    row, status = source_row(principal, pointer, evidence_file, answer, question)
    if status != "ok":
        return not_saved(sdir, question, f"stale: pointer {pointer} is {status}")
    if not row or row.get("contentSHA") != live_sha(evidence_file, row):
        return not_saved(sdir, question, f"stale: {evidence_file} changed since connect (or is not in {pointer})")
    out, why = ask_evidence(principal, pointer, question, answer, evidence_file, row.get("sourceId"),
                            row.get("lines"))
    if out and out.get("status") == "verified-cache-hit":
        print("already cached")
        return 0
    if not out:
        return not_saved(sdir, question, f"{why}; nothing to save")
    # The gate checks exactly the passages that will be saved as evidence.
    verdict, score = run_gate(f"Question: {question} Answer: {answer}",
                              evidence_file, evidence_text(out))
    if verdict != "CLEAN" or score is None:
        return not_saved(sdir, question, f"check gate verdict {verdict} (only CLEAN saves)")
    if score < 0.80:
        return not_saved(sdir, question, f"check gate score {score:.2f} is below the 0.80 auto-save floor")
    print(f"auto-check CLEAN (score {score:.2f}, evidence {evidence_file})")
    return send_approval(principal, question, answer, pointer, out, sdir,
                         approved_by=approved_by, evidence_file=evidence_file, score=score)

def miss(principal: str, question: str, actual: str, sdir: Path) -> int:
    log(sdir, "miss", question=question, actual=actual)
    print("miss recorded")
    write_outcome(sdir, last_lookup_id(sdir, question), question, "wrong", file=actual)
    res = memory({"action": "forget", "principal": principal, "question": question})
    if res.get("status") == "forgotten":
        who = approver(sdir, question).get("approved_by", "human")
        record_approver(sdir, question, None, removed_by="miss", was=who)
        print(f"un-saved: the cached answer (approved_by: {who}) was removed from {', '.join(res.get('pointers') or [])}")
    return 0

FOLLOWUP_MAX_TRIES = 5

def pending_misses(sdir: Path) -> dict:
    """question -> {"actual": str, "wrong": str|None, "tries": int} for every
    --miss not yet resolved (approved since the miss) or dropped
    (followup-drop, after FOLLOWUP_MAX_TRIES retries with no confident file).
    Read straight off lookups.jsonl -- no new state file, reusing the log
    --miss/--approve already write. "wrong" is the top path of the lookup
    that immediately preceded the miss -- the file SJ actually got wrong --
    tracked separately from "actual" (the --miss docstring's "where it
    actually was", i.e. the CORRECT location): the two are not the same
    thing, and a followup must never re-propose "wrong"."""
    path = sdir / "lookups.jsonl"
    pending: dict = {}
    if not path.is_file():
        return pending
    last_top = {}
    for line in path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        q = rec.get("question")
        if not q:
            continue
        kind = rec.get("kind")
        if kind == "lookup":
            top = rec.get("top")
            last_top[q] = top[0]["path"] if top else None
        elif kind == "miss":
            pending[q] = {"actual": rec.get("actual"), "wrong": last_top.get(q), "tries": 0}
        elif kind == "followup-try" and q in pending:
            pending[q]["tries"] = rec.get("tries", pending[q]["tries"])
        elif kind == "followup-drop":
            pending.pop(q, None)
        elif kind == "approve" and rec.get("result") in ("approved", "saved", "ok") and q in pending:
            pending.pop(q, None)
    return pending

def fresh_top(sdir: Path, question: str):
    """The top row from the MOST RECENT lookups.jsonl line, only if it is a
    'lookup' record for this exact question with a non-empty, non-possible
    top. Unlike find_top/find_pointer (which scan backward through history
    and can surface an OLD lookup's result), this never falls back past the
    single fresh lookup just run: a failed or empty fresh search (top=[], or
    an early-return path that logs nothing at all) yields None here instead
    of silently reusing a stale prior hit."""
    path = sdir / "lookups.jsonl"
    if not path.is_file():
        return None
    lines = path.read_text().splitlines()
    if not lines:
        return None
    try:
        rec = json.loads(lines[-1])
    except ValueError:
        return None
    if rec.get("kind") != "lookup" or rec.get("question") != question:
        return None
    top = rec.get("top")
    if not top or top[0].get("possible"):
        return None
    return top[0]

# A same-domain neighbor file shares SOME vocabulary with almost any in-domain
# question (a buyback report mentions "macbook"/"bid" on every page); a single
# shared word proves nothing. Real subject matches share most of the question's
# terms, not one -- the v1.0.6 amazon-bm-fb miss (a price-ceiling question
# proposing a different-date snapshot report) hit 4/8 terms by vocabulary
# alone, while the actually-correct file hit 6/8. 0.6 sits between the two.
SUBJECT_MATCH_FLOOR = 0.6

def subject_matches(question: str, path: str) -> bool:
    """True if the proposed file's own content actually shares subject with the
    question: at least SUBJECT_MATCH_FLOOR of the question's meaningful terms
    show up in the file, using the same term_hits check the routing/content
    layer uses elsewhere. A file can out-score everything else on routing
    score and still be about a different subject -- this is the
    belt-and-suspenders check the confidence tier alone doesn't give us.
    Unreadable file -> no match, never a free pass."""
    terms = query_terms(question)
    if not terms:
        return True  # nothing meaningful to check against; don't block on this alone
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return False
    return term_hits(terms, text) / len(terms) >= SUBJECT_MATCH_FLOOR

def followup(principal: str, sdir: Path, max_tries: int = FOLLOWUP_MAX_TRIES) -> int:
    """Re-try every pending miss: re-run the normal lookup (pointers may have
    refreshed since the miss) and propose the fresh top file for one-step
    --approve only when ALL of: (1) it's a CONFIRMED top file (fresh_top
    refuses a possible-only hit), (2) it's not the exact file the original
    miss already named wrong, and (3) its own content actually shares subject
    with the question (subject_matches) -- confidence tier alone isn't proof
    the file is even about the right thing. Never approves on its own -- a
    human still runs --approve with the answer text. A miss that stays
    unconfirmed for max_tries retries is dropped."""
    pending = pending_misses(sdir)
    if not pending:
        print("no pending misses")
        return 0
    proposed = 0
    for question, info in pending.items():
        tries = info["tries"] + 1
        print(f"retrying miss: {question!r} (try {tries}/{max_tries}; originally found at: {info['actual']})")
        lookup(question, principal, sdir)
        top = fresh_top(sdir, question)
        wrong = (info.get("wrong") or "").strip()
        if top and wrong and top["path"].strip() == wrong:
            # The fresh search found exactly the file SJ was already wrong
            # about at miss-time -- never re-propose it. Treat as still-miss.
            print(f"still wrong file for {question!r} -> {top['path']} (miss already named this)")
            top = None
        elif top and not subject_matches(question, top["path"]):
            # Confirmed tier is not proof of subject match -- a same-topic
            # neighbor file (e.g. a different date's snapshot in the same
            # folder) can out-score everything else while never mentioning
            # what was actually asked. Never propose it.
            print(f"confirmed but off-subject for {question!r} -> {top['path']} (no shared terms; not proposed)")
            top = None
        pointer = top["pointer"] if top else None
        if pointer and top:
            proposed += 1
            print(f"PROPOSED: confident file found for {question!r} -> {top['path']} [{pointer}]")
            print(f"  approve with: ask.py --principal {principal} --approve {question!r} \"<answer text>\"")
            log(sdir, "followup-try", question=question, tries=tries, result="proposed", pointer=pointer)
        elif tries >= max_tries:
            log(sdir, "followup-drop", question=question, tries=tries)
            print(f"dropped after {tries} tries with no confident file: {question!r}")
        else:
            log(sdir, "followup-try", question=question, tries=tries, result="still-miss")
    print(f"{proposed} proposal(s), {len(pending)} miss(es) checked")
    return 0

def approve(principal: str, question: str, answer: str, sdir: Path, pointer=None, rank=None, file=None,
            chosen=None) -> int:
    if chosen is None and (rank is not None or file is not None):
        # The lead picked a listed candidate by hand (any rank, possible tier
        # included): that pick IS the review, so approve from its own pointer.
        chosen, why = find_candidate(sdir, question, rank=rank, file=file)
        if not chosen:
            print(why)
            return 1
    if chosen is None and pointer is None:
        top = find_top(sdir, question)  # the file ask() ranked first, unless possible-only
        chosen = top if top and not top.get("possible") else None
    pointer = chosen["pointer"] if chosen else pointer
    if not pointer:
        print("no confirmed top candidate for that question; run ask first, pick a listed "
              "file with --rank N or --file PATH, or use --add")
        return 1
    extra = {"file": chosen["path"]} if chosen else {}
    if chosen:
        # Evidence comes from the file ask() ranked (or the lead picked) itself, matched by
        # full path so a same-name file in the same tree never stands in (businessfi
        # retest 2026-09-24); memory's own search ranking plays no part.
        row, _ = source_row(principal, pointer, chosen["path"], answer, question)
        try:
            now_sha = live_sha(chosen["path"], row)
        except OSError:
            now_sha = None
        if row and row.get("contentSHA") and row["contentSHA"] != now_sha:
            print(f"cannot approve: stale: {chosen['path']} changed since connect; refresh the pointer first.")
            log(sdir, "approve", question=question, pointer=pointer, result="stale")
            return 1
        out, why = ask_evidence(principal, pointer, question, answer, chosen["path"],
                                (row or {}).get("sourceId"), (row or {}).get("lines"))
    else:
        # No file known (--approve with an explicit pointer only): search is the fallback.
        out = memory({"action": "search", "pointer": pointer, "principal": principal, "question": question})
        why = f"search returned {out.get('status')} on {pointer}"
        if out.get("status") not in ("ready", "verified-cache-hit"):
            out = None
    if out and out.get("status") == "verified-cache-hit":
        print("already cached")
        return 0
    if not out:
        print(f"cannot approve: {why}; use --add with --source to record it manually.")
        log(sdir, "approve", question=question, pointer=pointer, result="evidence-mismatch")
        return 1
    rc = send_approval(principal, question, answer, pointer, out, sdir, **extra)
    if rc == 0:
        top = chosen or find_top(sdir, question)
        write_outcome(sdir, last_lookup_id(sdir, question), question, "right", file=(top or {}).get("path"))
    return rc

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
    # The memory backend may echo back a canonicalized (realpath) form of the
    # path we sent -- e.g. on macOS /tmp is itself a symlink to /private/tmp,
    # so a caller whose state dir or source lives under /tmp gets a literal
    # mismatch here even though it's the same file. Compare on realpath.
    hashes = {os.path.realpath(x["path"]): x["sha256"] for x in preview["sources"]}
    for s in req["sources"]:
        real = os.path.realpath(s["path"])
        if s["path"] in hashes:
            s["sha256"] = hashes[s["path"]]
        else:
            s["sha256"] = hashes[real]
    req["reviewed"] = True
    reg = memory(req)
    if reg.get("status") != "registered" or not reg.get("sources"):
        print("connect failed:", json.dumps(reg)[:300])
        return 1
    print(f"manual entry written: {record.name}; pointer {pointer} registered")
    source_id = reg["sources"][0]["id"]
    rc = approve_manual(principal, question, answer, pointer, source_id, record, sdir)
    if rc == 0:
        lid = last_lookup_id(sdir, question)
        prior = last_outcome(sdir, lid) if lid else None
        if prior and prior.get("result") == "wrong":
            added_file = str(Path(source).resolve()) if source and Path(source).is_file() else str(record)
            write_outcome(sdir, lid, question, "wrong-added", file=added_file)
    return rc

# --- Pick trail: an agent records which listed choice it used; every
# PICK_BATCH picks the claim checker runs on each and saves only CLEAN ones.
# Refused verdicts drop the pick; unsure ones stay for --pending-picks.
PICK_BATCH_DEFAULT = 5
PICK_REFUSED = ("REJECT", "CONTRADICTED", "TIME_SENSITIVE", "stale", "secret-held")

def pick_batch() -> int:
    try:
        return max(1, int(os.environ.get("SUPERJEV_PICK_BATCH", PICK_BATCH_DEFAULT)))
    except ValueError:
        return PICK_BATCH_DEFAULT

def picks_path(sdir: Path) -> Path:
    return sdir / "pending_picks.jsonl"

def load_picks(sdir: Path) -> list:
    p = picks_path(sdir)
    out = []
    for line in (p.read_text().splitlines() if p.is_file() else []):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out

PICK_MAX = 200  # queue cap: oldest picks fall off so unsure ones cannot pile up forever

@contextlib.contextmanager
def picks_lock(sdir: Path):
    """Serialize read-modify-write of pending_picks.jsonl across agents sharing a principal."""
    sdir.mkdir(parents=True, exist_ok=True)
    with open(sdir / "pending_picks.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield

def save_picks(sdir: Path, picks: list) -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    tmp = picks_path(sdir).with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in picks[-PICK_MAX:]))
    tmp.replace(picks_path(sdir))

def record_pick(principal: str, sdir: Path, which: str, rank=None, file=None, answer=None) -> int:
    """--used: queue "agent used choice N of trace <which>"; flush once the queue is full."""
    rec = find_trace(sdir, which)
    if not rec:
        print(f"no trace {which!r}; run ask first")
        return 1
    ranked = rec.get("final_ranked") or []
    if rank is not None:
        if not 1 <= rank <= len(ranked):
            print(f"--rank {rank} is out of range: that lookup listed {len(ranked)} candidate(s)")
            return 1
        row = ranked[rank - 1]
    else:
        hits = [r for r in ranked if r.get("path") == file] or \
               [r for r in ranked if Path(r.get("path", "")).name == Path(file).name]
        if len(hits) != 1:
            print(f"--file {file!r} matches {len(hits)} of that lookup's candidates; pass the full path or --rank N")
            return 1
        row, rank = hits[0], ranked.index(hits[0]) + 1
    pick = {"id": hashlib.sha1(f"{rec.get('lookup_id')}|{rank}|{time.time()}".encode()).hexdigest()[:8],
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "lookup_id": rec.get("lookup_id"),
            "question": rec.get("question"), "rank": rank, "file": row.get("path"),
            "pointer": row.get("pointer"), "answer": answer}
    with picks_lock(sdir):
        picks = load_picks(sdir) + [pick]
        save_picks(sdir, picks)
    write_trace(sdir, kind="pick", lookup_id=pick["lookup_id"], question=pick["question"],
                rank=rank, file=pick["file"])
    print(f"pick {pick['id']} queued: rank {rank} {pick['file']}")
    if sum(1 for r in picks if "why" not in r and "dropped" not in r) >= pick_batch():
        return flush_picks(principal, sdir)
    return 0

def flush_picks(principal: str, sdir: Path) -> int:
    """Check every unchecked pick: CLEAN saves (approved_by agent-pick+check), a refused
    verdict drops it, anything else stays listed as unsure."""
    if not auto_cache_on():
        print("picks kept: auto-cache is off (SUPERJEV_AUTO_CACHE=0)")
        return 0
    with picks_lock(sdir):
        return _flush_picks(principal, sdir)

def _flush_picks(principal: str, sdir: Path) -> int:
    keep = []
    for pick in load_picks(sdir):
        if "why" in pick or "dropped" in pick:
            keep.append(pick)
            continue
        print(f"pick {pick['id']}: {pick['question']}")
        if not pick.get("answer"):
            keep.append({**pick, "why": "no answer text to check; confirm with an answer or drop"})
            continue
        rc = auto_approve(principal, pick["question"], pick["answer"], sdir,
                          top={"path": pick["file"], "pointer": pick["pointer"]},
                          approved_by="agent-pick+check")
        why = _LAST_WHY["why"]
        if rc == 0:
            write_outcome(sdir, pick["lookup_id"], pick["question"], "right", file=pick["file"])
        elif why and any(w in why for w in PICK_REFUSED):
            # A refused pick (TIME_SENSITIVE, stale, secret-held...) used to be
            # discarded here with only a transient print, so it vanished from
            # --pending-picks with no trace beyond lookups.jsonl -- a real
            # refusal ("fair" per the businessfi report) looked identical to a
            # silent bug. It now stays listed under --pending-picks' "Dropped"
            # section (tagged "dropped": why) until a human clears it with an
            # explicit --confirm-pick or --drop-pick, instead of vanishing on
            # its own the moment the checker runs.
            print(f"pick {pick['id']} dropped: {why}")
            keep.append({**pick, "dropped": why})
        else:
            keep.append({**pick, "why": why or "not saved"})
    save_picks(sdir, keep)
    return 0

def pending_picks(sdir: Path) -> int:
    picks = load_picks(sdir)
    dropped = [r for r in picks if "dropped" in r]
    unsure = [r for r in picks if "dropped" not in r]
    if not picks:
        print("no pending picks")
    for r in unsure:
        print(f"{r['id']}  rank {r['rank']}  {r['file']}\n    Q: {r['question']}\n    "
              f"{r.get('why') or 'waiting for batch check'}")
    if dropped:
        print("\nDropped (not saved):")
        for r in dropped:
            print(f"{r['id']}  rank {r['rank']}  {r['file']}\n    Q: {r['question']}\n    "
                  f"{r['dropped']}")
    if any("why" in r for r in unsure) or dropped:
        print('\nconfirm: --confirm-pick ID ["answer"]   drop/clear: --drop-pick ID')
    return 0

def settle_pick(principal: str, sdir: Path, pid: str, answer=None, drop=False) -> int:
    with picks_lock(sdir):
        return _settle_pick(principal, sdir, pid, answer, drop)

def _settle_pick(principal: str, sdir: Path, pid: str, answer, drop) -> int:
    picks = load_picks(sdir)
    pick = next((r for r in picks if r["id"] == pid), None)
    if not pick:
        print(f"no pending pick {pid}")
        return 1
    if not drop:
        answer = answer or pick.get("answer")
        if not answer:
            print('usage: --confirm-pick ID "answer" (this pick has no answer text)')
            return 2
        if approve(principal, pick["question"], answer, sdir,
                   chosen={"path": pick["file"], "pointer": pick["pointer"]}) != 0:
            return 1
    save_picks(sdir, [r for r in picks if r["id"] != pid])
    print(f"pick {pid} {'dropped' if drop else 'confirmed'}")
    return 0

def connection_status(principal: str) -> int:
    """Read scoped registration metadata, without searching or refreshing."""
    skill = skill_dir_for_display()
    panel = memory({"action": "panel", "principal": principal})
    if (not isinstance(panel, dict) or panel.get("status") == "error"
            or not isinstance(panel.get("pointers"), list)):
        if isinstance(panel, dict) and panel.get("reason") == "not-set-up":
            print(f"Not set up. Next: python3 {skill / 'setup.py'}")
        else:
            print("Connection status unavailable. Next: check the memory connector configuration.")
        return 1
    pointers = panel["pointers"]
    if not pointers:
        print(f"Nothing connected for {principal}.")
        print(f"Next: follow {skill.parent / 'super-jev-connect' / 'SKILL.md'} to connect a folder.")
        return 0
    print(f"Connections for {principal} (registered snapshots, not a freshness guarantee):")
    for row in pointers:
        if isinstance(row, str):
            name, status = row, "unknown"
        elif isinstance(row, dict) and isinstance(row.get("pointer"), str):
            name = row["pointer"]
            status = row.get("snapshotStatus") or row.get("status") or "unknown"
        else:
            print("Connection status unavailable: malformed pointer metadata.")
            return 1
        if status == "available":
            print(f"  {name}: ready")
        elif str(status).startswith(("preparation-required", "refresh-required")):
            hint = refresh_hint(name, principal, str(status))
            hint = hint.replace("and replaying its connect recipe failed", "and it has a recorded connect recipe")
            print(f"  {name}: stale ({status})" + hint)
        else:
            print(f"  {name}: {status}")
    print("Next: refresh stale sets with their listed instructions; inspect unknown/error sets before use. "
          "Ready sets can be searched with ask.py --principal " + principal + ' "question".')
    return 0


PREFLIGHT_QUESTIONS = (
    "have we already tried {about}, and how did it go",
    "which design rules and steps apply to {about}",
    "what errors, traps or quirks are known for {about}",
    "which code files and tests handle {about}",
)
PREFLIGHT_SKIP_DIRS = {"__pycache__", "node_modules", "prepare-cache", "autoheal-state"}  # generated, never notes
PREFLIGHT_STRONG = 0.85  # a confirmed hit at or above this means the topic is known
_HIT_LINE = re.compile(r"^\s*([0-9]+\.[0-9]{2})\s+\S")
_QUALIFIED = re.compile(r"\]\s+\(")  # "(possible: ...)", "(inconclusive: ...)": an unconfirmed hit


def _connected_files(principal: str, pointer: str) -> tuple:
    """(files, extensions, ok) for one pointer. files = the sources the backend has REGISTERED for it
    now (paged `sources`), never a prepare report's approved list, which a --no-connect or failed run
    also writes. extensions = the suffixes the pointer opted into (its report, or its split parent's)."""
    files, offset = set(), 0
    while True:
        out = memory({"action": "sources", "pointer": pointer, "principal": principal, "offset": offset, "limit": 100})
        if not isinstance(out, dict) or out.get("status") != "ok":
            return set(), (), False
        files |= {os.path.realpath(os.path.expanduser(s["originalPath"])) for s in out.get("sources") or []
                  if isinstance(s, dict) and isinstance(s.get("originalPath"), str)}
        nxt = out.get("nextOffset")
        if not isinstance(nxt, int) or nxt <= offset:
            break
        offset = nxt
    exts = ()
    for n in [pointer] + ([re.sub(r"-[0-9]+$", "", pointer)] if re.search(r"-[0-9]+$", pointer) else []):
        try:
            rep = json.loads((prepare_bulk.CACHE_DIR / f"{n}-report.json").read_text())
        except (OSError, ValueError):
            continue
        if isinstance(rep, dict):
            exts = tuple(str(e).lower() for e in rep.get("extensions") or [] if isinstance(e, str))
        break
    return files, exts, True


def _folder_files(root: Path) -> dict:
    """{real path: presented names} for every file under root, walking into symlinked folders like
    prepare_bulk.walk_md (os.walk followlinks, each real folder once so a link loop ends); hidden and
    generated dirs skipped. The suffix is judged on the presented name (alias.md -> target.txt is a .md
    source, as inventory treats it); identity is the real path, so two routes to one file count once."""
    out, walked = {}, set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        real = os.path.realpath(dirpath)
        if real in walked:
            dirnames[:] = []
            continue
        walked.add(real)
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in PREFLIGHT_SKIP_DIRS]
        for n in filenames:
            f = os.path.join(dirpath, n)
            if not n.startswith(".") and os.path.isfile(f):
                out.setdefault(os.path.realpath(f), set()).add(n.lower())
    return out


def _folder_coverage(folder: Path, principal: str, ready: list) -> dict:
    """How many of the folder's connectable files a ready pointer of this principal has registered.
    Connectable = the default suffixes plus any a pointer registered here opted into (name endswith,
    so compound suffixes like .schema.json match)."""
    names = _folder_files(folder.expanduser())
    on_disk = set(names)
    exts, connected, unread = set(getattr(prepare_bulk, "CONNECTABLE_EXTENSIONS", (".md",))), set(), []
    for name in ready:
        files, opted, ok = _connected_files(principal, name)
        if not ok:
            unread.append(name)
        mine = files & on_disk
        if mine:
            connected |= mine
            exts |= set(opted)
    exts = {e.lower() for e in exts}
    connectable = {f for f, ns in names.items() if any(n.endswith(tuple(exts)) for n in ns)}
    return {"folder": str(folder), "connectable": len(connectable), "connected": len(connected & connectable),
            "unsupported": len(on_disk) - len(connectable), "extensions": sorted(exts), "unread": unread}


def _run_ask(cmd: list) -> tuple:
    """(stdout, failure or ""): a nonzero exit or a timeout is a failure, never an empty answer."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return "", "timed out after 600s"
    except OSError as e:
        return "", str(e)
    if r.returncode:
        return r.stdout, f"exit {r.returncode}: " + (r.stderr or r.stdout).strip()[-200:]
    return r.stdout, ""


def preflight(principal: str, args: list) -> int:
    """Before work starts: am I ready (free), and what do I already know (asks, only with --about)."""
    dirs, about, skill, as_json, i = [], "", "", False, 0
    while i < len(args):
        flag = args[i]
        if flag == "--json":
            as_json, i = True, i + 1
            continue
        if flag not in ("--project-dir", "--about", "--skill") or i + 1 >= len(args):
            print('usage: --principal AGENT --preflight [--project-dir DIR ...] [--about "the work"] '
                  '[--skill "what a new skill would do"] [--json]')
            return 2
        val = args[i + 1]
        if flag == "--project-dir":
            dirs.append(val)
        elif flag == "--about":
            about = val
        else:
            skill = val
        i += 2
    problems, warnings, report = [], [], {"principal": principal}
    panel = memory({"action": "panel", "principal": principal})
    rows = panel.get("pointers") if isinstance(panel, dict) and panel.get("status") != "error" else None
    if not isinstance(rows, list):
        problems.append("connection status unavailable" + (": not set up (run setup.py)"
                        if isinstance(panel, dict) and panel.get("reason") == "not-set-up" else ""))
        rows = []
    elif not rows:
        problems.append("nothing connected for this principal (see super-jev-connect/SKILL.md)")
    ready, states = [], {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("pointer"), str):
            name, st = row["pointer"], str(row.get("snapshotStatus") or row.get("status") or "unknown")
        elif isinstance(row, str):
            name, st = row, "unknown"
        else:
            problems.append("malformed pointer metadata")
            continue
        states[name] = st
        if st == "available":
            ready.append(name)
        elif st.startswith(("preparation-required", "refresh-required")):
            warnings.append(f"{name} is stale (its last snapshot is still searched; an ask refreshes it)"
                            + refresh_hint(name, principal, st))
        else:
            problems.append(f"{name} is not ready: {st}")
    report["connections"] = {"ready": len(ready), "total": len(states)}
    report["folders"] = []
    for d in dirs:
        folder = Path(d)
        if not folder.expanduser().is_dir():
            problems.append(f"not a folder: {d}")
            continue
        cov = _folder_coverage(folder, principal, ready)
        report["folders"].append(cov)
        if cov["unread"]:
            problems.append(f"{d}: could not list the registered files of {', '.join(cov['unread'])}")
        if not cov["connected"]:
            problems.append(f"{d}: 0 of {cov['connectable']} connectable files are connected; connect the folder "
                            f"(see {skill_dir_for_display().parent / 'super-jev-connect' / 'SKILL.md'})")
        elif cov["connected"] < cov["connectable"]:
            warnings.append(f"{d}: {cov['connected']} of {cov['connectable']} connectable files are connected")
        if cov["unsupported"]:
            warnings.append(f"{d}: {cov['unsupported']} files of other types are not connected "
                            f"(connected types here: {', '.join(cov['extensions'])})")
    if about and not problems:
        answers, strong, failed = [], 0, 0
        for q in PREFLIGHT_QUESTIONS:
            q = q.format(about=about)
            out, err = _run_ask([sys.executable, str(skill_dir_for_display() / "ask.py"), "--principal", principal, "--", q])
            hits = [l.strip() for l in out.splitlines() if _HIT_LINE.match(l)][:5]
            # a failed ask's output is never evidence, whatever it printed before failing
            strong += 0 if err else sum(1 for h in hits if float(_HIT_LINE.match(h).group(1)) >= PREFLIGHT_STRONG
                                        and not _QUALIFIED.search(h))
            answers.append({"question": q, "hits": hits, **({"error": err} if err else {})})
            if err:
                failed += 1
                warnings.append(f"knowledge ask failed ({err}): {q}")
        report["knowledge"] = answers
        # New ground only when every ask ran and none found a confirmed strong note; a failed ask proves nothing.
        report["new_ground"] = False if strong else (None if failed else True)
    if skill and not problems:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"request": f"a skill that {skill}", "context": []}, f)
        out, err = _run_ask([sys.executable, str(skill_dir_for_display() / "dispatch.py"), "skills", "--request-file", f.name])
        os.unlink(f.name)
        found = None
        if not err:
            try:
                data = json.loads(out)
                found = [c.get("name") for c in data.get("candidates", []) if isinstance(c, dict)]
                if data.get("status") == "error" or data.get("error"):
                    found, err = None, str(data.get("error") or data.get("reason") or "status error")[:200]
            except (ValueError, AttributeError):
                err = "unreadable output: " + out.strip()[:200]
        if err:
            warnings.append(f"existing-skill search failed ({err}); not proof that no skill does this")
        report["existing_skills"] = found
    verdict = "NOT READY" if problems else ("READY WITH WARNINGS" if warnings else "READY")
    report.update(verdict=verdict, problems=problems, warnings=warnings)
    if as_json:
        print(json.dumps(report, ensure_ascii=False))
        return 1 if problems else 0
    print(f"PREFLIGHT {verdict} (principal {principal}; {len(ready)} of {len(states)} connections ready)")
    for c in report["folders"]:
        print(f"  folder {c['folder']}: {c['connected']} of {c['connectable']} connectable files connected")
    for p in problems:
        print(f"  NOT READY: {p}")
    for w in warnings:
        print(f"  WARNING: {w}")
    for a in report.get("knowledge", []):
        print(f"  asked: {a['question']}")
        print("\n".join(f"    {h}" for h in a["hits"]) or "    (nothing found)")
    if report.get("new_ground") is None and "knowledge" in report:
        print("  NEW GROUND UNKNOWN: a knowledge ask failed; rerun before treating this as new ground.")
    if report.get("new_ground"):
        print("  NEW GROUND: no strong note on this; research outside (official docs, maintained projects) "
              "first, and teach what you learn back afterwards.")
    if report.get("existing_skills") is not None:
        print("  existing skills that may already do this: " + (", ".join(report["existing_skills"]) or "none")
              + " (reuse or extend a match instead of building a duplicate)")
    if problems:
        print("Next: fix each NOT READY line and rerun --preflight.")
    return 1 if problems else 0


def resolve_principal(args: list) -> tuple[str, list]:
    if "--principal" in args:
        i = args.index("--principal")
        return args[i + 1], args[:i] + args[i + 2:]
    return os.environ.get("SUPERJEV_PRINCIPAL", ""), args

def main() -> int:
    try:
        return _main()
    except SecretHeld as e:
        print(f"ask: {e}", file=sys.stderr)
        return 1

def _main() -> int:
    principal, a = resolve_principal(sys.argv[1:])
    if not principal or not a:
        print(__doc__)
        return 2
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", principal):  # same rule as the memory runtime
        print(f"invalid --principal {principal!r}: use the agent's exact name (letters, digits, "
              "'.', '_', '-'; no spaces or slashes)")
        return 2
    if a[0] == "--":  # the rest is the question, read literally (never a --flag)
        if len(a) == 1:
            print(__doc__)
            return 2
        return lookup(" ".join(a[1:]), principal, state_dir(principal))
    if a[0] == "--preflight":
        return preflight(principal, a[1:])
    if a[0] == "--status":
        if len(a) != 1:
            print("usage: --principal AGENT --status")
            return 2
        return connection_status(principal)
    sdir = state_dir(principal)
    if "--no-auto" in a:
        os.environ["SUPERJEV_AUTO_CACHE"] = "0"
        a = [x for x in a if x != "--no-auto"]
        if not a:
            print(__doc__)
            return 2
    if a[0] == "--trace-show":
        return trace_show(sdir, a[1] if len(a) > 1 else "last")
    if a[0] == "--trace-report":
        days = None
        if len(a) >= 3 and a[1] == "--days":
            try:
                days = int(a[2])
            except ValueError:
                print("usage: --trace-report [--days N]")
                return 2
        return trace_report(sdir, days)
    if a[0] == "--miss":
        return miss(principal, a[1], " ".join(a[2:]), sdir)
    if a[0] == "--followup":
        max_tries = FOLLOWUP_MAX_TRIES
        if len(a) >= 3 and a[1] == "--max-tries":
            try:
                max_tries = int(a[2])
            except ValueError:
                print("usage: --followup [--max-tries N]")
                return 2
        return followup(principal, sdir, max_tries)
    if a[0] == "--answer":
        if len(a) < 3:
            print('usage: --answer "question" "answer"')
            return 2
        return auto_approve(principal, a[1], " ".join(a[2:]), sdir)
    if a[0] == "--approve":
        rest, rank, file = a[1:], None, None
        try:
            while "--rank" in rest:
                i = rest.index("--rank"); rank = int(rest[i + 1]); del rest[i:i + 2]
            while "--file" in rest:
                i = rest.index("--file"); file = rest[i + 1]; del rest[i:i + 2]
        except (IndexError, ValueError):
            rest = []
        if len(rest) != 2 or (rank is not None and file is not None):
            print('usage: --approve "question" "answer" [--rank N | --file PATH]')
            return 2
        return approve(principal, rest[0], rest[1], sdir, rank=rank, file=file)
    if a[0] == "--used":
        rest, rank, file, answer = a[2:], None, None, None
        try:
            while rest:
                flag, val = rest[0], rest[1]
                if flag == "--rank":
                    rank = int(val)
                elif flag == "--file":
                    file = val
                elif flag == "--answer":
                    answer = val
                else:
                    raise ValueError
                del rest[:2]
        except (IndexError, ValueError):
            rank = file = None
        if len(a) < 2 or (rank is None) == (file is None):
            print('usage: --used <trace_id|last> (--rank N | --file PATH) [--answer "text"]')
            return 2
        return record_pick(principal, sdir, a[1], rank=rank, file=file, answer=answer)
    if a[0] == "--flush-picks":
        return flush_picks(principal, sdir)
    if a[0] == "--pending-picks":
        return pending_picks(sdir)
    if a[0] in ("--confirm-pick", "--drop-pick") and len(a) >= 2:
        return settle_pick(principal, sdir, a[1], answer=" ".join(a[2:]) or None, drop=a[0] == "--drop-pick")
    if a[0] == "--add":
        return do_add(principal, a[1:], sdir)
    if a[0] in ("--claim", "--claims-file"):
        claims, rest = [], a
        while rest and rest[0] in ("--claim", "--claims-file") and len(rest) >= 2:
            if rest[0] == "--claim":
                claims.append(rest[1])
            else:  # one statement per line: a draft's facts or a worker report's claims
                try:
                    claims += [re.sub(r"^(?:[-*\u2022]|\d+[.)])\s+", "", ln.strip())
                               for ln in Path(rest[1]).expanduser().read_text(errors="replace").splitlines()
                               if ln.strip() and not ln.lstrip().startswith("#")]
                except OSError as e:
                    print(f"cannot read {rest[1]}: {e.strerror or e}")
                    return 2
            rest = rest[2:]
        if not claims or rest:
            print('usage: --claim "statement" [--claim ...] | --claims-file FILE (one statement per line)')
            return 2
        if len(claims) > MAX_CLAIMS:  # each statement is its own lookup and paid Jev calls
            print(f"{len(claims)} statements; at most {MAX_CLAIMS} per run -- split the file or keep the facts that matter")
            return 2
        rc = 0
        for i, claim in enumerate(claims):
            if len(claims) > 1:
                print(f"{'' if i == 0 else chr(10)}=== CLAIM {i + 1}: {claim}")
            _CLAIM["text"] = claim
            try:
                rc = max(rc, lookup(claim, principal, sdir))
            except Exception as e:  # one failed statement must not drop the rest of the file
                print(f"ERROR: this statement could not be checked ({type(e).__name__}: {e}); "
                      "check it by hand")
                traceback.print_exc()
                rc = max(rc, 3)  # distinct from 2 (bad arguments)
            finally:
                _CLAIM["text"] = None
        return rc
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
