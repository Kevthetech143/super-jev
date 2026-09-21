#!/usr/bin/env python3
"""Super Jev auto-catch: approved hits get captured for reuse without the agent
remembering a command.

Switch: <sdir>/auto-catch.json {"autoCatch": bool}, default false. Off is
byte-identical to today's behavior: no receipt, no check call, no cache write.

Flow:
  ask (autoCatch on) -> write <sdir>/receipts/<principal>.json
  --opened PATH      -> mark a candidate opened (Claude Code PostToolUse hook calls it on Read)
  --done "answer"    -> pick evidence file, run the existing check door ONCE,
                       approve only past the gate; cache the FILE'S QUOTES
                       (verbatim) through the existing --approve and --add
                       paths; always append one line to <sdir>/auto-catch.log.

Everything harness-shaped (check/approve/add) arrives as injectable callables
so tests run fully offline; ask.py wires the live ones (dispatch.py check,
ask.approve, ask.add_manual). No second copy of any door lives here.
"""
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

CONFIG_FILE = "auto-catch.json"
RECEIPT_TTL_S = 10 * 60          # receipt older than this -> not-caught, no check
SCORE_GAP = 0.20                 # top candidate must beat #2 by this, else close-call
SUPPORT_LINE = 0.80              # every claim row must be SUPPORTED at/above this
TIME_SENS_LINE = 0.90            # time_sensitive must be NOT_TIME_SENSITIVE at/above this
MAX_CLAIM_CHARS = 1500
MAX_QUOTE_LINES = 20

# Same row shape both doors print (see superjev.py _FLAG_LINE_RE): one line per
# claim (c<N> VERDICT score) and one per draft-level flag
# (overclaim/time_sensitive/leaked_internal/self_contradictory VERDICT score).
_FLAG_RE = re.compile(
    r"^\s*(?P<key>c\d+|leaked_internal|time_sensitive|self_contradictory|overclaim)"
    r"\s+(?P<verdict>[A-Z][A-Z_]*)\s+(?P<score>\d+\.\d+)", re.MULTILINE)

_SENT_RE = re.compile(r"[^.!?]+[.!?]+")
_QUOTE_RE = re.compile(r'"([^"]{4,})"')


def _sdir_paths(sdir: Path):
    return sdir / CONFIG_FILE, sdir / "receipts", sdir / "auto-catch.log", sdir / "auto-catch-feedback.jsonl"


def enabled(sdir: Path) -> bool:
    """autoCatch switch; missing/corrupt config file means off."""
    try:
        return bool(json.loads((sdir / CONFIG_FILE).read_text()).get("autoCatch", False))
    except Exception:
        return False


def set_enabled(sdir: Path, on: bool) -> str:
    """Flip the switch; returns the resulting config line for --auto-catch output."""
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / CONFIG_FILE).write_text(json.dumps({"autoCatch": bool(on)}) + "\n")
    return "autoCatch=true" if on else "autoCatch=false"


def question_hash(question: str) -> str:
    return hashlib.sha1(question.encode()).hexdigest()[:10]


def write_receipt(sdir: Path, principal: str, question: str, candidates: list, attempt_id=None) -> Path:
    """Overwrite the one live receipt for this principal. candidates: [{path, pointer, score}]."""
    _, rdir, _, _ = _sdir_paths(sdir)
    rdir.mkdir(parents=True, exist_ok=True)
    rp = rdir / f"{principal}.json"
    rp.write_text(json.dumps({
        "question": question, "attemptId": attempt_id, "ts": time.time(),
        "candidates": [{"path": c.get("path"), "pointer": c.get("pointer"),
                        "score": float(c.get("score", 0))} for c in candidates],
        "opened": [],
    }) + "\n")
    return rp


def load_receipt(sdir: Path, principal: str):
    rp = sdir / "receipts" / f"{principal}.json"
    if not rp.is_file():
        return None
    try:
        return json.loads(rp.read_text())
    except Exception:
        return None


def mark_opened(sdir: Path, principal: str, path: str) -> bool:
    """Mark a candidate opened. True if a live receipt had that path, else False."""
    receipt = load_receipt(sdir, principal)
    if not receipt:
        return False
    paths = {c.get("path") for c in receipt.get("candidates", [])}
    if path not in paths:
        return False
    opened = receipt.setdefault("opened", [])
    if path not in opened:
        opened.append(path)
    (sdir / "receipts" / f"{principal}.json").write_text(json.dumps(receipt) + "\n")
    return True


def mark_done(sdir: Path, principal: str, decision: str) -> None:
    receipt = load_receipt(sdir, principal)
    if receipt is not None:
        receipt["done"] = decision
        (sdir / "receipts" / f"{principal}.json").write_text(json.dumps(receipt) + "\n")


def log_line(sdir: Path, decision: str, principal: str, question: str, scores=None) -> None:
    """Always exactly one line per --done decision (and per stale sweep): the
    ts/principal/question-hash/decision/scores record the rollout reads."""
    _, _, logp, _ = _sdir_paths(sdir)
    sdir.mkdir(parents=True, exist_ok=True)
    logp.open("a").write(json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "principal": principal,
        "question_hash": question_hash(question or ""), "decision": decision,
        "scores": scores or {},
    }) + "\n")


def pending_catch(sdir: Path, principal: str, question: str, reason: str, flags=None) -> None:
    """Local evidence artifact for catches that must NOT be cached (plan 3f).
    Per references/feedback.md: durable private artifact in the project."""
    _, _, _, fbp = _sdir_paths(sdir)
    sdir.mkdir(parents=True, exist_ok=True)
    fbp.open("a").write(json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": "pending-catch",
        "principal": principal, "question": question[:200], "reason": reason,
        "flags": flags or {},
    }) + "\n")


def choose_evidence(receipt: dict):
    """Plan 3b. Returns (mode, candidate|None). mode in opened|top|close-call|no-candidates."""
    ranked = sorted(receipt.get("candidates", []), key=lambda c: c.get("score", 0), reverse=True)
    opened = set(receipt.get("opened", []) or [])
    for c in ranked:                       # first opened candidate, in rank order
        if c.get("path") in opened:
            return "opened", c
    if not ranked:
        return "no-candidates", None
    if len(ranked) == 1:
        return "top", ranked[0]
    if ranked[0].get("score", 0) - ranked[1].get("score", 0) >= SCORE_GAP:
        return "top", ranked[0]
    return "close-call", None            # too close to call: self-grading guard


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def claim_sentences(answer: str, file_text: str) -> str:
    """Claim = answer sentences that cite/quote the file; fallback: whole answer (<=1500 chars)."""
    norm_file = _norm(file_text)
    kept = []
    for m in _SENT_RE.finditer(answer):
        sent = m.group(0).strip()
        spans = [s for s in _QUOTE_RE.findall(sent) if _norm(s) in norm_file]
        if spans:
            kept.append(sent)
    claim = " ".join(kept) if kept else answer
    return claim[:MAX_CLAIM_CHARS]


def extract_file_quotes(answer: str, file_text: str) -> str:
    """The FILE'S quotes (verbatim lines), never the agent's prose: for every
    quoted span in the answer that occurs verbatim in the file, take the file's
    own line(s) containing it, deduped, in file order. Empty string = none."""
    norm_file = _norm(file_text)
    spans = {s for s in _QUOTE_RE.findall(answer) if _norm(s) in norm_file}
    if not spans:
        return ""
    out, seen = [], set()
    for line in file_text.splitlines():
        nline = _norm(line)
        if nline and any(_norm(s) in nline for s in spans) and nline not in seen:
            seen.add(nline)
            out.append(line.strip())
        if len(out) >= MAX_QUOTE_LINES:
            break
    return "\n".join(out)


def parse_flags(text: str) -> dict:
    """Parse the check door's reply table. Returns {claims: [(verdict, score)], flags: {key: (verdict, score)}}."""
    claims, flags = [], {}
    for m in _FLAG_RE.finditer(text):
        key, verdict, score = m.group("key"), m.group("verdict"), float(m.group("score"))
        if key.startswith("c"):
            claims.append((verdict, score))
        else:
            flags[key] = (verdict, score)
    return {"claims": claims, "flags": flags}


def gate_pass(parsed: dict):
    """Plan 3d: SUPPORTED>=0.80 on every claim row, overclaim=HONEST,
    time_sensitive=NOT_TIME_SENSITIVE>=0.90, leaked_internal=CLEAN."""
    claims, flags = parsed["claims"], parsed["flags"]
    if not claims:
        return False, "no claim rows parsed from check output"
    for verdict, score in claims:
        if verdict != "SUPPORTED" or score < SUPPORT_LINE:
            return False, "claim %s %.2f below SUPPORTED>=%.2f" % (verdict, score, SUPPORT_LINE)
    if flags.get("overclaim", (None, 0))[0] != "HONEST":
        return False, "overclaim=%s" % (flags.get("overclaim", ("?", 0))[0],)
    ts_v, ts_s = flags.get("time_sensitive", (None, 0))
    if ts_v != "NOT_TIME_SENSITIVE" or ts_s < TIME_SENS_LINE:
        return False, "time_sensitive=%s %.2f" % (ts_v, ts_s)
    if flags.get("leaked_internal", (None, 0))[0] != "CLEAN":
        return False, "leaked_internal=%s" % (flags.get("leaked_internal", ("?", 0))[0],)
    return True, "pass"


def default_check(claim: str, file_path: str) -> dict:
    """The existing check door (dispatch.py check -> superjev gate), parsed. One Jev call."""
    skill = Path(__file__).resolve().parent
    r = subprocess.run([sys.executable, str(skill / "dispatch.py"), "check",
                        "--claim", claim, file_path],
                       capture_output=True, text=True, timeout=120)
    return parse_flags(r.stdout + "\n" + r.stderr)


def do_done(answer: str, principal: str, sdir: Path,
            check_fn=None, approve_fn=None, add_fn=None) -> int:
    """Plan 3: the catch. check/approve/add_fn injectable; ask.py passes the live doors."""
    if not enabled(sdir):
        return 0                                   # off = today's behavior, quietly
    if check_fn is None:
        check_fn = default_check
    receipt = load_receipt(sdir, principal)
    question = (receipt or {}).get("question", "")
    if receipt is None or time.time() - receipt.get("ts", 0) > RECEIPT_TTL_S:
        log_line(sdir, "not-caught", principal, question)   # missing or stale receipt
        if receipt is not None:
            mark_done(sdir, principal, "not-caught")
        return 0
    mode, cand = choose_evidence(receipt)
    if mode in ("close-call", "no-candidates"):
        scores = {c["path"]: c["score"] for c in
                  sorted(receipt.get("candidates", []), key=lambda c: -c.get("score", 0))[:2]}
        log_line(sdir, mode, principal, question, scores)     # nothing cached
        mark_done(sdir, principal, mode)
        return 0
    path = cand["path"]
    try:
        file_text = Path(path).read_text()
    except Exception:
        pending_catch(sdir, principal, question, "evidence-file-missing")
        log_line(sdir, "pending-catch", principal, question, {"reason": "evidence-file-missing"})
        mark_done(sdir, principal, "pending-catch")
        return 0
    claim = claim_sentences(answer, file_text)
    parsed = check_fn(claim, path)                            # the ONE Jev call per --done
    ok, reason = gate_pass(parsed)
    flag_summary = {k: list(v) for k, v in parsed["flags"].items()}
    flag_summary["claims"] = [[v, s] for v, s in parsed["claims"]]
    if not ok:
        pending_catch(sdir, principal, question, reason, flag_summary)
        log_line(sdir, "pending-catch", principal, question, flag_summary)
        mark_done(sdir, principal, "pending-catch")
        return 0
    quotes = extract_file_quotes(answer, file_text)
    if not quotes.strip():
        pending_catch(sdir, principal, question, "no-verbatim-quotes", flag_summary)
        log_line(sdir, "pending-catch", principal, question, flag_summary)
        mark_done(sdir, principal, "pending-catch")
        return 0
    # Both lanes, existing paths only: --approve caches the file's quotes verbatim,
    # --add with --source <file> writes the search-lane record (hash freshness applies).
    approve_fn(question, quotes)
    add_fn(question, quotes, path)
    log_line(sdir, "approved", principal, question, flag_summary)
    mark_done(sdir, principal, "approved")
    return 0
