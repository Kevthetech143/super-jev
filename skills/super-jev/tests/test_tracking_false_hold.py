"""A spaced USPS tracking number must not hold a notes file as card-like; real cards are still held.

The card pattern reads the first 16 digits of a 22-digit number written in groups of 4 as a card,
and about one in ten such numbers passes Luhn there, so one line held a whole 150 KB notes file. Only a whole
spaced/dashed run that is a valid USPS IMpb number (22 or 26 digits, 91-95, GS1 check digit) is exempt.
Python and Node must agree on every case. Needs node on PATH for the parity test; no network.

    python3 -m pytest skills/super-jev/tests/test_tracking_false_hold.py -q
"""
import json
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parent.parent
sys.path.insert(0, str(SKILL))
from prepare_bulk import CARD_RE, _usps_check_ok, _usps_tracking, card_hit, has_secret, secret_detail  # noqa: E402


def _gs1(body: str) -> str:
    total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(body)))
    return str((10 - total % 10) % 10)


def _luhn_ok(d: str) -> bool:
    total = 0
    for i, c in enumerate(reversed(d)):
        n = int(c) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def _spaced(d: str, sep: str = " ") -> str:
    return sep.join(d[i:i + 4] for i in range(0, len(d), 4))


def _card(rng, prefix="4") -> str:
    body = prefix + "".join(rng.choice("0123456789") for _ in range(15 - len(prefix)))
    return next(body + c for c in "0123456789" if _luhn_ok(body + c))


def _tracking(rng, n=22) -> str:
    body = rng.choice(["92", "93", "94", "95"]) + "".join(rng.choice("0123456789") for _ in range(n - 3))
    return body + _gs1(body)


def _held_by_window(d: str) -> bool:
    """A card-pattern match in the spaced number passes Luhn (what tripped the old scan)."""
    return any(_luhn_ok(re.sub(r"\D", "", m.group())) for m in CARD_RE.finditer(_spaced(d)))


def _old_card(text: str) -> bool:
    return any(_luhn_ok(re.sub(r"\D", "", m.group())) for m in CARD_RE.finditer(text))


EXAMPLE = "9302 2110 4790 0005 3721 11"  # made-up, same shape as the real one; valid check digit


def test_example_tracking_line_not_held():
    assert _held_by_window(EXAMPLE.replace(" ", ""))  # the old scan held it
    for line in [f"USPS tracking {EXAMPLE} delivered", EXAMPLE, EXAMPLE.replace(" ", "-"),
                 f"- 2026-09-20 order shipped, tracking: {EXAMPLE}."]:
        assert not has_secret(line), line


def test_notes_file_with_tracking_line_has_no_secret_detail(tmp_path):
    f = tmp_path / "pending.md"
    f.write_text("# Pending\n" + "- a fact about the order\n" * 50 + f"- tracking {EXAMPLE}\n")
    assert secret_detail(f) is None
    assert not has_secret(f.read_text())


def test_random_valid_tracking_numbers_never_held():
    rng = random.Random(22)
    nums = [_tracking(rng, rng.choice([22, 26])) for _ in range(2000)]
    assert sum(map(_held_by_window, nums)) > 100  # the case the old scan got wrong is common
    nums = [n for n in nums if not any(_luhn_ok(n[i:i + 16]) for i in range(4, len(n) - 15, 4))]
    assert len(nums) > 1500
    for sep in (" ", "-"):
        assert [n for n in nums if has_secret(f"tracking {_spaced(n, sep)} ok")] == []


def test_real_cards_still_held():
    rng = random.Random(16)
    cards = [_card(rng, rng.choice(["4", "51", "37", "6011", "9"])) for _ in range(2000)]
    assert [c for c in cards if not has_secret(f"card {_spaced(c)}")] == []
    assert [c for c in cards if not has_secret(f"card {c}")] == []


def test_card_near_or_inside_a_digit_run_still_held():
    rng = random.Random(4)
    card, trk = _spaced(_card(rng)), _spaced(_tracking(rng))
    for line in [f"{card} 123", f"{card} 12 27", f"{card} 0927 123",  # card then cvv/expiry
                 f"{card} {trk}", f"{trk} {card}",  # card glued to a tracking run
                 f"tracking {trk}, card {card}",  # separate numbers on one line
                 f"{trk}x {card}"]:
        assert has_secret(line), line


def test_short_91_95_number_before_a_card_never_hides_it():
    # Found in review: a loose run rule read "9100000001 4000 0566 5566 5556" (26 digits) as a tracking
    # number whenever the card's last digit happened to be a valid check digit. Only USPS layout counts.
    rng = random.Random(26)
    for line in ["call 9100000001 4000 0566 5566 5556 thanks", "ref 920000 4000 0566 5566 5556"]:
        assert has_secret(line), line
    lines = []
    for _ in range(3000):
        card = _card(rng, rng.choice(["4", "51", "6011"]))
        pre = rng.choice(["91", "92", "93", "94", "95"]) + "".join(rng.choice("0123456789") for _ in range(rng.choice([4, 8])))
        sep = rng.choice([" ", "-"])
        lines += [f"ref {pre}{sep}{_spaced(card, sep)}", f"ref {_spaced(pre, sep)}{sep}{_spaced(card, sep)}"]
    assert sum(_usps_tracking(re.sub(r"\D", "", x)) for x in lines) > 300  # the loose rule would exempt these
    assert [x for x in lines if not has_secret(x)] == []


def test_card_behind_a_4_or_8_digit_91_95_group():
    # Found in the second review: "9100 4000 0566 5566 5556 06" is a USPS-layout run with a card at its
    # 2nd group. A run with a Luhn-valid window after its first group is never exempt. A run whose USPS check
    # digit is valid gets exactly the old first-window answer (so real tracking numbers are held no more often);
    # any other run is held, since its later card window passes Luhn and has a card-network prefix.
    rng = random.Random(8)
    lines = []
    for _ in range(3000):
        pre = rng.choice(["91", "92", "93", "94", "95"]) + "".join(rng.choice("0123456789") for _ in range(rng.choice([2, 6])))
        tail = "".join(rng.choice("0123456789") for _ in range(2))
        card = _card(rng, rng.choice(["4", "51", "6011"]))
        lines.append(_spaced(pre + card + tail, rng.choice([" ", "-"])))
    assert sum(_old_card(x) for x in lines) > 100  # the old rule held these about one time in ten
    valid = [x for x in lines if _usps_check_ok(x)]
    assert 200 < len(valid) < 400
    assert [x for x in valid if has_secret(x) != _old_card(x)] == []
    assert [x for x in lines if x not in valid and not has_secret(x)] == []
    # Known limit, kept on purpose: this line is also a check-digit-valid USPS number, judged as before. Holding
    # every such run with a card-prefixed Luhn window would hold about 4 in 100 real 22-digit tracking numbers
    # (7 in 100 for 26 digits) instead of 1 (2).
    assert _usps_check_ok("9100 4000 0566 5566 5556 06")
    assert has_secret("9100 4000 0566 5566 5556 06") == _old_card("9100 4000 0566 5566 5556 06")
    assert has_secret("9100 4000 0566 5566 5556 02")  # wrong check digit: not a tracking number, held


def test_valid_tracking_numbers_mostly_exempt():
    rng = random.Random(50)
    nums = [_tracking(rng, rng.choice([22, 26])) for _ in range(5000)]
    held = sum(has_secret(_spaced(n)) for n in nums)
    assert held < 150, held  # about 1 in 100 (22 digits) to 1 in 50 (26 digits) stay held; before, about 1 in 10


def test_91_95_card_then_expiry_still_held():
    rng = random.Random(95)
    for _ in range(500):
        card = _card(rng, rng.choice(["91", "95"]))
        for tail in [" 12 2027", " 12 27", " 123", " 1227 123"]:
            assert has_secret(f"card {_spaced(card)}{tail}"), card + tail


def test_only_whole_valid_usps_runs_are_exempt():
    rng = random.Random(7)
    bad = []
    while len(bad) < 50:  # a Luhn-passing window, but not a valid USPS number
        n = _tracking(rng)
        wrong = n[:-1] + str((int(n[-1]) + 1) % 10)
        if _held_by_window(wrong):
            bad.append(wrong)
    for n in bad:
        assert has_secret(_spaced(n)), n  # wrong check digit
    for n in [x for x in (_tracking(rng) for _ in range(500)) if _held_by_window(x)][:50]:
        assert has_secret(_spaced(n) + " 5"), n  # the run is longer than the number
        assert has_secret("5 " + _spaced(n)), n
        for x in [" ".join([n[:6], n[6:10], n[10:14], n[14:18], n[18:]]),  # not USPS layout
                  " ".join([n[:4], n[4:8], n[8:12], n[12:16], n[16:]]),  # final group of 6
                  _spaced(n)[:14] + "-" + _spaced(n)[15:]]:  # mixed separators
            assert has_secret(x) == _old_card(x), x  # not exempt: the plain card rule decides
    for prefix in ["90", "96", "42", "41"]:  # not a 91-95 prefix
        body = [prefix + "".join(rng.choice("0123456789") for _ in range(19)) for _ in range(500)]
        n = next(d for d in (b + _gs1(b) for b in body) if _held_by_window(d))
        assert has_secret(_spaced(n)), n
    # 20 digits: not an IMpb length
    twenty = next(d for d in (_tracking(rng, 20) for _ in range(500)) if _held_by_window(d))
    assert has_secret(_spaced(twenty))


def test_non_ascii_digits_disable_the_exemption():
    # Folding loses the digits' value, so no run can be proven a tracking number.
    assert card_hit(EXAMPLE, luhn=False)
    assert has_secret(f"{EXAMPLE} ١")


def _node(inputs):
    js = ("import {hasSecret} from %s; import fs from 'node:fs';"
          "console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(hasSecret)));"
          % json.dumps(str(REPO / "src" / "secret-scan.ts")))
    out = subprocess.run(["node", "--input-type=module", "-e", js], input=json.dumps(inputs),
                         capture_output=True, text=True, check=True, cwd=REPO)
    return json.loads(out.stdout)


@pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")
def test_python_and_node_agree():
    rng = random.Random(99)
    inputs = [EXAMPLE, EXAMPLE.replace(" ", "-"), f"{EXAMPLE} ١", f"{EXAMPLE} 5",
              "call 9100000001 4000 0566 5566 5556 thanks", "ref 920000 4000 0566 5566 5556"]
    for _ in range(300):
        trk, card = _tracking(rng, rng.choice([20, 22, 26])), _card(rng)
        pre = trk[:rng.choice([6, 10])]
        inputs += [_spaced(trk), _spaced(trk[:-1] + str((int(trk[-1]) + 1) % 10)), f"{_spaced(card)} 123",
                   f"{_spaced(card)} {_spaced(trk)}", _spaced(card, "-"), f"{pre} {_spaced(card)}",
                   " ".join([trk[:6], trk[6:10], trk[10:14], trk[14:]])]
    py, js = [has_secret(s) for s in inputs], _node(inputs)
    assert [s for s, a, b in zip(inputs, py, js) if a != b] == []
