#!/usr/bin/env python3
"""Bulk preparation: inventory -> cheap writer drafts descriptions -> Jev checks them -> connect the approved set.

Usage:
  python3 prepare_bulk.py --root DIR [--root DIR2 ...] --pointer NAME --principal AGENT [--principal AGENT2 ...]
                          [--exclude SUBPATH ...] [--no-recurse] [--name GLOB ...] [--limit 50] [--max-files 250]
                          [--batch 10] [--line 0.80] [--writer-model haiku]
                          [--writer-command 'COMMAND [ARG ...]'] [--allow-held] [--no-connect]
                          [--no-findability] [--refresh]

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
  1. Inventory *.md under the union of one or more --root directories, in the order given (repeat --root for
     a whole agent brain spanning several folders). Skips .bak*, profile/, documents/, logins.md, *-secret.md,
     hidden directories and test/scratch output (ops/sj*/ except ops/sj-manual/, *superjev-test*, *-hand-test-*); --exclude SUBPATH (repeatable) also skips any file whose path relative to its
     root starts with that subpath; --no-recurse limits each root to its direct children only; --name GLOB (repeatable, e.g. SKILL.md)
     keeps only files whose name matches, so a skills folder connects its entry files and not every reference doc. Files matching
     card/password-like patterns or over the gate's size ceiling are HELD and never sent to the writer; a
     per-file reason (and, for the secret-pattern case, the matching line's pattern type and line number with
     all digits masked) is written to prepare-cache/<pointer>-held.txt for human review without opening files.
     The card-number check ignores ISO dates and URLs first (a long numeric id in a URL, or a run of dates on
     one line, must not trigger it); the password/api-key keyword check is never affected. --allow-held admits
     a file the secret scan alone would hold -- it is still listed in the held file, noting the override -- but
     never lifts the size-ceiling hold, since an oversized file cannot be gated regardless. A first connect
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
  6. Findability: each connected file's own sample question is navigated; the file must rank first or it is
     listed as a findability miss. Report only; no automatic loop beyond the one rewrite.
Nothing here edits original files. Cache and report land under prepare-cache/ next to this script.

A label is only as true as the file it was drafted and gated from; as_of shows staleness, not currency.
Live truth for anything time-sensitive still needs a gated roll-up read fresh, not a cached label.
"""
import argparse, fnmatch, hashlib, json, math, os, re, shlex, shutil, subprocess, sys, time, unicodedata
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from connect_checked import gate, memory  # noqa: E402

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
CEILING_BYTES = 90_000  # conservative stand-in for the gate's 32k-token ceiling

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
# answer in word search, so it is never connected or searched. Kept narrow: real ops notes stay.
TEST_MATERIAL_RE = re.compile(r"(^|/)ops/sj(?!-manual/)[^/]*/|superjev-test|-hand-test-", re.I)  # ops/sj-manual/ holds real facts


def is_test_material(path: str) -> bool:
    return bool(TEST_MATERIAL_RE.search(Path(path).as_posix()))


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


def card_hit(text: str, luhn: bool = True) -> bool:
    """A standalone 16-digit run (dates/URLs scrubbed) that passes the Luhn check. luhn=False when the
    raw text had non-ASCII digits: normalizing folds them to 0, so their true value is lost."""
    return any(not luhn or _luhn(re.sub(r"\D", "", m.group())) for m in CARD_RE.finditer(_scrub_dates_and_urls(text)))


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


def inventory(roots: list, excludes: list = None, no_recurse: bool = False, allow_held: bool = False,
              names: list = None, allow_targets: list = None):
    """Union of *.md files under `roots`, in root order then sorted-per-root order. Each file is counted once
    even if reachable through more than one root. --allow-held admits a file the secret scan would otherwise
    hold (still listed in `held`, with its reason noting the override); the size-ceiling hold is unaffected,
    since an oversized file cannot be gated regardless.
    A symlinked file is judged on its target too: the target must sit under a root or an `allow_targets`
    folder (--allow-target) and pass the same name/folder/secret-name checks, so a link cannot reach profile/,
    logins.md or any other file the roots would never have admitted."""
    bases = [Path(r).resolve() for r in list(roots) + list(allow_targets or [])]
    excludes = [e.strip("/") for e in (excludes or []) if e.strip("/")]
    files, held, seen = [], [], set()
    for root in roots:
        glob_iter = sorted(root.glob("*.md")) if no_recurse else sorted(root.rglob("*.md"))
        for p in glob_iter:
            rp = p.resolve()
            if rp in seen:
                continue
            # Case-insensitive: 225 of 315 fleet skills name the entry file skill.md, not SKILL.md.
            if names and not any(fnmatch.fnmatch(p.name.lower(), n.lower()) for n in names):
                continue
            base = next((b for b in bases if rp.is_relative_to(b)), None)
            if base is None:
                print(f"  SKIP  {p}  (links to {rp}, outside every --root/--allow-target)")
                continue
            if any(".bak" in n or n == "logins.md" or n.endswith("-secret.md") for n in (p.name, rp.name)):
                continue
            if any(part in SKIP_PARTS or part.startswith(".")
                   for part in p.relative_to(root).parts + rp.relative_to(base).parts):
                continue
            if (_excluded(p.relative_to(root).as_posix(), excludes) or is_test_material(p.relative_to(root).as_posix())
                    or is_bench_dataset(str(rp))):
                continue
            b = p.read_bytes()
            if not b.strip():
                continue
            seen.add(rp)
            if len(b) > CEILING_BYTES:
                held.append((str(p), f"over size ceiling ({len(b):,} bytes, max {CEILING_BYTES:,}); "
                                      "split it into smaller .md files, e.g. one per ## section")); continue
            if b"\x00" in b:
                held.append((str(p), "binary file (contains null bytes), not text; skipped")); continue
            try:
                b.decode("utf-8")
            except UnicodeDecodeError:
                held.append((str(p), "not UTF-8 text; re-save it as UTF-8 to connect it")); continue
            if has_secret(b.decode("utf-8", "replace")):
                if allow_held:
                    held.append((str(p), "card/password-like text; admitted by --allow-held"))
                else:
                    held.append((str(p), "card/password-like text; review before onboarding")); continue
            if path_has_secret(p.name) or path_has_secret(rp.name):
                if allow_held:
                    held.append((str(p), "secret-keyword-like file name; admitted by --allow-held"))
                else:
                    held.append((str(p), "secret-keyword-like file name; review before onboarding")); continue
            files.append(p)
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


def write_held_txt(pointer: str, held: list) -> None:
    if not held:
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


def excerpt(p: Path) -> dict:
    text = p.read_text(errors="replace")
    heads = [l.strip() for l in text.splitlines() if l.startswith("#")][:15]
    return {"path": str(p), "headings": heads, "start": text[:1200]}


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
        "shows them; no praise, no guessing beyond the excerpt and headings; if the file is a stub or pointer, say so.\n"
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
    # The one place the writer prompt leaves this machine: scan it here, whoever called.
    if has_secret(prompt):
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


def connect_part(pointer: str, principals: list, part_files: list, cache: dict) -> dict:
    """Preview -> confirm connect for one pointer (a whole pointer or one split part of one).
    Labels ride in the bracketed description only for a file whose stage-2 label gate passed
    (cache["labels_ok"]); a cache entry without that key (pre-two-stage cache) defaults to
    carrying its labels, since it passed under the old single-claim gate. Everything else
    connects on its plain description -- a label problem never drops a file.

    `principals` carries every principal this pointer must stay registered for -- a pointer
    connected under several principals (e.g. primary + primary-helper) must repeat all of
    them on every reconnect, or the harness sees the request as narrowing its scope and
    refuses with "scope-change"."""
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
    known = memory({"action": "panel", "principal": principals[0]})
    if any((x.get("pointer") if isinstance(x, dict) else x) == pointer for x in known.get("pointers", [])):
        req["replace"] = True
        print(f"WARNING: replace:true on pointer {pointer} rotates that pointer's approved answers")
    prev = memory(req)
    if prev.get("status") != "preparation-required":
        print(f"connect preview failed for {pointer}:", json.dumps(prev)[:300])
        return {"connected": False}
    # The backend echoes each path in realpath form: a symlinked file (a skill folder whose SKILL.md
    # links into tools/) came back under its target and crashed the skills reconnect with a KeyError.
    hashes = {os.path.realpath(x["path"]): x["sha256"] for x in prev["sources"]}
    for s in req["sources"]:
        s["sha256"] = hashes[os.path.realpath(s["path"])]
    req["reviewed"] = True
    reg = memory(req)
    wider = reg.get("registeredPrincipals") if reg.get("reason") == "scope-change" else None
    if req.get("replace") and wider:
        # A refresh naming only some of the pointer's principals would narrow it; the harness
        # refuses that, which left the pointer stale. Keep its registered scope instead.
        print(f"refresh: {pointer} is registered for {', '.join(wider)}; reconnecting with all of them")
        principals[:] = wider
        req["principals"] = list(wider)
        reg = memory(req)
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
    new_roots = bool(a.roots and sorted(str(Path(r).resolve()) for r in a.roots) != sorted(rep.get("roots") or []))
    rescoped = bool((a.excludes and sorted(a.excludes) != sorted(rep.get("excludes") or []))
                    or (a.names and sorted(a.names) != sorted(rep.get("names") or []))
                    or (a.no_recurse and not rep.get("noRecurse"))
                    or new_roots)
    a.roots = a.roots or rep.get("roots") or None
    a.principals = a.principals or rep.get("principals") or ([rep["principal"]] if rep.get("principal") else [])
    a.excludes = a.excludes or rep.get("excludes") or []
    # A new root set is a new scope: the old root's --no-recurse does not carry over to it.
    a.no_recurse = a.no_recurse or (not new_roots and bool(rep.get("noRecurse")))
    a.names = a.names or rep.get("names") or []
    a.allow_targets = a.allow_targets or rep.get("allowTargets") or []
    # --allow-held is never replayed: it would admit NEW secret-looking files without review.
    if a.limit is None and isinstance(rep.get("limit"), int):
        a.limit = rep["limit"]
    print(f"refresh: replaying recorded recipe (roots {len(a.roots or [])}, excludes {a.excludes}, "
          f"no-recurse {a.no_recurse}, part size {a.limit or 50}; --allow-held is never replayed)")
    # A report from before recipes were recorded has no noRecurse key: its root alone would re-inventory
    # the whole (possibly grown) folder, so its recorded file list is the scope instead.
    # The pinned list is re-recorded as scopeFiles so later refreshes stay pinned too.
    if rescoped:
        return
    if isinstance(rep.get("scopeFiles"), list):
        a.legacy_scope = set(rep["scopeFiles"])
    elif "noRecurse" not in rep:
        a.legacy_scope = {str(e[0] if isinstance(e, list) else e)
                          for k in ("approved", "exceptions", "held") for e in rep.get(k) or []}
    if getattr(a, "legacy_scope", None) is not None:
        print(f"refresh: legacy report without a recorded recipe; keeping its {len(a.legacy_scope)} recorded files "
              "(pass --root/--exclude/--no-recurse to rescope)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", dest="roots", action="append")
    ap.add_argument("--pointer")
    ap.add_argument("--principal", dest="principals", action="append", default=[],
                    help="repeatable. On --refresh, a pointer registered for several principals "
                         "(e.g. primary + primary-helper) must repeat --principal for each one it "
                         "still serves, or the harness refuses the refresh with scope-change")
    ap.add_argument("--exclude", dest="excludes", action="append", default=[])
    ap.add_argument("--no-recurse", action="store_true")
    ap.add_argument("--name", dest="names", action="append", default=[])
    ap.add_argument("--allow-target", dest="allow_targets", action="append", default=[],
                    help="folder a symlinked file may point into besides the roots (repeatable)")
    ap.add_argument("--limit", type=int, default=None); ap.add_argument("--max-files", type=int, default=250)
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
    ap.add_argument("--no-connect", action="store_true")
    ap.add_argument("--no-findability", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--list", action="store_true",
                    help="no-judge local list: read already-gated labels from prepare-cache and filter them; "
                         "never calls the writer, the gate, or memory")
    ap.add_argument("--status", default=None); ap.add_argument("--kind", default=None)
    ap.add_argument("--subject", default=None)
    ap.add_argument("--within-days", type=int, default=None)
    a = ap.parse_args()

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
        replay_recipe(a)
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

    roots = [Path(r).resolve() for r in a.roots]
    CACHE_DIR.mkdir(exist_ok=True)
    cache_path = CACHE_DIR / f"{a.pointer}.json"
    cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    t0 = time.time()

    missing = [str(r) for r in roots if not r.is_dir()]
    if missing:
        print(f"REFUSED: --root is not a folder: {', '.join(missing)}"); return 2
    files, held = inventory(roots, a.excludes, a.no_recurse, a.allow_held, a.names, a.allow_targets)
    scope = getattr(a, "legacy_scope", None)
    if scope is not None:
        files = [p for p in files if str(p) in scope]
        held = [(p, why) for p, why in held if str(p) in scope]
    print(f"inventory: {len(files)} files to prepare, {len(held)} held")
    for p, why in held:
        print(f"  HELD  {relstr(p, roots)}  ({why})")
    write_held_txt(a.pointer, held)
    if not files and not held:
        print(f"ERROR: no .md files found under {', '.join(str(r) for r in roots)} "
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

    drafts = {}
    for i in range(0, len(todo), a.batch):
        batch = todo[i:i + a.batch]
        try:
            if use_builtin:
                got = builtin_writer([excerpt(p) for p in batch])
            elif writer_command:
                got = writer([excerpt(p) for p in batch], a.writer_model, command=writer_command)
            else:
                got = writer([excerpt(p) for p in batch], a.writer_model)
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

    exceptions, passing = [], []
    for p in todo:
        d = drafts.get(str(p))
        if not d or not d.get("description"):
            exceptions.append((str(p), "writer returned no draft")); continue

        # Stage 1: gate the description alone -- exactly the pre-labels claim. A label
        # problem must never cost a file its place; only a description problem does.
        desc = d["description"].strip()
        if use_builtin and d["description"] == builtin_writer([excerpt(p)])[str(p)]["description"]:
            # A built-in description is only the file's own headings and words, quoted; rebuilding
            # it from the file proves that exactly. The judge scored such quotes 0.29-0.89, so a
            # plain note could fall under the line and be set aside for no real reason.
            v = {"state": "QUOTED", "confidence": None}
        else:
            v = gate(desc, str(p))
        ok = v["state"] == "QUOTED" or (v["state"] == "SUPPORTED" and v.get("confidence", 0) >= a.line)
        if not ok:
            fb = {str(p): {"draft": desc, "verdict": v["state"], "confidence": v.get("confidence"), "reason": v.get("reason")}}
            try:
                if use_builtin:
                    redo = None  # a quoted description has nothing to rewrite
                elif writer_command:
                    redo = writer([excerpt(p)], a.writer_model, feedback=fb, command=writer_command).get(str(p))
                else:
                    redo = writer([excerpt(p)], a.writer_model, feedback=fb).get(str(p))
            except WriterError as e:
                print(f"ERROR: description writer failed: {e}"); return 1
            if redo and redo.get("description"):
                redo_desc = redo["description"].strip()
                v2 = gate(redo_desc, str(p))
                if v2["state"] == "SUPPORTED" and v2.get("confidence", 0) >= a.line:
                    d, desc, v, ok = redo, redo_desc, v2, True

        if not ok:
            cache[str(p)] = {"sha256": sha(p), "description": d["description"], "question": d.get("question", ""),
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
        v_labels = ({"state": "SKIPPED"} if use_builtin
                    else gate(claim_sentence(labels), str(p)))
        labels_ok = v_labels["state"] == "SUPPORTED" and v_labels.get("confidence", 0) >= a.line
        if not labels_ok:
            labels = {"kind": "unknown", "status": "unknown", "as_of": "unknown", "subject": "unknown"}

        cache[str(p)] = {"sha256": sha(p), "description": d["description"], "question": d.get("question", ""),
                         "kind": labels["kind"], "status": labels["status"], "as_of": labels["as_of"],
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
    cache_path.write_text(json.dumps(cache, indent=1))
    _record_written(cache_path)

    connect_set = reused + passing
    print(f"\napproved: {len(connect_set)}  exceptions: {len(exceptions)}  held: {len(held)}")
    rerun = "python3 " + shlex.join(sys.argv)
    for p, why in exceptions:
        print(f"  EXCEPTION  {relstr(p, roots)}  ({why})\n"
              f"      to include it: check the file says what it should, then run: {rerun}"
              + ("" if use_builtin else " --writer builtin"))
    for p, why in held:
        if "admitted by --allow-held" in why:
            continue
        print(f"  HELD  {relstr(p, roots)}  ({why})")
        if "review before onboarding" in why:
            print(f"      to include it (and any other held secret-looking file) after checking it: "
                  f"{rerun} --allow-held")
        elif "binary" not in why:
            print(f"      then run: {rerun}")

    # principal/excludes/noRecurse let refresh_changed.py re-run this exact prepare later.
    report = {"pointer": a.pointer, "roots": [str(r) for r in roots], "principal": a.principals[0],
              "principals": a.principals,
              "excludes": a.excludes, "noRecurse": a.no_recurse, "names": a.names, "allowTargets": a.allow_targets, "limit": a.limit,
              **({"scopeFiles": sorted(a.legacy_scope)} if getattr(a, "legacy_scope", None) is not None else {}),
              "approved": [str(p) for p in connect_set],
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
    parts = [ordered[i:i + a.limit] for i in range(0, len(ordered), a.limit)]
    if len(parts) > 1:
        print(f"splitting {len(ordered)} approved files into {len(parts)} parts of at most {a.limit}")

    all_connected = True
    hits, total, misses = 0, 0, []
    for idx, part_files in enumerate(parts):
        pname = a.pointer if idx == 0 else f"{a.pointer}-{idx + 1}"
        result = connect_part(pname, a.principals, part_files, cache)
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
