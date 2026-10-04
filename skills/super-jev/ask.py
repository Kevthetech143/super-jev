#!/usr/bin/env python3
"""Front door for the super-jev harness: one cache-first lookup loop, any agent.

  ask.py --version
      Print the release (read from package.json, the one version source).

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
      errors never hide as "no-candidates". Output starts with one line,
      "OUTCOME: found | not-found | not-supported | needs-setup | error", with a
      reason and (unless found) the one next command; exit 0 / 1 / 2 / 4 / 3 in
      that order (found 0, not-found 1, not-supported 2, error 3, needs-setup 4).
      found with a failed content check says "(unconfirmed: content check
      failed)" and exits 0: the files are unconfirmed candidates, read them.

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
      Exit codes: only TRUE exits 0; FALSE 5; every other result is not a pass, and
      its code says how the search went, whatever the verdict word and whether or
      not files are listed: 1 searched fully, nothing settles it (NOT FOUND, UNSURE,
      PARTIAL, CONFLICT); 3 a set or the content check failed (or "UNSURE: the
      true/false check did not run"); 4 setup needed (a set is stale or unprepared,
      or files were skipped or held); 2 refused input (empty, too long, holds a
      secret). Several statements exit with the highest code. A statement that ends
      before any verdict prints the OUTCOME line an ordinary ask prints, first.
      Only exit 0 passes.

  ask.py --principal AGENT --json REQUEST
      For a program (the terminal app) instead of a person. REQUEST is a question, one
      --claim "statement", --status, --approve or --miss; any other mode (--add,
      --followup, --preflight, --trace-*) is refused (exit 2) before any work. It does
      the same work, prints exactly one JSON line (schema "v": 1; empty fields left
      out; no scores) and exits with text mode's code; a crash exits 3. An ask or claim:
      outcome, why, next (setup|key|connect|refresh|include|rephrase|none), files
      [{path, tier, line?, text?}], skills, skills_off, saved, saved_now, unsearched,
      left_out, errors, claim. --status: {next, principal, sets}, or {outcome: error,
      why} when it could not be read. --approve / --miss: {done, why, removed?}.
      --json is read only as the first word of the request, so after "--" or inside a
      question it is just text.

SAVED ANSWERS (one promise). A repeat question saves itself; --approve and --add
save by hand. All save through one function and run the secret scan. A repeat-question
save stores the file, and its N wins stand in for the claim check; only --approve and
--add run the claim check (CLEAN, score 0.80 or higher). --add with no file gets the secret scan only and is
marked "no source file"; a --source that does not exist is refused (exit 2). A saved
answer lasts until its source file changes (no clock expiry); a fact with no file lasts
until --miss (wrong) removes it. "The same question" = lowercased, spaces collapsed,
trailing punctuation dropped; nothing fuzzier. A hit prints "saved answer, from FILE,
saved DATE" (or "no source file"); a changed source says STALE and searches live.

  ask.py --principal AGENT --approve "question" ["answer"] [--rank N | --file PATH]
      With no answer text the person vouches for the last lookup's ranked list (up to
      five files): it is saved as a repeat win saves it, approved_by principal:AGENT,
      the secret scan and unchanged-file check run on every file, no claim check; a list with a
      possible-tier file is refused. An empty answer counts as none. --rank N or --file
      puts that file first. With an answer:
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
      text) so the answer is still cached. Never replaces a pointer or
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

REPEAT QUESTIONS (the one rule for saving a file). When the same file wins the same
question (same words after lowercasing, collapsing spaces and dropping trailing
punctuation) for the same principal N times in a row and passes the content check, ask
saves it automatically: it prints "Saved for next time", and the next ask of that
question returns the whole ranked list of the winning search (up to 5 files, in rank order,
labelled "saved answer, from FILE"), with no Jev call and no answer text: the caller opens the
files. If any listed file changed or cannot be read, the saved answer is withheld as STALE. N is a setting:
SUPERJEV_SAVE_AFTER, default 2. Only an ordinary ask counts (not --claim, not a
possible-tier file, and only a complete search: a partial one records no win and does not
reset the count). The N wins are the evidence (each already passed the content check),
so the save adds no claim check; the secret scan and unchanged-file check still run. A
changed source is withheld as STALE and searched live (and can win its way back in).
--miss removes a saved file and starts the count over. A person can meet the threshold at
once with --approve. Off with --no-auto or SUPERJEV_AUTO_CACHE=0.

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
      The saved answer is wrong: logs it and removes the saved answer (human or
      auto-check, or a --add note) so the next ask looks it up fresh. Exits 1
      with "no saved answer" when there was nothing to remove.

Cache hits print who saved the answer: the caller principal for --approve and --add
(principal:NAME) or "approved_by: auto-save" (repeat wins), from $STATE/approvals.jsonl.

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
with their ranked lists -- the input for weekly tuning. It also prints one line per
principal: how many searches saved answers skipped in that window ("--days 7" = weekly).
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
import hashlib
import io
import math
import json
import os
import re
import shlex
import sqlite3
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
                          payload_has_secret, path_has_secret, redact_path_secrets, clean_text)
import auto_heal  # noqa: E402
import judges  # noqa: E402
import toc_search  # noqa: E402
import judge_profile  # noqa: E402
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
    from dispatch import state_root
    return state_root() / principal

class SecretHeld(RuntimeError):
    """A request carried a secret, so it was never sent. A claim catches it in lookup() and exits 2
    with an OUTCOME line; an ordinary ask lets it reach main(), which exits 1 (a known gap, a follow-up)."""


ENGINE_PATHS: Counter = Counter()  # which way memory() ran: "inprocess" or "subprocess" (shown in the trace)
_ENGINE_LOCK = threading.Lock()  # guards only the one-time import, never a call
_ENGINE_MODULES: dict = {}  # engine dir -> cli module (imported once per process)


def _engine_target():
    """(repo, config path) the subprocess chain would end up running cli.py with, or None when
    that cannot be worked out here. Same resolution as dispatch.command: the installed memory.sh
    wrapper (fixed repo = two levels up, one fixed --config) unless SUPERJEV_REPO is set."""
    skill_dir = Path(__file__).resolve().parent
    wrapper = skill_dir / "memory.sh"
    if (not os.environ.get("SUPERJEV_REPO") and not os.environ.get("SUPERJEV_STATE_DIR")
            and not os.environ.get("SUPERJEV_MEMORY_WRAPPER_ACTIVE") and wrapper.is_file()):
        found = re.search(r"--config\s+(\S+)", wrapper.read_text())
        if not found:
            return None
        return skill_dir.parents[1], Path(found.group(1).strip("\"'"))
    repo = Path(os.environ["SUPERJEV_REPO"]) if os.environ.get("SUPERJEV_REPO") else skill_dir.parents[1]
    import dispatch
    return repo, dispatch.config_path()


def _engine_module(repo: Path):
    exp = repo / "experiments" / "verified-pointer-memory"
    cli = exp / "cli.py"
    if not cli.is_file():
        return None
    key = str(exp)
    if key not in _ENGINE_MODULES:
        with _ENGINE_LOCK:  # one-time import only; never held across a call
            if key not in _ENGINE_MODULES:
                import importlib.util
                if key not in sys.path:
                    sys.path.insert(0, key)  # cli.py does "from service import ..."
                spec = importlib.util.spec_from_file_location("superjev_engine_cli", cli)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                os.umask(0o077)  # what cli.main sets; set once here, process-wide, no per-call swap
                _ENGINE_MODULES[key] = mod
    return _ENGINE_MODULES[key]


def _memory_inprocess(req: dict):
    """The engine's answer to req, run in this process: the same steps cli.main takes for
    --input (load config, run, hints, nextAction), no interpreter chain. None when it cannot be
    used (not importable, no config, a connect) so the caller falls back to the subprocess."""
    if req.get("action") == "connect":
        return None
    target = _engine_target()
    if not target or not target[1].is_file():
        return None
    try:
        cli = _engine_module(target[0])
    except Exception:
        return None
    if cli is None:
        return None
    request = json.loads(json.dumps(req))  # what the subprocess would read from stdin
    try:
        result = cli.run(request, cli.load_config(target[1]))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        result = {"status": "error", "reason": str(error) if isinstance(error, ValueError)
                  and not isinstance(error, json.JSONDecodeError) else type(error).__name__}
    except Exception as error:  # the subprocess would print a traceback and ask.py reads "error"
        return {"status": "error", "raw": f"{type(error).__name__}: {error}"[-300:]}
    cli.add_hints(result)
    result.setdefault("nextAction", cli.NEXT.get(result["status"], "record-unresolved"))
    if "message" in result:
        result = {"message": result.pop("message"), **result}
    return json.loads(json.dumps(result))


def memory(req: dict) -> dict:
    # Every memory request (navigate, search, cached, add ...) goes through here: one scan.
    if payload_has_secret(req):
        raise SecretHeld("memory request contains a secret; not sent")
    out = _memory_inprocess(req)
    if out is not None:
        ENGINE_PATHS["inprocess"] += 1
        return out
    ENGINE_PATHS["subprocess"] += 1
    r = subprocess.run([sys.executable, str(SKILL), "memory", "--input", "/dev/stdin"], input=json.dumps(req), capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"status": "error", "raw": (r.stdout + r.stderr)[-300:]}

def uncalibrated_why():
    """Why nothing may be saved or approved under the active judge, or None. A judge whose lines
    were not measured may answer, but its answers never become saved ones."""
    prof = judges.profile()
    if prof.calibrated:
        return None
    return (f"the {prof.name} judge is uncalibrated (its pass line is not measured), so it never saves "
            "or approves; use the calibrated judge to save")

def log(sdir: Path, kind: str, **fields) -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    # Not redacted/truncated: lookups.jsonl is the working index find_top
    # read back by exact question, pointer and on-disk path. The save check (auto-save,
    # --approve) depends on it.
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
        if fields.get("kind") == "trace" and ENGINE_PATHS:
            fields = {**fields, "engine": dict(ENGINE_PATHS)}  # which engine path ran: inprocess / subprocess
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
        if rec.get("kind") == "trace" and norm_q(rec.get("question") or "") == norm_q(question):
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

def trace_report(sdir: Path, days=None, principal: str = "") -> int:
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
    skipped = sum(1 for tr in traces.values() if tr.get("tier") == "cache")
    print(f"searches skipped by saved answers ({f'last {days} days' if days else 'all time'}"
          f"{f', principal {principal}' if principal else ''}): {skipped}")
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

def norm_q(question: str) -> str:
    """The saved-answer matching key: lowercase, whitespace collapsed, trailing punctuation
    dropped. Nothing fuzzier -- a reworded question is a different question."""
    return " ".join(question.lower().split()).rstrip(" .,;:!?")

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
    """This question's manual record: named from the matching key, else (a note saved before
    matching was normalised) from the exact wording."""
    for q in (norm_q(question), question):
        path = sdir / "manual" / f"{manual_pointer_name(principal, q)}.md"
        if path.is_file():
            return path
    return sdir / "manual" / f"{manual_pointer_name(principal, norm_q(question))}.md"

def changed_saved_source(rec, record: Path):
    """The source file of a saved answer that changed (or vanished) since it was saved, else None.
    Checks the save's own record, and the manual note's header for older --add saves."""
    if rec and rec.get("file") and rec.get("source_sha"):
        p = Path(rec["file"])
        try:  # a file the asker cannot read is never a current answer
            if not p.is_file() or sha256_file(p) != rec["source_sha"]:
                return rec["file"]
        except OSError:
            return rec["file"]
    for f in (rec or {}).get("files") or []:  # a repeat win saved the whole ranked list
        try:
            if sha256_file(Path(f["path"])) != f["sha"]:
                return f["path"]
        except OSError:
            return f["path"]
    return changed_source(record)

def _lost_pointers(principal: str, rec: dict) -> list:
    """Pointers of a saved list's files that the principal no longer has (a local panel call)."""
    have = {(p.get("pointer") if isinstance(p, dict) else p) for p in memory({"action": "panel", "principal": principal}).get("pointers", [])}
    return sorted({f["pointer"] for f in rec.get("files") or []} - have)

# One result per ask feeds both printers (contract C1): an ask's OUTCOME line, files and skills and its
# `--json` object are made from the same _RESULT, so they cannot drift. A claim verdict and --status are
# recorded beside their own text lines (claim_result, fold_status), and a test holds the two together.
# Every ordinary ask's text starts with one OUTCOME line, computed from the whole search state, and its
# exit code is the outcome's. A claim check prints its verdict first and exits by it (TRUE 0, FALSE 5, a
# check that did not run 3); any other claim result exits by the search outcome's code (1 searched fully,
# 3 a set or the check failed, 4 setup needed), listed files or not, and never 0. A claim that ends before
# any verdict prints the same OUTCOME line, first, and exits by it. The JSON has no scores on purpose
# (schema v: 1; see run_json).
OUTCOME_EXIT = {"found": 0, "not-found": 1, "not-supported": 2, "error": 3, "needs-setup": 4}
CLAIM_EXIT = {"TRUE": 0, "FALSE": 5, "NOT RUN": 3}
_RESULT = {}
_FILE_PRIVATE = ("score", "pointer")  # a file row's own bookkeeping; never in the JSON
UNCHECKED_NOTE = "  (inconclusive: content check did not finish; routing score)"
JUDGE_KINDS = {k.kind for k in judges.ERROR_KINDS}  # the wire names a navigate row may carry as `kind`
KEY_KINDS = {"no-key", "auth-rejected"}  # a failure only a new key fixes: `next: key`


def _done(kind: str, why: str, cmd: str = "", next: str = "none") -> int:
    """Record an ask's outcome. `why` and the text-mode command `cmd` make the OUTCOME line; `next` is the
    same step as one word for --json (setup, key, connect, refresh, include, rephrase or none)."""
    _RESULT.update(outcome=kind, why=why, cmd=cmd, next=next)
    return OUTCOME_EXIT[kind]


def say(text: str, why: str = None) -> None:
    """Print a line and record its reason for --json (approve and miss). `why` is the reason in plain
    words when the line itself carries a score or a command-line flag; otherwise the line is the reason."""
    print(text)
    _RESULT["why"] = why or text


def line_text(path: str, n: int):
    """File line n (1-based) cut to 160 characters, or None when it cannot be read or looks like a
    secret: a line that looks like a secret is never quoted."""
    try:
        line = (clean_text(Path(path).read_text(errors="replace"), Path(path)) or "").splitlines()[n - 1].strip()
    except (OSError, IndexError):
        return None
    return line[:160] if line and not has_secret(line) else None


def file_row(score, path: str, pointer: str, tier: str) -> dict:
    """One ranked file (tier: confirmed, possible or unchecked). `line` is the file line the content check
    judged best and `text` that line; text mode prints neither."""
    row = {"path": path, "tier": tier, "score": score, "pointer": pointer}
    loc = ((_STAGE.get("checks") or {}).get(path) or {}).get("location")
    if loc:
        row["location"] = loc
    n = ((_STAGE.get("checks") or {}).get(path) or {}).get("best_line")
    if isinstance(n, int) and n > 0:
        row["line"] = n
        if text := line_text(path, n):
            row["text"] = text
    return row


def show_file(f: dict) -> None:
    note = {"possible": POSSIBLE_NOTE, "unchecked": UNCHECKED_NOTE}.get(f["tier"], "")
    score = f"{f['score']:5.2f}" if isinstance(f.get("score"), (int, float)) else "saved"
    loc = f.get("location") or {}
    where = f"{f['path']}:{loc['start']}-{loc['end']}" if loc.get("unit") == "lines" else f['path']
    print(f"{score}  {where}  [{f['pointer']}]{note}")


def unsearched_rows(ptrs, state_of: dict, healing: set) -> list:
    """The sets this ask did not fully search, one row per base set (a split part folds into its base)."""
    rows = {}
    for ptr in ptrs:
        report, owner = auto_heal._report_for(ptr, prepare_bulk.CACHE_DIR)
        row = rows.setdefault(owner or ptr, {"set": owner or ptr, "state": state_of.get(ptr, "failed"), "healing": False})
        if report and report.get("roots"):
            row["root"] = report["roots"][0]
        row["healing"] = row["healing"] or ptr in healing
    return list(rows.values())


def error_rows(ptrs, kinds: dict) -> list:
    """One {set, kind} per failed set. A kind is kept only if the navigate row carried a judge's name for it;
    today's navigation does not, so it is `unknown` (the app then shows the plain error)."""
    names = ((auto_heal._report_for(ptr, prepare_bulk.CACHE_DIR)[1] or ptr,
              kinds.get(ptr) if kinds.get(ptr) in JUDGE_KINDS else "unknown") for ptr in ptrs)
    return [{"set": n, "kind": k} for n, k in dict.fromkeys(names)]


def left_out_rows(skipped, held) -> list:
    """Files not searched, grouped by reason: {what, count, where, way_in}. `skipped` is (path, why, fix)
    rows from setup; `held` is files the content check withheld for holding a secret."""
    groups = {}
    for path, what, fix in [*skipped, *((p, SECRET_WHAT, SECRET_FIX) for p in held)]:
        groups.setdefault((what, fix), {})[path] = None
    return [{"what": what, "count": len(ps), "where": os.path.commonpath([os.path.dirname(p) for p in ps]), "way_in": fix}
            for (what, fix), ps in groups.items()]


def skill_rows(pairs) -> list:
    """[(name, SKILL.md path)] from the catalog, where a guess carries the GUESS suffix, as rows."""
    return [{"name": n.removesuffix(" " + GUESS), "path": p, "guess": n.endswith(GUESS)} for n, p in pairs]


def show_skill(s: dict) -> None:
    name = s["name"] + (" " + GUESS if s["guess"] else "")
    print(f"skill  {s['path']}  [skills: {name}]{skill_note(name)}")


def print_hit(hit: dict, sdir: Path, principal: str, question: str) -> int:
    # The harness's evidence "path" is always its own internal prepared-copy path,
    # never the caller's file -- so staleness is checked against the file recorded when
    # the answer was saved (approvals.jsonl), and a manual note's own header.
    rec = saved_record(sdir, question)
    stale_source = changed_saved_source(rec, manual_record_path(sdir, principal, question))
    if stale_source:
        print(f"STALE: source changed since this answer was recorded ({stale_source}); answer withheld. "
              "It is saved again on its own once the file wins this question again "
              f"{save_after()} times (or --approve it now).")
        return 1
    gone = _lost_pointers(principal, rec) if (rec or {}).get("files") else []
    if gone:
        print(f"STALE: this principal no longer has {', '.join(gone)} connected; answer withheld. "
              "It is saved again on its own once the files win this question again "
              f"{save_after()} times (or --approve it now).")
        return 1
    print("CACHE HIT")
    who = approver(sdir, question)
    when = rec["ts"][:10] if rec and rec.get("ts") else "date not recorded"
    auto = who.get("approved_by") == "auto-save"
    listed = bool(rec and rec.get("file") and (auto or rec.get("files")))  # a saved file list, not an answer
    _RESULT["saved"] = {"by": "auto" if auto else "you", **({"date": when} if rec and rec.get("ts") else {})}
    if listed:
        # A repeat win or a no-text --approve saved the ranked files, not an answer: print them like a found result.
        _RESULT["skills"] = skill_rows((s["name"], s["path"]) for s in rec.get("skills") or [])
        _RESULT["files"] = [{"path": f["path"], "tier": "confirmed", "score": f.get("score"), "pointer": f["pointer"]}
                            for f in rec.get("files") or [{"path": rec["file"], "pointer": rec.get("pointer", "")}]]
        for s in _RESULT["skills"]:
            show_skill(s)
        for f in _RESULT["files"]:
            show_file(f)
        if rec.get("leans_none"):
            _RESULT["leans_none"] = True
            print(LEANS_NONE_NOTE)
    else:
        print("answer:", hit.get("answer") or "")
        _RESULT["saved"]["answer"] = hit.get("answer") or ""
        if rec and rec.get("file"):
            _RESULT["files"] = [{"path": rec["file"], "tier": "confirmed", "pointer": rec.get("pointer", "")}]
    print("approved_by:", who.get("approved_by", "human") + (
        f" (evidence {who['evidence_file']}, score {who['score']:.2f})" if who.get("evidence_file") else ""))
    origin = (f"from {rec['file']}" if rec and rec.get("file")
              else "no source file" if rec and rec.get("no_source") else "source not recorded")
    print(f"saved answer, {origin}, saved {when}")
    for e in ([] if listed else (hit.get("evidence") or []))[:3]:
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
# preparation-required / refresh-required is a STALE pointer (needs a refresh run),
# not a live failure of the provider -- benching it hides its real, current facts
# behind a generic "benched" message instead of the honest "still preparing" one
# (post-#133 regression: one team's brain-root pointer sat preparation-required
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
# Judge calibration (measured per judge): read from the profile, judge_profiles.json.
ROUTE_FLOOR, CONFIRM_FLOOR = judges.profile().route_floor, judges.profile().confirm_floor
SOURCE_FLOOR = judges.profile().source_floor
CLAIM_CONTENT_FLOOR = judges.profile().claim_content_floor
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

def is_network_error(text, kind=None) -> bool:
    """A TypeSafe call that never got an answer (navigation-cli words it "could not reach TypeSafe (network)")."""
    return kind == "unreachable" or "(network)" in str(text or "")

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
    # The file may have changed since connect scanned it; never ship a secret to Jev. A secret-shaped
    # section is withheld (its lines blanked, line numbers kept); a file that cannot be made clean is held.
    text = clean_text(text, path)
    if text is None:
        return (None, False, None, HELD_SECRET), None
    tparts = (_STAGE.get("toc_parts") or {}).get(path) or []
    # A short file is read whole; a long one as the passages the word overlap picks. Either way the named
    # parts the TOC search chose ride along, each with its line range, so the best one carries a location
    # (a whole file alone could only point at its first lines).
    chunks = [text] if len(text) <= WHOLE_FILE_CHARS else split_passages(text)
    tlines = text.split("\n")
    extra = [toc_search.part_text(tlines, s_, e_) for _n, s_, e_ in tparts]
    partial = False  # long files are judged on chosen passages, never passed through unread
    label = confirm_label(question)
    base = len(chunks)
    picked = pick_chunks(question, chunks) + list(range(base, base + len(extra)))
    chunks = chunks + extra
    detail = _STAGE.setdefault("checks", {})[path] = {
        "chunks": len(chunks), "read": picked[:STAGE_LIST_CAP],
        "wording": "claim-evidence" if _CLAIM["text"] else "source-evidence"}
    leaves = [{"id": f"c{i}", "label": label.format(n=i + 1),
               "description": (f"{tparts[i - base][0]} (lines {tparts[i - base][1]}-{tparts[i - base][2]} of "
                               f"{Path(path).name})\n{chunks[i]}" if i >= base else with_subject(chunks, i)),
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
                  "detail": detail, "partial": partial, "tparts": tparts, "base": base}

TIE_MARGIN = 0.02  # candidate scores this close to the top are a tie


def tightest_near_top(top_c, candidates, chunks: list, tparts: list, base: int):
    """The candidate to locate the answer: among those scoring within TIE_MARGIN of the top one, the one
    spanning the fewest lines (a named part, not the header passage that merely describes it). The judge
    scores a file's opening passage and the function it describes alike, and the first listed won the tie."""
    def span(c):
        i = int(c["sourceId"])
        if base <= i < base + len(tparts):
            return tparts[i - base][2] - tparts[i - base][1] + 1
        return chunks[i].count("\n") + 1 if i < len(chunks) else 10 ** 9
    if top_c is None:
        return None
    near = [c for c in candidates if isinstance(c, dict) and isinstance(c.get("score"), (int, float))
            and str(c.get("sourceId", "")).isdigit() and c["score"] >= top_c["score"] - TIE_MARGIN]
    return min(near, key=lambda c: (span(c), -c["score"])) if near else top_c


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
    top_c = tightest_near_top(top_c, body.get("candidates"), ctx.get("chunks") or [], ctx.get("tparts") or [],
                              ctx.get("base", len(chunks)))
    if top_c and str(top_c.get("sourceId", "")).isdigit():
        detail["best_chunk"] = int(top_c["sourceId"])
        i_ = int(top_c["sourceId"])
        tp_, base_ = ctx.get("tparts") or [], ctx.get("base", len(chunks))
        if base_ <= i_ < base_ + len(tp_):
            name_, s_, e_ = tp_[i_ - base_]
            detail["location"] = {"unit": "lines", "start": s_, "end": e_, "part": name_}
            detail["best_line"] = s_ + best_line(question, [chunks[i_]], 0) - 1
        elif i_ < len(chunks):
            detail["best_line"] = best_line(question, chunks, i_)
            # name the TOC part the best line sits in, when the TOC search listed one
            home = [(n_, s_, e_) for n_, s_, e_ in tp_ if s_ <= detail["best_line"] <= e_]
            if home:
                n_, s_, e_ = min(home, key=lambda x: x[2] - x[1])
                detail["location"] = {"unit": "lines", "start": s_, "end": e_, "part": n_}
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
# summary sibling) -- ranking alone is often a coin flip: stress
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
        text = clean_text(text, p)
        if text is None:
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
LISTWISE_PROMOTE_FLOOR = judges.profile().sure_line
LISTWISE_NONE = "none"
LEANS_NONE_NOTE = "(Jev leans none of these: read the files before answering; the answer may not be here)"
LISTWISE_INSTRUCTIONS = "Question: %s\nWhich file states the answer? When torn, pick none."

# Claim mode (ask.py --claim "statement"): is a statement true, by our own files?
# The same listwise Jev call also asks, for each file it shows, whether that file
# supports, partly supports, contradicts or does not state the statement, and which
# line of it says so (picked from the file's own lines, never written). No extra
# call. Plain code then combines the per-file answers (claim_verdict): only answers
# at >= CLAIM_SURE count; files that disagree are a CONFLICT, newest first.
CLAIM_SURE = judges.profile().sure_line
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
_CLAIM = {"text": None, "word": None}

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

CLAIM_SAYS = {"supported": "TRUE", "contradicted": "FALSE", "partly": "PARTLY"}

def file_day(p: str) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(_file_date(p))) if _file_date(p) else ""

def claim_file(p: str, f: dict, proof: bool = False) -> dict:
    """A file a claim verdict names, for --json: what it says (or, as the proof, the line quoted),
    the line the judge picked and the file's date. A line that looks like a secret is never quoted."""
    row = {"path": p} if proof else {"path": p, "says": CLAIM_SAYS.get(f["verdict"], f["verdict"])}
    if f.get("line_no"):
        row["line"] = f["line_no"]
    if proof and f.get("line") and not has_secret(f["line"]):
        row["text"] = f["line"][:160]
    if file_day(p):
        row["date"] = file_day(p)
    return row

def claim_result(word: str, files: dict, paths: list, read: int, proof: str = None) -> None:
    """Record a claim verdict for --json; only TRUE and FALSE have a proof."""
    _RESULT["claim"] = {"verdict": word, "proof": claim_file(proof, files[proof], True) if proof else None,
                        "files": [claim_file(p, files[p]) for p in paths], "read": read}

def claim_verdict(files: dict, read: list, incomplete: bool = False) -> tuple:
    """(word, lines) combining per-file answers. word is TRUE, FALSE, CONFLICT, PARTIAL,
    UNSURE or NOT FOUND. Only answers at >= CLAIM_SURE decide; files that disagree
    are a CONFLICT with the newest (by modified date) first. The same verdict is
    recorded for --json (claim_result). incomplete: a set failed or is stale, or the content
    check failed, so a NOT FOUND says the search was incomplete."""
    day = lambda p: file_day(p) or "date unknown"
    sure = {p: f for p, f in files.items() if isinstance(f.get("prob"), (int, float)) and f["prob"] >= CLAIM_SURE}
    yes = [p for p, f in sure.items() if f["verdict"] == "supported"]
    no = [p for p, f in sure.items() if f["verdict"] == "contradicted"]
    part = [p for p, f in sure.items() if f["verdict"] == "partly"]
    quote = lambda p: (f'line {files[p]["line_no"]}: "{files[p]["line"]}"' if files[p].get("line_no")
                       else f'"{files[p]["line"]}"' if files[p].get("line") else "(no single line picked)")
    if yes and no:
        both = sorted(yes + no, key=_file_date, reverse=True)
        word = "CONFLICT"
        claim_result(word, files, both, len(read))
        out = [f"CONFLICT: the files disagree; the newest says {'TRUE' if both[0] in yes else 'FALSE'}. Read both before answering."]
        out += [f"  {'TRUE ' if p in yes else 'FALSE'} {p} ({day(p)}) {quote(p)}" for p in both]
        return word, out
    if yes or no:
        side = yes or no
        word = "TRUE" if yes else "FALSE"
        best = max(side, key=lambda p: files[p]["prob"])
        claim_result(word, files, sorted(side, key=lambda p: -files[p]["prob"]), len(read), best)
        out = [f"{word} ({files[best]['prob']:.2f})", f"Proof: {best} ({day(best)})", f"  {quote(best)}"]
        also = [p for p in side if p != best]
        if also:
            out.append("Also says so: " + ", ".join(Path(p).name for p in also))
        other = [p for p in read if p not in side]
        if other:
            out.append("Other files read: " + ", ".join(Path(p).name for p in other))
        return word, out
    if part:
        claim_result("PARTIAL", files, part, len(read))
        out = ["PARTIAL: each file holds only part of it; read these before answering:"]
        out += [f"  {p} ({day(p)}) {quote(p)}" for p in part]
        return "PARTIAL", out
    leaning = sorted((p for p, f in files.items() if f["verdict"] != "not_stated"),
                     key=lambda p: -(files[p].get("prob") or 0))
    if leaning:
        claim_result("UNSURE", files, leaning, len(read))
        f0 = files[leaning[0]]
        out = [f"UNSURE ({f0['verdict']} {f0.get('prob') or 0:.2f}): read these files before answering:"]
        out += [f"  {p}" for p in leaning]
        return "UNSURE", out
    claim_result("NOT FOUND", files, [], len(read))
    gap = "the search was incomplete (a set failed or is stale, or the content check failed)"
    if not read:
        return "NOT FOUND", [f"NOT FOUND: {gap}; it may still exist in a file that was not searched." if incomplete
                             else "NOT FOUND in the connected files; it may still exist somewhere not connected."]
    return "NOT FOUND", [f"NOT FOUND in the {len(read)} file(s) I read" + (f", and {gap}" if incomplete else "")
                         + "; it may still exist in a file that was not read."]

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
    text = clean_text(text, path)
    if text is None:
        return None
    if len(text) <= WHOLE_FILE_CHARS:  # judged whole, so shown whole
        return text
    i = (_STAGE.get("checks") or {}).get(path, {}).get("best_chunk") or 0
    chunks = split_passages(text)
    return with_subject(chunks, min(i, len(chunks) - 1))

def jev_choice(state: dict, questions: dict) -> dict:
    """One judge call through the doorway (judges.ask)."""
    return judges.ask(state, questions, timeout=60)

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
            if not _CLAIM["text"] or not isinstance(e, judges.TooBig):
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

def pick_chunks(question: str, chunks: list) -> list:
    """Indexes to read, in file order: every passage if they fit in READ_CHARS, else
    the passages scoring best by BM25 on the question's words (each word weighted by
    how rare it is within this file; earlier passages win ties, so a file with few
    matches is read from its top) until READ_CHARS is spent. Counting distinct words,
    with the first chunk always read, spent the slots on the intro and on passages
    full of the file's common words, missing the one section that answered."""
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
    for i in sorted(range(len(chunks)), key=lambda i: (-score[i], i)):
        if used + cost[i] <= READ_CHARS:
            picked.append(i)
            used += cost[i]
        elif not score[i]:
            break  # no word left to match: read on from the top only, never skip ahead
    return sorted(picked)

def load_cache_files(pointer: str) -> dict:
    """Only prepare-cache/<pointer>.json. Part caches are registered as their own
    pointers (<pointer>-N) and arrive in the principal's pointer list when owned, so
    matching <pointer>-N.json here could read another principal's pointer.
    A set with no prepare-cache (a reviewed view, a manual note, a path-connected set) gets its rows
    from its reviewed sources instead (load_local_rows), so word search, the TOC and the index see it."""
    if pointer in _STAGE.get("view_pointers", ()):
        return dict(_LOCAL_ROWS.get(pointer) or {})  # A leftover bulk cache names raw originals, never reviewed views.
    p = prepare_bulk.CACHE_DIR / f"{pointer}.json"
    try:
        data = json.loads(p.read_text())
    except Exception:
        return dict(_LOCAL_ROWS.get(pointer) or {})
    return data if isinstance(data, dict) else {}

# S4: routing never asks Jev per set. Every set must be discoverable from local rows: its prepare-cache,
# its split parent's cache (a part <pointer>-N holds files the parent's cache already lists), or, for a set
# with no cache (reviewed view, manual note), rows built from the engine's reviewed sources (path, reviewed
# sha, description). Rows are kept per generation in set-rows.json, so an ask rebuilds only a changed set.
SET_ROWS_FILE = "set-rows.json"
SET_ROWS_RETRY_SECS = 600  # a set whose sources could not be listed is tried again after this long
NAV_FALLBACK_MAX = 10  # a set with no local rows at all is asked in ONE batched navigate, at most this many sets
_LOCAL_ROWS = {}  # {pointer: {path: cache-shaped entry}} for the sets of the current ask / index update

def _has_own_cache(pointer: str) -> bool:
    try:
        return (prepare_bulk.CACHE_DIR / f"{pointer}.json").stat().st_size > 2
    except OSError:
        return False

def covered_by_parent(pointer: str, all_pointers) -> bool:
    """A split part (<pointer>-N) whose parent set is visible and has a cache: the parent's rows hold its files."""
    base, _, n = pointer.rpartition("-")
    return bool(base and n.isdigit() and base in all_pointers and _has_own_cache(base))

def source_rows(principal: str, pointer: str):
    """{path: entry} from the pointer's reviewed sources (local engine call, no judge), or None when unreadable."""
    rows, offset = {}, 0
    while True:
        out = memory({"action": "sources", "pointer": pointer, "principal": principal, "offset": offset, "limit": 100})
        if not isinstance(out, dict) or out.get("status") != "ok":
            return None
        for src in out.get("sources") or []:
            # originalPath when present (a reviewed view lists its view there); else the source's own listed path
            path, sha = (src.get("originalPath") or src.get("path"), src.get("contentSHA")) if isinstance(src, dict) else (None, None)
            if isinstance(path, str) and path and isinstance(sha, str) and sha:
                rows[path] = {"pass": True, "sha256": sha, "description": str(src.get("description") or ""), "local": True}
        nxt = out.get("nextOffset")
        if not isinstance(nxt, int) or nxt <= offset:
            return rows
        offset = nxt

def load_local_rows(sdir: Path, principal: str, pointers: list, generations: dict, view_pointers=()) -> dict:
    """Fill _LOCAL_ROWS for the sets that have neither their own cache nor a covering parent. Returns {pointer: n rows}."""
    _LOCAL_ROWS.clear()
    need = [p for p in pointers if p in view_pointers or (not _has_own_cache(p) and not covered_by_parent(p, pointers))]
    if not need:
        return {}
    path = sdir / SET_ROWS_FILE
    try:
        saved = json.loads(path.read_text())
    except Exception:
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    dirty = False
    for ptr in need:
        gen, hit = generations.get(ptr), saved.get(ptr)
        if gen and isinstance(hit, dict) and hit.get("generation") == gen:
            if isinstance(hit.get("rows"), dict):
                _LOCAL_ROWS[ptr] = hit["rows"]
                continue
            if time.time() - (hit.get("failed_at") or 0) < SET_ROWS_RETRY_SECS:
                continue  # its sources could not be listed a moment ago: not listed again every ask
        try:
            got = source_rows(principal, ptr)
        except Exception:  # noqa: BLE001 -- a set whose sources cannot be listed has no local rows: the capped fallback asks
            got = None
        if got is None:
            if gen:
                saved[ptr], dirty = {"generation": gen, "rows": None, "failed_at": time.time()}, True
            continue
        _LOCAL_ROWS[ptr] = got
        if gen:
            saved[ptr], dirty = {"generation": gen, "rows": got}, True
    for ptr in [k for k in saved if k not in pointers]:
        saved.pop(ptr)
        dirty = True
    if dirty:
        try:
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(saved))
            tmp.replace(path)
        except OSError:
            pass  # best effort: the rows are rebuilt next ask
    return {p: len(r) for p, r in _LOCAL_ROWS.items()}

# A written phone number ("212-555-0100") counts as the words "phone" and "number": a note lists
# the number without ever saying "phone" (a clinic line in a timeline note).
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
    the pointer's recorded scope (a set built from reviewed sources, not a folder, is scoped by its listed files)."""
    return (isinstance(entry, dict) and bool(entry.get("pass")) and bool(entry.get("sha256"))
            and len(raw) <= prepare_bulk.CEILING_BYTES
            and clean_text(text, path) is not None
            and (bool(entry.get("local")) or refresh_would_admit(path, ptr)))  # a local row's scope is the set's listed files


def candidate_files(pointers: list, exclude=(), done=()):
    """(pointer, path, cache entry) of every file a word search would open: reviewed (`pass`), not test
    material, not in `exclude` (another person's files). A path is skipped only once the caller has indexed it
    (`done`, which the caller fills), so a set that cannot read its copy never hides a good copy in another set.
    The one list that word search and the edited-file count both start from."""
    for ptr in pointers:
        names = connector_names(ptr)
        for path, entry in load_cache_files(ptr).items():
            if (path in done or path in exclude or not isinstance(entry, dict) or not entry.get("pass")
                    or prepare_bulk.is_test_material(path, prepare_bulk.named_exactly(Path(path).name, names))):
                continue
            yield ptr, path, entry


def read_sha(path: str, reads):
    """(raw bytes, sha256 hex) of the file as it is now, or None when unreadable. `reads`, when given, is
    the one read+sha pass an ask shares between edited_held and word_search."""
    if reads is not None and path in reads:
        return reads[path]
    try:
        raw = Path(path).read_bytes()
        got = (raw, hashlib.sha256(raw).hexdigest())
    except OSError:
        got = None
    if reads is not None:
        reads[path] = got
    return got


def edited_held(pointers: list, exclude=(), reads=None) -> dict:
    """The one count of files left out because they were edited since their set's last refresh and a
    refresh would not admit them. Worked out once per searched set from the files a search would open,
    whichever search path (routing, word search, a later one) skips them. Split by what would help:
    {"secret": its current text holds a secret (held: the value must go), "stuck": too big or out of the
    set's scope (a refresh would hold it again), "refresh": a refresh will review it}."""
    done, why = set(), {}  # done: some set reads the file; why: the first set's reason it cannot
    for ptr, path, entry in candidate_files(pointers, exclude, done):
        got = read_sha(path, reads)
        if got is None:
            continue  # removed: not an edited file
        raw, sha = got
        if sha == entry.get("sha256"):
            done.add(path)  # a file is out only if no searched set reads it
            continue
        text = raw.decode("utf-8", "replace")
        if edited_readable(path, ptr, entry, raw, text):
            done.add(path)
            continue
        why.setdefault(path, "secret" if clean_text(text, path) is None else "stuck" if (
            len(raw) > prepare_bulk.CEILING_BYTES or not refresh_would_admit(path, ptr)) else "refresh")
    out = {"secret": [], "stuck": [], "refresh": []}
    for path, kind in why.items():
        if path not in done:
            out[kind].append(path)
    return out


# --- Index read path (flag-gated, default OFF): the ask reads its candidate list and pointer status from the
# per-principal file index (file_index.py) instead of hashing every file; only files it serves are sha-checked.
INDEX_FILE = "index.sqlite"
INDEX_STAMP = "index-sync.stamp"
INDEX_MAX_AGE_SECS = 24 * 3600   # an index not synced for a day is stale: today's path runs
INDEX_SPAWN_EVERY_SECS = 600     # detached updater after an ask, at most this often (sooner on a served mismatch)

def index_enabled() -> bool:
    """SUPERJEV_INDEX=1/0 wins; else the engine config key "indexRead": true. Default off."""
    v = os.environ.get("SUPERJEV_INDEX", "").strip().lower()
    if v:
        return v in ("1", "on", "true", "yes")
    try:
        target = _engine_target()
        return bool(target and json.loads(target[1].read_text()).get("indexRead") is True)
    except Exception:
        return False

def engine_visible(principal: str):
    """Names of the pointers the engine says this principal is authorized on right now (one SELECT, no snapshot,
    no hashing), or None when the engine DB cannot be read (the caller then runs today's path)."""
    try:
        target = _engine_target()
        db = _engine_module(target[0]).load_config(target[1])["db"]
        with contextlib.closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)) as c:
            return {n for n, b in c.execute("SELECT name, body FROM pointers") if principal in json.loads(b).get("principals", [])}
    except Exception:  # noqa: BLE001
        return None

def engine_generations():
    """{pointer name: generation} as the registry holds them right now (one SELECT), or None when unreadable."""
    try:
        target = _engine_target()
        db = _engine_module(target[0]).load_config(target[1])["db"]
        with contextlib.closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)) as c:
            return {n: json.loads(b).get("generation") for n, b in c.execute("SELECT name, body FROM pointers")}
    except Exception:  # noqa: BLE001
        return None

def pointer_fallbacks(idx, allowed, gens) -> dict:
    """{pointer: reason} for every pointer the engine lists that the index does not hold completely and currently.
    The one rule: the index answers only for a pointer whose indexed generation equals the registry's, that is not
    stale, whose files it holds (count > 0 when the pointer has files) and all of them; every other pointer is
    served by today's path for that pointer."""
    cov, out = idx.coverage(), {}
    for name in sorted(allowed):
        c = cov.get(name)
        if c is None:
            out[name] = "not indexed"
        elif c["stale"]:
            out[name] = "stale (files changed since its last refresh)"
        elif gens is not None and name in gens and c["generation"] != gens[name]:
            out[name] = "generation mismatch (index holds an older refresh)"
        elif c["entries"] and not c["files"]:
            out[name] = f"0 files indexed ({c['entries']} in its catalog)"
        elif c["entries"] and c["complete"] != 1:
            out[name] = "files missing from the index"
    return out

def index_panel(principal: str, sdir: Path):
    """(FileIndex, panel dict, None, fallbacks), or (None, None, why it cannot be used: missing / corrupt / empty / stale, {}).
    fallbacks {pointer: reason}: pointers served by today's path inside an ask that uses the index; the panel is then
    None, so the caller reads the registry's rows (today's statuses) for every pointer."""
    path = sdir / INDEX_FILE
    if not path.is_file():
        return None, None, "index missing", {}
    from file_index import FileIndex
    idx = None
    try:
        idx = FileIndex(principal, path)
        rows, synced = idx.panel_rows(), idx.synced_at()
        idx.db.execute("SELECT COUNT(*) FROM files").fetchone()
    except Exception as e:  # noqa: BLE001 -- sqlite3.DatabaseError and friends: today's path runs
        if idx:
            idx.close()
        return None, None, f"index corrupt ({type(e).__name__})", {}
    why = ("index empty (never synced)" if not rows or synced is None else
           f"index stale (synced {int((time.time() - synced) / 3600)}h ago)" if time.time() - synced > INDEX_MAX_AGE_SECS else "")
    allowed = None if why else engine_visible(principal)
    if not why and allowed is None:
        why = "engine pointer list unreadable"
    if why:
        idx.close()
        return None, None, why, {}
    # An unshare must show at once: a pointer the engine no longer lists for this principal is dropped, so
    # neither its files nor its status are used. A pointer new to the engine (not yet indexed) is served by today's path.
    rows = [r for r in rows if r.get("pointer") in allowed]
    try:
        fb = pointer_fallbacks(idx, allowed, engine_generations())
    except Exception as e:  # noqa: BLE001 -- an index that cannot say what it holds is not used
        idx.close()
        return None, None, f"index corrupt ({type(e).__name__})", {}
    return idx, (None if fb else {"pointers": rows}), None, fb

def index_candidates(idx, pointers, exclude=()):
    """candidate_files() from the index rows: same filters, no prepare-cache parse, no file opened."""
    names = {p: connector_names(p) for p in pointers}
    return [(ptr, path, entry) for ptr, path, entry in idx.candidates(pointers)
            if path not in exclude and not prepare_bulk.is_test_material(path, prepare_bulk.named_exactly(Path(path).name, names[ptr]))]

def index_failed(idx, error):
    """The index could not be read mid-ask (locked by the updater, damaged): close it and let today's path answer, the
    same ask. Never a lost answer. Returns None (the new idx_read)."""
    try:
        idx.close()
    except Exception:  # noqa: BLE001
        pass
    _STAGE["index"].update({"used": False, "fallback": f"index read failed ({type(error).__name__}: {str(error)[:60]})"})
    return None

def fallback_candidates(idx, ptrs, ix_ptrs, exclude=()):
    """candidate_files() for the pointers the index does not hold (today's path, from their prepare-cache), minus the files
    the index already serves under a pointer it does hold. Returns [(pointer, path, entry)]."""
    ix = set(ix_ptrs)
    return [(ptr, path, entry) for ptr, path, entry in candidate_files(ptrs, exclude)
            if idx.owner_of(path) not in ix]

def _mark_stale(idx, ptr) -> None:
    try:
        idx.mark_stale(ptr)
    except sqlite3.Error:  # the updater holds the file: the next ask marks it; the answer is not lost
        pass

def index_verify(idx, paths, entries, edited) -> tuple:
    """Verify-at-read: sha-check only the files about to be served. A match is served as indexed. A mismatch
    uses today's edited_readable rule for that file (read at its current text, or left out as edited) and marks
    its pointer for the updater. Returns (paths to serve, {"verified": n, "mismatch": [...], "gone": [...]})."""
    keep, info = [], {"verified": 0, "mismatch": [], "gone": []}
    for path in paths:
        hit = entries.get(path)
        if hit is None:
            keep.append(path)  # not an indexed file (a routed set's file): checked where it always was
            continue
        ptr, entry = hit
        got = read_sha(path, None)
        info["verified"] += 1
        if got is None:
            info["gone"].append(path); _mark_stale(idx, ptr)
            continue
        raw, sha = got
        if sha == entry.get("sha256"):
            keep.append(path)
            continue
        info["mismatch"].append(path); _mark_stale(idx, ptr)
        text = raw.decode("utf-8", "replace")
        if edited_readable(path, ptr, entry, raw, text):
            keep.append(path)
            _STAGE.setdefault("stale_changed", []).append(path)
        else:
            edited["secret" if clean_text(text, path) is None else "stuck" if (
                len(raw) > prepare_bulk.CEILING_BYTES or not refresh_would_admit(path, ptr)) else "refresh"].append(path)
    return keep, info

def spawn_index_updater(principal: str) -> None:
    """The updater, detached: never on the ask's clock. Failure to start is not an ask failure."""
    try:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--principal", principal, "--index-update"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception:  # noqa: BLE001
        pass

def index_after_ask(principal: str, sdir: Path) -> None:
    """After an ask run with the flag on: start the updater if a served file mismatched, the index was unusable,
    or the last start is older than INDEX_SPAWN_EVERY_SECS. One stamp file throttles it (a mismatch skips the wait)."""
    st = _STAGE.get("index")
    if not st:
        return
    stamp = sdir / INDEX_STAMP
    try:
        age = time.time() - stamp.stat().st_mtime
    except OSError:
        age = None
    if age is not None and age < INDEX_SPAWN_EVERY_SECS and not st.get("mismatch"):
        return
    try:
        sdir.mkdir(parents=True, exist_ok=True)
        stamp.touch()
    except OSError:
        return
    spawn_index_updater(principal)

FTS_TF_CAP = 4  # most times a word is repeated in a file's FTS body (bm25 saturates anyway)
FTS_K = 200  # files per shortlist (word bm25, TOC): a constant, so the re-score is O(K) at any corpus size

def _label_key(entry: dict) -> str:
    return hashlib.sha256(f"{entry.get('description') or ''}\0{entry.get('question') or ''}".encode()).hexdigest()[:16]

def index_fts_pass(idx, pointers: list, widx: dict, toc) -> int:
    """Updater only: keep the FTS5 table in step with the `files` rows. Only a file that is new, whose sha, pointer or
    labels changed, is written; one that left the index is dropped. Marked ok only when every file got its rows.
    Returns the number of files written."""
    if idx.fts_error:
        return 0
    idx.fts_begin()
    have, keep, wrote, complete = idx.fts_have(), set(), 0, True
    edited = {p for _ptr, p, _e in idx.edited_candidates(pointers)}
    for ptr, path, entry in index_candidates(idx, pointers):
        if path in edited:
            continue
        keep.add(path)
        sha, item = entry.get("sha256"), widx.get(path)
        if not _valid_item(item, sha):
            complete = False  # bytes changed under the updater: the next run builds it
            continue
        lab = _label_key(entry)
        if have.get(path) == (sha, ptr, lab):
            continue
        row = toc.rows.get(path)
        trow = row if row and row.get("sha256") == sha else None
        head = " ".join([path.replace("/", " ").replace("-", " ").replace("_", " "), item["heading"],
                         str(entry.get("description") or ""), str(entry.get("question") or "")])
        hw = Counter(w for w in words(head) for _ in range(3))
        htotal = sum(hw.values())
        tf = Counter(item["whole"]) + hw  # head words count triple, as the scorer counts them
        body = " ".join(w for w, c in sorted(tf.items()) for _ in range(min(c, FTS_TF_CAP)))  # repeats so bm25 sees term frequency
        tocw = " ".join(sorted(set(words(toc_search.toc_words(path, entry, (trow or {}).get("toc") or {})))))
        idx.fts_put(path, ptr, person_of(path), sha, lab, body, tocw, len(item["passages"]),
                    sum(c[1] + htotal for c in item["passages"]), json.dumps(item), json.dumps(trow) if trow else None)
        wrote += 1
    for path in set(have) - keep:
        idx.fts_drop(path)
        wrote += 1
    if complete:
        idx.fts_finish(WORD_INDEX_VERSION)
    else:
        idx.db.commit()
    return wrote

def _fts_q(tokens) -> str:
    return " OR ".join(f'"{t}"' for t in sorted(tokens))

def fts_pick(idx, question: str, pointers: list, who, out_of_scope_fn):
    """The O(K) read path: (candidates [(ptr, path, entry)], items {path: word item}, toc rows, fts numbers for
    word_search, trace), or (None, why) to run the S3a path. Takes the top FTS_K files by bm25 on the question's
    words and the top FTS_K by bm25 on the TOC words, plus the few edited files; nothing else is touched."""
    ok, why = idx.fts_usable(WORD_INDEX_VERSION)
    if not ok:
        return None, why
    terms = query_terms(question)
    if not terms:
        return None, "no question words"
    try:
        who = sorted(who)
        variants = idx.fts_variants(terms, SYNONYMS)
        df = {t: idx.fts_df("body : (" + _fts_q(v) + ")", pointers, who) if v else 0 for t, v in variants.items()}
        allv = set().union(*variants.values())
        wtop = idx.fts_top("body : (" + _fts_q(allv) + ")", pointers, FTS_K, who) if allv else []
        tq = {f"{t[:5] if len(t) > 5 else t}*" for t in terms}
        ttop = idx.fts_top("toc : (" + " OR ".join(f'"{q[:-1]}"*' for q in sorted(tq)) + ")", pointers, FTS_K, who)
        if len(ttop) < FTS_K:  # the TOC shortlist ties fall back to path order: pad with the first paths, as it does
            ttop += [p for p in idx.fts_first_paths(pointers, FTS_K, who) if p not in set(ttop)][:FTS_K - len(ttop)]
        stats = idx.fts_stats_for(pointers, who)
        rows = idx.fts_rows(list(dict.fromkeys(wtop + ttop)))
    except sqlite3.Error as e:
        return None, f"fts query failed ({type(e).__name__})"
    names = {p: connector_names(p) for p in pointers}
    cands, items, trows = [], {}, {}
    for ptr, path, entry, item, trow in rows:
        if out_of_scope_fn(path) or prepare_bulk.is_test_material(path, prepare_bulk.named_exactly(Path(path).name, names[ptr])):
            continue
        cands.append((ptr, path, entry))
        if item:
            items[path] = item
        if trow:
            trows[path] = trow
    edited = [c for c in idx.edited_candidates(pointers) if not out_of_scope_fn(c[1])]
    cands += edited
    fts = {"n": stats["n"], "passages": stats["passages"], "size": stats["size"], "df": df, "variants": variants,
           "local": {c[1] for c in edited}}
    return (cands, items, trows, fts,
            {"used": True, "k": FTS_K, "word_candidates": len(wtop), "toc_candidates": len(ttop), "edited": len(edited),
             "corpus_files": stats["n"]}), None

def index_sync(principal: str, sdir: Path) -> int:
    """The updater (connect, refresh, detached after an ask): list the pointers from the registry once, stat-diff
    every indexed file (sha only what changed), and build the word-index items for new bytes. The only O(files) work."""
    from file_index import FileIndex
    panel = memory({"action": "panel", "principal": principal})
    rows = [r for r in panel.get("pointers", []) if isinstance(r, dict) and r.get("pointer")] if isinstance(panel, dict) else []
    if not rows:
        say(f"index update skipped: no pointer list ({panel.get('reason') or panel.get('status') or 'empty'})" if isinstance(panel, dict) else "index update skipped")
        return 1
    idx = FileIndex(principal, sdir / INDEX_FILE)
    hashed, expected = 0, {}
    views = {r["pointer"] for r in rows if r.get("viewOriginals")}
    _STAGE["view_pointers"] = sorted(views)
    load_local_rows(sdir, principal, [r["pointer"] for r in rows], {r["pointer"]: r.get("generation") for r in rows}, views)
    for r in rows:
        ptr = r["pointer"]
        entries = load_cache_files(ptr)
        if not entries:
            continue
        expected[ptr] = [p for p, e in entries.items() if isinstance(e, dict) and e.get("pass") and os.path.isfile(p)]
        fresh = not str(r.get("snapshotStatus") or r.get("status") or "").startswith(("preparation-required", "refresh-required"))
        if fresh and r.get("generation") is not None and idx.generation_of(ptr) not in (None, r.get("generation")):
            idx.purge(ptr)  # the pointer was refreshed since: re-seed it from its new prepare-cache
        roots = (auto_heal._report_for(ptr, prepare_bulk.CACHE_DIR)[0] or {}).get("roots")
        hashed += idx.update(ptr, entries=entries, roots=roots)["hashed"]
    idx.set_panel(rows)
    idx.set_complete(expected)  # after every pointer is updated: a path shared by two pointers is held by the last one
    wpath = sdir / WORD_INDEX_FILE
    widx, dirty = _load_word_index(wpath), False
    cands = list(idx.candidates([r["pointer"] for r in rows]))
    dirty = _prune_word_index(widx, {p for _ptr, p, _e in cands})
    for ptr, path, entry in cands:
        if _valid_item(widx.get(path), entry.get("sha256")):
            continue
        got = read_sha(path, None)
        if got and got[1] == entry.get("sha256"):
            widx[path] = _index_item(got[0].decode("utf-8", "replace"), got[1], True)
            dirty = True
    if dirty:
        _save_word_index(wpath, widx)
    # TOC pages of new bytes, so the ask's TOC search finds them cached (it reads a file only on a miss).
    toc = toc_search.TocCache(sdir / "toc-cache.json")
    allc = idx.candidates([r["pointer"] for r in rows])
    for ptr, path, entry in allc:
        row = toc.rows.get(path)
        if row and row.get("sha256") == entry.get("sha256"):
            continue
        got = read_sha(path, None)
        if got and got[1] == entry.get("sha256"):
            toc.get(path, got[1], lambda _p, raw=got[0]: clean_text(raw.decode("utf-8", "replace"), path))
    toc.save({p for _ptr, p, _e in allc})
    fts_changed = index_fts_pass(idx, [r["pointer"] for r in rows], widx, toc)
    idx.close()
    print(f"index updated: {len(rows)} pointer(s), {hashed} file(s) hashed, {fts_changed} fts file(s) written")
    return 0

# Per-file word index: each file's passage word counts (without the head words, which depend on the
# pointer entry and are added at ask time), passage sizes and 4-letter pair keys, kept per path and
# valid only for the sha it was built from. Only text that passed word_search's gate is ever indexed.
WORD_INDEX_FILE = "word-index.json"
# The stamp covers everything that decides a stored token: tokenizer version and pattern, stopwords
# (they shape the pair keys), passage size. Any change makes old entries invalid.
def _word_index_version() -> str:
    return "{}.1.{}.{}".format(WORDS_VERSION, CONFIRM_CHUNK, hashlib.sha256(
        json.dumps([WORD_RE.pattern, sorted(QUERY_STOPWORDS)]).encode()).hexdigest()[:12])

WORD_INDEX_VERSION = _word_index_version()

def _prune_word_index(widx: dict, keep) -> bool:
    """Drop entries for paths no longer in `keep` (left every pointer, or deleted). True when any went."""
    gone = [p for p in widx if p not in keep]
    for p in gone:
        del widx[p]
    return bool(gone)

def _load_word_index(path) -> dict:
    try:
        saved = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(saved, dict) or saved.get("version") != WORD_INDEX_VERSION or not isinstance(saved.get("files"), dict):
        return {}  # old tokenizer, or not ours: rebuild
    return saved["files"]

def _save_word_index(path, files: dict) -> None:
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"version": WORD_INDEX_VERSION, "files": files}))
        tmp.replace(path)
    except Exception:
        pass  # best effort: an unsaved index is just rebuilt

def _index_item(text: str, sha: str, pairs: bool = True) -> dict:
    heading = next((ln.lstrip("# ") for ln in text.splitlines() if ln.startswith("#")), "")
    chunks = [text[i:i + CONFIRM_CHUNK] for i in range(0, len(text), CONFIRM_CHUNK)] or [""]
    parts = []
    for c in chunks:
        pw = passage_words(c)
        parts.append([dict(pw), sum(pw.values()), sorted(f"{a} {b}" for a, b in word_pairs(c)) if pairs else []])
    whole = Counter()
    for p in parts:
        whole.update(p[0])
    return {"sha": sha, "heading": heading, "whole": dict(whole), "passages": parts}

def _valid_item(item, sha) -> bool:
    return (isinstance(item, dict) and item.get("sha") == sha and isinstance(item.get("heading"), str)
            and isinstance(item.get("whole"), dict) and isinstance(item.get("passages"), list) and bool(item["passages"])
            and all(isinstance(p, list) and len(p) == 3 and isinstance(p[0], dict) and isinstance(p[1], int)
                    and isinstance(p[2], list) for p in item["passages"]))

def term_variants(terms: list, vocab) -> dict:
    """{term: the vocabulary words that count as that term} (close spellings, stem/ending forms, synonyms)."""
    out = {}
    for t in terms:
        near = set(difflib.get_close_matches(t, vocab, n=3, cutoff=0.8)) if len(t) > 3 else set()
        stem = t[:5] if len(t) > 5 else t  # long words by stem, short ones plus an ending (owe/owed)
        out[t] = near | {v for v in vocab if v.startswith(stem) and len(v) - len(t) <= (99 if len(t) > 5 else 2)}
        # A synonym counts as a match for its source word, not as an extra word.
        out[t] |= {x for x in SYNONYMS.get(t, []) if x in vocab}
    return out

def word_search(question: str, pointers: list, limit: int = FALLBACK_FILES, skip=(), extra=(), held_cover=None,
                reads=None, index_path=None, candidates=None, items=None, fts=None, read_paths=()) -> list:
    """Local, no provider calls: [(score, path, pointer)] of the principal's reviewed
    files best matching the question's words (BM25 per CONFIRM_CHUNK passage, a file
    scores its best passage; a file's path, description and stored question count triple
    in every passage). Typos match a close word (difflib). A file must
    cover FALLBACK_MIN_COVERAGE of the question's weighted words to be offered. Test/scratch
    output is never searched; `skip` paths (already routed) are dropped before the top `limit`.
    A reviewed file edited since connect stays searchable at its current text while its
    pointer waits on the refresh (routing cannot see a stale pointer), if that text passes
    the same secret scan and size ceiling connect applies; it is listed in the trace.
    `extra` names edited files a refresh would hold. They never enter the ranking; in the same pass their word
    coverage of the question (the share of the question's weighted words they contain, the test every file
    must pass to be offered) is recorded in `held_cover`, locally, nothing sent."""
    terms = query_terms(question)
    if not terms:
        return []
    docs, changed, aside = {}, [], {}
    # Adjacent question words, in question order (before de-duplication); a passage keeps
    # only the pairs it shares with these, so the pair pass costs little memory.
    qwords = [w for w in words(question.replace("'", "").replace("\u2019", ""))
              if len(w) > 2 and w not in QUERY_STOPWORDS]
    qpairs = list(dict.fromkeys((a, b) for a, b in zip(qwords, qwords[1:])
                                if a != b and a in terms and b in terms))  # "step by step" is no phrase
    qkeys_s = {f"{a[:4]} {b[:4]}" for a, b in qpairs}
    index = items if items is not None else _load_word_index(index_path) if index_path else {}
    dirty = False
    for ptr, path, entry in (candidate_files(pointers, done=docs) if candidates is None else candidates):
        if path in docs:
            continue
        if candidates is not None and path not in read_paths and _valid_item(index.get(path), entry.get("sha256")):
            item = index[path]  # index read path: the file index vouches for these bytes; sha-checked when served
        else:
            got = read_sha(path, reads)
            if got is None:
                continue
            raw, sha = got
            text = None
            if sha != entry.get("sha256"):
                text = raw.decode("utf-8", "replace")
                # Edited since connect: the refresh (auto-heal) re-gates it soon. Until then
                # search its current text, held back only as a refresh would hold it.
                if not edited_readable(path, ptr, entry, raw, text):
                    if path in extra:
                        aside[path] = text
                    if index.pop(path, None) is not None:
                        dirty = True  # an old entry for a file now held or stale: gone
                    continue  # never reviewed at a known version, or a refresh would hold it
                changed.append(path)
            item = index.get(path)
            if not _valid_item(item, sha):
                if text is None:
                    text = raw.decode("utf-8", "replace")
                item = _index_item(text, sha, bool(index_path or qkeys_s))
                if index_path:
                    index[path] = item
                    dirty = True
        head = " ".join([path.replace("/", " ").replace("-", " ").replace("_", " "), item["heading"],
                         str(entry.get("description") or ""), str(entry.get("question") or "")])
        head_words = Counter(w for w in words(head) for _ in range(3))
        htotal = sum(head_words.values())
        parts = [(c, n + htotal, {k for k in pairs if k in qkeys_s} if qkeys_s else set())
                 for c, n, pairs in item["passages"]]
        docs[path] = (ptr, item["whole"], len(parts), head_words, parts)
    if dirty and index_path:
        _save_word_index(index_path, index)
    def variants_of(vocab):
        return term_variants(terms, vocab)
    if aside and held_cover is not None:
        # Edited files a refresh would hold: coverage only, scored against the same corpus plus themselves.
        counts = [Counter(wc) + Counter({w: v * npass for w, v in hw.items()})
                  for _, wc, npass, hw, _ in docs.values()] + [passage_words(t) for t in aside.values()]
        var2 = variants_of(sorted(set().union(*(c.keys() for c in counts))))
        tf2 = [{t: sum(c.get(v, 0) for v in var2[t]) for t in terms} for c in counts]
        idf2 = {t: math.log(1 + (len(tf2) - df + 0.5) / (df + 0.5))
                for t, df in ((t, sum(1 for f in tf2 if f[t])) for t in terms)}
        total2 = sum(idf2[t] for t in terms if any(f[t] for f in tf2)) or 1
        for path, f in zip(aside, tf2[len(docs):]):
            held_cover[path] = sum(idf2[t] for t in terms if f[t]) / total2
    if not docs:
        return []
    _STAGE["word_changed"] = changed[:STAGE_LIST_CAP]
    if fts:
        # S3b: the corpus-wide numbers (word variants, document frequency, file count, average passage size) were
        # stored by the updater; only the shortlisted files (and the few edited ones, counted here) are scored.
        local = {p: d for p, d in docs.items() if p in fts["local"]}
        variants = {t: set(v) for t, v in fts["variants"].items()}
        if local:
            for t, v in variants_of(sorted(set().union(*(set(wc) | set(hw) for _, wc, _, hw, _ in local.values())))).items():
                variants[t] |= v
    else:
        vocab = sorted(set().union(*(set(wc) | set(hw) for _, wc, _, hw, _ in docs.values())))
        variants = variants_of(vocab)
    def tf_of(c, h, k=1):
        return {t: sum(c.get(v, 0) + k * h.get(v, 0) for v in variants[t]) for t in terms}
    tf = {path: tf_of(wc, hw, npass) for path, (_, wc, npass, hw, _) in docs.items()}
    if fts:
        lsizes = [size for p in local for _, size, _ in docs[p][4]]
        n = fts["n"] + len(local)
        avg = (fts["size"] + sum(lsizes)) / ((fts["passages"] + len(lsizes)) or 1) or 1
        dfs = {t: fts["df"].get(t, 0) + sum(1 for p in local if tf[p][t]) for t in terms}
    else:
        n = len(docs)
        sizes = [size for *_, parts in docs.values() for _, size, _ in parts]
        avg = sum(sizes) / len(sizes) or 1
        dfs = {t: sum(1 for f in tf.values() if f[t]) for t in terms}
    idf = {t: math.log(1 + (n - df + 0.5) / (df + 0.5)) for t, df in dfs.items()}
    # Words no reviewed file contains (e.g. "time") cannot tell files apart; leave
    # them out of the coverage total so they do not sink every file.
    total = sum(idf[t] for t in terms if dfs[t]) or 1
    scored = []
    for path, (ptr, _, _, hw, parts) in docs.items():
        f = tf[path]
        if sum(idf[t] for t in terms if f[t]) / total < FALLBACK_MIN_COVERAGE:
            continue
        # Scored per passage, best passage wins: whole-file BM25 sank a long file
        # (36 KB medical timeline) whose one passage held every question word.
        # Two question words side by side in a passage (a phrase: "read the source",
        # "parent folder") add their weight once more: scattered matches tie often, and a
        # file stating the phrase was left one slot past the read list.
        bm25 = max(sum(idf[t] * pf[t] * 2.2 / (pf[t] + 1.2 * (0.25 + 0.75 * size / avg)) for t in terms)
                   + PHRASE_WEIGHT * sum((idf[a] + idf[b]) / 2 for a, b in qpairs if f"{a[:4]} {b[:4]}" in pairs)
                   for pf, size, pairs in ((tf_of(c, hw), size, pairs) for c, size, pairs in parts))
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
        return confirm_results(paths, retry_network(question, paths, results))
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
        return confirm_results(paths, retry_network(question, paths, results))
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
    return confirm_results(paths, retry_network(question, paths, results))

def retry_network(question: str, paths: list, results: list) -> list:
    """A file whose content check hit a TypeSafe network error is checked once more on its own
    before it counts as unchecked. A second failure stays an error."""
    again = [i for i, (_, _, e, _) in enumerate(results) if is_network_error(e)]
    for i, r in zip(again, ThreadPoolExecutor(max_workers=max(1, min(len(again), NAV_CONCURRENCY))).map(
            lambda i: confirm_one(question, paths[i]), again) if again else []):
        results[i] = r
    if again:
        _STAGE["network_rechecks"] = len(again)
    return results

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

def refresh_hint(ptr: str, principal: str, kind: str) -> str:
    """A stale pointer's files changed since connect; say the exact command that re-prepares it."""
    if not kind.startswith(("preparation-required", "refresh-required")):
        return ""
    if "-manual-" in ptr:
        return "; its source changed: re-add it with ask.py --add ... --replace-entry"
    # The printed command must run as-is from any folder: the absolute script path plus the pointer
    # and every principal it serves (--refresh replays the recorded recipe, so no --root or writer
    # here; a bare --refresh with no report is refused for want of --root, and one principal short
    # is refused as a scope change).
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

# "Which skill/tool/command ..." asks a skill catalog question: the answer is a SKILL.md
# in the trusted skill roots, which are not connected files. The skills connector
# (dispatch.py skills) searches them for every principal; its picks print first.
SKILL_Q_RE = re.compile(r"\b(skills?|slash commands?)\b|\b(which|what|any)\b.{0,30}\b(tools?|commands?)\b", re.I)
GUESS = "(unverified guess)"

def skill_note(name: str) -> str:
    return ("  (unverified local guess: read the SKILL.md before using it)" if name.endswith(GUESS)
            else "  (skill catalog match: read the SKILL.md before using it)")

def skill_question(question: str) -> bool:
    return os.environ.get("SUPERJEV_SKILLS", "1") != "0" and bool(SKILL_Q_RE.search(question))

SKILL_SEARCH_TIMEOUT = 60

def skill_search(request: str) -> tuple:
    """(status, [(label, SKILL.md path)], failure or "", fallback cause or ""): the one reader of the skills
    connector, for asks and preflight. One labelling rule: only an exact name or a live judge pick is a
    match; everything else (local ranking, doubt, a clarify question, a fallback) is an "(unverified
    guess)". A failure is never an empty answer. The door's own error sentence (setup steps included) is
    returned whole; only output that is not the door's JSON is cut."""
    try:
        r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "dispatch.py"),
                            "skills", "--request", request], capture_output=True, text=True,
                           timeout=SKILL_SEARCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "", [], f"timed out after {SKILL_SEARCH_TIMEOUT}s", ""
    except OSError as e:
        return "", [], str(e), ""
    try:
        out = json.loads(r.stdout)
    except ValueError:
        out = None
    if not isinstance(out, dict):
        return "", [], f"exit {r.returncode}: unreadable output: {(r.stdout or '').strip()[-200:]}", ""
    status = out.get("status") or ""
    if status == "error" or r.returncode:
        return status, [], str(out.get("error") or out.get("reason") or f"exit {r.returncode}"), ""
    match = status == "exact" or (status == "suggestions" and out.get("source") == "jev")
    picks = [((c.get("name") or c.get("id") or "") + ("" if match else " " + GUESS), c["path"] if isinstance(c.get("path"), str) else "")
             for c in out.get("candidates") or [] if isinstance(c, dict)]
    return status, picks, "", (str(out.get("error") or "") if status == "fallback" else "")

SKILL_FAILED = {}  # question -> why its catalog search failed; the ask that started it reads it (skills_off)

def skill_catalog(question: str) -> list:
    """[(name, SKILL.md path)] for the ask path; [] on any failure, which prints one cause line
    and is kept for --json."""
    _status, picks, err, _fallback = skill_search(question)
    if err:
        print(f"skill search failed: {err}", file=sys.stderr)
        SKILL_FAILED[question] = err[:200]
    return [(name, path) for name, path in picks if path]

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

def people(pointers: list, paths=None) -> dict:
    """{person folder name: relation words from its PROFILE's Relation line}. `paths`: the person-folder files
    already listed by the file index (the flag-on FTS path), instead of every connected file's prepare-cache."""
    out = {}
    for group in ([load_cache_files(p) for p in pointers] if paths is None else [paths]):
        for path in group:
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
    and person folder names ("belinda's") add their folders; "I"/"me" adds the
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

def _outcome_line(o: dict) -> str:
    return f"OUTCOME: {o['outcome']} - {o['why']}" + (f"; next: {o['cmd']}" if o["cmd"] else "")


def lookup(question: str, principal: str, sdir: Path) -> int:
    _RESULT.clear()
    buf = io.StringIO()
    if _CLAIM["text"]:
        _CLAIM["word"] = None
        try:
            with contextlib.redirect_stdout(buf):
                rc = _lookup(question, principal, sdir)
        except SecretHeld:  # an expected refusal, not a crash
            _RESULT.clear()
            rc = _done("not-supported", "the statement holds a secret, so it was not sent",
                       "remove the secret and check again", "rephrase")
        except Exception:  # a crash: keep what it printed, then let the caller report it
            sys.stdout.write(buf.getvalue())
            raise
        if _CLAIM["word"] is None:  # ended before any verdict: its first line is the OUTCOME line
            print(_outcome_line(_RESULT))
        sys.stdout.write(buf.getvalue())
        return rc if _CLAIM["word"] is None else CLAIM_EXIT.get(_CLAIM["word"], max(1, rc))
    try:
        with contextlib.redirect_stdout(buf):
            try:
                _lookup(question, principal, sdir)
            finally:
                index_after_ask(principal, sdir)  # flag on only; detached, never on the ask's clock
    except Exception as e:  # SecretHeld and the like keep main()'s handling
        if isinstance(e, SecretHeld):
            sys.stdout.write(buf.getvalue())
            raise
        _RESULT.clear()
        _done("error", _redact(f"the lookup failed ({type(e).__name__}: {e})"), f"python3 {skill_dir_for_display() / 'ask.py'} --principal {principal} --status")
        traceback.print_exc()
    o = _RESULT if "outcome" in _RESULT else dict(outcome="error", why="the lookup ended without a result", cmd="")
    print(_outcome_line(o))
    sys.stdout.write(buf.getvalue())
    return OUTCOME_EXIT[o["outcome"]]


def _lookup(question: str, principal: str, sdir: Path) -> int:
    # Relative route mass ranks candidates; it is not ordinary evidence confidence.
    route_floor = ROUTE_FLOOR if _CLAIM["text"] else 0
    status_cmd = f"python3 {skill_dir_for_display() / 'ask.py'} --principal {principal} --status"
    claim = bool(_CLAIM["text"])  # a claim is told what to fix in a claim's words, not a question's
    if not question.strip():
        return _done("not-supported", "empty statement" if claim else "empty question",
                     f'check one fact, e.g. ask.py --principal {principal} --claim "the lease ends in June"' if claim
                     else f'ask one focused question, e.g. ask.py --principal {principal} "find the note about X"',
                     "rephrase")
    if len(question) > MAX_QUESTION:
        return _done("not-supported", f"{'statement' if claim else 'question'} too long "
                     f"({len(question):,} chars, max {MAX_QUESTION:,})",
                     "check one shorter, focused statement" if claim else "ask one shorter, focused question",
                     "rephrase")
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
            claim_result(saved["verdict"], {saved["path"]: {**saved, "verdict": "supported" if saved["verdict"] == "TRUE"
                                                            else "contradicted"}}, [saved["path"]], 0, saved["path"])
            _RESULT["saved"] = {"by": "auto", **({"date": time.strftime("%Y-%m-%d", time.localtime(saved["saved"]))}
                                                 if isinstance(saved.get("saved"), (int, float)) else {})}
            _CLAIM["word"] = saved["verdict"]
            return _done("found", "the claim was checked before; its proof file is unchanged")
        cache = {"status": "skipped (claim)"}  # saved answers answer questions, not statements
    elif replay:
        cache = {"status": "skipped (replay)"}
    else:
        cache = memory({"action": "cached", "principal": principal, "question": norm_q(question)})
        if cache.get("status") != "verified-cache-hit" and norm_q(question) != question:
            # An answer saved before matching was normalised sits under the caller's exact wording.
            cache = memory({"action": "cached", "principal": principal, "question": question})
    cache_stage = {"result": cache.get("status"), "checked": len(cache.get("checked") or [])}
    withheld = set()
    if cache.get("status") == "verified-cache-hit":
        rc = print_hit(cache, sdir, principal, question)
        log(sdir, "lookup", question=question, result="cache-hit" if rc == 0 else "stale-source", secs=round(time.time() - t0, 1))
        if rc == 0:
            r = saved_record(sdir, question) or {}
            n, k = len(r.get("files") or []) or 1, len(r.get("skills") or [])
            _done("found", f"{n} file{'s' if n != 1 else ''}"
                  + (f"; {k} skill suggestion{'s' if k != 1 else ''}" if k else "") + "; saved answer, sources unchanged")
        write_trace(sdir, kind="trace", lookup_id=lookup_id, question=question, routing={},
                    content_check={}, final_ranked=[], tier="cache" if rc == 0 else "stale",
                    timings={"total_secs": round(time.time() - t0, 2)},
                    stages={"cache": {"result": "hit" if rc == 0 else "stale"}})
        if rc == 0:
            return rc
        # The stale answer stays withheld, but the question still gets a fresh live search.
        # Its manual pointer's record text IS the stale answer, so keep it out of the live search.
        withheld = {manual_pointer_name(principal, norm_q(question)), manual_pointer_name(principal, question)}
        print("Searching live instead...")
    idx_read, index_fb = None, {}  # the file index, when the flag is on and it is usable; else today's path
    if panel is None and index_enabled():
        idx_read, ipanel, why, index_fb = index_panel(principal, sdir)
        _STAGE["index"] = {"on": True, "used": idx_read is not None, **({"fallback": why} if why else {})}
        if index_fb:  # some pointers are served by today's path, in this same ask
            _STAGE["index"]["fallback"] = {"pointers": [f"{n}: {r}" for n, r in list(index_fb.items())[:STAGE_LIST_CAP]]}
        panel = ipanel
    panel = panel if panel is not None else memory({"action": "panel", "principal": principal})
    view_pointers = {row["pointer"] for row in panel.get("pointers", [])
                     if isinstance(row, dict) and row.get("viewOriginals")}
    _STAGE["view_pointers"] = sorted(view_pointers)
    if panel.get("reason") == "not-set-up":
        return _done("needs-setup", "Super Jev is not set up yet", f"python3 {skill_dir_for_display() / 'setup.py'}",
                     "setup")
    pointers = [n for n in ((p.get("pointer") if isinstance(p, dict) else p)
                            for p in panel.get("pointers", [])) if n and n not in withheld]
    if not _CLAIM["text"]:  # the claim path is unchanged: it keeps its own routing
        gens_all = {row.get("pointer"): row.get("generation") for row in panel.get("pointers", []) if isinstance(row, dict)}
        _STAGE["local_rows"] = load_local_rows(sdir, principal, pointers, gens_all, view_pointers)
    else:
        _LOCAL_ROWS.clear()
    # A saved note whose source file changed after it was recorded is not a current answer.
    stale_notes = {n: changed_source(sdir / "manual" / f"{n}.md") for n in pointers
                   if n.startswith(f"{principal}-manual-")}
    stale_notes = {n: src for n, src in stale_notes.items() if src}
    if stale_notes:
        print("STALE: saved note(s) skipped, source changed since they were recorded: "
              + ", ".join(sorted(set(stale_notes.values()))))
        pointers = [n for n in pointers if n not in stale_notes]
    if not pointers:
        # Runnable from any folder (the skill folder as invoked), and a new agent in a fleet
        # learns the shared sets it can join at once, with no connect.
        here = skill_dir_for_display()
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
        return _done("needs-setup", f"nothing is connected yet for principal '{principal}'",
                     f"python3 {here / 'prepare_bulk.py'} --root /path/to/folder --pointer my-notes "
                     f"--principal {principal}", "connect")

    # Sick-pointer circuit breaker: skip pointers already benched from repeated
    # recent failures instead of waiting on them (and re-erroring) again this call.
    health = load_pointer_health(sdir)
    original_pointers = pointers
    # Runs beside routing; its picks print first (see SKILL_Q_RE).
    skill_job = (ThreadPoolExecutor(max_workers=1).submit(skill_catalog, question)
                 if skill_question(question) else None)
    active_pointers, error_lines, failed, stale_ptrs, hints = [], [], [], [], {}
    older = []  # stale sets served from their last catalog: searched, so not counted as left out
    for ptr in original_pointers:
        benched, remaining, fails = pointer_benched(health, ptr, principal)
        if benched:
            failed.append(ptr)
            error_lines.append(f"[{ptr}] benched ({fails} consecutive failures, cooling off {int(remaining)}s more)")
        else:
            active_pointers.append(ptr)
    pointers = active_pointers
    # Person resolution: a question about one person never reads (or confirms)
    # another person's records, and a pointer holding only theirs is not asked.
    try:
        fts_ready = bool(idx_read and idx_read.fts_usable(WORD_INDEX_VERSION)[0])
        folks = people(pointers, idx_read.person_paths([p for p in pointers if p not in index_fb])
                       + [p for ptr in pointers if ptr in index_fb for p in load_cache_files(ptr)]) if fts_ready else people(pointers)
    except sqlite3.Error as e:
        idx_read, index_fb, fts_ready = index_failed(idx_read, e), {}, False
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
    # and never one holding a resolved person's folder ("my mom" -> nora).
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
                               daemon=True) if unknown and not idx_read else None  # never on the index path's clock
    if learner:
        learner.start()

    nav_none, nav_kinds = {}, {}
    # A stale set is routed on the catalog of its last refresh (memory navigate lastGood), so
    # a refresh cooldown never hides the whole set: {pointer: {"changed": [...], "missing": [...]}}.
    stale_served = {}

    def classify(ptr, out, elapsed):
        status, reason = out.get("status"), out.get("reason", "")
        nav_none[ptr] = (_root_none(out), round(elapsed, 2))
        nav_kinds[ptr] = out.get("kind")
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

    # Match the five-source result bound on every routing path. Claim checks
    # retain their existing navigation defaults.
    routing_limits = {} if _CLAIM["text"] else {"mode": "source-discovery", "limits": {"beamWidth": 5, "maxResults": 5}}

    def nav(ptr):
        t_start = time.time()
        out = memory({"action": "navigate", "pointer": ptr, "principal": principal, "question": question,
                      "lastGood": True, **routing_limits})
        # An overloaded judge was already retried inside the call; a network error gets
        # its one retry below, after routing (is_network_error).
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
    if not _CLAIM["text"]:
        # The TOC search picks the read list, so routing asks Jev nothing: each set's status comes from
        # the registry (no Jev call), which keeps stale sets searched, named and auto-healed.
        outs = {ptr: {"status": "no-candidates"} for ptr in pointers}
        # Reuse the panel fetched above: nothing writes the registry between the two reads.
        panel = panel if pointers else {}
        for row in (panel.get("pointers") or []) if isinstance(panel, dict) else []:
            if isinstance(row, dict) and row.get("pointer") in outs and str(
                    row.get("snapshotStatus") or row.get("status") or "").startswith(("preparation-required", "refresh-required")):
                outs[row["pointer"]] = {"status": "no-candidates", "stale": {"from_registry": True}}
    else:
        outs = nav_many(pointers) if pointers and batch_jev() else None
    if not _CLAIM["text"]:
        # A set with no prepare-cache (a reviewed view, a path-connected or recipe set) has no files for the TOC
        # search to list, so it is still routed by Jev's navigate, as before: no quiet shrink of what a question reaches.
        # S4: a set is routed by Jev only when it has no local rows at all (no cache, no covering parent, no reviewed
        # sources); one batched call, at most NAV_FALLBACK_MAX sets, named in the trace as routing.fallback.
        def has_local(p):
            own = (idx_read.has_entries(p) if fts_ready and p not in index_fb and p not in view_pointers else bool(load_cache_files(p)))
            return own or bool(_LOCAL_ROWS.get(p)) or (p not in view_pointers and covered_by_parent(p, original_pointers))
        nav_ptrs = [p for p in pointers if not has_local(p)]
        _STAGE["routing_fallback"] = [f"{p}: no local rows" for p in nav_ptrs[:min(NAV_FALLBACK_MAX, STAGE_LIST_CAP)]]
        if len(nav_ptrs) > NAV_FALLBACK_MAX:
            _STAGE["routing_fallback"].append(f"{len(nav_ptrs) - NAV_FALLBACK_MAX} more sets: no local rows, over the cap, not asked")
        nav_ptrs = nav_ptrs[:NAV_FALLBACK_MAX]
        bm = nav_many(nav_ptrs) if nav_ptrs and batch_jev() else None
        if bm is not None:
            navd = {ptr: classify(ptr, bm[ptr], time.time() - t_start) for ptr in nav_ptrs}
        else:
            navd = dict(zip(nav_ptrs, ThreadPoolExecutor(max_workers=min(len(nav_ptrs), NAV_CONCURRENCY)).map(nav, nav_ptrs))) if nav_ptrs else {}
        results = [navd[ptr] if ptr in navd else classify(ptr, outs[ptr], time.time() - t_start) for ptr in pointers]
    elif outs is None:  # one navigate call per pointer, as before batching
        results = list(ThreadPoolExecutor(max_workers=min(len(pointers), NAV_CONCURRENCY)).map(nav, pointers)) if pointers else []
    else:
        results = [classify(ptr, outs[ptr], time.time() - t_start) for ptr in pointers]
    # A set whose routing hit a TypeSafe network error is asked once more before it counts as not searched.
    again = [i for i, (ptr, kind, *_r) in enumerate(results) if not replay and is_network_error(kind, nav_kinds.get(ptr))]
    for i in again:
        results[i] = nav(results[i][0])
    if again:
        _STAGE["network_retries"] = len(again)
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
    merged, statuses, routing_picked = [], {}, set()
    state_of, healing = {}, set()  # --json: why each unsearched set was left out, and whether it is being refreshed
    _STAGE["stale_changed"] = []
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
                        if c.get("score", 0) >= route_floor:
                            routing_picked.add(path)  # picked for this question (counted once, by edited_held)
                        continue
                    _STAGE["stale_changed"].append(path)
                kept.append(c)
            rows = kept
        if kind == "candidates" and rows:
            statuses[ptr] = "candidates"
            merged += [(c.get("score", 0), c.get("originalPath", ""), ptr) for c in rows]
        elif kind in ("candidates", "no-candidates"):
            statuses[ptr] = "no-candidates"
        else:
            statuses[ptr] = kind
            (stale_ptrs if auto_heal.is_stale_kind(kind) else failed).append(ptr)
            if auto_heal.is_stale_kind(kind):
                state_of[ptr] = "unprepared" if kind.startswith("preparation-required") else "stale"
        if served:
            # It answered from its last refresh (classify serves only routed sets): searched, not left out.
            older.append(ptr)
            state_of[ptr] = "stale"
            statuses[ptr] += " (stale: last refresh)"
        if served or kind not in ("candidates", "no-candidates"):
            stale_kind = served.get("status", "preparation-required") if served else kind
            hint = hints[ptr] = ("; refresh through the recorded view recipe; raw bulk refresh is disabled"
                    if ptr in view_pointers else refresh_hint(ptr, principal, stale_kind))
            # A stale pointer never has to wait on a human to run the refresh hint above by
            # hand: this starts the exact same prepare_bulk.py --refresh in the background,
            # bounded (one run per pointer, a few at once per principal, the rest queued;
            # per-pointer cooldown, hourly cap -- see auto_heal.py), and returns immediately either way. It never blocks this lookup
            # and never changes what this lookup reports for the pointer that triggered it;
            # it only means the *next* lookup may no longer hit it.
            heal_note = ""
            stale = bool(served) or auto_heal.is_stale_kind(kind)
            result = None
            if stale and not replay:
                result = (reconnected.get(ptr, "no-recipe") if ptr in view_pointers else
                          auto_heal.maybe_heal(ptr, principal))
                if result in ("started", "in-progress"):
                    healing.add(ptr)
                if result == "started":
                    heal_note = " (auto-heal: refresh started in background)"
                elif result == "in-progress":
                    heal_note = " (auto-heal: a refresh of this set is running, or the agent is at its limit of refreshes; queued, it runs when one finishes)"
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
            if served:
                # Not an error: this set answered from its last refresh. Say so, and how much is newer.
                gone = len(served.get("missing") or [])
                error_lines.append(f"[{ptr}] stale: searched as of its last refresh "
                                   + ("(files changed since)" if served.get("from_registry") else
                                      f"({len(served.get('changed') or [])} file(s) changed since"
                                      + (f", {gone} removed" if gone else "") + ")")
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
    # Routing scores are only comparable inside one pointer, and it sees names and
    # descriptions, not text: unrelated files can fill every routed read slot and hide the
    # note that states the answer. Word search adds the files whose text matches the
    # question; the content check still decides what is kept.
    reads = {}  # one read+sha pass shared by edited_held and word_search
    icands, fitems, ftocs, fts_nums, fb_paths = None, None, None, None, set()
    if idx_read:
        try:
            # Index read path: candidates come from the index rows; no file is read or hashed here. Edited files
            # are found by the updater, or at read below for the files actually served.
            ix_ptrs = [p for p in search_pointers if p not in index_fb]
            fb_ptrs = [p for p in search_pointers if p in index_fb]
            picked, fwhy = fts_pick(idx_read, question, ix_ptrs, who, other_person) if ix_ptrs else (None, "the index holds none of the searched pointers")
            if picked:
                icands, fitems, ftocs, fts_nums, ftrace = picked
                out_of_scope = set()  # the FTS scope already left out other people's files
                _STAGE["index"]["fts"] = ftrace
            else:
                _STAGE["index"]["fts"] = {"used": False, "fallback": fwhy}
                icands = idx_read.candidates(ix_ptrs)
                out_of_scope = {p for _ptr, p, _e in icands if other_person(p)}
                icands = index_candidates(idx_read, ix_ptrs, out_of_scope)
            edited = {"secret": [], "stuck": [], "refresh": []}
            if fb_ptrs:
                # Pointers the index does not hold completely and currently: today's path, here, for each of them. Their
                # files are read and sha-checked as today (word search and TOC corpus), not vouched for by the index.
                fb_out = {p for ptr in fb_ptrs for p in load_cache_files(ptr) if other_person(p)}
                out_of_scope |= fb_out
                fb_cands = fallback_candidates(idx_read, fb_ptrs, ix_ptrs, fb_out)
                fb_paths = {c[1] for c in fb_cands}
                icands = (icands or []) + fb_cands
                if fts_nums:
                    fts_nums["local"] = set(fts_nums["local"]) | fb_paths  # scored with the shortlist, like the edited files
                edited = edited_held(fb_ptrs, fb_out, reads)
        except sqlite3.Error as e:  # locked by the updater, damaged: today's path answers this ask
            idx_read, index_fb = index_failed(idx_read, e), {}
            icands = fitems = ftocs = fts_nums = None
            fb_paths = set()
    if not idx_read:
        out_of_scope = {p for ptr in search_pointers for p in load_cache_files(ptr) if other_person(p)}
        edited = edited_held(search_pointers, out_of_scope, reads)  # once per searched set, not per search path
    held_cover = {}  # a held file's word coverage of the question, from the same word-search pass
    toc_on = not _CLAIM["text"]
    found = word_search(question, search_pointers, skip=(out_of_scope if toc_on else set(routed[:CONFIRM_FILES]) | out_of_scope),
                        reads=reads, index_path=None if fts_nums else sdir / WORD_INDEX_FILE,
                        **({"candidates": icands, "read_paths": fb_paths} if idx_read else {}),
                        **({"items": fitems, "fts": fts_nums} if fts_nums else {}),
                        **({"extra": set(edited["secret"]), "held_cover": held_cover} if edited["secret"] else {}))
    wpaths = {p: ptr for _, p, ptr in found}
    to_check = routed[:CONFIRM_FILES] + list(wpaths)
    if toc_on:
        # The TOC search picks the read list: a free shortlist from every file's table of contents,
        # Jev's pick of files from their TOC pages, then the parts of those files. The word search's
        # own hits ride along at every step. If it fails, the word search's list is read and the
        # failure is named in the trace.
        corpus = {}
        for tptr, tpath, tentry in (icands if idx_read else candidate_files(search_pointers, out_of_scope)):
            if tpath in corpus:
                continue
            if idx_read and tpath not in fb_paths:
                corpus[tpath] = (tptr, tentry)  # vouched for by the index; sha-checked below if served
                continue
            try:  # a file edited since connect is searched only as a refresh would admit it (word_search's rule)
                traw = Path(tpath).read_bytes()
            except OSError:
                continue
            if hashlib.sha256(traw).hexdigest() == tentry.get("sha256") or edited_readable(
                    tpath, tptr, tentry, traw, traw.decode("utf-8", "replace")):
                corpus[tpath] = (tptr, tentry)
        tt0 = time.time()
        try:
            tfiles, tparts, ttrace = toc_search.run(question, corpus, found, {
                "read": lambda path: (lambda r: r if r is None else clean_text(r.decode("utf-8", "replace"), path))(
                    (lambda q: q.read_bytes() if q.is_file() else None)(Path(path))),
                "has_secret": has_secret, "query_terms": query_terms, "term_hits": term_hits},
                cache_path=None if fts_nums else sdir / "toc-cache.json", **({"rows": ftocs} if fts_nums else {}))
            _STAGE["toc_parts"] = tparts
            # the files Jev's navigate routed (sets with no prepare-cache) ride along after the TOC pick
            tfiles = list(dict.fromkeys(list(tfiles) + routed[:CONFIRM_FILES]))
            wpaths = {p: (corpus[p][0] if p in corpus else next(m[2] for m in merged if m[1] == p)) for p in tfiles}
            to_check = list(tfiles)
            for tp_, ts_ in (ttrace.get("pick") or {}).get("top") or []:  # the pick's scores break content ties like routing's did
                route.setdefault(tp_, ts_)
        except Exception as e:  # noqa: BLE001 -- any failure reads the word search's list instead
            ttrace = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
        ttrace["secs"] = round(time.time() - tt0, 1)
        _STAGE["toc"] = ttrace
    if idx_read:
        # Verify-at-read: only the files about to be served are hashed, whatever the corpus size.
        by_path = {p: (ptr_, e) for ptr_, p, e in icands if p not in fb_paths}
        to_check, vinfo = index_verify(idx_read, to_check, by_path, edited)
        _STAGE["index"].update({"verified": vinfo["verified"], "mismatch": vinfo["mismatch"][:STAGE_LIST_CAP],
                                "gone": vinfo["gone"][:STAGE_LIST_CAP]})
        idx_read.close()
    checked = set(to_check)
    if to_check:
        scores, partial, check_error, notes = confirm(question, to_check)
        if check_error:
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
        # A file the judge read and surely says does not state the claim is rejected: never listed.
        rejected = {p for p, f in (_STAGE.get("claim_files") or {}).items() if f.get("verdict") == "not_stated"
                    and isinstance(f.get("prob"), (int, float)) and f["prob"] >= CLAIM_SURE}
        top = [m for m in top if m[1] not in rejected]
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
    incomplete = bool(failed or stale_ptrs)  # a set was not searched: a better file may sit in it
    win = None if replay or _CLAIM["text"] or incomplete else win_of(
        top, possible, notes, skills, (_STAGE.get("listwise") or {}).get("leans_none"))
    log(sdir, "lookup", question=question, judge=judges.profile().name, pointers=len(pointers), statuses=statuses, secs=round(time.time() - t0, 1),
        top=[{"score": s, "path": p, "pointer": ptr, "possible": p in possible} for s, p, ptr in top],
        **({"win": win} if win else {}), **({"partial": True} if incomplete else {}))
    edited_paths = [p for kind in edited.values() for p in kind]  # once per searched set, not per search path
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
            "routing_fallback": _STAGE.get("routing_fallback") or [],
            "local_rows": _STAGE.get("local_rows") or {},
            "routing": {ptr: {"status": kind + (" (stale: last refresh)" if ptr in stale_served else ""),
                              "none": nav_none.get(ptr, (None,))[0],
                              "secs": nav_none.get(ptr, (None, None))[1],
                              "files": [{"path": c.get("originalPath", ""), "score": c.get("score", 0),
                                         "kept": c.get("score", 0) >= route_floor} for c in rows][:STAGE_LIST_CAP]}
                        for ptr, kind, rows, _elapsed, _ok in results},
            "benched": [ln for ln in error_lines if "] benched (" in ln][:STAGE_LIST_CAP],
            "stale_held": edited_paths[:STAGE_LIST_CAP],
            "word_search": {"terms": wsearch.get("terms"), "files_searched": wsearch.get("files_searched"),
                            "changed_since_connect": sorted(set(_STAGE.get("word_changed") or [])
                                                            | set(_STAGE.get("stale_changed") or [])),
                            "passed_coverage": wsearch.get("passed_coverage"),
                            "top": [{"score": sc, "path": p,
                                     "fate": fates.get(p) or ("already routed" if p in routed[:CONFIRM_FILES]
                                                              else "not read: past top %d" % FALLBACK_FILES)}
                                    for sc, p, _ptr in wsearch.get("ranked", [])]},
            "toc": _STAGE.get("toc"),
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
            **({"index": _STAGE["index"]} if "index" in _STAGE else {}),  # flag on: used, or why it fell back
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
        not_run = bool((top or _STAGE.get("claim_read")) and files is None)
        if not_run:
            word, lines = "UNSURE", ["UNSURE: the true/false check did not run; read the files below before answering."]
            claim_result(word, {}, [], len(_STAGE.get("claim_read") or []))
        else:
            word, lines = claim_verdict(files or {}, _STAGE.get("claim_read") or list(content_check),
                                        incomplete=bool(failed or stale_ptrs or check_error))
        _CLAIM["word"] = "NOT RUN" if not_run else word  # the exit code reads this; the printed line and the log keep UNSURE
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
    file_list = [file_row(sc, p, ptr, "unchecked" if notes.get(p) == INCONCLUSIVE
                          else "possible" if p in possible else "confirmed") for sc, p, ptr in top]
    skill_list = skill_rows(skills)
    # A file picked for this question and withheld for a secret, or edited and now holding one: never read.
    held = list(dict.fromkeys([p for p, note in notes.items() if note == HELD_SECRET] + edited["secret"]))
    # Only a held file that matches THIS question makes the ask needs-setup: one picked by the content check, by
    # routing, or that word search scores above its threshold. The rest are named in left_out but change nothing.
    probe = set(edited["secret"])
    matched = {p for p, cov in held_cover.items() if cov >= FALLBACK_MIN_COVERAGE}
    held_hit = [p for p in held if p not in probe or p in matched or p in routing_picked]
    leans = bool(top and (_STAGE.get("listwise") or {}).get("leans_none"))
    for sk in skill_list:
        show_skill(sk)
    for f in file_list:
        show_file(f)
    if leans:
        print(LEANS_NONE_NOTE)
    for p in held:
        print(f"HELD  {p}  ({HELD_SECRET})")
    for line in error_lines:
        print(line)
    # One outcome from the whole search state. A set that failed, is stale or is unprepared was not
    # fully searched, so it never reads as a complete not-found.
    edited_out = ([(p, EDITED_WHAT, EDITED_FIX) for p in edited["refresh"]]
                  + [(p, EDITED_STUCK_WHAT, EDITED_STUCK_FIX) for p in edited["stuck"]])
    unsearched = list(dict.fromkeys(failed + stale_ptrs))
    n = len(unsearched)
    sets = f"{n} set{'s' if n != 1 else ''}"
    if older:  # searched, from the catalog of their last refresh: say so, but it is not a gap
        _RESULT["older_catalog"] = list(older)
        print(f"served from older catalog: {len(older)} set{'s' if len(older) != 1 else ''}")
    errors = error_rows(failed, nav_kinds)
    _RESULT.update(files=file_list, skills=skill_list, leans_none=leans, errors=errors,
                   unsearched=unsearched_rows(unsearched, state_of, healing), left_out=left_out_rows(edited_out, held))
    if why := SKILL_FAILED.pop(question, "") if skill_job else (
            "skill search is off (SUPERJEV_SKILLS=0)" if SKILL_Q_RE.search(question) else ""):
        _RESULT["skills_off"] = _redact(f"no skill catalog was searched: {why}")
    listed = bool(top or skills)
    # A claim that nothing settles takes its exit from how the search went, listed files or not: the judge's
    # confidence in the files it read must not decide whether a failed or stale set shows in the exit code.
    unsettled = bool(_CLAIM["text"]) and _CLAIM["word"] not in ("TRUE", "FALSE")
    if listed and not unsettled:
        # Files are the ranked files only; skill suggestions get their own label, never the file count.
        unconfirmed = " (unconfirmed: content check failed)" if check_error and any(m[1] in possible for m in top) else ""
        parts = ([f"{len(top)} file{'s' if len(top) != 1 else ''}{unconfirmed}"] if top else [])
        if skills:
            parts.append(f"{len(skills)} skill suggestion{'s' if len(skills) != 1 else ''}")
        found = "; ".join(parts)
        _RESULT["saved_now"] = autosave(principal, question, sdir, win)
        return _done("found", found + (f"; partial: {sets} not searched" if n else ""))
    if dropped and not listed:
        print(f"({dropped} file(s) matched the topic but no answer was confirmed on reading)")
    skipped = skipped_for_question(question, original_pointers, principal)
    _RESULT["left_out"] = left_out_rows(skipped + edited_out, held)
    ask_py = f"python3 {skill_dir_for_display() / 'ask.py'} --principal {principal}"
    if failed or check_error:
        why = (f"no match, and {len(failed)} set{'s' if len(failed) != 1 else ''} failed" if failed
               else "no match, and the content check failed")
        key = bool(errors) and all(e["kind"] in KEY_KINDS for e in errors)
        rc = _done("error", why, f"{ask_py} --status", "key" if key else "none")
    elif stale_ptrs or held_hit:
        first = next((m.group(1) for h in hints.values() if (m := re.search(r"Run: (.+)$", h))), "")
        why = "no match, but the search was incomplete: " + "; ".join(
            x for x in (f"{len(stale_ptrs)} set{'s' if len(stale_ptrs) != 1 else ''} stale or unprepared" if stale_ptrs else "",
                        f"{len(skipped)} file{'s' if len(skipped) != 1 else ''} skipped at setup" if skipped else "",
                        f"{len(edited_out)} edited file{'s' if len(edited_out) != 1 else ''} not read" if edited_out else "",
                        f"{len(held_hit)} file{'s' if len(held_hit) != 1 else ''} held (contains a secret; not sent)" if held_hit else "") if x)
        rc = _done("needs-setup", why, first or f"{ask_py} --status", "refresh" if stale_ptrs else "include")
    else:
        # Files skipped at setup do not make a searched set a setup gap: the sets were searched.
        gone = "; ".join(x for x in (
            f"{len(skipped)} file{'s' if len(skipped) != 1 else ''} skipped at setup" if skipped else "",
            f"{len(edited_out)} edited file{'s' if len(edited_out) != 1 else ''} not read" if edited_out else "",
            f"{len(held)} file{'s' if len(held) != 1 else ''} held (contains a secret; not sent)" if held else "") if x)
        gone = f"; {gone} (see {ask_py} --status)" if gone else ""
        rc = _done("not-found", f"searched {len(original_pointers)} set{'s' if len(original_pointers) != 1 else ''}, "
                   f"no matching file (it may still exist){gone}", f"{ask_py} --trace-show last",
                   "none" if _CLAIM["text"] else "connect")
        _RESULT["searched"] = {"sets": len(original_pointers),
                               "notes": sum(len(load_cache_files(ptr)) for ptr in original_pointers)}
    if listed:  # an unsettled claim that listed files: the verdict lines above are its answer; this is its exit
        return rc
    for line in miss_report(principal, len(original_pointers), routing, content_check,
                            question, original_pointers):
        print(line)
    if _RESULT["outcome"] == "not-found" or _CLAIM["text"]:
        _RESULT["voice"] = VOICE_LINE
        print(VOICE_LINE)
    return rc

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
SECRET_WHAT = "held back: it looks like it holds a password, key or card number"
SECRET_FIX = "remove or move the flagged value, then re-run setup"
EDITED_WHAT = "edited since its last refresh and not readable until a refresh admits it"
EDITED_FIX = "run the refresh command shown above (auto-heal also retries it)"
EDITED_STUCK_WHAT = "edited since its last refresh, and too big or outside the set's scope for a refresh to admit"
EDITED_STUCK_FIX = "make it smaller, or connect its folder as its own set"
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
            if (path in out
                    or (cache.get(path) or {}).get("pass") or not os.path.exists(path)):
                continue
            if "over size ceiling" in why:
                m = re.search(r"\(([\d,]+) bytes, max ([\d,]+)", why)
                size = (f" ({int(m.group(1).replace(',', '')) // 1000} KB, limit "
                        f"{int(m.group(2).replace(',', '')) // 1000} KB)") if m else ""
                out[path] = ("too big to connect" + size, "split it into smaller files, then re-run setup", "name")
            elif any(k in why for k in SECRET_HELD):
                out[path] = (SECRET_WHAT, SECRET_FIX, True)
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
             f"  - {total} connected sets; {len(routing)} searched after the topic filter; descriptions matched in {len(on_topic)}"
             + (": " + ", ".join(on_topic[:5]) + (" ..." if len(on_topic) > 5 else "") if on_topic else "")]
    if read:
        lines.append(f"  - {len(read)} file(s) read (picked by description or by words in the file); no answer confirmed. Closest: "
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


def find_candidate(sdir: Path, question: str, rank=None, file=None):
    """The row {score, path, pointer, possible} the lead picked from the last lookup's
    printed list: by 1-based rank, or by path (full path or unique file name).
    Returns (row, None) or (None, reason)."""
    rec = last_lookup(sdir, question)
    top = rec and rec["top"]
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
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "question": norm_q(question), "approved_by": approved_by,
             "judge": judges.profile().name, **fields}
    (sdir / "approvals.jsonl").open("a").write(json.dumps(entry) + "\n")

def saved_record(sdir: Path, question: str):
    """The last approvals.jsonl entry for this question while it is saved, else None
    (never saved, or un-saved since)."""
    path = sdir / "approvals.jsonl"
    if path.is_file():
        for line in reversed(path.read_text().splitlines()):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if norm_q(rec.get("question") or "") == norm_q(question):
                return rec if rec.get("approved_by") else None
    return None

def approver(sdir: Path, question: str) -> dict:
    """The saved answer's approvals entry. No entry means a save from before auto-cache
    existed, when only humans could approve."""
    return saved_record(sdir, question) or {"approved_by": "unknown (legacy)"}

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
                  sid, out: dict) -> tuple:
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
        lines = text.splitlines()
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
            # The file may have changed since connect: the cited reviewed text must still support the answer.
            passages = [p for p in out.get("passages") or [] if p.get("sourceId") == sid]
            if not any(support(p)[0] > 0 for p in passages):
                passages = []
    if not any(p.get("reviewedText") for p in passages):
        return None, f"no passage from {path} itself could be cited"
    return {**out, "passages": sorted(passages, key=support, reverse=True)}, None

def source_row(principal: str, pointer: str, path: str):
    """(the pointer's sources row for path or None, sources status); pages past 100."""
    offset, rows = 0, []
    while True:
        listed = memory({"action": "sources", "pointer": pointer, "principal": principal,
                         "offset": offset, "limit": 100})
        if listed.get("status") != "ok":
            return None, listed.get("status")
        rows += [s for s in listed.get("sources") or [] if s.get("originalPath") == path]
        if rows or listed.get("nextOffset") is None:
            break
        offset = listed["nextOffset"]
    return (rows[0] if rows else None), "ok"

def live_sha(path: str) -> str:
    """sha256 of the file as it is now."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def ask_evidence(principal: str, pointer: str, question: str, answer: str, path: str, sid) -> tuple:
    """(ticket result citing only `path`, None) or (None, why). The file is the one ask()
    already ranked, so its own passage is cited through assist; memory's separate
    retrieval (description-only ranking) never decides whether it can be saved. An older
    runtime without the open action falls back to search only to get an attempt id."""
    hit = memory({"action": "cached", "principal": principal, "question": question})
    if hit.get("status") == "verified-cache-hit":
        return hit, None
    out = memory({"action": "open", "pointer": pointer, "principal": principal, "question": question})
    if out.get("status") != "ok":
        out = memory({"action": "search", "pointer": pointer, "principal": principal, "question": question})
        if out.get("status") == "verified-cache-hit":
            return out, None
        if out.get("status") not in ("ready", "no-match", "refused") or not out.get("attemptId"):
            return None, f"cannot open {pointer}: {out.get('status')}"
    return file_evidence(principal, pointer, question, answer, path, sid,
                         {"attemptId": out["attemptId"]})

def send_approval(principal: str, question: str, answer: str, pointer: str, ticket_result: dict, sdir: Path,
                  approved_by: str = None, **fields) -> int:
    why = uncalibrated_why()  # the one place every save and approval passes through (--add included)
    if why:
        say(f"not saved: {why}", why)
        log(sdir, "approve", question=question, pointer=pointer, result="refused-uncalibrated", approved_by=approved_by)
        return 1
    approved_by = approved_by if approved_by is not None else f"principal:{principal}"
    evidence = [{"sourceId": p["sourceId"], "quote": p["reviewedText"]} for p in ticket_result.get("passages", [])[:3] if p.get("reviewedText")]
    res = memory({"action": "approve", "ticket": ticket_result["approvalTicket"], "principal": principal, "approved": True, "answer": answer, "evidence": evidence})
    ok = res.get("status") in ("approved", "saved", "ok")
    say(f"approve: {res.get('status')} {'' if ok else json.dumps(res)[:200]}", "saved" if ok else f"not saved ({res.get('status')})")
    log(sdir, "approve", question=question, pointer=pointer, result=res.get("status"), approved_by=approved_by)
    if ok:
        record_approver(sdir, question, approved_by, pointer=pointer, **fields)
    return 0 if ok else 1

def last_lookup(sdir: Path, question: str):
    """The last lookup record for this exact question that listed files, else None."""
    path = sdir / "lookups.jsonl"
    for line in reversed(path.read_text().splitlines() if path.is_file() else []):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("kind") == "lookup" and norm_q(rec.get("question") or "") == norm_q(question) and rec.get("top"):
            return rec
    return None

def find_top(sdir: Path, question: str):
    """The last lookup's top row {score, path, pointer} for this exact question."""
    rec = last_lookup(sdir, question)
    return rec["top"][0] if rec else None

def auto_cache_on() -> bool:
    """Default ON; SUPERJEV_AUTO_CACHE=0/off/false/no turns it off."""
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
    if verdict == "CLEAN" and ts_match and float(ts_match.group(1)) >= judges.profile().confidence_line:
        verdict = "TIME_SENSITIVE"
    return verdict, (min(float(x) for x in rows) if rows else None)

_LAST_WHY = {"why": None}

def not_saved(sdir: Path, question: str, why: str, plain: str = None) -> int:
    _LAST_WHY["why"] = why
    say(f"not saved: {why}", plain or why)
    log(sdir, "auto-approve", question=question, result="not-saved", why=why)
    return 1

SAVE_FLOOR = judges.profile().confidence_line  # the claim check must call the answer CLEAN at or above this

def secret_why(*texts):
    """Why the secret scan holds this save, or None. Every save runs it."""
    if any(has_secret(t) for t in texts if t):
        return f"secret-held: {HELD_SECRET}"
    return None

def gate_why(question: str, answer: str, path: str, passage=None):
    """(why the claim check refuses this save or None, its score). Every file-backed save runs it."""
    verdict, score = run_gate(f"Question: {question} Answer: {answer}", path, passage)
    if verdict != "CLEAN" or score is None:
        return f"check gate verdict {verdict} (only CLEAN saves)", None
    if score < SAVE_FLOOR:
        return f"check gate score {score:.2f} is below the {SAVE_FLOOR:.2f} auto-save floor", None
    return None, score

def save_answer(principal: str, question: str, answer: str, sdir: Path,
                top=None, approved_by: str = "auto-check", automatic: bool = True,
                claim_check: bool = True, stored: str = None, extra: dict = None) -> int:
    """The one way a file-backed answer is saved (a repeat win, --approve):
    the secret scan, the unchanged-file check, then the claim check (CLEAN >= SAVE_FLOOR) on the
    cited file, then the write. The caller's word is recorded (approved_by) but never skips a check.
    claim_check=False skips only the claim check, for a repeat win or a no-text --approve, where the
    N wins or the person vouch for the list. `stored` is the text
    kept as the answer when it differs from `answer` (which still ranks the cited passage).
    `extra` is a repeat win's ranked list, skill suggestions and leans-none flag, kept in the saved record.
    top is the file row {path, pointer} the caller chose, else the last lookup's top file.
    automatic=False (a person's --approve) ignores the --no-auto switch, never the checks."""
    _LAST_WHY["why"] = None
    why = uncalibrated_why()
    if why:
        return not_saved(sdir, norm_q(question), why)
    if automatic and not auto_cache_on():
        print("not saved: auto-cache is off (--no-auto or SUPERJEV_AUTO_CACHE=0); a human can still --approve")
        return 0
    key = norm_q(question)
    top = top or find_top(sdir, key)
    if not top or not top.get("path"):
        return not_saved(sdir, key, "no prior lookup with candidates for that exact question; run ask first")
    pointer, evidence_file = top["pointer"], top["path"]
    try:
        text = Path(evidence_file).read_text(errors="replace")
    except OSError as e:
        return not_saved(sdir, key, f"cannot read {evidence_file}: {e.strerror or e}")
    why = secret_why(text, key, answer)
    if why:
        return not_saved(sdir, key, why)
    row, status = source_row(principal, pointer, evidence_file)
    if status != "ok":
        return not_saved(sdir, key, f"stale: pointer {pointer} is {status}")
    if not row or row.get("contentSHA") != live_sha(evidence_file):
        return not_saved(sdir, key, f"stale: {evidence_file} changed since connect (or is not in {pointer})")
    out, why = ask_evidence(principal, pointer, key, answer, evidence_file, row.get("sourceId"))
    if out and out.get("status") == "verified-cache-hit":
        say("already cached")
        return 0
    if not out:
        return not_saved(sdir, key, f"{why}; nothing to save")
    if not claim_check:
        return send_approval(principal, key, stored or answer, pointer, out, sdir, approved_by=approved_by,
                             file=evidence_file, source_sha=row["contentSHA"], **(extra or {}))
    # The claim check reads exactly the passages that will be saved as evidence.
    why, score = gate_why(key, answer, evidence_file, evidence_text(out))
    if why:
        return not_saved(sdir, key, why, "the claim check did not pass it, so it was not saved")
    print(f"auto-check CLEAN (score {score:.2f}, evidence {evidence_file})")
    return send_approval(principal, key, stored or answer, pointer, out, sdir, approved_by=approved_by,
                         evidence_file=evidence_file, score=score,
                         file=evidence_file, source_sha=row["contentSHA"])

SAVE_AFTER_DEFAULT = 2

def save_after() -> int:
    """N: how many times in a row one file must win a question before it is saved
    (SUPERJEV_SAVE_AFTER, default 2)."""
    try:
        return max(1, int(os.environ.get("SUPERJEV_SAVE_AFTER", SAVE_AFTER_DEFAULT)))
    except ValueError:
        return SAVE_AFTER_DEFAULT

def win_of(top: list, possible: dict, notes: dict, skills=(), leans_none=False):
    """The win this ordinary ask records: its ranked files (up to 5), each with its bytes' hash,
    plus the skill suggestions and the leans-none note, so a saved hit equals the live result.
    No win when any of those files is possible-tier, unfinished or unreadable (a hit could
    then lose a warning or shorten the list)."""
    top = top[:5]
    if not top or any(p in possible or notes.get(p) == INCONCLUSIVE for _s, p, _ptr in top):
        return None
    try:
        files = [{"score": s, "path": p, "pointer": ptr, "sha": live_sha(p)} for s, p, ptr in top]
    except OSError:
        return None
    return {"path": files[0]["path"], "pointer": files[0]["pointer"], "sha": files[0]["sha"], "files": files,
            "skills": [{"name": n, "path": p} for n, p in skills], "leans_none": bool(leans_none)}

def win_count(sdir: Path, question: str, win: dict) -> int:
    """How many asks in a row (this one included) the same unchanged file won this question.
    Any other outcome for the question, or a --miss, starts the count over.
    Read off lookups.jsonl; no new store."""
    key, count = norm_q(question), 0
    me, first = judges.profile().name, judge_profile._read(judge_profile.PROFILES_PATH)[0]
    path = sdir / "lookups.jsonl"
    for line in (path.read_text().splitlines() if path.is_file() else []):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if norm_q(rec.get("question") or "") != key:
            continue
        if rec.get("kind") == "lookup" and (rec.get("result") == "stale-source" or rec.get("partial")):
            continue  # not a win and not a loss: the withheld saved answer, or a search that missed a set
        if rec.get("kind") == "lookup" and (rec.get("judge") or first) != me:
            continue  # another judge's ask: it neither adds to this judge's wins nor breaks them
        if rec.get("kind") == "lookup":
            w = rec.get("win") or {}
            count = count + 1 if (w.get("path"), w.get("sha")) == (win["path"], win["sha"]) else 0
        elif rec.get("kind") == "miss":
            count = 0
    return count

def autosave(principal: str, question: str, sdir: Path, win) -> bool:
    """The one rule for saving a file: once it has won this question N times in a row, save the
    FILE (path, pointer, content hash, question, principal, date), not an answer line. Each win
    already passed the content check ("does this file state the answer"), so no claim check runs
    here: the secret scan and the unchanged-file check still do. True when this ask saved the list."""
    if not win or not auto_cache_on() or win_count(sdir, question, win) < save_after():
        return False
    why = uncalibrated_why()
    if why:
        print(f"not saved: {why}")
        return False
    key = norm_q(question)
    if saved_record(sdir, key):  # reached a live search, so the saved file was withheld as stale
        for k in dict.fromkeys([key, question]):
            memory({"action": "forget", "principal": principal, "question": k})
        record_approver(sdir, key, None, removed_by="stale")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = save_answer(principal, key, key, sdir, top={"path": win["path"], "pointer": win["pointer"]},
                         approved_by="auto-save", claim_check=False, stored=f"Saved file: {win['path']}",
                         extra={k: win[k] for k in ("files", "skills", "leans_none") if win.get(k)})
    if rc == 0:
        n = len(win.get("files") or [])
        print(f"Saved for next time: {n} file{'s' if n != 1 else ''}, top: {win['path']}; it won this question "
              f"{save_after()} times; the next ask returns them at once, no search (--miss removes it).")
    else:
        print(f"not saved: {_LAST_WHY['why'] or 'the save was refused'}")
    return rc == 0

def miss(principal: str, question: str, actual: str, sdir: Path) -> int:
    """--miss: the saved answer is wrong. Removes it (and a manual note's own record) and exits 1,
    saying so, when there was nothing saved to remove."""
    key = norm_q(question)
    log(sdir, "miss", question=question, actual=actual)
    lookup_id = last_lookup_id(sdir, question)
    write_outcome(sdir, lookup_id, question, "wrong", file=actual)
    who = approver(sdir, question).get("approved_by", "human")
    removed = []
    for k in dict.fromkeys([key, question]):  # an older save sits under the exact wording
        res = memory({"action": "forget", "principal": principal, "question": k})
        if res.get("status") == "forgotten":
            removed += res.get("pointers") or []
        record = sdir / "manual" / f"{manual_pointer_name(principal, k)}.md"
        if record.is_file():  # a wrong --add fact must not stay searchable as a note
            memory({"action": "remove", "pointer": manual_pointer_name(principal, k), "principal": principal})
            record.unlink()
            removed.append(record.stem)
    if not removed:
        say("miss logged, but there is no saved answer for that question, so nothing was removed"
            + ("" if lookup_id else " (and no earlier lookup of it to mark wrong)"))
        return 1
    record_approver(sdir, key, None, removed_by="miss", was=who)
    _RESULT["removed"] = list(dict.fromkeys(removed))
    print("miss recorded")
    say(f"un-saved: the saved answer (approved_by: {who}) was removed from {', '.join(dict.fromkeys(removed))}",
        "the saved answer was removed")
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
    top. Unlike find_top (which scan backward through history
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
# terms, not one -- the v1.0.6 miss (a price-ceiling question
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

def approve(principal: str, question: str, answer, sdir: Path, rank=None, file=None) -> int:
    """--approve: a person's choice of file, saved through save_answer at once (the manual way
    to meet the repeat-win threshold; same secret scan as every other save). With an answer, the
    claim check runs on the chosen file. With no answer, the person vouches for the ranked list:
    it is saved as a repeat win saves it (the chosen file, else the top one, leads), no claim check."""
    why = uncalibrated_why()
    if why:
        say(f"not saved: {why}", why)
        return 1
    answer = answer.strip() if answer and answer.strip() else None
    chosen = None
    if rank is not None or file is not None:
        # The lead picked a listed candidate by hand (any rank, possible tier included).
        chosen, why = find_candidate(sdir, question, rank=rank, file=file)
        if not chosen:
            say(why)
            return 1
    if chosen is None:
        top = find_top(sdir, question)  # the file ask() ranked first, unless possible-only
        chosen = top if top and not top.get("possible") else None
    if not chosen:
        say("no confirmed top candidate for that question; run ask first, pick a listed "
            "file with --rank N or --file PATH, or use --add")
        return 1
    extra = {}
    if answer is None:
        rec = last_lookup(sdir, question)
        rows = ([chosen] + [r for r in rec["top"][:5] if r["path"] != chosen["path"]])[:5]
        if any(r.get("possible") for r in rec["top"][:5]):
            return not_saved(sdir, norm_q(question), "the list has a possible-tier file nobody checked; "
                             "give the answer text so the claim check runs (--rank N then picks the file)")
        try:
            files = [{"score": r["score"], "path": r["path"], "pointer": r["pointer"], "sha": live_sha(r["path"])} for r in rows]
            why = secret_why(*(Path(f["path"]).read_text(errors="replace") for f in files))
        except OSError as e:
            return not_saved(sdir, norm_q(question), f"cannot read {e.filename}: {e.strerror or e}")
        if why:
            return not_saved(sdir, norm_q(question), why)
        for f in files[1:]:
            r, st = source_row(principal, f["pointer"], f["path"])
            if st != "ok" or not r or r.get("contentSHA") != f["sha"]:
                return not_saved(sdir, norm_q(question), f"stale: {f['path']} changed since connect")
        win = rec.get("win") or {}
        extra = {"files": files, **{k: win[k] for k in ("skills", "leans_none") if win.get(k)}}
    rc = save_answer(principal, question, answer or norm_q(question), sdir, top=chosen,
                     approved_by=f"principal:{principal}", automatic=False,
                     **({"claim_check": False, "stored": f"Saved file: {chosen['path']}", "extra": extra} if answer is None else {}))
    if rc == 0:
        write_outcome(sdir, last_lookup_id(sdir, question), question, "right", file=chosen.get("path"))
    return rc

def approve_manual(principal: str, question: str, answer: str, pointer: str, source_id: str, record: Path, sdir: Path,
                   **fields) -> int:
    """Approve a just-registered manual pointer, falling back to assisted review on a retrieval miss."""
    out = memory({"action": "search", "pointer": pointer, "principal": principal, "question": question})
    if out.get("status") == "verified-cache-hit":
        print("already cached")
        return 0
    if out.get("status") == "ready":
        return send_approval(principal, question, answer, pointer, out, sdir, **fields)
    attempt_id = out.get("attemptId")
    if not attempt_id:
        print(f"cannot approve: search returned {out.get('status')} on {pointer} with no attempt to assist from.")
        log(sdir, "approve", question=question, pointer=pointer, result=out.get("status"))
        return 1
    last_line = len(record.read_text().splitlines())
    reason = f"manual entry for {pointer}: the record is a single small reviewed source and its own text is the answer."
    assisted = memory({"action": "assist", "attemptId": attempt_id, "principal": principal, "reason": reason,
                       "references": [{"sourceId": source_id, "startLine": 1, "endLine": last_line}]})
    if assisted.get("status") != "ready":
        print(f"cannot approve: assist returned {assisted.get('status')} on {pointer}.")
        log(sdir, "approve", question=question, pointer=pointer, result=assisted.get("status"))
        return 1
    return send_approval(principal, question, answer, pointer, assisted, sdir, **fields)

def add_manual(principal: str, question: str, answer: str, source, sdir: Path,
                kind: str = DEFAULT_KIND, status: str = DEFAULT_STATUS,
                as_of: str = None, subject: str = None, replace: bool = False) -> int:
    labels = validate_labels({
        "kind": kind, "status": status,
        "as_of": as_of or time.strftime("%Y-%m-%d"),
        "subject": subject or derive_subject(question),
    })
    key = norm_q(question)
    src = None
    if source:
        if not Path(source).is_file():
            print(f"refused: --source {source} does not exist; give the file this fact comes from, "
                  "or leave --source out to save it as a fact with no source file.")
            return 2
        src = Path(source).resolve()
    mine = my_pointers(principal)
    # A note saved before matching was normalised is named from the exact wording.
    pointer = next((n for n in (manual_pointer_name(principal, key), manual_pointer_name(principal, question))
                    if n in mine), manual_pointer_name(principal, key))
    exists = pointer in mine
    if exists and not replace:
        print(f"refused: {pointer} already exists for this question wording; use different wording, --replace-entry, or remove the pointer explicitly.")
        return 1
    # The same secret scan and claim check as every other save (a fact with no source file: the
    # secret scan only), before anything is registered or replaced.
    why = secret_why(question, answer, src.read_text(errors="replace") if src else None)
    score = None
    if not why and src:
        why, score = gate_why(key, answer, str(src))
    if why:
        return not_saved(sdir, key, why)
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
    if src:
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
    fields = ({"file": str(src), "source_sha": sha256_file(src), "score": score} if src
              else {"no_source": True})
    rc = approve_manual(principal, key, answer, pointer, source_id, record, sdir, **fields)
    if rc == 0:
        lid = last_lookup_id(sdir, question)
        prior = last_outcome(sdir, lid) if lid else None
        if prior and prior.get("result") == "wrong":
            added_file = str(src) if src else str(record)
            write_outcome(sdir, lid, question, "wrong-added", file=added_file)
    return rc

STATE_RANK = ("ready", "stale", "refreshing", "failed")  # worst last: a set is as bad as its worst part

def fold_status(sets: dict, name: str, status: str, principal: str) -> None:
    """Add one pointer to --status's per-set rows. A split part folds into its base set; the set's
    roots and note count come from its prepare report."""
    report, owner = auto_heal._report_for(name, prepare_bulk.CACHE_DIR)
    base = owner or name
    if str(status).startswith(("preparation-required", "refresh-required")):
        state = "refreshing" if auto_heal._lock_holder_alive(auto_heal._lock_path(principal, base)) else "stale"
    else:
        state = "ready" if status == "available" else "failed"
    row = sets.setdefault(base, {"name": base, "state": state})
    row["state"] = max(row["state"], state, key=STATE_RANK.index)
    if report and report.get("roots"):
        row["roots"] = report["roots"]
    if notes := sum(x.get("count") or 0 for x in (report or {}).get("parts") or [] if isinstance(x, dict)):
        row["notes"] = notes

def status_unavailable(line: str) -> int:
    """Status could not be read: say so (exit 1), and record it as an error so --json does not
    show a failed read as an empty, healthy list."""
    print(line)
    _done("error", line)
    return 1

def connection_status(principal: str, as_json: bool = False) -> int:
    """Read scoped registration metadata, without searching or refreshing. With as_json the sets are
    also recorded, folded by base set, for the one JSON object."""
    skill = skill_dir_for_display()
    panel = memory({"action": "panel", "principal": principal})
    sets = {}
    if (not isinstance(panel, dict) or panel.get("status") == "error"
            or not isinstance(panel.get("pointers"), list)):
        if isinstance(panel, dict) and panel.get("reason") == "not-set-up":
            print(f"Not set up. Next: python3 {skill / 'setup.py'}")
            _RESULT.update(next="setup", principal=principal, sets=[])
        else:
            return status_unavailable("Connection status unavailable. Next: check the memory connector configuration.")
        return 1
    pointers = panel["pointers"]
    if not pointers:
        print(f"Nothing connected for {principal}.")
        print(f"Next: follow {skill.parent / 'super-jev-connect' / 'SKILL.md'} to connect a folder.")
        _RESULT.update(next="connect", principal=principal, sets=[])
        return 0
    print(f"Connections for {principal} (registered snapshots, not a freshness guarantee):")
    for row in pointers:
        if isinstance(row, str):
            name, status = row, "unknown"
        elif isinstance(row, dict) and isinstance(row.get("pointer"), str):
            name = row["pointer"]
            status = row.get("snapshotStatus") or row.get("status") or "unknown"
        else:
            return status_unavailable("Connection status unavailable: malformed pointer metadata.")
        if as_json:
            fold_status(sets, name, status, principal)
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
    _RESULT.update(next="refresh" if any(r["state"] == "stale" for r in sets.values()) else "none",
                   principal=principal, sets=list(sets.values()))
    return 0


PREFLIGHT_QUESTIONS = (
    "have we already tried {about}, and how did it go",
    "which design rules and steps apply to {about}",
    "what errors, traps or quirks are known for {about}",
    "which code files and tests handle {about}",
)
PREFLIGHT_SKIP_DIRS = {"__pycache__", "node_modules", "prepare-cache", "autoheal-state"}  # generated, never notes
PREFLIGHT_STRONG = judges.profile().preflight_strong  # a confirmed hit at or above this means the topic is known
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
    if skill:
        # A paid check runs when its own inputs are ready: --about reads the connections (so it waits for
        # READY); --skill reads skill folders, which a fresh install already has.
        status, picks, err, fallback = skill_search(f"a skill that {skill}")
        if err:
            warnings.append(f"existing-skill search failed ({err}); not proof that no skill does this")
        elif status == "fallback":  # guesses are labelled, and the user is told why the judge did not run
            warnings.append(f"skill search fell back to local guesses: {fallback or 'no reason given'}")
        report["existing_skills"] = None if err else [name for name, _path in picks]
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
    """The principal and the rest of the arguments. A typed --principal with no value gives "" (never the
    environment's), so the caller can say it is missing."""
    if "--principal" in args:
        i = args.index("--principal")
        return (args[i + 1] if i + 1 < len(args) else ""), args[:i] + args[i + 2:]
    return os.environ.get("SUPERJEV_PRINCIPAL", ""), args


def need(what: str) -> int:
    """A required argument is missing: name it, then the usage. Exit code 2."""
    say(f"{what} is required")
    print(f"\n{__doc__}")
    return 2


def main() -> int:
    try:
        return _main()
    except SecretHeld as e:
        print(f"ask: {e}", file=sys.stderr)
        return 1

def version() -> str:
    """The release, from the repo's package.json: the one version source the terminal app also reads."""
    try:
        return json.loads((Path(__file__).resolve().parents[2] / "package.json").read_text())["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return "unknown"

JSON_REQUESTS = ("--", "--status", "--miss", "--approve", "--claim")

def json_refusal(a: list) -> str:
    """Why --json will not run this request (before any work), or "". It covers an ask,
    --claim (one statement), --status, --approve and --miss."""
    if a[0].startswith("--") and a[0] not in JSON_REQUESTS:
        return f"--json covers an ask, --claim, --status, --approve and --miss, not {a[0]}"
    if a[0] == "--claim" and len(a) != 2:
        return "--json checks one statement at a time: use --claim STATEMENT once"
    return ""

def run_json(principal: str, a: list) -> int:
    """--json: do the request exactly as text mode does, with its output held back, then print one
    JSON object from the same result (contract C1, v: 1) and return the same exit code."""
    _RESULT.clear()
    buf, crash = io.StringIO(), None
    try:
        with contextlib.redirect_stdout(buf):
            rc = _dispatch(principal, a, as_json=True)
    except SecretHeld as e:  # exits 1, as main() does in text mode
        rc, crash = 1, str(e)
    except Exception as e:  # any other crash is an error, exit 3 (text mode lets it fall out)
        traceback.print_exc()
        rc, crash = OUTCOME_EXIT["error"], _redact(f"{type(e).__name__}: {e}")
    lines = [crash] if crash else [ln.strip() for ln in buf.getvalue().splitlines() if ln.strip()]
    if a[:1] == ["--status"] and "sets" in _RESULT and not crash:
        out = dict(_RESULT)
    elif a[:1] in (["--approve"], ["--miss"]):  # done is the exit code; why is the reason recorded where it was decided
        out = {"done": rc == 0, "why": crash or _RESULT.get("why", ""), **({"removed": _RESULT["removed"]} if _RESULT.get("removed") else {})}
    else:
        if crash or "outcome" not in _RESULT:  # a crash, or refused before any search: the same shape, with the reason
            _RESULT.clear()
            _done("error" if crash or rc != 2 else "not-supported", lines[0] if lines else "no result", "",
                  "rephrase" if rc == 2 and not crash else "none")
        out = {k: v for k, v in _RESULT.items() if v and k != "cmd"}
        if out.get("files"):
            out["files"] = [{k: v for k, v in f.items() if k not in _FILE_PRIVATE} for f in out["files"]]
    print(json.dumps({"v": 1, **out}))
    return rc

def _main() -> int:
    principal, a = resolve_principal(sys.argv[1:])
    if a[:1] == ["--json"]:  # only a leading flag: after `--`, or inside a question, it is just words
        return run_json(principal, a[1:])
    return _dispatch(principal, a)

def _dispatch(principal: str, a: list, as_json: bool = False) -> int:
    if not sys.argv[1:] or sys.argv[1:] == ["--help"]:  # asking for the usage, not missing anything
        print(__doc__)
        return 2
    if a == ["--version"]:  # no principal needed
        print(f"Super Jev {version()}")
        return 0
    if not principal:
        return need("--principal AGENT (or the SUPERJEV_PRINCIPAL environment variable)")
    if not a:
        return need("a question")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", principal):  # same rule as the memory runtime
        say(f"invalid --principal {principal!r}: use the agent's exact name (letters, digits, "
            "'.', '_', '-'; no spaces or slashes)")
        return 2
    if as_json and (why := json_refusal(a)):
        print(why)
        return 2
    if a[0] == "--":  # the rest is the question, read literally (never a --flag)
        if len(a) == 1:
            return need("a question")
        return lookup(" ".join(a[1:]), principal, state_dir(principal))
    if a[0] == "--preflight":
        return preflight(principal, a[1:])
    if a[0] == "--index-update":
        return index_sync(principal, state_dir(principal))
    if a[0] == "--index-update-if-on":  # connect/refresh: the flag decides, so flag off writes nothing
        return index_sync(principal, state_dir(principal)) if index_enabled() else 0
    if a[0] == "--status":
        if len(a) != 1:
            print("usage: --principal AGENT --status")
            return 2
        return connection_status(principal, as_json)
    sdir = state_dir(principal)
    if "--no-auto" in a:
        os.environ["SUPERJEV_AUTO_CACHE"] = "0"
        a = [x for x in a if x != "--no-auto"]
        if not a:
            return need("a question")
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
        return trace_report(sdir, days, principal)
    if a[0] == "--miss":
        if len(a) < 2:
            return need("a question after --miss")
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
    if a[0] == "--approve":
        rest, rank, file = a[1:], None, None
        try:
            while "--rank" in rest:
                i = rest.index("--rank"); rank = int(rest[i + 1]); del rest[i:i + 2]
            while "--file" in rest:
                i = rest.index("--file"); file = rest[i + 1]; del rest[i:i + 2]
        except (IndexError, ValueError):
            rest = []
        if len(rest) not in (1, 2) or (rank is not None and file is not None):
            say('usage: --approve "question" ["answer"] [--rank N | --file PATH]')
            return 2
        return approve(principal, rest[0], rest[1] if len(rest) == 2 else None, sdir, rank=rank, file=file)
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
                print(f"ERROR: this statement could not be checked ({_redact(f'{type(e).__name__}: {e}')}); "
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
