#!/usr/bin/env python3
"""window_model.py — the gate's evidence window as labelled PIECES.

WHY THIS EXISTS
---------------
`superjev.py` composes ONE flat text window for every Stop-hook gate run
(`_derive_evidence_text_from_transcript`): the current turn's tool
results, the previous turns' tool results, the session receipts, this
turn's relayed worker/teammate reports, and the derived-fact head. Six
independent readers then parse that flat text again, each with its own
regexes and its own idea of where a section starts and where a worker's
prose ends:

  * the PR-state arm (`_pr_state_signals` / `_pr_mismatch_verdict`),
  * the labelled-count arm (`_extract_labelled_evidence_counts_scoped`),
  * the derived-fact families (written file, result table, merge receipt,
    labelled value, score list, extremum),
  * the stale-report family (`_report_not_merged_claims`),
  * `_fact_window_lines`,
  * the judge prompt.

Every one of those readers has to answer the same two questions — "which
section is this line in" and "did a TOOL say this, or did a WORKER say
this" — and every one of them answers it by pattern-matching text that a
worker can partly choose. PR #53 spent eight review rounds closing
"worker text impersonates a tool receipt" holes one reader at a time:
a body line reading `[current turn]`, a forged `END REPORT FROM` fence, a
blank `teammate_id` that rendered a label the matcher rejected, a
receipt-shaped `teammate_id` that put `MERGED PR #52` on the fence line
itself, a truncation that cut a fence in half.

The fix is not a ninth pattern. It is to parse ONCE, into pieces that
carry their own provenance, and let every reader consume pieces. A
worker cannot forge a piece boundary, because the boundary comes from the
transcript record the text was lifted out of, not from the text.

WHAT THIS MODULE IS
-------------------
Two constructors, one renderer, one trust rule, and the query helpers the
six readers will need.

  `from_transcript(...)`   Builds a `Window` straight from the transcript
                           records — the composer's own inputs. Provenance
                           is structural: a piece is a receipt because it
                           came out of a `tool_result` block, full stop.

  `from_text(window_text)` Parses an ALREADY-COMPOSED window using the
                           section headers and report labels the composer
                           emits. For replaying recorded benches, whose
                           evidence is on disk as flat text. Its trust
                           labels are INFERRED from bytes, so they are
                           only as good as the bytes: it honours a section
                           boundary only where the composer's own emit
                           order allows one, and refuses one entirely once
                           an unbounded report body has opened. It
                           therefore LOSES trust on windows carrying a
                           relayed report rather than risk inventing any —
                           see `from_text`, `Window.inferred` and
                           docs/window-model.md.

  `render()`               Emits the exact bytes the composer emits today,
                           byte-for-byte, so the two can be swapped under
                           a live gate without moving a single verdict.

  `is_trusted(origin)`     THE trust rule. One function, one line. A piece
                           is trusted iff its text came out of a tool
                           result record (or a session receipt, which is a
                           cache of an earlier turn's tool result). Never
                           from a teammate message, never from assistant
                           text, regardless of wording.

NOT WIRED
---------
This module deliberately does NOT change any existing reader: PR #53 is
open over the very functions a migration would touch. The only reader
here is the proof of concept `pr_state_signals_from_window` /
`pr_state_verdict_from_window`, which reproduces PR #53's arm from pieces
and is read-only. `docs/window-model.md` carries the per-reader migration
plan.

Zero dependencies (stdlib only). `from_text`, `render`, the query helpers
and the PR-state proof of concept need nothing but this file;
`from_transcript` imports `superjev` lazily, for its transcript readers.

READ-ONLY
---------
`from_transcript` never writes anything. In particular it does NOT call
`_record_receipts`: recording this turn's receipts is a side effect of the
composer's Stop-hook run, not of modelling a window, and a model you can
call twice without changing the world is the whole point.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import NamedTuple

__all__ = [
    "Piece", "Window", "Truncation", "PrStateSignal",
    "PIECE_KINDS", "PIECE_ORIGINS", "TRUSTED_ORIGINS", "is_trusted",
    "SECTION_CURRENT", "SECTION_RECEIPTS", "SECTION_REPORTS",
    "SECTION_CONTRIBUTED", "SECTION_PREV_REPORTS", "SECTION_UNKNOWN",
    "prev_section", "prev_index", "recency_rank",
    "header_for_section", "section_for_header", "emit_slot",
    "HEADER_CONTRIBUTED", "CONTRIBUTED_QUOTE_PREFIX",
    "neutralise_contributed_line",
    "from_transcript", "from_text",
    "pr_state_signals_from_window", "pr_state_verdict_from_window",
]


# ============================================================ vocabulary

#: What a piece can BE. `receipt` — one tool result carried into the
#: window. `claim` — one relayed worker/teammate report block. `fact` —
#: one session-receipt line (a one-line memory of a receipt-worthy fact).
#: `header` — a line of the composer's own structure (a section header,
#: the relayed-reports mark). `draft` — the reply under judgement; no
#: constructor emits one today, because the draft is not part of the
#: window, and the judge-prompt reader is handed it separately. The kind
#: is reserved here so that reader can join the same vocabulary when it
#: migrates rather than inventing a second one.
PIECE_KINDS = ("receipt", "claim", "draft", "fact", "header")

#: Where a piece's TEXT came from. This, and only this, decides trust.
PIECE_ORIGINS = (
    "tool_result",      # a tool_result block in the transcript
    "session_receipt",  # a session-receipt line: a cache of an earlier
                        # turn's tool result (only `_record_receipts`, fed
                        # from tool results, and the transcript backfill,
                        # which scans tool results, ever write one)
    "teammate",         # a <teammate-message> / <task-notification> block
    "assistant",        # assistant-authored text
    "composer",         # the composer's own structure lines
    "check_arm",        # a line a check arm CONTRIBUTED for this run (see
                        # SECTION_CONTRIBUTED). Arm-authored, not received
                        # from anywhere, so it is never trusted
    "truncated_mixed",  # a legacy byte tail-keep that fused tool-result
                        # text and report text into one blob; provenance
                        # is no longer separable, so it fails closed
)

#: THE trusted set. Kept as data next to `is_trusted` so a reviewer can
#: read the whole trust rule in one glance.
TRUSTED_ORIGINS = frozenset({"tool_result", "session_receipt"})


def is_trusted(origin):
    """THE trust rule, in one place.

    A piece is trusted iff its text came out of a tool result record — or
    out of a session receipt, which is a cache of an earlier turn's tool
    result and nothing else. Never from a teammate message, never from
    assistant text, never from composer structure, regardless of how the
    text is worded.

    Two consequences worth stating out loud, because they are the two
    halves of the boundary PR #53 kept re-litigating:

    * A teammate report that says `{"number": 52, "state": "MERGED"}`, or
      `MERGED`, or carries a `[from: gh pr merge 52 @ ...]` suffix, is
      still a claim. Quoting a receipt is not receiving one.
    * A tool result that PRINTS a report — `cat report.md`, a transcript
      dump, a grep hit — is still a receipt. The round-6 version of PR #53
      made a printed `REPORT FROM` line demote the rest of its own tool
      result to prose, which let a genuine `{"state": "OPEN"}` in that
      same output lose its strength. A tool result is a tool result.

    Composer structure (`kind == "header"`) is trusted AS STRUCTURE — that
    is what the kind means — but it is not trusted as evidence and carries
    no facts, so it is not in `TRUSTED_ORIGINS` and every
    `lines(trusted=True)` sweep skips it.
    """
    return origin in TRUSTED_ORIGINS


SECTION_CURRENT = "current"
SECTION_RECEIPTS = "receipts"
SECTION_REPORTS = "reports"
#: The block a check arm's `contribute` phase put in front of the judge
#: for THIS run (`arms.JudgeEvidence`). A first-class section, with its own
#: header and its own emit slot, for one reason: before it was one, its
#: header was not a header the reader recognised, so `_section_chunks`
#: folded the whole block into whatever chunk preceded it — and when that
#: chunk was `[session receipts]`, every contributed line re-parsed as a
#: TRUSTED session receipt. Arm-authored text laundered into a receipt by
#: nothing more than where it sat. Its pieces carry the `check_arm` origin,
#: so they are untrusted by the one trust rule rather than by a special
#: case, and its lines are prose-strength to every deterministic reader.
SECTION_CONTRIBUTED = "contributed"
#: Reports relayed in a PREVIOUS turn. Their own section since the window
#: budget change: they used to ride inside their turn's `prev(n)` block and
#: be dropped with it.
SECTION_PREV_REPORTS = "prev_reports"
#: Flat text with no recognisable composer header — a unit-test fixture, a
#: hand-written bench evidence file. No ordering information at all.
SECTION_UNKNOWN = "unknown"

_PREV_SECTION_RE = re.compile(r'^prev\((\d+)\)$')


def prev_section(n):
    """The section name for previous turn -`n` (1 = the turn just gone)."""
    return f"prev({int(n)})"


def prev_index(section):
    """`n` for a `prev(n)` section name, else None."""
    m = _PREV_SECTION_RE.match(section or "")
    return int(m.group(1)) if m else None


def recency_rank(section):
    """A coarse oldest-to-newest ordering of sections, or None when the
    section carries no ordering information at all.

    Mirrors `superjev._section_recency_rank` exactly: previous turns
    oldest-to-newest as -K grows smaller in magnitude (turn -1 is newer
    than turn -2), then reports relayed in those previous turns, then the
    session receipts, then this turn's relayed reports, then this turn's
    own tool results. `SECTION_UNKNOWN` returns
    None rather than a number — "unknown turn", NOT "oldest" — so two
    unlabelled mentions can never be ordered against each other.
    """
    n = prev_index(section)
    if n is not None:
        return -n
    # SECTION_CONTRIBUTED is deliberately absent, so it answers None like
    # SECTION_UNKNOWN. A contributed block is not a TURN — it is one run's
    # own working note — so it has no recency to compare against a receipt,
    # and None ("unknown turn", not "oldest") is what keeps a reader from
    # ordering it against one.
    return {SECTION_PREV_REPORTS: -0.5, SECTION_RECEIPTS: 0,
            SECTION_REPORTS: 1, SECTION_CURRENT: 2}.get(section)


#: The composer's own section headers, and the glue between the things it
#: joins. Every byte `render()` emits that is not piece text is one of
#: these, which is why `render()` can be byte-identical without carrying a
#: copy of the composer's string formatting.
HEADER_CURRENT = "[current turn]"
HEADER_RECEIPTS = "[session receipts]"
HEADER_REPORTS = "[current turn reports]"
HEADER_PREV = "[previous turn -{n}]"
#: The header the contributed-evidence block is written under. One
#: definition, here, and `arms.CONTRIBUTED_HEADER` is an alias of it: the
#: renderer that writes this line and the reader that recognises it must
#: never be able to disagree about its bytes, which is exactly how the
#: laundering hole opened.
HEADER_CONTRIBUTED = "[contributed by check arms]"
#: What every contributed line is prefixed with when it is written out.
#: A quoted line cannot be read back as a bare receipt row by a reader
#: that keys on the row's own shape, so neutralising at the renderer is a
#: second, independent guard behind the section boundary.
CONTRIBUTED_QUOTE_PREFIX = "> "
#: The header over reports relayed in a previous turn. Emitted only by a
#: composer that has `PREV_REPORTS_LABEL` (see `_prev_reports_section`);
#: parsed here unconditionally so a recorded window reads the same either
#: way.
HEADER_PREV_REPORTS = "[relayed reports in previous turns]"
#: PR #53 adds this mark inside a previous-turn block, ahead of that turn's
#: relayed reports. `main` does not emit it. Parsed here anyway so a
#: recorded window from either branch reads the same.
REPORTS_REGION_LABEL = "[relayed reports in this turn]"

SECTION_SEPARATOR = "\n\n===\n\n"   # between window sections
TURN_SEPARATOR = "\n\n"             # between previous-turn blocks
ITEM_SEPARATOR = "\n\n---\n\n"      # between items inside a turn
RECEIPT_SEPARATOR = "\n"            # between session-receipt lines
REPORT_SEPARATOR = "\n\n"           # between report blocks
HEADER_SEPARATOR = "\n"             # between a header and what it heads

#: The label the composer puts on every carried report. `who` is worker
#: text on `main` (PR #53 sanitises it); either way the label line itself
#: is parsed here by ONE regex, used by both constructors, so a `who` that
#: defeats the parse defeats it identically on both sides.
REPORT_LABEL = "REPORT FROM {who} (unverified worker claim)"
REPORT_END_LABEL = "END REPORT FROM {who} (unverified worker claim)"

#: Any line that merely BEGINS like a report label opens a claim. Fail
#: closed: a label the strict parse rejects is still a label.
_REPORT_OPEN_LOOSE_RE = re.compile(r'^REPORT FROM\b')
_REPORT_END_LOOSE_RE = re.compile(r'^END REPORT FROM\b')
_REPORT_WHO_RE = re.compile(r'^REPORT FROM (.*) \(unverified worker claim\)$')

_PREV_HEADER_RE = re.compile(r'^\[previous turn -(\d+)\]$')
_SECTION_SEPARATOR_LINE_RE = re.compile(r'^\s*(?:={3,}|-{3,})\s*$')

#: How a receipt or a carried tool result names the run that produced it.
#: Same shape `superjev._render_receipt_identity` writes and
#: `superjev._RECEIPT_IDENTITY_RE` reads back.
_IDENTITY_TAIL_RE = re.compile(r'\s*\[from:\s*(.*?)(?:\s+@\s+([^\]]*))?\]\s*$')
_IDENTITY_LINE_RE = re.compile(r'^\[from:\s*(.*?)(?:\s+@\s+([^\]]*))?\]\s*$')

#: The two markers the composer writes when a legacy byte tail-keep cut
#: into a block. Their presence is how `from_text` knows not to try to
#: split the remainder into items: the cut fused them.
PREV_CUT_MARKER = "[...older content in this turn dropped...]"
REPORT_CUT_MARKER = "[...head of this report dropped...]"


def header_for_section(section):
    """The composer's header line for `section`, or None for a section the
    composer writes no header for (`SECTION_UNKNOWN`)."""
    n = prev_index(section)
    if n is not None:
        return HEADER_PREV.format(n=n)
    return {SECTION_CURRENT: HEADER_CURRENT, SECTION_RECEIPTS: HEADER_RECEIPTS,
            SECTION_REPORTS: HEADER_REPORTS,
            SECTION_CONTRIBUTED: HEADER_CONTRIBUTED,
            SECTION_PREV_REPORTS: HEADER_PREV_REPORTS}.get(section)


def section_for_header(line):
    """The section a composer header line names, or None when `line` is
    not one of the composer's headers."""
    s = (line or "").strip()
    m = _PREV_HEADER_RE.match(s)
    if m:
        return prev_section(int(m.group(1)))
    return {HEADER_CURRENT: SECTION_CURRENT, HEADER_RECEIPTS: SECTION_RECEIPTS,
            HEADER_REPORTS: SECTION_REPORTS,
            HEADER_CONTRIBUTED: SECTION_CONTRIBUTED,
            HEADER_PREV_REPORTS: SECTION_PREV_REPORTS}.get(s)


def emit_slot(section):
    """Where in the assembled window the COMPOSER is able to emit
    `section`, as a sortable key, or None for a section it writes no
    header for (`SECTION_UNKNOWN`).

    The mirror of `superjev._section_emit_slot`, and deliberately NOT
    `recency_rank`. `from_transcript` joins its sections in exactly one
    order: the previous-turns block first (rendered NEWEST-first, so
    `[previous turn -1]`, then -2, then -3), then
    `[relayed reports in previous turns]`, then `[session receipts]`,
    then `[current turn reports]`, then `[current turn]`. Recency runs the
    other way along the previous turns — turn -1 is NEWER than turn -2 —
    so the two orders disagree on that run and must stay separate keys.

    `SECTION_CONTRIBUTED` sits LAST, above `[current turn]`, because that
    is where `arms.JudgeEvidence` writes it: after the whole window. Being
    the highest slot is what makes the block a boundary the reader will
    accept rather than a chunk folded into the section above it, and being
    AFTER the receipts is what stops it being read as part of them.

    A real window's headers therefore step STRICTLY upward in this key,
    each appearing at most once. A header that would step backward or
    repeat is one the composer could not have written there, which is what
    makes this the rule `from_text` uses to tell a section boundary from a
    worker who typed a section header.
    """
    n = prev_index(section)
    if n is not None:
        return (0, n)
    return {SECTION_PREV_REPORTS: (1, 0), SECTION_RECEIPTS: (2, 0),
            SECTION_REPORTS: (3, 0), SECTION_CURRENT: (4, 0),
            SECTION_CONTRIBUTED: (5, 0)}.get(section)


def _parse_identity(line):
    """The `cmd @ cwd` identity a `[from: ...]` line or suffix names, as
    ONE string, or None. Both constructors call this and nothing else, so
    a source string built from records and one parsed back out of rendered
    text cannot disagree."""
    if not line:
        return None
    m = _IDENTITY_LINE_RE.match(line.strip()) or _IDENTITY_TAIL_RE.search(line)
    if not m:
        return None
    cmd, cwd = (m.group(1) or "").strip(), (m.group(2) or "").strip()
    if cmd and cwd:
        return f"{cmd} @ {cwd}"
    return cmd or cwd or None


def neutralise_contributed_line(line):
    """One contributed line, in the form it is safe to WRITE into a window.

    Two changes, both about what the line can be read back as:

    * any `[from: cmd @ cwd]` identity tail is STRIPPED. That tail is how
      a receipt names the run that produced it, and a check arm's own line
      never came out of a run. Left on, a forged tail let a contributed
      line re-parse as an identified receipt.
    * the line is prefixed with `CONTRIBUTED_QUOTE_PREFIX`, so a reader
      keying on a row's own shape (a `LABEL: VALUE` row, a `gh pr merge N`
      receipt) sees a quotation rather than the row.

    Belt and braces behind `SECTION_CONTRIBUTED`: the section boundary is
    what makes the block untrusted, and this is what keeps an individual
    line from passing for something else even if a future reader forgets
    to ask which section it sits in. Idempotent, so rendering a window
    parsed out of already-neutralised bytes does not double the prefix.
    """
    text = (line or "").rstrip("\n")
    text = _IDENTITY_TAIL_RE.sub("", text)
    if text.startswith(CONTRIBUTED_QUOTE_PREFIX):
        return text
    return CONTRIBUTED_QUOTE_PREFIX + text


def _parse_report_who(first_line):
    """The `who` a report label line names, or None when the line only
    LOOSELY looks like a label (an embedded newline in `who` split the
    label across two physical lines; a blank `who` rendered two spaces).
    None means "a claim by someone we cannot name" — never "not a
    claim"."""
    m = _REPORT_WHO_RE.match((first_line or "").rstrip())
    if not m:
        return None
    return m.group(1) or None


def _opens_claim(line):
    """True when `line` opens a relayed report block."""
    return bool(_REPORT_OPEN_LOOSE_RE.match((line or "").strip()))


def _opens_worker_text(line):
    """True when `line` hands the rest of its block to worker text — a
    report label, loose or strict, or the composer's relayed-reports mark.

    `from_text` never returns to receipt territory inside a block once one
    of these has appeared (`_parse_items` re-joins the remainder), so for
    that parser "a report has opened" and "a report is still open" are the
    same question, `END REPORT FROM` fence or no fence.
    """
    s = (line or "").strip()
    return bool(_REPORT_OPEN_LOOSE_RE.match(s)) or s == REPORTS_REGION_LABEL


def _fuses_provenance(line):
    """True when `line` is one of the markers a legacy byte tail-keep
    leaves behind.

    Past one of these the window is FUSED: the head of the cut item is
    gone, so which bytes came out of which record is no longer
    recoverable, and no line after it can be honoured as composer
    structure. A body whose opening was cut away cannot be bounded by
    anything downstream, which is exactly why PR #53's
    `_repair_report_tail` re-opens one at a cut.
    """
    return (line or "").strip() in (PREV_CUT_MARKER, REPORT_CUT_MARKER)


def _unbounded(line):
    """True when `line` means nothing after it in this block can be
    trusted as composer structure — worker text has opened, or a byte cut
    has fused two records' bytes together."""
    return _fuses_provenance(line) or _opens_worker_text(line)


def _unbounded_in(chunk):
    """True when any line of `chunk` is `_unbounded`."""
    return any(_unbounded(ln) for ln in chunk.split("\n"))


# ============================================================ dataclasses

@dataclass(frozen=True)
class Piece:
    """One labelled piece of the window, with its own provenance.

    kind       one of `PIECE_KINDS`.
    section    `SECTION_CURRENT`, `prev(n)`, `SECTION_RECEIPTS`,
               `SECTION_REPORTS`, or `SECTION_UNKNOWN`.
    turn_rank  `recency_rank(section)` — None for `SECTION_UNKNOWN`, which
               means "no ordering information", not "oldest".
    source     the `cmd @ cwd` identity of the run that produced a receipt,
               the teammate/agent id that wrote a claim, or None when the
               window itself never said.
    text       the piece's own bytes, exactly as they appear in the
               rendered window, with no section header and no glue.
    line_span  (first, last) 1-based line numbers of this piece in the
               rendered window, or None before a layout has been computed.
    trusted    `is_trusted(origin)`. Never set by hand — see `_piece`.
    origin     one of `PIECE_ORIGINS`. The ONE input to the trust rule.
    cut        "", "head" or "tail": whether a legacy byte truncation took
               bytes off this piece. Not compared; it is a note about how
               the piece came to be this size, not part of its identity.
    """
    kind: str
    section: str
    turn_rank: int | None
    source: str | None
    text: str
    line_span: tuple | None = None
    trusted: bool = False
    origin: str = "composer"
    cut: str = field(default="", compare=False)

    @property
    def nbytes(self):
        return len(self.text.encode("utf-8"))

    def content_lines(self):
        """(offset, raw_line) for every non-blank, non-separator line of
        this piece — offset is 0-based within the piece. Blank lines and
        the window's own `---`/`===` separator lines are consumed, the
        same lines `superjev._iter_window_report_lines` never yields."""
        for i, raw in enumerate(self.text.splitlines()):
            s = raw.strip()
            if not s or _SECTION_SEPARATOR_LINE_RE.match(s):
                continue
            yield i, raw


def _piece(kind, section, text, origin, source=None, cut=""):
    """The ONLY way a Piece is built. Trust is computed here from `origin`
    and can therefore never be passed in by a caller who thinks it knows
    better."""
    if kind not in PIECE_KINDS:
        raise ValueError(f"unknown piece kind: {kind!r}")
    if origin not in PIECE_ORIGINS:
        raise ValueError(f"unknown piece origin: {origin!r}")
    return Piece(kind=kind, section=section, turn_rank=recency_rank(section),
                 source=source, text=text, line_span=None,
                 trusted=is_trusted(origin), origin=origin, cut=cut)


@dataclass
class Truncation:
    """What the window had to leave out to fit its cap.

    The first five fields record the composer's LEGACY policy, which cuts
    bytes and can cut inside a piece. `claims_dropped`/`receipts_dropped`
    record this module's own piece-preserving policy (`Window.fit`).
    """
    prev_turns_dropped: int = 0
    prev_turn_truncated: tuple | None = None
    current_items_dropped: int = 0
    reports_dropped: int = 0
    reports_bytes_cut: int = 0
    claims_dropped: int = 0
    receipts_dropped: int = 0
    #: The composer's last-resort whole-window byte tail-keep. The one
    #: place a rendered window is NOT the concatenation of its pieces.
    legacy_tail_cut: bool = False
    legacy_tail_bytes_cut: int = 0


class WindowLine(NamedTuple):
    """One content line of the window, carrying its piece's labels — the
    shape every migrated reader iterates instead of splitting text."""
    pos: int            # 1-based line number in the rendered window
    raw: str
    stripped: str
    section: str
    turn_rank: int | None
    kind: str
    trusted: bool
    source: str | None
    piece: Piece


@dataclass
class Window:
    """A composed evidence window as an ordered tuple of labelled pieces.

    `pieces` is compared; `byte_cap` is compared; `truncated`, `meta` and
    `inferred` are NOT — they describe how this window came to be, not
    what it is. That is what makes the round-trip property
    `from_text(render(w), byte_cap=w.byte_cap) == w` meaningful: it
    compares the CONTENT and its labels, which is all a reader reads.
    """
    pieces: tuple
    byte_cap: int
    truncated: Truncation = field(default_factory=Truncation, compare=False)
    #: True when the trust labels were INFERRED from composed bytes
    #: (`from_text`) rather than read off transcript records
    #: (`from_transcript`). A reader that cares about the difference — and
    #: a live gate should — can branch on it.
    inferred: bool = field(default=False, compare=False)
    #: The composer's own `meta` dict for this window, for a caller
    #: migrating off `derive_evidence_window(..., return_meta=True)`.
    meta: dict = field(default_factory=dict, compare=False)

    # ---------------------------------------------------------- rendering

    def render(self):
        """The exact bytes the composer emits for this window.

        Pure glue: every byte that is not piece text comes from the
        composer's grammar (a header, a separator), which is entirely
        determined by the pieces' sections and kinds. The one exception is
        the composer's last-resort whole-window byte tail-keep, which cuts
        below the piece level; when `truncated.legacy_tail_cut` is set this
        applies it, and the result is NOT the concatenation of the pieces.
        """
        out = []
        prev = None
        for p in self.pieces:
            out.append(self._glue(prev, p))
            out.append(p.text)
            prev = p
        text = "".join(out)
        if self.truncated.legacy_tail_cut and self.byte_cap:
            raw = text.encode("utf-8")
            if len(raw) > self.byte_cap:
                text = raw[-self.byte_cap:].decode("utf-8", errors="ignore")
        return text

    @staticmethod
    def _glue(prev, nxt):
        """The bytes the composer puts between two adjacent pieces."""
        if prev is None:
            return ""
        if prev.section == nxt.section:
            if prev.kind == "header":
                return HEADER_SEPARATOR
            if nxt.kind == "header":          # the relayed-reports mark
                return ITEM_SEPARATOR
            if nxt.section in (SECTION_RECEIPTS, SECTION_CONTRIBUTED):
                return RECEIPT_SEPARATOR
            if nxt.section in (SECTION_REPORTS, SECTION_PREV_REPORTS):
                return REPORT_SEPARATOR
            return ITEM_SEPARATOR
        if prev_index(prev.section) is not None and prev_index(nxt.section) is not None:
            return TURN_SEPARATOR
        return SECTION_SEPARATOR

    def nbytes(self):
        return len(self.render().encode("utf-8"))

    def with_spans(self):
        """This window with every piece's `line_span` filled in from the
        rendered layout. Both constructors end with this, so a span is
        never stale. A window that took the legacy whole-window tail cut
        gets None spans: the cut moved every line."""
        if self.truncated.legacy_tail_cut:
            return replace(self, pieces=tuple(
                replace(p, line_span=None) for p in self.pieces))
        pieces = []
        line = 1
        prev = None
        for p in self.pieces:
            glue = self._glue(prev, p)
            line += glue.count("\n")
            n_lines = p.text.count("\n") + 1
            pieces.append(replace(p, line_span=(line, line + n_lines - 1)))
            line += n_lines - 1
            prev = p
        return replace(self, pieces=tuple(pieces))

    # ----------------------------------------------------------- queries

    def sections(self):
        """Every section present, in emit order, deduplicated."""
        out = []
        for p in self.pieces:
            if p.section not in out:
                out.append(p.section)
        return tuple(out)

    def pieces_in(self, section):
        return tuple(p for p in self.pieces if p.section == section)

    def by_kind(self, *kinds):
        return tuple(p for p in self.pieces if p.kind in kinds)

    def receipts(self):
        """Every trusted piece — tool results and session receipts."""
        return tuple(p for p in self.pieces if p.trusted and p.kind != "header")

    def claims(self):
        """Every relayed worker/teammate report block."""
        return self.by_kind("claim")

    def lines(self, kind=None, section=None, trusted=None):
        """Every content line of the window, in order, with its piece's
        labels. This is the iterator a migrated reader replaces its own
        text splitting with.

        `kind`, `section` and `trusted` filter; `kind` accepts a string or
        a collection. Header pieces are yielded only when `kind` asks for
        them by name, so `lines(trusted=True)` never hands a reader a
        section header to read a fact out of.
        """
        kinds = ((kind,) if isinstance(kind, str) else
                 tuple(kind) if kind is not None else None)
        for p in self.pieces:
            if kinds is None:
                if p.kind == "header":
                    continue
            elif p.kind not in kinds:
                continue
            if section is not None and p.section != section:
                continue
            if trusted is not None and p.trusted != trusted:
                continue
            base = p.line_span[0] if p.line_span else 1
            for off, raw in p.content_lines():
                yield WindowLine(base + off, raw, raw.strip(), p.section,
                                 p.turn_rank, p.kind, p.trusted, p.source, p)

    def newest(self, section=None):
        """The newest piece — highest `turn_rank`, then latest in emit
        order — optionally within one section. None when there is none.

        A piece whose `turn_rank` is None (`SECTION_UNKNOWN`) is never
        "newest": unknown is not a position.
        """
        cands = [p for p in self.pieces
                 if p.kind != "header" and p.turn_rank is not None
                 and (section is None or p.section == section)]
        if not cands:
            return None
        return max(enumerate(cands), key=lambda t: (t[1].turn_rank, t[0]))[1]

    def receipts_for(self, pr_number):
        """Every TRUSTED piece that names PR `pr_number`, newest first.

        The PR-state arm's raw material, with the trust question already
        settled: a report that quotes `MERGED PR #52` is not in here, and
        a tool result that prints one is.
        """
        pat = _pr_number_res(str(pr_number))
        hits = [p for p in self.receipts()
                if any(rx.search(p.text) for rx in pat)]
        return tuple(sorted(hits, key=lambda p: (
            -(p.turn_rank if p.turn_rank is not None else -10 ** 6),
            self.pieces.index(p))))

    def values_labelled(self, label):
        """Every `label: value` pairing the window states, as
        (value_text, WindowLine) — both `coverage: 91.2` prose and a
        `| coverage | 91.2 |` table row, in window order.

        Deliberately returns the LINE, not a bare number: a value with no
        provenance is exactly the thing the count arm kept getting wrong,
        and the caller needs to know whether a tool said it.
        """
        lab = re.escape((label or "").strip())
        if not lab:
            return ()
        colon = re.compile(r'(?<![\w/])' + lab + r'\s*[:=]\s*([-+]?\d+(?:\.\d+)?%?)',
                           re.IGNORECASE)
        row = re.compile(r'^\|\s*' + lab + r'\s*\|\s*([-+]?\d+(?:\.\d+)?%?)\s*\|',
                         re.IGNORECASE)
        out = []
        for ln in self.lines():
            m = row.match(ln.stripped) or colon.search(ln.raw)
            if m:
                out.append((m.group(1), ln))
        return tuple(out)

    # -------------------------------------------- piece-preserving fit

    def fit(self, byte_cap=None):
        """This window trimmed to `byte_cap` WITHOUT ever cutting inside a
        piece — the model's own truncation rule, and the one a migrated
        composer should use.

        Drop order, strictly: relayed claims first (oldest turn first,
        oldest within a turn first), then previous-turn receipts (oldest
        first), then session-receipt facts (oldest first). The current
        turn's own receipts are never dropped, exactly as today. A
        section's header goes when its last content piece goes.

        Claims before receipts, because a claim is a worker's paraphrase
        of something and a receipt is the thing itself: if the window has
        to forget something, forget the retelling.
        """
        cap = self.byte_cap if byte_cap is None else byte_cap
        trunc = replace(self.truncated, legacy_tail_cut=False,
                        legacy_tail_bytes_cut=0, claims_dropped=0,
                        receipts_dropped=0)
        keep = list(self.pieces)

        def order(p):
            i = keep.index(p)
            rank = p.turn_rank if p.turn_rank is not None else -10 ** 6
            return (rank, i)

        def shrink(candidates):
            nonlocal keep
            for p in sorted(candidates, key=order):
                if _render(keep, cap).encode("utf-8").__len__() <= cap:
                    return
                keep = [q for q in keep if q is not p]
                keep = _drop_orphan_headers(keep)
                yield p

        for _ in shrink([p for p in keep if p.kind == "claim"]):
            trunc.claims_dropped += 1
        for _ in shrink([p for p in keep
                         if p.kind == "receipt" and prev_index(p.section) is not None]):
            trunc.receipts_dropped += 1
        for _ in shrink([p for p in keep if p.kind == "fact"]):
            trunc.receipts_dropped += 1
        return Window(pieces=tuple(_drop_orphan_headers(keep)), byte_cap=cap,
                      truncated=trunc, inferred=self.inferred,
                      meta=dict(self.meta)).with_spans()


def _render(pieces, byte_cap):
    return Window(pieces=tuple(pieces), byte_cap=byte_cap).render()


def _drop_orphan_headers(pieces):
    """`pieces` with every header that no longer heads any content
    removed — a section with nothing in it is not a section."""
    out = []
    for i, p in enumerate(pieces):
        if p.kind != "header":
            out.append(p)
            continue
        if any(q.section == p.section and q.kind != "header"
               for q in pieces[i + 1:]):
            out.append(p)
    return out


def _pr_number_res(pr_str):
    """Every way the window names a PR number, as the PR-state arm reads
    them (`superjev._FACT_PR_NUM_RES`, pinned to one number)."""
    n = re.escape(pr_str)
    return (
        re.compile(r'\bgh\s+pr\s+merge\s+' + n + r'\b'),
        re.compile(r'\bgh\s+pr\s+(?:view|checks)\s+' + n + r'\b'),
        re.compile(r'\(#' + n + r'\)'),
        re.compile(r'#' + n + r'\b'),
        re.compile(r'"number"\s*:\s*' + n + r'\b'),
    )


# =========================================================== constructors

def from_transcript(transcript_path, n=None, max_bytes=None, session_id=None,
                    prev_turns=None, cap_bytes=None, records=None,
                    policy="legacy", draft_text=None):
    """A `Window` built straight from the transcript records — the same
    inputs, the same layers and the same budget arithmetic
    `superjev._derive_evidence_text_from_transcript` uses, with every
    piece carrying the provenance of the RECORD it came out of.

    Same arguments as the composer, plus:

      `records`  pre-read transcript records, for a caller that already
                 has them (and for a fuzz harness that would rather not
                 touch a filesystem 100,000 times).
      `draft_text`  the draft this window is being built for, used only
                 to decide which session receipts to keep first when the
                 receipts layer is over its share — the composer's own
                 `draft_text` argument, same meaning.
      `policy`   "legacy" (default) reproduces the composer's byte
                 truncation exactly, so `render()` is byte-identical;
                 "pieces" applies `Window.fit`'s piece-preserving rule
                 instead. Legacy exists only so the two can be swapped
                 under a live gate without moving a verdict; "pieces" is
                 what a migrated composer should use.

    Returns a `Window` whose `pieces` may be empty, which is the model's
    way of saying what the composer says with `None`.

    Read-only: this never records receipts and never writes a file.
    """
    sj = _superjev()
    n = n if n is not None else sj._hook_evidence_n()
    max_bytes = max_bytes if max_bytes is not None else sj._hook_evidence_max_bytes()
    prev_turns = prev_turns if prev_turns is not None else sj._hook_prev_turns()
    cap_bytes = cap_bytes if cap_bytes is not None else sj._hook_evidence_cap_bytes()
    effective_cap = min(cap_bytes, max_bytes)

    if records is None:
        records = sj._read_transcript_records(transcript_path)
    start = sj._current_turn_start_index(records)
    scoped = records[start:] if start is not None else records

    cur_receipts = _receipt_pieces(sj, scoped, SECTION_CURRENT)
    cur_claims = _claim_pieces(sj, scoped, SECTION_REPORTS)

    prev_spans = (sj._previous_turn_spans(records, start, prev_turns)
                  if start is not None else [])
    # A composer with PREV_REPORTS_LABEL lifts a previous turn's relayed
    # reports OUT of that turn's block into one section of their own; an
    # older one leaves them inside it, behind the region mark. Detected by
    # FEATURE, like every other branch difference in this module, so the
    # bytes follow the composer either way.
    split_prev_reports = hasattr(sj, "PREV_REPORTS_LABEL")
    prev_turn_pieces = []
    prev_claim_pieces = []
    for i, (a, b, _texts) in enumerate(prev_spans, start=1):
        sec = prev_section(i)
        claims = _claim_pieces(sj, records[a:b], sec)
        if split_prev_reports:
            prev_turn_pieces.append(_receipt_pieces(sj, records[a:b], sec))
        else:
            prev_turn_pieces.append(_receipt_pieces(sj, records[a:b], sec)
                                    + _reports_region_mark(sj, sec, claims)
                                    + claims)
    if split_prev_reports:
        # Newest-first across turns, re-laid oldest-first, exactly as the
        # composer flattens them (turn -1's reports are fresher than turn
        # -2's, and within a turn file order is already oldest-first).
        for i in range(len(prev_spans), 0, -1):
            a, b, _t = prev_spans[i - 1]
            prev_claim_pieces.extend(
                _claim_pieces(sj, records[a:b], SECTION_PREV_REPORTS))

    fact_pieces, fact_stats = _fact_pieces(sj, records, start, session_id)

    meta = _compose_meta(sj, records, start, cur_receipts, cur_claims,
                         prev_spans, prev_turn_pieces, fact_pieces,
                         fact_stats, effective_cap,
                         prev_claim_pieces=(prev_claim_pieces
                                            if split_prev_reports else None),
                         prev_claims_per_turn=[
                             _claim_pieces(sj, records[a:b], prev_section(i))
                             for i, (a, b, _t) in enumerate(prev_spans, start=1)]
                         if split_prev_reports else None)

    trunc = Truncation()

    # --- layer 4: the current turn's own tool results. Highest priority,
    # never dropped; only the composer's `n`-call slice applies.
    kept_cur = cur_receipts[-n:] if n is not None and n >= 0 else list(cur_receipts)
    trunc.current_items_dropped = len(cur_receipts) - len(kept_cur)
    cur_block = ([_header_piece(SECTION_CURRENT)] + kept_cur) if kept_cur else []
    meta["current_bytes"] = _section_bytes(cur_block)

    # --- layer 2: the session receipts. Their block is built LAST now
    # (the share is a reservation, not a ceiling — see the composer), so
    # only the full size is known here.
    fact_block_full = ([_header_piece(SECTION_RECEIPTS)] + fact_pieces) if fact_pieces else []
    fact_block = fact_block_full

    overhead = 32   # the composer's own allowance for section separators
    if split_prev_reports:
        receipts_reserve = min(
            max(int(effective_cap * sj.RECEIPTS_BUDGET_SHARE), 0),
            _section_bytes(fact_block_full))
        meta["receipts_share_bytes"] = receipts_reserve
        prev_reports_reserve = min(
            max(int(effective_cap * sj.PREV_REPORTS_BUDGET_SHARE), 0),
            _section_bytes([_header_piece(SECTION_PREV_REPORTS)] + prev_claim_pieces)
            if prev_claim_pieces else 0)
        remaining = effective_cap - meta["current_bytes"] - overhead
    else:
        receipts_reserve = 0
        prev_reports_reserve = 0
        meta["receipts_bytes"] = _section_bytes(fact_block)
        remaining = (effective_cap - meta["current_bytes"]
                     - meta["receipts_bytes"] - overhead)

    # --- layer 3: this turn's relayed reports, at current-turn priority
    # but capped at REPORTS_BUDGET_SHARE of what is left.
    report_block = []
    if cur_claims and remaining > 0:
        budget = max(int(max(remaining - receipts_reserve, 0)
                         * sj.REPORTS_BUDGET_SHARE), 0)
        report_block, kept, cut = _fit_reports(cur_claims, budget)
        meta["reports_kept"] = kept
        meta["reports_cut_bytes"] = cut
        meta["reports_bytes"] = _section_bytes(report_block)
        trunc.reports_dropped = len(cur_claims) - kept
        trunc.reports_bytes_cut = cut
        remaining -= meta["reports_bytes"] + 8
    elif cur_claims:
        cut = sum(p.nbytes for p in cur_claims)
        meta["reports_cut_bytes"] = cut
        trunc.reports_dropped = len(cur_claims)
        trunc.reports_bytes_cut = cut

    # --- layer 1: the previous turns, oldest dropped first, after the
    # two reservations below them are set aside.
    remaining_for_prev = max(remaining - receipts_reserve - prev_reports_reserve, 0)
    prev_block = []
    if prev_turn_pieces and remaining_for_prev > 0:
        prev_block, dropped, kept, truncated = _fit_prev_turns(
            prev_turn_pieces, remaining_for_prev)
        meta["prev_dropped"] = dropped
        meta["prev_truncated"] = truncated
        trunc.prev_turns_dropped = dropped
        trunc.prev_turn_truncated = truncated
        for d in meta["prev_turn_detail"]:
            d["kept"] = d["turn"] <= kept
        meta["prev_bytes"] = _section_bytes(prev_block)
    elif prev_turn_pieces:
        meta["prev_dropped"] = len(prev_turn_pieces)
        trunc.prev_turns_dropped = len(prev_turn_pieces)
        for d in meta["prev_turn_detail"]:
            d["kept"] = False
    if prev_block:
        remaining -= meta["prev_bytes"] + 8

    # --- layer 1b: reports relayed in a previous turn, reserved share
    # then whatever the previous-turn block left unspent.
    prev_report_block = []
    if prev_claim_pieces and remaining > 0:
        budget = max(remaining - receipts_reserve, prev_reports_reserve)
        prev_report_block, kept_prev, cut_prev = _fit_reports(
            prev_claim_pieces, budget, section=SECTION_PREV_REPORTS)
        meta["reports_prev_kept"] = kept_prev
        meta["reports_prev_cut_bytes"] = cut_prev
        meta["reports_prev_bytes"] = _section_bytes(prev_report_block)
        if prev_report_block:
            remaining -= meta["reports_prev_bytes"] + 8
    elif prev_claim_pieces:
        meta["reports_prev_cut_bytes"] = sum(p.nbytes for p in prev_claim_pieces)
    if split_prev_reports:
        meta["reports_kept"] += meta["reports_prev_kept"]
        meta["reports_cut_bytes"] += meta["reports_prev_cut_bytes"]

        # --- layer 2, built last: the receipts get everything above them
        # left unspent, never less than their reservation.
        if fact_pieces:
            fact_block, kept_receipts, dropped_receipts = _fit_receipts(
                fact_pieces, max(remaining, receipts_reserve),
                getattr(sj, "_receipts_relevant_to_draft", None), draft_text)
            meta["receipts_count"] = kept_receipts
            meta["receipts_dropped"] = dropped_receipts
            meta["receipts_bytes"] = _section_bytes(fact_block)

    pieces = tuple(prev_block + prev_report_block + fact_block
                   + report_block + cur_block)
    win = Window(pieces=pieces, byte_cap=effective_cap, truncated=trunc, meta=meta)

    if policy == "pieces":
        win = win.fit(effective_cap)
        meta["total_bytes"] = win.nbytes()
        return win
    if policy != "legacy":
        raise ValueError(f"unknown truncation policy: {policy!r}")

    joined = win.render()
    if len(joined.encode("utf-8")) > effective_cap:
        # The composer's last resort: the current turn alone is bigger
        # than the cap, so keep the tail. This is the ONE cut that goes
        # below the piece level — see docs/window-model.md.
        trunc.legacy_tail_cut = True
        trunc.legacy_tail_bytes_cut = (len(joined.encode("utf-8")) - effective_cap)
        win = replace(win, truncated=trunc)
    meta["total_bytes"] = len(win.render().encode("utf-8"))
    return win.with_spans()


def from_text(window_text, byte_cap=None):
    """A `Window` parsed out of an ALREADY-COMPOSED window, using the
    section headers and report labels the composer emits — for replaying a
    recorded bench, whose evidence sits on disk as flat text.

    Sets `inferred=True`, and it matters. `from_transcript` knows a piece
    is a receipt because it came out of a `tool_result` record;
    `from_text` can only know it because of where the bytes sit, and the
    bytes are partly worker-chosen.

    WHAT THIS GUARANTEES
    --------------------
    Exactly one thing, and it is the thing a reader needs: **no byte that
    the composer copied out of a worker's report can end up inside a
    TRUSTED piece.** Every way a report body can dress itself up as
    structure loses trust rather than gaining it:

    * a line that merely BEGINS `REPORT FROM` opens a claim — a label the
      strict parse rejects is still a label;
    * every item after the first report label in a block is a claim too,
      because the composer emits a turn's tool results first and its
      reports last, so an item separator inside a body cannot manufacture
      a receipt out of its tail;
    * a section header is a boundary only where the composer could have
      written one: its `emit_slot` must step STRICTLY upward from the
      highest slot accepted so far, so `[session receipts]` after
      `[current turn reports]`, or a repeat of a header already seen, is
      prose wearing a header's clothes;
    * and a `\n\n===\n\n` run after a report label is not a boundary at
      all. It stays inside the report.

    That last rule is the expensive one, and it is not a choice. This
    checkout's composer copies a worker's report body in VERBATIM
    (`superjev._collect_report_blocks`): no fence closes it and no
    structure line is quoted out of it. So a `===` line standing between
    a report body and a `[current turn]` header may be the composer's own
    section separator or three characters the worker typed, and the bytes
    do not say which. Reading it as a separator is how a report body
    reading `blank / === / blank / [session receipts] / MERGED PR #52
    [from: gh pr merge 52 @ ...]` used to re-parse into a TRUSTED session
    receipt and turn a PR-mismatch block into an allow. Fail closed, always
    — there is no flag that turns this off. A composer that closes its
    report bodies with a fence and quotes structure out of them (the way
    PR #53's `_render_report_block` does) would let a reader trust a
    `===` outside a body again, but `from_text` does not take that
    composer's word for it: it has no way to tell, from the bytes alone,
    whether the composer that wrote them actually fences. Trusting an
    unverified claim about the composer is exactly the hole this function
    exists to close, so the fail-closed parse is the only parse.

    WHAT IT COSTS
    -------------
    On a window that carries no relayed report, nothing: the parse is
    exact and `from_text(render(w), byte_cap=w.byte_cap) == w`. On a
    window that does carry one, every section AFTER the first report is
    folded into that report's claim, so its receipts re-parse as worker
    text — trust lost, never gained. `docs/window-model.md` records this
    gap, and `tests/replay_window_model.py` measures it on every run.

    What no amount of care here repairs: a genuine tool result whose own
    output happens to contain `\n\n---\n\n` splits into two receipt
    pieces, and one that prints a `REPORT FROM` line demotes itself and
    its neighbours to claims. Both are safe directions, both are invisible
    in flat text, and both are why `from_transcript` is the constructor a
    live gate should use.

    `byte_cap` cannot be recovered from the bytes; it defaults to the
    text's own size.
    """
    text = window_text or ""
    cap = byte_cap if byte_cap is not None else len(text.encode("utf-8"))
    pieces = []
    for raw_section in _section_chunks(text):
        pieces.extend(_parse_section(raw_section))
    win = Window(pieces=tuple(pieces), byte_cap=cap, inferred=True)
    return win.with_spans()


# ------------------------------------------------------- from_text guts

def _section_chunks(text):
    """`text` split into the window sections the COMPOSER could have
    written, as raw chunk strings — the split `from_text` uses instead of
    a bare `text.split(SECTION_SEPARATOR)`.

    A `\n\n===\n\n` run opens a new section only when both of the
    composer's own rules allow it:

    1. the chunk after it must start with a composer section header whose
       `emit_slot` steps STRICTLY upward from the highest slot accepted so
       far (the composer emits its sections in one fixed order, each at
       most once), and
    2. no report label may have appeared yet. An unfenced report body is
       unbounded — see `from_text` — so a `===` after one is not provably
       the composer's.

    A `===` that fails either rule stays in the chunk it fell in, which is
    what carrying the "a report has opened" state ACROSS the split means:
    the report body keeps its own bytes instead of handing its tail to a
    fresh, trusted section.

    Header-less chunks are still split, but only while the window has
    shown no accepted header at all — that is a hand-written bench
    evidence file or a unit-test fixture, with no composer structure to
    respect. Once one section header has been accepted the window is
    structured, and an unheaded chunk after a `===` is not something the
    composer emits.
    """
    raw = (text or "").split(SECTION_SEPARATOR)
    chunks = [raw[0]]
    slot = _chunk_accepted_slot(raw[0])
    worker = _unbounded_in(raw[0])
    for nxt in raw[1:]:
        sec = section_for_header(nxt.split("\n", 1)[0])
        new = emit_slot(sec) if sec is not None else None
        if new is not None:
            accept = (slot is None or new > slot) and not worker
        else:
            accept = slot is None and not worker
        if accept:
            chunks.append(nxt)
            if new is not None:
                slot = _chunk_accepted_slot(nxt)
            worker = _unbounded_in(nxt)
        else:
            chunks[-1] = chunks[-1] + SECTION_SEPARATOR + nxt
            worker = worker or _unbounded_in(nxt)
    return chunks


def _chunk_accepted_slot(chunk):
    """The highest `emit_slot` any header ACCEPTED inside `chunk` carries,
    or None when the chunk has no composer header.

    Only the previous-turns section holds more than one header, so this is
    the chunk's own leading header everywhere else. It matters there
    because a forged `[previous turn -2]` outside the block could
    otherwise step upward past a real `[previous turn -1]` and open a
    trusted turn of its own.
    """
    sec = section_for_header((chunk or "").split("\n", 1)[0])
    if sec is None:
        return None
    if prev_index(sec) is None:
        return emit_slot(sec)
    _idx, slot = _prev_header_indices(chunk.split("\n"))
    return slot


def _prev_header_indices(lines):
    """(indices, highest_slot) — the `[previous turn -K]` header lines of a
    previous-turns block that the COMPOSER could have written, by the same
    two rules `_section_chunks` applies to a `===`: strictly upward in
    `emit_slot`, and not after a report label has opened an unbounded
    body.

    Previous-turn blocks are joined by a blank line, not by `===`, so this
    is the second place a worker's report body can try to invent a section
    — and the second place it must not be able to.
    """
    out, slot, worker = [], None, False
    for i, line in enumerate(lines):
        sec = section_for_header(line)
        if sec is not None and prev_index(sec) is not None:
            new = emit_slot(sec)
            if not worker and (slot is None or new > slot):
                out.append(i)
                slot = new
                continue
        if _unbounded(line):
            worker = True
    return out, slot


def _parse_section(raw_section):
    """Pieces for one accepted window section (see `_section_chunks`)."""
    if not raw_section:
        return []
    lines = raw_section.split("\n")
    first = section_for_header(lines[0])
    if first is not None and prev_index(first) is not None:
        return _parse_prev_block(raw_section)
    if first == SECTION_CURRENT:
        return ([_header_piece(SECTION_CURRENT)]
                + _parse_items("\n".join(lines[1:]), SECTION_CURRENT))
    if first == SECTION_RECEIPTS:
        return [_header_piece(SECTION_RECEIPTS)] + [
            _piece("fact", SECTION_RECEIPTS, ln, "session_receipt",
                   source=_parse_identity(ln))
            for ln in lines[1:]]
    if first in (SECTION_REPORTS, SECTION_PREV_REPORTS):
        return ([_header_piece(first)]
                + _parse_claims("\n".join(lines[1:]), first))
    if first == SECTION_CONTRIBUTED:
        # One piece per line, `check_arm` origin, so `is_trusted` says no
        # for the ordinary reason rather than by a special case here. Kind
        # is `claim`: a check arm's contributed line is an assertion about
        # this run, never a receipt of one. No item splitting — the block
        # is line-oriented (`arms.JudgeEvidence` joins with a newline), and
        # splitting it on `---` would only invent structure a contributor
        # could then forge.
        return [_header_piece(SECTION_CONTRIBUTED)] + [
            _piece("claim", SECTION_CONTRIBUTED, ln, "check_arm")
            for ln in lines[1:]]
    # No composer header at all: flat text. One unknown section, parsed
    # with the same fail-closed item rules and no ordering information.
    return _parse_items(raw_section, SECTION_UNKNOWN)


def _parse_prev_block(raw_section):
    """Pieces for the previous-turns section, which holds one
    `[previous turn -K]` block per turn joined by a blank line. Only the
    header lines `_prev_header_indices` accepts start a block; the rest
    are content of whatever block they fell in."""
    lines = raw_section.split("\n")
    idx, _slot = _prev_header_indices(lines)
    if not idx:
        return _parse_items(raw_section, SECTION_UNKNOWN)
    out = []
    if idx[0] != 0:
        out.extend(_parse_items("\n".join(lines[:idx[0]]), SECTION_UNKNOWN))
    bounds = idx + [len(lines)]
    for a, b in zip(bounds, bounds[1:]):
        sec = section_for_header(lines[a])
        buf = lines[a + 1:b]
        if b < len(lines):
            # strip the blank line that joined this turn block to the next
            while buf and not buf[-1].strip():
                buf.pop()
        body = "\n".join(buf)
        if body.startswith("\n"):
            body = body[1:]
        out.append(_header_piece(sec))
        out.extend(_parse_items(body, sec))
    return out


def _parse_items(body, section):
    """Items of one turn (or of flat text): the composer's tool results
    first, then any relayed reports. Fails closed at the first LINE that
    opens worker text, wherever in the block it sits.

    Not just the first line of each `---`-separated item. A `===` run that
    `_section_chunks` refused to honour leaves the rejected chunk inside
    this block, so a report label can now turn up in the middle of an
    item, and a head-only check let the label and everything under it be
    read as one trusted tool result. Everything from that line on is
    worker text; everything above it is not.
    """
    if body == "":
        return []
    first_line = body.split("\n", 1)[0].strip()
    if first_line == PREV_CUT_MARKER:
        # A legacy tail-keep fused this turn's items into one blob.
        # Provenance is no longer separable, so it is not trusted.
        return [_piece("receipt", section, body, "truncated_mixed", cut="head")]
    if first_line == REPORT_CUT_MARKER:
        return [_piece("claim", section, body, "teammate", cut="head")]

    raw_items = body.split(ITEM_SEPARATOR)
    out = []
    for i, item in enumerate(raw_items):
        item_lines = item.split("\n")
        fused = next((j for j, ln in enumerate(item_lines)
                      if _fuses_provenance(ln)), None)
        at = next((j for j, ln in enumerate(item_lines)
                   if _opens_worker_text(ln)), None)
        if fused is not None and (at is None or fused < at):
            # A byte cut fused the rest of this block. Whatever the
            # remaining bytes say, their provenance is gone.
            if fused:
                lead = "\n".join(_strip_glue_tail(item_lines[:fused]))
                if lead.strip():
                    out.append(_piece("receipt", section, lead, "tool_result",
                                      source=_parse_identity(item_lines[0])))
            rest = ITEM_SEPARATOR.join(
                ["\n".join(item_lines[fused:])] + raw_items[i + 1:])
            out.append(_piece("receipt", section, rest, "truncated_mixed",
                              cut="head"))
            return out
        if at is not None:
            # From here on everything in this block is worker text. Re-join
            # the rest so an item separator inside a report body cannot
            # manufacture a receipt out of its tail.
            if at:
                lead = "\n".join(_strip_glue_tail(item_lines[:at]))
                if lead.strip():
                    out.append(_piece("receipt", section, lead, "tool_result",
                                      source=_parse_identity(item_lines[0])))
            rest = ITEM_SEPARATOR.join(
                ["\n".join(item_lines[at:])] + raw_items[i + 1:])
            if rest.split("\n", 1)[0].strip() == REPORTS_REGION_LABEL:
                out.append(_header_piece(section, REPORTS_REGION_LABEL))
                rest = rest.split("\n", 1)[1] if "\n" in rest else ""
            out.extend(_parse_claims(rest, section))
            return out
        out.append(_piece("receipt", section, item, "tool_result",
                          source=_parse_identity(item_lines[0])))
    return out


def _strip_glue_tail(chunk):
    """`chunk` with the trailing blank and `---`/`===` separator lines
    removed — those bytes are the composer's GLUE between two items, not
    part of the item, and leaving them on the piece is how a re-parsed
    window stopped matching a record-built one."""
    out = list(chunk)
    while out and (not out[-1].strip()
                   or _SECTION_SEPARATOR_LINE_RE.match(out[-1].strip())):
        out.pop()
    return out


def _parse_claims(body, section):
    """One claim piece per relayed report in `body`.

    Split on the LABEL lines, never on a blank-line-joined paragraph: a
    report body is full of blank lines, and `\\n\\n`-splitting it was how
    a body's own paragraph break used to become a piece boundary. A label
    line inside a body can still split one claim into two, which costs a
    boundary but never costs trust — both halves are claims.
    """
    if body == "":
        return []
    lines = body.split("\n")
    if lines and lines[0].strip() == REPORT_CUT_MARKER:
        return [_piece("claim", section, body, "teammate", cut="head")]
    starts = [i for i, ln in enumerate(lines)
              if _opens_claim(ln) and (i == 0 or not lines[i - 1].strip())]
    if not starts:
        return [_piece("claim", section, body, "teammate",
                       source=_parse_report_who(lines[0]))]
    out = []
    if starts[0] != 0:
        # Text ahead of the first label: worker text all the same.
        lead = "\n".join(_strip_glue_tail(lines[:starts[0]]))
        if lead.strip():
            out.append(_piece("claim", section, lead, "teammate"))
    bounds = starts + [len(lines)]
    for a, b in zip(bounds, bounds[1:]):
        chunk = lines[a:b]
        chunk = _strip_glue_tail(chunk)
        if not chunk:
            continue
        out.append(_piece("claim", section, "\n".join(chunk), "teammate",
                          source=_parse_report_who(chunk[0])))
    return out


# -------------------------------------------------- from_transcript guts

def _superjev():
    """`superjev`, imported lazily and from this file's own directory, so
    `from_text`/`render`/the query helpers stay dependency-free."""
    import importlib.util
    import sys
    from pathlib import Path
    if "superjev" in sys.modules:
        return sys.modules["superjev"]
    path = Path(__file__).resolve().parent / "superjev.py"
    spec = importlib.util.spec_from_file_location("superjev", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["superjev"] = mod
    spec.loader.exec_module(mod)
    return mod


def _header_piece(section, text=None):
    """A composer-structure piece: a section header, or the
    relayed-reports mark inside a previous-turn block."""
    return _piece("header", section,
                  header_for_section(section) if text is None else text,
                  "composer")


def _receipt_pieces(sj, records, section):
    """One receipt piece per tool result in `records`, with the identity
    header the composer prefixes — byte-for-byte what
    `superjev._labelled_tool_results` produces, split back out."""
    out = []
    for text, cmd, cwd in sj._collect_tool_results_with_identity(records):
        header = sj._identity_header(cmd, cwd)
        body = f"{header}\n{text}" if header else text
        out.append(_piece("receipt", section, body, "tool_result",
                          source=_parse_identity(header)))
    return out


def _claim_pieces(sj, records, section):
    """One claim piece per relayed worker/teammate report in `records`,
    labelled exactly as the composer labels it. Mirrors
    `superjev._collect_report_blocks`, but keeps `who` instead of
    discarding it into a formatted string."""
    out = []
    for rec in records:
        if not sj._is_real_user_prompt_record(rec):
            continue
        msg = rec.get("message") if isinstance(rec, dict) else None
        text = sj._extract_text_blocks((msg or {}).get("content"))
        for who, body in sj._extract_report_blocks_from_text(text):
            rendered = _render_report(sj, who, body)
            out.append(_piece("claim", section, rendered, "teammate",
                              source=_parse_report_who(
                                  rendered.split("\n", 1)[0])))
    return out


def _reports_region_mark(sj, section, claims):
    """`[relayed reports in this turn]` as its own piece, ahead of a
    previous turn's first relayed report — when this checkout's composer
    emits that line, and nothing when it does not.

    Detected by FEATURE, not by branch name, exactly as `_render_report`
    detects the fenced renderer. PR #53's `_prev_turn_items` prepends
    `REPORTS_REGION_LABEL` to the first report in a previous turn's block,
    so its walker can tell that turn's receipts from its reports by
    structure the composer wrote rather than by the text of the lines;
    `main` emits no such line, and a window from either branch has to
    render byte-for-byte here.

    Taken from `sj.REPORTS_REGION_LABEL` rather than this module's own
    copy of the string, so if the composer ever changes the wording the
    bytes follow it instead of quietly diverging.

    The glue needs no special case: `Window._glue` gives a header that
    follows content in the same section the item separator, and gives the
    piece after a header a plain newline — which is byte-for-byte what
    `REPORTS_REGION_LABEL + "\n" + reports[0]` inside the composer's
    `"\n\n---\n\n".join` produces. It is a `header` piece, so it is
    structure and not evidence, and every `lines(trusted=True)` sweep
    skips it.
    """
    label = getattr(sj, "REPORTS_REGION_LABEL", None)
    if not claims or label is None:
        return []
    return [_header_piece(section, label)]


def _render_report(sj, who, body):
    """One report block, rendered exactly as this checkout's composer
    renders it. Reads the composer's own renderer when the branch has one
    (PR #53 adds `_render_report_block`, which fences and neutralises the
    body) and falls back to the label-plus-body shape `main` emits.

    This is half of what byte-identity on either branch needs;
    `_reports_region_mark` is the other half, and leaving it out was how
    every window with a relayed report in a PREVIOUS turn came out one
    header line short of PR #53's composer.
    `tests/replay_window_model.py` runs both branches' composers and
    reports the one class of case that still differs: a window a byte
    truncation cut into, where PR #53's `_repair_report_tail` re-opens the
    fence the cut sliced through and this module reproduces `main`'s raw
    byte cut instead. Porting that repair belongs to the migration."""
    renderer = getattr(sj, "_render_report_block", None)
    if renderer is not None:
        return renderer(who, body)
    return f"{sj.REPORT_LABEL.format(who=who)}\n{body}"


def _fact_pieces(sj, records, start, session_id):
    """(pieces, stats) — one fact piece per session-receipt line, the
    composer's layer 2: the transcript backfill first, then the on-disk
    store, deduped on the bare fact, trimmed to the newest
    `RECEIPTS_WINDOW`. `stats` carries where they came from, for the
    composer's `meta`."""
    stored = sj._load_receipts(session_id) if session_id else []
    stored_facts = {sj._receipt_bare_fact(r) for r in stored}
    backfilled = [f for f in sj._backfill_receipts_from_transcript(records, start)
                  if sj._receipt_bare_fact(f) not in stored_facts]
    receipts = backfilled + stored
    if len(receipts) > sj.RECEIPTS_WINDOW:
        receipts = receipts[-sj.RECEIPTS_WINDOW:]
    stats = {"receipts_from_store": len(stored),
             "receipts_backfilled": len(backfilled),
             "receipts_source": (
                 "store + transcript backfill" if stored and backfilled
                 else "transcript backfill" if backfilled
                 else "store" if stored else "none")}
    if not receipts:
        stats["receipts_source"] = "none" if not (stored or backfilled) \
            else stats["receipts_source"]
    pieces = [_piece("fact", SECTION_RECEIPTS, line, "session_receipt",
                     source=_parse_identity(line))
              for line in receipts]
    return pieces, stats


def _section_bytes(block):
    """The rendered byte size of one section's pieces, glue included."""
    if not block:
        return 0
    return len(_render(block, 0).encode("utf-8"))


def _fit_receipts(facts, budget, relevance_fn=None, draft_text=None):
    """(pieces, kept, dropped) for the session-receipts section inside
    `budget` bytes — the composer's `_build_receipts_block` rule in piece
    terms: the whole block when it fits, else receipts whose claim keys
    appear in the draft first (newest-first among them), then the rest
    newest-first, the survivors emitted in their ORIGINAL order."""
    if not facts:
        return [], 0, 0
    header = _header_piece(SECTION_RECEIPTS)
    whole = [header] + list(facts)
    if budget > 0 and _section_bytes(whole) <= budget:
        return whole, len(facts), 0
    if budget <= 0:
        return [], 0, len(facts)
    relevant = set()
    if relevance_fn is not None:
        relevant = relevance_fn([p.text for p in facts], draft_text)
    order = ([i for i in reversed(range(len(facts))) if i in relevant]
             + [i for i in reversed(range(len(facts))) if i not in relevant])
    kept = set()
    used = len((header.text + HEADER_SEPARATOR).encode("utf-8"))
    for i in order:
        cost = facts[i].nbytes + 1
        if used + cost > budget:
            continue
        kept.add(i)
        used += cost
    if not kept:
        return [], 0, len(facts)
    return ([header] + [facts[i] for i in sorted(kept)],
            len(kept), len(facts) - len(kept))


def _fit_reports(claims, budget, section=SECTION_REPORTS):
    """(pieces, kept, bytes_cut) for the relayed-reports section inside
    `budget` bytes — the composer's `_build_reports_block` rule in piece
    terms: whole reports go from the OLDEST end first, and if even the
    newest single report is over budget its own tail is kept."""
    if not claims or budget <= 0:
        return [], 0, 0
    header = _header_piece(section)
    kept = list(claims)
    cut = 0
    block = [header] + kept
    while _section_bytes(block) > budget and len(kept) > 1:
        cut += kept.pop(0).nbytes
        block = [header] + kept
    if _section_bytes(block) > budget:
        header_bytes = len((header.text + HEADER_SEPARATOR).encode("utf-8"))
        room = max(budget - header_bytes - 40, 0)
        if room <= 0:
            return [], 0, sum(p.nbytes for p in claims)
        raw = kept[0].text.encode("utf-8")
        tail = raw[-room:].decode("utf-8", errors="ignore")
        cut += len(raw) - len(tail.encode("utf-8"))
        block = [header, _piece("claim", section,
                                REPORT_CUT_MARKER + "\n" + tail,
                                "teammate", cut="head")]
        return block, 1, cut
    return block, len(kept), cut


def _fit_prev_turns(turns, budget):
    """(pieces, dropped, kept, truncated) for the previous-turns section
    inside `budget` bytes — the composer's
    `_build_prev_turns_block_detailed` rule in piece terms: the OLDEST
    turn goes first, and if the freshest turn alone is still over budget
    its TAIL is kept.

    A tail-keep FUSES that turn's items into one blob: tool-result text
    and, if the turn carried a relayed report, worker text, welded
    together with the head of the oldest item missing. Provenance is no
    longer separable, so the blob fails closed as untrusted — always, even
    when the turn happened to carry only tool results, because nothing in
    the resulting bytes says so and `from_text` would have to guess. That
    is the legacy policy's real cost, and the reason `Window.fit` exists:
    it never fuses two pieces, so it never has to.
    """
    if not turns or budget <= 0:
        return [], len(turns), 0, None

    def block_for(keep_turns):
        out = []
        for i, items in enumerate(keep_turns, start=1):
            if not items:
                continue
            out.append(_header_piece(prev_section(i)))
            out.extend(items)
        return out

    kept = list(turns)
    dropped = 0
    block = block_for(kept)
    while _section_bytes(block) > budget and len(kept) > 1:
        kept.pop()          # the oldest, furthest-back turn first
        dropped += 1
        block = block_for(kept)
    truncated = None
    if _section_bytes(block) > budget and kept:
        items = kept[0]
        joined = _render(items, 0)
        keep_bytes = max(budget - 48, 0)
        raw = joined.encode("utf-8")
        # `raw[-0:]` is the WHOLE string, not the empty one. The composer
        # has this same expression and therefore this same behaviour: at a
        # budget under 48 bytes it emits the marker plus the turn ENTIRE,
        # and leaves the over-cap result to the whole-window tail-keep
        # below. Reproduced deliberately, quirk and all, because
        # byte-identity is the point of this policy; `Window.fit` has no
        # such edge because it never cuts inside a piece.
        tail = raw[-keep_bytes:].decode("utf-8", errors="ignore")
        block = [
            _header_piece(prev_section(1)),
            _piece("receipt", prev_section(1),
                   PREV_CUT_MARKER + "\n" + tail,
                   "truncated_mixed", cut="head"),
        ]
        truncated = (1, len(raw) - len(tail.encode("utf-8")))
    return block, dropped, len(turns) - dropped, truncated


def _compose_meta(sj, records, start, cur_receipts, cur_claims, prev_spans,
                  prev_turn_pieces, fact_pieces, fact_stats, effective_cap,
                  prev_claim_pieces=None, prev_claims_per_turn=None):
    """The composer's own `meta` dict for this window, field for field, so
    a caller can migrate off `derive_evidence_window(..., return_meta=True)`
    without also rewriting `hook gate --explain`."""
    # A composer that lifts a previous turn's reports into their own
    # section leaves none inside the per-turn blocks, so the per-turn
    # counts come from `prev_claims_per_turn` instead.
    prev_reports = (prev_claims_per_turn if prev_claims_per_turn is not None
                    else [[p for p in items if p.kind == "claim"]
                          for items in prev_turn_pieces])
    split = prev_claim_pieces is not None
    # getattr, not a direct call: the byte-identity tests load THIS module
    # against an OLDER composer module to prove the render has not drifted,
    # and that older `sj` has no receipt-shape family at all. There the keys
    # are left OUT entirely rather than emitted empty, so `meta` still
    # matches that composer's `meta` field for field. The family adds meta
    # keys, never bytes.
    _shapes = getattr(sj, "_facts_receipt_shapes", None)
    meta = {
        "prev_turns_found": len(prev_turn_pieces),
        "prev_bytes": 0,
        "prev_dropped": 0,
        "receipts_count": len(fact_pieces),
        "receipts_bytes": 0,
        "current_bytes": 0,
        "cap_bytes": effective_cap,
        "total_bytes": 0,
        "current_turn_empty": not cur_receipts,
        "current_turn_start": start,
        "receipts_source": fact_stats["receipts_source"],
        "receipts_from_store": fact_stats["receipts_from_store"],
        "receipts_backfilled": fact_stats["receipts_backfilled"],
        "prev_truncated": None,
        "reports_found": len(cur_claims) + sum(len(r) for r in prev_reports),
        "reports_current": len(cur_claims),
        "reports_kept": 0,
        "reports_bytes": 0,
        "reports_cut_bytes": 0,
        "prev_turn_detail": [
            {"turn": i,
             "records": f"{a}-{b - 1}",
             "tool_results": len([p for p in prev_turn_pieces[i - 1]
                                  if p.kind == "receipt"]),
             "reports": len(prev_reports[i - 1]),
             # The composer's own glue, not a naive item join: the
             # relayed-reports mark sits a plain newline above the report
             # it marks, not behind an item separator.
             "bytes": len(_render(prev_turn_pieces[i - 1], 0).encode("utf-8")),
             "kept": None}
            for i, (a, b, _t) in enumerate(prev_spans, start=1)],
    }
    if _shapes:
        # RECEIPT SHAPES (see _facts_receipt_shapes): derived from the
        # transcript records, not from the rendered window, so the model
        # computes them exactly as the composer does rather than off its own
        # pieces. Same keys, same values, so `hook gate --explain` and the
        # gate's compose call read the same thing either way.
        facts = _shapes(records, start)
        meta["receipt_shape_facts"] = facts
        meta["receipt_shapes_count"] = len(facts)
    if split:
        meta.update({
            "prev_scanned": len(prev_spans),
            "receipts_dropped": 0,
            "receipts_share_bytes": 0,
            "reports_prev_found": len(prev_claim_pieces),
            "reports_prev_kept": 0,
            "reports_prev_bytes": 0,
            "reports_prev_cut_bytes": 0,
        })
    return meta


# ================================================ proof of concept reader
#
# ONE reader, read-only, wired to nothing: the PR-state arm PR #53 spent
# eight rounds hardening, rebuilt on pieces. Same verdict strings, same
# strength/recency rules, same fail-closed behaviour — but the question
# "is this line inside a worker report" is answered by `piece.trusted`,
# which comes from the transcript record, not by walking fences through
# text a worker helped write.
#
# The regexes are held here rather than imported so this file stays
# dependency-free for `from_text` callers; a test asserts every one is
# character-identical to `superjev`'s, so they cannot drift.

_PR_MERGED_CLAIM_RE = re.compile(r'PR\s*#?(\d+)\b[^.\n]{0,30}?\bmerged\b', re.IGNORECASE)
_PR_STATE_JSON_RE = re.compile(
    r'"number"\s*:\s*(\d+)[^{}]{0,300}?"state"\s*:\s*"(\w+)"', re.DOTALL)
_FACT_MERGE_RECEIPT_RE = re.compile(
    r'^\s*MERGED\b|\bgh\s+pr\s+merge\s+\d+|"mergedAt"\s*:\s*"[^"]+"', re.IGNORECASE)
_FACT_PR_NUM_RES = (
    re.compile(r'\bgh\s+pr\s+merge\s+(\d+)'),
    re.compile(r'\bgh\s+pr\s+(?:view|checks)\s+(\d+)'),
    re.compile(r'\(#(\d+)\)'),
    re.compile(r'#(\d+)\b'),
    re.compile(r'"number"\s*:\s*(\d+)'),
)
_PR_NOT_MERGED_PROSE_TMPL = r'#{n}\b[^.\n]{{0,40}}?\b(open|not merged|draft)\b'


class PrStateSignal(NamedTuple):
    """One PR-state signal, in PR #53's own field order.

    rank           the section's recency rank, or None for no ordering
                   information at all.
    pos            1-based line number in the rendered window.
    kind           'receipt' (a tool said it) or 'prose' (someone claimed
                   it).
    state          'MERGED' or 'NOT_MERGED'.
    raw_state      the literal lowercase word to quote in a block reason.
    strength       2 a state-bearing receipt, 1 a command-invocation
                   receipt, 0 prose.
    state_bearing  whether the LINE itself carries a machine state value,
                   whether or not a trust rule then demoted it to prose.
    piece          the piece this line came out of — the thing text-level
                   readers never had.
    """
    rank: int | None
    pos: int
    kind: str
    state: str
    raw_state: str
    strength: int
    state_bearing: bool
    piece: Piece

    def as_tuple7(self):
        """This signal in the exact 7-tuple shape
        `superjev._pr_state_signals` returns, for a direct cross-check."""
        return tuple(self)[:7]


def pr_state_signals_from_window(window, pr_number):
    """Every PR-state signal about `pr_number` in `window`, in window
    order — PR #53's `_pr_state_signals` rebuilt on pieces.

    The one substantive change is where trust comes from. `in_report` was
    a text-walk over report fences and section headers; here it is
    `not line.trusted`, which is set from the transcript record the line
    came out of. Everything that follows — a state-bearing receipt is
    strength 2, a bare `gh pr merge N` invocation receipt is strength 1, a
    claim's state-bearing line DEGRADES to prose at strength 0 rather than
    vanishing, and `state_bearing` is recorded separately from
    `strength` — is PR #53's rule unchanged.
    """
    pr_str = str(pr_number)
    prose_re = re.compile(_PR_NOT_MERGED_PROSE_TMPL.format(n=re.escape(pr_str)),
                          re.IGNORECASE)
    out = []
    for ln in window.lines():
        in_report = not ln.trusted
        for jm in _PR_STATE_JSON_RE.finditer(ln.raw):
            if jm.group(1) != pr_str:
                continue
            raw_state = jm.group(2).lower()
            norm = "MERGED" if raw_state == "merged" else "NOT_MERGED"
            if in_report:
                out.append(PrStateSignal(ln.turn_rank, ln.pos, "prose", norm,
                                         raw_state, 0, True, ln.piece))
            else:
                out.append(PrStateSignal(ln.turn_rank, ln.pos, "receipt", norm,
                                         raw_state, 2, True, ln.piece))

        if _FACT_MERGE_RECEIPT_RE.search(ln.raw):
            is_state_line = (bool(re.match(r'^\s*MERGED\b', ln.raw, re.IGNORECASE))
                             or '"mergedAt"' in ln.raw)
            has_identity = bool(_IDENTITY_TAIL_RE.search(ln.raw))
            if in_report:
                kind, strength, ok = "prose", 0, is_state_line
            else:
                kind, strength = "receipt", (2 if is_state_line else 1)
                ok = is_state_line or has_identity
            if ok:
                for rx in _FACT_PR_NUM_RES:
                    mm = rx.search(ln.raw)
                    if mm and mm.group(1) == pr_str:
                        out.append(PrStateSignal(ln.turn_rank, ln.pos, kind,
                                                 "MERGED", "merged", strength,
                                                 is_state_line, ln.piece))
                        break

        om = prose_re.search(ln.raw)
        if om:
            out.append(PrStateSignal(ln.turn_rank, ln.pos, "prose", "NOT_MERGED",
                                     om.group(1).lower(), 0, False, ln.piece))
    return out


def _signals_orderable(a, b):
    """True only when there is real ordering information between two
    signals: both sit in a known section AND those sections differ.
    `rank is None` means "unknown turn", not "oldest", and two signals in
    one section have no ordering either — inside a section, text order is
    assembly order."""
    return a.rank is not None and b.rank is not None and a.rank != b.rank


def pr_state_verdict_from_window(window, draft_text):
    """(reason_or_None, note_or_None) for the draft's PR-merge claim
    against `window` — PR #53's `_pr_mismatch_verdict`, verdict strings
    included, computed from pieces.

    Winner by (1) strength, then (2) recency among equal strength. Two
    fail-closed rules, both PR #53's:

    Rule A — if the strongest signal disagrees with ANY same-strength
    signal it cannot be ordered against, block on whichever says
    NOT_MERGED and say why in the note.

    Rule B — if the strongest MERGED signal carries no state value of its
    own (a bare `gh pr merge N` invocation receipt) and ANY not-merged
    signal exists at any strength, block on the not-merged side. A command
    invocation proves a command was typed, never that it succeeded.
    """
    if not draft_text or not window.pieces:
        return None, None
    m = _PR_MERGED_CLAIM_RE.search(draft_text)
    if not m:
        return None, None
    pr_num = m.group(1)
    signals = pr_state_signals_from_window(window, pr_num)
    if not signals:
        return None, None

    def sort_key(sig):
        # `-inf` is a tie-break placeholder ONLY; any conflict it would
        # settle against an unranked signal is caught as unorderable below.
        return (sig.strength,
                sig.rank if sig.rank is not None else float("-inf"),
                sig.pos)

    best = max(signals, key=sort_key)
    tied = [s for s in signals
            if s is not best and s.state != best.state
            and s.strength == best.strength and not _signals_orderable(s, best)]
    if tied:
        note = (f"PR state ambiguous: PR #{pr_num} has conflicting same-strength "
                "signals with no window section/turn ordering between them "
                "— failing closed on the not-merged signal")
        not_merged = best if best.state != "MERGED" else tied[0]
        return (f"PR mismatch: draft says PR #{pr_num} merged, evidence shows "
                f"{not_merged.raw_state}"), note
    if best.state == "MERGED" and not best.state_bearing:
        not_merged = [s for s in signals if s.state != "MERGED"]
        if not_merged:
            note = (f"PR state ambiguous: PR #{pr_num}'s strongest MERGED signal "
                    "carries no state value (a command invocation only); an "
                    "invocation-only merge receipt cannot outrank a not-merged "
                    "signal — failing closed on the not-merged signal")
            return (f"PR mismatch: draft says PR #{pr_num} merged, evidence shows "
                    f"{not_merged[0].raw_state}"), note
    if best.state == "MERGED":
        return None, None
    return (f"PR mismatch: draft says PR #{pr_num} merged, evidence shows "
            f"{best.raw_state}"), None
