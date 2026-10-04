"""A file may carry the fake card numbers payment processors publish; a real card is still held.

The published test numbers (secret_patterns.json test_cards) open no account, so a scanner's own source or a
checkout test's fixtures can be connected and searched. Any other Luhn-valid number is held as before, also
when it sits in the same file or right beside a test number. Python and Node must agree.

    python3 -m pytest skills/super-jev/tests/test_published_test_cards.py -q
"""
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_card_gaps import _node  # noqa: E402
from prepare_bulk import has_secret, clean_text  # noqa: E402

TEST = ["4111 1111 1111 1111", "4242-4242-4242-4242", "5555555555554444", "3782 822463 10005",
        "order 1234 4111 1111 1111 1111 12 27 123"]
REAL = ["5500 0000 0000 0004", "4000 0566 5566 5556",
        "order 1234 5500 0000 0000 0004", "3400 000000 00009"]


@pytest.mark.parametrize("text", TEST)
def test_published_test_number_is_not_a_secret(text):
    assert not has_secret("# fixture\ncard = '" + text + "'\n")


@pytest.mark.parametrize("text", REAL)
def test_other_luhn_numbers_still_held(text):
    assert has_secret("card " + text)


def test_real_card_beside_or_after_a_test_number_is_held():
    assert has_secret("4111 1111 1111 1111\n5500 0000 0000 0004")
    assert has_secret("4111 1111 1111 1111 5500 0000 0000 0004")
    assert has_secret("test 4242424242424242, real 5500000000000004")


def test_file_with_only_test_numbers_is_kept_whole(tmp_path):
    text = "def test():\n    assert pay('4111 1111 1111 1111')\n"
    assert clean_text(text, str(tmp_path / "a.py")) == text


def test_file_with_a_real_card_is_not_kept_whole(tmp_path):
    text = "x = 1\n" * 200 + "pay('5500 0000 0000 0004')\n" + "y = 2\n" * 200
    out = clean_text(text, str(tmp_path / "a.py"))
    assert out is None or "5500" not in out


@pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")
def test_python_and_node_agree():
    inputs = ["card " + t for t in TEST + REAL] + ["4111 1111 1111 1111\n5500 0000 0000 0004"]
    assert _node(inputs) == [has_secret(s) for s in inputs]
    assert _node(inputs)[:len(TEST)] == [False] * len(TEST)
