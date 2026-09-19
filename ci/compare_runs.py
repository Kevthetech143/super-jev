#!/usr/bin/env python3
"""Compare two judge runs of the same bench and label each flipped case.

Reads <bench>/runs/<tagA>/decisions.tsv and <bench>/runs/<tagB>/decisions.tsv.
Each decisions.tsv is tab-separated with exactly three columns:

    case_id<TAB>verdict_text<TAB>decision

For every case present in both runs whose decision flipped:
  - DRIFT         : verdict text is identical, decision flipped
  - WINDOW CHANGE : verdict text changed, decision flipped

Cases that did not flip are not reported. Cases present in only one run are
reported separately (count + ids), not silently ignored.

With --baseline <json> (a drift-baseline file written by ci/drift_baseline.py),
a flipped case is reported as KNOWN DRIFT and excluded from the DRIFT /
WINDOW CHANGE counts only when BOTH its id is in the baseline drift set AND
classify_flips labels this flip DRIFT (verdict text unchanged). A
WINDOW CHANGE flip on a drift-listed case is a genuine window change: it stays
counted and is labelled "WINDOW CHANGE (case is also in the drift set)".

Decisions are normalized before comparison (case-insensitive; common synonyms
true/pass/accept/yes and false/fail/reject/no are folded), so spelling or
synonym differences are not counted as flips.

Usage:
    ci/compare_runs.py <bench> <tagA> <tagB> [--diff ID] [--baseline <json>]

Exit code is 0 on success, 1 on bad input (missing/malformed decisions.tsv or
baseline file); the error is a single line naming the path/row, with no
traceback. A one-line summary is printed to stdout on success (plus an extra
line only when cases are present in only one run).
Only the Python standard library is used.
"""

import argparse
import csv
import json
import os
import sys

DECISIONS_TSV = "decisions.tsv"

DRIFT = "DRIFT"
WINDOW_CHANGE = "WINDOW CHANGE"
KNOWN_DRIFT = "KNOWN DRIFT"
DRIFT_SET_SUFFIX = " (case is also in the drift set)"

TRUE_WORDS = {"true", "pass", "accept", "yes"}
FALSE_WORDS = {"false", "fail", "reject", "no"}


def decisions_path(bench, tag):
    return os.path.join(bench, "runs", tag, DECISIONS_TSV)


def read_decisions(bench, tag):
    """Return {case_id: (verdict_text, decision)} for one run.

    Raises FileNotFoundError when the decisions.tsv is missing and
    ValueError on malformed rows.
    """
    path = decisions_path(bench, tag)
    rows = {}
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for lineno, row in enumerate(reader, start=1):
            if not row:
                continue
            if len(row) != 3:
                raise ValueError(
                    "%s:%d: expected 3 tab-separated columns "
                    "(case_id, verdict_text, decision), got %d"
                    % (path, lineno, len(row))
                )
            case_id, verdict_text, decision = row
            if not case_id:
                raise ValueError("%s:%d: empty case_id" % (path, lineno))
            rows[case_id] = (verdict_text, decision)  # last row wins on duplicates
    return rows


def normalize_decision(decision):
    """Fold a decision string to "true"/"false", or None if unrecognized."""
    word = decision.strip().lower()
    if word in TRUE_WORDS:
        return "true"
    if word in FALSE_WORDS:
        return "false"
    return None


def classify_flips(cases_a, cases_b):
    """Return [(case_id, label)] for cases in both runs with a flipped decision.

    Decisions are normalized on both sides before comparison, so
    case/synonym differences (e.g. "TRUE" vs "true", "yes" vs "true") are
    not counted as flips. The label is DRIFT when the verdict text is
    identical but the (normalized) decision flipped, and WINDOW CHANGE when
    the verdict text changed.
    """
    flips = []
    for case_id in sorted(set(cases_a) & set(cases_b)):
        verdict_a, decision_a = cases_a[case_id]
        verdict_b, decision_b = cases_b[case_id]
        if normalize_decision(decision_a) == normalize_decision(decision_b):
            continue
        if verdict_a == verdict_b:
            flips.append((case_id, DRIFT))
        else:
            flips.append((case_id, WINDOW_CHANGE))
    return flips


def load_baseline(path):
    """Return the set of drift case ids from a drift-baseline JSON file."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    drift_ids = data.get("drift_ids", [])
    if not isinstance(drift_ids, list):
        raise ValueError("baseline %s: drift_ids must be a list" % path)
    return set(drift_ids)


def compare(bench, tag_a, tag_b, baseline=None):
    """Compare two runs; return a result dict.

    baseline is a set of known-drift case ids (or None). A flip is excluded
    as KNOWN DRIFT only when its id is in the baseline set AND classify_flips
    labels this flip DRIFT; a WINDOW CHANGE flip on a drift-listed case stays
    counted and is labelled "WINDOW CHANGE (case is also in the drift set)".
    """
    cases_a = read_decisions(bench, tag_a)
    cases_b = read_decisions(bench, tag_b)
    flips = classify_flips(cases_a, cases_b)

    only_in_a = sorted(set(cases_a) - set(cases_b))
    only_in_b = sorted(set(cases_b) - set(cases_a))

    known_drift = set()
    counted = []
    display_labels = {}
    for case_id, label in flips:
        if baseline is not None and case_id in baseline and label == DRIFT:
            known_drift.add(case_id)
            display_labels[case_id] = KNOWN_DRIFT
        else:
            counted.append((case_id, label))
            if baseline is not None and case_id in baseline:
                display_labels[case_id] = label + DRIFT_SET_SUFFIX
            else:
                display_labels[case_id] = label

    counts = {DRIFT: 0, WINDOW_CHANGE: 0}
    for _, label in counted:
        counts[label] += 1

    return {
        "bench": bench,
        "tags": [tag_a, tag_b],
        "cases_a": cases_a,
        "cases_b": cases_b,
        "flips": flips,
        "only_in_a": only_in_a,
        "only_in_b": only_in_b,
        "known_drift": sorted(known_drift),
        "counted": counted,
        "counts": counts,
        "display_labels": display_labels,
    }


def describe_case(result, case_id):
    """Return a multi-line description of one case across the two runs."""
    cases_a, cases_b = result["cases_a"], result["cases_b"]
    tag_a, tag_b = result["tags"]
    if case_id not in cases_a and case_id not in cases_b:
        return "case %s: not present in either run" % case_id
    lines = ["case %s:" % case_id]
    for tag, cases in ((tag_a, cases_a), (tag_b, cases_b)):
        if case_id in cases:
            verdict, decision = cases[case_id]
            lines.append("  %s: decision=%s verdict=%r" % (tag, decision, verdict))
        else:
            lines.append("  %s: not present" % tag)
    lines.append("  label: %s" % result["display_labels"].get(case_id, "no flip"))
    return "\n".join(lines)


def only_in_line(tag_a, only_in_a, tag_b, only_in_b):
    """Return the 'present in only one run' summary line, or None."""
    total = len(only_in_a) + len(only_in_b)
    if total == 0:
        return None
    parts = []
    if only_in_a:
        parts.append("only in %s: %s" % (tag_a, ", ".join(only_in_a)))
    if only_in_b:
        parts.append("only in %s: %s" % (tag_b, ", ".join(only_in_b)))
    return "%d case(s) present in only one run (%s)" % (total, "; ".join(parts))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare two judge runs and label each flipped case."
    )
    parser.add_argument("bench", help="bench directory containing runs/<tag>/")
    parser.add_argument("tagA", help="first run tag")
    parser.add_argument("tagB", help="second run tag")
    parser.add_argument("--diff", metavar="ID", default=None,
                        help="show detail for one case id")
    parser.add_argument("--baseline", metavar="JSON", default=None,
                        help="drift-baseline JSON; known-drift flips are "
                             "reported as KNOWN DRIFT and excluded from counts")
    args = parser.parse_args(argv)

    try:
        baseline = load_baseline(args.baseline) if args.baseline else None
        result = compare(args.bench, args.tagA, args.tagB, baseline=baseline)
    except (OSError, ValueError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 1

    out = []
    labels = result["display_labels"]
    for case_id, _ in result["flips"]:
        out.append("%-13s %s" % (labels[case_id], case_id))

    if args.diff:
        out.append(describe_case(result, args.diff))

    one_run = only_in_line(args.tagA, result["only_in_a"],
                           args.tagB, result["only_in_b"])
    if one_run:
        out.append(one_run)

    counts = result["counts"]
    summary = ("%s A=%s B=%s: %d flip(s) counted"
               % (args.bench, args.tagA, args.tagB, len(result["counted"])))
    counts_part = "%d %s, %d %s" % (counts[DRIFT], DRIFT,
                                    counts[WINDOW_CHANGE], WINDOW_CHANGE)
    if baseline is not None:
        summary += " (%d total, %d known drift excluded; %s)" % (
            len(result["flips"]), len(result["known_drift"]), counts_part)
    else:
        summary += " (%s)" % counts_part
    out.append(summary)

    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
