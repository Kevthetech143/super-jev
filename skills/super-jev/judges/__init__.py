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

THE DOORWAY
Every model call goes through this package, and only the judge's own
implementation (lib/jev_client.py for the Jev profile) knows the vendor:

    judges.ask(state, questions, timeout)   one call, answers + usage
    judges.key_present() / key_env()         the key check, by the profile's env name
    judges.key_file()                        where that key is kept (~/.<env name>), for the setup steps
    judges.door_script()                     the implementation's own claim-check CLI
    judges.with_retry(send)                  the one retry rule (Overloaded only)

Failures are typed (judges.errors) and always mean "no verdict".

BACKENDS (the gate)
  * the profile's judge (default `typesafe-jev`) — wraps today's call exactly: it hands the
    window and the draft to `superjev.cmd_gate`, which is the same code
    path, the same ledger row and the same exit codes as before. No
    behaviour changes by adding this file.
  * `fake` — runs `SUPERJEV_GATE_CMD` directly, the way the existing
    self-mocking tests already do, and never falls back to the fleet
    jev.py. A test can therefore assert "no live call happened" rather
    than hoping.

Pick one with `SUPERJEV_JUDGE` (a profile name from judge_profiles.json, or
`fake`). An unrecognised value stops the run with one stderr line; it never
falls back to another judge.

See docs/plugins.md.
"""
import os
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.append(str(_SKILL_DIR))
import judge_profile                                       # noqa: E402
from judges.errors import (JudgeError, NoKey, AuthRejected, Unreachable,   # noqa: E402,F401
                           Overloaded, BadReply, TooBig, SecretBlocked, ERROR_KINDS)

__all__ = ["JudgeResult", "Judge", "ProfileJudge", "FakeJudge", "get_judge", "JUDGE_ENV",
           "ask", "profile", "key_env", "key_file", "key_file_path", "key_present", "require_key", "door_script", "with_retry",
           "JudgeError", "NoKey", "AuthRejected", "Unreachable", "Overloaded", "BadReply", "TooBig",
           "SecretBlocked", "ERROR_KINDS"]

#: The one setting that picks the judge.
JUDGE_ENV = judge_profile.JUDGE_ENV

#: profile kind -> (module in skills/super-jev/lib that speaks to that judge).
IMPLEMENTATIONS = {"jev": "jev_client"}
_LIB_DIR = _SKILL_DIR / "lib"


def profile():
    """The active judge's profile (SUPERJEV_JUDGE, read once at start)."""
    return judge_profile.PROFILE


def key_env():
    """Name of the environment variable holding the active judge's key."""
    return profile().key_env


def key_file():
    """Where the active judge's key is kept: ~/. plus its variable name, lowercased with dashes
    (ACME_API_KEY -> ~/.acme-api-key). "" for a judge that takes no key. Setup and the
    NoKey message both name this one file; nothing reads it for you."""
    env = key_env()
    return "~/." + env.lower().replace("_", "-") if env else ""


def key_file_path():
    """The key file to read, as a full path: the file named by $<KEY_ENV>_FILE when that is set
    (<KEY_ENV>_FILE), else key_file() with ~ expanded. "" for a keyless
    judge. The one file-path rule: the terminal app and the key provider follow it too."""
    env = key_env()
    if not env:
        return ""
    return os.environ.get(env + "_FILE", "").strip() or os.path.expanduser(key_file())


def key_present():
    """True when the judge can be called as far as its key goes: a keyless judge always can."""
    if not profile().key_required:
        return True
    return bool(os.environ.get(key_env(), "").strip())


def require_key():
    """The key, or NoKey ("" for a judge that takes none). The one place a key is read."""
    if not profile().key_required:
        return ""
    env = key_env()
    key = os.environ.get(env, "").strip()
    if not key:
        where = key_file()
        raise NoKey(f"{env} is not set -- export it first "
                    f"(export {env}=\"$(cat {where})\"; no {where} yet? "
                    f"your human makes it: (umask 077; cat > {where}))")
    return key


def _impl():
    name = IMPLEMENTATIONS.get(profile().kind)
    if name is None:
        raise SystemExit(f"super-jev: judge profile {profile().name!r} has kind {profile().kind!r}, "
                         f"which has no implementation (known: {', '.join(sorted(IMPLEMENTATIONS))})")
    if str(_LIB_DIR) not in sys.path:
        sys.path.append(str(_LIB_DIR))
    import importlib                                       # noqa: PLC0415
    return importlib.import_module(name)


def ask(state, questions, timeout=120):
    """One judge call for every question: {answers, model, chunks, input_tokens, latency_ms}.
    Raises a typed JudgeError on anything short of a usable answer; that is no verdict."""
    # The door scans for every judge, so no adapter can be handed a secret. An adapter may scan again.
    if str(_SKILL_DIR) not in sys.path:
        sys.path.insert(0, str(_SKILL_DIR))
    from prepare_bulk import payload_has_secret            # noqa: PLC0415
    if payload_has_secret(state) or payload_has_secret(questions):
        raise SecretBlocked("the request contains a secret; not sent")
    return _impl().ask(state, questions, timeout=timeout)


def door_script():
    """Path of the implementation's own claim-check CLI (the default gate door)."""
    name = IMPLEMENTATIONS.get(profile().kind)
    return (_LIB_DIR / f"{name}.py").resolve()


def with_retry(send):
    """The one retry rule: call `send()`; on Overloaded wait and try again, as the profile says
    (attempts, first delay, doubling). Every other error passes straight through."""
    p = profile()
    delay = p.retry_first_delay_ms / 1000
    for attempt in range(p.retry_attempts):
        try:
            return send()
        except Overloaded:
            if attempt >= p.retry_attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2


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


class ProfileJudge(Judge):
    """The profile's judge (Jev by default), today's call wrapped. Nothing more.

    It writes the window and the draft to temp files (which is what the
    Stop hook already does) and calls `superjev.cmd_gate` in hook mode,
    so the ledger row, the timeout budget, the cap-and-truncate pass and
    the exit codes are the existing ones.
    """

    @property
    def name(self):
        return profile().name

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
        return sj.door_missing(sj.GATE_CMD_ENV, sj.GATE_DOOR) is None

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


def get_judge(backend=None, **kwargs):
    """The judge to use: the argument, else `SUPERJEV_JUDGE`, else the default profile.

    An unrecognised name stops the run with one line: a typo must not quietly leave
    a draft unjudged, or judged by a different judge than the one asked for.
    """
    want = (backend or os.environ.get(JUDGE_ENV) or "").strip()
    try:
        prof = judge_profile.load(want or None)        # raises on an unknown name
    except ValueError as e:
        raise SystemExit(f"super-jev: {JUDGE_ENV}: {e}")
    return (FakeJudge if want == judge_profile.FAKE_JUDGE else ProfileJudge)(**kwargs) if prof else None


def _write_tmp(text, suffix):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False,
                                      encoding="utf-8")
    tmp.write(text or "")
    tmp.close()
    return tmp.name
