"""v1.0.1: tool-made sha256 hashes must never trip the secret scan; real cards and keys still held."""
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare_bulk import has_secret, payload_has_secret  # noqa: E402

DIGESTS = [hashlib.sha256(os.urandom(32)).hexdigest() for _ in range(2000)]


def test_random_sha256_never_held():
    assert not [d for d in DIGESTS if has_secret(d)]
    req = {"action": "connect", "pointer": "p", "principals": ["a"],
           "sources": [{"path": "/x/a.md", "description": "notes", "sha256": d} for d in DIGESTS]}
    assert not payload_has_secret(req)


def test_real_cards_and_keys_still_held():
    for text in ["card 4111 1111 1111 1111", "4111111111111111", "5500-0000-0000-0004",
                 "api_key = sk-live-9fQ2xZ7pL0aBcD3eF4", "ghp_" + "a1" * 18]:
        assert has_secret(text), text
    assert payload_has_secret({"sources": [{"description": "card 4111111111111111", "sha256": "ab"}]})
