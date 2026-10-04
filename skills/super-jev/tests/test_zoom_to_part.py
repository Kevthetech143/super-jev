"""The located span: a tie at the top goes to the tightest part, and a short file's parts are offered too."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ask  # noqa: E402

CHUNKS = ["intro\n" * 30]               # one whole-file passage of 30 lines
PARTS = [("<window>", 1, 31), ("count_wicks", 40, 47)]  # chunk ids 1 and 2


def cands(*pairs):
    return [{"sourceId": str(i), "score": s} for i, s in pairs]


def test_a_tie_goes_to_the_fewest_lines_not_the_first_listed():
    c = cands((0, 0.99), (2, 0.99))
    assert ask.tightest_near_top(c[0], c, CHUNKS, PARTS, 1)["sourceId"] == "2"


def test_a_near_tie_inside_the_margin_goes_to_the_tighter_part():
    c = cands((0, 0.99), (2, 0.98))
    assert ask.tightest_near_top(c[0], c, CHUNKS, PARTS, 1)["sourceId"] == "2"


def test_a_clearly_lower_tight_part_does_not_beat_the_top():
    c = cands((0, 0.95), (2, 0.5))
    assert ask.tightest_near_top(c[0], c, CHUNKS, PARTS, 1)["sourceId"] == "0"


def test_no_candidates_gives_none():
    assert ask.tightest_near_top(None, [], CHUNKS, PARTS, 1) is None
