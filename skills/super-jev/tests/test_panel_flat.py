#!/usr/bin/env python3
"""Per-ask work must not grow with connected pointers: with K indexed pointers and one stale, an ask snapshots only the
stale one, and the engine hashes the same bytes whether K is 10 or 60. Real engine and index, made-up notes, no network.

    python3 -m pytest skills/super-jev/tests/test_panel_flat.py -q
"""
import hashlib
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_index_read_path as rp  # noqa: E402  (its corpus builder)

ask = rp.ask
SKILL = Path(__file__).resolve().parent.parent
EXP = SKILL.parents[1] / "experiments" / "verified-pointer-memory"

pytestmark = pytest.mark.real_toc


class Reached(Exception):
    """The ask got to routing: everything before it has been counted."""


def connect_all(tmp_path, monkeypatch, names):
    for name in ("SUPERJEV_REPO", "SUPERJEV_JUDGE"):
        monkeypatch.delenv(name, raising=False)
    subprocess.run([sys.executable, str(SKILL / "setup.py")], capture_output=True, text=True, cwd=str(tmp_path), timeout=60)
    monkeypatch.syspath_prepend(str(EXP))
    from cli import load_config
    from path_connect import connect
    config = load_config(Path(ask.state_dir(rp.PRINCIPAL).parent) / "_memory" / "config.json")
    for name in names:
        paths = sorted(ask.prepare_bulk.load_cache_files(name))
        sources = [{"path": p} for p in paths]
        draft = connect({"pointer": name, "principals": [rp.PRINCIPAL], "sources": sources}, config)
        done = connect({"pointer": name, "principals": [rp.PRINCIPAL], "reviewed": True,
                        "navigationSHA": draft["navigationSHA"],
                        "sources": [{"path": x["path"], "sha256": x["sha256"]} for x in draft["sources"]]}, config)
        assert done.get("status") == "registered", done


def counts_for(tmp_path, monkeypatch, k):
    notes, names, sdir = rp.build(tmp_path, monkeypatch, 2 * k, n_ptr=k, tag=f"k{k}")
    connect_all(tmp_path / f"k{k}", monkeypatch, names)
    monkeypatch.setattr(ask, "spawn_index_updater", lambda principal: None)
    rp.flag(monkeypatch, True)
    time.sleep(0.15)  # past the stat memo's racy window
    rp.sync(sdir)
    from file_index import FileIndex
    idx = FileIndex(rp.PRINCIPAL, sdir / "index.sqlite")
    idx.mark_stale(names[0])  # the one fallback pointer
    idx.close()
    import service
    snaps, shas = [], [0]
    real_snap, real_sha = service.Service.snapshot, hashlib.sha256

    def snapshot(self, dataset):
        snaps.append(dataset)
        return real_snap(self, dataset)

    def sha256(*a, **kw):
        shas[0] += 1
        return real_sha(*a, **kw)

    def stop(*a, **kw):
        raise Reached
    monkeypatch.setattr(ask, "pointer_words", stop)
    monkeypatch.setattr(ask, "confirm", lambda *a, **kw: ({}, set(), None, {}))
    monkeypatch.setattr(ask, "run_gate", lambda *a, **kw: ("CLEAN", 0.9))
    with pytest.raises(Reached):  # warm: the stat memo and the manifest memo hold their shas
        ask._lookup(rp.PLANTED[0][0], rp.PRINCIPAL, sdir)
    monkeypatch.setattr(service.Service, "snapshot", snapshot)
    monkeypatch.setattr(hashlib, "sha256", sha256)
    with pytest.raises(Reached):
        ask._lookup(rp.PLANTED[0][0], rp.PRINCIPAL, sdir)
    return len(snaps), shas[0]


def test_panel_work_is_flat_in_connected_pointers(tmp_path, monkeypatch):
    small = counts_for(tmp_path, monkeypatch, 10)
    big = counts_for(tmp_path, monkeypatch, 60)
    print("COUNTS (snapshots, sha256) K=10:", small, "K=60:", big)
    assert small[0] == big[0] == 1, (small, big)  # snapshots == fallback pointers
    assert small[1] == big[1], (small, big)  # hashlib.sha256 calls
