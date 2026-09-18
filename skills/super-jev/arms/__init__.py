"""The arm registry — super-jev's plug-in layer for CHECK ARMS.

An arm is one check. It looks at a draft and the evidence window behind
it and either says nothing or returns one `Verdict`. That is the whole
contract, and it is deliberately the whole contract: an arm is one file
with one function, so adding a check never means editing the gate.

    skills/super-jev/arms/<name>.py

        NAME = "my_check"                 # registry key, also the env key
        KIND = "deterministic"            # or "judge"
        DEFAULT_MODE = "block"            # or "advisory" / "off"

        def check(window, draft, ctx):
            ...
            return None                   # nothing to say
            return Verdict(arm=NAME, decision="block", reason="...",
                           explain="...")

`check` returns `None` or a `Verdict`. It MUST NOT raise; the registry
catches anyway (an arm bug must never take the gate down) but an arm that
leans on that is a broken arm.

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

DISCOVERY
---------
`arm_names()` lists the package directory. There is no hard-coded list of
arms anywhere: drop a file in, it is an arm. Files starting with `_` and
`__init__.py` itself are skipped, so private helpers are still possible.

See docs/plugins.md.
"""
import importlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Verdict", "ArmSpec", "MODES", "DEFAULT_MODE",
    "ARM_MODE_ENV_PREFIX", "ARMS_CONFIG_ENV", "ARMS_SWITCH_ENV",
    "arm_names", "load_arm", "load_arms", "arm_mode", "arms_enabled",
    "run_arms", "reset_mode_warnings",
]

#: The three modes an arm can be in.
MODES = ("block", "advisory", "off")
#: The mode an arm gets when it declares nothing legible itself.
DEFAULT_MODE = "block"

#: `SUPERJEV_ARM_PR_STATE=advisory` overrides the `pr_state` arm.
ARM_MODE_ENV_PREFIX = "SUPERJEV_ARM_"
#: Path to a JSON file of per-arm modes.
ARMS_CONFIG_ENV = "SUPERJEV_ARMS_CONFIG"
#: `SUPERJEV_ARMS=1` makes the gate run its migrated arms through here
#: instead of calling them inline. Off by default: with the switch off,
#: nothing in this package is consulted and the gate behaves exactly as
#: it did before the package existed.
ARMS_SWITCH_ENV = "SUPERJEV_ARMS"

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
    """A discovered arm: its declared metadata plus its `check`."""
    name: str
    kind: str                # "deterministic" | "judge"
    default_mode: str
    check: object
    module: str

    def __call__(self, window, draft, ctx):
        return self.check(window, draft, ctx)


# ------------------------------------------------------------- discovery

def arm_names():
    """Every arm module name in this package, sorted.

    Listing the directory IS the registry. Nothing here knows the name of
    any particular arm.
    """
    names = []
    for p in sorted(_PKG_DIR.glob("*.py")):
        stem = p.stem
        if stem.startswith("_"):
            continue
        names.append(stem)
    return names


def load_arm(name):
    """The `ArmSpec` for one arm, or None if the module is not a valid arm.

    A module that is missing `check`, or whose `check` is not callable, is
    not an arm — it is skipped rather than raising, so a half-written file
    in the package directory cannot break a live gate.
    """
    try:
        mod = importlib.import_module(f"{__name__}.{name}")
    except Exception as e:                                # pragma: no cover
        _warn(f"arms: cannot import {name!r} ({e!r}) — skipped")
        return None
    check = getattr(mod, "check", None)
    if not callable(check):
        _warn(f"arms: {name!r} has no callable check() — skipped")
        return None
    declared = getattr(mod, "DEFAULT_MODE", DEFAULT_MODE)
    if declared not in MODES:
        _warn(f"arms: {name!r} declares DEFAULT_MODE={declared!r}, which is not "
              f"one of {'/'.join(MODES)} — using {DEFAULT_MODE!r}")
        declared = DEFAULT_MODE
    return ArmSpec(name=getattr(mod, "NAME", name),
                   kind=getattr(mod, "KIND", "deterministic"),
                   default_mode=declared,
                   check=check,
                   module=name)


def load_arms(names=None):
    """Every valid arm, in name order. `names` narrows it."""
    wanted = arm_names() if names is None else list(names)
    out = []
    for n in wanted:
        spec = load_arm(n)
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


# ------------------------------------------------------------------- run

def run_arms(window, draft, ctx=None, names=None):
    """Run the arms over one draft and collect their verdicts.

    Returns `(verdicts, ran)`: the `Verdict`s in arm-name order, and the
    list of `(name, mode)` pairs actually run, so a caller can log what
    was consulted rather than guessing from what fired.

    An arm in `off` mode is not called. An arm in `advisory` mode has its
    verdict's decision rewritten to "advisory". An arm that raises is one
    stderr line and no verdict: a broken check must never be the reason a
    reply cannot be sent.
    """
    ctx = {} if ctx is None else ctx
    verdicts, ran = [], []
    for spec in load_arms(names):
        mode = arm_mode(spec)
        if mode == "off":
            continue
        ran.append((spec.name, mode))
        try:
            v = spec.check(window, draft, ctx)
        except Exception as e:                            # pragma: no cover
            _warn(f"arms: {spec.name}.check() raised {e!r} — arm skipped for "
                  "this draft")
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
    return verdicts, ran


def block_reasons(window, draft, ctx=None, names=None):
    """Just the blocking reason lines, for a caller that wants the same
    shape `deterministic_block_reasons` already returns."""
    verdicts, _ran = run_arms(window, draft, ctx=ctx, names=names)
    return [v.reason for v in verdicts if v.is_block()]
