#!/usr/bin/env python3
"""Bulk preparation: inventory -> cheap writer drafts descriptions -> Jev checks them -> connect the approved set.

Usage:
  python3 prepare_bulk.py --root DIR [--root DIR2 ...] --pointer NAME --principal AGENT [--principal AGENT2 ...]
                          [--exclude SUBPATH ...] [--no-recurse] [--name GLOB ...] [--limit 50] [--max-files 250]
                          [--batch 10] [--line 0.80] [--writer-model haiku]
                          [--writer-command 'COMMAND [ARG ...]'] [--allow-held] [--approve-held PATH ...]
                          [--no-connect]
                          [--findability] [--refresh] [--no-shared] [--shareable]

  Prints a `writer: <command>` banner at the start of every run: the resolved --writer-command
  (or the SUPERJEV_WRITER_COMMAND env var, checked when --writer-command is omitted), or the
  default `claude -p --model <writer-model>`. When neither flag nor env var is set, a second
  line recommends a cheap writer model -- bulk labeling should never run on a premium model --
  and names the proven default (Claude Code CLI, Haiku).

  python3 prepare_bulk.py --list [--pointer NAME] [--principal AGENT] [--status active] [--kind dashboard]
                          [--within-days 30] [--subject CLOV]
    No-judge local list: with --pointer, reads the already-gated labels back out of prepare-cache/<pointer>.json
    (and any <pointer>-N.json part caches); with --principal, also scans that principal's manual ask.py records
    (state dir manual/*.md, labelled the same way ask.py --add writes them), shown with pointer name
    <principal>-manual-* in the path column. At least one of --pointer/--principal is required; both may be
    given together. Filters apply the same way to either source. Prints them merged, sorted by as_of desc.
    Never calls the writer, the gate, or memory.

Pipeline per run:
  1. Inventory *.md (plus explicit --ext text suffixes) under the union of one or more --root directories, in the order given (repeat --root for
     a whole agent brain spanning several folders). Skips .bak*, profile/, documents/, logins.md, *-secret.md,
     hidden directories, git worktree copies (any .claude/worktrees/ folder, or a checkout whose .git file points into
     another repo's .git/worktrees/ -- even when it is the --root itself) and test/scratch output (ops/sj*/ except ops/sj-manual/, *superjev-test*, *-hand-test-*); --exclude SUBPATH (repeatable) also skips any file whose path relative to its
     root starts with that subpath; --no-recurse limits each root to its direct children only; --name GLOB (repeatable, e.g. SKILL.md)
     keeps only files whose name matches, so a skills folder connects its entry files and not every reference doc. Files matching
     card/password-like patterns or over the size ceiling (250,000 bytes; files over one Jev call are gated in parts) are HELD and never sent to the writer; a
     per-file reason (and, for the secret-pattern case, the matching line's pattern type and line number with
     all digits masked) is written to prepare-cache/<pointer>-held.txt for human review without opening files.
     The card-number check ignores ISO dates and URLs first (a long numeric id in a URL, or a run of dates on
     one line, must not trigger it); the password/api-key keyword check is never affected. --allow-held admits
     a file the secret scan alone would hold -- it is still listed in the held file, noting the override -- but
     never lifts the size-ceiling hold, since an oversized file cannot be gated regardless. --approve-held PATH
     (repeatable) admits one size-held file after a person reviewed it and records its path and sha256 in the report;
     --refresh (and so auto-heal) replays it only while the file's bytes still match, and a changed file is held
     again naming the old approval. It lifts only a size hold, up to APPROVE_MAX_BYTES (1,000,000); a file held
     for secret-like text or name, or with a credential suffix, can never be approved. --allow-held is never
     replayed. A first connect
     (no cache yet) refuses above --max-files (default 250) total files, as a size guard. A --refresh of an
     already-cached pointer instead guards on files that actually need a writer call this run (unchanged
     cached files are free and reused); raise --max-files to opt into a larger writer cost.
  2. Cache (prepare-cache/<pointer>.json) keyed by path: unchanged sha256 with a passing verdict skips steps 3-4.
     --refresh additionally drops any cached path that no longer exists on disk from the cache and the connect
     set, noting it in the report. A pointer connected under several principals must repeat --principal for
     every one of them on --refresh (once each) -- naming only a subset narrows the pointer's registered
     scope and the harness refuses the reconnect with "scope-change".
  3. Writer (by default `claude -p --model <writer-model>`, or `--writer-command` for another adapter that
     reads the prompt on stdin and returns a JSON array on stdout) drafts, per batch, one factual description,
     one sample question a user would ask that this file answers, and four gated labels: kind (dashboard,
     playbook, ledger, record, index, pointer, research, note), status (active, closed, paper, done, unknown —
     using only what the file itself states; NOT FILED/pending/open counts as active, nothing stated is
     unknown), as_of (the date the file claims for that status, or unknown), and subject (1-4 words).
     Descriptions and labels are drafts, never trusted until gated.
  4. Jev gate (connect_checked.gate), two stages per file, never more than one label problem cost a file its
     place in the set:
       Stage 1 gates the description ALONE against the file, exactly the pre-labels claim. Fail -> one
       rewrite retry, verdict fed back; still failing -> EXCEPTION list, file goes no further.
       A --writer builtin description is only the file's own words quoted, so stage 1 checks it
       locally (rebuilt from the file, must match exactly: verdict QUOTED) with no judge call.
       Stage 2 (only for a file that passed stage 1) gates the label sentence ALONE ("This file is a <kind>
       about <subject>. Its status is <status>[ as of <as_of>].") against the file. SUPPORTED at or above
       --line keeps the drafted labels; anything else (under the line, NOT_SUPPORTED, CONTRADICTED, ERROR)
       resets kind/status/as_of/subject to "unknown" and records labels_verdict/labels_confidence in the
       cache entry -- the file still connects on its description alone. A label outside its enum is coerced
       to "unknown" locally, before the stage-2 claim is built, so a bad label from the writer never crashes
       the run. Cost: two judge calls per file when stage 1 passes, one when it doesn't.
  5. Connect the passing set through the normal preview -> confirm path (replace:true, with a one-line warning,
     if the pointer already exists). The description sent to the harness carries the labels in brackets
     (`<description> [kind: ...; status: ...; as_of: ...; subject: ...]`) only for a file whose stage-2 label
     gate passed; every other connected file carries its plain description. The harness accepts at most 50
     files per connect request, so a set over --limit (default 50, hard max 50) is split into parts named
     <pointer>, <pointer>-2, <pointer>-3, ... in stable sorted-path order, each connected separately; the
     cache and report stay keyed by the base pointer.
  6. Findability (only with --findability; it costs a search per file): each connected file's own sample question is navigated; the file must rank first or it is
     listed as a findability miss. Report only; no automatic loop beyond the one rewrite.
  7. Shared sets (onboarding default, skip with --no-shared): once every part connected, each --principal is added to
     the fleet's shared pointers listed in shared-pointers.json (share_pointers.py): register only, no writer or Jev call.
  Every connection is private (share_pointers.py refuses it) unless --shareable marks it for fleet-wide sharing;
  a refresh without the flag keeps the mark it had.
Nothing here edits original files. Cache and report land under prepare-cache/ next to this script.

A label is only as true as the file it was drafted and gated from; as_of shows staleness, not currency.
Live truth for anything time-sensitive still needs a gated roll-up read fresh, not a cached label.
"""
import argparse, fnmatch, functools, hashlib, json, math, os, re, shlex, shutil, subprocess, sys, time, unicodedata
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Default connect scope, also imported by coverage/preflight callers. Extra text
# suffixes require --ext and are persisted in that pointer's recipe.
CONNECTABLE_EXTENSIONS = ('.md',)
MAX_FILES = 250  # --max-files default
UNCONNECTED_TRIES = 3  # refreshes that retry a new file whose connect failed
CODE_EXTENSIONS = ('.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.sh', '.bash')
# Credential containers are not ordinary text inputs; --allow-held cannot opt
# them in. Check compound suffixes and symlink targets as well.
CREDENTIAL_SUFFIXES = frozenset({'env', 'pem', 'key', 'p12', 'pfx', 'jks', 'kdbx',
                                 'kdb', 'keystore', 'pkcs12', 'ppk', 'p8'})


def credential_suffix(name: str) -> bool:
    return bool(CREDENTIAL_SUFFIXES.intersection(name.lower().split('.')[1:]))


def extension_list(value: str) -> tuple:
    """Explicit literal suffixes only; no globs, paths or empty entries."""
    result = []
    for item in value.split(','):
        suffix = '.' + item.strip().lower().lstrip('.')
        if not re.fullmatch(r'\.[a-z0-9]+(?:[.-][a-z0-9]+)*', suffix):
            raise argparse.ArgumentTypeError('extensions must be comma-separated literal suffixes, e.g. py,ts,js,sh,json-schema')
        if credential_suffix(suffix):
            raise argparse.ArgumentTypeError('credential/key suffixes cannot be connected, including with --allow-held')
        if suffix not in result:
            result.append(suffix)
    return tuple(result)


def given_path(value) -> Path:
    """Absolute spelling without dereferencing a release-switching symlink."""
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def code_heading(line: str, suffix: str) -> bool:
    """Best-effort declaration boundaries, not a parser or code execution."""
    if suffix == '.py':
        return bool(re.match(r'^\s*(?:async\s+def|def|class)\s+\w+', line))
    if suffix in {'.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'}:
        return bool(re.match(r'^\s*(?:export\s+(?:default\s+)?)?(?:(?:async|abstract)\s+)?(?:function\s*\*?\s+|class\s+|interface\s+|type\s+)\w+', line))
    if suffix in {'.sh', '.bash'}:
        return bool(re.match(r'^\s*(?:function\s+\w+|[a-zA-Z_]\w*\s*\(\s*\))', line))
    return False
sys.path.insert(0, str(HERE))
from connect_checked import gate, gate_many, memory  # noqa: E402

# Gate packing: TypeSafe's docs batch independent questions into one call (parallel
# questions, speculative fan-out). Small files are gated several to a call: each file's
# description claim and its label claim ride together, under a FILE header, so one judge
# call replaces up to 2 per file. A pack whose call fails or loses a claim row falls back
# to the one-file gate(). Off with SUPERJEV_BATCH_JEV=0, the same switch as ask's batching.
PACK_CHARS = 24000
PACK_FILES = 6


def pack_groups(paths: list, sizes: dict) -> list:
    """Consecutive groups of at most PACK_FILES files and PACK_CHARS characters. A file too
    big for a pack on its own is left out (it is gated alone)."""
    groups, cur, used = [], [], 0
    for p in paths:
        n = sizes.get(str(p), PACK_CHARS + 1)
        if n > PACK_CHARS:
            continue
        if cur and (len(cur) >= PACK_FILES or used + n > PACK_CHARS):
            groups.append(cur); cur, used = [], 0
        cur.append(p); used += n
    if cur:
        groups.append(cur)
    return [g for g in groups if len(g) > 1]


def gate_pack(items: list) -> dict:
    """items: [(path, description, label_claim or None)]. One judge call for the whole pack;
    returns {path: (description verdict, label verdict or None)}, or {} when the call failed."""
    import tempfile
    parts, claims, slots = [], [], []
    for i, (p, desc, label_claim) in enumerate(items, 1):
        tag = f"F{i}"
        parts.append(f"===== FILE {tag} =====\n" + Path(p).read_text(errors="replace"))
        claims.append(f"File {tag}: {desc}"); slots.append((str(p), 0))
        if label_claim:
            claims.append(f"File {tag}: {label_claim}"); slots.append((str(p), 1))
    fd, tmp = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write("\n\n".join(parts))
        got = gate_many(claims, tmp)
    finally:
        os.unlink(tmp)
    if not got:
        return {}
    out = {str(p): [None, None] for p, _d, _l in items}
    for (p, k), v in zip(slots, got):
        out[p][k] = v
    return {p: tuple(v) for p, v in out.items()}

CACHE_DIR = HERE / "prepare-cache"
# Names of the files written into CACHE_DIR, one per line; uninstall deletes only these.
WRITTEN_MANIFEST = ".superjev-written"


def _record_written(path: Path) -> None:
    m = CACHE_DIR / WRITTEN_MANIFEST
    names = set(m.read_text().split("\n")) if m.is_file() else set()
    if path.name not in names:
        with m.open("a") as fh:
            fh.write(path.name + "\n")


# One pattern source shared with Node (src/secret-scan.ts): secret_patterns.json.
# Token shapes are adapted from gitleaks' default rules; "1Password" (the app) is not
# a password (digit lookbehind). GENERIC keywords start a word and their tail is capped.
_PAT = json.loads((Path(__file__).resolve().parent / "secret_patterns.json").read_text())
CARD_RE = re.compile(_PAT["card"], re.A)
WORD_RE = re.compile(_PAT["word"], re.I | re.A)
TOKEN_RE = re.compile(_PAT["token"], re.I | re.A)
GENERIC_RE = re.compile(_PAT["generic"], re.I | re.A)
SECRET_RE = re.compile(f"{CARD_RE.pattern}|{WORD_RE.pattern}|{TOKEN_RE.pattern}", re.I | re.A)
# An ISO date or a URL can contain a run of digits that coincidentally matches the
# card-number pattern (a long numeric id in a query string, a table of dates on one
# line). Both are scrubbed out before the card check only; the keyword rule below
# always runs against the original, unscrubbed text.
ISO_DATE_RE = re.compile(_PAT["iso_date"], re.A)
URL_RE = re.compile(_PAT["url"], re.A)
# A spaced USPS tracking number (22 or 26 digits in groups of 4) starts with a card-shaped
# 16-digit run, and about one in ten such numbers passes Luhn there by chance.
# A whole run in USPS layout (TRACKING_RE: starts 91-95, groups of 4 then a final 2, one separator)
# with a valid check digit is removed before the card check only. The exact layout matters: a loose
# run would let a short 91-95 number in front of a card turn "number + card" into a tracking number.
# A run with a Luhn-valid window after its first group is not removed; about 1 in 100 real 22-digit
# numbers (1 in 50 for 26 digits) are then still held by the card rule, down from 1 in 10.
TRACKING_RE = re.compile(_PAT["tracking"], re.A)
_CTRL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_NON_ASCII_RE = re.compile(r"[^\x00-\x7f]+")
_FOLD = {}


def _fold_char(c: str) -> str:
    if c not in _FOLD:
        cat = unicodedata.category(c)
        _FOLD[c] = "x" if cat[0] in "LM" or cat == "Cn" else "0" if cat == "Nd" else " "
    return _FOLD[c]


def _fold_run(m) -> str:
    return "".join(map(_fold_char, m.group()))


def normalize_for_scan(text: str) -> str:
    """The one normalization both scanners apply (twin: normalizeForScan in src/secret-scan.ts):
    NFKC, casefold, every control or whitespace char but newline to a space, any remaining
    non-ASCII letter/mark/unassigned to "x" and digit to "0", anything else non-ASCII to a space."""
    s = text.casefold() if text.isascii() else unicodedata.normalize("NFKC", text).casefold()
    return _CTRL_RE.sub(" ", _NON_ASCII_RE.sub(_fold_run, s))
SKIP_PARTS = {"profile", "documents", "__pycache__", "node_modules", ".git"}
# One file's size limit. The label gate splits evidence over Jev's input ceiling into parts and
# merges the verdicts (lib/jev_client.check; the fleet door does the same), and the writer reads an
# excerpt, so a big file needs no hand split. The old 90,000-byte hold left every notes log, dead-ends
# list and knowledge file over it unsearchable. 250,000 bytes keeps one file's gate to about 5 Jev
# calls; a bigger file is still held with a split hint.
CEILING_BYTES = 250_000
# A reviewed --approve-held file may go over CEILING_BYTES up to this hard cap (about 20 gate calls).
# Only a size hold can be approved: secret-like text is never sent, so approving it could not connect it.
APPROVE_MAX_BYTES = 1_000_000
SECRET_NOT_APPROVABLE = ("held for secret-like text; Super Jev never sends that text. "
                         "Remove or move the value, then reconnect.")
# One connect (a part pointer) may hold at most 5 MiB (path_connect.MAX_BYTES); parts close early
# before that. Under the old 90,000-byte file limit 50 files never reached it, so parts are unchanged.
PART_BYTES = 4_500_000

# Four gated labels drafted by the writer, validated locally, then folded into the one
# claim gated per file. A value outside its own enum is coerced to "unknown" rather than
# ever crashing the run on a bad writer response.
KIND_VALUES = {"dashboard", "playbook", "ledger", "record", "index", "pointer", "research", "note"}
STATUS_VALUES = {"active", "closed", "paper", "done", "unknown"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_labels(d: dict) -> dict:
    """Coerce a writer's draft labels to their enums. Never raises."""
    kind = d.get("kind")
    kind = kind if kind in KIND_VALUES else "unknown"
    status = d.get("status")
    status = status if status in STATUS_VALUES else "unknown"
    as_of = d.get("as_of")
    as_of = as_of if isinstance(as_of, str) and DATE_RE.match(as_of) else "unknown"
    subject = d.get("subject")
    subject = subject.strip() if isinstance(subject, str) and subject.strip() else "unknown"
    return {"kind": kind, "status": status, "as_of": as_of, "subject": subject}


def claim_sentence(labels: dict) -> str:
    s = f"This file is a {labels['kind']} about {labels['subject']}. Its status is {labels['status']}"
    if labels["as_of"] != "unknown":
        s += f" as of {labels['as_of']}"
    return s + "."


def label_bracket(labels: dict) -> str:
    return (f"[kind: {labels['kind']}; status: {labels['status']}; "
            f"as_of: {labels['as_of']}; subject: {labels['subject']}]")


def labeled_description(description: str, labels: dict) -> str:
    """The connect description sent to the harness: the description plus labels in
    brackets, so ranking sees them."""
    return f"{description} {label_bracket(labels)}"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def relstr(p, roots) -> str:
    """Path relative to whichever --root contains it; falls back to the raw path."""
    pr = Path(p).resolve()
    for r in roots:
        try:
            return str(pr.relative_to(r))
        except ValueError:
            continue
    return str(p)


# Test/scratch output (e.g. a Super Jev test report that repeats the test questions) outranks the real
# answer in word search, so it is never connected or searched. Kept narrow: real ops notes stay, and so
# does a run folder's own STATUS.md (the run's outcome record) unless the folder is a bench/eval/test one.
TEST_MATERIAL_RE = re.compile(r"(^|/)ops/sj(?!-manual/)(?![^/]*/STATUS\.md$)[^/]*/"  # ops/sj-manual/ holds real facts
                              r"|(^|/)ops/sj[^/]*(bench|eval|test)[^/]*/|superjev-test|-hand-test-", re.I)


# Fixture/sample folders (a test fixture, sample data, or an evidence run's copied
# sources) hold made-up text about other companies, never the user's own facts; a
# fixture releases.md once answered "what was decided about releasing v1.0.22".
# Names only a test tool uses count on their own; a plain fixtures/ or
# evidence/*/sources/ folder counts only under a test or docs folder, so a
# lighting business's fixtures/ or a legal case's evidence/ stays visible.
TEST_CONTEXT = r"(^|/)(tests?|__tests__|specs?|e2e|docs|examples|bench(marks?)?)/(.+/)?"
FIXTURE_RE = re.compile(r"(^|/)(__fixtures__|__mocks__|test[-_]?data|sample[-_]data)/"
                        rf"|{TEST_CONTEXT}(fixtures?/|evidence/(.+/)?sources/)", re.I)


def is_test_material(path: str, named: bool = False) -> bool:
    """A file its connector named exactly (named=True) is judged by its folder only: naming a
    run log such as superjev-test-timeline.md connects it on purpose, while a named file inside
    a test/scratch or fixture folder stays out."""
    p = Path(path).as_posix()
    if named:
        p = p.rpartition("/")[0] + "/"
    return bool(TEST_MATERIAL_RE.search(p) or FIXTURE_RE.search(p))


def named_exactly(filename: str, names) -> bool:
    """True when a --name with no wildcard is this file's own name (case-insensitive)."""
    return any(isinstance(n, str) and not any(c in n for c in "*?[") and n.casefold() == filename.casefold()
               for n in names or [])


# Super Jev's own bench/eval datasets (e.g. health-selected-passages): copies of passages,
# never a real note, so they must not be connected or show up in real ask results.
BENCH_DATASET_RE = re.compile(r"/\.local/retrieval-datasets/")


def is_bench_dataset(path: str) -> bool:
    return bool(BENCH_DATASET_RE.search(Path(path).as_posix()))


def _excluded(rel_posix: str, excludes: list) -> bool:
    return any(rel_posix == ex or rel_posix.startswith(ex + "/") for ex in excludes)


def _scrub_dates_and_urls(text: str) -> str:
    """Remove ISO-date and URL substrings before testing the card-number pattern, so a
    long numeric id in a URL or a run of dates on one line cannot trigger a false hold."""
    return ISO_DATE_RE.sub(" ", URL_RE.sub(" ", text))


def _entropy(s: str) -> float:
    return -sum(s.count(c) / len(s) * math.log2(s.count(c) / len(s)) for c in set(s))


def _token_hit(text: str) -> bool:
    """A known token shape, or a key/token/secret assignment whose value looks random
    (entropy >= 3.5 bits/char, the gitleaks generic-api-key threshold, and mixes letters with digits)."""
    if TOKEN_RE.search(text):
        return True
    return any(_entropy(v) >= 3.5 and re.search(r"\d", v) and re.search(r"[A-Za-z]", v)
               for v in (m.group(4) for m in GENERIC_RE.finditer(text)))


NON_ASCII_DIGIT_RE = re.compile(r"(?![0-9])\d")


def _luhn(digits: str) -> bool:
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def _usps_tracking(run: str) -> bool:
    """A TRACKING_RE run (prefix and layout already checked) of 22 or 26 digits with a valid GS1 mod-10 check
    digit, and no Luhn-valid 16-digit window starting at its 2nd or 3rd group, where a card could sit behind a
    short 91-95 number ("9100 4111 1111 1111 1111 01"). Such a run is not exempt and the card rule decides."""
    d = re.sub(r"\D", "", run)
    if len(d) not in (22, 26):
        return False
    total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(d[:-1])))
    return (10 - total % 10) % 10 == int(d[-1]) and not any(_luhn(d[i:i + 16]) for i in range(4, len(d) - 15, 4))


def card_hit(text: str, luhn: bool = True) -> bool:
    """A standalone 16-digit run (dates/URLs and whole USPS tracking numbers scrubbed) that passes the Luhn
    check. luhn=False when the raw text had non-ASCII digits: normalizing folds them to 0, so their true value
    is lost and no run is exempted as a tracking number either."""
    text = _scrub_dates_and_urls(text)
    if luhn:
        text = TRACKING_RE.sub(lambda m: " " if _usps_tracking(m.group()) else m.group(), text)
    return any(not luhn or _luhn(re.sub(r"\D", "", m.group())) for m in CARD_RE.finditer(text))


def has_secret(text: str) -> bool:
    """Scans normalize_for_scan(text). Card-number check runs on the scrubbed text
    (dates/URLs removed); the keyword and token checks run on the unscrubbed text."""
    luhn = not NON_ASCII_DIGIT_RE.search(text)
    text = normalize_for_scan(text)
    return (card_hit(text, luhn) or bool(WORD_RE.search(text))
            or _token_hit(text))


# The WORD_RE/GENERIC_RE keyword checks above only fire in a key=value, key:value,
# or "key is value" shape -- prose or a plain word is not a secret. A file NAME or
# PATH is different: nobody writes "password: hunter2xyz" as a filename, they write
# password-hunter2xyz-notes.md, so the same keywords glued to other characters with
# a "-"/"_" joiner are treated as secret-shaped there. Requires a non-alnum (or
# start-of-segment) boundary before the keyword (so "tokenizer.py" does not
# false-positive) AND the token immediately glued on after the joiner to carry a
# digit, the way an actual secret value does ("hunter2xyz", "prod789") -- a plain
# word after the joiner ("secret_held_does_not_save", a test's own tmp dir name,
# "token_metrics") is prose, not a leaked value, and must not be flagged.
PATH_SECRET_KEYWORDS = r"(?:password|passwd|secret|token|api[_-]?key|private[_-]?key)"
PATH_SECRET_RE = re.compile(
    rf"(?<![0-9A-Za-z]){PATH_SECRET_KEYWORDS}[-_\u2010-\u2015](?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*",
    re.I)


def path_has_secret(text: str) -> bool:
    """True when any segment of a path/filename string looks like it carries a
    secret keyword glued to a value, e.g. 'password-hunter2xyz-notes.md'."""
    return bool(PATH_SECRET_RE.search(text or ""))


def redact_path_secrets(text: str) -> str:
    """Replace every secret-keyword-shaped run in a path/filename string with
    '<keyword>-[REDACTED]', leaving the rest of the path (directories, extension)
    intact. No-op when nothing matches."""
    def _sub(m):
        kw = re.match(PATH_SECRET_KEYWORDS, m.group(0), re.I).group(0).lower()
        return f"{kw}-[REDACTED]"
    return PATH_SECRET_RE.sub(_sub, text or "")


# Fields the tool builds itself (hashes, ids, pointer names); never user text, so never scanned.
MACHINE_KEYS = frozenset({"sha256", "id", "sourceId", "rootId", "children", "pointer", "principals"})


def payload_has_secret(obj) -> bool:
    """has_secret over every user-text string inside a request payload (dicts, lists, tuples);
    values under MACHINE_KEYS are skipped."""
    if isinstance(obj, str):
        return has_secret(obj)
    if isinstance(obj, dict):
        return any(payload_has_secret(k) or (k not in MACHINE_KEYS and payload_has_secret(v))
                   for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return any(payload_has_secret(v) for v in obj)
    return False


@functools.lru_cache(maxsize=None)
def _worktree_dir(d: str) -> bool:
    """True when folder `d` (or a parent) is a git worktree checkout: its `.git` is a file whose
    gitdir points into another repo's .git/worktrees/. A submodule's .git file (.git/modules/)
    and a real repo root (.git folder) end the climb."""
    g = Path(d) / ".git"
    if g.is_file():
        try:
            return "/worktrees/" in g.read_text(errors="replace")
        except OSError:
            return False
    if g.is_dir() or Path(d).parent == Path(d):
        return False
    return _worktree_dir(str(Path(d).parent))


def is_worktree_copy(path: Path) -> bool:
    """A file inside a git worktree copy (a `.claude/worktrees/` folder, or any worktree checkout)
    is a stale snapshot of a brain or repo, never its live notes: one agent once carried 23
    pointers of a Claude worktree's copy of its own brain. Checked on the absolute path, so a
    --root that is itself a worktree is refused too."""
    parts = path.parts
    if any(a == ".claude" and b == "worktrees" for a, b in zip(parts, parts[1:])):
        return True
    return _worktree_dir(str(path.parent))


def walk_md(root: Path, no_recurse: bool = False, extensions=CONNECTABLE_EXTENSIONS):
    """Selected text suffixes under `root`, sorted, plus resolved folder symlink targets.
    Path.rglob does not descend into a symlinked folder (Python 3.12), which silently dropped every
    symlinked skill folder from a skills root; os.walk(followlinks=True) does, with a guard so a link
    loop is walked once."""
    if no_recurse:
        return sorted(p for p in root.iterdir() if p.is_file() and p.name.lower().endswith(tuple(extensions))), []
    out, linked, walked = [], [], set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        real = os.path.realpath(dirpath)
        if real in walked:
            dirnames[:] = []
            continue
        walked.add(real)
        linked += [Path(os.path.realpath(os.path.join(dirpath, d))) for d in dirnames
                   if os.path.islink(os.path.join(dirpath, d))]
        out += [Path(dirpath) / n for n in filenames if n.lower().endswith(tuple(extensions))]
    return sorted(out), linked


def inventory(roots: list, excludes: list = None, no_recurse: bool = False, allow_held: bool = False,
              names: list = None, allow_targets: list = None, extensions=CONNECTABLE_EXTENSIONS,
              approvals: dict = None, approved_out: dict = None):
    """Union of selected text suffixes under `roots`, in root order then sorted-per-root order. Each file is counted once
    even if reachable through more than one root. --allow-held admits a file the secret scan would otherwise
    hold (still listed in `held`, with its reason noting the override); the size-ceiling hold is unaffected,
    since an oversized file cannot be gated regardless. `approvals` ({path: sha256}, from --approve-held)
    lifts a size hold (up to APPROVE_MAX_BYTES) only while the file's bytes hash to the reviewed sha256;
    a secret-like file stays held with SECRET_NOT_APPROVABLE; each approval key used maps to the admitted path in `approved_out`.
    A symlinked file is judged on its target too: the target must sit under a root or an `allow_targets`
    folder (--allow-target) and pass the same name/folder/secret-name checks, so a link cannot reach profile/,
    logins.md or any other file the roots would never have admitted."""
    bases = [Path(r).resolve() for r in list(roots) + list(allow_targets or [])]
    excludes = [e.strip("/") for e in (excludes or []) if e.strip("/")]
    files, held, seen, worktree_skips, test_skips = [], [], set(), 0, 0
    for root in roots:
        glob_iter, linked = walk_md(root, no_recurse, extensions)
        # A folder symlinked inside a root was placed there on purpose (install.sh links the Super Jev
        # skills into ~/.claude/skills), so its target is admitted like a root, unless it is a vault folder.
        bases += [t for t in linked if not SKIP_PARTS.intersection(x.casefold() for x in t.parts)]
        for p in glob_iter:
            rp = p.resolve()
            if rp in seen:
                continue
            if credential_suffix(p.name) or credential_suffix(rp.name):
                held.append((str(p), 'credential/key file suffix; cannot be overridden'))
                continue
            # Case-insensitive: 225 of 315 fleet skills name the entry file skill.md, not SKILL.md.
            if names and not any(fnmatch.fnmatch(p.name.lower(), n.lower()) for n in names):
                continue
            base = next((b for b in bases if rp.is_relative_to(b)), None)
            if base is None:
                print(f"  SKIP  {p}  (links to {rp}, outside every --root/--allow-target)")
                continue
            if any(".bak" in n or Path(n).stem == "logins" or Path(n).stem.endswith("-secret") for n in (p.name.casefold(), rp.name.casefold())):
                continue
            if any(part.casefold() in SKIP_PARTS or part.startswith(".")
                   for part in p.relative_to(root).parts + rp.relative_to(base).parts):
                continue
            if _excluded(p.relative_to(root).as_posix(), excludes) or is_bench_dataset(str(rp)):
                continue
            if is_test_material(p.relative_to(root).as_posix(), named_exactly(p.name, names)):
                test_skips += 1
                continue
            if is_worktree_copy(p.absolute()) or is_worktree_copy(rp):
                worktree_skips += 1
                continue
            b = p.read_bytes()
            if not b.strip():
                continue
            seen.add(rp)
            key = next((k for k in (str(p), str(rp)) if k in (approvals or {})), None)
            approved, note, admitted = None, "", []
            if key:
                digest = hashlib.sha256(b).hexdigest()
                if approvals[key] == digest:
                    approved = digest
                else:
                    note = (f"; changed since its --approve-held review (approved sha256 {approvals[key][:12]}), "
                            "review it again")
            if len(b) > CEILING_BYTES:
                split_hint = ("split it into smaller .md files, e.g. one per ## section"
                              if p.suffix.lower() == '.md' else "split it into smaller text files")
                why = f"over size ceiling ({len(b):,} bytes, max {CEILING_BYTES:,})"
                if not (approved and len(b) <= APPROVE_MAX_BYTES):
                    too_big = f"; too big to approve (max {APPROVE_MAX_BYTES:,})" if approved else ""
                    held.append((str(p), f"{why}; {split_hint}{too_big}{note}")); continue
                admitted.append(why)
            if b"\x00" in b:
                held.append((str(p), "binary file (contains null bytes), not text; skipped")); continue
            try:
                text = b.decode("utf-8")
            except UnicodeDecodeError:
                held.append((str(p), "not UTF-8 text; re-save it as UTF-8 to connect it")); continue
            if any(ord(c) < 32 and c not in '\n\r\t' for c in text):
                held.append((str(p), "binary/control-character content, not text; skipped")); continue
            if has_secret(b.decode("utf-8", "replace")):
                if allow_held:
                    held.append((str(p), "card/password-like text; admitted by --allow-held"))
                elif approved:
                    held.append((str(p), f"card/password-like text; {SECRET_NOT_APPROVABLE}")); continue
                else:
                    held.append((str(p), f"card/password-like text; review before onboarding{note}")); continue
            if path_has_secret(p.name) or path_has_secret(rp.name):
                if allow_held:
                    held.append((str(p), "secret-keyword-like file name; admitted by --allow-held"))
                elif approved:
                    held.append((str(p), f"secret-keyword-like file name; {SECRET_NOT_APPROVABLE}")); continue
                else:
                    held.append((str(p), f"secret-keyword-like file name; review before onboarding{note}")); continue
            if admitted:
                held.append((str(p), f"{'; '.join(admitted)}; admitted by --approve-held (sha256 {approved[:12]})"))
                if approved_out is not None:
                    approved_out[key] = str(p)
            files.append(p)
    if test_skips:
        print(f"  SKIP  {test_skips} test/scratch output file(s) (e.g. *superjev-test*, ops/sj*/); name one exactly with --name to connect it")
    if worktree_skips:
        print(f"  SKIP  {worktree_skips} file(s) inside git worktree copies (.claude/worktrees/ or a worktree checkout)")
    return files, held


def secret_detail(p: Path):
    """First matching line for a secret-pattern hold, with digits masked. Returns the pattern type and line
    number for review; never returns the raw matched text."""
    try:
        text = p.read_text(errors="replace")
    except Exception:
        return None
    for i, line in enumerate(text.splitlines(), start=1):
        if card_hit(line):
            return {"type": "card-number-like digits", "line": i, "masked": re.sub(r"\d", "#", line)}
        if WORD_RE.search(line):
            return {"type": "password/api-key keyword", "line": i, "masked": re.sub(r"\d", "#", line)}
        if _token_hit(line):
            return {"type": "token/key-like text", "line": i, "masked": re.sub(r"[A-Za-z0-9]", "#", line)}
    return None


def split_parts(ordered: list, limit: int, part_bytes: int = None) -> list:
    """Files in order, cut into parts of at most `limit` files and `part_bytes` bytes (PART_BYTES)."""
    part_bytes = PART_BYTES if part_bytes is None else part_bytes
    parts, cur, size = [], [], 0
    for p in ordered:
        n = Path(p).stat().st_size if Path(p).exists() else 0
        if cur and (len(cur) >= limit or size + n > part_bytes):
            parts.append(cur); cur, size = [], 0
        cur.append(p); size += n
    if cur:
        parts.append(cur)
    return parts


def write_held_txt(pointer: str, held: list) -> None:
    if not held:
        # A run that holds nothing clears the last run's list, or a fixed file reads as still held.
        (CACHE_DIR / f"{pointer}-held.txt").unlink(missing_ok=True)
        return
    lines = []
    for p, why in held:
        lines.append(f"{p}\t{why}")
        if "card/password" in why:
            d = secret_detail(Path(p))
            if d:
                lines.append(f"    pattern={d['type']}  line={d['line']}  masked={d['masked']}")
    (CACHE_DIR / f"{pointer}-held.txt").write_text("\n".join(lines) + "\n")
    _record_written(CACHE_DIR / f"{pointer}-held.txt")


EXCERPT_HEADS = 15
EXCERPT_MORE_HEADS = 10
SAMPLE_OVER = 12_000
SAMPLES = 8
SAMPLE_CHARS = 350


def excerpt(p: Path, text: str = None) -> dict:
    """What the writer reads of one file: its headings and first 1,200 characters, plus, for a big
    file, headings and short passages taken evenly across the rest. The gate judges the whole file,
    so a writer that saw only the top described only the top (a 37 KB changelog drafted as its
    oldest versions) and was refused. The first 15 headings and the start stay exactly as before,
    so builtin_writer's quote rebuilds identically; the added part is bounded (at most 10 headings,
    8 x 350 characters) whatever the file's size. `text` is a snapshot already read and verified
    (an --approve-held file's reviewed bytes), used instead of reading the path again."""
    text = p.read_text(errors="replace") if text is None else text
    all_heads = [l.strip() for l in text.splitlines()
                 if l.startswith("#") or code_heading(l, p.suffix.lower())]
    heads = all_heads[:EXCERPT_HEADS]
    rest = all_heads[EXCERPT_HEADS:]
    if len(rest) > EXCERPT_MORE_HEADS:
        rest = [rest[i * len(rest) // EXCERPT_MORE_HEADS] for i in range(EXCERPT_MORE_HEADS - 1)] + [rest[-1]]
    heads += [h[:200] for h in rest]
    out = {"path": str(p), "headings": heads, "start": text[:1200]}
    if len(text) > SAMPLE_OVER:
        samples, span = [], len(text) - 1200
        for i in range(SAMPLES):
            # Start each passage at a line start, so it opens on a heading or bullet, not mid-word.
            pos = text.find("\n", 1200 + i * span // SAMPLES) + 1
            if pos <= 0 or (samples and pos < samples[-1][0] + SAMPLE_CHARS):
                continue
            chunk = text[pos:pos + SAMPLE_CHARS]
            if pos + SAMPLE_CHARS < len(text):
                cut = max(chunk.rfind("\n"), chunk.rfind(" "))
                chunk = chunk[:cut] if cut > 0 else chunk
            if chunk.strip():
                samples.append((pos, chunk.strip()))
        out["samples"] = [c for _pos, c in samples]
    return out


class WriterError(RuntimeError):
    """The external description writer did not produce a usable response."""


def writer(items: list, model: str, feedback: dict | None = None, command: list[str] | None = None) -> dict:
    """Run a writer that reads the prompt from stdin and returns a JSON array on stdout."""
    fb = ""
    if feedback:
        fb = ("\nPrevious drafts were REJECTED by a fact checker for these paths; write more literally from the file "
              "and do not claim anything not in it:\n" + json.dumps(feedback, indent=1))
    prompt = (
        "You write catalog descriptions for files. For EACH file below, return one JSON object with keys "
        "path, description, question, kind, status, as_of, subject.\n"
        "description: one factual sentence, at most 40 words, naming the main topics and purpose exactly as the file "
        "shows them; no praise, no guessing beyond the excerpt and headings; if the file is a stub or pointer, say so. "
        "A long file also has samples, short passages taken evenly across it: describe the whole file, not just "
        "its start.\n"
        "question: one natural question a user would ask that THIS file answers better than any sibling file; mention a "
        "specific detail from it.\n"
        "kind: one of dashboard (a status/tracker page for one campaign or case), playbook, ledger (dated rows), "
        "record, index, pointer, research, note.\n"
        "status: one of active, closed, paper, done, unknown. Use ONLY what the file itself states; 'NOT FILED', "
        "'pending', or 'open' means active; if the file states nothing about status, use unknown.\n"
        "as_of: the date the file itself claims for that status, as YYYY-MM-DD, or unknown if it states none.\n"
        "subject: the ticker, person, case, or topic the file is about, 1 to 4 words.\n"
        "Return ONLY a JSON array, no prose." + fb + "\n\nFILES:\n" + json.dumps(items, indent=1)
    )
    # The one place the writer prompt leaves this machine: scan it here, whoever called. Scan the
    # file text itself, not its JSON form: escaping (\" and \n) turned a safe shell line such as
    # KEY="$(cat file)", which the inventory scan admits, into a key=value hit, and that one file
    # failed every refresh of its pointer.
    if payload_has_secret(items) or payload_has_secret(feedback or {}):
        raise WriterError("the writer prompt contains a secret; not sent")
    argv = command or ["claude", "-p", "--model", model]
    for attempt in range(2):
        try:
            r = subprocess.run(argv, input=prompt, capture_output=True, text=True)
        except OSError as e:
            raise WriterError(f"could not start writer: {e.strerror or e.__class__.__name__}") from e
        if r.returncode:
            raise WriterError(f"writer exited with status {r.returncode}")
        m = re.search(r"\[.*\]", r.stdout, re.S)
        if m:
            try:
                arr = json.loads(m.group(0))
                return {x["path"]: x for x in arr if isinstance(x, dict) and x.get("path")}
            except Exception:
                pass
    raise WriterError("writer returned invalid JSON after 2 attempts")


BUILTIN_QUOTE_WORDS = 60


def builtin_writer(items: list) -> dict:
    """No-model writer: a description quoted from the file's own headings and first words.

    Used when no claude CLI is installed or with --writer builtin, so the TypeSafe key alone is
    enough to connect. Labels are left unknown; the gate still checks every description."""
    out = {}
    for it in items:
        heads = [h.lstrip("#").strip() for h in it["headings"] if h.lstrip("#").strip()]
        body = [l.strip() for l in it["start"].splitlines() if l.strip() and not l.startswith("#")]
        words = " ".join(body).split()[:BUILTIN_QUOTE_WORDS]
        if heads:
            desc = f'This file is titled "{heads[0]}"'
            if len(heads) > 1:
                desc += " with sections " + ", ".join(f'"{h}"' for h in heads[1:5])
            desc += "."
            # Navigation ranks files on their description alone; headings only let it guess
            # (hits flipped to no-candidates, absent facts matched on topic). Quote the body too.
            if words:
                desc += f' It begins: "{" ".join(words)}"'
            question = f"What does {heads[0]} say?"
        else:
            desc = f'This file begins: "{" ".join(words)}"'
            question = f"Which file says {' '.join(words[:8])}?"
        subject = " ".join((heads[0] if heads else Path(it["path"]).stem).split()[:4])
        out[it["path"]] = {"path": it["path"], "description": desc, "question": question,
                           "kind": "unknown", "status": "unknown", "as_of": "unknown",
                           "subject": subject}
    return out


def navigate(pointer: str, principal: str, question: str) -> list:
    out = memory({"action": "navigate", "pointer": pointer, "principal": principal, "question": question})
    return [c.get("originalPath") for c in out.get("candidates", [])]


def connect_part(pointer: str, principals: list, part_files: list, cache: dict, shareable: bool = False,
                 pinned: dict = None) -> dict:
    """Preview -> confirm connect for one pointer (a whole pointer or one split part of one).
    Labels ride in the bracketed description only for a file whose stage-2 label gate passed
    (cache["labels_ok"]); a cache entry without that key (pre-two-stage cache) defaults to
    carrying its labels, since it passed under the old single-claim gate. Everything else
    connects on its plain description -- a label problem never drops a file.

    `principals` carries every principal this pointer must stay registered for -- a pointer
    connected under several principals (e.g. primary + primary-helper) must repeat all of
    them on every reconnect, or the harness sees the request as narrowing its scope and
    refuses with "scope-change".

    `pinned` ({path: sha256}) holds --approve-held files: when the preview's hash for one differs,
    the reviewed connect is not sent and the paths come back as `drifted`."""
    sources = []
    for p in part_files:
        c = cache[str(p)]
        if c.get("labels_ok", True):
            desc = labeled_description(c["description"], {
                "kind": c.get("kind", "unknown"),
                "status": c.get("status", "unknown"),
                "as_of": c.get("as_of", "unknown"),
                "subject": c.get("subject", "unknown"),
            })
        else:
            desc = c["description"]
        sources.append({"path": str(p), "description": desc})
    held = [s["path"] for s in sources if payload_has_secret(s)]
    if held:
        print(f"connect held for {pointer}: secret-like text in {', '.join(held)}; not sent")
        return {"connected": False}
    req = {"action": "connect", "pointer": pointer, "principals": list(principals), "sources": sources}
    if shareable:
        req["shareable"] = True
    # Whether the pointer exists is asked of EVERY principal, not just the first: a part that one
    # of them cannot see yet (never shared to it) still exists, and a fresh connect of it fails
    # "already-connected" on every refresh and heal. Refresh it in the scope it has, then widen.
    seen_by = [p for p in principals
               if any((x.get("pointer") if isinstance(x, dict) else x) == pointer
                      for x in memory({"action": "panel", "principal": p}).get("pointers", []))]
    widen_to = None
    if seen_by:
        req["replace"] = True
        print(f"WARNING: replace:true on pointer {pointer} rotates that pointer's approved answers")
        if set(seen_by) != set(principals):
            widen_to = list(principals)
            req["principals"] = seen_by
    prev = memory(req)
    if prev.get("status") != "preparation-required":
        print(f"connect preview failed for {pointer}:", json.dumps(prev)[:300])
        return {"connected": False}
    # The backend echoes each path in realpath form: a symlinked file (a skill folder whose SKILL.md
    # links into tools/) came back under its target and crashed the skills reconnect with a KeyError.
    hashes = {os.path.realpath(x["path"]): x["sha256"] for x in prev["sources"]}
    for s in req["sources"]:
        s["sha256"] = hashes[os.path.realpath(s["path"])]
    drifted = [s["path"] for s in req["sources"] if s["path"] in (pinned or {}) and s["sha256"] != pinned[s["path"]]]
    if drifted:
        print(f"connect held for {pointer}: approved file(s) changed since approval: {', '.join(drifted)}; not sent")
        return {"connected": False, "drifted": drifted}
    req["reviewed"] = True
    reg = memory(req)
    wider = reg.get("registeredPrincipals") if reg.get("reason") == "scope-change" else None
    if req.get("replace") and wider:
        # A refresh naming only some of the pointer's principals would narrow it; the harness
        # refuses that, which left the pointer stale. Keep its registered scope instead.
        print(f"refresh: {pointer} is registered for {', '.join(wider)}; reconnecting with all of them")
        principals[:] = list(dict.fromkeys(list(principals) + list(wider)))
        req["principals"] = list(wider)
        reg = memory(req)
    if reg.get("status") == "registered" and widen_to and not set(widen_to) <= set(req["principals"]):
        scope = sorted(set(req["principals"]) | set(widen_to))
        print(f"refresh: {pointer} was registered for {', '.join(req['principals'])} only; "
              f"widening to {', '.join(scope)} like the rest of this connect")
        reg = {**reg, **memory({"action": "register", "pointer": pointer,
                                "dataset": reg.get("dataset") or pointer, "principals": scope})}
    connected = reg.get("status") == "registered"
    print(f"connect: {reg.get('status')} pointer={reg.get('pointer')} sources={len(reg.get('sources', []))}")
    if not connected:
        print(json.dumps(reg)[:300])
    return {"connected": connected}


def load_cache_files(pointer: str) -> dict:
    """Merge prepare-cache/<pointer>.json with any <pointer>-N.json part caches. No provider calls."""
    entries = {}
    if not CACHE_DIR.is_dir():
        return entries
    pat = re.compile(rf"^{re.escape(pointer)}(-\d+)?\.json$")
    for p in sorted(CACHE_DIR.glob(f"{pointer}*.json")):
        if not pat.match(p.name):
            continue
        try:
            entries.update(json.loads(p.read_text()))
        except Exception:
            continue
    return entries


def _label_match(c_kind: str, c_status: str, c_as_of: str, c_subject: str, today: date,
                  status: str = None, kind: str = None, within_days: int = None, subject: str = None):
    """Shared filter for one label set against --status/--kind/--subject/--within-days.
    Returns (included, excluded_unknown_date) -- excluded_unknown_date is only meaningful
    when within_days was given and the row was dropped for an unresolvable as_of."""
    if status and c_status != status:
        return False, False
    if kind and c_kind != kind:
        return False, False
    if subject and subject.lower() not in c_subject.lower():
        return False, False
    if within_days is not None:
        try:
            if c_as_of == "unknown" or (today - date.fromisoformat(c_as_of)).days > within_days:
                return False, c_as_of == "unknown"
        except ValueError:
            return False, True
    return True, False


def list_cmd(pointer: str, status: str = None, kind: str = None,
             within_days: int = None, subject: str = None):
    """No-judge local filter over already-gated labels. Returns (rows, excluded_unknown_date)."""
    entries = load_cache_files(pointer)
    today = date.today()
    rows, excluded = [], 0
    for path, c in entries.items():
        if not c.get("pass"):
            continue
        c_kind = c.get("kind", "unknown"); c_status = c.get("status", "unknown")
        c_as_of = c.get("as_of", "unknown"); c_subject = c.get("subject", "unknown")
        ok, exc = _label_match(c_kind, c_status, c_as_of, c_subject, today, status, kind, within_days, subject)
        if exc:
            excluded += 1
        if not ok:
            continue
        rows.append((c_subject, c_status, c_as_of, c_kind, path))
    rows.sort(key=lambda r: (r[2] != "unknown", r[2]), reverse=True)
    return rows, excluded


def _state_dir(principal: str) -> Path:
    """Same state-dir resolution as ask.py's state_dir; kept local to avoid a circular
    import (ask.py imports the label enums/helpers from this module)."""
    root = os.environ.get("SUPERJEV_STATE_DIR")
    base = Path(root).expanduser() if root else Path.home() / ".local/state/super-jev"
    return base / principal


def manual_label_rows(principal: str, status: str = None, kind: str = None,
                       within_days: int = None, subject: str = None):
    """Scan a principal's ask.py manual records (state-dir manual/*.md) for the same
    kind/status/as_of/subject header lines ask.py --add writes, filtered the same way
    as list_cmd. Path column shows the manual pointer name (<principal>-manual-*), not
    a filesystem path. Never calls the writer, the gate, or memory."""
    manual_dir = _state_dir(principal) / "manual"
    today = date.today()
    rows, excluded = [], 0
    if not manual_dir.is_dir():
        return rows, excluded
    for p in sorted(manual_dir.glob("*.md")):
        labels = {"kind": "unknown", "status": "unknown", "as_of": "unknown", "subject": "unknown"}
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            for key in labels:
                prefix = f"{key}:"
                if line.startswith(prefix):
                    labels[key] = line.split(":", 1)[1].strip()
        ok, exc = _label_match(labels["kind"], labels["status"], labels["as_of"], labels["subject"],
                                today, status, kind, within_days, subject)
        if exc:
            excluded += 1
        if not ok:
            continue
        rows.append((labels["subject"], labels["status"], labels["as_of"], labels["kind"], p.stem))
    return rows, excluded


def replay_recipe(a) -> None:
    """--refresh replays the pointer's recorded recipe for anything not given on the command line,
    so a refresh never widens a pointer (a missing --no-recurse once grew tools/ to 634 files)."""
    try:
        rep = json.loads((CACHE_DIR / f"{a.pointer}-report.json").read_text())
    except (OSError, ValueError):
        return
    if not isinstance(rep, dict):
        return
    # Only a new root set, --exclude or --no-recurse on the command line rescopes a pinned pointer;
    # refresh_changed.py re-passes the recorded roots/excludes/--no-recurse, which must not unpin it.
    new_roots = bool(a.roots and sorted(str(given_path(r)) for r in a.roots) != sorted(rep.get("roots") or []))
    supplied_ext = getattr(a, 'extensions', None)
    recorded_ext = rep.get('extensions', list(CONNECTABLE_EXTENSIONS))
    # Validate recorded scope as strictly as CLI input before using it.
    if not isinstance(recorded_ext, list) or not recorded_ext or any(not isinstance(e, str) for e in recorded_ext):
        raise ValueError('invalid recorded extensions')
    recorded_ext = list(extension_list(','.join(recorded_ext)))
    rescoped = bool((a.excludes and sorted(a.excludes) != sorted(rep.get("excludes") or []))
                    or (a.names and sorted(a.names) != sorted(rep.get("names") or []))
                    or (a.no_recurse and not rep.get("noRecurse"))
                    or (supplied_ext is not None and sorted(supplied_ext) != sorted(recorded_ext))
                    or new_roots)
    a.extensions = supplied_ext if supplied_ext is not None else recorded_ext
    a.roots = a.roots or rep.get("roots") or None
    a.principals = a.principals or rep.get("principals") or ([rep["principal"]] if rep.get("principal") else [])
    a.excludes = a.excludes or rep.get("excludes") or []
    # A new root set is a new scope: the old root's --no-recurse does not carry over to it.
    a.no_recurse = a.no_recurse or (not new_roots and bool(rep.get("noRecurse")))
    a.names = a.names or rep.get("names") or []
    a.allow_targets = a.allow_targets or rep.get("allowTargets") or []
    # --allow-held is never replayed: it would admit NEW secret-looking files without review.
    # --approve-held approvals are: each admits only the exact bytes a person reviewed (sha256).
    a.recorded_approvals = {e["path"]: e["sha256"] for e in rep.get("approvedHeld") or []
                            if isinstance(e, dict) and isinstance(e.get("path"), str)
                            and isinstance(e.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", e["sha256"])}
    if a.limit is None and isinstance(rep.get("limit"), int):
        a.limit = rep["limit"]
    print(f"refresh: replaying recorded recipe (roots {len(a.roots or [])}, excludes {a.excludes}, "
          f"no-recurse {a.no_recurse}, part size {a.limit or 50}; --allow-held is never replayed, "
          f"{len(a.recorded_approvals)} --approve-held file(s) replayed while unchanged)")
    # A report from before recipes were recorded has no noRecurse key: its root alone would re-inventory
    # the whole (possibly grown) folder, so its recorded file list is the scope instead.
    # The pinned list is re-recorded as scopeFiles so later refreshes stay pinned too.
    if rescoped:
        return
    a.pinned_since = rep.get("pinnedSince") if isinstance(rep.get("pinnedSince"), (int, float)) else None
    a.unconnected_new = [p for p in rep.get("unconnectedNew") or [] if isinstance(p, str)]
    a.unconnected_tries = rep.get("unconnectedTries") if isinstance(rep.get("unconnectedTries"), int) else 0
    a.left_out = [p for p in rep.get("leftOut") or [] if isinstance(p, str)]
    if isinstance(rep.get("scopeFiles"), list):
        a.legacy_scope = set(rep["scopeFiles"])
    elif "noRecurse" not in rep:
        a.legacy_scope = {str(e[0] if isinstance(e, list) else e)
                          for k in ("approved", "exceptions", "held") for e in rep.get(k) or []}
    if getattr(a, "legacy_scope", None) is not None:
        print(f"refresh: legacy report without a recorded recipe; keeping its {len(a.legacy_scope)} recorded files "
              "(pass --root/--exclude/--no-recurse to rescope)")


def pinned_folders(scope) -> set:
    """The folders a pinned file list connects: a file written later directly in one belongs to it."""
    return {str(Path(f).parent) for f in scope}


def pinned_since(cache: dict, scope) -> float | None:
    """When a pinned list was connected: its oldest reviewed file's checkedAt (None if unknown)."""
    times = []
    for f in scope:
        try:
            times.append(datetime.fromisoformat((cache.get(f) or {})["checkedAt"]).timestamp())
        except (KeyError, TypeError, ValueError):
            continue
    return min(times) if times else None


# Rebuilt automatically (SKILL.md: keep them out with --exclude so they do not stale a pointer daily).
AUTO_REBUILT = frozenset({"links.md", "index.md"})


def born_after(path, since: float | None) -> bool:
    """True for a file created after `since` that may join a pinned list. A legacy report never
    recorded its --exclude, so a file already there at connect (left out on purpose) is not new,
    nor is an auto-rebuilt index; with no known connect time nothing is. Birth time where the
    system has one, else last change time."""
    if since is None or Path(path).name.casefold() in AUTO_REBUILT:
        return False
    try:
        st = os.stat(path)
    except OSError:
        return False
    return getattr(st, "st_birthtime", st.st_ctime) > since


def growth(scope, paths, since: float | None, left_out, others) -> tuple[list, list]:
    """(grown, left): of inventoried `paths`, the files that join a pinned list, and the ones that
    were there at its connect (recorded as leftOut, so a later atomic save, which resets a birth
    time, cannot bring them back). A file joins only directly in a folder the list connects that no
    other pointer's files sit in (`others`: every other report's files), so a new note never lands
    in a pointer whose principals differ from its neighbours'. refresh_changed.new_files uses this too."""
    def real(folder) -> str:  # one spelling per folder: a symlinked or re-cased path is the same folder
        return os.path.realpath(folder).casefold()
    folders = {real(f) for f in pinned_folders(scope)}
    shared = {real(Path(k).parent) for k in others}
    grown, left = [], []
    for p in map(str, paths):
        parent = real(Path(p).parent)
        if (p in scope or p in left_out or parent not in folders or parent in shared
                or p in others or os.path.realpath(p) in others):
            continue
        if born_after(p, since):
            grown.append(p)
        elif since is not None and Path(p).name.casefold() not in AUTO_REBUILT:
            left.append(p)
    return grown, left


def principal_name(name: str) -> str:
    """argparse type for --principal: the memory runtime's agent-name rule, checked before any
    state path is built from it (letters, digits, ".", "_", "-"; starts with a letter or digit)."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
        raise argparse.ArgumentTypeError(f"invalid agent name {name!r}: letters, digits, '.', '_', '-'; "
                                         "no spaces or slashes")
    return name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", dest="roots", action="append")
    ap.add_argument('--ext', action='append', type=extension_list,
                    help='opt into extra UTF-8 text/code suffixes; comma-separated, repeatable (default: md only)')
    ap.add_argument("--pointer")
    ap.add_argument("--principal", dest="principals", action="append", default=[], type=principal_name,
                    help="repeatable. On --refresh, a pointer registered for several principals "
                         "(e.g. primary + primary-helper) must repeat --principal for each one it "
                         "still serves, or the harness refuses the refresh with scope-change")
    ap.add_argument("--exclude", dest="excludes", action="append", default=[])
    ap.add_argument("--no-recurse", action="store_true")
    ap.add_argument("--name", dest="names", action="append", default=[])
    ap.add_argument("--allow-target", dest="allow_targets", action="append", default=[],
                    help="folder a symlinked file may point into besides the roots (repeatable)")
    ap.add_argument("--limit", type=int, default=None); ap.add_argument("--max-files", type=int, default=MAX_FILES)
    ap.add_argument("--batch", type=int, default=10); ap.add_argument("--line", type=float, default=0.80)
    ap.add_argument("--writer", choices=["auto", "claude", "builtin"], default="auto",
                    help="builtin: no model call, descriptions quoted from each file's headings (needs only the "
                         "TypeSafe key). auto (default): --writer-command if given, else the claude CLI if it is "
                         "installed, else builtin")
    ap.add_argument("--writer-model", default="haiku",
                    help="model passed to the default Claude writer")
    ap.add_argument("--writer-command", metavar="COMMAND",
                    help="shell-style command for another writer; it receives the prompt on stdin and returns a JSON array on stdout. "
                         "Falls back to the SUPERJEV_WRITER_COMMAND env var when omitted")
    ap.add_argument("--allow-held", action="store_true",
                    help="admit files the secret scan would hold (still listed in the held file, noting the override); "
                         "the size-ceiling hold is unaffected")
    ap.add_argument("--approve-held", dest="approve_held", action="append", default=[], metavar="PATH",
                    help="admit this size-held file after a person reviewed it (repeatable), up to "
                         f"{APPROVE_MAX_BYTES:,} bytes; its sha256 is recorded and --refresh replays the approval only "
                         "while the file is unchanged. Never a secret-like or credential/key file")
    ap.add_argument("--no-connect", action="store_true")
    ap.add_argument("--no-findability", action="store_true", help=argparse.SUPPRESS)  # the default now
    ap.add_argument("--findability", action="store_true",
                    help="after connecting, search each file's own sample question (one search per file, "
                         "paid judge calls) and report the misses; off by default")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--shareable", action="store_true",
                    help="mark this connection shareable with other agents (default private); a person's decision")
    ap.add_argument("--no-shared", action="store_true",
                    help="skip the onboarding default: sharing the fleet's shared-pointers.json list with --principal")
    ap.add_argument("--list", action="store_true",
                    help="no-judge local list: read already-gated labels from prepare-cache and filter them; "
                         "never calls the writer, the gate, or memory")
    ap.add_argument("--status", default=None); ap.add_argument("--kind", default=None)
    ap.add_argument("--subject", default=None)
    ap.add_argument("--within-days", type=int, default=None)
    a = ap.parse_args()
    a.extensions = list(dict.fromkeys((*CONNECTABLE_EXTENSIONS, *(e for group in a.ext for e in group)))) if a.ext is not None else None
    a.no_findability = a.no_findability or not a.findability
    for path in a.approve_held:
        if credential_suffix(given_path(path).name) or credential_suffix(given_path(path).resolve().name):
            print(f"REFUSED: --approve-held {path}: credential/key file suffixes can never be approved"); return 2
        gp = given_path(path)
        if path_has_secret(gp.name) or (gp.is_file() and has_secret(gp.read_bytes().decode("utf-8", "replace"))):
            print(f"REFUSED: --approve-held {path}: {SECRET_NOT_APPROVABLE}"); return 2

    if a.list:
        if not a.pointer and not a.principals:
            print("REFUSED: --list needs --pointer and/or --principal"); return 2
        rows, excluded = [], 0
        if a.pointer:
            r, e = list_cmd(a.pointer, a.status, a.kind, a.within_days, a.subject)
            rows += r; excluded += e
        for principal in a.principals:
            r, e = manual_label_rows(principal, a.status, a.kind, a.within_days, a.subject)
            rows += r; excluded += e
        rows.sort(key=lambda r: (r[2] != "unknown", r[2]), reverse=True)
        print(f"{'subject':20} | {'status':8} | {'as_of':10} | {'kind':10} | path")
        for subject, status, as_of, kind, path in rows:
            print(f"{subject:20} | {status:8} | {as_of:10} | {kind:10} | {path}")
        if a.within_days is not None:
            print(f"\n{excluded} excluded (as_of unknown)")
        return 0

    if a.refresh and a.pointer:
        try:
            replay_recipe(a)
        except (ValueError, argparse.ArgumentTypeError) as e:
            print(f'REFUSED: {e}'); return 2
    if a.extensions is None:
        a.extensions = list(CONNECTABLE_EXTENSIONS)
    if a.limit is None:
        a.limit = 50
    if not a.roots or not a.principals or not a.pointer:
        print("REFUSED: --root, --pointer and --principal are required unless --list is given"); return 2
    if a.limit > 50:
        print("REFUSED: connect accepts at most 50 files per pointer part; use --limit <= 50 (parts split automatically)"); return 2
    writer_command_str = a.writer_command or os.environ.get("SUPERJEV_WRITER_COMMAND")
    try:
        writer_command = shlex.split(writer_command_str) if writer_command_str else None
    except ValueError as e:
        print(f"REFUSED: invalid --writer-command: {e}"); return 2
    if writer_command_str and not writer_command:
        print("REFUSED: --writer-command must name a command"); return 2

    use_builtin = a.writer == "builtin" or (a.writer == "auto" and not writer_command
                                              and not shutil.which("claude"))
    if use_builtin:
        why = "" if a.writer == "builtin" else " (no claude CLI found)"
        print(f"writer: builtin{why} -- descriptions quoted from each file's headings, no model call")
    else:
        banner_cmd = " ".join(writer_command) if writer_command else f"claude -p --model {a.writer_model}"
        print(f"writer: {banner_cmd}")
    if not writer_command and not use_builtin:
        print("tip: bulk labeling should run on a cheap writer model, never a premium one; the proven default "
              "is claude -p --model haiku (the Claude Code Haiku command) -- set --writer-command or "
              "SUPERJEV_WRITER_COMMAND for another adapter that reads the prompt on stdin and prints JSON")

    roots = [given_path(r) for r in a.roots]
    CACHE_DIR.mkdir(exist_ok=True)
    cache_path = CACHE_DIR / f"{a.pointer}.json"
    cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    t0 = time.time()

    missing = [str(r) for r in roots if not r.is_dir()]
    if missing:
        print(f"REFUSED: --root is not a folder: {', '.join(missing)}"); return 2
    approvals = dict(getattr(a, "recorded_approvals", {}))
    given = {}
    for path in a.approve_held:
        gp = given_path(path)
        if gp.is_file():
            approvals[str(gp)] = given[str(gp)] = sha(gp)
        else:
            print(f"  --approve-held {path}: no such file; nothing approved")
    used = {}
    files, held = inventory(roots, a.excludes, a.no_recurse, a.allow_held, a.names, a.allow_targets, a.extensions,
                            approvals, used)
    scope = getattr(a, "legacy_scope", None)
    if scope is not None:
        # A note written later into a folder the pinned list already connects joins the scope
        # (same holds, review and connect gate as every file); files in other folders stay out.
        from refresh_changed import known_across
        since = a.pinned_since = getattr(a, "pinned_since", None) or pinned_since(cache, scope)
        grown, left = growth(scope, files + [h[0] for h in held], since, set(a.left_out),
                             known_across(CACHE_DIR, skip=a.pointer))
        a.left_out = sorted(p for p in set(a.left_out) | set(left) if Path(p).exists())
        if len(grown) > a.max_files:
            print(f"refresh: {len(grown)} new files in already-connected folders exceed --max-files "
                  f"{a.max_files}; not added (pass --root/--exclude/--no-recurse to rescope)")
        elif grown:
            print(f"refresh: {len(grown)} new file(s) in already-connected folders join the pinned list")
            scope = a.legacy_scope = scope | set(grown)
            a.grown = grown
        files = [p for p in files if str(p) in scope]
        held = [(p, why) for p, why in held if str(p) in scope]
    print(f"inventory: {len(files)} files to prepare, {len(held)} held")
    for p, why in held:
        print(f"  HELD  {relstr(p, roots)}  ({why})")
    write_held_txt(a.pointer, held)
    for p, why in held:
        if "admitted by --approve-held" in why:
            print(f"  APPROVED  {relstr(p, roots)}  ({why})")
    for path in given:
        if path not in used:
            print(f"  --approve-held {path}: not held in this pointer's scope; nothing approved or recorded")
    # Recorded approvals persist while their file exists (a stale one keeps naming itself in the hold reason);
    # one given now is recorded only when it admitted a held file.
    approved_held = {k: v for k, v in getattr(a, "recorded_approvals", {}).items() if Path(k).exists()}
    approved_held.update({k: approvals[k] for k in used})
    # The reviewed bytes are pinned: every later stage re-reads the path, so an approved file whose
    # sha256 no longer matches before it is cached or connected is held again, never cached or sent.
    pinned, dropped = {path: approvals[k] for k, path in used.items()}, set()

    def drifted(p) -> bool:
        want = pinned.get(str(p))
        if str(p) in dropped:
            return True
        if want is None or sha(p) == want:
            return False
        hold_changed(p)
        return True

    def hold_changed(p) -> None:
        want = pinned[str(p)]
        dropped.add(str(p))
        cache.pop(str(p), None)
        base = next((w for h, w in held if h == str(p)), "").split("; admitted by --approve-held")[0]
        held[:] = [(h, w) for h, w in held if h != str(p)] + [
            (str(p), f"{base}; changed since its --approve-held review "
                     f"(approved sha256 {want[:12]}), review it again")]
        write_held_txt(a.pointer, held)
        print(f"  HELD  {relstr(p, roots)}  (changed after its approval was checked this run; not cached or connected)")
    if not files and not held:
        print(f"ERROR: no {','.join(a.extensions)} files found under {', '.join(str(r) for r in roots)} "
              "(empty, hidden or excluded files are skipped); nothing to connect"); return 1

    # First connect (no cache yet) has no way to know how many files would actually need a
    # writer call, so the guard is on the raw inventory. A refresh already has a cache: most
    # files are unchanged and cost nothing to keep serving, so a big folder must not be blocked
    # from refreshing just because it once grew past --max-files. There, the guard moves to the
    # files that would actually need a writer call (below), and total size is reported, not refused.
    if not (a.refresh and cache):
        if len(files) > a.max_files:
            print(f"REFUSED: {len(files)} files exceed --max-files {a.max_files}; narrow --root/--exclude/--no-recurse or raise --max-files")
            return 2

    removed = []
    if a.refresh:
        live = {str(p) for p in files}
        for k in list(cache.keys()):
            if k not in live and not Path(k).exists():
                removed.append(k)
                cache.pop(k, None)
        if removed:
            print(f"refresh: {len(removed)} cached files removed from disk, dropped from cache and connect set")
            for k in removed:
                print(f"  REMOVED  {relstr(k, roots)}")
    # Word search reads this cache as the pointer's file list, so an entry outside the current
    # scope (an old --root, a narrowed --exclude/--name) would keep surfacing unconnected files.
    in_scope = {str(p) for p in files} | {str(p) for p, _ in held}
    out_of_scope = [k for k in cache if k not in in_scope]
    for k in out_of_scope:
        cache.pop(k, None)
    if out_of_scope:
        print(f"cache: {len(out_of_scope)} entries outside the current scope dropped")

    todo, reused = [], []
    for p in files:
        c = cache.get(str(p))
        if c and c.get("sha256") == sha(p) and c.get("pass"):
            reused.append(p)
        else:
            todo.append(p)
    print(f"cache: {len(reused)} unchanged and already passing, {len(todo)} to draft")

    if a.refresh and cache and len(files) > a.max_files:
        print(f"refresh: {len(files)} files total (over --max-files {a.max_files}), "
              f"but only {len(todo)} need a writer call this run (cost); the rest are unchanged and reused for free")
        if len(todo) > a.max_files:
            print(f"REFUSED: {len(todo)} files need drafting, which itself exceeds --max-files {a.max_files}; "
                  "raise --max-files to opt into the larger writer cost, or narrow --root/--exclude/--no-recurse first")
            return 2

    # An approved file is read once here and its hash verified; the writer gets exactly those bytes,
    # so an edit after inventory never reaches the writer.
    snapshot = {}
    for p in [p for p in todo if str(p) in pinned]:
        b = p.read_bytes()
        if hashlib.sha256(b).hexdigest() == pinned[str(p)]:
            snapshot[str(p)] = b.decode("utf-8", "replace")
        else:
            hold_changed(p)
    todo = [p for p in todo if str(p) not in dropped]

    def ex(p) -> dict:
        return excerpt(p, snapshot.get(str(p)))

    drafts = {}
    for i in range(0, len(todo), a.batch):
        batch = todo[i:i + a.batch]
        try:
            if use_builtin:
                got = builtin_writer([ex(p) for p in batch])
            elif writer_command:
                got = writer([ex(p) for p in batch], a.writer_model, command=writer_command)
            else:
                got = writer([ex(p) for p in batch], a.writer_model)
        except WriterError as e:
            print(f"ERROR: description writer failed: {e}")
            if not writer_command:
                print("  The claude CLI must be installed and logged in for this writer. Or re-run with "
                      "--writer builtin (no model call; needs only TYPESAFE_API_KEY).")
            return 1
        drafts.update(got)
        print(f"writer batch {i // a.batch + 1}: {len(got)}/{len(batch)} drafted")

    def fmt_conf(verdict: dict) -> str:
        if verdict.get("state") == "QUOTED":
            return "quoted"
        c = verdict.get("confidence")
        return f"{c:.2f}" if isinstance(c, (int, float)) else "n/a"

    packed = {}
    if not use_builtin and os.environ.get("SUPERJEV_BATCH_JEV") != "0":
        sizes = {}
        for p in todo:
            try:
                sizes[str(p)] = len(Path(p).read_text(errors="replace"))
            except OSError:
                pass
        ready = [p for p in todo if (drafts.get(str(p)) or {}).get("description") and not drifted(p)]
        for group in pack_groups(ready, sizes):
            items = [(p, drafts[str(p)]["description"].strip(),
                      claim_sentence(validate_labels(drafts[str(p)]))) for p in group]
            packed.update(gate_pack(items))
        if packed:
            print(f"gate packs: {len(packed)} files checked in shared calls")

    exceptions, passing = [], []
    for p in todo:
        if drifted(p):  # never gate bytes nobody reviewed
            continue
        d = drafts.get(str(p))
        if not d or not d.get("description"):
            exceptions.append((str(p), "writer returned no draft")); continue

        # Stage 1: gate the description alone -- exactly the pre-labels claim. A label
        # problem must never cost a file its place; only a description problem does.
        desc = d["description"].strip()
        if use_builtin and d["description"] == builtin_writer([ex(p)])[str(p)]["description"]:
            # A built-in description is only the file's own headings and words, quoted; rebuilding
            # it from the file proves that exactly. The judge scored such quotes 0.29-0.89, so a
            # plain note could fall under the line and be set aside for no real reason.
            v = {"state": "QUOTED", "confidence": None}
        elif packed.get(str(p)):
            v = packed[str(p)][0]
        else:
            v = gate(desc, str(p))
        ok = v["state"] == "QUOTED" or (v["state"] == "SUPPORTED" and v.get("confidence", 0) >= a.line)
        if not ok:
            fb = {str(p): {"draft": desc, "verdict": v["state"], "confidence": v.get("confidence"), "reason": v.get("reason")}}
            try:
                if use_builtin:
                    redo = None  # a quoted description has nothing to rewrite
                elif writer_command:
                    redo = writer([ex(p)], a.writer_model, feedback=fb, command=writer_command).get(str(p))
                else:
                    redo = writer([ex(p)], a.writer_model, feedback=fb).get(str(p))
            except WriterError as e:
                print(f"ERROR: description writer failed: {e}"); return 1
            if redo and redo.get("description"):
                redo_desc = redo["description"].strip()
                v2 = gate(redo_desc, str(p))
                if v2["state"] == "SUPPORTED" and v2.get("confidence", 0) >= a.line:
                    d, desc, v, ok = redo, redo_desc, v2, True

        if drifted(p):
            continue
        if not ok:
            cache[str(p)] = {"sha256": pinned.get(str(p)) or sha(p), "description": d["description"], "question": d.get("question", ""),
                             "kind": "unknown", "status": "unknown", "as_of": "unknown", "subject": "unknown",
                             "verdict": v["state"], "confidence": v.get("confidence"), "pass": False,
                             "labels_ok": False,
                             "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
            print(f"  {v['state']:14}{v.get('confidence', ''):>5}  {relstr(p, roots)}")
            exceptions.append((str(p), f"{v['state']} {v.get('confidence', '')} {v.get('reason', '')}".strip()))
            continue

        # Stage 2 (only reached on a stage-1 pass): gate the label sentence alone. A miss here
        # never drops the file -- it connects on its plain description with labels unknown.
        labels = validate_labels(d)
        # the built-in writer drafts no labels, so there is nothing worth a judge call
        pre = packed.get(str(p))
        v_labels = ({"state": "SKIPPED"} if use_builtin
                    else pre[1] if pre and pre[1] and pre[0] is v
                    else gate(claim_sentence(labels), str(p)))
        labels_ok = v_labels["state"] == "SUPPORTED" and v_labels.get("confidence", 0) >= a.line
        if not labels_ok:
            labels = {"kind": "unknown", "status": "unknown", "as_of": "unknown", "subject": "unknown"}

        if drifted(p):
            continue
        cache[str(p)] = {"sha256": pinned.get(str(p)) or sha(p), "description": d["description"],
                         "question": d.get("question", ""), "kind": labels["kind"], "status": labels["status"], "as_of": labels["as_of"],
                         "subject": labels["subject"],
                         "verdict": v["state"], "confidence": v.get("confidence"), "pass": True,
                         "labels_ok": labels_ok, "labels_verdict": v_labels["state"],
                         "labels_confidence": v_labels.get("confidence"),
                         "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        if labels_ok:
            print(f"  PASS {fmt_conf(v)} | labels ok {fmt_conf(v_labels)}  {relstr(p, roots)}")
        else:
            print(f"  PASS {fmt_conf(v)} | labels unknown ({fmt_conf(v_labels)})  {relstr(p, roots)}")
        passing.append(p)
    connect_set = [p for p in reused + passing if not drifted(p)]
    cache_path.write_text(json.dumps(cache, indent=1))
    _record_written(cache_path)

    print(f"\napproved: {len(connect_set)}  exceptions: {len(exceptions)}  held: {len(held)}")
    rerun = "python3 " + shlex.join(sys.argv)
    for p, why in exceptions:
        print(f"  EXCEPTION  {relstr(p, roots)}  ({why})\n"
              f"      to include it: check the file says what it should, then run: {rerun}"
              + ("" if use_builtin else " --writer builtin"))
    for p, why in held:
        if "admitted by --allow-held" in why or "admitted by --approve-held" in why:
            continue
        print(f"  HELD  {relstr(p, roots)}  ({why})")
        if "review before onboarding" in why:
            print(f"      to include it (and any other held secret-looking file) after checking it: "
                  f"{rerun} --allow-held")
        elif "binary" not in why:
            print(f"      then run: {rerun}")
            if "over size ceiling" in why and Path(p).is_file() and Path(p).stat().st_size <= APPROVE_MAX_BYTES:
                print(f"      or, after checking it, connect it whole (gated in parts): "
                      f"{rerun} --approve-held {shlex.quote(p)}")

    # principal/excludes/noRecurse let refresh_changed.py re-run this exact prepare later.
    report = {"pointer": a.pointer, "roots": [str(r) for r in roots], "principal": a.principals[0],
              "extensions": a.extensions,
              "principals": a.principals,
              "excludes": a.excludes, "noRecurse": a.no_recurse, "names": a.names, "allowTargets": [str(Path(t).expanduser().resolve()) for t in a.allow_targets], "limit": a.limit,
              **({"scopeFiles": sorted(a.legacy_scope)} if getattr(a, "legacy_scope", None) is not None else {}),
              **({"pinnedSince": a.pinned_since} if getattr(a, "pinned_since", None) is not None else {}),
              **({"leftOut": a.left_out} if getattr(a, "left_out", None) else {}),
              "approved": [str(p) for p in connect_set],
              "approvedHeld": [{"path": k, "sha256": v} for k, v in sorted(approved_held.items())],
              "exceptions": exceptions, "held": held, "removed": removed, "findability": None,
              "connected": False, "parts": []}
    if a.no_connect or not connect_set:
        (CACHE_DIR / f"{a.pointer}-report.json").write_text(json.dumps(report, indent=1))
        _record_written(CACHE_DIR / f"{a.pointer}-report.json")
        print(f"no connect ({'--no-connect' if a.no_connect else 'nothing approved'}); {time.time() - t0:.0f}s")
        if a.no_connect:
            return 0
        # Nothing connected is a failure, never a quiet success; name the shared cause when there is one.
        reasons = sorted({why for _, why in exceptions})
        cause = reasons[0] if len(reasons) == 1 else f"{len(reasons)} different reasons, listed above"
        print(f"ERROR: all {len(exceptions) + len(held)} files failed or were held; nothing connected"
              + (f" ({cause})" if exceptions else ""))
        return 1

    ordered = sorted(connect_set, key=str)
    parts = split_parts(ordered, a.limit)
    if len(parts) > 1:
        print(f"splitting {len(ordered)} approved files into {len(parts)} parts of at most {a.limit}")

    all_connected = True
    hits, total, misses = 0, 0, []
    for idx, part_files in enumerate(parts):
        pname = a.pointer if idx == 0 else f"{a.pointer}-{idx + 1}"
        result = connect_part(pname, a.principals, part_files, cache, shareable=a.shareable, pinned=pinned)
        for p in result.get("drifted", []):
            hold_changed(p)
            report["approved"] = [x for x in report["approved"] if x != str(p)]
            cache_path.write_text(json.dumps(cache, indent=1))
        report["parts"].append({"pointer": pname, "count": len(part_files), "connected": result["connected"]})
        if not result["connected"]:
            all_connected = False
            continue
        if not a.no_findability:
            for p in part_files:
                q = cache[str(p)].get("question") or ""
                total += 1
                if not q:
                    misses.append((str(p), "no question")); continue
                top = navigate(pname, a.principals[0], q)
                if top and top[0] == str(p):
                    hits += 1
                else:
                    misses.append((str(p), f"ranked {'#' + str(top.index(str(p)) + 1) if str(p) in top else 'absent'}; top={Path(top[0]).name if top else 'none'}"))
    report["connected"] = all_connected
    unsent = [p for p in dict.fromkeys(getattr(a, "grown", []) + getattr(a, "unconnected_new", []))
              if p in report["approved"]] if not all_connected else []
    if unsent:
        # refresh_changed.new_files keeps them new until they connect, for UNCONNECTED_TRIES refreshes:
        # a connect that fails every time (a held payload, a refusal) must not eat the heal budget.
        report["unconnectedNew"] = unsent
        report["unconnectedTries"] = getattr(a, "unconnected_tries", 0) + 1 if getattr(a, "unconnected_new", []) else 1
    if all_connected and not a.no_shared:
        # Onboarding default: every connected principal also sees the fleet's shared sets
        # (shared knowledge, skills catalog). Zero cost: register only, no writer or Jev call.
        from share_pointers import share_defaults
        share_defaults(a.principals, memory=memory)

    if not a.no_findability:
        report["findability"] = {"hits": hits, "total": total, "misses": misses}
        print(f"findability: {hits}/{total} files rank first on their own question")
        for p, why in misses:
            print(f"  MISS  {relstr(p, roots)}  ({why})")

    (CACHE_DIR / f"{a.pointer}-report.json").write_text(json.dumps(report, indent=1))
    _record_written(CACHE_DIR / f"{a.pointer}-report.json")
    print(f"done in {time.time() - t0:.0f}s; report -> {CACHE_DIR / (a.pointer + '-report.json')}")
    return 0 if all_connected else 1


if __name__ == "__main__":
    sys.exit(main())
