"""Gate packing in prepare_bulk: small files share one judge call; a failed pack falls back per file."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

import connect_checked as cc
from test_prepare_bulk import base_argv, fake_connect_memory, pb


@pytest.fixture(autouse=True)
def _claude_cli_present(monkeypatch):
    # --writer auto falls back to builtin (no judge calls to pack) when no claude CLI is on PATH, as on CI.
    real_which = pb.shutil.which
    monkeypatch.setattr(pb.shutil, "which",
                        lambda name, *a, **k: "/usr/bin/claude" if name == "claude" else real_which(name, *a, **k))


def _files(root, n):
    root.mkdir()
    fs = []
    for i in range(n):
        f = root / f"n{i}.md"
        f.write_text(f"# Note {i}\nFacts about topic {i}.\n")
        fs.append(f)
    return fs


def _writer(fs):
    return lambda items, model, feedback=None: {
        str(f): {"path": str(f), "description": f"Topic {i} note.", "question": f"What is topic {i}?",
                 "kind": "note", "status": "active", "as_of": "unknown", "subject": f"topic {i}"}
        for i, f in enumerate(fs)}


def test_pack_groups_respect_file_and_char_caps():
    paths = [f"/p{i}" for i in range(8)]
    sizes = {p: 100 for p in paths}
    sizes["/p7"] = pb.PACK_CHARS + 1  # too big to share a call
    groups = pb.pack_groups(paths, sizes)
    assert [len(g) for g in groups] == [6]  # the 7th small file alone is gated by itself
    assert "/p7" not in sum(groups, [])


def test_small_files_share_one_call_and_skip_per_file_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "1")
    fs = _files(tmp_path / "root", 3)
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "writer", _writer(fs))
    monkeypatch.setattr(pb, "gate", lambda c, p: (_ for _ in ()).throw(AssertionError("per-file gate ran")))
    packs = []

    def fake_many(claims, path):
        text = Path(path).read_text()
        packs.append((claims, text))
        return [{"state": "SUPPORTED", "confidence": 0.95, "secs": 0.1} for _ in claims]

    monkeypatch.setattr(pb, "gate_many", fake_many)
    monkeypatch.setattr(pb, "memory", fake_connect_memory([]))
    monkeypatch.setattr(sys, "argv", base_argv(tmp_path / "root"))
    assert pb.main() == 0
    assert len(packs) == 1
    claims, text = packs[0]
    assert len(claims) == 6 and claims[0] == "File F1: Topic 0 note."
    assert "===== FILE F3 =====" in text and "Facts about topic 2." in text
    cache = json.loads((pb.CACHE_DIR / "my-records.json").read_text())
    assert all(cache[str(f)]["pass"] and cache[str(f)]["labels_ok"] for f in fs)


def test_failed_pack_falls_back_to_per_file_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "1")
    fs = _files(tmp_path / "root", 2)
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "writer", _writer(fs))
    monkeypatch.setattr(pb, "gate_many", lambda claims, path: None)
    calls = []
    monkeypatch.setattr(pb, "gate", lambda c, p: calls.append(c) or {"state": "SUPPORTED", "confidence": 0.9})
    monkeypatch.setattr(pb, "memory", fake_connect_memory([]))
    monkeypatch.setattr(sys, "argv", base_argv(tmp_path / "root"))
    assert pb.main() == 0
    assert len(calls) == 4  # description + labels, per file


def test_pack_failing_description_still_gets_one_rewrite_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "1")
    fs = _files(tmp_path / "root", 2)
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "writer", _writer(fs))
    monkeypatch.setattr(pb, "gate_many", lambda claims, path: [
        {"state": "NOT_SUPPORTED" if c.startswith("File F1: Topic") else "SUPPORTED", "confidence": 0.95}
        for c in claims])
    alone = []
    monkeypatch.setattr(pb, "gate", lambda c, p: alone.append((c, p)) or {"state": "SUPPORTED", "confidence": 0.9})
    monkeypatch.setattr(pb, "memory", fake_connect_memory([]))
    monkeypatch.setattr(sys, "argv", base_argv(tmp_path / "root"))
    assert pb.main() == 0
    # file 0: rewrite gated alone, then its labels alone (the pack's label verdict belonged to the old draft)
    assert [p for _c, p in alone] == [str(fs[0]), str(fs[0])]


def test_gate_many_needs_every_claim_row(monkeypatch):
    class R:
        stdout = "  c1 SUPPORTED 0.97\n"
        stderr = ""
    monkeypatch.setattr(cc.subprocess, "run", lambda *a, **k: R())
    assert cc.gate_many(["a", "b"], "/x") is None
    R.stdout = "  c1 SUPPORTED 0.97\n  c2 CONTRADICTED 0.60\n"
    got = cc.gate_many(["a", "b"], "/x")
    assert [g["state"] for g in got] == ["SUPPORTED", "CONTRADICTED"]
