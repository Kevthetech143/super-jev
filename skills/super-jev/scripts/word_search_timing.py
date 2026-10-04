#!/usr/bin/env python3
"""Timing gate for the word index. Exits 0 only if a warm word_search (saved index) is under
--max-warm-seconds and a cold one (no index) is within --max-cold-ratio of origin/main's time.
Local, no network.

    python3 scripts/word_search_timing.py --max-warm-seconds 3 --max-cold-ratio 1.10
"""
import argparse
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ab_word_rank as ab  # noqa: E402


def one(skill, state, files, nq):
    return sum(ab.run_tree(skill, state, "t", files, 12000, nq)["times"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-warm-seconds", type=float, default=3.0)
    ap.add_argument("--max-cold-ratio", type=float, default=1.10)
    ap.add_argument("--files", type=int, default=300)
    ap.add_argument("--questions", type=int, default=5)
    ap.add_argument("--runs", type=int, default=3)
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        base = ab.main_tree(t / "main")
        base_t, cold_t, warm_t = [], [], []
        for i in range(a.runs):
            base_t.append(one(base, t / f"m{i}", a.files, a.questions))
            # cold: fresh state dir each run, so no saved index; warm: same dir again
            cold_t.append(one(ab.SKILL, t / f"c{i}", a.files, a.questions))
            warm_t.append(one(ab.SKILL, t / f"c{i}", a.files, a.questions) / a.questions)
        b, c, w = statistics.median(base_t), statistics.median(cold_t), statistics.median(warm_t)
    ratio = c / b
    print(f"main_total={b:.3f}s cold_total={c:.3f}s ratio={ratio:.3f} warm_per_question={w:.3f}s")
    ok = w < a.max_warm_seconds and ratio <= a.max_cold_ratio
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
