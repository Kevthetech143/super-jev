#!/usr/bin/env python3
"""Scrub PR title+body for performance numbers before merging.

Reads the PR title+body from stdin, prints every line that contains a
performance number, and exits 1 when any line hit, 0 when the text is clean.

A performance number is a digit run followed (with optional whitespace) by a
unit word: passed, failed, test(s), case(s), truth(s), lie(s), ms, ns, s,
bugs, tok(en)(s), % -- or the phrase "Nx faster" (e.g. "2x faster"; a bare
"x" as in "12 x 34" is NOT a unit).
PR and issue references ("#52 passed", "PR #63") and dotted version strings
("2.0.1") are ignored.
"""

import re
import sys

PERF_RE = re.compile(
    r"(?<![#\d.])"      # not part of a longer digit run, a #ref, or a dotted version
    r"\d+"
    r"(?!\.\d)"         # not the head of a dotted version like 2.0
    r"\s*"
    r"(?:(?:passed|failed|tests?|cases?|truths?|lies?|ms|ns|s|bugs|toks?|tokens?)\b|x\s+faster|%)",
    re.IGNORECASE,
)


def scrub(text):
    """Return the list of lines in text that contain a performance number."""
    return [line for line in text.splitlines() if PERF_RE.search(line)]


def selftest():
    cases = [
        # (text, expect_hit)
        ("All 3 passed, 2 failed.", True),   # real count line
        ("#52 passed", False),               # issue reference
        ("PR #63", False),                   # PR reference
        ("12 ms", True),                     # real measurement
        ("100% green", True),
        ("v2.0.1", False),                   # version string
        ("2.0.1 released", False),           # version string
        ("See tests 12 and 13.", False),    # bare numbers, no unit
        ("12 messages sent", False),         # ms-lookalike word
        ("Fixes #63 failed runs", False),    # digit run behind #
        ("0 failed", True),
        ("7 truths, 1 lie", True),
        # widened unit list (v2)
        ("44ns", True),                      # Ns, no space
        ("9 s", True),                       # N s
        ("12 s/op", True),                   # N s/op
        ("2x faster", True),                 # Nx faster
        ("3X Faster", True),                  # case-insensitive
        ("3 bugs left", True),               # N bugs
        ("50 tokens used", True),            # N tokens
        ("50 tok", True),                     # N tok
        ("12 x 34 grid", False),             # bare x without "faster" is not a unit
        ("12 seconds", False),               # unit word must be exact, not a longer word
    ]
    bad = []
    for text, expect in cases:
        hit = bool(scrub(text))
        if hit != expect:
            bad.append((text, expect, hit))
    if bad:
        for text, expect, hit in bad:
            print(f"SELFTEST FAIL: {text!r} expected_hit={expect} got_hit={hit}")
        return 1
    print("SELFTEST OK")
    return 0


def main(argv):
    if len(argv) == 2 and argv[1] == "--selftest":
        return selftest()
    hits = scrub(sys.stdin.read())
    for line in hits:
        print(line)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
