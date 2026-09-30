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
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path

PROFILES_PATH = Path(__file__).resolve().parent / "judge_profiles.json"
#: The one setting that picks the judge, in both languages.
JUDGE_ENV = "SUPERJEV_JUDGE"
#: The test judge: not a profile, it uses the default profile's numbers.
FAKE_JUDGE = "fake"


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
    # calibration: measured for one judge, re-measured for another
    confidence_line: float
    sure_line: float
    confirm_floor: float
    source_floor: float
    claim_content_floor: float
    route_floor: float
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
_FLOAT = ("input_usd_per_mtok", "confidence_line", "sure_line", "confirm_floor", "source_floor",
          "claim_content_floor", "route_floor", "preflight_strong", "outcome_line")
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


def load(name=None, path=None):
    """The profile named `name`; else SUPERJEV_JUDGE; else the table's default."""
    path = path or PROFILES_PATH
    default, profiles = _read(path)
    want = name if name is not None else (os.environ.get(JUDGE_ENV) or "").strip() or default
    key = _resolve(want, default, profiles)
    p = profiles[key]
    try:
        vals = {**{k: int(p[k]) for k in _INT}, **{k: float(p[k]) for k in _FLOAT}, **{k: str(p[k]) for k in _STR},
                "overloaded_statuses": tuple(int(c) for c in p["overloaded_statuses"])}
    except (KeyError, ValueError, TypeError) as e:
        raise SystemExit(f"super-jev: cannot read the judge limits table {Path(path).name}: {e!r}")
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
    opt["judge_name"] = opt.get("judge_name") or key
    opt["vendor"] = opt.get("vendor") or opt["judge_name"]
    return JudgeProfile(name=key, **vals, **opt)


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
