#!/usr/bin/env python3
"""Compute the judge-drift baseline for one bench from two runs of the SAME code.

Reads <bench>/runs/<tagA>/decisions.tsv and <bench>/runs/<tagB>/decisions.tsv
(see ci/compare_runs.py for the decisions.tsv format) and writes a baseline
JSON:

    {
      "bench": "<bench>",
      "tags": ["<tagA>", "<tagB>"],
      "drift_ids": ["<case id that flipped>", ...],
      "truths_blocked": [<count in tagA>, <count in tagB>],
      "lies_allowed": [<count in tagA>, <count in tagB>],
      "only_in_a": ["<case id present only in run tagA>", ...],
      "only_in_b": ["<case id present only in run tagB>", ...]
    }

The drift set is the set of case ids whose decision flipped between the two
same-code runs. Because the code did not change, every flip is judge drift,
not a real window change. Cases present in only one run cannot be classified
as flips; they are reported separately (count + ids), not silently ignored.

The [a, b] pairs are per-run counts over the drift set: for run tagA and
run tagB respectively, how many drifted cases it decided "false" (truths
possibly blocked) and how many it decided "true" (lies possibly allowed).
A flip always contributes one true and one false across the two runs, so
truths_blocked[i] + lies_allowed[i] == len(drift_ids) for each run. Decision
strings are matched case-insensitively; common synonyms are accepted
(true/pass/accept/yes vs false/fail/reject/no). Decisions in neither group
are counted in neither.

Usage:
    ci/drift_baseline.py <bench> <tagA> <tagB> [--out <json>]

Exit code is 0 on success, 1 on bad input (missing/malformed decisions.tsv);
the error is a single line naming the path/row, with no traceback. A one-line
summary is printed to stdout (plus an extra line only when cases are present
in only one run).
Only the Python standard library is used.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import compare_runs  # noqa: E402


def build_baseline(bench, tag_a, tag_b):
    cases_a = compare_runs.read_decisions(bench, tag_a)
    cases_b = compare_runs.read_decisions(bench, tag_b)
    flips = compare_runs.classify_flips(cases_a, cases_b)
    drift_ids = sorted(case_id for case_id, _ in flips)

    truths_blocked = [0, 0]
    lies_allowed = [0, 0]
    for i, cases in enumerate((cases_a, cases_b)):
        for case_id in drift_ids:
            norm = compare_runs.normalize_decision(cases[case_id][1])
            if norm == "false":
                truths_blocked[i] += 1
            elif norm == "true":
                lies_allowed[i] += 1

    return {
        "bench": bench,
        "tags": [tag_a, tag_b],
        "drift_ids": drift_ids,
        "truths_blocked": truths_blocked,
        "lies_allowed": lies_allowed,
        "only_in_a": sorted(set(cases_a) - set(cases_b)),
        "only_in_b": sorted(set(cases_b) - set(cases_a)),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build the judge-drift baseline for a bench from two "
                    "runs of the same code."
    )
    parser.add_argument("bench", help="bench directory containing runs/<tag>/")
    parser.add_argument("tagA", help="first run tag (same code)")
    parser.add_argument("tagB", help="second run tag (same code)")
    parser.add_argument("--out", metavar="JSON", default=None,
                        help="where to write the baseline JSON "
                             "(default: drift-baseline-<tagA>-<tagB>.json "
                             "in the current directory)")
    args = parser.parse_args(argv)

    try:
        baseline = build_baseline(args.bench, args.tagA, args.tagB)
    except (OSError, ValueError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 1

    out = args.out or ("drift-baseline-%s-%s.json" % (args.tagA, args.tagB))
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(baseline, fh, indent=2, sort_keys=True)
        fh.write("\n")

    sys.stdout.write(
        "drift_baseline %s A=%s B=%s: %d drift case(s), "
        "truths_blocked=%s lies_allowed=%s -> %s\n"
        % (args.bench, args.tagA, args.tagB,
           len(baseline["drift_ids"]),
           baseline["truths_blocked"], baseline["lies_allowed"], out)
    )

    only_in_a = baseline["only_in_a"]
    only_in_b = baseline["only_in_b"]
    total = len(only_in_a) + len(only_in_b)
    if total:
        parts = []
        if only_in_a:
            parts.append("only in %s: %s" % (args.tagA, ", ".join(only_in_a)))
        if only_in_b:
            parts.append("only in %s: %s" % (args.tagB, ", ".join(only_in_b)))
        sys.stdout.write(
            "%d case(s) present in only one run (%s)\n"
            % (total, "; ".join(parts))
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
