"""A refresh with a size-held file and a low-score judge exception is a partial success (exit 3), not a failure."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

import auto_heal as ah
import connect_checked as cc
from test_prepare_bulk import base_argv, fake_connect_memory, pb


@pytest.fixture(autouse=True)
def _claude_cli_present(monkeypatch):
    real_which = pb.shutil.which
    monkeypatch.setattr(pb.shutil, "which",
                        lambda name, *a, **k: "/usr/bin/claude" if name == "claude" else real_which(name, *a, **k))


def _set(tmp_path, monkeypatch, n=4):
    root = tmp_path / "root"
    root.mkdir()
    fs = []
    for i in range(n):
        f = root / f"orchard{i}.md"
        f.write_text(f"# Orchard {i}\nFacts about pear tree {i}.\n")
        fs.append(f)
    big = root / "ledger-big.md"
    big.write_text("x " * 130_000)  # over the 250,000-byte ceiling: held by rule
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None: {
        str(Path(it["path"]) if isinstance(it, dict) else it): {
            "description": f"Note about {Path(it['path'] if isinstance(it, dict) else it).stem}.",
            "question": f"What is in {Path(it['path'] if isinstance(it, dict) else it).stem}?",
            "kind": "note", "status": "active", "as_of": "unknown", "subject": "pears"} for it in items})
    monkeypatch.setattr(pb, "navigate", lambda pointer, principal, q: [])
    return fs


def _gate(low=None, error=None):
    def gate(claim, path):
        if error and error in path:
            return {"state": "ERROR", "reason": "jev: HTTP 503"}
        if low and low in path:
            return {"state": "SUPPORTED", "confidence": 0.35}
        return {"state": "SUPPORTED", "confidence": 0.95}
    return gate


def _run(tmp_path, monkeypatch, gate):
    calls = []
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")
    monkeypatch.setattr(pb, "gate", gate)
    monkeypatch.setattr(pb, "gate_many", lambda claims, path: None)
    monkeypatch.setattr(pb, "memory", fake_connect_memory(calls))
    monkeypatch.setattr(sys, "argv", base_argv(tmp_path / "root", extra=["--no-findability"]))
    return pb.main(), calls


def test_size_held_plus_low_score_exception_exits_3_and_connected_files_are_served(tmp_path, monkeypatch, capsys):
    fs = _set(tmp_path, monkeypatch)
    rc, calls = _run(tmp_path, monkeypatch, _gate(low="orchard1"))
    out = capsys.readouterr().out
    assert rc == 3
    assert "CONNECTED 3, HELD 2, FAILED 0" in out
    assert "approved: 3  exceptions: 0  held: 2" in out and "EXCEPTION" not in out
    cache = json.loads((pb.CACHE_DIR / "my-records.json").read_text())
    assert [Path(k).name for k, v in sorted(cache.items()) if v["pass"]] == ["orchard0.md", "orchard2.md", "orchard3.md"]
    registered = [s["path"] for c in calls if "reviewed" in c for s in c["sources"]]
    assert sorted(Path(p).name for p in registered) == ["orchard0.md", "orchard2.md", "orchard3.md"]
    # the report names the connected files as approved, so the set is not stale for them
    report = json.loads((pb.CACHE_DIR / "my-records-report.json").read_text())
    assert len(report["approved"]) == 3
    # the judge-held file's judged sha is in the report, so an ask can tell an edit without opening the cache
    low = next(k for k, v in cache.items() if not v["pass"])
    assert report["heldSha"] == {low: cache[low]["sha256"]}
    # auto-heal settles the attempt as ok: no failure count, no retry backoff
    ah.STATE_DIR = tmp_path / "state"
    ah._settle("agent", "my-records", rc in (0, 3))
    st = ah._load_state("agent")
    assert "my-records" not in st["fails"] and "my-records" not in st["retry"]


def test_a_judge_call_error_still_exits_1(tmp_path, monkeypatch, capsys):
    _set(tmp_path, monkeypatch)
    rc, _calls = _run(tmp_path, monkeypatch, _gate(error="orchard1"))
    assert rc == 1
    assert "FAILED 1" in capsys.readouterr().out


def test_a_402_still_exits_1_and_stops(tmp_path, monkeypatch):
    _set(tmp_path, monkeypatch)
    n = []

    def gate(claim, path):
        n.append(path)
        raise cc.PaymentRequired("HTTP 402")
    rc, _calls = _run(tmp_path, monkeypatch, gate)
    assert rc == 1
    assert len(n) == 1
    assert not (pb.CACHE_DIR / "my-records.json").exists()


def test_an_edited_low_held_file_is_retried_and_an_unchanged_one_is_not(tmp_path):
    import hashlib
    import refresh_changed as rc
    f = tmp_path / "pear.md"
    f.write_text("# Pear\nfirst\n")
    ok = tmp_path / "plum.md"
    ok.write_text("# Plum\n")
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    cache = {str(f): {"sha256": sha(f), "pass": False}, str(ok): {"sha256": sha(ok), "pass": True}}
    report = {"approved": [str(ok)]}
    assert rc.changed_files(report, cache) == []  # unchanged low-held: no paid retry loop
    f.write_text("# Pear\nfixed so it says what it should\n")
    assert rc.changed_files(report, cache) == [str(f)]  # edited: re-judged
