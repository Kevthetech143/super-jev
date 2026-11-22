"""Frozen danger set for harness-only asks (made-up notes, no live calls, no judge).
Each case is a way the word-search-only path could hand back something unsafe: another person's note, a
superseded note ahead of the current one, a hold/do-not note left out beside the note it overrides, or a
no-answer question that still lists files. Cases that fail on today's code are xfail(strict) so a fix flips them.
The harness is the one in test_harness_only_outcome.py."""
import importlib.util
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("harness_outcome_mod", SKILL / "tests" / "test_harness_only_outcome.py")
_h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_h)

NOISE = [f"Note {i}: the garden hose, the oven timer and the recycling pickup are on the fridge list." for i in range(8)]

CASES = {
    # id: (question, {file: text}, check)
    "wrong-person": ("what medicine does Bob take every morning",
                     {"alice-meds.md": "# Alice\nAlice takes lisinopril every morning with water.\n",
                      "bob-meds.md": "# Bob\nBob takes metformin every morning with breakfast.\n"},
                     lambda f: f and f[0] == "bob-meds.md"),
    "superseded-not-first": ("what is the current parking permit rule",
                             {"parking-2023.md": "# Parking permit rule\nSUPERSEDED. The parking permit rule was: permit on the dash.\n",
                              "parking-current.md": "# Parking permit rule\nCurrent parking permit rule: permit hangs from the mirror.\n"},
                             lambda f: f and f[0] == "parking-current.md"),
    "stopped-not-current": ("which memberships do I have now",
                            {"memberships-active.md": "# Memberships\nActive memberships now: library and the pool.\n",
                             "gym-membership.md": "# Gym\nCancelled. The gym membership ended last year; stopped billing.\n"},
                            lambda f: f and f[0] == "memberships-active.md"),
    "hold-flag-kept": ("when should I refill the Zeta prescription",
                       {"zeta-refill.md": "# Zeta\nZeta prescription refill every 30 days at the corner pharmacy.\n",
                        "zeta-hold.md": "# Zeta HOLD\nDo not refill the Zeta prescription until the doctor calls.\n"},
                       lambda f: "zeta-hold.md" in f),
    "except-note-kept": ("is the office open on Sunday",
                         {"office-hours.md": "# Office hours\nThe office is open every day except Sunday.\n"},
                         lambda f: f == ["office-hours.md"]),
    "absent-neighbour-topic": ("what is the next orthodontist appointment",
                               {"dentist.md": "# Dentist\nDr Park, dental cleaning, appointment every six months at the main office.\n"},
                               lambda f: f == []),
    "absent-value-missing": ("what is Sam's passport number",
                             {"passport.md": "# Passport\nSam's passport renewal is due in May; the form is in the blue folder.\n"},
                             lambda f: f == []),
    "absent-no-note": ("what is the wifi password at the cabin",
                       {},
                       lambda f: f == []),
}
KNOWN_GAPS = {"absent-neighbour-topic", "absent-value-missing"}  # lexical coverage cannot tell a related note from the answer


@pytest.mark.parametrize("cid", [pytest.param(c, marks=pytest.mark.xfail(strict=True)) if c in KNOWN_GAPS else c for c in CASES])
def test_danger_case(cid, tmp_path, monkeypatch):
    question, notes, ok = CASES[cid]
    files = []
    for name, text in {**notes, **{f"noise{i}.md": t for i, t in enumerate(NOISE)}}.items():
        p = tmp_path / name
        p.write_text(text)
        files.append(p)
    rc, res = _h._lookup(tmp_path, question, files, monkeypatch=monkeypatch)
    got = [Path(x["path"]).name for x in res.get("files") or []]
    assert ok(got), (res.get("outcome"), got)
