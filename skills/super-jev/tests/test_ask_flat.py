#!/usr/bin/env python3
"""Per-ask work must not grow with connected files: a warm ask reads and hashes nothing it already hashed
(stat-keyed memo: size, mtime_ns, ctime_ns, inode). Made-up state, no network.

    python3 -m pytest skills/super-jev/tests/test_ask_flat.py -q
"""
import hashlib
import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
ENGINE = SKILL.parents[1] / "experiments" / "verified-pointer-memory"
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(ENGINE))
spec = importlib.util.spec_from_file_location("ask_flat", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)
import service  # noqa: E402  (the engine's own module; ask hands it the same memo)

SECRET = "api_key = sk-live-" + "a1B2c3D4e5" * 4


def _make(tmp_path, n):
    d = tmp_path / "notes"
    d.mkdir()
    files = []
    for i in range(n):
        f = d / f"n{i}.md"
        f.write_text(f"# Note {i}\nbody text number {i}\n")
        files.append(f)
    cache = {str(f): {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True} for f in files}
    return files, cache


@pytest.fixture
def counts(monkeypatch):
    c = {"read": 0, "sha": 0}
    real_read, real_sha = Path.read_bytes, hashlib.sha256

    def read_bytes(self):
        c["read"] += 1
        return real_read(self)

    def sha256(*a, **k):
        c["sha"] += 1
        return real_sha(*a, **k)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(hashlib, "sha256", sha256)
    return c


def _ask_passes(cache, files, monkeypatch):
    """edited_held, the TOC corpus build and the engine snapshot's sha, as one ask runs them."""
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: cache)
    ask.use_stat_memo("me")
    reads = {}
    edited = ask.edited_held(["p1"], set(), reads)
    corpus = ask.toc_corpus(ask.candidate_files(["p1"], set()), reads)
    for f in files:
        service.sha(f)
    ask.flush_stat_memo()
    return edited, corpus


@pytest.mark.parametrize("n", [200, 2000])
def test_warm_ask_reads_and_hashes_nothing_and_one_edit_rehashes_once(tmp_path, monkeypatch, counts, n):
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    ask._STAT_MEMOS.clear()
    files, cache = _make(tmp_path, n)
    time.sleep(0.15)  # past the memo's racy window
    _ask_passes(cache, files, monkeypatch)  # cold: every file hashed once
    assert counts["read"] >= n and counts["sha"] >= n
    ask._STAT_MEMOS.clear()  # a new process: the memo must come back from index.sqlite
    counts.update(read=0, sha=0)
    edited, corpus = _ask_passes(cache, files, monkeypatch)
    assert (counts["read"], counts["sha"]) == (0, 0)
    assert len(corpus) == n and not any(edited.values())

    # one edit, same size, mtime put back with touch -r semantics: still exactly one re-hash
    victim = files[n // 2]
    st = victim.stat()
    victim.write_text(victim.read_text().replace("body", "BODY"))
    os.utime(victim, ns=(st.st_atime_ns, st.st_mtime_ns))
    time.sleep(0.15)
    counts.update(read=0, sha=0)
    edited, corpus = _ask_passes(cache, files, monkeypatch)
    # one hash of the new bytes in this ask's pass (the engine snapshot then reuses it)
    assert counts["sha"] == 1
    assert str(victim) in corpus or edited["refresh"] == [str(victim)] or str(victim) in str(edited)


def test_held_secret_file_stays_held_after_content_change(tmp_path, monkeypatch, counts):
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    ask._STAT_MEMOS.clear()
    files, cache = _make(tmp_path, 5)
    time.sleep(0.15)
    victim = files[2]
    victim.write_text("# Note\n" + SECRET + "\n")
    time.sleep(0.15)
    for _ in range(3):  # cold, then memo hits: the verdict never softens
        edited, corpus = _ask_passes(cache, files, monkeypatch)
        assert edited["secret"] == [str(victim)]
        assert str(victim) not in corpus
    victim.write_text("# Note\n" + SECRET + "\nmore text\n")  # changed again, still a secret
    time.sleep(0.15)
    edited, corpus = _ask_passes(cache, files, monkeypatch)
    assert edited["secret"] == [str(victim)] and str(victim) not in corpus
