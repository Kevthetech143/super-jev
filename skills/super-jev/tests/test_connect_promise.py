"""The connect promise, offline: Markdown only, notes up to 250,000 bytes connect whole, bigger ones are held
"too big, split it", and every run ends with CONNECTED/HELD/FAILED counts and an exit code that matches
(0 all connected, 3 something held, 1 something failed)."""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pb_promise", SKILL / "prepare_bulk.py")
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)

OK = {"state": "SUPPORTED", "confidence": 0.95, "secs": 0}
COUNTS = re.compile(r"^CONNECTED (\d+), HELD (\d+), FAILED (\d+)\b", re.M)


def _folder(tmp_path, **files):
    root = tmp_path / "notes"
    root.mkdir()
    for name, text in files.items():
        (root / name).write_text(text)
    return root


@pytest.fixture
def go(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")
    monkeypatch.setattr(pb, "writer", lambda items, *a, **k: {i["path"]: {"description": "A note.", "question": "q?"} for i in items})
    monkeypatch.setattr(pb, "gate", lambda *a, **k: OK)
    monkeypatch.setattr(pb, "gate_many", lambda p, claims, *a, **k: [OK for _ in claims], raising=False)
    monkeypatch.setattr(pb, "connect_part", lambda *a, **k: {"connected": True})

    def run(root, *extra):
        monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--root", str(root), "--pointer", "p", "--principal", "alice",
                                          "--no-findability", "--no-shared", "--writer", "claude", *extra])
        code = pb.main()
        return code, capsys.readouterr().out
    return run


def test_note_of_exactly_250000_bytes_connects_whole_and_one_byte_more_is_held(tmp_path):
    root = _folder(tmp_path)
    (root / "exact.md").write_text("a" * 250_000)
    (root / "over.md").write_text("a" * 250_001)
    files, held = pb.inventory([root])
    assert [p.name for p in files] == ["exact.md"]
    assert len(held) == 1 and held[0][0].endswith("over.md")
    assert "too big, split it" in held[0][1] and "250,001 bytes" in held[0][1]


def test_size_rule_has_one_number_no_sections_no_size_approval():
    for gone in ("SECTION_MAX_BYTES", "split_sections", "section_text", "APPROVE_MAX_BYTES",
                 "extension_list", "CODE_EXTENSIONS", "code_heading"):
        assert not hasattr(pb, gone), gone
    assert pb.CEILING_BYTES == 250_000 and pb.CONNECTABLE_EXTENSIONS == (".md",)


@pytest.mark.parametrize("flag", [["--ext", "py"], ["--approve-held", "x.md"]])
def test_removed_flags_are_refused(tmp_path, monkeypatch, flag):
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--root", str(tmp_path), "--pointer", "p", "--principal", "a", *flag])
    with pytest.raises(SystemExit) as exc:
        pb.main()
    assert exc.value.code == 2


def test_only_markdown_is_inventoried(tmp_path):
    root = _folder(tmp_path, **{"a.md": "note\n", "b.py": "x = 1\n", "c.txt": "text\n"})
    files, held = pb.inventory([root])
    assert [p.name for p in files] == ["a.md"] and not held


def test_all_connected_ends_with_counts_and_exits_0(tmp_path, go):
    root = _folder(tmp_path, **{"a.md": "alpha\n", "b.md": "beta\n"})
    code, out = go(root)
    assert code == 0
    assert out.strip().splitlines()[-1].startswith("CONNECTED 2, HELD 0, FAILED 0")


def test_a_held_file_exits_3_and_the_counts_line_is_last(tmp_path, go):
    root = _folder(tmp_path, **{"a.md": "alpha\n"})
    (root / "big.md").write_text("a" * 250_001)
    code, out = go(root)
    assert code == 3
    last = out.strip().splitlines()[-1]
    assert COUNTS.match(last) and last.startswith("CONNECTED 1, HELD 1, FAILED 0")
    assert "too big, split it" in out


def test_a_secret_held_file_exits_3(tmp_path, go):
    root = _folder(tmp_path, **{"a.md": "alpha\n", "s.md": "pass" + "word: hunter2xyz\n"})
    code, out = go(root)
    assert code == 3 and out.strip().splitlines()[-1].startswith("CONNECTED 1, HELD 1, FAILED 0")


def test_a_file_the_gate_refuses_is_failed_and_exits_1(tmp_path, go, monkeypatch):
    root = _folder(tmp_path, **{"a.md": "alpha\n"})
    monkeypatch.setattr(pb, "gate", lambda *a, **k: {"state": "CONTRADICTED", "confidence": 0.9, "secs": 0})
    monkeypatch.setattr(pb, "gate_many", lambda p, claims, *a, **k: [{"state": "CONTRADICTED", "confidence": 0.9, "secs": 0} for _ in claims], raising=False)
    code, out = go(root)
    assert code == 1 and out.strip().splitlines()[-1].startswith("CONNECTED 0, HELD 0, FAILED 1")


def test_a_part_that_does_not_connect_is_failed_and_exits_1(tmp_path, go, monkeypatch):
    root = _folder(tmp_path, **{"a.md": "alpha\n", "b.md": "beta\n"})
    monkeypatch.setattr(pb, "connect_part", lambda *a, **k: {"connected": False})
    code, out = go(root)
    assert code == 1 and out.strip().splitlines()[-1].startswith("CONNECTED 0, HELD 0, FAILED 2")


def test_failed_and_held_together_exit_1(tmp_path, go, monkeypatch):
    root = _folder(tmp_path, **{"a.md": "alpha\n"})
    (root / "big.md").write_text("a" * 250_001)
    monkeypatch.setattr(pb, "connect_part", lambda *a, **k: {"connected": False})
    code, out = go(root)
    assert code == 1 and out.strip().splitlines()[-1].startswith("CONNECTED 0, HELD 1, FAILED 1")


def test_no_connect_preview_still_ends_with_counts(tmp_path, go):
    root = _folder(tmp_path, **{"a.md": "alpha\n"})
    code, out = go(root, "--no-connect")
    assert code == 0 and out.strip().splitlines()[-1].startswith("CONNECTED 0, HELD 0, FAILED 0")
    assert "--no-connect" in out.strip().splitlines()[-1]


def test_every_file_held_still_ends_with_counts_and_is_nonzero(tmp_path, go):
    root = _folder(tmp_path)
    (root / "big.md").write_text("a" * 250_001)
    code, out = go(root)
    assert code != 0 and out.strip().splitlines()[-1].startswith("CONNECTED 0, HELD 1, FAILED 0")


def test_refresh_of_a_pointer_connected_with_code_types_is_refused(tmp_path, go, monkeypatch):
    root = _folder(tmp_path, **{"a.md": "alpha\n"})
    code, _ = go(root)
    assert code == 0
    rp = tmp_path / "cache" / "p-report.json"
    rep = json.loads(rp.read_text())
    rep["extensions"] = [".md", ".py"]
    rp.write_text(json.dumps(rep))
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--pointer", "p", "--principal", "alice", "--refresh"])
    assert pb.main() == 2
