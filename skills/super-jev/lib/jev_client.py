#!/usr/bin/env python3
"""jev_client.py -- the Jev implementation behind the judge doorway (skills/super-jev/judges).

Nothing else in the skill talks to TypeSafe: callers use `judges.ask` and the door
script. The endpoint, model, key variable name and limits come from the profile
(judge_profiles.json); the key is never printed or logged.

Checks claims against evidence files with TypeSafe Jev.

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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from judge_profile import PROFILE, judge_tokens as estimate_tokens  # noqa: E402
import judges  # noqa: E402
from judges.errors import (JudgeError, AuthRejected, BadReply, Overloaded, SecretBlocked,  # noqa: E402
                           TooBig, Unreachable)

API_URL = PROFILE.api_url
URL_ENV = PROFILE.api_url_env
VENDOR = PROFILE.vendor   # the judge named in error text
CONFIDENCE_FIELD = PROFILE.confidence_field
MODEL = PROFILE.model
# The judge's input ceiling (the profile's window) covers the
# state plus the longest question. A character cap sized for prose let number-dense
# text (IDs, dates, amounts) through over the ceiling, so tokens are estimated on the
# high side instead.
MAX_INPUT_TOKENS = PROFILE.call_tokens
LINE = PROFILE.confidence_line   # under this confidence a human reads the source
MAX_QUESTIONS = PROFILE.max_questions_per_call
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


_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _key_may_go_to(url):
    """The key goes only to the profile's own host (same scheme and host as its api_url) or to
    this machine, so a URL override can never carry it to a third party."""
    from urllib.parse import urlsplit
    mine, want = urlsplit(API_URL), urlsplit(url)
    return (want.hostname or "") in _LOOPBACK or (want.scheme, want.hostname) == (mine.scheme, mine.hostname)


def _tls_context():
    """certifi's CA bundle when installed and SSL_CERT_FILE is unset (some Macs ship none)."""
    if os.environ.get("SSL_CERT_FILE"):
        return None
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A judge never redirects a request: following one would carry the Authorization header along."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BadReply(f"the judge answered with a redirect (HTTP {code}); not followed, the API key is never re-sent")


def _http_post(url, body, headers, timeout):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=_tls_context()))
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read())


# Tests replace this with a fake; production posts over HTTPS.
transport = _http_post


_TOO_BIG_WORDS = ("too large", "too long", "too big", "context length", "context window",
                  "token limit", "maximum context", "exceeds the", "payload too")


def _says_too_big(err):
    """True only when the error body clearly says the input was over the limit. The local
    size check already raised TooBig before sending, so a bare 400 is a bad request."""
    try:
        body = err.read().decode("utf-8", "replace").lower()
    except Exception:
        return False
    return any(w in body for w in _TOO_BIG_WORDS)


def ask(state, questions, timeout=120):
    """One Jev call for every question. Returns answers plus model/usage metadata.
    Every failure is a typed judges.errors error: no verdict, never a pass."""
    # The one place a request leaves for TypeSafe: scan the state and every question's
    # instructions and criteria here, so no caller can skip it.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from prepare_bulk import payload_has_secret
    if payload_has_secret(state) or payload_has_secret(questions):
        raise SecretBlocked("the request contains a secret; not sent")
    key = judges.require_key()
    longest = max((estimate_tokens(q) for q in questions.values()), default=0)
    if estimate_tokens(state) + longest > MAX_INPUT_TOKENS:
        raise TooBig(f"evidence exceeds {PROFILE.ceiling_text} -- split the file; "
                     "it is never truncated")
    body = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode()
    # The TypeSafe edge rejects the default "Python-urllib" user agent with 403.
    headers = {"Content-Type": "application/json",
               "User-Agent": "super-jev (+https://github.com/Kevthetech143/super-jev)"}
    if key:   # a keyless judge (key_required false in its profile) is sent no Authorization
        headers = {"Authorization": f"Bearer {key}", **headers}
    url = (os.environ.get(URL_ENV) if URL_ENV else None) or API_URL
    if key and not _key_may_go_to(url):
        raise AuthRejected(f"{URL_ENV} points at a host other than this judge's own; the API key is "
                           "never sent there, so nothing was sent")
    t0 = time.monotonic()

    def send():
        try:
            return transport(url, body, headers, timeout)
        except urllib.error.HTTPError as e:
            if e.code in PROFILE.overloaded_statuses:
                raise Overloaded(f"{VENDOR} returned HTTP {e.code}") from None
            if e.code == 401:
                raise AuthRejected(f"401 -- {VENDOR} rejected the API key") from None
            if e.code == PROFILE.too_big_status and _says_too_big(e):
                raise TooBig(f"{VENDOR} returned HTTP {e.code}") from None
            raise BadReply(f"{VENDOR} returned HTTP {e.code}") from None
        except ValueError as e:
            raise BadReply(f"could not reach {VENDOR}: {e.__class__.__name__}") from None
        except (urllib.error.URLError, OSError) as e:
            raise Unreachable(f"could not reach {VENDOR}: {e.__class__.__name__}") from None

    data = judges.with_retry(send)
    if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
        raise BadReply(f"{VENDOR} reply had no answers")
    if (data.get("usage") or {}).get("truncated"):
        # A judge that cut the input to fit its window (Laya does) has not read it all: no verdict.
        raise TooBig("the judge cut the input to fit its window -- split the file; it is never truncated")
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
        raise JudgeError("no checkable claims -- pass --claim, --claims-file, or a --draft "
                       "with a sentence of four words or more")
    if len(claims) + len(DRAFT_QUESTIONS) > MAX_QUESTIONS:
        raise TooBig(f"{len(claims)} claims is too many for one call -- split the draft")
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
        conf = float(answer.get(CONFIDENCE_FIELD, 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return {"key": key, "subject": subject, "verdict": verdict, "confidence": conf,
            "flag": (verdict not in FAVORABLE) if side else (verdict != "SUPPORTED" or conf < LINE)}


def evidence_parts(evidence, room):
    """Group (path, text) evidence into states of at most `room` estimated tokens.
    A file too big for one state is cut at line breaks into labeled parts; no text
    is ever dropped."""
    pieces = []
    for p, t in evidence:
        lines, cur, cuts = t.strip().splitlines(keepends=True), "", []
        for line in lines:
            while estimate_tokens(line) > room:  # one enormous line: hard cut
                if cur:
                    cuts.append(cur)
                    cur = ""
                cuts.append(line[:room // 2])  # 4 UTF-8 bytes a character at most
                line = line[room // 2:]
            if cur and estimate_tokens(cur + line) > room:
                cuts.append(cur)
                cur = ""
            cur += line
        cuts.append(cur)
        n = len(cuts)
        pieces += [f"=== {p}{f' (part {i}/{n})' if n > 1 else ''} ===\n{c.strip()}"
                   for i, c in enumerate(cuts, 1)]
    groups, cur = [], []
    for piece in pieces:
        if cur and estimate_tokens("\n\n".join(cur + [piece])) > room:
            groups.append(cur)
            cur = []
        cur.append(piece)
    return groups + [cur] if cur else groups


def merge_rows(parts):
    """One row per question across evidence parts. A contradiction (or, for a draft-level
    question, any red label) in any part wins, since it must be read; else a claim any
    part supports is supported. The highest confidence of the winning label is kept."""
    out = []
    for rows in zip(*parts):
        if rows[0]["key"] in DRAFT_QUESTIONS:
            pick = [r for r in rows if r["verdict"] in RED]
        else:
            pick = ([r for r in rows if r["verdict"] == "CONTRADICTED"]
                    or [r for r in rows if r["verdict"] == "SUPPORTED"])
        out.append(max(pick or rows, key=lambda r: r["confidence"]))
    return out


def check(evidence, claims, draft=""):
    """evidence: [(path, text)]. Returns (rows, meta, exit_code). Evidence over Jev's
    input ceiling is split into parts, each checked in its own call, then merged."""
    questions = questions_for(claims)
    tail = f"\n\nDRAFT:\n{draft.strip()}"
    room = MAX_INPUT_TOKENS - max(estimate_tokens(q) for q in questions.values()) - estimate_tokens(tail) - 10
    if room < min(1000, MAX_INPUT_TOKENS // 4):   # 1000 for Jev; a small-window judge gets a proportional floor
        raise TooBig("the draft or a claim alone is too long for one call -- shorten it")
    parts, meta = [], {"model": None, "chunks": 0, "input_tokens": 0, "latency_ms": 0}
    for group in evidence_parts(evidence, room):
        # through the door, so its secret scan covers this path for every judge
        res = judges.ask("EVIDENCE:\n" + "\n\n".join(group) + tail, questions)
        meta.update(model=meta["model"] or res.get("model"), chunks=meta["chunks"] + 1,
                    input_tokens=meta["input_tokens"] + (res.get("input_tokens") or 0),
                    latency_ms=meta["latency_ms"] + (res.get("latency_ms") or 0))
        rows = [row(f"c{i}", res["answers"].get(f"c{i}"), c) for i, c in enumerate(claims, 1)]
        parts.append(rows + [row(k, res["answers"].get(k), side=True) for k in DRAFT_QUESTIONS])
    rows = merge_rows(parts)
    return rows, meta, (3 if any(r["flag"] for r in rows) else 0)


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
            raise JudgeError("needs at least one evidence file")
        # newline="": a lone CR stays inside its line (see prepare_bulk.mask_evidence)
        evidence = [(f, open(os.path.expanduser(f), encoding="utf-8", errors="replace", newline="").read())
                    for f in a.evidence]
        # Evidence goes to the Jev API, so it gets the same secret scan connect uses: each file
        # that scans as a secret anywhere in the evidence (a test fixture's fake card number too)
        # is withheld whole; an item left with nothing judgeable is dropped, none left is refused.
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from prepare_bulk import has_secret, mask_evidence
        masked, withheld = mask_evidence(evidence)
        kept = [(f, text) for f, text in masked if text is not None]
        if not kept:
            raise SecretBlocked(f"evidence file {masked[0][0]} contains a secret; not sent")
        if withheld:
            print(f"jev: withheld {len(withheld)} file(s) holding secret-shaped text, not sent: "
                  f"{', '.join(withheld)}; a claim about them cannot be checked", file=sys.stderr)
        evidence = kept
        claims = list(a.claim)
        if a.claims_file:
            claims += [l.strip() for l in open(os.path.expanduser(a.claims_file), encoding="utf-8") if l.strip()]
        draft = open(os.path.expanduser(a.draft), encoding="utf-8").read() if a.draft else ""
        if not claims:
            claims = split_claims(draft)
        # The draft and every claim go into the request too, so they get the same scan.
        if has_secret(draft):
            raise SecretBlocked("the draft contains a secret; not sent")
        if any(has_secret(c) for c in claims):
            raise SecretBlocked("a claim contains a secret; not sent")
        rows, meta, code = check(evidence, claims, draft)
        side_advisory = bool(a.claim) and not a.claims_file and not a.draft
        if side_advisory and code == 3:
            code = 3 if any(r["flag"] for r in rows if r["key"] not in CLAIM_ADVISORY) else 0
    except (JudgeError, OSError) as e:
        print(f"jev: {e}", file=sys.stderr)
        return 1
    print_table(rows, meta, len(claims), side_advisory)
    return code


if __name__ == "__main__":
    sys.exit(main())
