"""The judge profile: the one place the judge model's limits are stated.

The profiles live in judge_profiles.json (shared with Node: src/judge-profile.ts
reads the same file and applies the same rules). SUPERJEV_JUDGE_PROFILE picks
one; unset, the file's default (TypeSafe Jev) is used. An unknown name falls
back to the default and prints one stderr line, as SUPERJEV_JUDGE does.

Every judge-sized number is derived here from the profile, never written in
place:

    input_cap_tokens  window less the question battery's overhead (INPUT_CAP_HEADROOM)
    call_tokens       what one call's state plus longest question is budgeted at,
                      estimated high (bytes/2) and CALL_HEADROOM under the input cap
    max_questions     the per-call question cap
    call_usd          what one full call costs at the input price
"""
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

PROFILE_ENV = "SUPERJEV_JUDGE_PROFILE"
PROFILES_PATH = Path(__file__).resolve().parent / "judge_profiles.json"

# The two headrooms are the gaps Jev's old constants had (32,768 -> 32,000 -> 30,000), kept as
# fixed amounts: exact for Jev, an assumption for another window until it is measured there.
# Room left under the window for the question battery's own overhead.
INPUT_CAP_HEADROOM = 768
# A call's budget sits this far under the input cap, since its token count is only an estimate.
CALL_HEADROOM = 2_000


@dataclass(frozen=True)
class JudgeProfile:
    name: str
    label: str
    window_tokens: int
    max_questions_per_call: int
    question_kinds: tuple
    input_usd_per_mtok: float
    output_usd_per_mtok: float

    @property
    def input_cap_tokens(self):
        return self.window_tokens - INPUT_CAP_HEADROOM

    @property
    def call_tokens(self):
        return self.input_cap_tokens - CALL_HEADROOM

    @property
    def call_usd(self):
        return self.call_tokens * self.input_usd_per_mtok / 1_000_000

    @property
    def ceiling_text(self):
        """How a refusal names the window, e.g. "the 32,768-token ceiling"."""
        return f"the {self.window_tokens:,}-token ceiling"


def load(name=None, path=PROFILES_PATH):
    data = json.loads(Path(path).read_text())
    profiles = data["profiles"]
    want = name if name is not None else (os.environ.get(PROFILE_ENV) or data["default"])
    if want not in profiles:
        print(f"super-jev: unknown {PROFILE_ENV}={want!r}; using {data['default']!r} "
              f"(known: {', '.join(sorted(profiles))})", file=sys.stderr)
        want = data["default"]
    p = profiles[want]
    return JudgeProfile(name=want, label=p["label"], window_tokens=int(p["window_tokens"]),
                        max_questions_per_call=int(p["max_questions_per_call"]),
                        question_kinds=tuple(p["question_kinds"]),
                        input_usd_per_mtok=float(p["input_usd_per_mtok"]),
                        output_usd_per_mtok=float(p.get("output_usd_per_mtok", 0)))


PROFILE = load()
