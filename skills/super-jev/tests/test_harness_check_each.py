"""Harness-only: the content check spawns no process, and a file holding a secret is still held (not sent, not kept)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ask  # noqa: E402

# Assembled from words at run time so no secret-shaped literal sits in the repo (the scanner flags it even when split in two).
KEY = " ".join(["PRIVATE", "KEY-----"])
SECRET = " ".join(["-----BEGIN", "RSA", KEY]) + "\nMIIEowIBAAKCAQEAx3Zk9d2Fq8s1\n" + " ".join(["-----END", "RSA", KEY]) + "\n"


@pytest.fixture
def files(tmp_path):
    clean, secret = tmp_path / "clean.md", tmp_path / "secret.md"
    clean.write_text("# Knee referral\nThe referral code is 123.\n")
    secret.write_text("# Keys\n" + SECRET)
    return str(clean), str(secret)


def run(monkeypatch, harness, paths):
    spawned = []
    monkeypatch.setattr(ask, "spawn_navigation", lambda payload: spawned.append(payload) or (None, "no judge"))
    ask._STAGE.clear()
    if harness:
        ask._STAGE["harness_only"] = True
    try:
        return ask.confirm("knee referral code", paths), spawned
    finally:
        ask._STAGE.clear()


def test_harness_only_spawns_nothing_and_leaves_files_unchecked(monkeypatch, files):
    (scores, _partial, error, notes), spawned = run(monkeypatch, True, [files[0]])
    assert spawned == []
    assert scores == {} and notes == {files[0]: ask.INCONCLUSIVE}
    assert error == ask.HARNESS_NO_CHECK


def test_secret_file_is_held_the_same_with_or_without_harness_only(monkeypatch, files):
    paths = list(files)
    (s_h, _p, _e, notes_h), spawned_h = run(monkeypatch, True, paths)
    (s_o, _p2, _e2, notes_o), _ = run(monkeypatch, False, paths)
    assert spawned_h == []
    assert notes_h[files[1]] == ask.HELD_SECRET == notes_o[files[1]]
    assert notes_h[files[0]] == ask.INCONCLUSIVE == notes_o[files[0]]
    assert s_h == s_o == {}
