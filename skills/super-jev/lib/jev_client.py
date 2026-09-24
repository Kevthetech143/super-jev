#!/usr/bin/env python3
"""jev_client.py -- the built-in judge client Super Jev uses by default.

Checks claims against evidence files with TypeSafe Jev. Needs only
TYPESAFE_API_KEY in the environment; the key is never printed or logged.

    python3 jev_client.py EVIDENCE [EVIDENCE ...] --kit reply --claim "..." [--claim ...]
    python3 jev_client.py EVIDENCE [EVIDENCE ...] --kit reply --claims-file FILE
    python3 jev_client.py EVIDENCE [EVIDENCE ...] --kit reply --draft FILE

Prints one row per claim ("  c1   SUPPORTED      0.97  <claim>") plus four
draft-level rows, in the table shape superjev.py parses. Exit codes:
  0  every row is fine and at or above the 0.80 line
  3  a row is red (NOT_SUPPORTED, CONTRADICTED, ...) or under the line
  1  usage, key, network or size error -- never a verdict

SUPERJEV_GATE_CMD, when set, replaces this client entirely.
"""
import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
MAX_STATE_CHARS = 110_000   # a safe margin under Jev's 32,768-token input ceiling
LINE = 0.80                 # under this confidence a human reads the source
MAX_QUESTIONS = 255
MIN_CLAIM_WORDS = 4

CLAIM_CRITERIA = {"SUPPORTED": "The evidence confirms it.",
                  "NOT_SUPPORTED": "The evidence does not address it.",
                  "CONTRADICTED": "The evidence disproves it."}

DRAFT_QUESTIONS = {
    "leaked_internal": {
        "type": "choice",
        "instructions": "Does the DRAFT contain internal working material that should not go to the "
                        "reader, such as assistant notes, agent chatter, or paths to secrets?",
        "criteria": {"CLEAN": "nothing internal appears in the draft",
                     "HAS_LEAKS": "internal notes, agent chatter, or a path to a secret appears"}},
    "time_sensitive": {
        "type": "choice",
        "instructions": "Does the DRAFT assert a fact whose truth depends on a date after 2024, such as "
                        "a current price, version or schedule? Answer only whether one is present.",
        "criteria": {"NOT_TIME_SENSITIVE": "no claim depends on anything after 2024",
                     "TIME_SENSITIVE": "at least one claim depends on a post-2024 fact"}},
    "self_contradictory": {
        "type": "choice",
        "instructions": "Does the EVIDENCE contradict itself anywhere?",
        "criteria": {"CONSISTENT": "the evidence is internally consistent",
                     "SELF_CONTRADICTORY": "the evidence contradicts itself"}},
    "overclaim": {
        "type": "choice",
        "instructions": "Does the DRAFT state something as tested, verified or done when the EVIDENCE "
                        "shows it only inferred, planned or partial?",
        "criteria": {"HONEST": "the draft's certainty matches the evidence",
                     "OVERCLAIMS": "the draft claims more certainty than the evidence carries"}},
}

# An explicit --claim check (no draft) is decided by its claim rows plus overclaim.
# These draft-level rows judge an outbound letter; against a bare claim on an
# internal note they misfire (HAS_LEAKS / SELF_CONTRADICTORY on a plain note blocked
# SUPPORTED 1.00 claims, fleet hand tests 2026-09-24), so there they are advisory.
CLAIM_ADVISORY = {"leaked_internal", "time_sensitive", "self_contradictory"}

# The good label of each draft-level question; any other side label (or none) blocks.
FAVORABLE = {k for q in DRAFT_QUESTIONS.values() for k in q["criteria"] if k not in {
    "HAS_LEAKS", "TIME_SENSITIVE", "SELF_CONTRADICTORY", "OVERCLAIMS"}}
RED = {"NOT_SUPPORTED", "CONTRADICTED", "HAS_LEAKS", "TIME_SENSITIVE",
       "SELF_CONTRADICTORY", "OVERCLAIMS"}


class JevError(RuntimeError):
    """A call that produced no verdict. Always exit 1, never a pass."""


def _tls_context():
    """certifi's CA bundle when installed and SSL_CERT_FILE is unset (some Macs ship none)."""
    if os.environ.get("SSL_CERT_FILE"):
        return None
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def _http_post(url, body, headers, timeout):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout, context=_tls_context()) as r:
        return json.loads(r.read())


# Tests replace this with a fake; production posts over HTTPS.
transport = _http_post


def ask(state, questions, timeout=120, attempts=4):
    """One Jev call for every question. Returns answers plus model/usage metadata."""
    # The one place a request leaves for TypeSafe: scan the state and every question's
    # instructions and criteria here, so no caller can skip it.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from prepare_bulk import payload_has_secret
    if payload_has_secret(state) or payload_has_secret(questions):
        raise JevError("the request contains a secret; not sent")
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key:
        raise JevError("TYPESAFE_API_KEY is not set -- export it first "
                       "(export TYPESAFE_API_KEY=\"$(cat /path/to/your/key-file)\")")
    if len(state) > MAX_STATE_CHARS:
        raise JevError("evidence exceeds the 32,768-token ceiling -- split the file; "
                       "it is never truncated")
    body = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode()
    # The TypeSafe edge rejects the default "Python-urllib" user agent with 403.
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "User-Agent": "super-jev (+https://github.com/Kevthetech143/super-jev)"}
    delay, t0 = 1.0, time.monotonic()
    for attempt in range(attempts):
        try:
            data = transport(os.environ.get("SUPERJEV_JEV_URL", API_URL), body, headers, timeout)
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and attempt < attempts - 1:
                time.sleep(delay)
                delay *= 2
                continue
            if e.code == 401:
                raise JevError("401 -- TypeSafe rejected the API key") from None
            raise JevError(f"TypeSafe returned HTTP {e.code}") from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise JevError(f"could not reach TypeSafe: {e.__class__.__name__}") from None
    if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
        raise JevError("TypeSafe reply had no answers")
    return {"answers": data["answers"], "model": data.get("model"), "chunks": 1,
            "input_tokens": (data.get("usage") or {}).get("input_tokens", 0),
            "latency_ms": round((time.monotonic() - t0) * 1000)}


def split_claims(draft):
    """Sentences of four words or more; machine tags like [BOARD: ...] dropped."""
    out = []
    for line in draft.splitlines():
        line = line.strip()
        if not line or re.match(r"^\[[A-Z][A-Z0-9 _-]*:.*\]$", line):
            continue
        out += [s.strip() for s in re.split(r"(?<=[.!?])\s+", line)
                if len(s.split()) >= MIN_CLAIM_WORDS]
    return out


def questions_for(claims):
    if not claims:
        raise JevError("no checkable claims -- pass --claim, --claims-file, or a --draft "
                       "with a sentence of four words or more")
    if len(claims) + len(DRAFT_QUESTIONS) > MAX_QUESTIONS:
        raise JevError(f"{len(claims)} claims is too many for one call -- split the draft")
    qs = {f"c{i}": {"type": "choice", "criteria": CLAIM_CRITERIA,
                    "instructions": "Given ONLY the evidence, is this claim supported, not "
                                    f"supported, or contradicted?\n\nCLAIM: {c}"}
          for i, c in enumerate(claims, 1)}
    qs.update(DRAFT_QUESTIONS)
    return qs


def row(key, answer, subject="", side=False):
    """A missing or malformed answer is NO_ANSWER at 0.00 -- it always needs a human.
    A side (draft-level) row only blocks on a red label: a favorable label at low
    confidence is not evidence of a problem, so the 0.80 line applies to claims."""
    answer = answer if isinstance(answer, dict) else {}
    verdict = answer.get("choice") if isinstance(answer.get("choice"), str) else "NO_ANSWER"
    try:
        conf = float(answer.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return {"key": key, "subject": subject, "verdict": verdict, "confidence": conf,
            "flag": (verdict not in FAVORABLE) if side else (verdict != "SUPPORTED" or conf < LINE)}


def check(evidence, claims, draft=""):
    """evidence: [(path, text)]. Returns (rows, meta, exit_code)."""
    state = ("EVIDENCE:\n" + "\n\n".join(f"=== {p} ===\n{t.strip()}" for p, t in evidence)
             + f"\n\nDRAFT:\n{draft.strip()}")
    res = ask(state, questions_for(claims))
    rows = [row(f"c{i}", res["answers"].get(f"c{i}"), c) for i, c in enumerate(claims, 1)]
    rows += [row(k, res["answers"].get(k), side=True) for k in DRAFT_QUESTIONS]
    return rows, res, (3 if any(r["flag"] for r in rows) else 0)


def print_table(rows, meta, n_claims, side_advisory=False):
    print(f"\njev {meta.get('model')} \xb7 {meta.get('chunks', 1)} chunk(s) \xb7 "
          f"{meta.get('input_tokens', 0)} in_tok \xb7 {meta.get('latency_ms', 0)}ms\n")
    for r in rows[:n_claims]:
        print(f"  {r['key']:4s} {r['verdict']:14s} {r['confidence']:.2f}  {r['subject'][:70]}")
    print()
    for r in rows[n_claims:]:
        print(f"  {r['key']:18s} {r['verdict']:20s} {r['confidence']:.2f}")
    if side_advisory:
        print("  (leaked_internal, time_sensitive, self_contradictory are advisory on a --claim check)")
    need = [r["key"] for r in rows if r["flag"] and not (side_advisory and r["key"] in CLAIM_ADVISORY)]
    print(f"\n  {len(need)} of {len(rows)} need a human: {', '.join(need) or 'none'}")
    print(f"  the line is {LINE:.2f} -- under it, read the source before you speak.\n")


def main(argv=None):
    p = argparse.ArgumentParser(description="Super Jev built-in judge client (TypeSafe Jev).")
    p.add_argument("evidence", nargs="*", help="evidence files you actually read")
    p.add_argument("--kit", default="reply", choices=["reply"])
    p.add_argument("--claim", action="append", default=[])
    p.add_argument("--claims-file")
    p.add_argument("--draft")
    a = p.parse_args(argv)
    try:
        if not a.evidence:
            raise JevError("needs at least one evidence file")
        evidence = [(f, open(os.path.expanduser(f), encoding="utf-8", errors="replace").read())
                    for f in a.evidence]
        # Evidence goes to the Jev API, so it gets the same secret scan connect uses.
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from prepare_bulk import has_secret
        for f, text in evidence:
            if has_secret(text):
                raise JevError(f"evidence file {f} contains a secret; not sent")
        claims = list(a.claim)
        if a.claims_file:
            claims += [l.strip() for l in open(os.path.expanduser(a.claims_file), encoding="utf-8") if l.strip()]
        draft = open(os.path.expanduser(a.draft), encoding="utf-8").read() if a.draft else ""
        if not claims:
            claims = split_claims(draft)
        # The draft and every claim go into the request too, so they get the same scan.
        if has_secret(draft):
            raise JevError("the draft contains a secret; not sent")
        if any(has_secret(c) for c in claims):
            raise JevError("a claim contains a secret; not sent")
        rows, meta, code = check(evidence, claims, draft)
        side_advisory = bool(a.claim) and not a.claims_file and not a.draft
        if side_advisory and code == 3:
            code = 3 if any(r["flag"] for r in rows if r["key"] not in CLAIM_ADVISORY) else 0
    except (JevError, OSError) as e:
        print(f"jev: {e}", file=sys.stderr)
        return 1
    print_table(rows, meta, len(claims), side_advisory)
    return code


if __name__ == "__main__":
    sys.exit(main())
