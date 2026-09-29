"""The judge profile: the one place the judge model's limits are stated.

The table lives in judge_profiles.json (shared with Node: src/judge-profile.ts reads the
same file and applies the same rules). There is one judge today, so there is nothing to
select; an unknown name passed to load() is an error, and a missing or broken table stops
the run with one line.

Every judge-sized number is derived here, never written in place, and every size is
counted in ONE unit by ONE function, judge_tokens (bytes/2, rounded up, high on purpose):

    input_cap_tokens  window less the question battery's overhead (INPUT_CAP_HEADROOM)
    call_tokens       what one call's state plus longest question is budgeted at,
                      CALL_HEADROOM under the input cap
    max_questions     the per-call question cap
"""
import json
from dataclasses import dataclass
from pathlib import Path

PROFILES_PATH = Path(__file__).resolve().parent / "judge_profiles.json"

# The two headrooms are the gaps Jev's old constants had (32,768 -> 32,000 -> 30,000), kept as
# fixed amounts: exact for Jev, an assumption for another window until it is measured there.
# Room left under the window for the question battery's own overhead.
INPUT_CAP_HEADROOM = 768
# A call's budget sits this far under the input cap, since its token count is only an estimate.
CALL_HEADROOM = 2_000


def judge_tokens(value):
    """The one size unit: UTF-8 bytes / 2, rounded up. Deliberately high, so number-dense text
    (IDs, dates, amounts) is never under-counted. A non-string is counted as its compact JSON
    (the Node twin, src/judge-profile.ts judgeTokens, counts the same way)."""
    if value is None:
        return 0
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return (len(text.encode("utf-8")) + 1) // 2


def judge_tail(text, tokens):
    """The end of `text` that fits in `tokens` judge tokens (whole characters only)."""
    return (text or "").encode("utf-8")[-max(0, tokens) * 2:].decode("utf-8", "ignore")


@dataclass(frozen=True)
class JudgeProfile:
    name: str
    api_url: str
    model: str
    window_tokens: int
    max_questions_per_call: int
    input_usd_per_mtok: float
    max_parts: int
    confidence_line: float
    file_ceiling_bytes: int
    gate_window_tokens: int

    @property
    def input_cap_tokens(self):
        return self.window_tokens - INPUT_CAP_HEADROOM

    @property
    def call_tokens(self):
        return self.input_cap_tokens - CALL_HEADROOM

    @property
    def ceiling_text(self):
        """How a refusal names the window, e.g. "the 32,768-token ceiling"."""
        return f"the {self.window_tokens:,}-token ceiling"


def load(name=None, path=PROFILES_PATH):
    try:
        data = json.loads(Path(path).read_text())
        profiles = data["profiles"]
        want = name if name is not None else data["default"]
        p = profiles.get(want)
        if p is not None:
            return JudgeProfile(name=want, api_url=p["api_url"], model=p["model"],
                                window_tokens=int(p["window_tokens"]),
                                max_questions_per_call=int(p["max_questions_per_call"]),
                                input_usd_per_mtok=float(p["input_usd_per_mtok"]),
                                max_parts=int(p["max_parts"]), confidence_line=float(p["confidence_line"]),
                                file_ceiling_bytes=int(p["file_ceiling_bytes"]),
                                gate_window_tokens=int(p["gate_window_tokens"]))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise SystemExit(f"super-jev: cannot read the judge limits table {Path(path).name}: {e!r}")
    raise ValueError(f"unknown judge profile {want!r} (known: {', '.join(sorted(profiles))})")


PROFILE = load()
