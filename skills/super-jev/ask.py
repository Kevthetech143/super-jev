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
      approves one as a human, --drop-pick ID removes it.

Cache hits print who approved them: "approved_by: human" (--approve, --add)
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
A hit prints nothing extra. Any harness relaying ask.py's output to a human
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
    print("2 routing (name + description only; floor %s):" % ROUTE_FLOOR)
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
    for i, f in enumerate(w.get("top", []), 1):
        print(f"  {i:2}. {f['score']:7.3f}  {f['path']}  -> {f['fate']}")
    print(f"4 read list ({len(st.get('read_list', []))}): " + ", ".join(Path(p).name for p in st.get("read_list", [])))
    print("5 content check (confirm >= %s, possible >= %s):" % (CONFIRM_FLOOR, POSSIBLE_FLOOR))
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
        print("  evidence:", e.get("sourceId", ""), "|", str(e.get("quote", ""))[:120])
    return 0

# navigation-cli refuses longer questions (src/enhance/navigation.ts MAX_QUESTION)
MAX_QUESTION = 8000
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
# A later breaker set still passed near misses at 0.70-0.83, so the floor is 0.85:
# re-measured 2026-09-22 on 8 near-miss and 8 present x2, near misses 0-0.87 (one,
# "current APY after the rate change", 0.86-0.87 still passes), present 0.90-0.96.
# A file too long to send whole (more than CONFIRM_CHUNKS_PER_FILE chunks) is judged on
# its first chunk plus the chunks sharing the most words with the question, never
# kept on its routing score alone: that bypass returned CLOV.md at 0.97 for a
# meeting it does not mention (businessfi stress test 2, 2026-09-23).
#
# Two tiers. >= CONFIRM_FLOOR is a confirmed match. The same stress test found owner
# questions (how/why/should/status) scoring 0.60-0.84 on the right file, so any
# checked file scoring >= POSSIBLE_FLOOR is kept as "possible"
# (printed with that note, the caller reads the file before answering) instead of
# being reported as not in the files. Replayed on the test's 196 traced asks: the
# near-miss negatives' top-routed files scored 0-0.55, so no negative gained a hit.
CONFIRM_FILES, CONFIRM_CHUNK, CONFIRM_CHUNKS_PER_FILE = 5, 3500, 4
ROUTE_FLOOR, CONFIRM_FLOOR = 0.05, 0.85
POSSIBLE_FLOOR = 0.6
# Round 2 (same stress test, 17 bad results): any file the check read scoring
# >= POSSIBLE_FLOOR may be possible, not just the top 2; open questions (how/should/
# why/when) are judged on "does it answer" instead of "does it state the exact
# value" (VALUE_RE picks the exact-value wording); live-number questions (LIVE_RE)
# get no possible tier at all, since on-topic files at 0.81 gave two false hits.
VALUE_RE = re.compile(r"\b(how much|how many|balance|breakeven|break even|right now|"
                      r"worth|owe|owed|price|cost|total)\b", re.I)
POSSIBLE_NOTE = "  (possible: on topic, answer not confirmed; read the file before answering)"
# Word search: on every lookup the
# principal's reviewed files (prepare-cache entries whose sha256 still matches) are
# searched locally for the question's words (typo-tolerant), and the best
# FALLBACK_FILES get the same content check (kept below CONFIRM_FLOOR as possible).
FALLBACK_FILES, FALLBACK_MIN_COVERAGE, FALLBACK_REL_FLOOR = 5, 0.5, 0.55
FALLBACK_NOTE = "  (possible: word-search match, answer not confirmed; read the file before answering)"
WORD_RE = re.compile(r"[a-z0-9]+")
QUERY_STOPWORDS = SUBJECT_STOPWORDS | {
    "i", "me", "we", "our", "us", "you", "can", "could", "should", "would", "will", "get",
    "got", "much", "many", "any", "now", "still", "need", "there", "about", "into", "out",
    "whats", "hows", "whens", "wheres", "whos", "im", "ive", "dont", "not", "no", "yes",
    "all", "some", "just", "so", "if", "than", "then", "up", "tell", "know", "right",
}
CONFIRM_LABEL = ("Passage {n}, choose only if it states the exact value asked for, for the exact "
                 "event asked about (a value for another event, or only the topic, is none)")
ANSWER_LABEL = ("Passage {n}, choose only if it answers the question: a rule, plan, reason, "
                "date, status or view on what is asked (a passage that only shares a word "
                "with the question is none)")
# Live-number asks: no possible tier, a file must confirm at CONFIRM_FLOOR.
# "how much" alone used to be enough (any bare "how much X" counted as live),
# but that caught non-money asks like "how much cheaper and faster is this"
# (EXPLORE.md, recall80 bench, dropped at 0.80 with no possible tier). "how
# much" now only counts as live when it names a money word too; the plain
# live markers (balance/breakeven/right now) still gate on their own.
LIVE_RE = re.compile(r"\b(balance|breakeven|break even|right now)\b", re.I)
LIVE_HOWMUCH_RE = re.compile(
    r"\bhow much\b.{0,40}\b(balance|owe|owed|worth|cost|price|due|total|left|"
    r"remaining|money|cash|pay|paid|spend|spent|charge|charged|fee|fees|bill|"
    r"dollars?|bucks)\b", re.I)

# Routing score at/above which a read file stays "possible" even when the content
# check finds no answer (opinion asks like "should I invest" rarely read as answered).
ROUTE_KEEP = 0.8
# A file too big to read whole (health-fitness pending.md: 80 KB, 24 chunks) is
# judged on 4 chunks, so the answer may sit in a chunk never read. When routing
# is sure (>= BIG_ROUTE_KEEP) and the read passages were on topic (>= BIG_ON_TOPIC),
# it stays possible. A big file scoring under BIG_ON_TOPIC (CLOV.md for a
# meeting it never mentions) is still dropped.
BIG_ROUTE_KEEP, BIG_ON_TOPIC = 0.85, 0.5
BIG_NOTE = "  (possible: strongly routed, file too long to read whole; start at section: %s)"
# Final order blends content and routing so a strong route is not thrown away.
CONTENT_WEIGHT, ROUTE_WEIGHT = 0.6, 0.4
# Word search only: vague words that name a file's topic in other words.
SYNONYMS = {"verify": ["check", "feedback"], "rebalance": ["watchlist", "allocation"],
            "holdings": ["positions", "watchlist"], "money": ["funding", "revenue", "cash"]}

# Opinion asks ("should I invest", "who is winning", "can I sell calls") rarely read
# as answered; only these get the route-keep.
OPINION_RE = re.compile(r"\b(should|can i|could i|who is winning|whos winning|worth it|good idea)\b", re.I)

def rank_score(content: float, route: float) -> float:
    return CONTENT_WEIGHT * content + ROUTE_WEIGHT * route

def is_value_question(question: str) -> bool:
    return bool(VALUE_RE.search(question))

def is_live_value_question(question: str) -> bool:
    return bool(LIVE_RE.search(question)) or bool(LIVE_HOWMUCH_RE.search(question))

# The narrower slice of VALUE_RE this round loosens for the possible tier: "how
# many" and "how much" only. worth/owe/owed/price/cost/total stay excluded --
# those are exactly the near-miss shapes ("what is owed", "what price did we
# pay") the 0.85 confirm floor was raised to block, per test_retrieval_recall
# and test_review_recall_holes.
HOWCOUNT_RE = re.compile(r"\b(how many|how much)\b", re.I)

def is_howcount_question(question: str) -> bool:
    return bool(HOWCOUNT_RE.search(question))

OPEN_RE = re.compile(r"\s*(how|should|shall|why|when|can|could|would|do|does|is|are|"
                     r"which|where)\b", re.I)
# "what" is not in OPEN_RE outright: "what car do I have" and "what time does it
# depart" are "what" questions that DO want one exact value, and treating every
# "what" as open reintroduced the near-miss false hits the exact-value label was
# built to stop (test_hardening_round3/4). Only "what ... <answer verb>" -- a
# casual ask about what a file says/covers/means, not a fact lookup -- gets the
# open treatment (idea B, 2026-09-23, recall80 bench: tools-audit's "what swap
# path services did we discover" was one of the 7 rejections this targets).
WHAT_ANSWER_RE = re.compile(
    r"\bwhat\b.{0,40}\b(say|says|said|cover|covers|mean|means|discover|discovered|"
    r"discuss|discusses|find|found|include|includes|show|shows|about)\b", re.I)
# "what is on the pending to-do list" wants the list, not one exact value: the
# exact-value wording scored the right 80 KB pending.md 0.57 (live trace
# 2026-09-24), so list/to-do/backlog asks get the open wording too.
# Only "what is on ... list": "what is the phone number ... on the referral list"
# still wants one exact value.
LIST_RE = re.compile(r"\bwhat('?s|s| is| are)?\s+on\b.{0,50}\b(list|lists|to-?dos?|backlog|checklist)\b",
                     re.I)

def confirm_label(question: str) -> str:
    """Open how/should/why/when/which/where questions, and "what ... say/cover/mean"
    style casual asks, ask "does it answer"; the rest (and any value question) ask
    for the exact value."""
    open_q = (OPEN_RE.match(question) or WHAT_ANSWER_RE.search(question)
              or LIST_RE.search(question)) \
        and not is_value_question(question)
    return ANSWER_LABEL if open_q else CONFIRM_LABEL
HELD_SECRET = "contains a secret; not sent"
INCONCLUSIVE = "inconclusive"

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
    The spread credit only orders files within a tier: a file is possible only when
    its best passage alone reaches POSSIBLE_FLOOR and confirms only when it reaches
    CONFIRM_FLOOR (capped just under it otherwise); live-value asks (balance, right
    now...) get no credit at all."""
    if live or passages <= 1 or not isinstance(none, (int, float)) or best < POSSIBLE_FLOOR:
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
    chunks = [text[i:i + CONFIRM_CHUNK] for i in range(0, len(text), CONFIRM_CHUNK)] or [""]
    partial = False  # long files are judged on chosen passages, never passed through unread
    label = confirm_label(question)
    picked = pick_chunks(question, chunks)
    detail = _STAGE.setdefault("checks", {})[path] = {
        "chunks": len(chunks), "read": picked[:STAGE_LIST_CAP],
        "wording": "exact-value" if label == CONFIRM_LABEL else "answers"}
    leaves = [{"id": f"c{i}", "label": label.format(n=i + 1), "description": chunks[i],
               "sourceId": str(i)} for i in picked]
    payload = {"question": question, "limits": {"beamWidth": 5, "maxResults": 10},
               "catalog": {"version": 1, "structure": "flat-files", "rootId": "root",
                           "nodes": [{"id": "root", "label": "Sources",
                                      "description": "Full text of candidate files",
                                      "children": [leaf["id"] for leaf in leaves]}, *leaves]}}
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
    # A file too big to read whole whose chosen passages were on topic but under
    # the possible floor records its best section; lookup keeps it as possible
    # only if it was strongly routed (BIG_ROUTE_KEEP).
    if len(chunks) > CONFIRM_CHUNKS_PER_FILE and BIG_ON_TOPIC <= best < POSSIBLE_FLOOR:
        top = max((c for c in body.get("candidates") or [] if isinstance(c, dict)
                   and isinstance(c.get("score"), (int, float))), key=lambda c: c["score"])
        head = text[:int(top.get("sourceId") or 0) * CONFIRM_CHUNK + CONFIRM_CHUNK]
        detail["section"] = next((ln.lstrip("# ").strip() for ln in reversed(head.splitlines())
                                  if ln.startswith("#")), "")[:80]
    score = file_score(best, detail["none"], len(picked), is_live_value_question(question))
    detail["score"] = round(score, 3)
    return (score if score >= POSSIBLE_FLOOR else None), partial, None, None

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

# Listwise reorder + promote (2026-09-25, tested as "Version C" in
# superjev-tests/listwise-2026-09-25/result.md): one extra Jev call over every
# file that reached POSSIBLE_FLOOR on today's content check, asking which
# single one best answers the question. Unlike an AND-gate this never demotes
# a file -- the winner just moves to #1 of the final ranking (see the reorder
# right after `top = apply_near_twin_tiebreak(...)`), and is only promoted to
# CONFIRM_FLOOR if its own winning probability is itself >= LISTWISE_PROMOTE_FLOOR.
# On the bakeoff this beat both the installed baseline and an
# AND-gate variant on right-confirmed and wrong-confirmed at once.
# Improved on internal eval (details kept private). SUPERJEV_LISTWISE=0
# turns it off and restores today's behavior (same style as SUPERJEV_BATCH_JEV).
def listwise_enabled() -> bool:
    return os.environ.get("SUPERJEV_LISTWISE", "1") != "0"

LISTWISE_MAX_FILES = 8
LISTWISE_SNIPPET = 2000
LISTWISE_LABEL = "Passage {n}, choose only if this is the single best answer to the question"
LISTWISE_PROMOTE_FLOOR = 0.9

def judge_listwise(question: str, paths: list):
    """One Jev call over up to LISTWISE_MAX_FILES paths (already capped by the
    caller to files scoring >= POSSIBLE_FLOOR), asking which single one best
    answers the question. Returns (winning path, its own probability), or
    (None, None) if fewer than 1 file could be read/sent, or the call errored,
    timed out, or was inconclusive -- callers must treat that as no opinion and
    leave scores and ranking unchanged."""
    texts = {}
    for p in paths[:LISTWISE_MAX_FILES]:
        try:
            text = Path(p).read_text(errors="replace")
        except OSError:
            continue
        if has_secret(text):
            continue
        texts[p] = text[:LISTWISE_SNIPPET]
    if len(texts) < 1:
        return None, None
    ordered = list(texts)
    leaves = [{"id": f"t{i}", "label": LISTWISE_LABEL.format(n=i + 1),
               "description": texts[p], "sourceId": p} for i, p in enumerate(ordered)]
    payload = {"question": question, "limits": {"beamWidth": 5, "maxResults": 10},
               "catalog": {"version": 1, "structure": "flat-files", "rootId": "root",
                           "nodes": [{"id": "root", "label": "Sources",
                                      "description": "Candidate files", "children": [leaf["id"] for leaf in leaves]}, *leaves]}}
    if payload_has_secret(payload):
        return None, None
    try:
        r = subprocess.run(navigation_command(), input=json.dumps(payload), capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if r.returncode:
        return None, None
    try:
        body = json.loads(r.stdout)
    except ValueError:
        return None, None
    if not isinstance(body, dict) or body.get("status") != "candidates" or not body.get("candidates"):
        return None, None
    best = max((c for c in body["candidates"] if isinstance(c, dict) and isinstance(c.get("score"), (int, float))),
               key=lambda c: c["score"], default=None)
    return (best.get("sourceId"), best.get("score")) if best else (None, None)

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
    # so do copies and write-ups (copy_kind), already ordered by prefer_sources.
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
    words = WORD_RE.findall(question.lower().replace("'", ""))
    return list(dict.fromkeys(w for w in words if len(w) > 2 and w not in QUERY_STOPWORDS))

def term_hits(terms: list, text: str) -> int:
    """How many terms appear in text, a term also matching by its first five letters."""
    low = text.lower()
    return sum(1 for t in terms if t in low or (len(t) > 5 and t[:5] in low))

def pick_chunks(question: str, chunks: list) -> list:
    """Indexes to read: all chunks if they fit, else chunk 0 plus the chunks sharing
    the most question words, in file order."""
    if len(chunks) <= CONFIRM_CHUNKS_PER_FILE:
        return list(range(len(chunks)))
    terms = query_terms(question)
    ranked = sorted(range(1, len(chunks)), key=lambda i: (-term_hits(terms, chunks[i]), i))
    return sorted([0] + ranked[:CONFIRM_CHUNKS_PER_FILE - 1])

def load_cache_files(pointer: str) -> dict:
    """Only prepare-cache/<pointer>.json. Part caches are registered as their own
    pointers (<pointer>-N) and arrive in the principal's pointer list when owned, so
    matching <pointer>-N.json here could read another principal's pointer."""
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
    counts = Counter(WORD_RE.findall(text.lower()))
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
        if entry.get("generation") != g:
            missing.append(p)
        elif entry.get("words") is not None:
            known[p] = entry["words"]
    return known, missing

def save_pointer_words(sdir: Path, principal: str, generations: dict, missing: list) -> None:
    """List and read the missing pointers' files (local, no provider calls) and save
    their words. Runs beside routing, so the first ask after a refresh is not slower."""
    def load(ptr):
        out = memory({"action": "sources", "pointer": ptr, "principal": principal})
        if out.get("status") != "ok" or not out.get("sources"):
            return ptr, None
        words = set()
        for src in out["sources"]:
            head = f"{src.get('originalPath', '')} {src.get('description', '')}".lower()
            try:
                words.update(WORD_RE.findall(head + " " + Path(src["path"]).read_text(errors="replace").lower()))
            except (OSError, KeyError, TypeError):
                return ptr, None  # a file we cannot read: never skip this pointer
        return ptr, " ".join(sorted(words))

    try:
        with ThreadPoolExecutor(max_workers=max(1, min(len(missing), NAV_CONCURRENCY))) as pool:
            loaded = list(pool.map(load, missing))
        path = sdir / POINTER_WORDS_FILE
        saved = _load_pointer_words(path)
        for ptr, words in loaded:
            saved[ptr] = {"generation": generations[ptr], "words": words}
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(saved))
        tmp.replace(path)
    except Exception:
        pass  # best effort: an unsaved pointer is just asked again next time

def word_search(question: str, pointers: list, limit: int = FALLBACK_FILES, skip=()) -> list:
    """Local, no provider calls: [(score, path, pointer)] of the principal's reviewed
    files best matching the question's words (BM25 per CONFIRM_CHUNK passage, a file
    scores its best passage; a file's path, description and stored question count triple
    in every passage). Typos match a close word (difflib). A file must
    cover FALLBACK_MIN_COVERAGE of the question's weighted words to be offered. Test/scratch
    output is never searched; `skip` paths (already routed) are dropped before the top `limit`."""
    terms = query_terms(question)
    if not terms:
        return []
    docs = {}
    for ptr in pointers:
        for path, entry in load_cache_files(ptr).items():
            if (path in docs or not isinstance(entry, dict) or not entry.get("pass")
                    or prepare_bulk.is_test_material(path) or prepare_bulk.is_bench_dataset(path)):
                continue
            try:
                raw = Path(path).read_bytes()
            except OSError:
                continue
            if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
                continue  # changed since review: not reviewed text any more
            text = raw.decode("utf-8", "replace")
            heading = next((ln.lstrip("# ") for ln in text.splitlines() if ln.startswith("#")), "")
            head = " ".join([path.replace("/", " ").replace("-", " ").replace("_", " "), heading,
                             str(entry.get("description") or ""), str(entry.get("question") or "")])
            head_words = Counter(w for w in WORD_RE.findall(head.lower()) for _ in range(3))
            passages = [passage_words(text[i:i + CONFIRM_CHUNK]) + head_words
                        for i in range(0, len(text), CONFIRM_CHUNK)] or [Counter(head_words)]
            docs[path] = (ptr, sum(passages, Counter()), [(c, sum(c.values())) for c in passages])
    if not docs:
        return []
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
    sizes = [size for _, _, parts in docs.values() for _, size in parts]
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
        bm25 = max(sum(idf[t] * pf[t] * 2.2 / (pf[t] + 1.2 * (0.25 + 0.75 * size / avg)) for t in terms)
                   for pf, size in ((tf_of(c), size) for c, size in parts))
        scored.append((round(bm25, 3), path, ptr))
    ranked = sorted(scored, key=lambda x: (-x[0], x[1]))
    _STAGE["word"] = {"terms": terms, "files_searched": len(docs), "passed_coverage": len(scored),
                      "ranked": ranked[:STAGE_LIST_CAP]}
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
    return confirm_results(paths, results)

def confirm_results(paths: list, results: list):
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
    # The printed command must run as-is from any folder: the absolute script path plus the
    # pointer's recorded roots/excludes and every principal it serves (a bare --refresh with
    # no report is refused for want of --root; one principal short is refused as a scope change).
    script = Path(__file__).resolve().parent / "prepare_bulk.py"
    try:
        report = json.loads((prepare_bulk.CACHE_DIR / f"{ptr}-report.json").read_text())
        args = refresh_changed.prepare_args(report) if isinstance(report, dict) else None
    except (OSError, ValueError):
        args = None
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
    return (f"; its files changed since connect and it has no recorded recipe. Run: python3 {script} "
            f"--root DIR --pointer {ptr} --principal {principal} --refresh "
            "(one --root per connected folder, one --principal per agent it serves)")

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
# pr-19's hook survey over docs/wire-into-claude-code.md), so prefer_sources()
# ranks the source first.
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

def prefer_sources(ranked: list, scores: dict, possible: dict) -> list:
    """Move each hub/copy/writeup just below the lowest real note it yields to.
    A copy yields to any note that passed the content check (>= POSSIBLE_FLOOR);
    a hub yields to a note under its own folder that is confirmed or scored
    higher than the hub; the hub or copy then counts as possible itself -- it
    is the pointer or duplicate, not the evidence. A writeup yields only to a confirmed note: pr-history notes are
    the source for "which PR did X". With nothing to yield to, a hub, copy or
    writeup keeps its place (it may be the only answer; clov/README is the
    campaign dashboard)."""
    out = list(ranked)
    for m in list(ranked):
        kind = copy_kind(m[1])
        if not kind:
            continue
        targets = [x for x in out if not copy_kind(x[1])
                   and (scores.get(x[1], 0) >= POSSIBLE_FLOOR if kind == "copy"
                        else x[1] not in possible and scores.get(x[1], 0) >= CONFIRM_FLOOR
                        or kind == "hub" and scores.get(x[1], 0) > scores.get(m[1], 0))
                   and (kind != "hub" or Path(x[1]).is_relative_to(Path(m[1]).parent))]
        last = max((out.index(x) for x in targets), default=-1)
        if last > out.index(m):
            out.remove(m)
            out.insert(last, m)
            _STAGE.setdefault("source_moves", []).append({"path": m[1], "kind": kind, "below": out[last - 1][1]})
            if kind != "writeup":
                possible[m[1]] = POSSIBLE_NOTE
    return out

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
    words = {w.removesuffix("'s").strip("'") for w in re.findall(r"[a-z']+", question.lower())}
    if words & GROUP_WORDS:
        return set()
    rel = {RELATIONS[w] for w in words if w in RELATIONS and w != "self"}
    by_rel = {n for n, r in folks.items() if r & rel}
    if rel and not by_rel:
        return set()
    who = by_rel | {n for n in folks if n in words}
    self_ = {n for n, r in folks.items() if "self" in r}
    if not who:
        return self_ if words & FIRST_PERSON else set()
    return who | self_ if words & {"i", "me", "myself"} else who

def lookup(question: str, principal: str, sdir: Path) -> int:
    if len(question) > MAX_QUESTION:
        print(f"question too long ({len(question):,} chars, max {MAX_QUESTION:,}); ask a shorter question")
        return 2
    t0 = time.time()
    lookup_id = new_lookup_id(principal, question, t0)
    _STAGE.clear()
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
    panel = memory({"action": "panel", "principal": principal})
    if panel.get("reason") == "not-set-up":
        print("Super Jev is not set up yet. Run: python3 skills/super-jev/setup.py")
        return 1
    pointers = [n for n in ((p.get("pointer") if isinstance(p, dict) else p)
                            for p in panel.get("pointers", [])) if n and n != withheld]
    if not pointers:
        print(f"nothing connected yet for principal '{principal}' -- run connect first:\n"
              f"  python3 skills/super-jev/prepare_bulk.py --root /path/to/folder "
              f"--pointer my-notes --principal {principal}")
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

    def classify(ptr, out, elapsed):
        status, reason = out.get("status"), out.get("reason", "")
        nav_none[ptr] = (_root_none(out), round(elapsed, 2))
        if status == "candidates" and out.get("candidates"):
            return ptr, "candidates", out["candidates"], elapsed, True
        if status in ("candidates", "no-candidates"):
            return ptr, "no-candidates", [], elapsed, True
        kind = status or "error"
        return ptr, f"{kind}: {reason}" if reason else kind, [], elapsed, False

    def failed_overloaded(out):
        return (out.get("status") not in ("candidates", "no-candidates")
                and _is_overloaded(f"{out.get('status')} {out.get('reason', '')}"))

    def nav(ptr):
        t_start = time.time()
        out = memory({"action": "navigate", "pointer": ptr, "principal": principal, "question": question})
        # One backoff retry for an overloaded provider (HTTP 529) -- not counted
        # as a failure unless the retry also fails.
        if failed_overloaded(out):
            time.sleep(OVERLOAD_BACKOFF_SECS)
            out = memory({"action": "navigate", "pointer": ptr, "principal": principal, "question": question})
        return classify(ptr, out, time.time() - t_start)

    def nav_many(ptrs):
        """{pointer: navigate result}: one navigation-cli run whose Jev questions for
        every pointer share as few calls as fit under Jev's input ceiling."""
        out = memory({"action": "navigate-many", "pointers": ptrs, "principal": principal, "question": question})
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
                                                         "question": question}) for ptr in retry})
        results = [classify(ptr, outs[ptr], time.time() - t_start) for ptr in pointers]
    # A stale pointer whose files are all already reviewed at their current bytes needs no
    # redraft, only a reconnect: do it now and ask it again, so this lookup reads it.
    # One RECONNECT_TIMEOUT_SECS budget covers every reconnect in this lookup.
    reconnected, deadline = {}, time.time() + auto_heal.RECONNECT_TIMEOUT_SECS
    for i, (ptr, kind, *_rest) in enumerate(results):
        left = int(deadline - time.time())
        if auto_heal.is_stale_kind(kind) and left >= 1:
            reconnected[ptr] = auto_heal.reconnect_now(ptr, principal, timeout=left)
            if reconnected[ptr] == "no-report":
                # Not built by prepare_bulk: replay the connect recipe recorded at connect time.
                reconnected[ptr] = auto_heal.reconnect_recipe(ptr, principal, memory=memory)
            if reconnected[ptr] == "reconnected":
                results[i] = nav(ptr)
    _STAGE["reconnect"] = reconnected
    merged, errored, statuses = [], 0, {}
    for ptr, kind, rows, elapsed, ok in results:
        record_pointer_outcome(health, ptr, ok, elapsed, stale=_is_stale_kind(kind))
        if kind == "candidates":
            statuses[ptr] = "candidates"
            merged += [(c.get("score", 0), c.get("originalPath", ""), ptr) for c in rows]
        elif kind == "no-candidates":
            statuses[ptr] = "no-candidates"
        else:
            errored += 1
            statuses[ptr] = kind
            hint = refresh_hint(ptr, principal, kind)
            # A stale pointer never has to wait on a human to run the refresh hint above by
            # hand: this starts the exact same prepare_bulk.py --refresh in the background,
            # bounded (one in flight per principal, per-pointer cooldown, hourly cap -- see
            # auto_heal.py), and returns immediately either way. It never blocks this lookup
            # and never changes what this lookup reports for the pointer that triggered it;
            # it only means the *next* lookup may no longer hit it.
            heal_note = ""
            stale = auto_heal.is_stale_kind(kind)
            if stale:
                result = auto_heal.maybe_heal(ptr, principal)
                if result == "started":
                    heal_note = " (auto-heal: refresh started in background)"
                elif result == "in-progress":
                    heal_note = " (auto-heal: refresh already in progress)"
                elif result == "cooldown":
                    heal_note = " (auto-heal: refreshed recently, cooling down)"
                elif result == "rate-limited":
                    heal_note = " (auto-heal: hourly refresh limit reached)"
            # [STALE] marks a pointer that is just waiting on its own refresh (not
            # a live error, never benched) so it reads differently at a glance from
            # a real provider error -- appended after the existing kind/hint/heal
            # text so it never changes what those already say.
            error_lines.append(f"[{ptr}] {kind}" + hint + heal_note + (" [STALE]" if stale else ""))
    save_pointer_health(sdir, health)
    if learner:
        learner.join()
    _STAGE["person"]["dropped"] = [m[1] for m in merged if other_person(m[1])][:STAGE_LIST_CAP]
    merged = sorted((m for m in merged if m[0] >= ROUTE_FLOOR and not prepare_bulk.is_bench_dataset(m[1])
                     and not other_person(m[1])), reverse=True)
    routed = list(dict.fromkeys(p for _, p, _ in merged))
    route = {}
    for s, p, _ in merged:
        route.setdefault(p, s)
    dropped, check_error, notes, possible = 0, None, {}, {}
    # Always add the word search's best few: routing alone missed 7 of 30 right files.
    found = word_search(question, search_pointers, skip=set(routed[:CONFIRM_FILES])
                        | {p for ptr in search_pointers for p in load_cache_files(ptr) if other_person(p)})
    wpaths = {p: ptr for _, p, ptr in found}
    to_check = routed[:CONFIRM_FILES] + list(wpaths)
    checked = set(to_check)
    listwise_winner = None  # survives to the reorder step even when to_check is empty
    if to_check:
        scores, partial, check_error, notes = confirm(question, to_check)
        if check_error:
            errored += 1
            error_lines.append(f"[content-check] error: {check_error}")
        if listwise_enabled():
            listwise_pool = [p for p in to_check if scores.get(p, 0) >= POSSIBLE_FLOOR]
            _STAGE["listwise"] = {"pool": listwise_pool, "ran": len(listwise_pool) >= 1}
            if listwise_pool:
                listwise_winner, listwise_prob = judge_listwise(question, listwise_pool)
                _STAGE["listwise"]["winner"] = listwise_winner
                _STAGE["listwise"]["winner_prob"] = listwise_prob
                _STAGE["listwise"]["promoted"] = False
                if listwise_winner and isinstance(listwise_prob, (int, float)) \
                        and listwise_prob >= LISTWISE_PROMOTE_FLOOR and scores.get(listwise_winner, 0) < CONFIRM_FLOOR:
                    scores[listwise_winner] = max(scores[listwise_winner], CONFIRM_FLOOR)
                    _STAGE["listwise"]["promoted"] = True
        # Only files the check actually read may stay: a file past the first
        # CONFIRM_FILES was never read, so it is not evidence of anything.
        # Value questions need a confirmed score; no possible tier -- except
        # "how many" / non-live "how much" (is_howcount_question), which get the
        # possible tier back same as any other question. wheel-radar (0.67, "how
        # many put opportunities") and EXPLORE (0.80, "how much cheaper and
        # faster") were both dropped here even though neither is a live-money ask
        # (idea A, 2026-09-23, recall80 bench). Other value words (worth/owe/
        # owed/price/cost/total) and any live figure stay gated: those are the
        # near-miss shapes the 0.85 confirm floor exists to block.
        if not is_value_question(question) or (is_howcount_question(question)
                                                 and not is_live_value_question(question)):
            possible = {p: (FALLBACK_NOTE if p in wpaths else POSSIBLE_NOTE) for p in to_check
                        if POSSIBLE_FLOOR <= scores.get(p, 0) < CONFIRM_FLOOR}
            # A strongly routed file that was read keeps a possible slot even if the
            # answer check found nothing (opinion asks); never for live-value asks.
            # Never for any value question (count or money) -- only plain opinion asks.
            for p in (routed[:CONFIRM_FILES] if OPINION_RE.search(question)
                      and not is_value_question(question) else []):
                if route.get(p, 0) >= ROUTE_KEEP and p not in notes and p not in possible \
                        and scores.get(p, 0) < CONFIRM_FLOOR:
                    possible[p] = POSSIBLE_NOTE
            for p in to_check:
                section = _STAGE.get("checks", {}).get(p, {}).get("section")
                if section is not None and route.get(p, 0) >= BIG_ROUTE_KEEP \
                        and p not in notes and p not in possible:
                    possible[p] = BIG_NOTE % (section or "top of file")
        def demoted(p: str) -> bool:
            """A hub file (is_hub_file) ranks as a table of contents -- except a
            README whose own content check CONFIRMED the answer: that README is
            the folder's dashboard (campaigns/clov/README.md holds the $3.94
            breakeven and was ranked 3rd behind notes without it, businessfi
            hand test 2026-09-24). INDEX/catalog/tools-used/handoff/template stay
            demoted at any score: replaying the fleet's 199 traces, promoting
            those would flip correct top notes (PeakMonsters, TD SMS codes)."""
            return is_hub_file(p) and not (Path(p).stem.lower() == "readme"
                                           and scores.get(p, 0) >= CONFIRM_FLOOR)

        cands = merged + [(0, p, ptr) for p, ptr in wpaths.items()]
        keep = {}
        for s, p, ptr in cands:
            if p in keep:
                continue
            if (scores.get(p, 0) >= CONFIRM_FLOOR or p in possible or p in partial
                    or (notes.get(p) == INCONCLUSIVE and p not in wpaths)):
                # A route-kept file shows no content score above the possible floor.
                keep[p] = (scores.get(p, POSSIBLE_FLOOR if p in possible else s), p, ptr)
            # An index/catalog/handoff file is a table of contents, not evidence
            # itself; move it into the possible group so the real note it points
            # to (which has a real content score) always outranks it.
            if p in keep and p not in possible and demoted(p):
                possible[p] = POSSIBLE_NOTE
        dropped = len(checked - set(keep) - {p for p, n in notes.items() if n == HELD_SECRET})

        def metric_of(p: str) -> tuple:
            """(has_content, content_or_0, route) for a kept path. A route-kept
            path that was never given a real content score (scores has no entry
            for it) reports has_content=False so it never outranks a path that
            does have one, no matter how high its routing score is."""
            c = scores.get(p)
            return (c is not None, c if c is not None else 0.0, route.get(p, 0))

        def better(a: str, b: str) -> bool:
            """True if path a should rank above path b within the possible
            group: any real content score beats route-only; between two real
            scores the higher one wins unless they round to a near-tie, in
            which case routing breaks it; two route-only paths fall back to
            routing alone."""
            ha, ca, ra = metric_of(a)
            hb, cb, rb = metric_of(b)
            if ha != hb:
                return ha
            if not ha:
                return ra > rb
            if round(ca, 2) == round(cb, 2):
                return ra > rb
            return ca > cb

        def sort_metric(m: tuple) -> tuple:
            """Sort key for every kept file, confirmed or possible alike:
            content score first (rounded to 2 decimals, so a near-tie falls
            through to the route component), then routing only to break that
            near-tie, then the name tie-break. A hub-type file (see
            is_hub_file) ranks below every other kept file regardless of its
            content score, since it is a table of contents, not evidence."""
            s, p, ptr = m
            if p in possible:
                has_content, content, r = metric_of(p)
            else:
                c = scores.get(p)
                has_content, content, r = (c is not None, c if c is not None else s, route.get(p, 0))
            # Groups: notes with a content score, then a possible README with a
            # real content score, then route-only notes, then other hubs. The
            # README still yields to any scored note (#149), but no longer sits
            # below route-only 0.60 files (businessfi retest 2026-09-24).
            readme_scored = (Path(p).stem.lower() == "readme" and p in possible
                             and scores.get(p, 0) >= POSSIBLE_FLOOR)
            group = 0 if demoted(p) and not readme_scored else \
                2 if readme_scored else 3 if has_content else 1
            return (group, has_content, round(content, 2), r, path_rank(question, p))

        # Non-hub files first (sort_metric's leading True), then content score
        # alone with routing only as a near-tie breaker (see metric_of/better/
        # sort_metric above), then the name tie-break.
        merged = prefer_sources(sorted(keep.values(), key=sort_metric, reverse=True), scores, possible)
    top = merged[:5]
    top = apply_near_twin_tiebreak(question, top)
    # Listwise reorder: the winner moves to #1 if it is anywhere in `top`,
    # preserving the relative order of everything else. Never drops or demotes
    # a file; a no-op if the winner isn't in `top` (e.g. it scored below what
    # made the final cut).
    winner_idx = next((i for i, (_s, p, _ptr) in enumerate(top) if p == listwise_winner), None) \
        if listwise_winner else None
    # A hub/copy/writeup winner never promotes past a non-hub file already
    # ranked ahead of it: prefer_sources already made that source-over-hub
    # call (see is_hub_file/copy_kind), same reasoning as the near-twin
    # tiebreak's hub exemption above (h22, 2026-09-25: README 0.98 promoted
    # itself back over the confirmed panel note 0.91).
    blocked = winner_idx is not None and (is_hub_file(listwise_winner) or copy_kind(listwise_winner)) \
        and any(not (is_hub_file(p) or copy_kind(p)) for _s, p, _ptr in top[:winner_idx])
    if winner_idx is not None and not blocked:
        _STAGE["listwise"]["reordered"] = top[0][1] != listwise_winner
        top = sorted(top, key=lambda m: m[1] != listwise_winner)
    else:
        _STAGE.setdefault("listwise", {})["reordered"] = False
    log(sdir, "lookup", question=question, pointers=len(pointers), statuses=statuses, secs=round(time.time() - t0, 1),
        top=[{"score": s, "path": p, "pointer": ptr, "possible": p in possible} for s, p, ptr in top])
    routing = {ptr: {"status": kind, "candidates": [{"path": c.get("originalPath", ""), "score": c.get("score", 0)} for c in rows]}
              for ptr, kind, rows, _elapsed, _ok in results}
    content_check = {p: {"score": scores.get(p),
                         "label": ("held-secret" if notes.get(p) == HELD_SECRET
                                   else "inconclusive" if notes.get(p) == INCONCLUSIVE
                                   else "possible" if p in possible
                                   else "confirmed" if scores.get(p, 0) >= CONFIRM_FLOOR
                                   else "dropped")}
                     for p in checked}
    tier = "none" if not top else ("possible" if top[0][1] in possible else "confirmed")
    try:  # trace detail is best-effort; it must never fail the ask
        wsearch = _STAGE.get("word") or {}
        fates = {p: "read" for p in wpaths}
        stages = {
            "cache": cache_stage,
            "routing": {ptr: {"status": kind, "none": nav_none.get(ptr, (None,))[0],
                              "secs": nav_none.get(ptr, (None, None))[1],
                              "files": [{"path": c.get("originalPath", ""), "score": c.get("score", 0),
                                         "kept": c.get("score", 0) >= ROUTE_FLOOR} for c in rows][:STAGE_LIST_CAP]}
                        for ptr, kind, rows, _elapsed, _ok in results},
            "benched": [ln for ln in error_lines if "] benched (" in ln][:STAGE_LIST_CAP],
            "word_search": {"terms": wsearch.get("terms"), "files_searched": wsearch.get("files_searched"),
                            "passed_coverage": wsearch.get("passed_coverage"),
                            "top": [{"score": sc, "path": p,
                                     "fate": fates.get(p) or ("already routed" if p in routed[:CONFIRM_FILES]
                                                              else "not read: past top %d" % FALLBACK_FILES)}
                                    for sc, p, _ptr in wsearch.get("ranked", [])]},
            "read_list": to_check[:CONFIRM_FILES + FALLBACK_FILES],
            "content_check": {p: {**(_STAGE.get("checks") or {}).get(p, {}), "verdict": v["label"]}
                              for p, v in content_check.items()},
            "tiebreak": _STAGE.get("tiebreak") or {},
            "listwise": _STAGE.get("listwise") or {},
            "person": _STAGE.get("person") or {},
            "prefilter": (_STAGE.get("prefilter") or [])[:STAGE_LIST_CAP],
            "source_moves": _STAGE.get("source_moves") or [],
            "final": [{"score": s, "path": p,
                       "rule": ("inconclusive: routing score" if notes.get(p) == INCONCLUSIVE
                                else "hub, ranked last" if p in possible and demoted(p)
                                and not (Path(p).stem.lower() == "readme" and scores.get(p, 0) >= POSSIBLE_FLOOR)
                                else "possible (word search)" if p in possible and p in wpaths
                                else "possible" if p in possible
                                else "confirmed >= %s" % CONFIRM_FLOOR)} for s, p, ptr in top],
            "cut_after_top5": [p for _s, p, _ptr in merged[5:5 + STAGE_LIST_CAP]],
        }
    except Exception as e:
        stages = {"error": type(e).__name__}
    write_trace(sdir, kind="trace", lookup_id=lookup_id, question=question, routing=routing,
                content_check=content_check,
                final_ranked=[{"score": s, "path": p, "pointer": ptr} for s, p, ptr in top],
                tier=tier, timings={"total_secs": round(time.time() - t0, 2)}, errors=error_lines,
                stages=stages)
    # Hits always print first: a pointer error must never bury a real candidate
    # from a healthy pointer under the "unresolved" summary below it.
    for s, p, ptr in top:
        note = ("  (inconclusive: content check did not finish; routing score)" if notes.get(p) == INCONCLUSIVE
                else possible.get(p, ""))
        print(f"{s:5.2f}  {p}  [{ptr}]{note}")
    for p, note in notes.items():
        if note == HELD_SECRET:
            print(f"HELD  {p}  ({HELD_SECRET})")
    for line in error_lines:
        print(line)
    if errored:
        print(f"unresolved: {errored} of {len(original_pointers)} pointers errored")
        if not top:
            print(VOICE_LINE)
            return 1
        # Partial failure: some pointers errored or are benched, but healthy
        # pointers still answered -- the failure stays visible above, it just
        # does not fail a lookup that actually has a real result.
        return 0
    if not top:
        if dropped:
            print(f"({dropped} file(s) matched the topic but did not contain the answer on reading)")
        print(f"no-candidates across {len(original_pointers)} pointers: Super Jev couldn't find it in the connected files. "
              "It may still exist: tell your human that, and offer to search by hand. To fill the gap, "
              "connect more files or record a fact with --add (see references/connectors.md).")
        print(VOICE_LINE)
        return 0
    return 0

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
    return {"approved_by": "human"}

def file_evidence(principal: str, pointer: str, question: str, answer: str, path: str,
                  sid, out: dict) -> tuple:
    """(search/assist result holding only passages from `path`, best-supporting first, or
    None, why). Search's passages are used when one comes from the file; otherwise the
    file's own reviewed lines that best match the answer are cited via assisted review,
    so a confirmed file is never refused just because search ranked another file first."""
    terms = query_terms(answer) or query_terms(question)
    support = lambda p: term_hits(terms, p.get("reviewedText") or "")
    passages = [p for p in out.get("passages") or [] if sid is not None and p.get("sourceId") == sid]
    if not any(p.get("reviewedText") for p in passages) and sid is not None and out.get("attemptId"):
        try:
            lines = Path(path).read_text(errors="replace").splitlines()
        except OSError:
            lines = []
        best = max(range(len(lines)), key=lambda i: term_hits(terms, lines[i]), default=None)
        if best is not None and term_hits(terms, lines[best]) == 0:
            return None, f"no line in {path} shares a word with the answer"
        if best is not None:
            # Like ask()'s content check (pick_chunks always reads chunk 0), the file's
            # opening lines ride along so the passage keeps its subject (a bare table row
            # "Trash | Monday..." never says which address it is for).
            refs = [{"sourceId": sid, "startLine": n, "endLine": n} for n in sorted({1, best + 1})]
            out = memory({"action": "assist", "attemptId": out["attemptId"], "principal": principal,
                          "reason": f"reviewer picked {path}; citing its own lines that state the answer.",
                          "references": refs})
            if out.get("status") == "error" and out.get("reason") == "agent assist is disabled":
                return None, ASSIST_DISABLED_HINT
            # The file may have changed since connect: the cited reviewed text must still support the answer.
            passages = [p for p in out.get("passages") or [] if p.get("sourceId") == sid]
            if not any(support(p) for p in passages):
                passages = []
    if not any(p.get("reviewedText") for p in passages):
        return None, f"no passage from {path} itself could be cited"
    return {**out, "passages": sorted(passages, key=support, reverse=True)}, None

def source_row(principal: str, pointer: str, path: str):
    """(the pointer's sources row for path or None, sources status); pages past 100."""
    offset = 0
    while True:
        listed = memory({"action": "sources", "pointer": pointer, "principal": principal,
                         "offset": offset, "limit": 100})
        if listed.get("status") != "ok":
            return None, listed.get("status")
        row = next((s for s in listed.get("sources") or [] if s.get("originalPath") == path), None)
        if row or listed.get("nextOffset") is None:
            return row, "ok"
        offset = listed["nextOffset"]

def ask_evidence(principal: str, pointer: str, question: str, answer: str, path: str, sid) -> tuple:
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
                         {"attemptId": out["attemptId"]})

def send_approval(principal: str, question: str, answer: str, pointer: str, ticket_result: dict, sdir: Path,
                  approved_by: str = "human", **fields) -> int:
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
    row, status = source_row(principal, pointer, evidence_file)
    if status != "ok":
        return not_saved(sdir, question, f"stale: pointer {pointer} is {status}")
    if not row or row.get("contentSHA") != sha256_file(Path(evidence_file)):
        return not_saved(sdir, question, f"stale: {evidence_file} changed since connect (or is not in {pointer})")
    out, why = ask_evidence(principal, pointer, question, answer, evidence_file, row.get("sourceId"))
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
        row, _ = source_row(principal, pointer, chosen["path"])
        try:
            live_sha = sha256_file(Path(chosen["path"]))
        except OSError:
            live_sha = None
        if row and row.get("contentSHA") and row["contentSHA"] != live_sha:
            print(f"cannot approve: stale: {chosen['path']} changed since connect; refresh the pointer first.")
            log(sdir, "approve", question=question, pointer=pointer, result="stale")
            return 1
        out, why = ask_evidence(principal, pointer, question, answer, chosen["path"],
                                (row or {}).get("sourceId"))
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
