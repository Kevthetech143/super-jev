"""A uuid is never a card number. About 1 random uuid4 in 8,000 has digit groups that form a Luhn-valid
16-digit run (mostly its first 8-4-4 window), so a uuid in a question, a uuid-named path or a log of uuids was
held at random. An RFC uuid (version 1-8, variant 8-b) is now scrubbed before the card check, in Python and
Node alike; real cards, including one written in uuid shape without the version/variant, are still held.
All data is made up. Needs node on PATH for the parity test.

    python3 -m pytest skills/super-jev/tests/test_uuid_not_card.py -q
"""
import json
import random
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parent.parent
sys.path.insert(0, str(SKILL))
from prepare_bulk import card_hit, has_secret  # noqa: E402

_rng = random.Random(4)
UUIDS = [str(uuid.UUID(int=_rng.getrandbits(128), version=4)) for _ in range(300_000)]


def _v7(ms: int, rng: random.Random) -> str:
    """A v7 (time-ordered) uuid: 48-bit ms timestamp, version 7, 12 random bits, variant 10, 62 random bits."""
    return str(uuid.UUID(int=ms << 80 | 7 << 76 | rng.getrandbits(12) << 64 | 2 << 62 | rng.getrandbits(62)))


_ms = 1_759_600_000_000  # a fixed 2025 timestamp, stepped forward like a log
V7 = [_v7(_ms + i * 37 + _rng.randrange(37), _rng) for i in range(100_000)]
# Known hits before the fix: last-window (9183-059972976233) and first-window (8-4-4 digits, Luhn-valid).
FIXED = ["d4b9227c-4460-444a-9183-059972976233", "81978559-1669-4329-9c4e-1b7f0a2d5e3c",
         "80515908-3116-4877-a6c6-aa5f447edff1", "80515908-3116-4877-A6C6-AA5F447EDFF1"]
BLOB = json.dumps([{"id": u, "status": "ok"} for u in FIXED + UUIDS[:496]], indent=1)
BLOB_V7 = json.dumps([{"id": u, "status": "ok"} for u in V7[:500]], indent=1)
NOT_HELD = [
    f"what happened to job {FIXED[1]} yesterday?",
    f"/var/log/runs/{FIXED[1]}/out.json",
    f"runs/run-{FIXED[0]}.log",
    f"attempt_{FIXED[2]}_retry",
    f"what happened to job {V7[0]} yesterday?",
    f"/var/log/runs/{V7[1]}/out.json",
    BLOB,
    BLOB_V7,
]
CARDS = [
    "4000056655665556",                                  # plain
    "card 4000 0566 5566 5556",                          # spaced
    "5500-0000-0000-0004",                               # dashed
    "order 1234 4000 0566 5566 5556",                    # later window
    "amex 3400-000000-00009 and card 4000 0566 5566 5556",  # Amex + card
    "amex 3400-000000-00009",
    "40000566-5566-5556-0000-000000000000",              # uuid shape, variant 0: not a uuid, held
    "40000566-5566-0557-8000-000000000000",              # uuid shape, version 0: not a uuid, held
    f"job {FIXED[1]} card 4000 0566 5566 5556",           # a card beside a uuid
    f"{FIXED[0]} 4000056655665556",
]


def test_random_and_known_uuids_never_held():
    assert [u for u in FIXED + UUIDS + V7 if has_secret(u)] == []


def test_uuid_in_prose_path_and_json_blob_not_held():
    assert [t for t in NOT_HELD if has_secret(t)] == []
    assert not card_hit(f"/data/{FIXED[1]}.csv")  # the raw prepare-time line check


def test_real_cards_still_held():
    assert [t for t in CARDS if not has_secret(t)] == []


def _node(inputs):
    js = ("import {hasSecret} from %s; import fs from 'node:fs';"
          "console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(hasSecret)));"
          % json.dumps(str(REPO / "src" / "secret-scan.ts")))
    out = subprocess.run(["node", "--input-type=module", "-e", js], input=json.dumps(inputs),
                         capture_output=True, text=True, check=True, cwd=REPO)
    return json.loads(out.stdout)


@pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")
def test_python_and_node_agree():
    inputs = FIXED + UUIDS[:20_000] + V7[:20_000] + NOT_HELD + CARDS
    py, js = [has_secret(s) for s in inputs], _node(inputs)
    assert [s for s, a, b in zip(inputs, py, js) if a != b] == []
