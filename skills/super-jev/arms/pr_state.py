"""The PR-state arm, as a plug-in.

This is the template arm — the one migrated end-to-end so the next arm
has a shape to copy. It is one file and one function, and it holds no
gate logic of its own: it asks the window model the question and wraps
the answer in a `Verdict`.

WHAT IT CHECKS
  A draft that says "PR #N merged" against every PR-state signal the
  evidence window carries for that PR. Fails closed on a conflict it
  cannot order, and on a bare `gh pr merge N` invocation receipt that
  proves only that a command was typed. Both rules, and the verdict
  strings, live in `window_model.pr_state_verdict_from_window`.

WHAT IT DOES NOT DO
  It never invents a mismatch from a PR the draft does not name, and it
  says nothing at all when the evidence carries no signal for that PR.
  An evidence GAP is the judge's problem, not this arm's.

The window is the argument, so the arm is indifferent to where the window
came from: a live `from_transcript` build or a recorded bench replay
through `from_text` both work, and that is what makes the switched-on and
switched-off paths comparable case for case.
"""
import sys
from pathlib import Path

_SKILL_DIR = str(Path(__file__).resolve().parent.parent)
if _SKILL_DIR not in sys.path:
    sys.path.insert(0, _SKILL_DIR)
import window_model as wm                                # noqa: E402

from . import Verdict                                    # noqa: E402

NAME = "pr_state"
KIND = "deterministic"
DEFAULT_MODE = "block"


def check(window, draft, ctx):
    """`Verdict` or None. Pure string/int work: no model call, no network.

    `ctx` is unused — this arm needs nothing beyond the window and the
    draft, which is the shape every deterministic arm should aim for.
    """
    if window is None or not draft:
        return None
    reason, note = wm.pr_state_verdict_from_window(window, draft)
    if not reason:
        return None
    return Verdict(arm=NAME, decision="block", reason=reason,
                   explain=note or "")


def window_from_text(evidence_text):
    """A `Window` for an already-composed window that only exists as flat
    text — what the gate has (it writes the composed window to a temp
    file before the judge call) and what a bench replay has.

    Here rather than in the registry on purpose: turning bytes back into
    pieces is a window-model concern this arm happens to need, not
    something every arm must inherit.
    """
    return wm.from_text(evidence_text or "")
