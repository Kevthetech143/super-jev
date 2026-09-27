"""A miss names files skipped at setup that may hold the answer, with the real
reason and the fix, from the principal's own prepare-cache only (no provider call)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ask  # noqa: E402
import prepare_bulk  # noqa: E402


@pytest.fixture
def cache(tmp_path, monkeypatch):
    cdir = tmp_path / "prepare-cache"
    cdir.mkdir()
    notes = tmp_path / "notes"
    notes.mkdir()
    big = notes / "closed.md"
    big.write_text("Villa roof repair quote from the contractor: 4,200 dollars.\n")
    secret = notes / "wifi-router.md"
    secret.write_text("SECRET-CONTENT-NEVER-PRINT router password hunter2\n")
    arc = notes / "arc-campaign.md"
    arc.write_text("ARC campaign phase notes.\n")
    ok = notes / "roof-ok.md"
    ok.write_text("roof\n")
    (cdir / "alice-notes-report.json").write_text(json.dumps({
        "pointer": "alice-notes",
        "held": [[str(big), "over size ceiling (109,340 bytes, max 90,000); split it into smaller .md files"],
                 [str(secret), "card/password-like text; review before onboarding"]],
        "exceptions": [[str(arc), "CONTRADICTED 0.28"]], "removed": []}))
    (cdir / "alice-notes.json").write_text(json.dumps({
        str(arc): {"verdict": "CONTRADICTED", "pass": False},
        str(ok): {"verdict": "SUPPORTED", "pass": True}}))
    # Held list only, no report (like primary-brain-notes); reached through part pointer -2.
    brain = notes / "brain-garden.md"
    brain.write_text("garden\n")
    (cdir / "alice-brain-held.txt").write_text(
        f"{brain}\tover size ceiling (95,000 bytes, max 90,000); split it\n    pattern=x line=1 masked=#\n")
    (cdir / "alice-brain.json").write_text(json.dumps({str(notes / "gate.md"): {"verdict": "ERROR", "pass": False}}))
    # Another principal's pointer: never shown to alice.
    other = notes / "roof-bob.md"
    other.write_text("roof\n")
    (cdir / "bob-notes-report.json").write_text(json.dumps({"principals": ["bob"], "held": [[str(other), "over size ceiling (1 bytes, max 2)"]]}))
    monkeypatch.setattr(prepare_bulk, "CACHE_DIR", cdir)
    return notes


def fake_memory(pointers, errored=()):
    def memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": list(pointers)}
        if req["action"] == "navigate" and req["pointer"] in errored:
            return {"status": "error", "reason": "Navigation provider failed"}
        if req["action"] == "navigate":
            return {"status": "no-candidates", "candidates": []}
        raise AssertionError(req)
    return memory


def test_size_held_file_named_with_split_fix_and_no_connect_step(cache):
    lines = ask.miss_report("alice", 1, {}, {}, "How much was the villa roof repair quote?", ["alice-notes"])
    joined = "\n".join(lines)
    assert "Skipped at setup, and may hold the answer:" in lines
    assert "    notes/closed.md: too big to connect (109 KB, limit 90 KB) -> Fix: split it into smaller files, then re-run setup" in lines
    assert "prepare_bulk.py --root <folder>" not in joined  # its folder is already connected
    assert "apply its fix, then ask again" in joined
    assert "roof-ok.md" not in joined  # connected file is not skipped
    assert "0.28" not in joined


def test_secret_held_matched_on_name_only_and_never_read(cache, monkeypatch):
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda f, *a, **k: (opened.append(str(f)), real_open(f, *a, **k))[1])
    lines = ask.miss_report("alice", 1, {}, {}, "what is the wifi router login", ["alice-notes"])
    assert not any("wifi-router" in f for f in opened)
    assert any(line.startswith("    notes/wifi-router.md: held back") and "--allow-held" in line for line in lines)
    assert "SECRET-CONTENT" not in "\n".join(lines) and "hunter2" not in "\n".join(lines)
    # Its words are only in the text, which is never read: no match.
    assert not any("wifi-router" in line for line in ask.miss_report("alice", 1, {}, {}, "hunter2 value", ["alice-notes"]))


def test_gate_failed_file_named_with_rerun_fix(cache):
    lines = ask.miss_report("alice", 1, {}, {}, "What is our ARC campaign phase?", ["alice-notes"])
    assert ("    notes/arc-campaign.md: not connected: its label did not pass the setup check -> "
            "Fix: re-run setup on that file (its label failed the check)") in lines


def test_part_pointer_reads_base_held_txt_and_cache(cache):
    lines = ask.miss_report("alice", 1, {}, {}, "garden gate notes", ["alice-brain-2"])
    joined = "\n".join(lines)
    assert "notes/brain-garden.md: too big to connect (95 KB, limit 90 KB)" in joined
    assert "notes/gate.md: not connected" in joined
    assert "masked" not in joined


def test_other_principals_pointer_excluded(cache):
    lines = ask.miss_report("alice", 1, {}, {}, "roof bob", ["alice-notes", "alice-brain"])
    assert "roof-bob" not in "\n".join(lines)
    # Even when the report is reachable, its named principals keep it from alice.
    assert "roof-bob" not in "\n".join(ask.miss_report("alice", 1, {}, {}, "roof bob", ["bob-notes"]))


def test_no_relevant_skipped_file_keeps_connect_step(cache):
    lines = ask.miss_report("alice", 1, {}, {}, "zebra migration season", ["alice-notes"])
    assert "Skipped at setup, and may hold the answer:" not in lines
    assert any("prepare_bulk.py --root <folder>" in line for line in lines)


def test_errored_pointer_still_prints_miss_report(cache, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", fake_memory(["alice-notes", "broken"], errored=("broken",)))
    rc = ask.lookup("How much was the villa roof repair quote?", "alice", tmp_path / "state")
    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 1
    assert "unresolved: 1 of 2 pointers errored" in out
    assert "What was searched:" in out and "Next step (pick one):" in out
    assert any("notes/closed.md: too big to connect" in line for line in out)
    assert out.index("unresolved: 1 of 2 pointers errored") < out.index("What was searched:")
    assert out[-1] == ask.VOICE_LINE
