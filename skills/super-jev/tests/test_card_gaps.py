"""Two card layouts the scan missed are held; ordinary numbers still are not.

1. A card right after another digit group ("order 1234 4000 0566 5566 5556"): the card pattern never
   overlaps, so only the first 16-digit window of the run was tested. Later windows are now tested too, with a
   card-network prefix required on top of Luhn, and only with at most 2 groups of 4+ digits before and 2 after
   them in the run (short groups such as a phone number, date, expiry or CVV are allowed, up to 4 groups a side),
   so a long row of 4-digit numbers is not held for its many windows by chance.
2. A 15-digit American Express number ("3400 000000 00009", 4-6-5, 4-4-4-3 or compact): only 16 digits were
   a card.

Known limits (see CHANGELOG): 19-digit cards, cards written with dots, networks outside card_iin after another
digit group. Nothing the old scan held is released. Python and Node must agree. Needs node on PATH for the parity test.

    python3 -m pytest skills/super-jev/tests/test_card_gaps.py -q
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
from prepare_bulk import CARD_RE, has_secret, secret_detail  # noqa: E402


def _luhn_ok(d: str) -> bool:
    total = 0
    for i, c in enumerate(reversed(d)):
        n = int(c) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def _card(rng, prefix="4", n=16) -> str:
    body = prefix + "".join(rng.choice("0123456789") for _ in range(n - 1 - len(prefix)))
    return next(body + c for c in "0123456789" if _luhn_ok(body + c))


def _digits(rng, n) -> str:
    return "".join(rng.choice("0123456789") for _ in range(n))


def _spaced(d: str, sep: str = " ") -> str:
    return sep.join(d[i:i + 4] for i in range(0, len(d), 4))


def _amex(d: str, sep: str = " ") -> str:
    return sep.join([d[:4], d[4:10], d[10:]])


def _old_card(text: str) -> bool:
    return any(_luhn_ok(re.sub(r"\D", "", m.group())) for m in CARD_RE.finditer(text))


REPORTED = ["order 1234 4000 0566 5566 5556", "3400 000000 00009"]


def test_reported_lines_held():
    for line in REPORTED:
        assert not _old_card(line), line  # the old scan let both through
        assert has_secret(line), line


def test_card_after_digit_groups_held():
    rng = random.Random(1)
    misses = []
    for _ in range(3000):
        card = _card(rng, rng.choice(["4", "51", "55", "2221", "6011", "65", "3530", "62"]))
        sep = rng.choice([" ", "-"])
        pre = sep.join(_digits(rng, rng.choice([4, 4, 6, 8])) for _ in range(rng.choice([1, 2])))
        for line in [f"order {pre}{sep}{_spaced(card, sep)}", f"{pre}{sep}{_spaced(card, sep)} 123",
                     f"{pre}{sep}{_spaced(card, sep)} 12 27", f"{pre}{sep}{_spaced(card, sep)} 12 27 123",
                     f"ref {pre} {card}"]:
            if not has_secret(line):
                misses.append(line)
    assert misses == []


def test_amex_held_in_usual_layouts():
    rng = random.Random(15)
    cards = [_card(rng, rng.choice(["34", "37"]), 15) for _ in range(2000)]
    for c in cards:
        for line in [f"amex {_amex(c)}", f"amex {_amex(c, '-')}", f"amex {c}", f"card: {_amex(c)} exp 09/28",
                     f"order 1234 {_amex(c)}", f"amex {_spaced(c)}", f"amex {_spaced(c, '-')}"]:
            assert has_secret(line), line
    assert has_secret("3400 000000 00009 ١")  # non-ASCII digit: held without Luhn


def test_amex_shape_needs_luhn_prefix_and_standing_alone():
    for line in ["3782 822463 10006", "3882 822463 10005", "x3400 000000 00009", "3400 000000 00009x",
                 "13400 000000 00009", "3400 000000 000091", "3782 822463-10005", "3782-8224 6310-005",
                 "3782 8224 6310 006", "3782 82246 310005"]:
        assert not has_secret(line), line


def test_never_releases_what_the_old_scan_held():
    rng = random.Random(3)
    lines = []
    for _ in range(4000):
        sep = rng.choice([" ", "-"])
        groups = [_digits(rng, rng.choice([4, 4, 4, 2, 6])) for _ in range(rng.randint(4, 8))]
        lines.append("x " + sep.join(groups) + " y")
    old = [x for x in lines if _old_card(x)]
    assert len(old) > 50
    assert [x for x in old if not has_secret(x)] == []


def test_card_after_phone_or_date_or_before_expiry_and_cvv_held():
    for line in ["call 555-867-5309 4290 4974 6228 7854", "call +1 555 867 5309 4290 4974 6228 7854",
                 "28-09-2026 4290 4974 6228 7854", "order 1234 4000 0566 5566 5556 12 27 123",
                 "order 1234 4000 0566 5566 5556 12-27 123", "order 5678 9012 4000 0566 5566 5556 09 2028 123"]:
        assert not _old_card(line), line
        assert has_secret(line), line


def test_card_after_three_long_groups_is_a_known_limit():
    # A later window with 3 or more groups of 4+ digits before (or after) it is not tested: a long row of
    # 4-digit numbers would otherwise be held about 3 more times in 100 for each extra window.
    assert not has_secret("order 1234 5678 9012 4000 0566 5566 5556")
    assert not has_secret("order 1234 4000 0566 5566 5556 5678 9012 3456")
    assert not has_secret("1 2 3 4 5 1234 4000 0566 5566 5556")  # 6 groups before


def test_long_grouped_numbers_rarely_held_more_than_before():
    # The old scan tested one window of a grouped run (about 1 in 10 held); later windows need a card-network
    # prefix as well as Luhn and sit near the run's start, adding about 3 in 100 per window (20 digits: 1,
    # 24 digits: 2, a row of 10 or more 4-digit numbers: none).
    rng = random.Random(20)
    for n, extra in [(20, 0.04), (24, 0.07), (40, 0.005), (80, 0.005)]:
        nums = [_spaced(_digits(rng, n), rng.choice([" ", "-"])) for _ in range(5000)]
        old = sum(map(_old_card, nums))
        new = sum(has_secret(f"order {x}") for x in nums)
        assert new - old < extra * len(nums), (n, old, new)


def test_ordinary_numbers_not_held():
    rng = random.Random(7)
    lines = ["call +1 (555) 123-4567 or 555-867-5309", "dates 09/28/2026 10/01/2026 12/31/2026",
             "2026-09-28 2026-09-29 2026-09-30 2026-10-01", "ups 1Z999AA10123456784", "tel 020 7946 0958",
             "order #112-4455667-8899001"]
    for _ in range(2000):
        first = rng.choice("0124567899") + _digits(rng, 3)  # 4-6-5 layout, but not an Amex 34/37 start
        lines += [f"fedex {_digits(rng, 12)}", f"imei 35{_digits(rng, 13)}", f"ref {first} {_digits(rng, 6)} {_digits(rng, 5)}"]
    assert [x for x in lines if has_secret(x)] == []


def test_compact_15_digit_numbers_rarely_held():
    rng = random.Random(151)
    nums = [_digits(rng, 15) for _ in range(20000)]
    held = sum(has_secret(f"ref {n}") for n in nums)
    assert held < 0.004 * len(nums), held  # only 34/37 + Luhn: about 2 in 1000


def test_notes_file_with_card_after_order_number_is_held(tmp_path):
    f = tmp_path / "orders.md"
    f.write_text("# Orders\n" + "- a fact about the order\n" * 30 + "- order 1234 4000 0566 5566 5556\n")
    detail = secret_detail(f)
    assert detail and detail["line"] == 32 and "4000" not in detail["masked"]


def _node(inputs):
    js = ("import {hasSecret} from %s; import fs from 'node:fs';"
          "console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(hasSecret)));"
          % json.dumps(str(REPO / "src" / "secret-scan.ts")))
    out = subprocess.run(["node", "--input-type=module", "-e", js], input=json.dumps(inputs),
                         capture_output=True, text=True, check=True, cwd=REPO)
    return json.loads(out.stdout)


@pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")
def test_python_and_node_agree():
    rng = random.Random(42)
    inputs = REPORTED + ["3400 000000 00009 ١", "3400-000000-00009", "340000000000009", "x3400 000000 00009",
                         "3400 0000 0000 009", "3400-0000-0000-009", "order 1234 5678 9012 4000 0566 5566 5556",
                         "order 1234 4000 0566 5566 5556 12 27", "order 1234 4000 0566 5566 5556 12 27 1",
                         "call 555-867-5309 4290 4974 6228 7854", "28-09-2026 4290 4974 6228 7854",
                         "1 2 3 4 5 1234 4000 0566 5566 5556", "1 2 3 4000 0566 5566 5556 1 2 3 4 5", "9 4000 0566 5566 5556"]
    for _ in range(400):
        sep = rng.choice([" ", "-"])
        card = _card(rng, rng.choice(["4", "51", "9", "37"]))
        amex = _card(rng, rng.choice(["34", "37"]), 15)
        pre = sep.join(_digits(rng, 4) for _ in range(rng.choice([1, 2])))
        inputs += [f"{pre}{sep}{_spaced(card, sep)}", f"{_spaced(card, sep)}{sep}{pre}", _amex(amex, sep), amex,
                   f"{pre} {_amex(amex)}", _spaced(_digits(rng, rng.choice([20, 24, 28, 40])), sep),
                   f"{pre}{sep}{_spaced(card, sep)}{sep}{sep.join(_digits(rng, rng.choice([2, 3, 4])) for _ in range(rng.choice([1, 2, 3, 4, 5])))}",
                   f"{sep.join(_digits(rng, rng.choice([1, 2, 3, 4])) for _ in range(rng.choice([1, 3, 4, 5])))}{sep}{_spaced(card, sep)}",
                   f"a {pre}{sep}{_spaced(card, sep)} b {_amex(amex)}"]
    py, js = [has_secret(s) for s in inputs], _node(inputs)
    assert [s for s, a, b in zip(inputs, py, js) if a != b] == []
    # a hit must not leave state behind in Node (a global regex's lastIndex once did)
    assert _node(["order 1234 4000 0566 5566 5556", "card 4000 0566 5566 5556"]) == [True, True]
