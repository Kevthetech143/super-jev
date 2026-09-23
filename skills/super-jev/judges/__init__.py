"""The judge backend, behind one interface.

super-jev's deterministic arms are string and integer work we own. The
judge is not: it is a model call to TypeSafe Jev, and it is the one part
of the harness we would most like to be able to swap — for a test that
must not touch the network, for a second opinion, for a local model
later. This package is that seam.

    from judges import get_judge
    judge = get_judge()
    result = judge.classify(draft_text, window)

`classify` returns a `JudgeResult`. Every backend returns the same shape,
so nothing downstream needs to know which backend answered.

BACKENDS
  * `typesafe` (default) — wraps today's call exactly: it hands the
    window and the draft to `superjev.cmd_gate`, which is the same code
    path, the same ledger row and the same exit codes as before. No
    behaviour changes by adding this file.
  * `fake` — runs `SUPERJEV_GATE_CMD` directly, the way the existing
    self-mocking tests already do, and never falls back to the fleet
    jev.py. A test can therefore assert "no live call happened" rather
    than hoping.

Pick one with `SUPERJEV_JUDGE=typesafe|fake`. An unrecognised value falls
back to `typesafe` and prints one stderr line.

See docs/plugins.md.
"""
import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["JudgeResult", "Judge", "TypeSafeJudge", "FakeJudge",
           "get_judge", "JUDGE_ENV", "BACKENDS", "DEFAULT_BACKEND"]

#: Which backend to use.
JUDGE_ENV = "SUPERJEV_JUDGE"
DEFAULT_BACKEND = "typesafe"

_SKILL_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class JudgeResult:
    """One judge's answer about one draft.

    `code` is the door's own exit code and keeps its existing meaning
    (0 clean, 2 reject, 3 read) — deliberately unchanged, because the
    hook's block/advisory branch is built on it and this seam is not the
    place to redefine it. `backend` names who answered, so a log can say
    so instead of implying a live call that never happened.
    """
    code: int
    backend: str
    stdout: str = ""
    stderr: str = ""
    detail: dict = field(default_factory=dict, compare=False)

    @property
    def clean(self):
        return self.code == 0

    @property
    def rejected(self):
        return self.code == 2


class Judge:
    """The interface. Two methods, one of them optional."""

    name = "judge"

    def classify(self, draft, window):
        """`JudgeResult` for one draft against one window."""
        raise NotImplementedError

    def available(self):
        """False when this backend cannot run here (no door, no key).

        A caller that wants to degrade gracefully checks this first; one
        that does not will get a `JudgeResult` carrying the door's own
        refusal code instead.
        """
        return True


def _window_text(window):
    """The composed window bytes, whether we were handed a `Window` or
    the text itself. A judge takes evidence; it should not care which of
    the two the caller happens to be holding."""
    if window is None:
        return ""
    render = getattr(window, "render", None)
    if callable(render):
        return render()
    return str(window)


class TypeSafeJudge(Judge):
    """Today's call, wrapped. Nothing more.

    It writes the window and the draft to temp files (which is what the
    Stop hook already does) and calls `superjev.cmd_gate` in hook mode,
    so the ledger row, the timeout budget, the cap-and-truncate pass and
    the exit codes are the existing ones.
    """

    name = "typesafe"

    def __init__(self, timeout=None):
        self.timeout = timeout

    def _sj(self):
        # Appended, not inserted: this backend must not reorder the
        # importing process's own module search path.
        if str(_SKILL_DIR) not in sys.path:
            sys.path.append(str(_SKILL_DIR))
        import superjev                                   # noqa: PLC0415
        return superjev

    def available(self):
        sj = self._sj()
        return sj.door_missing(sj.GATE_CMD_ENV, sj.JEV_LIB) is None

    def classify(self, draft, window):
        import argparse                                   # noqa: PLC0415
        sj = self._sj()
        ev_path = draft_path = None
        try:
            ev_path = _write_tmp(_window_text(window), ".md")
            draft_path = _write_tmp(draft or "", ".md")
            ns = argparse.Namespace(evidence=[ev_path], draft=draft_path,
                                    claim=None, json=False, hook_mode=True,
                                    timeout=self.timeout)
            code, out, err = sj.cmd_gate(ns)
        finally:
            for p in (ev_path, draft_path):
                if p:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
        return JudgeResult(code=code, backend=self.name, stdout=out or "",
                           stderr=err or "")


class FakeJudge(Judge):
    """`SUPERJEV_GATE_CMD`, run directly — the test backend.

    Same env var the existing self-mocking tests set, same argv shape the
    real door builds (`<cmd> <evidence...> --kit reply --draft <file>`),
    so a fake door script written for those tests works here unchanged.
    With the var unset this is simply unavailable and says so through the
    door's own refusal code rather than reaching for the fleet jev.py.
    """

    name = "fake"
    #: Exit code for "there is no door here" — `door_refuse`'s own.
    UNAVAILABLE_CODE = 6

    def available(self):
        return bool(os.environ.get("SUPERJEV_GATE_CMD"))

    def classify(self, draft, window):
        cmd = os.environ.get("SUPERJEV_GATE_CMD")
        if not cmd:
            return JudgeResult(
                code=self.UNAVAILABLE_CODE, backend=self.name,
                stderr="SUPERJEV_GATE_CMD is not set — the fake judge has "
                       "nothing to run")
        ev_path = draft_path = None
        try:
            ev_path = _write_tmp(_window_text(window), ".md")
            draft_path = _write_tmp(draft or "", ".md")
            argv = [*shlex.split(cmd), ev_path, "--kit", "reply",
                    "--draft", draft_path]
            try:
                p = subprocess.run(argv, capture_output=True, text=True,
                                   timeout=60)
            except (OSError, subprocess.SubprocessError) as e:
                return JudgeResult(code=self.UNAVAILABLE_CODE,
                                   backend=self.name, stderr=repr(e))
            return JudgeResult(code=p.returncode, backend=self.name,
                               stdout=p.stdout or "", stderr=p.stderr or "")
        finally:
            for p_ in (ev_path, draft_path):
                if p_:
                    try:
                        os.unlink(p_)
                    except OSError:
                        pass


BACKENDS = {"typesafe": TypeSafeJudge, "fake": FakeJudge}


def get_judge(backend=None, **kwargs):
    """The judge to use: the argument, else `SUPERJEV_JUDGE`, else TypeSafe.

    An unrecognised name is one stderr line and the default backend — a
    typo must not quietly leave a draft unjudged.
    """
    want = backend or os.environ.get(JUDGE_ENV) or DEFAULT_BACKEND
    key = str(want).strip().lower()
    cls = BACKENDS.get(key)
    if cls is None:
        print(f"judges: {JUDGE_ENV}={want!r} is not one of "
              f"{'/'.join(sorted(BACKENDS))} — using {DEFAULT_BACKEND!r}",
              file=sys.stderr)
        cls = BACKENDS[DEFAULT_BACKEND]
    return cls(**kwargs)


def _write_tmp(text, suffix):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False,
                                      encoding="utf-8")
    tmp.write(text or "")
    tmp.close()
    return tmp.name
