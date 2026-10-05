#!/usr/bin/env python3
"""The index updater walks each connected root once per update round, not once per set (flat per-ask work as files
grow). Made-up tree, several sets sharing one root; counts os.walk / os.scandir calls. No network, no spend.

    python3 -m pytest skills/super-jev/tests/test_index_walk_once.py -q
"""
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from file_index import FileIndex  # noqa: E402


def _run(tmp_path, monkeypatch, n_sets, share):
    root = tmp_path / "notes"
    root.mkdir(parents=True)
    sets, expected = {}, {}
    for i in range(n_sets):
        sub = root / f"s{i}"
        sub.mkdir()
        f = sub / "a.md"
        f.write_text(f"# note {i}\nplain text\n")
        sets[f"p{i}"] = {str(f): {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True}}
        expected[f"p{i}"] = [str(f)]
    calls = []
    for name in ("walk", "scandir"):
        real = getattr(os, name)
        monkeypatch.setattr(os, name, (lambda r, n: lambda *a, **k: (calls.append(n), r(*a, **k))[1])(real, name))
    idx = FileIndex("t", tmp_path / "index.sqlite")
    idx.begin_round()
    for ptr, ents in sets.items():
        idx.update(ptr, entries=ents, roots=[str(root)])
    idx.set_complete(expected)
    return idx, root, calls, expected


def test_shared_root_walked_once(tmp_path, monkeypatch):
    import prepare_bulk as pb
    _idx, root, calls, _ = _run(tmp_path, monkeypatch, 4, True)
    del calls[:]
    pb.walk_md(root)
    one_walk = list(calls)  # what a single walk of this tree costs
    del calls[:]
    _idx2, root2, calls2, _ = _run(tmp_path / "again", monkeypatch, 4, True)
    assert one_walk and len(calls2) <= len(one_walk) + 2  # 4 sets, one walk (before the fix: 4x)


def test_next_round_sees_new_file(tmp_path, monkeypatch):
    idx, root, _calls, expected = _run(tmp_path, monkeypatch, 3, True)
    (root / "s0" / "late.md").write_text("# late\n")
    out = idx.update("p0", entries={}, roots=[str(root)])
    assert str(root / "s0" / "late.md") in out["new"]  # the cache was cleared by set_complete
