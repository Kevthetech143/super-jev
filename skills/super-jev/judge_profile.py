"""The judge profile: the one place the judge's limits, key name and calibration are stated.

The table lives in judge_profiles.json (shared with Node: src/judge-profile.ts reads the
same file and applies the same rules). SUPERJEV_JUDGE picks the profile (default: the file's
default; "fake" is the test judge and uses the default's numbers); an unknown name stops the
run with one line, and a missing or broken table does too.

Every judge-sized number is derived here, never written in place, and every size is
counted in ONE unit by ONE function, judge_tokens (bytes / the profile's bytes_per_token,
rounded up, high on purpose):

    input_cap_tokens  window less the question battery's overhead (input_cap_headroom)
    call_tokens       what one call's state plus longest question is budgeted at,
                      call_headroom under the input cap
    max_questions     the per-call question cap

A profile may also state context presets, how much of each note one content check reads. The profile as
written is the preset "small"; each row of its `contexts` table is a larger one and overrides only the
label, the window, the characters read per note and the check's timeout (the input cap and call budget
follow from the row's window). load(context=NAME) reads the profile at a preset; load() without one, and
so PROFILE, is always small, which keeps everything derived from PROFILE at start-up at small's numbers.
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

PROFILES_PATH = Path(__file__).resolve().parent / "judge_profiles.json"
#: The one setting that picks the judge, in both languages.
JUDGE_ENV = "SUPERJEV_JUDGE"
#: The test judge: not a profile, it uses the default profile's numbers.
FAKE_JUDGE = "fake"
#: The preset that is the profile exactly as written; the rows of its `contexts` table are the others.
SMALL = "small"
#: What a `contexts` row may override (label first, then whole numbers). Everything else a preset reports is the profile's own.
_ROW = ("label", "window_tokens", "read_chars", "nav_timeout_ms")


@dataclass(frozen=True)
class JudgeProfile:
    name: str
    kind: str
    key_env: str
    api_url: str
    model: str
    window_tokens: int
    max_questions_per_call: int
    input_usd_per_mtok: float
    max_parts: int
    file_ceiling_bytes: int
    gate_window_tokens: int
    bytes_per_token: int
    input_cap_headroom: int
    call_headroom: int
    overloaded_statuses: tuple
    retry_attempts: int
    retry_first_delay_ms: int
    too_big_status: int
    #: most characters of one note a single content check reads, and how long (ms) that check may take
    read_chars: int
    nav_timeout_ms: int
    #: one call's budget at small, whatever preset this is: what the long-context header rule measures a request against
    base_call_tokens: int
    # calibration: measured for one judge, re-measured for another
    confidence_line: float
    sure_line: float
    confirm_floor: float
    source_floor: float
    claim_content_floor: float
    preflight_strong: float
    outcome_line: float
    # optional wire fields (defaults keep the Jev behaviour)
    #: environment variable that, when set, replaces api_url ("" = none)
    api_url_env: str = ""
    #: False for a judge that takes no key (a local model): no key check, no Authorization header
    key_required: bool = True
    #: the reply field the confidence line reads; the pass line is only meaningful for this field
    confidence_field: str = "confidence"
    #: False until the calibration numbers are measured on this judge: it may answer, never save or approve
    calibrated: bool = True
    #: the judge's short name in error text ("" = the profile name) and its vendor name ("" = that name)
    judge_name: str = ""
    vendor: str = ""
    #: the context preset this profile is read at (small = as written), the name shown for it, and the preset used
    #: when no one picks one (the table's default_context; small when it says nothing)
    context: str = SMALL
    context_label: str = "Small"
    default_context: str = SMALL
    #: the [name, value] request header that lifts the judge's input limit past long_context_above_tokens (its
    #: normal limit); None = the profile never sends one
    long_context_header: Optional[Tuple[str, str]] = None
    long_context_above_tokens: Optional[int] = None

    @property
    def input_cap_tokens(self):
        return self.window_tokens - self.input_cap_headroom

    @property
    def call_tokens(self):
        return self.input_cap_tokens - self.call_headroom

    @property
    def ceiling_text(self):
        """How a refusal names the window, e.g. "the 32,768-token ceiling"."""
        return f"the {self.window_tokens:,}-token ceiling"


_INT = ("window_tokens", "max_questions_per_call", "max_parts", "file_ceiling_bytes", "gate_window_tokens",
        "bytes_per_token", "input_cap_headroom", "call_headroom", "retry_attempts", "retry_first_delay_ms",
        "too_big_status")
#: What the presets set per note, read through _whole (as strictly as Node reads it) rather than int().
_WHOLE = ("read_chars", "nav_timeout_ms")
_FLOAT = ("input_usd_per_mtok", "confidence_line", "sure_line", "confirm_floor", "source_floor",
          "claim_content_floor", "preflight_strong", "outcome_line")
_STR = ("kind", "key_env", "api_url", "model")


def _read(path):
    try:
        data = json.loads(Path(path).read_text())
        return data["default"], data["profiles"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise SystemExit(f"super-jev: cannot read the judge limits table {Path(path).name}: {e!r}")


def _resolve(want, default, profiles):
    """The profile key for a judge name: the name itself, an alias, or "fake" (the default's numbers)."""
    if want == FAKE_JUDGE:
        return default
    if want in profiles:
        return want
    for key, p in profiles.items():
        if want in (p.get("aliases") or []):
            return key
    raise ValueError(f"unknown judge {want!r} (known: {', '.join(sorted([*profiles, FAKE_JUDGE]))})")


def judge_names(path=None):
    """Every name SUPERJEV_JUDGE accepts."""
    default, profiles = _read(path or PROFILES_PATH)
    return sorted({*profiles, FAKE_JUDGE, *(a for p in profiles.values() for a in p.get("aliases") or [])})


def _bad(path, why):
    """A table that cannot be read stops the run with one line."""
    raise SystemExit(f"super-jev: cannot read the judge limits table {Path(path).name}: {why}")


def _whole(path, what, value):
    """A whole number as the table writes it, read exactly as Node's Number.isInteger reads it: 56000 and
    56000.0 are whole numbers; "56000", 56000.5 and true are not (int() would take all three) and stop the run."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    _bad(path, f"{what} must be a whole number")


def _choose(names):
    """The names as a choice: "small", "small or medium", "small, medium or large"."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"


def _presets(p, path):
    """The presets a profile states, in table order: {name: what that preset sets}. small is the profile as
    written (an empty row, nothing overridden); a row of `contexts` sets only the _ROW keys. A label that is
    not text, or none, shows as the preset's name with a capital."""
    rows = p.get("contexts", {})
    if not isinstance(rows, dict):
        _bad(path, "contexts must be a table of rows")
    if SMALL in rows:
        _bad(path, f"contexts has a {SMALL} row, but {SMALL} is the profile as written")
    presets = {}
    for name, row in {SMALL: {}, **rows}.items():
        if not isinstance(row, dict) or set(row) - set(_ROW):
            _bad(path, f"contexts.{name} may only set {', '.join(_ROW)}")
        label = row.get("label")
        presets[name] = {"context_label": label if isinstance(label, str) and label else name[:1].upper() + name[1:],
                         **{k: _whole(path, f"contexts.{name}.{k}", row[k]) for k in _ROW[1:] if k in row}}
    return presets


def load(name=None, path=None, context=None):
    """The profile named `name`; else SUPERJEV_JUDGE; else the table's default. `context` picks one of its
    presets; None is small, the profile as written, never default_context (that one is for the setting to
    read). A preset the profile does not state is a ValueError that names the ones it does."""
    path = path or PROFILES_PATH
    default, profiles = _read(path)
    want = name if name is not None else (os.environ.get(JUDGE_ENV) or "").strip() or default
    key = _resolve(want, default, profiles)
    p = profiles[key]
    try:
        vals = {**{k: int(p[k]) for k in _INT}, **{k: _whole(path, k, p[k]) for k in _WHOLE},
                **{k: float(p[k]) for k in _FLOAT}, **{k: str(p[k]) for k in _STR},
                "overloaded_statuses": tuple(int(c) for c in p["overloaded_statuses"])}
    except (KeyError, ValueError, TypeError) as e:
        raise SystemExit(f"super-jev: cannot read the judge limits table {Path(path).name}: {e!r}")
    vals["base_call_tokens"] = vals["window_tokens"] - vals["input_cap_headroom"] - vals["call_headroom"]
    presets = _presets(p, path)
    default_context = p.get("default_context", SMALL)
    if not isinstance(default_context, str) or default_context not in presets:
        _bad(path, f"default_context {json.dumps(default_context)} is not one of {_choose(list(presets))}")
    context = SMALL if context is None else context
    if context not in presets:
        raise ValueError(f"unknown context {json.dumps(context, ensure_ascii=False)} (choose {_choose(list(presets))})")
    vals.update(presets[context])
    opt = {}
    if "api_url_env" in p:
        opt["api_url_env"] = str(p["api_url_env"])
    if "key_required" in p:
        if not isinstance(p["key_required"], bool):
            raise SystemExit(f"super-jev: cannot read the judge limits table {Path(path).name}: key_required must be true or false")
        opt["key_required"] = p["key_required"]
    for f in ("judge_name", "vendor"):
        if f in p:
            opt[f] = str(p[f])
    if "calibrated" in p:
        if not isinstance(p["calibrated"], bool):
            raise SystemExit(f"super-jev: cannot read the judge limits table {Path(path).name}: calibrated must be true or false")
        opt["calibrated"] = p["calibrated"]
    if "confidence_field" in p:
        opt["confidence_field"] = str(p["confidence_field"])
    if "long_context_header" in p:
        h = p["long_context_header"]
        if not (isinstance(h, list) and len(h) == 2 and all(isinstance(s, str) and s for s in h)):
            _bad(path, "long_context_header must be [name, value]")
        if "long_context_above_tokens" not in p:
            _bad(path, "long_context_header needs long_context_above_tokens")
        opt["long_context_above_tokens"] = _whole(path, "long_context_above_tokens", p["long_context_above_tokens"])
        opt["long_context_header"] = (h[0], h[1])
    opt["judge_name"] = opt.get("judge_name") or key
    opt["vendor"] = opt.get("vendor") or opt["judge_name"]
    return JudgeProfile(name=key, context=context, default_context=default_context, **vals, **opt)


def _from_env():
    try:
        return load()
    except ValueError as e:
        raise SystemExit(f"super-jev: {JUDGE_ENV}: {e}")


PROFILE = _from_env()


def judge_tokens(value):
    """The one size unit: UTF-8 bytes / bytes_per_token, rounded up. Deliberately high, so number-dense
    text (IDs, dates, amounts) is never under-counted. A non-string is counted as its compact JSON
    (the Node twin, src/judge-profile.ts judgeTokens, counts the same way)."""
    if value is None:
        return 0
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    n = PROFILE.bytes_per_token
    return (len(text.encode("utf-8")) + n - 1) // n


def judge_tail(text, tokens):
    """The end of `text` that fits in `tokens` judge tokens (whole characters only)."""
    return (text or "").encode("utf-8")[-max(0, tokens) * PROFILE.bytes_per_token:].decode("utf-8", "ignore")
