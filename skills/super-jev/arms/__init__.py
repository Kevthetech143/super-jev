"""The arm registry — super-jev's plug-in layer for CHECK ARMS.

An arm is one check. It looks at a draft and the evidence window behind
it and either says nothing or returns one `Verdict`. That is the whole
contract, and it is deliberately the whole contract: an arm is one file
with one function, so adding a check never means editing the gate.

    skills/super-jev/arms/<name>.py

        NAME = "my_check"                 # MUST equal the file stem
        KIND = "deterministic"            # or "judge"
        DEFAULT_MODE = "block"            # or "advisory" / "off"

        def check(window, draft, ctx):
            ...
            return None                   # nothing to say
            return Verdict(arm=NAME, decision="block", reason="...",
                           explain="...")

        def contribute(window, draft, ctx):   # OPTIONAL, see below
            return ["one evidence line", "another"]

`check` returns `None` or a `Verdict`. It MUST NOT raise; the registry
catches anyway (an arm bug must never take the gate down) but an arm that
leans on that is a broken arm.

Keep an arm's module-level imports CHEAP. Every hook event imports every
arm in the package, so a top-level import of something slow is paid on
every turn whether that arm fires or not. Import the expensive thing
inside `check`.

THE TWO PHASES OF A RUN
-----------------------
`run_arms` runs in two passes, in this order:

  1. THE EVIDENCE PHASE. Every arm that exposes `contribute(window,
     draft, ctx) -> list[str]` is asked for extra window lines. This is
     the hook for a fact family that has to put something IN FRONT of the
     judge — a derived line the composer never wrote — rather than just
     ruling on what is already there. The lines are appended to the
     evidence the judge sees, under one labelled block, and they are
     appended BEFORE any judge call happens. `ctx` carries no `judge` key
     during this phase, on purpose: an arm cannot both feed the judge and
     read its answer.
  2. THE CHECK PHASE. Every arm's `check` runs, in arm-name order, and
     `ctx["judge"]` is now a zero-argument callable.

ONE JUDGE CALL PER RUN
----------------------
`ctx["judge"]()` returns a `judges.JudgeResult`. The FIRST call performs
the single TypeSafe classify over (draft, contributed evidence + window);
every later call in the same run returns that same object. So:

  * zero arms with `KIND = "judge"`      -> zero judge calls;
  * N arms with `KIND = "judge"`         -> exactly one judge call, and
    all N interpret the same `JudgeResult` in their own `check`.

An arm that needs nothing from the judge must not touch `ctx["judge"]`.

MODES
-----
Every arm carries a `DEFAULT_MODE`, and every arm can be overridden:

  * env, per arm:  `SUPERJEV_ARM_<NAME>=block|advisory|off` (NAME
    upper-cased), which wins over everything;
  * a JSON config file named by `SUPERJEV_ARMS_CONFIG`, of the shape
    `{"arms": {"pr_state": "advisory"}}` (a bare `{"pr_state": ...}` map
    is accepted too);
  * otherwise the arm's own `DEFAULT_MODE`.

An unrecognised mode value falls back to the arm's default and prints ONE
line to stderr saying so — a typo in a mode name must not silently turn a
block into nothing, and must not spam a hook's stderr either.

  * `block`    — the verdict blocks (decision="block")
  * `advisory` — the verdict is reported, never blocks (decision="advisory")
  * `off`      — the arm does not run at all

Per-arm mode is NOT the gate's judge-advisory failsafe. They are two
different objects over two different scopes: a mode is one arm's own
standing, set once and read on every run; the failsafe is one gate call's
outcome being demoted after the fact. `judge_only_blocks` below states
the failsafe rule in registry terms. See docs/plugins.md.

DISCOVERY
---------
`arm_names()` lists the search path: this package directory, plus any
directory named by `SUPERJEV_ARMS_EXTRA_DIR` (os.pathsep-separated) or
passed as `extra=`. There is no hard-coded list of arms anywhere: drop a
file in, it is an arm.

  * files starting with `_`, and `__init__.py` itself, are skipped, so
    private helpers are still possible;
  * an arm whose `NAME` is not its file stem is REJECTED with one stderr
    line and skipped. Names and stems are one keyspace, so the name in
    `SUPERJEV_ARM_<NAME>`, the key in the config file, the argument to
    `load_arms(names=...)` and the file on disk are always the same word.

The extra directory exists so a test (or a private local arm) never has
to write a module into the shipped package directory.

See docs/plugins.md.
"""
import importlib
import importlib.util
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Verdict", "ArmSpec", "ArmRun", "JudgeEvidence", "MODES", "DEFAULT_MODE",
    "ARM_MODE_ENV_PREFIX", "ARMS_CONFIG_ENV", "ARMS_SWITCH_ENV",
    "ARMS_EXTRA_DIR_ENV", "KIND_DETERMINISTIC", "KIND_JUDGE",
    "search_dirs", "arm_names", "load_arm", "load_arms", "arm_mode",
    "arms_enabled", "run_arms", "block_reasons", "judge_only_blocks",
    "make_judge_accessor", "reset_mode_warnings",
]

#: The three modes an arm can be in.
MODES = ("block", "advisory", "off")
#: The mode an arm gets when it declares nothing legible itself.
DEFAULT_MODE = "block"

#: The two kinds of arm. `deterministic` is string and integer work we
#: own; `judge` needs the model call behind `ctx["judge"]`.
KIND_DETERMINISTIC = "deterministic"
KIND_JUDGE = "judge"
KINDS = (KIND_DETERMINISTIC, KIND_JUDGE)

#: `SUPERJEV_ARM_PR_STATE=advisory` overrides the `pr_state` arm.
ARM_MODE_ENV_PREFIX = "SUPERJEV_ARM_"
#: Path to a JSON file of per-arm modes.
ARMS_CONFIG_ENV = "SUPERJEV_ARMS_CONFIG"
#: `SUPERJEV_ARMS=1` makes the gate run its migrated arms through here
#: instead of calling them inline. Off by default: with the switch off,
#: nothing in this package is consulted and the gate behaves exactly as
#: it did before the package existed.
ARMS_SWITCH_ENV = "SUPERJEV_ARMS"
#: Extra directories to discover arms in, os.pathsep-separated. Used by
#: the tests so a test double is never written into the shipped package
#: directory; usable for a private local arm too.
ARMS_EXTRA_DIR_ENV = "SUPERJEV_ARMS_EXTRA_DIR"

#: The header the contributed-evidence block is written under, when an
#: arm's `contribute` put lines in front of the judge.
CONTRIBUTED_HEADER = "[contributed by check arms]"

_PKG_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Verdict:
    """One arm's finding about one draft.

    `reason` is the line a human reads and the line the gate blocks on —
    keep it one sentence, and make it name the contradiction, not the
    check. `explain` is optional detail for a log or a `--json` payload;
    it is never required to be present and never parsed.
    """
    arm: str
    decision: str            # "block" | "advisory"
    reason: str
    explain: str = ""
    #: Anything structured the arm wants to hand back. Not parsed here.
    detail: dict = field(default_factory=dict, compare=False)

    def is_block(self):
        return self.decision == "block"


@dataclass(frozen=True)
class ArmSpec:
    """A discovered arm: its declared metadata, its `check`, and its
    optional `contribute`."""
    name: str
    kind: str                # "deterministic" | "judge"
    default_mode: str
    check: object
    module: str
    #: `contribute(window, draft, ctx) -> list[str]`, or None.
    contribute: object = None
    #: The file this arm was discovered in.
    path: str = ""

    def __call__(self, window, draft, ctx):
        return self.check(window, draft, ctx)


@dataclass(frozen=True)
class ArmRun:
    """The LEDGER of one `run_arms` call — what was consulted, and what
    broke, separately.

    `consulted` is `(name, mode)` for every arm actually run, so a caller
    can log what was asked rather than inferring it from what fired.
    `errors` is `(name, exception class name)` for every arm that raised:
    a raising blocking arm still fails open, but it is no longer
    invisible. `kinds` maps a consulted arm to its `KIND`, which is what
    `judge_only_blocks` needs. `judge_calls` is how many times the
    memoised judge accessor actually performed a classify — 0 or 1.
    """
    consulted: tuple = ()
    errors: tuple = ()
    kinds: dict = field(default_factory=dict)
    contributed: tuple = ()
    judge_calls: int = 0

    @property
    def names(self):
        return [n for n, _m in self.consulted]

    def as_ledger(self):
        """`(arms, arm_errors)` in the shape the catch ledger writes:
        `["pr_state:block", ...]` and `["flaky:RuntimeError", ...]`."""
        return ([f"{n}:{m}" for n, m in self.consulted],
                [f"{n}:{e}" for n, e in self.errors])


class JudgeEvidence:
    """The window the judge sees when an arm contributed lines to it.

    Duck-types `render()`, which is the only thing `judges` asks of a
    window, so a backend needs to know nothing about this class. The
    underlying `Window` is kept on `.window` and is never mutated: a
    contribution is extra evidence for THIS run, not an edit to the
    window's identity (the round-trip property `from_text(render(w)) == w`
    would stop holding if it were).
    """

    def __init__(self, window, lines, header=CONTRIBUTED_HEADER):
        self.window = window
        self.lines = tuple(lines or ())
        self.header = header

    def render(self):
        base = ""
        if self.window is not None:
            render = getattr(self.window, "render", None)
            base = render() if callable(render) else str(self.window)
        if not self.lines:
            return base
        block = "\n".join([self.header, *self.lines])
        return f"{base}\n\n===\n\n{block}\n" if base else block + "\n"

    def __getattr__(self, item):
        # Every Window query an arm might make still works through here,
        # so an arm handed a JudgeEvidence is not handed a lesser object.
        # `window` itself is a real attribute; guarding on it keeps a
        # half-built instance from recursing here forever.
        if item == "window":
            raise AttributeError(item)
        return getattr(self.window, item)


# ------------------------------------------------------------- discovery

def search_dirs(extra=None):
    """Every directory arms are discovered in: this package, then extras.

    `extra` is a path, or an iterable of paths, and is appended to
    whatever `SUPERJEV_ARMS_EXTRA_DIR` names. Order matters only for the
    duplicate rule below: the package directory wins a stem collision,
    because a shipped arm must not be shadowed by a loose file.
    """
    dirs = [_PKG_DIR]
    raw = os.environ.get(ARMS_EXTRA_DIR_ENV) or ""
    parts = [p for p in raw.split(os.pathsep) if p.strip()]
    if extra:
        parts.extend([extra] if isinstance(extra, (str, Path)) else list(extra))
    for part in parts:
        try:
            d = Path(str(part)).expanduser().resolve()
        except OSError:                                   # pragma: no cover
            continue
        if d not in dirs and d.is_dir():
            dirs.append(d)
    return dirs


def _arm_files(extra=None):
    """`{stem: path}` for every candidate arm module on the search path."""
    found = {}
    for d in search_dirs(extra):
        for p in sorted(d.glob("*.py")):
            if p.stem.startswith("_") or p.stem in found:
                continue
            found[p.stem] = p
    return found


def arm_names(extra=None):
    """Every arm module name on the search path, sorted.

    Listing the directories IS the registry. Nothing here knows the name
    of any particular arm.
    """
    return sorted(_arm_files(extra))


def _import_arm(stem, path):
    """The module for one arm file, imported under `arms.<stem>`.

    A file inside the package goes through the normal import machinery. A
    file in an extra directory is loaded from its path but still
    registered as `arms.<stem>`, so `from . import Verdict` inside it
    resolves exactly as it does for a shipped arm.
    """
    mod_name = f"{__name__}.{stem}"
    if path.parent == _PKG_DIR:
        return importlib.import_module(mod_name)
    cached = sys.modules.get(mod_name)
    if cached is not None and getattr(cached, "__file__", None) == str(path):
        return cached
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:               # pragma: no cover
        raise ImportError(f"no loader for {path}")
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = __name__
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(mod_name, None)
        raise
    return mod


def load_arm(name, extra=None, _files=None):
    """The `ArmSpec` for one arm, or None if the module is not a valid arm.

    A module that is missing `check`, whose `check` is not callable, or
    whose `NAME` is not its own file stem, is not an arm — it is skipped
    with one stderr line rather than raising, so a half-written file on
    the search path cannot break a live gate.
    """
    stem = str(name)
    files = _arm_files(extra) if _files is None else _files
    path = files.get(stem)
    if path is None:
        _warn(f"arms: no arm module named {stem!r} on the search path — skipped")
        return None
    try:
        mod = _import_arm(stem, path)
    except Exception as e:
        _warn(f"arms: cannot import {stem!r} ({e!r}) — skipped")
        return None
    check = getattr(mod, "check", None)
    if not callable(check):
        _warn(f"arms: {stem!r} has no callable check() — skipped")
        return None
    declared_name = getattr(mod, "NAME", stem)
    if declared_name != stem:
        # Names and stems are one keyspace. If they could differ, the env
        # key, the config key, `names=[...]` and the file on disk would be
        # four keyspaces pretending to be one.
        _warn(f"arms: {path} declares NAME={declared_name!r} but its module "
              f"stem is {stem!r} — an arm's NAME must be its file stem, "
              "skipped")
        return None
    declared = getattr(mod, "DEFAULT_MODE", DEFAULT_MODE)
    if declared not in MODES:
        _warn(f"arms: {stem!r} declares DEFAULT_MODE={declared!r}, which is not "
              f"one of {'/'.join(MODES)} — using {DEFAULT_MODE!r}")
        declared = DEFAULT_MODE
    kind = getattr(mod, "KIND", KIND_DETERMINISTIC)
    if kind not in KINDS:
        _warn(f"arms: {stem!r} declares KIND={kind!r}, which is not one of "
              f"{'/'.join(KINDS)} — treating it as {KIND_DETERMINISTIC!r}")
        kind = KIND_DETERMINISTIC
    contribute = getattr(mod, "contribute", None)
    if contribute is not None and not callable(contribute):
        _warn(f"arms: {stem!r} has a non-callable contribute — ignored")
        contribute = None
    return ArmSpec(name=stem, kind=kind, default_mode=declared, check=check,
                   module=stem, contribute=contribute, path=str(path))


def load_arms(names=None, extra=None):
    """Every valid arm, in name order. `names` narrows it, using the same
    keyspace as the file stems (see `load_arm`)."""
    files = _arm_files(extra)
    wanted = sorted(files) if names is None else list(names)
    out = []
    for n in wanted:
        spec = load_arm(n, extra=extra, _files=files)
        if spec is not None:
            out.append(spec)
    return out


# ------------------------------------------------------------------ modes

# One stderr line per (arm, bad value) per process. A hook runs this on
# every event; a typo'd env var must say so, once, not on every turn.
_warned = set()


def reset_mode_warnings():
    """Forget which warnings have been printed (tests)."""
    _warned.clear()


def _warn(line):
    key = line
    if key in _warned:
        return
    _warned.add(key)
    print(line, file=sys.stderr)


def _config_modes():
    """The `{arm: mode}` map out of `SUPERJEV_ARMS_CONFIG`, or {}.

    An unreadable or malformed config file is one warning and an empty
    map — never an exception. A config file is a convenience; losing it
    must not cost us the gate.
    """
    path = os.environ.get(ARMS_CONFIG_ENV)
    if not path:
        return {}
    try:
        raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        _warn(f"arms: cannot read {ARMS_CONFIG_ENV}={path!r} ({e.__class__.__name__}) "
              "— per-arm config ignored")
        return {}
    if isinstance(raw, dict) and isinstance(raw.get("arms"), dict):
        raw = raw["arms"]
    if not isinstance(raw, dict):
        _warn(f"arms: {ARMS_CONFIG_ENV}={path!r} is not a JSON object of "
              "arm->mode — per-arm config ignored")
        return {}
    return {str(k): v for k, v in raw.items()}


def arm_mode(spec_or_name, default=None):
    """The effective mode for one arm: env, else config file, else default.

    `spec_or_name` is an `ArmSpec` (whose `default_mode` is the fallback)
    or a bare name (fallback `default`, else the package default).
    """
    if isinstance(spec_or_name, ArmSpec):
        name = spec_or_name.name
        fallback = spec_or_name.default_mode
    else:
        name = str(spec_or_name)
        fallback = default or DEFAULT_MODE
    if fallback not in MODES:
        fallback = DEFAULT_MODE

    env_key = ARM_MODE_ENV_PREFIX + name.upper()
    raw = os.environ.get(env_key)
    source = env_key
    if raw is None:
        raw = _config_modes().get(name)
        source = f"{ARMS_CONFIG_ENV}[{name}]"
    if raw is None:
        return fallback
    val = str(raw).strip().lower()
    if val in MODES:
        return val
    _warn(f"arms: {source}={raw!r} is not one of {'/'.join(MODES)} — "
          f"using {name}'s default mode {fallback!r}")
    return fallback


def arms_enabled():
    """Is the registry switched on for the gate? (`SUPERJEV_ARMS=1`)

    Anything in 1/true/yes/on, case-insensitive, is on. Everything else,
    including unset, is off — this switch defaults to the legacy inline
    path on purpose.
    """
    return str(os.environ.get(ARMS_SWITCH_ENV, "")).strip().lower() in (
        "1", "true", "yes", "on")


# ------------------------------------------------------------- the judge

def make_judge_accessor(window, draft, judge=None):
    """A zero-argument callable returning ONE `JudgeResult`, memoised.

    The first call performs the classify; every later call returns the
    same object, so N judge arms in one run cost one model call and all
    N read the same answer. Never called at all means never classified —
    which is how a run with no judge arm makes no judge call.

    `judge` is an explicit backend (a test's fake); with it None the
    backend comes from `judges.get_judge()`, imported lazily so a run
    with no judge arm does not even import the package.
    """
    box = {}

    def accessor():
        if "result" not in box:
            j = judge
            if j is None:
                from judges import get_judge                # noqa: PLC0415
                j = get_judge()
            box["result"] = j.classify(draft, window)
            accessor.calls += 1
        return box["result"]

    accessor.calls = 0
    return accessor


def judge_only_blocks(verdicts, kinds):
    """The gate's judge-advisory failsafe rule, in registry terms.

    True when there IS at least one blocking verdict and EVERY blocking
    verdict came from an arm whose `KIND` is `judge`. That is the whole
    rule, and stating it here rather than in the gate is the point: the
    gate no longer has to know which judge arm fired, only whether
    anything deterministic is in the mix.

    `kinds` is `{arm name: KIND}` — `ArmRun.kinds` for a registry run.
    An unknown arm name is treated as deterministic, so an arm the map
    forgot fails CLOSED (its block is never demoted).
    """
    blocking = [v for v in (verdicts or []) if v.is_block()]
    if not blocking:
        return False
    return all(kinds.get(v.arm, KIND_DETERMINISTIC) == KIND_JUDGE
               for v in blocking)


# ------------------------------------------------------------------- run

def run_arms(window, draft, ctx=None, names=None, extra=None, judge=None):
    """Run the arms over one draft and collect their verdicts.

    Returns `(verdicts, run)`: the `Verdict`s in arm-name order, and one
    `ArmRun` recording what was consulted, what raised, what was
    contributed and whether the judge was called.

    Two phases, evidence then check — see this module's docstring. An arm
    in `off` mode is in neither. An arm in `advisory` mode has its
    verdict's decision rewritten to "advisory". An arm that raises in
    either phase is one stderr line, no verdict, and one entry in
    `run.errors`: a broken check must never be the reason a reply cannot
    be sent, but it must not be silent either.
    """
    ctx = {} if ctx is None else dict(ctx)
    specs = [(s, arm_mode(s)) for s in load_arms(names, extra=extra)]
    specs = [(s, m) for s, m in specs if m != "off"]
    consulted = [(s.name, m) for s, m in specs]
    kinds = {s.name: s.kind for s, _m in specs}
    errors = []

    # ---- phase 1: the evidence phase (no judge in ctx, on purpose)
    ev_ctx = dict(ctx)
    ev_ctx.pop("judge", None)
    contributed = []
    for spec, _mode in specs:
        if spec.contribute is None:
            continue
        try:
            lines = spec.contribute(window, draft, ev_ctx)
        except Exception as e:
            _warn(f"arms: {spec.name}.contribute() raised {e!r} — contributed "
                  "nothing for this draft")
            errors.append((spec.name, e.__class__.__name__))
            continue
        if not lines:
            continue
        if isinstance(lines, str) or not hasattr(lines, "__iter__"):
            _warn(f"arms: {spec.name}.contribute() returned "
                  f"{type(lines).__name__}, not a list of lines — ignored")
            continue
        contributed.extend(str(x) for x in lines)

    judge_window = JudgeEvidence(window, contributed) if contributed else window

    # ---- phase 2: the check phase, with ONE memoised judge behind ctx
    accessor = ctx.get("judge")
    if not callable(accessor):
        accessor = make_judge_accessor(judge_window, draft, judge=judge)
    ctx["judge"] = accessor
    ctx.setdefault("judge_window", judge_window)
    ctx.setdefault("contributed_lines", tuple(contributed))

    verdicts = []
    for spec, mode in specs:
        try:
            v = spec.check(window, draft, ctx)
        except Exception as e:
            _warn(f"arms: {spec.name}.check() raised {e!r} — arm skipped for "
                  "this draft")
            errors.append((spec.name, e.__class__.__name__))
            continue
        if v is None:
            continue
        if not isinstance(v, Verdict):
            _warn(f"arms: {spec.name}.check() returned {type(v).__name__}, "
                  "not a Verdict — ignored")
            continue
        if mode == "advisory" and v.decision != "advisory":
            v = Verdict(arm=v.arm, decision="advisory", reason=v.reason,
                        explain=v.explain, detail=v.detail)
        verdicts.append(v)

    run = ArmRun(consulted=tuple(consulted), errors=tuple(errors), kinds=kinds,
                 contributed=tuple(contributed),
                 judge_calls=getattr(accessor, "calls", 0))
    return verdicts, run


def block_reasons(window, draft, ctx=None, names=None, extra=None, judge=None):
    """Just the blocking reason lines, for a caller that wants the same
    shape `deterministic_block_reasons` already returns."""
    verdicts, _run = run_arms(window, draft, ctx=ctx, names=names, extra=extra,
                              judge=judge)
    return [v.reason for v in verdicts if v.is_block()]
